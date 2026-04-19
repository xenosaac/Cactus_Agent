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

## Browser Result Display Follow-up

### Problem

Browser Use can complete a web task and write concrete findings to `results.md`,
but the user-facing final answer only says the details were saved for review.
The native UI has no visible place to open that saved file, so the result appears
missing even though Browser Use did the work.

### Hypotheses

- R1: Browser Use returns only the final "saved to file" sentence to the adapter,
  while the real user-facing data lives in the Browser Use file system.
- R2: The adapter truncates useful output before the planner can synthesize the
  final response.
- R3: The UI reducer prefers the short `summary` field over the full
  `final_text`, clipping or replacing detailed final answers.
- R4: Browser Use did not actually find the flight details.

### Experiments

#### Experiment 1
- Hypothesis tested: R4
- Expected if true: The Browser Use `results.md` file is absent or contains no
  concrete flight details.
- Expected if false: The file contains the actual flight result.
- Change / probe: Read the Browser Use result file from the user's run.
- Result: `results.md` contains Frontier, `$124`, 1 stop via ONT, 3h 44m,
  departing 9:18 am and arriving 1:02 pm.
- Conclusion: R4 is false; Browser Use found the answer.

#### Experiment 2
- Hypothesis tested: R1
- Expected if true: Browser Use exposes a per-agent file system with
  `browseruse_agent_data/results.md`, and `final_result()` may only reference
  that file.
- Expected if false: There is no stable file-system path to read from the
  adapter.
- Change / probe: Inspected Browser Use `Agent` and `AgentHistoryList`.
- Result: `Agent` has `file_system_path`, and Browser Use creates
  `browseruse_agent_data/results.md` under it. `AgentHistoryList` also exposes
  `extracted_content()`.
- Conclusion: R1 is supported.

#### Experiment 3
- Hypothesis tested: R3
- Expected if true: `AGENT_DONE` rendering uses `summary` before `final_text`.
- Expected if false: The UI already prefers full final text.
- Change / probe: Inspected `voice_agent/ui/native/reducer.py`.
- Result: `AGENT_DONE` sets `result=e.summary or e.final_text or "Done"`.
- Conclusion: R3 is supported.

### Fix Direction

- Have `BrowserUseAdapter` read Browser Use result artifacts and extracted
  content after `agent.run()`.
- If the final Browser Use sentence only says details are attached/saved, replace
  it with the artifact contents before returning `ToolResult`.
- Increase successful Browser Use content enough for the planner to see concrete
  results.
- Tell the planner to include concrete tool details directly instead of saying
  they were saved for review.
- Make the native reducer prefer full `final_text` on `AGENT_DONE`.

### Evidence-backed Root Cause

Browser Use completed the flight task and wrote the real answer into its file
system, but `BrowserUseAdapter` returned only the final pointer sentence to the
outer planner. The planner then faithfully produced a vague final answer. The UI
made this worse by preferring the clipped `summary` field over `final_text` for
the done state.

### Fix

- `BrowserUseAdapter` now reads Browser Use `results.md` / `todo.md` artifacts
  and `AgentHistoryList.extracted_content()` after `agent.run()`.
- If Browser Use's final sentence only points at an attached/saved artifact, the
  adapter returns the artifact content instead.
- Successful Browser Use tool output now keeps up to 2000 characters instead of
  truncating at 500.
- The planner prompt now explicitly says to include concrete tool details
  directly and not punt to saved/attached files.
- The orchestrator replaces a vague Browser Use "saved for review" final answer
  with the concrete Browser Use tool content as a deterministic guardrail.
- The native reducer now prefers full `final_text` over the short `summary` on
  `AGENT_DONE`.

### Verification

- Focused Ruff: `cactus/venv/bin/ruff check voice_agent/agent/browser_use_adapter.py voice_agent/agent/orchestrator.py voice_agent/agent/system_prompts.py voice_agent/ui/native/reducer.py tests/test_browser_use_adapter.py tests/test_orchestrator.py tests/test_native_reducer.py` -> all checks passed.
- Focused regression: `cactus/venv/bin/python -m pytest tests/test_browser_use_adapter.py tests/test_orchestrator.py tests/test_native_reducer.py -q` -> 8 passed.
- Full non-eval regression: `cactus/venv/bin/python -m pytest tests/ -q --ignore=tests/evals` -> 112 passed.

## Chrome Browser Use Follow-up

### Problem

The user needs Browser Use to operate inside a browser session that can be
logged in to LinkedIn. The first attempt to attach to default Chrome via CDP did
not expose a debugging port. The fallback attempt to let Browser Use launch the
real default Chrome profile opened a black Chrome window and did not progress.

### Hypotheses

- C1: Chrome no longer exposes remote debugging for the default user data
  directory, so `--remote-debugging-port=9222` with the normal profile silently
  fails to bind.
