"""Turn loop: one user utterance -> one AGENT_START … AGENT_DONE/ERROR sequence.

Current flow:
    PTT_START → PTT_END → STT_FINAL → AGENT_START → STEP_START
    → STEP_DONE → repeat → AGENT_DONE.

Tool calls execute immediately. Confirmation gates are intentionally disabled
for the current push-to-talk build.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time as _time
from typing import TYPE_CHECKING, Any

from voice_agent.agent.system_prompts import PLANNER_SYSTEM
from voice_agent.agent.tool_router import ToolResult, ToolRouter
from voice_agent.config import Mode
from voice_agent.events import AgentEvent, EventBus, EventType
from voice_agent.voice.tts import say

if TYPE_CHECKING:
    from voice_agent.agent.cactus_chat_model import CactusChatModel
    from voice_agent.config import Settings

log = logging.getLogger(__name__)

_VAGUE_BROWSER_RESULT_RE = re.compile(
    r"\b(saved|attached|available for review|details have been saved|results\.md)\b",
    re.IGNORECASE,
)


class _Msg:
    """Lightweight Browser Use-compatible message.

    Has `.role` and `.content` attributes (which CactusChatModel duck-types).
    """

    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


class AgentOrchestrator:
    def __init__(
        self,
        settings: Settings,
        planner: CactusChatModel,
        router: ToolRouter,
        bus: EventBus,
    ) -> None:
        self._settings = settings
        self._planner = planner
        self._router = router
        self._bus = bus
        # Kept for endpoint compatibility; the current normal flow does not
        # block on user confirmation.
        self._intent_queue: asyncio.Queue[str] = asyncio.Queue()
        self._awaiting_confirm = asyncio.Event()

    def awaiting_confirm(self) -> bool:
        """Does the orchestrator currently want user confirmation?"""
        return self._awaiting_confirm.is_set()

    def deliver_intent(self, intent: str) -> None:
        """Push a legacy confirm/cancel intent."""
        try:
            self._intent_queue.put_nowait(intent)
        except asyncio.QueueFull:
            log.warning("intent queue full, dropping: %s", intent)

    async def run_turn(self, utterance: str) -> None:
        turn_start = _time.perf_counter()
        log.info("run_turn START utterance=%r", utterance[:160])

        # New turn: reset KV cache.
        try:
            self._planner.reset_turn()
        except Exception as exc:  # noqa: BLE001
            log.warning("planner.reset_turn failed: %s", exc)

        mode_str = getattr(self._settings.mode, "value", str(self._settings.mode))
        await self._bus.publish(AgentEvent(
            type=EventType.AGENT_START,
            task=utterance,
            mode=mode_str,  # type: ignore[arg-type]
        ))

        messages: list[Any] = [
            _Msg("system", PLANNER_SYSTEM),
            _Msg("user", utterance),
        ]
        tools_json = self._router.manifest_json()
        last_step_idx = 0
        last_tool_name = ""
        last_tool_result: ToolResult | None = None

        for step in range(1, self._settings.max_agent_steps + 1):
            last_step_idx = step
            t0 = _time.perf_counter()
            log.info(
                "planner step=%d START msgs=%d tools_json_len=%d",
                step, len(messages), len(tools_json or ""),
            )
            try:
                completion = await self._planner.ainvoke(
                    messages=messages,
                    output_format=None,
                    tools_json=tools_json,
                )
            except Exception as exc:  # noqa: BLE001
                log.exception(
                    "planner step=%d RAISED after %.1fms: %s",
                    step, (_time.perf_counter() - t0) * 1000, exc,
                )
                await self._bus.publish(AgentEvent(
                    type=EventType.AGENT_ERROR,
                    error=str(exc),
                    step_index=step,
                ))
                return

            tool_calls = getattr(completion, "tool_calls", None) or []
            log.info(
                "planner step=%d END duration_ms=%.1f tool_calls=%d completion_preview=%r",
                step, (_time.perf_counter() - t0) * 1000, len(tool_calls),
                str(getattr(completion, "completion", ""))[:80],
            )

            if tool_calls:
                tc = tool_calls[0]
                tc_name = getattr(tc, "name", "")
                tc_args = getattr(tc, "arguments", None) or {}

                await self._bus.publish(AgentEvent(
                    type=EventType.STEP_START,
                    step_index=step,
                    tool_name=tc_name,
                    summary=self._humanize(tc_name, tc_args),
                ))
                d0 = _time.perf_counter()
                log.info(
                    "dispatch START tool=%s args=%s",
                    tc_name, {k: str(v)[:50] for k, v in tc_args.items()},
                )
                result: ToolResult = await self._router.dispatch(tc_name, tc_args)
                if result.ok:
                    last_tool_name = tc_name
                    last_tool_result = result
                log.info(
                    "dispatch END tool=%s ok=%s duration_ms=%.1f content_preview=%r",
                    tc_name, result.ok, (_time.perf_counter() - d0) * 1000,
                    str(result.content)[:120],
                )
                await self._bus.publish(AgentEvent(
                    type=EventType.STEP_DONE,
                    step_index=step,
                    tool_name=tc_name,
                    success=result.ok,
                    summary=(
                        result.content[:120]
                        if result.ok else (result.error or "failed")
                    ),
                ))

                if not result.ok and self._settings.mode == Mode.LOCAL:
                    await self._bus.publish(AgentEvent(
                        type=EventType.AGENT_ERROR,
                        error=result.error or "tool failed",
                        step_index=step,
                        tool_name=tc_name,
                    ))
                    return

                # Feed the planner the tool call + its result so it can plan
                # the next step. Per Cactus LLM docs only system/user/assistant
                # roles are documented — avoid role="tool" which is undocumented
                # in Cactus (even though Gemma 4's native template supports it,
                # Cactus may drop unknown roles silently).
                messages.append(
                    _Msg("assistant", f"<tool_call>{tc_name}({tc_args})</tool_call>")
                )
                messages.append(
                    _Msg(
                        "user",
                        f"<tool_result name={tc_name!r}>"
                        f"{result.to_tool_message_content()}"
                        f"</tool_result>",
                    )
                )
                continue

            # No tool call -> final answer
            final_text = getattr(completion, "completion", "")
            if not isinstance(final_text, str):
                final_text = str(final_text)
            final_text = self._repair_vague_browser_result(
                final_text=final_text,
                last_tool_name=last_tool_name,
                last_tool_result=last_tool_result,
            )
            await self._bus.publish(AgentEvent(
                type=EventType.AGENT_DONE,
                success=True,
                summary=final_text[:120],
                final_text=final_text,
            ))
            log.info(
                "run_turn DONE duration_ms=%.1f final=%r",
                (_time.perf_counter() - turn_start) * 1000, final_text[:120],
            )
            await say(final_text)
            return

        # Loop exhausted without final answer
        await self._bus.publish(AgentEvent(
            type=EventType.AGENT_ERROR,
            error=f"max_agent_steps ({self._settings.max_agent_steps}) exceeded",
            step_index=last_step_idx,
        ))

    @staticmethod
    def _humanize(name: str, args: dict[str, Any]) -> str:
        if not args:
            return name
        short = ", ".join(f"{k}={str(v)[:30]}" for k, v in list(args.items())[:2])
        return f"{name}({short})"[:120]

    @staticmethod
    def _repair_vague_browser_result(
        *,
        final_text: str,
        last_tool_name: str,
        last_tool_result: ToolResult | None,
    ) -> str:
        """Prefer concrete Browser Use output over "saved for review" replies."""
        if last_tool_name != "web_navigate" or last_tool_result is None:
            return final_text
        if not last_tool_result.ok or not _VAGUE_BROWSER_RESULT_RE.search(final_text):
            return final_text
        content = last_tool_result.content.strip()
        if not content:
            return final_text
        log.info("replacing vague browser final answer with tool content")
        return content[:2000]
