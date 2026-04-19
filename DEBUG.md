# DEBUG

## Problem

The voice agent appears "dumb" because the planner is receiving damaged or incomplete transcripts from the voice pipeline. In the latest captured run, the planner did not fail to use tools; it was given a partial command and responded to that partial input.

## Symptoms

- User says a wake phrase and command, but the planner receives truncated text.
- Later correct transcripts are dropped because they no longer contain the wake phrase.
- Older logs show 5s Whisper timeouts; newer logs show Cactus streaming returns text, but sometimes with hallucinated or partial content.

## Known Facts

- Runtime is Hybrid mode for planner calls; `CactusChatModel` routes planning to Gemini when Gemini is available.
- Latest log shows VAD force-emitting a 6s segment before the user command fully resolved.
- Latest log: Whisper returned `Hey cactus! Hey cactus! Turn on the c-`.
- Voice loop dispatched `Hey cactus! Turn on the c-` immediately.
- Gemini answered that the request sounded cut off.
- Later Whisper returned `Turn on the calculator!`, but voice loop ignored it because there was no wake phrase.
- Mic capture is active and audio levels move, so this is not a dead microphone.

## Hypotheses

- H1: VAD startup backlog and force-emitted segments are feeding stale/partial audio into Whisper.
- H2: The wake dispatch policy is wrong: it dispatches the first wake-containing transcript instead of waiting for a complete command.
- H3: Whisper streaming output is sometimes wrong or hallucinated, and there is not enough captured evidence to replay or compare failures.
- H4: Planner/tool routing is the primary issue.

## Experiments

### Experiment 1
- Hypothesis tested: H2
- Expected if true: Logs show planner input differs from the user's complete command and is dispatched before later clean transcripts.
- Expected if false: Planner input should match the complete spoken command; failures should happen after planning/tool dispatch.
- Change / probe: Inspected `logs/voice_agent.log` for VAD, Whisper, wake filter, and `run_turn` sequence.
- Result: `run_turn START utterance='Hey cactus! Turn on the c-'` happened before `stt_final='Turn on the calculator!'`.
- Conclusion: Strong support for H2.

### Experiment 2
- Hypothesis tested: H1
- Expected if true: Logs show force-emitted segments and queue/ring buildup around bad transcripts.
- Expected if false: Segments should be cleanly finalized by silence before bad transcripts.
- Change / probe: Inspected latest VAD logs.
- Result: Bad wake transcript came from `vad force-emit long-speech segment max_ms=6000`.
- Conclusion: Strong support for H1.

### Experiment 3
- Hypothesis tested: H3
- Expected if true: Logs alone are insufficient to replay the bad PCM; we need WAV/metadata capture.
- Expected if false: Existing logs would provide enough audio evidence.
- Change / probe: Inspected current logging and STT code.
- Result: Logs include raw JSON and first bytes, but not replayable WAV segments.
- Conclusion: Add opt-in debug WAV and JSONL capture.

## Evidence

- `logs/voice_agent.log` around 22:09:03 shows the partial transcript dispatch and later dropped correct transcript.
- `voice_agent/main.py` previously kept the session armed indefinitely after wake, allowing later ambient speech to become commands.
- `voice_agent/voice/vad.py` previously capped active speech at 6000ms and did not mark forced segments in emitted metadata.

## Root Cause

The evidence points to the voice dispatch layer, not the planner. VAD/Whisper can emit partial forced segments, and the current wake handling immediately dispatches the first wake-containing transcript. Once the partial command is dispatched, the later correct transcript is ignored or treated as ambient speech.

## Fix

- Add replayable voice diagnostics.
- Flush startup mic backlog before voice processing begins.
- Mark forced VAD segments.
- Add command assembly that waits for a clean post-wake command and disarms after one dispatch.
- Add STT-only debug mode that does not initialize planner/tools.

## Verification

- Add unit tests for command assembly, VAD flush/debug metadata, and debug recorder WAV/JSONL.
- Focused regression: `cactus/venv/bin/python -m pytest tests/test_command_assembler.py tests/test_voice_debug.py tests/test_voice_loop.py tests/test_config.py tests/test_main_intents.py -q` -> 43 passed.
- Full non-eval regression: `cactus/venv/bin/python -m pytest tests/ -q --ignore=tests/evals` -> 90 passed.
- CLI smoke: `cactus/venv/bin/python -m voice_agent.main --help` shows `--voice-debug-only`.
- Ruff on new modules/tests: `cactus/venv/bin/ruff check voice_agent/voice/command_assembler.py voice_agent/voice/debug_recorder.py tests/test_command_assembler.py tests/test_voice_debug.py tests/test_voice_loop.py` -> all checks passed.
- Broad Ruff on touched runtime files still reports the repo's existing style backlog, so this fix leaves unrelated cleanup out of scope.

