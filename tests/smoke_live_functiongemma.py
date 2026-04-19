"""Live smoke test against the locally-downloaded functiongemma-270m-it.

Not part of the unit suite — requires the model files and does real inference.
Run: python tests/smoke_live_functiongemma.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

# When running as a script (python tests/smoke_live_functiongemma.py),
# Python puts the tests/ dir on sys.path, not the project root.
# Add the project root so `cactus.python.src.cactus` resolves.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cactus.python.src.cactus import (
    cactus_init,
    cactus_destroy,
    cactus_complete,
)


WEIGHTS = Path(__file__).resolve().parent.parent / "cactus" / "weights" / "functiongemma-270m-it"


def _print_stats(label: str, result: dict) -> None:
    ttft = result.get("time_to_first_token_ms")
    total = result.get("total_time_ms")
    dtps = result.get("decode_tps")
    ptps = result.get("prefill_tps")
    rr = result.get("response")
    fcs = result.get("function_calls")
    print(f"\n--- {label} ---")
    print(f"  success:      {result.get('success')}")
    print(f"  cloud_handoff:{result.get('cloud_handoff')}")
    print(f"  response:     {rr!r}")
    print(f"  function_calls: {fcs!r}")
    print(f"  confidence:   {result.get('confidence')}")
    print(f"  TTFT:         {ttft} ms")
    print(f"  total_time:   {total} ms")
    print(f"  prefill_tps:  {ptps}")
    print(f"  decode_tps:   {dtps}")


async def run() -> int:
    assert WEIGHTS.exists(), f"missing weights dir: {WEIGHTS}"

    print(f"Loading model from {WEIGHTS}")
    t0 = time.time()
    handle = cactus_init(str(WEIGHTS), None, False)
    print(f"Loaded in {(time.time() - t0) * 1000:.0f} ms")

    try:
        # Case 1: plain completion
        messages = json.dumps([
            {"role": "user", "content": "Say hi in 3 words."},
        ])
        options = json.dumps({"max_tokens": 32, "temperature": 0.2, "top_p": 0.9})
        r1 = json.loads(cactus_complete(handle, messages, options, None, None, None))
        _print_stats("Plain completion", r1)
        assert r1.get("success"), f"plain completion failed: {r1}"

        # Case 2: function calling
        tools = json.dumps([
            {
                "name": "get_weather",
                "description": "Get the current weather for a city.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name"},
                    },
                    "required": ["city"],
                },
            },
        ])
        messages2 = json.dumps([
            {"role": "user", "content": "What's the weather in San Francisco?"},
        ])
        r2 = json.loads(cactus_complete(handle, messages2, options, tools, None, None))
        _print_stats("Tool call (get_weather)", r2)
        assert r2.get("success"), f"tool call failed: {r2}"
        fcs = r2.get("function_calls") or []
        if fcs:
            print(f"  GOOD: model emitted {len(fcs)} function call(s)")
            print(f"  First call: {fcs[0]}")
        else:
            print(f"  NOTE: no function_calls — Gemma returned prose: {r2.get('response')!r}")

        return 0

    finally:
        cactus_destroy(handle)
        print("\nDestroyed model handle.")


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(run()))
