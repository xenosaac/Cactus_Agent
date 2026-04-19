"""Text-to-speech via macOS /usr/bin/say. Non-blocking subprocess wrapper."""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

SAY_BIN = "/usr/bin/say"


async def say(
    text: str,
    voice: str | None = None,
    rate_wpm: int | None = None,
) -> None:
    """Speak `text` through the system speaker. Awaits until speech finishes.

    Empty / whitespace-only input is a no-op (does not spawn a subprocess).
    """
    text = text.strip()
    if not text:
        return

    cmd = [SAY_BIN]
    if voice:
        cmd += ["-v", voice]
    if rate_wpm:
        cmd += ["-r", str(rate_wpm)]
    cmd.append(text)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        log.error("%s not found; TTS disabled", SAY_BIN)
        return

    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.error(
            "say failed (rc=%s): %s",
            proc.returncode,
            stderr.decode("utf-8", errors="ignore"),
        )
