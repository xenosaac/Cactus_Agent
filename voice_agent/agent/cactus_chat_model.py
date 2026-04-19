"""Planner LLM wrapper. Implements Browser Use's BaseChatModel Protocol
so the same object is usable by our orchestrator AND by Browser Use.

On any failure:
  - Hybrid mode: escalate to Gemini via gemini_client.
  - Local mode: raise RuntimeError.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel

if TYPE_CHECKING:
    from voice_agent.agent.gemini_client import GeminiClient
    from voice_agent.config import Settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class CactusChatModel:
    _verified_api_keys: bool = True
    model: str

    def __init__(
        self,
        settings: "Settings",
        cactus_handle: int,
        gemini_client: "GeminiClient | None" = None,
    ) -> None:
        # Lazy-import FFI + Browser Use so tests can substitute mocks.
        from cactus.python.src.cactus import (
            cactus_complete,
            cactus_reset,
        )
        self._complete = cactus_complete
        self._reset = cactus_reset
        self._handle = cactus_handle
        self._settings = settings
        self._gemini = gemini_client
        self.model = "gemma-4-E4B-it"

    # ── BaseChatModel Protocol ────────────────────────────────────────────────

    @property
    def provider(self) -> str:
        return "cactus"

    @property
    def name(self) -> str:
        return "cactus-gemma-4-E4B"

    @property
    def model_name(self) -> str:
        return self.model

    # ── Turn lifecycle ────────────────────────────────────────────────────────

    def reset_turn(self) -> None:
        """Clear KV cache. Call between distinct user utterances."""
        self._reset(self._handle)

    # ── Main invocation ───────────────────────────────────────────────────────

    async def ainvoke(
        self,
        messages: list[Any],
        output_format: type[T] | None = None,
        **kwargs: Any,
    ) -> Any:  # ChatInvokeCompletion[T] | ChatInvokeCompletion[str]
        from browser_use.llm.views import ChatInvokeCompletion

        import time as _time
        t0 = _time.perf_counter()
        from voice_agent.config import Mode
        log.info(
            "cactus.ainvoke START msgs=%d mode=%s gemini_available=%s",
            len(messages), getattr(self._settings.mode, "value", "?"),
            self._gemini is not None,
        )
        # HYBRID short-circuit: Gemma 4 E4B LLM prefill on CPU is multi-minute
        # with a 35-tool manifest (no NPU mlpackage shipped by Cactus for the
        # core LM). Route the planner to Gemini directly whenever it's
        # available. Cactus stays loaded and owns vision + whisper.
        if self._gemini is not None and self._settings.mode == Mode.HYBRID:
            log.info("cactus.ainvoke: hybrid short-circuit → Gemini")
            try:
                out = await self._maybe_escalate(
                    messages, output_format, kwargs.get("tools_json"),
                    reason="hybrid-force-cloud",
                )
                log.info(
                    "cactus.ainvoke END(gemini) duration_ms=%.1f",
                    (_time.perf_counter() - t0) * 1000,
                )
                return out
            except Exception:
                log.exception(
                    "cactus.ainvoke gemini path raised after %.1fms",
                    (_time.perf_counter() - t0) * 1000,
                )
                raise

        messages_json = self._translate_messages(messages)
        tools_json = kwargs.get("tools_json")
        options_json = json.dumps({
            "max_tokens": self._settings.planner_max_tokens,
            "temperature": self._settings.planner_temperature,
            "top_p": self._settings.planner_top_p,
            "stop_sequences": ["<turn|>", "</s>"],
        })

        for attempt in (1, 2):
            try:
                result_json = await asyncio.to_thread(
                    self._complete,
                    self._handle,
                    messages_json,
                    options_json,
                    tools_json,
                    None,   # streaming callback unused on planner path
                    None,   # pcm_data unused for text-only turns
                )
                result = json.loads(result_json)
            except Exception as exc:  # noqa: BLE001
                log.warning("cactus_complete raised (attempt %d): %s", attempt, exc)
                if attempt == 1:
                    continue
                return await self._maybe_escalate(
                    messages, output_format, tools_json, reason=str(exc)
                )

            if not result.get("success"):
                err = result.get("error") or "cactus reported failure"
                if attempt == 1:
                    continue
                return await self._maybe_escalate(
                    messages, output_format, tools_json, reason=err
                )

            # Cactus Cloud handoff semantics (per hybrid-ai docs):
            # `cloud_handoff=true` means Cactus flagged confidence below threshold
            # or the query exceeds device capabilities.
            #
            # For LLM completions, Cactus Cloud LLM handoff is GATED and must
            # be enabled per-account ("contact us to enable" per docs). Without
            # enablement, cloud_handoff=true but response is null/empty — we
            # then route to OUR Gemini client.
            #
            # If the user HAS enabled Cactus Cloud LLM handoff and it populated
            # a response or function_calls, use that directly (no double call).
            if result.get("cloud_handoff"):
                confidence = result.get("confidence")
                already_populated = (
                    (result.get("response") or "").strip()
                    or result.get("function_calls")
                )
                if already_populated:
                    log.info(
                        "Cactus Cloud returned result (confidence=%s); using it.",
                        confidence,
                    )
                    return self._to_completion(result, output_format)
                log.info(
                    "Cactus requested cloud handoff (confidence=%s); routing to Gemini",
                    confidence,
                )
                return await self._maybe_escalate(
                    messages,
                    output_format,
                    tools_json,
                    reason=f"cloud_handoff (confidence={confidence})",
                )

            # Successful local completion
            return self._to_completion(result, output_format)

        # Unreachable
        raise RuntimeError("cactus_chat_model: unreachable")

    # ── internals ─────────────────────────────────────────────────────────────

    def _translate_messages(self, messages: list[Any]) -> str:
        """Browser-Use BaseMessage list -> Cactus messages JSON.

        We duck-type by `.role` / `.content` + optional `.images` / `.audio`
        to avoid tight coupling to a specific Browser Use minor version.
        """
        out: list[dict[str, Any]] = []
        for m in messages:
            role = getattr(m, "role", None)
            content = getattr(m, "content", None)
            if role is None:
                # Accept plain dicts too (our orchestrator uses them).
                if isinstance(m, dict):
                    out.append({k: v for k, v in m.items() if v is not None})
                    continue
                role = "user"
            if content is None:
                content = str(m)

            # Normalize Browser Use's system/user/assistant class names.
            role_str = str(role).lower()
            if "system" in role_str:
                r = "system"
            elif "assistant" in role_str or "model" in role_str:
                r = "assistant"
            elif "tool" in role_str:
                r = "tool"
            else:
                r = "user"

            entry: dict[str, Any] = {"role": r, "content": content}
            imgs = getattr(m, "images", None)
            if imgs:
                entry["images"] = list(imgs)
            auds = getattr(m, "audio", None)
            if auds:
                entry["audio"] = list(auds)
            out.append(entry)
        return json.dumps(out)

    def _to_completion(self, result: dict[str, Any], output_format: type[T] | None) -> Any:
        from browser_use.llm.views import ChatInvokeCompletion

        fcs = result.get("function_calls") or []
        text = result.get("response") or ""
        # Some Browser Use versions mark `usage` as required on ChatInvokeCompletion;
        # Cactus only reports it when it feels like it. Default to a zeroed payload
        # so construction never fails on a missing field.
        usage = result.get("usage") or {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "prompt_cached_tokens": 0, "prompt_cache_creation_tokens": 0,
            "prompt_image_tokens": 0,
        }

        def _build(**kwargs: Any) -> Any:
            try:
                return ChatInvokeCompletion(**kwargs, usage=usage)
            except TypeError:
                # Older Browser Use: no `usage` kwarg.
                return ChatInvokeCompletion(**kwargs)

        if output_format is not None:
            if fcs:
                try:
                    return _build(completion=output_format(**fcs[0].get("arguments", {})))
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(f"Malformed tool-call args: {exc}") from exc
            # No function_calls but output_format requested: try JSON text.
            try:
                data = json.loads(text)
                return _build(completion=output_format(**data))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"Planner did not produce parseable structured output: {text!r}"
                ) from exc

        if fcs:
            tool_calls = [self._translate_fc(fc) for fc in fcs]
            # ChatInvokeCompletion here has no `tool_calls` field and Pydantic
            # blocks dynamic attrs. Return a SimpleNamespace exposing the
            # attributes the orchestrator reads.
            from types import SimpleNamespace
            return SimpleNamespace(completion=text, tool_calls=tool_calls)

        return _build(completion=text)

    def _translate_fc(self, fc: dict[str, Any]) -> Any:
        try:
            from browser_use.llm.views import ToolCall
            return ToolCall(name=fc["name"], arguments=fc.get("arguments", {}))
        except Exception:  # noqa: BLE001
            # Fallback: simple dataclass-ish namespace
            from types import SimpleNamespace
            return SimpleNamespace(
                name=fc["name"], arguments=fc.get("arguments", {})
            )

    async def _maybe_escalate(
        self,
        messages: list[Any],
        output_format: type[T] | None,
        tools_json: str | None,
        reason: str,
    ) -> Any:
        from voice_agent.config import Mode
        if self._gemini is not None and self._settings.mode == Mode.HYBRID:
            log.warning("Escalating to Gemini (reason: %s)", reason)
            return await self._gemini.ainvoke(
                messages, output_format=output_format, tools_json=tools_json
            )
        raise RuntimeError(f"Planner failed (local-only, no fallback): {reason}")