- C2: Launching Playwright/Browser Use directly against the normal Chrome user
  data directory is blocked by profile locks or Chrome profile safety rules,
  resulting in a black window and timeout.
- C3: Browser Use cannot launch Google Chrome at all in this environment.
- C4: The browser task stalled in the planner or Browser Use LLM after a valid
  browser session was established.

### Experiments

#### Experiment 1
- Hypothesis tested: C1
- Expected if true: Launching Chrome with `--remote-debugging-port=9222` and
  the default profile shows the flag in the process list but `127.0.0.1:9222`
  does not listen.
- Expected if false: `curl http://127.0.0.1:9222/json/version` returns Chrome
  version metadata.
- Change / probe: Launched Google Chrome with `--remote-debugging-port=9222`
  and `--profile-directory=Default`, then checked `curl` and `lsof`.
- Result: Chrome process had the flag, but no process listened on port `9222`.
- Conclusion: C1 is supported.

#### Experiment 2
- Hypothesis tested: C2
- Expected if true: Browser Use logs show it launching against
  `~/Library/Application Support/Google/Chrome`, then timing out before a usable
  browser context appears.
- Expected if false: Browser Use should connect, navigate, and report page state.
- Change / probe: Ran the app with `VA_BROWSER_USER_DATA_DIR` pointed at the real
  Chrome profile and inspected `logs/voice_agent.log`.
- Result: Browser Use logged `Launching new local browser ... user_data_dir=
  "~/Library/Application Support/Google/Chrome"`, then `Browser operation timed
  out`; only a Chrome crashpad handler remained.
- Conclusion: C2 is supported.

### Fix Direction

Use a dedicated Chrome automation profile for Browser Use, not the normal Chrome
profile directory. Launch Chrome with CDP against that dedicated profile, wait
for `/json/version`, and connect Browser Use over CDP. The user can log in to
LinkedIn once in that dedicated profile, and the cookies will persist without
corrupting or locking the normal Chrome profile.

#### Experiment 3
- Hypothesis tested: C3
- Expected if true: Chrome with a dedicated automation profile also fails to
  expose CDP or render.
- Expected if false: Chrome exposes `127.0.0.1:9222/json/version` with a
  `webSocketDebuggerUrl`.
- Change / probe: Launched Google Chrome with
  `--remote-debugging-port=9222 --user-data-dir="$HOME/Library/Application Support/Cactus/ChromeCDP"`.
- Result: `curl http://127.0.0.1:9222/json/version` returned Chrome 147 version
  metadata and a `webSocketDebuggerUrl`.
- Conclusion: C3 is false. A dedicated Chrome automation profile is the working
  path.

### Evidence-backed Root Cause

The normal Chrome profile is not usable as a Browser Use automation target here.
Chrome 147 did not bind the remote debugging port for the default profile, and
Playwright timed out when asked to launch against
`~/Library/Application Support/Google/Chrome`. A dedicated automation profile
does expose CDP immediately.

### Fix

- Stop using the real default Chrome profile as Browser Use's
  `user_data_dir`.
- Use a dedicated Chrome profile at
  `$HOME/Library/Application Support/Cactus/ChromeCDP`.
- Connect Browser Use through `VA_BROWSER_CDP_URL=http://127.0.0.1:9222`.

### Verification

- Dedicated Chrome CDP probe: `curl http://127.0.0.1:9222/json/version`
  returned Chrome 147 metadata and `webSocketDebuggerUrl`.
- App restart: `VA_BROWSER_CDP_URL=http://127.0.0.1:9222 cactus/venv/bin/python -m voice_agent.main --ui webview`
  logged `Browser Use will connect over CDP` and emitted `voice_ready`.
- Browser Use smoke through the app: typed `/wake` command for `https://example.com`
  connected over CDP, navigated successfully, extracted `Example Domain`, and
  reached `AGENT_DONE`.

## CDP Repeat Failure / Gmail Follow-up

### Problem

After the first successful dedicated-Chrome Browser Use smoke, later browser
tasks failed immediately. The UI reported that the web browser kept stopping
unexpectedly. The user also reported that Gmail does not work.

### Symptoms

- Browser Use fails three consecutive times before doing page work.
- The Playwright error is
  `BrowserType.connect_over_cdp: Protocol error (Browser.setDownloadBehavior): Browser context management is not supported`.
- A previous successful Browser Use run logged
  `Closing cdp_url=http://127.0.0.1:9222 browser context`, even though the Chrome
  process was user-launched and should remain reusable.
- Gmail tools register successfully, but a live `gmail.search_emails` call
  returned `Error: unauthorized_client`.

### Hypotheses

- P1: The CDP BrowserSession is created with `keep_alive=False`, so Browser Use
  closes the attached Chrome context after a run and leaves later CDP attaches in
  a broken state.
- P2: Passing the session through Browser Use's legacy `browser=` argument causes
  the agent to treat the session as owned and close it.
