from __future__ import annotations

from voice_agent.events import AgentEvent, EventType
from voice_agent.ui.native.reducer import UIState, apply


def test_agent_done_prefers_full_final_text_over_summary() -> None:
    state = apply(
        UIState(stage="acting"),
        AgentEvent(
            type=EventType.AGENT_DONE,
            summary="short summary",
            final_text="full result with details",
        ),
    )

    assert state.stage == "done"
    assert state.result == "full result with details"