## Sensitivity Follow-up

### Problem

After the first successful command, the VAD kept treating low room noise and far-field speech as speech. That pushed the UI into transcription and caused Whisper to decode irrelevant background phrases.

### Hypotheses

- S1: Silero VAD threshold is too permissive for this mic/room.
- S2: Valid VAD segments need an additional energy floor before Whisper runs.
- S3: The command assembler is dispatching non-wake background speech.

### Evidence

- Logs after the Calculator test show background segments with low audio level, for example `rms=0.0213 peak=0.1421`, still being sent to Whisper.
- Whisper decoded one low-energy segment as `the vibe is turning right now.`
- Command assembler/orchestrator did not dispatch those as new commands while idle; the noisy behavior was VAD/STT activation, not planner dispatch.

### Fix Direction

- Make the Silero VAD probability threshold configurable and raise the default.
- Add a configurable post-VAD RMS floor so low-energy segments are dropped before Whisper.
- Keep idle ambient transcripts internal so no-wake background speech does not publish `whisper_start` or `stt_final` UI events.
- Keep confirmation handling and wake command assembly unchanged.

### Verification

- Focused sensitivity regression: `cactus/venv/bin/python -m pytest tests/test_voice_loop.py tests/test_voice_debug.py tests/test_config.py tests/test_command_assembler.py -q` -> 15 passed.
- Full non-eval regression: `cactus/venv/bin/python -m pytest tests/ -q --ignore=tests/evals` -> 92 passed.

## Push-to-Talk Follow-up

### Problem

Always-live wake-word transcription is still too noisy for the current room. Even when no-wake ambient speech is ignored, the system still spends Whisper time on background speech and can be accidentally armed by Space before a noisy segment finishes.

### Decision

Disable wake-word listening for the normal app path for now. Voice input is push-to-talk only: hold Space to capture audio, release Space to cut the utterance and send exactly that captured audio to Whisper.

### Implementation Direction

- Add explicit push-to-talk start/end events.
- Change the native UI Space key handling from one-shot wake to press/release push-to-talk.
- Add a VAD push-to-talk segment source that ignores mic frames while Space is not held.
- Keep typed `/wake` as a text/debug path, but stop using wake words for normal microphone commands.

### Verification

- Focused PTT regression: `cactus/venv/bin/python -m pytest tests/test_voice_loop.py tests/test_command_assembler.py tests/test_voice_debug.py tests/test_config.py -q` -> 18 passed.
- Full non-eval regression: `cactus/venv/bin/python -m pytest tests/ -q --ignore=tests/evals` -> 95 passed.
- Syntax check: `cactus/venv/bin/python -m py_compile voice_agent/ui/native/companion.py voice_agent/ui/native/bridge.py voice_agent/ui/native/reducer.py voice_agent/main.py voice_agent/voice/vad.py voice_agent/events.py` -> passed.

## No-Confirm / Hard Mute Follow-up

### Problem

Push-to-talk captured the command correctly, but the orchestrator still paused on `CONFIRMATION_REQUIRED`, so commands like "open Discord and check the top 10 messages" stopped after planning `open_app`. Also, after Space release the PortAudio callback still measured/queued frames, making the mic look live even though downstream command dispatch ignored background speech.

### Fix

- Removed the orchestrator confirmation wait from the tool-call path. Tool calls now publish `STEP_START` immediately and execute.
- Updated the planner system prompt to never ask for confirmation; push-to-talk is the explicit command boundary.
- In push-to-talk capture mode, the VAD callback now discards frames before queueing or audio-level accounting while Space is up.
- Reset the audio meter rollup on PTT start/stop so release visibly mutes the meter.

### Verification

- Focused regression: `cactus/venv/bin/python -m pytest tests/test_orchestrator.py tests/test_voice_loop.py tests/test_command_assembler.py tests/test_voice_debug.py -q` -> 16 passed.
- Full non-eval regression: `cactus/venv/bin/python -m pytest tests/ -q --ignore=tests/evals` -> 95 passed.
- Syntax check: `cactus/venv/bin/python -m py_compile voice_agent/agent/orchestrator.py voice_agent/agent/system_prompts.py voice_agent/voice/vad.py voice_agent/main.py voice_agent/ui/native/companion.py voice_agent/ui/native/reducer.py voice_agent/ui/native/bridge.py` -> passed.

## Startup Readiness Follow-up

### Problem

The native window became interactive before the backend voice loop subscribed to push-to-talk events. Pressing Space during model/MCP startup could visually move the UI to listening while the backend missed the capture start.

