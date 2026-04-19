"""Wake-phrase filter. Word-boundary substring match on finalized transcripts."""
from __future__ import annotations

import re


def _compile(phrases: tuple[str, ...]) -> re.Pattern[str]:
    escaped = [re.escape(p) for p in phrases]
    # Match the wake phrase ANYWHERE in the utterance — Whisper often
    # prepends filler ("so, hey cactus") or extra words before the phrase.
    # Rejects "hey cactuses" via the `(?!\w)` negative lookahead on the
    # next char. Captures everything AFTER the phrase as the command.
    pattern = (
        rf".*?(?:{'|'.join(escaped)})(?!\w)[\s,.!?]*(.*)$"
    )
    return re.compile(pattern, re.IGNORECASE | re.DOTALL)


class WakeFilter:
    """Substring + word-boundary filter. Fast, deterministic, zero deps.

    Upgrade path (v2): swap for a wake-word engine (openWakeWord, Porcupine).
    """

    def __init__(self, phrases: tuple[str, ...]) -> None:
        if not phrases:
            raise ValueError("At least one wake phrase is required")
        self._re = _compile(phrases)

    def match(self, utterance: str) -> tuple[bool, str]:
        """Return (matched, command). `command` has the wake phrase stripped.

        Examples:
            "hey agent, book my flight"      -> (True,  "book my flight")
            "Okay agent book flight"         -> (True,  "book flight")
            "hey agents of change"           -> (False, "")
            "nothing about agents"           -> (False, "")
            "agent"                          -> (True,  "")    # phrase alone
        """
        m = self._re.match(utterance)
        if not m:
            return False, ""
        return True, m.group(1).strip()
