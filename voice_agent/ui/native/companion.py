"""CompanionWindow — native Qt port of the React Companion component.

Stages (idle / listening / transcribing / planning / confirming / acting /
done) each get a dedicated body layout. A reducer-driven render loop rebuilds
the minimum set of widgets when state changes; the orb is reused across
stages and driven by the same amplitude engine as the JSX.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFontDatabase, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from voice_agent.events import AgentEvent
from voice_agent.ui.native.bridge import EventBridge
from voice_agent.ui.native.orb import VoiceOrb
from voice_agent.ui.native.previews import build_preview
from voice_agent.ui.native.reducer import UIState, apply
from voice_agent.ui.native.tokens import FAMILY_MONO, FAMILY_SANS, FONTS_DIR, T

log = logging.getLogger(__name__)


def install_fonts() -> None:
    """Register bundled Comfortaa + JetBrainsMono so styleSheets can resolve them."""
    for fname in ("Comfortaa.ttf", "JetBrainsMono.ttf"):
        path = FONTS_DIR / fname
        if not path.exists():
            log.warning("font missing: %s", path)
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id < 0:
            log.warning("failed to register font: %s", path)
        else:
            families = QFontDatabase.applicationFontFamilies(font_id)
            log.info("loaded font %s → families=%s", fname, families)


# ── small helpers ─────────────────────────────────────────────────────────
def _label(text: str, *, family: str = FAMILY_SANS, size: int = T.size.base,
           weight: int = 500, color: str = T.color.ink,
           line_height: float | None = None) -> QLabel:
    lbl = QLabel(text)
    ss = (
        f"font-family:'{family}'; font-size:{size}px; "
        f"font-weight:{weight}; color:{color};"
    )
    if line_height is not None:
        ss += f" line-height:{line_height};"
    lbl.setStyleSheet(ss)
    lbl.setWordWrap(True)
    return lbl


def _mono_pill(text: str, *, bg: str, fg: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"font-family:'{FAMILY_MONO}'; font-size:11px; font-weight:600; "
        f"letter-spacing:1.4px; color:{fg}; background:{bg}; "
        f"padding:4px 10px; border-radius:{T.radius.pill}px;"
    )
    return lbl


# ── main window ───────────────────────────────────────────────────────────
class CompanionWindow(QMainWindow):
    STAGES = (
        "idle", "listening", "transcribing", "planning",
        "confirming", "acting", "done",
    )

    def __init__(self, bridge: EventBridge) -> None:
        super().__init__()
        self._bridge = bridge
        self._state = UIState()
        self.setWindowTitle("Cactus Voice Companion")
        self.resize(980, 760)

        self.setStyleSheet(
            f"QMainWindow {{ background: {T.color.bg}; }}"
        )
        root = QWidget()
        root.setStyleSheet(f"background: {T.color.bg};")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._build_header(outer)
        self._build_body(outer)
        self._build_footer(outer)

        # Shared pulsing dot for stage indicator + footer.
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(1400)
        self._pulse_timer.timeout.connect(self._pulse_tick)
        self._pulse_timer.start()
        self._pulse_on = True

        # Keyboard: hold Space = push-to-talk, Enter=confirm, Esc=cancel.
        self._space_down = False
        ret = QShortcut(QKeySequence(Qt.Key.Key_Return), self)
        ret.setAutoRepeat(False)
        ret.activated.connect(self._on_confirm)
        ent = QShortcut(QKeySequence(Qt.Key.Key_Enter), self)
        ent.setAutoRepeat(False)
        ent.activated.connect(self._on_confirm)
        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        esc.setAutoRepeat(False)
        esc.activated.connect(self._on_cancel)

        # Wire bridge signal → reducer → render. Qt auto-queues across threads.
        self._bridge.agent_event.connect(self._on_event)

        # Initial render.
        self._render(self._state)

    # ── layout helpers ────────────────────────────────────────────────────
    def _build_header(self, outer: QVBoxLayout) -> None:
        header = QFrame()
        header.setStyleSheet(
            "border-bottom: 1px solid rgba(45,53,59,0.12); background: transparent;"
        )
        col = QVBoxLayout(header)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        row_wrap = QFrame()
        row = QHBoxLayout(row_wrap)
        row.setContentsMargins(26, 20, 26, 12)
        row.setSpacing(12)

        logo = QLabel()
        logo.setFixedSize(28, 28)
        logo.setStyleSheet(
            f"background:{T.color.ink}; border-radius:8px;"
        )
        dot = QLabel(logo)
        dot.setFixedSize(10, 10)
        dot.move(9, 9)
        dot.setStyleSheet(f"background:{T.color.accent}; border-radius:5px;")
        row.addWidget(logo)

        brand = QVBoxLayout()
        brand.setSpacing(2)
        name = _label("Cactus", family=FAMILY_SANS, size=T.size.base, weight=600)
        sub = _label("🔒 on-device · Gemma 4", family=FAMILY_MONO,
                     size=T.size.xs, color=T.color.ink_mute)
        brand.addWidget(name)
        brand.addWidget(sub)
        row.addLayout(brand, 1)

        self._stage_pill = _mono_pill("STAGE", bg=T.color.live_soft, fg=T.color.ink)
        row.addWidget(self._stage_pill)

        col.addWidget(row_wrap)

        # Audio-level meter — thin horizontal bar, always visible so the user
        # can confirm the mic captures sound even when Whisper returns empty.
        meter_wrap = QFrame()
        meter_wrap.setFixedHeight(6)
        meter_wrap.setStyleSheet(
            "background: rgba(45,53,59,0.06); border: none;"
        )
        # The inner fill widget — width is set dynamically in _render.
        self._audio_meter = QFrame(meter_wrap)
        self._audio_meter.setStyleSheet(
            f"background:{T.color.accent}; border: none;"
        )
        self._audio_meter.setGeometry(0, 0, 0, 6)
        self._audio_meter_wrap = meter_wrap
        col.addWidget(meter_wrap)

        outer.addWidget(header)

    def _build_body(self, outer: QVBoxLayout) -> None:
        body = QFrame()
        body.setStyleSheet("background: transparent;")
        body.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding,
        )
        v = QVBoxLayout(body)
        v.setContentsMargins(28, 22, 28, 22)
        v.setSpacing(22)

        self._orb = VoiceOrb(QColor(T.color.ink), size=180, parent=body)

        # Stacked pages, one per stage.
        self._stack = QStackedWidget()
        self._pages: dict[str, QWidget] = {}
        for stage in self.STAGES:
            page = self._make_page(stage)
            self._pages[stage] = page
            self._stack.addWidget(page)
        v.addWidget(self._stack, 1)
        outer.addWidget(body, 1)

    def _build_footer(self, outer: QVBoxLayout) -> None:
        self._footer = QFrame()
        self._footer.setStyleSheet(
            f"background:{T.color.bg_elevated}; "
            f"border-top:1px solid rgba(45,53,59,0.12);"
        )
        row = QHBoxLayout(self._footer)
        row.setContentsMargins(30, 14, 30, 14)
        row.setSpacing(12)
        self._foot_dot = QLabel()
        self._foot_dot.setFixedSize(10, 10)
        self._foot_dot.setStyleSheet(
            f"background:{T.color.live}; border-radius:5px;"
        )
        row.addWidget(self._foot_dot)
        col = QVBoxLayout()
        col.setSpacing(2)
        self._foot_primary = _label("", size=T.size.base, weight=500)
        self._foot_secondary = _label(
            "", family=FAMILY_MONO, size=T.size.xs,
            color=T.color.ink_mute,
        )
        col.addWidget(self._foot_primary)
        col.addWidget(self._foot_secondary)
        row.addLayout(col, 1)
        outer.addWidget(self._footer)

    # ── stage pages ───────────────────────────────────────────────────────
    def _make_page(self, stage: str) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(18)

        if stage == "idle":
            self._build_idle(v)
        elif stage in ("listening", "transcribing", "planning"):
            self._build_transcript(v, stage)
        elif stage == "confirming":
            self._build_confirming(v)
        elif stage == "acting":
            self._build_acting(v)
        elif stage == "done":
            self._build_done(v)
        return page

    # Each builder stashes the widgets it needs to update on the window.
    def _build_idle(self, v: QVBoxLayout) -> None:
        v.addStretch(1)
        center = QVBoxLayout()
        center.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        center.setSpacing(22)
        orb_row = QHBoxLayout()
        orb_row.addStretch(1)
        orb_row.addWidget(self._orb)  # placed here by default
        orb_row.addStretch(1)
        center.addLayout(orb_row)

        self._idle_title = QLabel(
            f"Hold <i style='color:{T.color.accent}'>Space</i> to talk"
        )
        self._idle_title.setTextFormat(Qt.TextFormat.RichText)
        self._idle_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._idle_title.setStyleSheet(
            f"font-family:'{FAMILY_SANS}'; font-size:{T.size.xxl}px; "
            f"font-weight:600; color:{T.color.ink};"
        )
        self._idle_subtitle = _label(
            "Release Space to send the command. The mic ignores speech while "
            "Space is up.",
            size=T.size.md, color=T.color.ink_soft,
        )
        self._idle_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._idle_subtitle.setMaximumWidth(520)
        center.addWidget(self._idle_title)
        sub_wrap = QHBoxLayout()
        sub_wrap.addStretch(1)
        sub_wrap.addWidget(self._idle_subtitle)
        sub_wrap.addStretch(1)
        center.addLayout(sub_wrap)

        pill_row = QHBoxLayout()
        pill_row.addStretch(1)
        self._idle_pill = _mono_pill(
            "● PUSH-TO-TALK · MIC MUTED UNTIL SPACE IS HELD",
            bg=T.color.live_soft, fg=T.color.ink,
        )
        pill_row.addWidget(self._idle_pill)
        pill_row.addStretch(1)
        center.addLayout(pill_row)
        v.addLayout(center)
        v.addStretch(1)

    def _build_transcript(self, v: QVBoxLayout, stage: str) -> None:
        row = QHBoxLayout()
        row.setSpacing(22)
        # The orb is a single shared instance — we just hold a placeholder;
        # during _render we reparent the orb into the active page's slot.
        holder = QWidget()
        holder.setFixedSize(200, 200)
        self._transcript_orb_holder = getattr(
            self, "_transcript_orb_holder", None,
        ) or {}
        self._transcript_orb_holder[stage] = holder
        row.addWidget(holder)

        col = QVBoxLayout()
        col.setSpacing(12)
        self._transcript_label = getattr(self, "_transcript_label", None)
        if not hasattr(self, "_transcript_labels"):
            self._transcript_labels: dict[str, QLabel] = {}
        lbl_meta = _mono_pill(
            "LISTENING" if stage == "listening" else stage.upper(),
            bg="transparent", fg=T.color.ink_mute,
        )
        col.addWidget(lbl_meta, 0, Qt.AlignmentFlag.AlignLeft)
        txt = _label(
            "\u201c\u2026\u201d", size=T.size.xl,
            color=T.color.ink_mute if stage == "listening" else T.color.ink,
            weight=500,
        )
        self._transcript_labels[stage] = txt
        col.addWidget(txt, 1)
        row.addLayout(col, 1)
        v.addLayout(row)
        v.addStretch(1)

    def _build_confirming(self, v: QVBoxLayout) -> None:
        # Transcript chip up top.
        self._conf_transcript = QFrame()
        self._conf_transcript.setStyleSheet(
            f"background:{T.color.bg_elevated}; "
            f"border:1px solid rgba(45,53,59,0.12); "
            f"border-radius:{T.radius.md}px;"
        )
        cv = QVBoxLayout(self._conf_transcript)
        cv.setContentsMargins(16, 12, 16, 12)
        cv.setSpacing(4)
        cv.addWidget(_label(
            "YOU SAID", family=FAMILY_MONO, size=T.size.xs,
            color=T.color.ink_mute,
        ))
        self._conf_utterance = _label(
            "", size=T.size.base, weight=500,
        )
        cv.addWidget(self._conf_utterance)
        v.addWidget(self._conf_transcript)

        # Preview slot with accent glow border.
        self._conf_preview_wrap = QFrame()
        self._conf_preview_wrap.setStyleSheet(
            f"#previewWrap {{ border: 2px solid {T.color.accent}; "
            f"border-radius: {T.radius.lg}px; background: transparent; }}"
        )
        self._conf_preview_wrap.setObjectName("previewWrap")
        self._conf_preview_layout = QVBoxLayout(self._conf_preview_wrap)
        self._conf_preview_layout.setContentsMargins(6, 6, 6, 6)
        self._conf_preview_layout.setSpacing(0)
        self._conf_preview_current: QWidget | None = None
        v.addWidget(self._conf_preview_wrap, 1)

    def _build_acting(self, v: QVBoxLayout) -> None:
        head = QHBoxLayout()
        head.setSpacing(14)
        self._act_orb_holder = QWidget()
        self._act_orb_holder.setFixedSize(88, 88)
        head.addWidget(self._act_orb_holder)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        title_col.addWidget(_label(
            "WORKING ON", family=FAMILY_MONO, size=T.size.xs,
            color=T.color.ink_mute,
        ))
        self._act_utterance = _label("", size=T.size.md, weight=600)
        title_col.addWidget(self._act_utterance)
        head.addLayout(title_col, 1)
        v.addLayout(head)

        self._plan_container = QVBoxLayout()
        self._plan_container.setSpacing(10)
        v.addLayout(self._plan_container, 1)

    def _build_done(self, v: QVBoxLayout) -> None:
        hero = QFrame()
        hero.setStyleSheet(
            f"background:{T.color.live_soft}; "
            f"border:1px solid rgba(45,53,59,0.22); "
            f"border-radius:{T.radius.lg}px;"
        )
        row = QHBoxLayout(hero)
        row.setContentsMargins(20, 18, 20, 18)
        row.setSpacing(14)
        badge = QLabel("✓")
        badge.setFixedSize(36, 36)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background:{T.color.ink}; color:{T.color.bg}; "
            f"border-radius:18px; font-size:20px; font-weight:700;"
        )
        row.addWidget(badge)
        self._done_result = _label("", size=T.size.md, weight=600)
        row.addWidget(self._done_result, 1)
        v.addWidget(hero)

        self._done_preview_wrap = QFrame()
        self._done_preview_wrap.setStyleSheet(
            "border:1px solid rgba(45,53,59,0.12); "
            f"border-radius:{T.radius.lg}px; background: transparent;"
        )
        self._done_preview_layout = QVBoxLayout(self._done_preview_wrap)
        self._done_preview_layout.setContentsMargins(0, 0, 0, 0)
        self._done_preview_current: QWidget | None = None
        v.addWidget(self._done_preview_wrap, 1)

    # ── render ────────────────────────────────────────────────────────────
    def _on_event(self, event: AgentEvent) -> None:
        prev = self._state
        self._state = apply(prev, event)
        log.debug("ui state: %s → %s (event=%s)",
                  prev.stage, self._state.stage, event.type.value)
        self._render(self._state)

    def _render(self, s: UIState) -> None:
        # Audio level meter — width is fraction of the wrapper width.
        if hasattr(self, "_audio_meter") and self._audio_meter_wrap.width() > 0:
            # peak-biased so short loud spikes register instantly.
            level = max(s.mic_rms * 4.0, s.mic_peak)
            level = max(0.0, min(1.0, level))
            full = self._audio_meter_wrap.width()
            self._audio_meter.setGeometry(0, 0, int(full * level), 6)
            # Red-ish tint near clip, sage otherwise.
            color = T.color.danger if level > 0.9 else T.color.accent
            self._audio_meter.setStyleSheet(f"background:{color}; border: none;")

        # Stage indicator pill
        label_map = {
            "idle": (
                "PUSH-TO-TALK READY" if s.ready else "STARTING VOICE STACK",
                T.color.ink_mute,
            ),
            "listening": ("LISTENING", T.color.ink),
            "transcribing": ("UNDERSTANDING", T.color.ink),
            "planning": ("PLANNING", T.color.ink),
            "confirming": ("REVIEW BEFORE I DO THIS", T.color.accent),
            "acting": ("DOING IT", T.color.ink),
            "done": ("DONE", T.color.live),
        }
        label, color = label_map.get(s.stage, (s.stage.upper(), T.color.ink))
        self._stage_pill.setText(f"● {label}")
        self._stage_pill.setStyleSheet(
            f"font-family:'{FAMILY_MONO}'; font-size:11px; font-weight:600; "
            f"letter-spacing:1.4px; color:{color}; "
            f"background:{T.color.live_soft}; padding:4px 10px; border-radius:{T.radius.pill}px;"
        )

        # Swap the active page
        idx = self.STAGES.index(s.stage) if s.stage in self.STAGES else 0
        self._stack.setCurrentIndex(idx)

        if hasattr(self, "_idle_title"):
            if s.ready:
                self._idle_title.setText(
                    f"Hold <i style='color:{T.color.accent}'>Space</i> to talk"
                )
                self._idle_subtitle.setText(
                    "Release Space to send the command. The mic ignores speech while "
                    "Space is up."
                )
                self._idle_pill.setText(
                    "● PUSH-TO-TALK · MIC MUTED UNTIL SPACE IS HELD"
                )
            else:
                self._idle_title.setText("Starting Cactus…")
                self._idle_subtitle.setText(
                    "Loading models and tools. Space is disabled until this is ready."
                )
                self._idle_pill.setText("● STARTUP · DO NOT HOLD SPACE YET")

        # Orb: move to a stage-appropriate parent + set active/speaking
        active = s.stage != "idle"
        speaking = s.stage in ("listening",) or s.speaking
        self._orb.set_active(active=active or s.stage == "idle", speaking=speaking)
        if s.stage == "idle":
            self._orb.set_size(180)
        elif s.stage in ("listening",):
            self._orb.set_size(200)
        elif s.stage in ("transcribing", "planning"):
            self._orb.set_size(160)
        elif s.stage == "acting":
            self._orb.set_size(88)
        # idle and footer always-on glow: still show dot when inactive
        self._orb.set_active(active=s.stage != "idle", speaking=speaking)

        # Transcript label for listening/transcribing/planning
        if hasattr(self, "_transcript_labels"):
            for stage, lbl in self._transcript_labels.items():
                text = s.utterance or "\u2026"
                text = (
                    "\u201c\u2026\u201d"
                    if stage == "listening" and not s.utterance
                    else f"\u201c{text}\u201d"
                )
                lbl.setText(text)

        # Confirming stage widgets
        if s.stage == "confirming":
            self._conf_utterance.setText(f"\u201c{s.utterance}\u201d")
            self._set_preview(
                self._conf_preview_layout,
                self._conf_preview_wrap,
                "_conf_preview_current",
                s.preview,
            )

        # Acting stage widgets
        if s.stage == "acting":
            self._act_utterance.setText(f"\u201c{s.utterance}\u201d")
            self._rebuild_plan(s.plan)

        # Done stage
        if s.stage == "done":
            self._done_result.setText(s.result or "Done")
            self._set_preview(
                self._done_preview_layout,
                self._done_preview_wrap,
                "_done_preview_current",
                s.preview,
            )

        # Footer content
        self._set_footer(s.stage)

    def _set_preview(
        self,
        layout: QVBoxLayout,
        wrap: QFrame,
        attr: str,
        preview: dict | None,
    ) -> None:
        prev = getattr(self, attr)
        if prev is not None:
            layout.removeWidget(prev)
            prev.deleteLater()
            setattr(self, attr, None)
        if not preview:
            wrap.setVisible(False)
            return
        wrap.setVisible(True)
        widget = build_preview(preview)
        layout.addWidget(widget, 1)
        setattr(self, attr, widget)

    def _rebuild_plan(self, plan: list) -> None:
        # Clear the container.
        while self._plan_container.count():
            item = self._plan_container.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for i, step in enumerate(plan):
            row = QHBoxLayout()
            row.setSpacing(12)
            dot = QLabel("✓" if step.done else str(i + 1))
            dot.setFixedSize(22, 22)
            dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            active = not step.done and i == len(plan) - 1
            bg = T.color.live if step.done else (T.color.accent if active else T.color.bg)
            fg = "#fff" if (step.done or active) else T.color.ink_mute
            dot.setStyleSheet(
                f"background:{bg}; color:{fg}; border-radius:11px; "
                f"font-family:'{FAMILY_MONO}'; font-size:11px; font-weight:700;"
            )
            verb = _label(step.verb, size=T.size.sm, weight=600)
            target = _label(
                step.target, size=T.size.sm, color=T.color.ink_soft,
                family=(
                    FAMILY_MONO if any(c in step.target for c in ("/", "~", "."))
                    else FAMILY_SANS
                ),
            )
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(verb)
            col.addWidget(target)
            row.addWidget(dot)
            row.addLayout(col, 1)
            wrap = QWidget()
            wrap.setLayout(row)
            if active:
                wrap.setStyleSheet(
                    f"background:{T.color.accent_soft}; "
                    f"border-radius:{T.radius.md}px;"
                )
            self._plan_container.addWidget(wrap)
        self._plan_container.addStretch(1)

    def _set_footer(self, stage: str) -> None:
        if not self._state.ready and stage == "idle":
            primary = "Starting voice stack…"
            secondary = "Space is disabled until loading finishes"
            dot = T.color.ink_mute
            self._foot_primary.setText(primary)
            self._foot_primary.setTextFormat(Qt.TextFormat.RichText)
            self._foot_secondary.setText(secondary)
            self._foot_secondary.setTextFormat(Qt.TextFormat.RichText)
            self._foot_dot.setStyleSheet(
                f"background:{dot}; border-radius:5px;"
            )
            self._footer.setStyleSheet(
                f"background:{T.color.bg_elevated}; "
                f"border-top:1px solid rgba(45,53,59,0.12);"
            )
            return

        copy = {
            "idle": (
                "hold <b>Space</b> to talk",
                "release <b>Space</b> to send \u00b7 mic is muted otherwise",
                T.color.live,
            ),
            "listening": (
                "Listening while Space is held.",
                "release <b>Space</b> to send",
                T.color.accent,
            ),
            "transcribing": ("Understanding\u2026", "", T.color.ink),
            "planning": ("Planning\u2026", "", T.color.ink),
            "confirming": (
                "say <b>\u201cyes\u201d</b>, <b>\u201cdo it\u201d</b> or <b>\u201cgo ahead\u201d</b>",
                "or say <b>\u201cno\u201d</b> to cancel \u00b7 Esc / \u21b5 also work",
                T.color.accent,
            ),
            "acting": ("Working\u2026", "say <b>\u201cstop\u201d</b> to halt", T.color.accent),
            "done": (
                "Done \u2014 hold <b>Space</b> for another command.",
                "",
                T.color.live,
            ),
        }
        primary, secondary, dot = copy.get(stage, ("", "", T.color.ink))
        self._foot_primary.setText(primary)
        self._foot_primary.setTextFormat(Qt.TextFormat.RichText)
        self._foot_secondary.setText(secondary)
        self._foot_secondary.setTextFormat(Qt.TextFormat.RichText)
        self._foot_dot.setStyleSheet(
            f"background:{dot}; border-radius:5px;"
        )
        # Border accent on confirming for emphasis.
        if stage == "confirming":
            self._footer.setStyleSheet(
                f"background:{T.color.surface}; "
                f"border-top:2px solid {T.color.accent};"
            )
        else:
            self._footer.setStyleSheet(
                f"background:{T.color.bg_elevated}; "
                f"border-top:1px solid rgba(45,53,59,0.12);"
            )

    # ── input + pulse ─────────────────────────────────────────────────────
    def keyPressEvent(self, event: object) -> None:  # noqa: N802
        key = getattr(event, "key", lambda: None)()
        is_auto = getattr(event, "isAutoRepeat", lambda: False)()
        if key == Qt.Key.Key_Space and not is_auto:
            self._on_space_down()
            getattr(event, "accept", lambda: None)()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: object) -> None:  # noqa: N802
        key = getattr(event, "key", lambda: None)()
        is_auto = getattr(event, "isAutoRepeat", lambda: False)()
        if key == Qt.Key.Key_Space and not is_auto:
            self._on_space_up()
            getattr(event, "accept", lambda: None)()
            return
        super().keyReleaseEvent(event)

    def _on_space_down(self) -> None:
        stage = self._state.stage
        if self._space_down:
            return
        if not self._state.ready:
            log.info("keyboard: Space down ignored; voice stack not ready")
            return
        if stage in ("idle", "done", "confirming", "listening"):
            self._space_down = True
            log.info("keyboard: Space down → push-to-talk start")
            self._bridge.push_to_talk_start()
        else:
            log.debug("keyboard: Space down (stage=%s) — no-op", stage)

    def _on_space_up(self) -> None:
        if not self._space_down:
            return
        self._space_down = False
        log.info("keyboard: Space up → push-to-talk end")
        self._bridge.push_to_talk_end()

    def _on_confirm(self) -> None:
        if self._state.stage == "confirming":
            log.info("keyboard: Enter → confirm")
            self._bridge.confirm()

    def _on_cancel(self) -> None:
        if self._state.stage in ("confirming", "acting", "listening"):
            log.info("keyboard: Esc → cancel")
            self._bridge.cancel()

    def _pulse_tick(self) -> None:
        # Gentle opacity pulse on the footer dot when stage is active.
        self._pulse_on = not self._pulse_on
        # no-op visually beyond the CSS; the orb has its own animation.
