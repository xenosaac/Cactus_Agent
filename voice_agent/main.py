"""Entry point. Bootstraps Cactus models, MCP pool, tool router, orchestrator,
voice loop, and the CompanionServer (HTTP + WebSocket for text fallback).

The UI is a native PySide6 Qt window (voice_agent/ui/native/). The old
pywebview / React path is gone — WKWebView silently refused to navigate to
localhost, verified via access-log (zero requests, no loaded event).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
import threading
import webbrowser
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from voice_agent.events import AgentEvent, EventType, bus
from voice_agent.voice.command_assembler import CommandAssembler, CommandDecision

if TYPE_CHECKING:
    from voice_agent.agent.orchestrator import AgentOrchestrator
    from voice_agent.voice.debug_recorder import VoiceDebugRecorder
    from voice_agent.voice.stt import StreamingWhisper
    from voice_agent.voice.vad import VADListener
    from voice_agent.voice.wake_filter import WakeFilter

log = logging.getLogger("voice_agent")


# ── yes/no/undo recognition ──────────────────────────────────────────────────
# Used to parse post-wake utterances when the orchestrator is awaiting a
# confirm/cancel, or (at DONE state) waiting for undo.
_YES_RE = re.compile(
    r"^\s*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|do it|go ahead|go for it|confirm|proceed|please)\b",
    re.IGNORECASE,
)
_NO_RE = re.compile(
    r"^\s*(?:no|nope|nah|cancel|stop|don'?t|never mind|abort|skip)\b",
    re.IGNORECASE,
)
_UNDO_RE = re.compile(r"^\s*(?:undo|revert|take that back)\b", re.IGNORECASE)


def classify_intent(text: str) -> str | None:
    """Return 'confirm' | 'cancel' | 'undo' | None for a short utterance."""
    if _YES_RE.match(text):
        return "confirm"
    if _NO_RE.match(text):
        return "cancel"
    if _UNDO_RE.match(text):
        return "undo"
    return None


async def voice_loop(
    whisper: StreamingWhisper,
    wake_filter: WakeFilter,
    orchestrator: AgentOrchestrator,
    vad: VADListener,
    debug_recorder: VoiceDebugRecorder | None = None,
    push_to_talk_only: bool = False,
) -> None:
    """Main mic loop.

    Modes of interpretation for each transcribed utterance:
      1. Push-to-talk mode: Space-down/Space-up cuts exactly one utterance;
         no wake phrase is required.
      2. Orchestrator awaiting confirm/cancel → classify yes/no/undo.
      3. Legacy wake mode: external wake or wake phrase arms one command.
      4. Otherwise → drop silently (ambient speech).
    """
    assembler = CommandAssembler(wake_filter)
    # Subscribe to WAKE_DETECTED on the bus. Any external path that fires one
    # (Space or HTTP /wake) arms the next utterance as a command. WAKE_DETECTED
    # events we publish for voice wake phrases are ignored here; the assembler
    # has already handled that transcript.
    self_wake_events = {"count": 0}

    async def _on_event(e: AgentEvent) -> None:
        if e.type == EventType.PTT_START:
            vad.start_push_to_talk("space")
            return
        if e.type == EventType.PTT_END:
            vad.stop_push_to_talk("space")
            return
        if e.type == EventType.WAKE_DETECTED:
            if push_to_talk_only:
                return
            if e.summary == "typed_wake":
                return
            if self_wake_events["count"]:
                self_wake_events["count"] -= 1
                return
            assembler.arm()

    bus.subscribe(_on_event)
    await bus.publish(AgentEvent(
        type=EventType.VOICE_READY,
        summary="hold_space_to_talk",
    ))

    # Background run_turn tasks — we DO NOT await them here, otherwise
    # voice_loop blocks waiting on confirmation and the user's "yes" never
    # gets transcribed. Holding strong refs prevents GC while they're live.
    turn_tasks: set[asyncio.Task[None]] = set()

    async def _run_turn_bg(cmd: str) -> None:
        try:
            await orchestrator.run_turn(cmd)
        except Exception as exc:  # noqa: BLE001
            log.exception("run_turn crashed: %s", exc)
            await bus.publish(AgentEvent(
                type=EventType.AGENT_ERROR, error=str(exc),
            ))

    segment_source = (
        vad.push_to_talk_segments() if push_to_talk_only else vad.segments()
    )
    async for seg in segment_source:
        awaiting_confirm = orchestrator.awaiting_confirm()
        emit_stt_events = push_to_talk_only or awaiting_confirm or assembler.armed
        try:
            transcript = await whisper.transcribe(
                seg.pcm_int16,
                emit_events=emit_stt_events,
            )
        except Exception as exc:  # noqa: BLE001
            # Whisper can time out on noisy / empty segments. Drop this segment
            # and keep listening — don't take down the whole stack.
            log.warning("whisper failed on segment: %s", exc)
            continue
        text = (transcript.text or "").strip()
        if not text:
            if debug_recorder is not None:
                debug_recorder.record(seg, transcript, None)
            continue

        # Mode 1: waiting for yes/no.
        if awaiting_confirm:
            await bus.publish(AgentEvent(type=EventType.STT_FINAL, final_text=text))
            intent = classify_intent(text)
            if intent in ("confirm", "cancel"):
                log.info("voice intent while awaiting: %s (text=%r)", intent, text)
                orchestrator.deliver_intent(intent)
                if debug_recorder is not None:
                    debug_recorder.record(
                        seg,
                        transcript,
                        CommandDecision(
                            kind="dispatch",
                            command=intent,
                            reason="confirmation_intent",
                        ),
                    )
            else:
                log.info("ignored utterance while awaiting confirm: %r", text)
                if debug_recorder is not None:
                    debug_recorder.record(
                        seg,
                        transcript,
                        CommandDecision(
                            kind="ignore",
                            reason="awaiting_confirm_unrecognized",
                        ),
                    )
            continue

        if push_to_talk_only:
            decision = assembler.process_direct(text, forced=seg.forced)
        else:
            decision = assembler.process(text, forced=seg.forced)
        log.info(
            "voice_loop command_decision kind=%s reason=%s command=%r "
            "wake_detected=%s armed_after=%s forced=%s text=%r",
            decision.kind,
            decision.reason,
            decision.command[:120],
            decision.wake_detected,
            decision.armed_after,
            seg.forced,
            text[:120],
        )

        if debug_recorder is not None:
            debug_recorder.record(seg, transcript, decision)

        if push_to_talk_only:
            await bus.publish(AgentEvent(type=EventType.STT_FINAL, final_text=text))
            if decision.kind != "dispatch":
                continue
            log.info("voice_loop run_turn command=%r", decision.command)
            task = asyncio.create_task(_run_turn_bg(decision.command))
            turn_tasks.add(task)
            task.add_done_callback(turn_tasks.discard)
            continue

        if decision.kind == "ignore" and not decision.wake_detected:
            continue

        await bus.publish(AgentEvent(type=EventType.STT_FINAL, final_text=text))

        if decision.wake_detected:
            self_wake_events["count"] += 1
            await bus.publish(AgentEvent(type=EventType.WAKE_DETECTED))

        if decision.kind != "dispatch":
            continue

        log.info("voice_loop run_turn command=%r", decision.command)
        task = asyncio.create_task(_run_turn_bg(decision.command))
        turn_tasks.add(task)
        task.add_done_callback(turn_tasks.discard)


async def _intent_consumer(
    intent_queue: asyncio.Queue[str],
    orchestrator: AgentOrchestrator,
) -> None:
    """Bridge HTTP /wake /confirm /cancel /undo endpoints → orchestrator.

    The HTTP server pushes tokens onto `intent_queue`; we route each one
    either to the orchestrator's internal confirmation queue or to a fresh
    turn (for typed utterances).
    """
    async def _run_turn_bg(utterance: str) -> None:
        try:
            await orchestrator.run_turn(utterance)
        except Exception as exc:  # noqa: BLE001
            log.exception("run_turn failed on manual intent: %s", exc)
            await bus.publish(AgentEvent(
                type=EventType.AGENT_ERROR, error=str(exc),
            ))

    bg_tasks: set[asyncio.Task[None]] = set()

    while True:
        intent = await intent_queue.get()
        if intent.startswith("utter:"):
            # Typed command came in via POST /wake. Spawn the turn as a
            # background task so this consumer stays live and can deliver
            # subsequent /confirm /cancel intents to the orchestrator's
            # internal confirmation queue. Otherwise we deadlock.
            utterance = intent[6:]
            log.info("Manual utterance intent: %r", utterance)
            task = asyncio.create_task(_run_turn_bg(utterance))
            bg_tasks.add(task)
            task.add_done_callback(bg_tasks.discard)
            continue

        if intent == "confirm":
            orchestrator.deliver_intent("confirm")
        elif intent == "cancel":
            orchestrator.deliver_intent("cancel")
        elif intent == "undo":
            log.info("undo requested — not yet implemented")
            await bus.publish(AgentEvent(
                type=EventType.UNDO_REQUESTED,
                summary="undo not implemented yet",
            ))
        else:
            log.warning("unknown intent: %s", intent)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="voice-agent")
    p.add_argument("--host", default="127.0.0.1", help="HTTP host")
    p.add_argument("--port", type=int, default=8765, help="HTTP port")
    p.add_argument(
        "--ui",
        choices=("webview", "browser", "none"),
        default="webview",
        help=(
            "how to show the UI: 'webview' = native PySide6 Qt Companion "
            "window (default), 'browser' = open URL in default browser "
            "(server endpoints only, no UI), 'none' = headless backend only"
        ),
    )
    p.add_argument(
        "--no-voice",
        action="store_true",
        help="do NOT start voice/mic/orchestrator (UI-only mode for bring-up)",
    )
    p.add_argument(
        "--audio-test",
        action="store_true",
        help=(
            "capture 3 s from the default mic, save to /tmp/voice_agent_mictest.wav, "
            "and exit. Use this to verify the capture chain (device, permission, "
            "sounddevice) without running the full agent."
        ),
    )
    p.add_argument(
        "--voice-debug-only",
        action="store_true",
        help=(
            "run only mic → VAD → Whisper → wake-command diagnostics. "
            "Does not initialize planner, MCP, Browser Use, or tools."
        ),
    )
    return p.parse_args()


async def _run_voice_stack(
    settings: Any, intent_queue: asyncio.Queue[str]
) -> None:
    """Heavy runtime: load Cactus models, spawn MCP subprocesses, run voice loop.

    Boot order matters here. VADListener opens the mic, which triggers the
    macOS permission prompt the first time. If we bury that behind the Cactus
    model load and the MCP subprocess spawn, the user sits in front of a black
    window for 12+ seconds with no prompt, concludes the mic is broken, and
    rage-quits. So: open the mic FIRST, then do the heavy work.
    """
    # Local imports so `--ui none --no-voice` can run without the cactus stack loaded.
    from contextlib import AsyncExitStack

    from cactus.python.src.cactus import cactus_destroy, cactus_init
    from voice_agent.agent.browser_session_factory import create_browser_session
    from voice_agent.agent.browser_use_adapter import BrowserUseAdapter
    from voice_agent.agent.cactus_chat_model import CactusChatModel
    from voice_agent.agent.desktop_app_launcher import DesktopAppLauncher
    from voice_agent.agent.gemini_client import GeminiClient
    from voice_agent.agent.mcp_client import MCPClientPool, MCPServerSpec
    from voice_agent.agent.orchestrator import AgentOrchestrator
    from voice_agent.agent.screen_reader_tool import VisibleScreenReaderTool
    from voice_agent.agent.tool_router import ToolRouter
    from voice_agent.agent.vision_desktop_tool import VisionDesktopTool
    from voice_agent.config import Mode
    from voice_agent.voice.debug_recorder import VoiceDebugRecorder
    from voice_agent.voice.stt import StreamingWhisper
    from voice_agent.voice.vad import VADListener
    from voice_agent.voice.wake_filter import WakeFilter

    log.info(
        "First launch on this machine will prompt for Microphone (and, if you "
        "use vision, Screen Recording). If no prompt appears, grant Python "
        "access in System Settings → Privacy & Security."
    )

    # Stage 1: open mic before anything heavy, so the permission prompt fires
    # while Cactus is warming up instead of after.
    vad_handle = cactus_init(str(settings.vad_weights), None, False)
    stack = AsyncExitStack()
    try:
        vad = await stack.enter_async_context(VADListener(
            vad_handle,
            sample_rate=settings.sample_rate_hz,
            frame_ms=settings.vad_frame_ms,
            silence_threshold_ms=settings.vad_silence_ms,
            min_speech_ms=settings.vad_min_speech_ms,
            max_speech_ms=settings.vad_max_speech_ms,
            threshold=settings.vad_threshold,
            min_segment_rms=settings.vad_min_segment_rms,
            push_to_talk_capture=True,
            bus=bus,
        ))
        log.info("mic open; loading Cactus models…")
    except Exception:
        cactus_destroy(vad_handle)
        raise

    planner_handle = cactus_init(str(settings.gemma4_weights), None, False)
    whisper_handle = cactus_init(str(settings.whisper_weights), None, False)

    gemini = GeminiClient(settings) if settings.mode == Mode.HYBRID else None

    try:
        # Absolute credential path — the child npx process has a different cwd
        # than our Python process, so relative paths miss.
        cred_path = str(settings.google_oauth_credentials.resolve())
        # Token path for gcal — where it saves access/refresh tokens after
        # first-run consent. Use project-local so tokens don't leak into $HOME
        # between projects.
        project_root = Path(__file__).resolve().parent.parent
        gcal_token_path = str((project_root / "token.json").resolve())

        mcp_specs = [
            MCPServerSpec(
                name="gcal",
                command="npx",
                args=["-y", "@cocal/google-calendar-mcp"],
                env={
                    # Try every common env-var name the package may look at.
                    "GOOGLE_OAUTH_CREDENTIALS": cred_path,
                    "GOOGLE_CREDENTIALS": cred_path,
                    "GOOGLE_CALENDAR_MCP_CREDENTIALS_PATH": cred_path,
                    "GOOGLE_CALENDAR_MCP_CREDENTIALS": cred_path,
                    "GOOGLE_CALENDAR_MCP_TOKEN_PATH": gcal_token_path,
                },
            ),
        ]
        try:
            mcp_tools = await stack.enter_async_context(MCPClientPool(mcp_specs))
        except Exception as exc:  # noqa: BLE001
            log.warning("MCP pool failed to start (continuing without MCP): %s", exc)
            mcp_tools = []

        try:
            browser = create_browser_session(settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("Browser Use unavailable: %s", exc)
            browser = None

        llm = CactusChatModel(settings, planner_handle, gemini)

        # Browser Use's internal Agent runs its own prompting loop. Its native
        # ChatGoogle wrapper currently fails in this environment from the async
        # google-genai path before the browser can act, so route Browser Use
        # through our sync-to-thread GeminiClient and a separate browser model.
        bu_llm: Any = llm
        if settings.mode == Mode.HYBRID and gemini is not None:
            browser_settings = settings.model_copy(update={
                "gemini_model": settings.browser_model,
                "planner_temperature": settings.browser_temperature,
            })
            bu_llm = GeminiClient(browser_settings)

        tools: list[Any] = list(mcp_tools)
        tools.append(DesktopAppLauncher())
        if browser is not None:
            tools.append(BrowserUseAdapter(bu_llm, browser, bus))
        tools.append(VisibleScreenReaderTool(settings, planner_handle, gemini))
        tools.append(VisionDesktopTool(settings, planner_handle, gemini))

        router = ToolRouter(tools, timeout_s=settings.tool_timeout_s)
        orchestrator = AgentOrchestrator(settings, llm, router, bus)
        whisper = StreamingWhisper(whisper_handle, settings, bus=bus)
        wake = WakeFilter(settings.wake_phrases)
        debug_recorder = VoiceDebugRecorder(
            enabled=settings.voice_debug,
            output_dir=settings.voice_debug_dir,
        )
        vad.flush("voice_stack_ready")

        # Run the mic voice loop + the HTTP-intent consumer side-by-side.
        await asyncio.gather(
            voice_loop(
                whisper,
                wake,
                orchestrator,
                vad,
                debug_recorder,
                push_to_talk_only=True,
            ),
            _intent_consumer(intent_queue, orchestrator),
        )
    finally:
        await stack.aclose()
        cactus_destroy(planner_handle)
        cactus_destroy(whisper_handle)
        cactus_destroy(vad_handle)


async def _run_voice_debug_only(settings: Any) -> None:
    """Headless mic → VAD → Whisper diagnostics.

    This intentionally avoids planner/tool initialization so bad voice
    behavior can be isolated without side effects.
    """
    from contextlib import AsyncExitStack

    from cactus.python.src.cactus import cactus_destroy, cactus_init
    from voice_agent.voice.command_assembler import CommandAssembler
    from voice_agent.voice.debug_recorder import VoiceDebugRecorder
    from voice_agent.voice.stt import StreamingWhisper
    from voice_agent.voice.vad import VADListener
    from voice_agent.voice.wake_filter import WakeFilter

    vad_handle = cactus_init(str(settings.vad_weights), None, False)
    whisper_handle: int | None = None
    stack = AsyncExitStack()
    try:
        vad = await stack.enter_async_context(VADListener(
            vad_handle,
            sample_rate=settings.sample_rate_hz,
            frame_ms=settings.vad_frame_ms,
            silence_threshold_ms=settings.vad_silence_ms,
            min_speech_ms=settings.vad_min_speech_ms,
            max_speech_ms=settings.vad_max_speech_ms,
            threshold=settings.vad_threshold,
            min_segment_rms=settings.vad_min_segment_rms,
            bus=bus,
        ))
        log.info("voice-debug-only: mic open; loading Whisper")
        whisper_handle = cactus_init(str(settings.whisper_weights), None, False)
        whisper = StreamingWhisper(whisper_handle, settings, bus=bus)
        assembler = CommandAssembler(WakeFilter(settings.wake_phrases))
        recorder = VoiceDebugRecorder(
            enabled=settings.voice_debug,
            output_dir=settings.voice_debug_dir,
        )
        vad.flush("voice_debug_ready")
        log.info("voice-debug-only ready; speak wake phrase + command")

        async for seg in vad.segments():
            try:
                transcript = await whisper.transcribe(seg.pcm_int16)
            except Exception as exc:  # noqa: BLE001
                log.exception(
                    "voice-debug-only: whisper failed segment_id=%s: %s",
                    seg.segment_id,
                    exc,
                )
                continue
            decision = assembler.process(transcript.text, forced=seg.forced)
            recorder.record(seg, transcript, decision, mode="voice_debug_only")
            log.info(
                "voice-debug-only segment_id=%d forced=%s dur_ms=%.0f "
                "text=%r decision=%s reason=%s command=%r raw=%r",
                seg.segment_id,
                seg.forced,
                seg.duration_ms,
                transcript.text[:160],
                decision.kind,
                decision.reason,
                decision.command[:160],
                transcript.raw[:220],
            )
    finally:
        await stack.aclose()
        if whisper_handle is not None:
            cactus_destroy(whisper_handle)
        cactus_destroy(vad_handle)


async def _run_backend(
    args: argparse.Namespace,
    intent_queue_holder: list[asyncio.Queue[str]] | None = None,
) -> None:
    """Run the HTTP server + (optionally) the voice stack concurrently.

    The asyncio.Queue must be constructed inside the running loop. If
    `intent_queue_holder` is given, the created queue is appended so the
    main thread can hand it to the Qt UI bridge.
    """
    from voice_agent.server import serve_forever

    intent_queue: asyncio.Queue[str] = asyncio.Queue()
    if intent_queue_holder is not None:
        intent_queue_holder.append(intent_queue)
    coros = [serve_forever(bus, intent_queue, host=args.host, port=args.port)]

    if args.no_voice:
        log.warning(
            "⚠️  --no-voice is ON. UI only. No mic, no models, no MCP. "
            "Drop the flag to run the real agent."
        )
    else:
        from voice_agent.config import load_settings
        settings = load_settings()
        coros.append(_run_voice_stack(settings, intent_queue))

    # If any task fails, bring the rest down.
    await asyncio.gather(*coros)


def _launch_qt_app(
    intent_queue: asyncio.Queue[str],
    backend_loop: asyncio.AbstractEventLoop,
) -> int:
    """Native Qt Companion window. Blocks the main thread with the Qt event
    loop. Returns the QApplication exit code."""
    from PySide6.QtWidgets import QApplication

    from voice_agent.ui.native.bridge import make_bridge
    from voice_agent.ui.native.companion import CompanionWindow, install_fonts

    wlog = logging.getLogger("voice_agent.qt")
    wlog.info("building QApplication on main thread")
    app = QApplication.instance() or QApplication(sys.argv)
    install_fonts()

    bridge = make_bridge(intent_queue=intent_queue, backend_loop=backend_loop)
    window = CompanionWindow(bridge)
    window.show()
    wlog.info("qt_window_shown")
    code = app.exec()
    wlog.info("qt_exit_code=%d", code)
    bridge.detach()
    return code




def _install_logging() -> str:
    """Configure root logging: console + fresh file `./logs/voice_agent.log`.

    Returns the absolute log path so we can print it once on startup.
    """
    import os
    from logging.handlers import RotatingFileHandler
    from pathlib import Path

    level_name = os.environ.get("VA_LOG_LEVEL", "DEBUG").upper()
    level = getattr(logging, level_name, logging.DEBUG)
    log_dir = Path("logs").resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "voice_agent.log"
    # Fresh file per launch — previous diagnostics are archived with a .prev suffix.
    if log_path.exists():
        with suppress(Exception):
            log_path.replace(log_path.with_suffix(".log.prev"))

    fmt = "%(asctime)s.%(msecs)03d %(name)s %(levelname)s %(message)s"
    datefmt = "%H:%M:%S"
    root = logging.getLogger()
    root.setLevel(level)
    # Nuke prior handlers if Python re-imports us.
    for h in list(root.handlers):
        root.removeHandler(h)
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root.addHandler(console)
    file_h = RotatingFileHandler(log_path, maxBytes=20_000_000, backupCount=1)
    file_h.setLevel(level)
    file_h.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root.addHandler(file_h)
    # Tame overly chatty libs.
    logging.getLogger("httpx").setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.INFO)
    logging.getLogger("asyncio").setLevel(logging.INFO)
    return str(log_path)


def _run_audio_test() -> int:
    """Capture 3s from the default mic, write /tmp/voice_agent_mictest.wav.

    Pure diagnostic path — no VAD, no Whisper, no orchestrator. Used to
    isolate "is the mic even delivering PCM" from "is the rest of the
    pipeline broken".
    """
    import struct
    import wave

    log.info("=== audio-test mode ===")
    try:
        import sounddevice as sd
    except Exception as exc:  # noqa: BLE001
        log.error("sounddevice import failed: %s", exc)
        return 1

    try:
        default_in_idx = sd.default.device[0]
        info = sd.query_devices(default_in_idx)
        log.info(
            "input device idx=%s name=%r channels_in=%s default_sr=%s",
            default_in_idx, info.get("name"),
            info.get("max_input_channels"), info.get("default_samplerate"),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("couldn't query default input: %s", exc)

    sr = 16_000
    seconds = 3
    log.info("capturing %d s @ %d Hz int16 mono…", seconds, sr)
    try:
        rec = sd.rec(int(seconds * sr), samplerate=sr, channels=1, dtype="int16")
        sd.wait()
    except Exception as exc:  # noqa: BLE001
        log.exception("sd.rec failed: %s", exc)
        return 2

    # Stats on the captured buffer.
    samples = rec.flatten().tolist() if hasattr(rec, "flatten") else list(rec)
    n = len(samples)
    peak = max((abs(s) for s in samples), default=0) / 32768.0
    mean_sq = (sum(s * s for s in samples) / n) if n else 0.0
    rms = (mean_sq ** 0.5) / 32768.0
    zero_run = sum(1 for s in samples if s == 0)
    log.info(
        "captured samples=%d peak=%.4f rms=%.4f zero_samples=%d",
        n, peak, rms, zero_run,
    )

    out_path = "/tmp/voice_agent_mictest.wav"
    try:
        with wave.open(out_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(struct.pack(f"<{n}h", *samples))
    except Exception as exc:  # noqa: BLE001
        log.exception("failed to write %s: %s", out_path, exc)
        return 3

    log.info("wrote %s — play back with:  afplay %s", out_path, out_path)
    if peak < 0.01:
        log.warning(
            "peak amplitude < 1%% of full scale — mic may be muted, wrong "
            "device, or permission denied. Check: tccutil reset Microphone "
            "then relaunch, or System Settings → Sound → Input."
        )
    return 0


def _log_diagnostics() -> None:
    """One-shot startup dump of everything that might bite later."""
    import platform
    try:
        import sounddevice as sd
        sd_ver = getattr(sd, "__version__", "?")
    except Exception as exc:  # noqa: BLE001
        sd_ver = f"import_failed:{exc}"
    log.info(
        "diagnostics python=%s platform=%s sounddevice=%s",
        platform.python_version(), platform.platform(), sd_ver,
    )


def main() -> int:
    log_path = _install_logging()
    log.info("=== voice_agent starting ===  log_file=%s", log_path)
    _log_diagnostics()
    args = _parse_args()
    if args.audio_test:
        return _run_audio_test()
    if args.voice_debug_only:
        from voice_agent.config import Settings
        try:
            asyncio.run(_run_voice_debug_only(Settings()))
        except KeyboardInterrupt:
            log.info("voice-debug-only interrupted")
            return 130
        return 0
    url = f"http://{args.host}:{args.port}/"
    log.debug("parsed args: %r", vars(args))

    if args.ui == "none":
        asyncio.run(_run_backend(args))
        return 0

    # UI wants the main thread (native Qt) or a delayed browser open.
    # Run asyncio backend in a background thread.
    loop = asyncio.new_event_loop()
    backend_exc: list[BaseException] = []
    intent_queue_holder: list[asyncio.Queue[str]] = []

    def _thread_target() -> None:
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                _run_backend(args, intent_queue_holder=intent_queue_holder)
            )
        except BaseException as exc:  # noqa: BLE001
            backend_exc.append(exc)

    t = threading.Thread(target=_thread_target, name="voice-agent-backend", daemon=True)
    t.start()

    # Wait briefly for the server to bind before opening the UI at it.
    import time
    for _ in range(50):  # up to 5 s
        if _port_open(args.host, args.port):
            break
        time.sleep(0.1)
    # Wait for the intent queue to be created inside the backend loop
    # (it's needed by the Qt bridge).
    for _ in range(50):
        if intent_queue_holder:
            break
        time.sleep(0.1)

    try:
        if args.ui == "webview":
            if not intent_queue_holder:
                log.error("backend did not expose intent_queue; aborting UI")
                return 1
            qt_code = _launch_qt_app(intent_queue_holder[0], loop)
            # Hard exit — in-flight Cactus FFI calls (Whisper, Gemma) can hold
            # non-daemon threads that would keep the process alive after the
            # window closes. Users expect close-the-window-to-quit.
            log.info("forcing exit after Qt close (code=%d)", qt_code)
            os._exit(qt_code)
        elif args.ui == "browser":
            webbrowser.open(url)
            # Keep main thread alive while backend runs.
            t.join()
    except KeyboardInterrupt:
        log.info("shutdown requested")
        os._exit(130)

    # Best-effort drain
    if backend_exc:
        log.error("backend died: %s", backend_exc[0])
        return 1
    return 0


def _port_open(host: str, port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.1)
        return s.connect_ex((host, port)) == 0


if __name__ == "__main__":
    sys.exit(main())