- P3: Chrome itself crashed or stopped exposing the CDP endpoint.
- G1: Gmail MCP is registered, but OAuth credentials are invalid for this account
  or app, producing `unauthorized_client`.

### Experiments

#### Experiment 1
- Hypothesis tested: P1
- Expected if true: Browser Use package code only auto-sets CDP keep-alive when
  `browser_profile.keep_alive is None`; our factory sets it to `False`, so the
  auto-protection is bypassed.
- Expected if false: Browser Use should override `False` to `True` for CDP
  sessions.
- Change / probe: Inspected `browser_use/browser/session.py`.
- Result: `_set_browser_keep_alive()` only mutates the profile when
  `keep_alive is None`.
- Conclusion: P1 is strongly supported.

#### Experiment 2
- Hypothesis tested: P3
- Expected if true: `http://127.0.0.1:9222/json/version` would fail after the
  Browser Use crash.
- Expected if false: The endpoint still answers, but session/context state may be
  invalid.
- Change / probe: Checked CDP endpoint after the failed run.
- Result: `/json/version` still returned Chrome metadata.
- Conclusion: P3 is not the primary failure.

#### Experiment 3
- Hypothesis tested: G1
- Expected if true: Gmail tool dispatch reaches the MCP server, then the server
  returns an OAuth error rather than a tool-registration error.
- Expected if false: Gmail tools would be absent or dispatch would fail before
  the MCP call.
- Change / probe: Searched recent logs for Gmail dispatch.
- Result: `gmail.search_emails` dispatched and returned
  `Error: unauthorized_client`.
- Conclusion: G1 is supported. Gmail needs OAuth/client credential repair; it is
  not blocked on browser automation.

#### Experiment 4
- Hypothesis tested: P2 / CDP target selection
- Expected if true: Browser Use connects to Chrome but selects a non-web CDP
  target, then fails before navigating.
- Expected if false: Browser Use should begin on a normal `about:blank` or
  `https://...` page and navigate.
- Change / probe: Ran a Browser Use smoke after the keep-alive patch and
  inspected `/json/list`.
- Result: Browser Use selected
  `chrome://omnibox-popup.top-chrome/omnibox_popup_aim.html`; logs then showed
  `Emulation.setDeviceMetricsOverride: Target does not support metrics override`
  and a screenshot timeout.
- Conclusion: CDP target selection is a second browser failure. The adapter
  needs to close/avoid internal Chrome top-chrome targets and select a usable
  page before handing the session to Browser Use.

### Fix Direction

- Let Browser Use own the CDP keep-alive decision by defaulting
  `VA_BROWSER_KEEP_ALIVE` to unset/`None`, not `False`.
- Keep explicitly attached CDP/PID sessions reusable across tasks.
- Pass Browser Use sessions through the explicit `browser_session=` argument.
- Surface incomplete Browser Use histories with their actual first error so the
  UI/planner does not hide protocol failures behind generic wording.
- Before each CDP Browser Use run, close internal `chrome://omnibox-popup`
  targets, ensure at least one normal page target exists, start the session, and
  point Browser Use at a usable page.
- Treat Gmail as a separate OAuth repair after Browser Use is stable.

### Fix

- Changed `browser_keep_alive` default from `False` to `None`, allowing Browser
  Use to set `keep_alive=True` automatically for CDP/PID-attached browsers.
- Passed reusable sessions via Browser Use's explicit `browser_session=`
  argument.
- Added CDP preflight to close `chrome://omnibox-popup` page targets and create a
  normal `about:blank` target if no usable page exists.
- Started external CDP/PID sessions before handing them to Browser Use, then
  selected a usable page so Browser Use does not automate Chrome UI targets.
- Improved Browser Use failed-history handling so protocol errors appear in the
  tool result.

### Verification

- Focused Ruff:
  `cactus/venv/bin/ruff check voice_agent/agent/browser_use_adapter.py tests/test_browser_use_adapter.py voice_agent/config.py tests/test_config.py tests/test_browser_session_factory.py`
  -> all checks passed.
- Focused regression:
  `cactus/venv/bin/python -m pytest tests/test_browser_use_adapter.py tests/test_browser_session_factory.py tests/test_config.py -q`
  -> 16 passed.
- Full non-eval regression:
  `cactus/venv/bin/python -m pytest tests/ -q --ignore=tests/evals`
  -> 119 passed.
- Live app smoke 1: `Open https://example.com in the browser and report the page
  heading.` completed with final text `The page heading for example.com is
  "Example Domain".`
- Live app smoke 2 in the same running process: `Open https://example.org in the
  browser and report the page heading.` completed with final text `The page
  heading is "Example Domain".`
- Live logs show `BrowserSession.stop() called but keep_alive=True`, confirming
  the attached Chrome CDP context is no longer closed between Browser Use tasks.
