"""Voice orb — concentric pulsing rings rendered with QPainter.

Matches the `OrbRings` React component in voice_agent/ui/src/app.jsx:
- 4 rings at phases [0, 0.25, 0.5, 0.75]
- Each ring scales from 0.55 → 1.20 over the cycle, fading out near the end
- Center dot scales with the "amplitude" envelope
- When `active=False`, only the dot is drawn (no rings)
- When `speaking=True`, the amplitude envelope is high (0.55-1.0)
  otherwise it's low (0.15-0.33)
"""
from __future__ import annotations

import math
import random
import time

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QRadialGradient
from PySide6.QtWidgets import QWidget


class VoiceOrb(QWidget):
    def __init__(
        self,
        accent: QColor,
        size: int = 160,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._accent = accent
        self._size = size
        self._active = False
        self._speaking = False
        self._t0 = time.perf_counter()
        self._amp = 0.0
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # ~60 fps while active; we start the timer on first set_active=True.
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def sizeHint(self) -> QSize:
        return QSize(self._size, self._size)

    def set_accent(self, color: QColor) -> None:
        self._accent = color
        self.update()

    def set_size(self, size: int) -> None:
        self._size = size
        self.setFixedSize(size, size)
        self.update()

    def set_active(self, active: bool, speaking: bool = False) -> None:
        changed = active != self._active or speaking != self._speaking
        self._active = active
        self._speaking = speaking
        if active and not self._timer.isActive():
            self._timer.start()
        elif not active and self._timer.isActive():
            self._timer.stop()
            self._amp = 0.0
            self.update()
        elif changed:
            self.update()

    # ── animation ─────────────────────────────────────────────────────────
    def _tick(self) -> None:
        # Simulated amplitude envelope that matches the JSX `useSimulatedAmplitude`.
        t = time.perf_counter() - self._t0
        base = (math.sin(t * 1.7) + math.sin(t * 2.9) * 0.6 + math.sin(t * 4.3) * 0.3) / 2.0
        env = (0.55 + random.random() * 0.45) if self._speaking else (0.15 + random.random() * 0.18)
        self._amp = max(0.0, min(1.0, (base * 0.5 + 0.5) * env))
        self.update()

    # ── painting ──────────────────────────────────────────────────────────
    def paintEvent(self, _event: object) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2

        # Rings (only when active)
        if self._active:
            now = time.perf_counter()
            for i in range(4):
                phase = ((now / 1.6) + i * 0.25) % 1.0
                scale = 0.55 + phase * 0.65 + self._amp * 0.15
                opacity = (1 - phase) * (0.35 + self._amp * 0.5)
                ring_color = QColor(self._accent)
                ring_color.setAlphaF(max(0.0, min(1.0, opacity)))
                p.setPen(ring_color)
                p.setBrush(Qt.BrushStyle.NoBrush)
                r = (w * 0.5) * scale
                pen = p.pen()
                pen.setWidthF(1.5)
                pen.setColor(ring_color)
                p.setPen(pen)
                p.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))

        # Center dot + glow
        core_r = w * 0.21 * (1 + self._amp * 0.12)
        if self._active:
            glow_r = core_r * 2.5
            grad = QRadialGradient(cx, cy, glow_r)
            glow_color = QColor(self._accent)
            glow_color.setAlphaF(0.35)
            grad.setColorAt(0.0, glow_color)
            transparent = QColor(self._accent)
            transparent.setAlphaF(0.0)
            grad.setColorAt(1.0, transparent)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grad)
            p.drawEllipse(
                int(cx - glow_r), int(cy - glow_r),
                int(glow_r * 2), int(glow_r * 2),
            )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._accent)
        p.drawEllipse(
            int(cx - core_r), int(cy - core_r),
            int(core_r * 2), int(core_r * 2),
        )
        p.end()
