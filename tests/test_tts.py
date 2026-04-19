from __future__ import annotations

from unittest.mock import patch, AsyncMock

import pytest

from voice_agent.voice.tts import say


@pytest.mark.asyncio
async def test_empty_string_is_noop() -> None:
    with patch("voice_agent.voice.tts.asyncio.create_subprocess_exec") as mock:
        await say("")
        await say("   ")
        await say("\n\t")
        mock.assert_not_called()


@pytest.mark.asyncio
async def test_say_passes_text_to_subprocess() -> None:
    class FakeProc:
        returncode = 0
        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    with patch(
        "voice_agent.voice.tts.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=FakeProc()),
    ) as mock:
        await say("hello world")
        args, _ = mock.call_args
        assert "/usr/bin/say" in args
        assert "hello world" in args