### Fix

- Added a `VOICE_READY` event emitted by `voice_loop` only after it subscribes to PTT events.
- UI starts in a visible startup state: "Starting Cactus..." and "Space is disabled until loading finishes."
- Space key presses before `VOICE_READY` are ignored and logged.
- Once `VOICE_READY` arrives, the UI switches to the normal push-to-talk instructions.

### Verification

- Focused regression: `cactus/venv/bin/python -m pytest tests/test_voice_loop.py tests/test_orchestrator.py -q` -> 6 passed.
- Full non-eval regression: `cactus/venv/bin/python -m pytest tests/ -q --ignore=tests/evals` -> 95 passed.
- Syntax check: `cactus/venv/bin/python -m py_compile voice_agent/events.py voice_agent/ui/native/reducer.py voice_agent/ui/native/companion.py voice_agent/main.py` -> passed.

## Discord Read Follow-up

### Problem

The user asked the push-to-talk agent to open Discord and check the top 10 messages. Discord launched, then the UI stayed in the acting state and appeared to stop.

### Hypotheses

- D1: The voice layer dropped the second half of the command.
- D2: The no-confirm flow still paused after `open_app`.
- D3: The planner continued correctly, but routed a read-only Discord task into the slow native desktop action loop.
- D4: The desktop action loop timed out and then retried a non-cancellable local vision call, extending the apparent hang.

### Evidence

- Latest log shows the PTT transcript was complete: `Log into Discord and check my top 10 messages.`
- The planner dispatched `open_app(app_name=Discord)` successfully, then dispatched `desktop_native_app(task=Check my top 10 messages.)`.
- The UI remained in `acting` while VAD stayed muted, so the mic was not the active failure.
- `ToolRouter` logged `tool=desktop_native_app timed out (attempt 1)` after 30 seconds and would retry once.
- `desktop_native_app` is a multi-step screenshot -> local Gemma vision -> pyautogui loop; it is suited to clicking/typing, not read-only message extraction.

### Conclusion

Strong support for D3 and D4. This failure is downstream of voice capture: the planner/tool layer needs a read-only screen inspection tool, and non-cancellable screen tools should not be retried after timeout.

### Fix Direction

- Add a `read_visible_screen` tool for visible app text/message summarization.
- Prefer Gemini vision for that one-shot read path in Hybrid mode; keep the existing local vision fallback only when Gemini is unavailable.
- Update planner rules so read/check/summarize native app content uses `read_visible_screen`, while clicking/typing stays on `desktop_native_app`.
- Disable retry-once for screen tools whose underlying work may continue in a background thread after timeout.

### Fix

- Added `read_visible_screen`, a read-only one-shot screen inspection tool.
- Added Gemini image understanding support for Hybrid screen reads.
- Registered `read_visible_screen` before the native desktop action loop.
- Updated planner/tool descriptions so native app read/check/summarize requests use `read_visible_screen`.
- Disabled retry-once for `desktop_native_app` and `read_visible_screen` timeouts.

### Verification

- Focused regression: `cactus/venv/bin/python -m pytest tests/test_tool_router.py tests/test_screen_reader_tool.py -q` -> 12 passed.
- Focused Ruff: `cactus/venv/bin/ruff check voice_agent/agent/gemini_client.py voice_agent/agent/screen_reader_tool.py voice_agent/agent/system_prompts.py voice_agent/agent/tool_router.py voice_agent/agent/desktop_app_launcher.py voice_agent/ui/native/reducer.py tests/test_tool_router.py tests/test_screen_reader_tool.py` -> all checks passed.
- Syntax check: `cactus/venv/bin/python -m py_compile voice_agent/agent/gemini_client.py voice_agent/agent/screen_reader_tool.py voice_agent/agent/system_prompts.py voice_agent/agent/tool_router.py voice_agent/main.py voice_agent/agent/desktop_app_launcher.py voice_agent/ui/native/reducer.py` -> passed.
- Full non-eval regression: `cactus/venv/bin/python -m pytest tests/ -q --ignore=tests/evals` -> 100 passed.

## Gemini Vision Migration Follow-up

### Problem

The read-only screen summarizer moved to Gemini, but `desktop_native_app` still used the local Gemma vision loop for icon/button clicking. The user observed that it did not understand which icon button to click.

### Evidence

- `read_visible_screen` used Gemini image understanding.
- `desktop_native_app` still called Cactus Gemma vision for each screenshot/action decision.
- Google's current Gemini API model docs list Gemini 3 Pro as `gemini-3-pro-preview`; there is no documented `gemini-3.1-pro` model ID in the checked docs.

### Fix

