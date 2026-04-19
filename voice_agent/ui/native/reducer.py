"""Python port of `applyEventToLive` from voice_agent/ui/src/app.jsx.

An immutable-style reducer: feed an `AgentEvent` + current `UIState`, get a
new `UIState`. No Qt imports here — the reducer is pure so it stays testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal

from voice_agent.events import AgentEvent, EventType

Stage = Literal[
    "idle", "listening", "transcribing", "planning",
    "confirming", "acting", "done",
]


@dataclass
class PlanStep:
    verb: str
    target: str
    done: bool = False


@dataclass
class UIState:
    stage: Stage = "idle"
    ready: bool = False
    utterance: str = ""
    app: str = ""
    route: Literal["local", "cloud"] = "local"
    confidence: float = 0.95
    plan: list[PlanStep] = field(default_factory=list)
    # Preview payload: {kind: "email"|"file"|"summary"|"windows"|"describe", ...}
    preview: dict[str, Any] | None = None
    result: str = ""
    mode: str = "hybrid"
    speaking: bool = False
    # Live mic level (RMS, peak) in [0, 1], updated from AUDIO_LEVEL events.
    mic_rms: float = 0.0
    mic_peak: float = 0.0
    # Brief "didn't catch that" flash after an empty transcription.
    stt_empty_flash: bool = False


def apply(prev: UIState, e: AgentEvent) -> UIState:
    t = e.type

    if t == EventType.AUDIO_LEVEL:
        return replace(
            prev,
            mic_rms=e.rms if e.rms is not None else prev.mic_rms,
            mic_peak=e.peak if e.peak is not None else prev.mic_peak,
        )

    if t == EventType.VOICE_READY:
        return replace(prev, ready=True, stage="idle", speaking=False)

    if t == EventType.WHISPER_START:
        # Progress cue — show "transcribing" only when the user has already
        # engaged (pressed Space / wake phrase matched, so stage is past
        # `idle`). Ambient speech triggers WHISPER_START too; advancing
        # from idle on every ambient clip would flicker the UI and block
        # Space presses.
        if prev.stage == "listening":
            return replace(prev, stage="transcribing")
        return prev

    if t == EventType.STT_EMPTY:
        # Whisper returned no text. Flash the footer + go back to idle so the
        # user knows to try again.
        stage: Stage = "confirming" if prev.stage == "confirming" else "idle"
        return replace(
            prev, stage=stage, utterance="",
            stt_empty_flash=True,
            result=e.summary or "didn't catch that",
        )

    if t == EventType.PTT_START:
        if not prev.ready:
            return prev
        if prev.stage in ("idle", "done"):
            return UIState(
                stage="listening",
                ready=True,
                mode=prev.mode,
                speaking=True,
                mic_rms=prev.mic_rms,
                mic_peak=prev.mic_peak,
            )
        return replace(prev, speaking=True)

    if t == EventType.PTT_END:
        return replace(prev, speaking=False)

    if t == EventType.WAKE_DETECTED:
        return UIState(stage="listening", ready=prev.ready, mode=prev.mode,
                       speaking=True, mic_rms=prev.mic_rms,
                       mic_peak=prev.mic_peak)

    if t == EventType.STT_PARTIAL:
        new_stage: Stage = "listening" if prev.stage == "idle" else prev.stage
        return replace(
            prev,
            stage=new_stage,
            utterance=e.partial_text or prev.utterance,
            speaking=True,
        )

    if t == EventType.STT_FINAL:
        return replace(
            prev,
            stage="transcribing",
            utterance=e.final_text or prev.utterance,
            speaking=False,
        )

    if t == EventType.AGENT_START:
        mode = e.mode or prev.mode or "hybrid"
        route: Literal["local", "cloud"] = "local" if mode == "local" else "cloud"
        return replace(
            prev,
            stage="planning",
            utterance=e.task or prev.utterance,
            mode=mode,
            route=route,
            plan=[],
        )

    if t == EventType.CONFIRMATION_REQUIRED:
        step = PlanStep(
            verb=str(e.tool_name or "tool"),
            target=str(e.summary or ""),
        )
        preview = _build_preview(e, prev)
        return replace(
            prev,
            stage="confirming",
            app=str(e.tool_name or prev.app),
            plan=[*prev.plan, step],
            preview=preview,
        )

    if t == EventType.AGENT_CANCELLED:
        return replace(prev, stage="done", result=e.summary or "Cancelled")

    if t == EventType.UNDO_REQUESTED:
        return replace(prev, stage="done", result=e.summary or "Undo")

    if t == EventType.STEP_START:
        step = PlanStep(
            verb=str(e.tool_name or "step"),
            target=str(e.summary or ""),
        )
        plan = list(prev.plan)
        # If we entered via confirming, the last plan row is the approved step.
        # Don't duplicate it.
        if not plan or plan[-1].verb != step.verb:
            plan.append(step)
        return replace(
            prev,
            stage="acting",
            app=str(e.tool_name or prev.app),
            plan=plan,
        )

    if t == EventType.STEP_DONE:
        plan = list(prev.plan)
        if plan:
            last = plan[-1]
            plan[-1] = PlanStep(
                verb=last.verb,
                target=e.summary or last.target,
                done=True,
            )
        return replace(prev, plan=plan)

    if t == EventType.AGENT_DONE:
        return replace(
            prev,
            stage="done",
            result=e.summary or e.final_text or "Done",
        )

    if t == EventType.AGENT_ERROR:
        return replace(
            prev, stage="done",
            result=f"Error: {e.error or 'unknown'}",
        )

    if t == EventType.MODE_CHANGE:
        return replace(prev, mode=e.mode or prev.mode)

    return prev  # type: ignore[unreachable]


def _build_preview(e: AgentEvent, prev: UIState) -> dict[str, Any]:
    """Heuristic: map the tool name + summary to one of the five preview kinds.

    The React spec has five typed previews (email / file / summary / windows /
    describe). Real tools don't always map cleanly, so we dispatch by a few
    keywords on the tool name. Unknown tools fall back to a generic summary
    preview with the humanized tool-call string.
    """
    tool = (e.tool_name or "").lower()
    summary = e.summary or ""
    app = (e.tool_name or "").replace(".", " · ")

    if "gmail" in tool or tool.startswith("gmail."):
        # Email-kind preview if we can parse anything; otherwise summary card.
        return {
            "kind": "email",
            "to": "—",
            "subject": summary or "(draft)",
            "body": summary or "",
        }
    if "gcal" in tool or "calendar" in tool:
        return {
            "kind": "summary",
            "title": app or "Calendar",
            "bullets": [summary] if summary else ["(no details yet)"],
        }
    if tool == "open_app":
        return {
            "kind": "file",
            "name": summary.replace("open_app(", "").rstrip(")") or "App",
            "path": "/Applications",
            "size": "",
            "modified": "launch",
        }
    if "web_navigate" in tool or "browser" in tool:
        return {
            "kind": "summary",
            "title": "Web task",
            "bullets": [summary] if summary else ["(browser work)"],
        }
    if "desktop_native_app" in tool or "read_visible_screen" in tool or "vision" in tool:
        return {
            "kind": "describe",
            "text": summary or "(screen read)",
        }
    # Fallback generic summary preview.
    return {
        "kind": "summary",
        "title": app or "Action",
        "bullets": [summary] if summary else ["(no details)"],
    }
