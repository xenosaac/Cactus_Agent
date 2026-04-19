"""Localhost HTTP + WebSocket bridge between Python backend and the React UI.

Serves the static `voice_agent/ui/` bundle at `/` and streams AgentEvent
objects over `/ws` to every connected client. User confirmation / cancel
intents are accepted over `POST /confirm` and `POST /cancel` respectively
and land on a shared asyncio.Queue that the orchestrator can consume.

Only Starlette + uvicorn are used (no FastAPI — starlette is enough).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.types import ASGIApp
from starlette.websockets import WebSocket, WebSocketDisconnect

from voice_agent.events import AgentEvent, EventBus


class _NoCacheMiddleware(BaseHTTPMiddleware):
    """Stop WebKit (and friends) from serving stale HTML/JS during dev."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


class _RequestLogMiddleware(BaseHTTPMiddleware):
    """Every HTTP request → one log line. Critical for diagnosing "did the
    webview even try to fetch the bundle?" (or did it silently abandon the
    navigation)."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        import time
        start = time.perf_counter()
        client = f"{request.client.host}:{request.client.port}" if request.client else "?"
        ua = request.headers.get("user-agent", "?")
        try:
            response: Response = await call_next(request)
        except Exception as exc:  # noqa: BLE001
            dur = (time.perf_counter() - start) * 1000
            logging.getLogger("voice_agent.http").exception(
                "%s %s RAISED %s duration_ms=%.1f client=%s ua=%r",
                request.method, request.url.path, exc, dur, client, ua[:80],
            )
            raise
        dur = (time.perf_counter() - start) * 1000
        logging.getLogger("voice_agent.http").info(
            "%s %s status=%d duration_ms=%.1f client=%s ua=%r",
            request.method, request.url.path, response.status_code, dur,
            client, ua[:80],
        )
        return response

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

class CompanionServer:
    """Starlette app: event-bus WebSocket bridge + intent endpoints.

    The UI is now the native PySide6 Qt window (see voice_agent/ui/native/).
    This server stays up as a text-side backup so `curl /wake /confirm /cancel`
    still works from another terminal during demos.
    """

    def __init__(
        self,
        bus: EventBus,
        intent_queue: "asyncio.Queue[str]",
    ) -> None:
        self._bus = bus
        self._intent_queue = intent_queue
        self._clients: set[WebSocket] = set()
        self._unsub: Callable[[], None] | None = None

    # ── lifecycle helpers ─────────────────────────────────────────────────────

    def _ensure_subscribed(self) -> None:
        if self._unsub is None:
            self._unsub = self._bus.subscribe(self._broadcast)

    def _maybe_unsubscribe(self) -> None:
        if not self._clients and self._unsub is not None:
            self._unsub()
            self._unsub = None

    async def _broadcast(self, event: AgentEvent) -> None:
        payload = self._event_to_dict(event)
        dead: list[WebSocket] = []
        log.debug(
            "ws broadcast event_id=%s type=%s clients=%d",
            event.event_id, event.type.value, len(self._clients),
        )
        for ws in list(self._clients):
            try:
                await ws.send_json(payload)
            except Exception as exc:  # noqa: BLE001
                log.warning("ws send failed: %s", exc)
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    @staticmethod
    def _event_to_dict(e: AgentEvent) -> dict[str, Any]:
        return {
            "type": e.type.value,
            "event_id": e.event_id,
            "timestamp": e.timestamp,
            "task": e.task,
            "step_index": e.step_index,
            "tool_name": e.tool_name,
            "summary": e.summary,
            "success": e.success,
            "error": e.error,
            "mode": e.mode,
            "partial_text": e.partial_text,
            "final_text": e.final_text,
        }

    # ── endpoints ─────────────────────────────────────────────────────────────

    async def websocket_endpoint(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)
        self._ensure_subscribed()
        log.info("ws client connected (total=%d)", len(self._clients))

        # Send a hello with the current state so new clients don't have to wait
        # for the next event to draw the UI.
        try:
            await websocket.send_json({
                "type": "hello",
                "event_id": 0,
                "timestamp": 0,
                "message": "connected",
            })
        except Exception:  # noqa: BLE001
            pass

        try:
            while True:
                # Expect no inbound messages; client-to-server intents go via
                # POST /confirm | /cancel (simpler to integrate with fetch).
                # We still block on recv to detect disconnects.
                msg = await websocket.receive_text()
                # Optional: accept control pings from the client.
                if msg.strip().lower() == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001
            log.warning("ws error: %s", exc)
        finally:
            self._clients.discard(websocket)
            self._maybe_unsubscribe()
            log.info("ws client disconnected (remaining=%d)", len(self._clients))

    async def confirm(self, _request: Request) -> JSONResponse:
        await self._intent_queue.put("confirm")
        log.info("intent queued: confirm  (queue_size=%d)", self._intent_queue.qsize())
        return JSONResponse({"ok": True, "intent": "confirm"})

    async def cancel(self, _request: Request) -> JSONResponse:
        await self._intent_queue.put("cancel")
        log.info("intent queued: cancel  (queue_size=%d)", self._intent_queue.qsize())
        return JSONResponse({"ok": True, "intent": "cancel"})

    async def wake(self, request: Request) -> JSONResponse:
        """Manual push-to-talk trigger. Publishes WAKE_DETECTED on the bus so
        the UI animates to LISTENING, then optionally publishes STT_FINAL if
        the body supplies a `text` field (useful when the mic isn't available
        and the user wants to type a command for testing)."""
        from voice_agent.events import EventType

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        text = (body or {}).get("text")

        await self._bus.publish(AgentEvent(
            type=EventType.WAKE_DETECTED,
            summary="typed_wake" if text else None,
        ))
        if text:
            await self._bus.publish(
                AgentEvent(type=EventType.STT_FINAL, final_text=str(text))
            )
            # Deliver the typed command into the voice loop via the intent queue
            # (consumer will route it to the orchestrator).
            await self._intent_queue.put(f"utter:{text}")
        return JSONResponse({"ok": True, "intent": "wake"})

    # ── application factory ───────────────────────────────────────────────────

    def build_app(self) -> Starlette:
        routes = [
            WebSocketRoute("/ws", self.websocket_endpoint),
            Route("/confirm", self.confirm, methods=["POST"]),
            Route("/cancel", self.cancel, methods=["POST"]),
            Route("/wake", self.wake, methods=["POST"]),
        ]
        return Starlette(
            routes=routes,
            middleware=[
                Middleware(_RequestLogMiddleware),
                Middleware(_NoCacheMiddleware),
            ],
        )


def make_app(bus: EventBus, intent_queue: "asyncio.Queue[str]") -> Starlette:
    return CompanionServer(bus, intent_queue).build_app()


async def serve_forever(
    bus: EventBus,
    intent_queue: "asyncio.Queue[str]",
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    """Run the uvicorn server in-process. Must be awaited from the same
    asyncio loop that publishes events to the bus."""
    import uvicorn

    app = make_app(bus, intent_queue)
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=True,
        # Keep uvicorn in the calling event loop — don't spin up its own.
        loop="none",
    )
    server = uvicorn.Server(config)
    await server.serve()
