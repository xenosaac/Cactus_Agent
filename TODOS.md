# TODOs

Deferred work from `/plan-eng-review` 2026-04-18. Not shipping in v1 (hackathon build). Captured so the thinking is not lost.

## Post-hackathon

### Low-power mode toggle
**What:** Config flag to disable continuous Whisper streaming and revert to wake-triggered transcription only (wake-word engine at hour 0, then Whisper only inside a triggered window).

**Why:** Continuous Whisper-small on Apple Silicon draws ~2–4 W incremental. Fine at a desk. Meaningful problem in battery-sensitive scenarios: hospice bedside, wheelchair-mounted Mac, travel. Motor-impaired users are disproportionately likely to be in these settings.

**Pros:** extends runtime by hours; makes the product credibly deployable outside a desk.

**Cons:** requires a wake-word engine (openWakeWord or Porcupine); custom training for "hey agent" if the defaults don't match the user's voice; adds a dependency.

**Context:** the design doc commits to continuous Whisper + substring wake-phrase for v1. This TODO is a strict addition, not a replacement.

**Depends on / blocked by:** field data — we should measure real battery drain on a test Mac before committing engineering time.

**Effort:** ~3 h wiring + ~3 h wake-word tuning.

---

### Barge-in / mid-execution interrupt
**What:** Allow the user to say "hey agent, stop" while an agent task is executing and have the agent cleanly abort.

**Why:** Current design has no way to stop a task mid-execution. If the agent goes down a wrong path (e.g., drafting the wrong email, navigating the wrong page), the user has to watch it finish or force-quit. For a motor-impaired user, force-quit by keyboard is not always possible.

**Pros:** real UX win; closes an obvious complaint a demo judge might raise.

**Cons:** non-trivial — requires the orchestrator to check for new wake events between every tool call AND support cooperative cancellation through MCP client, Browser Use, and VisionDesktopTool paths. Each tool layer needs a cancellation token.

**Context:** orchestrator.py currently loops uninterrupted until `agent_done` or `max_steps`. Add an `asyncio.Event` cancellation flag checked at each iteration and propagated to tool execution.

**Depends on / blocked by:** v1 event bus (done); wake-phrase filter (done).

**Effort:** ~6–8 h.

---

### Native-app MCP server suite beyond FaceTime
**What:** Proper MCP servers for Mail.app, Messages.app, Notes.app, Reminders.app, Safari (native), Finder, Calendar.app — each wrapping macOS Accessibility API (AXUI) and native frameworks (MessageUI, EventKit). Replaces `VisionDesktopTool` for apps that have AXUI coverage.

**Why:** `VisionDesktopTool` is a screen-vision fallback — works anywhere but hallucinates click coordinates. AXUI-based MCP servers are deterministic (widget tree is structured data) and much more reliable. The vision of "operate any Mac app by voice" lives here.

**Pros:** dramatically improves reliability on the most common Mac apps; removes VLM cost for most tasks; completes the "any app, any action, always local" narrative.

**Cons:** per-app work; each app's AXUI surface is different; some apps (Messages) have limited AXUI coverage and still need vision fallback. macOS version fragmentation matters (AXUI changes across OS versions).

**Context:** `vision_desktop_tool.py` is the v1 catch-all. This TODO builds a tiered router: AXUI-MCP first (when available), vision fallback second.

**Depends on / blocked by:** v1 ships (have a working demo); design-partner feedback on which apps matter most.

**Effort:** ~40–60 h for a suite of 5–6 apps.

---

### Wake-phrase false-positive eval on LibriSpeech
**What:** Eval harness that feeds the wake-phrase filter against LibriSpeech test-clean (neutral, conversational speech) and measures false-positive rate. Target <0.5%.

**Why:** Our v1 filter is substring + word-boundary regex. It's simple and fast but could false-positive on "hey agents of change" and similar. We commit a regression test with a small corpus but a real eval over LibriSpeech is the standard.

**Pros:** small effort, big confidence boost; detects regressions when the filter is tuned.

**Cons:** none really.

**Context:** `tests/evals/test_wake_phrase_fpr.py`. Download LibriSpeech test-clean once, run Whisper over each utterance, assert filter returns False.

**Depends on / blocked by:** v1 voice/vad.py landed.

**Effort:** ~2 h.

---

### E4B Local-only tool-calling investigation
**What:** Figure out why Gemma 4 E4B on Cactus is conservative about emitting tool calls — either refuses to call provided tools, or hallucinates tool names not in the manifest. Verified against live Cactus v1.7 on 2026-04-18 with multiple prompt variants.

**Why:** Local-only mode's tool-calling reliability directly affects HIPAA/privacy-sensitive demo credibility. Current behavior forces cloud_handoff → Gemini for most multi-tool tasks, which is fine for Hybrid mode but leaves Local-only as a narrow single-tool demo. Ideally we want E4B to reliably emit function calls in Local-only too.

**Pros:** closes the gap between "theoretical private mode" and "actually useful private mode."

**Cons:** may require Cactus-side changes (how `tools_json` gets rendered into the Gemma 4 native template). Could be a Cactus engine bug, not fixable from user code.

**Context:**
- Smoke tests show E4B DOES emit function_calls when given `PLANNER_SYSTEM`, but with hallucinated names (`web_search` instead of `get_weather`, `gcal:add_event` instead of a name from the manifest).
- With strict "use EXACT names from list" prompt: E4B refuses entirely ("I do not have a tool...").
- Evidence points to the Gemma 4 `<|tool>declaration:...<|tool|>` template either not being rendered by Cactus or being rendered incorrectly.

**Investigation paths:**
1. Compare cactus's Gemma 4 template application vs what the Gemma 4 function-calling doc specifies.
2. Try an older Cactus release (v1.6) if there's one.
3. File a Cactus issue with a minimal repro.
4. Consider fine-tuning E4B on our specific tool schema (post-hackathon).

**Depends on:** working E4B install (done).

**Effort:** ~4-8h investigation; fix may be weeks (upstream).

---

## Explicitly NOT captured (decided out)

- **iOS port** — target platform for v1 is macOS only, per 2026-04-18 decision. iOS is a separate product/rewrite; no TODO.
- **Windows / Linux port** — same reason. Not on the roadmap.
- **Barge-in via audio-level detection** (instead of wake phrase) — superseded by the wake-phrase barge-in approach above.
- **Multi-turn conversation memory** — architectural decision in v1 is per-turn stateless for reliability of 270M Gemma. Revisit only with larger models.
