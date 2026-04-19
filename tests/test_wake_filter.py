"""Full regression coverage for WakeFilter. This module is critical because
a false positive fires the agent on arbitrary speech, and a false negative
makes the demo appear broken on stage.
"""
from __future__ import annotations

import pytest

from voice_agent.voice.wake_filter import WakeFilter


PHRASES = ("hey cactus", "okay cactus", "cactus,")


@pytest.fixture
def wf() -> WakeFilter:
    return WakeFilter(PHRASES)


# ── Positive matches ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "utterance, expected_cmd",
    [
        ("hey cactus book a flight", "book a flight"),
        ("Hey Cactus book a flight", "book a flight"),
        ("HEY CACTUS book a flight", "book a flight"),
        ("hey cactus, book a flight", "book a flight"),
        ("hey cactus.  book a flight.", "book a flight."),
        ("okay cactus book a flight", "book a flight"),
        ("Okay Cactus book a flight", "book a flight"),
        ("cactus, book a flight", "book a flight"),
        ("  hey cactus  book a flight  ", "book a flight"),
        ("hey cactus", ""),           # phrase alone
        ("hey cactus!", ""),          # phrase + punctuation only
        ("cactus, ", ""),             # phrase alone with trailing comma
    ],
)
def test_positive_match(wf: WakeFilter, utterance: str, expected_cmd: str) -> None:
    matched, cmd = wf.match(utterance)
    assert matched is True
    assert cmd == expected_cmd


# ── Negative matches (word-boundary protection) ──────────────────────────────

@pytest.mark.parametrize(
    "utterance",
    [
        "hey cactuses and trees",      # plural noun, word boundary matters
        "heya cactus",                 # run-together, no match
        "I love cactus plants",        # mid-sentence
        "my cactus is green",          # mid-sentence
        "cactusy vibes today",         # substring only
        "",                            # empty
        "   ",                         # whitespace
        "okay",                        # partial phrase
        "hey",                         # partial phrase
    ],
)
def test_negative_match(wf: WakeFilter, utterance: str) -> None:
    matched, cmd = wf.match(utterance)
    assert matched is False
    assert cmd == ""


def test_wake_phrase_anywhere_in_utterance(wf: WakeFilter) -> None:
    """Whisper prepends filler words; wake filter must still find the phrase."""
    matched, cmd = wf.match("Fuckin' done, mom. Hey cactus, open Discord.")
    assert matched
    assert cmd.lower().startswith("open discord")


# ── Constructor validation ────────────────────────────────────────────────────

def test_empty_phrases_rejected() -> None:
    with pytest.raises(ValueError):
        WakeFilter(())


# ── Regex injection / unicode robustness ──────────────────────────────────────

def test_phrase_with_regex_specials() -> None:
    wf = WakeFilter(("hey cactus.",))
    # "." is escaped; must literal-match.
    matched, cmd = wf.match("hey cactus. book")
    assert matched is True
    assert cmd == "book"
    matched2, _ = wf.match("hey cactusX book")  # '.' is escaped so no match
    assert matched2 is False


def test_unicode_command() -> None:
    wf = WakeFilter(("hey cactus",))
    matched, cmd = wf.match("hey cactus 预约 医生")
    assert matched is True
    assert cmd == "预约 医生"
