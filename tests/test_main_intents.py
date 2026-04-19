"""Smoke tests for main.classify_intent (yes/no/undo voice recognizer)."""
from __future__ import annotations

import pytest

from voice_agent.main import classify_intent


@pytest.mark.parametrize(
    "utterance, expected",
    [
        # confirm
        ("yes", "confirm"),
        ("Yes.", "confirm"),
        ("yeah", "confirm"),
        ("YEP", "confirm"),
        ("sure", "confirm"),
        ("okay", "confirm"),
        ("ok", "confirm"),
        ("do it", "confirm"),
        ("Go ahead", "confirm"),
        ("go for it", "confirm"),
        ("confirm", "confirm"),
        ("proceed please", "confirm"),
        # cancel
        ("no", "cancel"),
        ("No!", "cancel"),
        ("nope", "cancel"),
        ("nah", "cancel"),
        ("cancel", "cancel"),
        ("stop", "cancel"),
        ("don't", "cancel"),
        ("Never mind", "cancel"),
        ("abort", "cancel"),
        ("skip", "cancel"),
        # undo
        ("undo", "undo"),
        ("Undo that", "undo"),
        ("revert", "undo"),
        ("take that back", "undo"),
        # none
        ("", None),
        ("hey cactus book a flight", None),
        ("tell me about the weather", None),
        ("maybe", None),
    ],
)
def test_classify_intent(utterance: str, expected: str | None) -> None:
    assert classify_intent(utterance) == expected
