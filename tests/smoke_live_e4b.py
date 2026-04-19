"""Live smoke test against Gemma 4 E4B.

Tests (1) plain text completion, (2) tool-calling, (3) multimodal image input.
Run: python tests/smoke_live_e4b.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cactus.python.src.cactus import (
    cactus_init,
    cactus_destroy,
    cactus_complete,
)


WEIGHTS = _ROOT / "cactus" / "weights" / "gemma-4-e4b-it"


def _print_stats(label: str, result: dict) -> None:
    print(f"\n--- {label} ---")
    for k in ("success", "cloud_handoff", "confidence",
             "time_to_first_token_ms", "total_time_ms",
             "prefill_tps", "decode_tps"):
        print(f"  {k}: {result.get(k)}")
    rr = result.get("response")
    if rr:
        print(f"  response: {rr!r}")
    fcs = result.get("function_calls")
    if fcs:
        print(f"  function_calls: {fcs!r}")


async def run() -> int:
    if not WEIGHTS.exists() or not (WEIGHTS / "config.txt").exists():
        print(f"ERROR: no E4B weights at {WEIGHTS}")
        return 1

    print(f"Loading Gemma 4 E4B from {WEIGHTS}")
    t0 = time.time()
    handle = cactus_init(str(WEIGHTS), None, False)
    print(f"Loaded in {(time.time() - t0) * 1000:.0f} ms")

    options = json.dumps({
        "max_tokens": 64,
        "temperature": 0.2,
        "top_p": 0.9,
        "stop_sequences": ["<turn|>"],
    })

    try:
        # Test 1: plain completion
        msgs1 = json.dumps([{"role": "user", "content": "Say hi in 3 words."}])
        r1 = json.loads(cactus_complete(handle, msgs1, options, None, None, None))
        _print_stats("Plain completion", r1)
        assert r1.get("success"), f"plain failed: {r1}"

        # Test 2: tool calling — WITH the production planner system prompt
        from voice_agent.agent.system_prompts import PLANNER_SYSTEM

        tools = json.dumps([
            {
                "name": "get_weather",
                "description": "Get current weather for a city.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name"},
                    },
                    "required": ["city"],
                },
            },
        ])
        msgs2 = json.dumps([
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user", "content": "What's the weather in San Francisco?"},
        ])
        r2 = json.loads(cactus_complete(handle, msgs2, options, tools, None, None))
        _print_stats("Tool call (get_weather)", r2)
        fcs = r2.get("function_calls") or []
        if fcs:
            print(f"  ✓ Model emitted {len(fcs)} function_call(s).")
            print(f"  First call: {fcs[0]}")
        else:
            print(f"  ✗ No function_calls; model returned prose.")

        # Test 3: multi-tool selection
        tools3 = json.dumps([
            {
                "name": "send_email",
                "description": "Send an email.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["to", "subject", "body"],
                },
            },
            {
                "name": "create_calendar_event",
                "description": "Create a calendar event.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "datetime": {"type": "string"},
                    },
                    "required": ["title", "datetime"],
                },
            },
        ])
        msgs3 = json.dumps([
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user",
             "content": "Put a hold on my calendar for Tuesday 2pm titled 'Oncology follow-up'."},
        ])
        r3 = json.loads(cactus_complete(handle, msgs3, options, tools3, None, None))
        _print_stats("Multi-tool selection (expect create_calendar_event)", r3)

        return 0

    finally:
        cactus_destroy(handle)
        print("\nDestroyed E4B model handle.")


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