- Changed default Hybrid model to `gemini-3-pro-preview`.
- Added `VA_GEMINI_VISION_MODEL`, `VA_GEMINI_VISION_TIMEOUT_S`, and `VA_GEMINI_VISION_THINKING_LEVEL`.
- Updated `GeminiClient.describe_image()` to use the configured vision model, JSON output mode, and Gemini thinking level.
- Migrated `desktop_native_app` to use Gemini vision for click/key/scroll/wait decisions when Gemini is available.
- Kept local Gemma vision only as a fallback when no Gemini client exists.
- Expanded the desktop vision action schema with `scroll` and `wait`, and added explicit icon/button click guidance.

### Verification

- Focused regression: `cactus/venv/bin/python -m pytest tests/test_vision_desktop_tool.py tests/test_screen_reader_tool.py tests/test_config.py -q` -> 12 passed.
- Focused Ruff: `cactus/venv/bin/ruff check voice_agent/config.py voice_agent/agent/gemini_client.py voice_agent/agent/vision_desktop_tool.py voice_agent/agent/system_prompts.py voice_agent/agent/screen_reader_tool.py tests/test_vision_desktop_tool.py tests/test_screen_reader_tool.py tests/test_config.py` -> all checks passed.
- Syntax check: `cactus/venv/bin/python -m py_compile voice_agent/config.py voice_agent/agent/gemini_client.py voice_agent/agent/vision_desktop_tool.py voice_agent/agent/system_prompts.py voice_agent/agent/screen_reader_tool.py voice_agent/main.py tests/test_vision_desktop_tool.py` -> passed.
- Full non-eval regression: `cactus/venv/bin/python -m pytest tests/ -q --ignore=tests/evals` -> 103 passed.

## UI Preview / Browser Use Follow-up

### Problem

After a failed browser task, the UI showed a large empty card labeled `No preview`, and the spoken result said the browser agent had technical trouble.

### Hypotheses

- B1: The preview card is rendered even when the reducer has no preview payload.
- B2: Playwright/Chromium cannot launch.
- B3: Browser Use launches the browser, but its LLM provider fails before any navigation action.
- B4: The Browser Use adapter reports failed histories as successful tool results.

### Evidence

- `build_preview(None)` explicitly returns a card containing `No preview`.
- Logs show Browser Use launched Chromium and reached `about:blank`.
- Logs show Browser Use failed its first LLM step three times with `browser_use.llm.exceptions.ModelProviderError: ('', 502)`.
- The Browser Use model in that run was `gemini-3-pro-preview`.
- The adapter returned `ok=True` with an `AgentHistoryList(...)` string even though the history contained errors and no final result.

### Conclusion

B1, B3, and B4 are supported. This is not primarily a browser launch failure. The first fix should hide absent previews, use a separate stable Browser Use model instead of Gemini 3 Pro Preview, and normalize failed Browser Use histories to `ToolResult(ok=False)`.

### Fix

- Removed the visible `No preview` card by hiding preview containers when no preview payload exists.
- Removed the Browser Use dependency on Browser Use's native `ChatGoogle` wrapper, which failed in this environment with `ModelProviderError: ('', 502)` from the async google-genai path.
- Routed Browser Use through the repo's `GeminiClient`, which uses the synchronous google-genai call inside `asyncio.to_thread`.
- Added Browser Use compatibility metadata on `GeminiClient`: `provider`, `name`, `model`, and `model_name`.
- Added a Browser Use model override, defaulting to `gemini-2.5-flash`, while keeping planner/vision defaults on Gemini 3 Pro Preview.
- Disabled Browser Use vision and memory for now so Browser Use drives pages from DOM/state instead of screenshot payloads.
- Fixed Gemini response-schema sanitation for Browser Use's large Pydantic output schema by resolving `$ref` and stripping unsupported `additionalProperties`.
- Made Browser Use failed histories return `ToolResult(ok=False)` instead of stringifying the failed history as a successful result.

### Verification

- Live Browser Use LLM probe through Browser Use `ChatGoogle` reproduced `ModelProviderError: ('', 502)`.
- Live structured LLM probe through repo `GeminiClient` succeeded for both `gemini-3-pro-preview` and `gemini-2.5-flash`.
- Live Browser Use smoke succeeded: `Go to https://example.com and report the page heading` returned `ok=True` with `Example Domain`.
- Focused regression: `cactus/venv/bin/python -m pytest tests/test_gemini_client.py tests/test_browser_use_adapter.py -q` -> 5 passed.
- Focused Ruff on touched Browser/UI files -> all checks passed.
- Full non-eval regression: `cactus/venv/bin/python -m pytest tests/ -q --ignore=tests/evals` -> 109 passed.
