"""Typed preview cards — direct port of the five variants in the React
`PreviewCard` component (email / file / summary / windows / describe).

Each factory returns a QWidget ready to drop into a layout. Styling uses
inline stylesheets so the visual parity with the React version stays tight.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from voice_agent.ui.native.tokens import FAMILY_MONO, FAMILY_SANS, T


def _box() -> QFrame:
    f = QFrame()
    f.setObjectName("previewBox")
    f.setStyleSheet(
        f"#previewBox {{ background: {T.color.surface}; "
        f"border-radius: {T.radius.lg}px; "
        f"border: 1px solid rgba(45,53,59,0.12); }}"
    )
    f.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    return f


def _label_mini(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setStyleSheet(
        f"font-family: '{FAMILY_MONO}'; font-size: 11px; "
        f"font-weight: 600; color: {T.color.ink_mute}; letter-spacing: 1.4px;"
    )
    return lbl


def build_preview(preview: dict[str, Any] | None) -> QWidget:
    """Dispatch on preview['kind'] to one of the five typed builders."""
    if not preview:
        return QWidget()
    kind = preview.get("kind")
    if kind == "email":
        return _email(preview)
    if kind == "file":
        return _file(preview)
    if kind == "summary":
        return _summary(preview)
    if kind == "windows":
        return _windows(preview)
    if kind == "describe":
        return _describe(preview)
    # Unknown kind → plain summary card.
    return _summary({"title": kind or "Action", "bullets": [str(preview)]})


# ── email ──────────────────────────────────────────────────────────────────
def _email(p: dict[str, Any]) -> QWidget:
    box = _box()
    root = QVBoxLayout(box)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)

    header = QFrame()
    header.setStyleSheet(
        "border-bottom: 1px solid rgba(45,53,59,0.12); background: transparent;"
    )
    h = QHBoxLayout(header)
    h.setContentsMargins(18, 14, 18, 14)
    h.setSpacing(12)

    avatar = QLabel((p.get("to") or "?")[:1].upper() or "M")
    avatar.setFixedSize(36, 36)
    avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
    avatar.setStyleSheet(
        f"background:#EADDCF; border-radius:18px; "
        f"font-family:'{FAMILY_SANS}'; font-size:18px; color:{T.color.ink};"
    )
    h.addWidget(avatar)

    meta = QVBoxLayout()
    meta.setContentsMargins(0, 0, 0, 0)
    meta.setSpacing(2)
    to_lbl = QLabel(f"To: {p.get('to') or '—'}")
    to_lbl.setStyleSheet(
        f"font-family:'{FAMILY_SANS}'; font-size:{T.size.sm}px; "
        f"font-weight:600; color:{T.color.ink};"
    )
    subj = QLabel(p.get("subject") or "")
    subj.setStyleSheet(
        f"font-family:'{FAMILY_SANS}'; font-size:{T.size.xs}px; color:{T.color.ink_mute};"
    )
    meta.addWidget(to_lbl)
    meta.addWidget(subj)
    h.addLayout(meta, 1)

    root.addWidget(header)

    body = QLabel(p.get("body") or "")
    body.setWordWrap(True)
    body.setContentsMargins(18, 16, 18, 18)
    body.setStyleSheet(
        f"font-family:'{FAMILY_SANS}'; font-size:{T.size.base}px; "
        f"line-height:1.55; color:{T.color.ink};"
    )
    body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    root.addWidget(body, 1)
    return box


# ── file ───────────────────────────────────────────────────────────────────
def _file(p: dict[str, Any]) -> QWidget:
    box = _box()
    h = QHBoxLayout(box)
    h.setContentsMargins(18, 18, 18, 18)
    h.setSpacing(14)

    thumb = QLabel("deck")
    thumb.setFixedSize(72, 96)
    thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
    thumb.setStyleSheet(
        f"background:{T.color.bg}; border:1px dashed rgba(45,53,59,0.22); "
        f"border-radius:{T.radius.md}px; "
        f"font-family:'{FAMILY_MONO}'; font-size:{T.size.xs}px; color:{T.color.ink_mute};"
    )
    h.addWidget(thumb)

    col = QVBoxLayout()
    col.setSpacing(4)
    name = QLabel(p.get("name") or "Untitled")
    name.setStyleSheet(
        f"font-family:'{FAMILY_SANS}'; font-size:{T.size.md}px; "
        f"font-weight:600; color:{T.color.ink};"
    )
    path = QLabel(p.get("path") or "")
    path.setStyleSheet(
        f"font-family:'{FAMILY_MONO}'; font-size:{T.size.sm}px; color:{T.color.ink_soft};"
    )
    meta_row = QHBoxLayout()
    meta_row.setSpacing(14)
    for piece in filter(None, [p.get("size"), p.get("modified")]):
        m = QLabel(str(piece))
        m.setStyleSheet(
            f"font-family:'{FAMILY_MONO}'; font-size:{T.size.xs}px; color:{T.color.ink_mute};"
        )
        meta_row.addWidget(m)
    meta_row.addStretch(1)

    col.addWidget(name)
    col.addWidget(path)
    col.addLayout(meta_row)
    col.addStretch(1)
    h.addLayout(col, 1)
    return box


# ── summary ────────────────────────────────────────────────────────────────
def _summary(p: dict[str, Any]) -> QWidget:
    box = _box()
    v = QVBoxLayout(box)
    v.setContentsMargins(18, 18, 18, 18)
    v.setSpacing(10)

    v.addWidget(_label_mini(f"Summary · {p.get('title') or ''}"))

    for i, bullet in enumerate(p.get("bullets") or []):
        row = QHBoxLayout()
        row.setSpacing(12)
        num = QLabel(f"{i + 1:02d}")
        num.setStyleSheet(
            f"font-family:'{FAMILY_MONO}'; font-size:{T.size.sm}px; "
            f"color:{T.color.accent}; font-weight:700;"
        )
        num.setFixedWidth(28)
        txt = QLabel(str(bullet))
        txt.setWordWrap(True)
        txt.setStyleSheet(
            f"font-family:'{FAMILY_SANS}'; font-size:{T.size.base}px; "
            f"color:{T.color.ink}; line-height:1.5;"
        )
        row.addWidget(num)
        row.addWidget(txt, 1)
        v.addLayout(row)

    v.addStretch(1)
    return box


# ── windows ────────────────────────────────────────────────────────────────
def _windows(p: dict[str, Any]) -> QWidget:
    box = _box()
    v = QVBoxLayout(box)
    v.setContentsMargins(18, 18, 18, 18)
    v.setSpacing(12)

    v.addWidget(_label_mini("Window arrangement"))

    stage = QFrame()
    stage.setStyleSheet(
        f"background:{T.color.bg}; border-radius:{T.radius.md}px;"
    )
    stage.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    grid = QHBoxLayout(stage)
    grid.setContentsMargins(6, 6, 6, 6)
    grid.setSpacing(6)

    for side_name in (p.get("left") or "—", p.get("right") or "—"):
        pane = QFrame()
        pane.setStyleSheet(
            f"background:{T.color.bg_elevated}; "
            f"border:1px solid rgba(45,53,59,0.12); "
            f"border-radius:{T.radius.sm}px;"
        )
        col = QVBoxLayout(pane)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        bar = QFrame()
        bar.setFixedHeight(18)
        bar.setStyleSheet(
            "border-bottom:1px solid rgba(45,53,59,0.12); background:transparent;"
        )
        dots = QHBoxLayout(bar)
        dots.setContentsMargins(6, 4, 6, 4)
        dots.setSpacing(3)
        for c in ("#ff736a", "#febc2e", "#19c332"):
            d = QLabel()
            d.setFixedSize(6, 6)
            d.setStyleSheet(f"background:{c}; border-radius:3px;")
            dots.addWidget(d)
        dots.addStretch(1)
        col.addWidget(bar)

        label = QLabel(str(side_name))
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(
            f"font-family:'{FAMILY_SANS}'; font-size:{T.size.sm}px; "
            f"font-weight:600; color:{T.color.ink_soft};"
        )
        col.addWidget(label, 1)
        grid.addWidget(pane, 1)

    v.addWidget(stage, 1)
    return box


# ── describe ───────────────────────────────────────────────────────────────
def _describe(p: dict[str, Any]) -> QWidget:
    box = _box()
    v = QVBoxLayout(box)
    v.setContentsMargins(18, 18, 18, 18)
    v.setSpacing(10)

    v.addWidget(_label_mini("Reading your screen"))

    text = QLabel(f"\u201c{p.get('text') or ''}\u201d")
    text.setWordWrap(True)
    text.setStyleSheet(
        f"font-family:'{FAMILY_SANS}'; font-size:{T.size.md}px; "
        f"line-height:1.45; color:{T.color.ink};"
    )
    v.addWidget(text, 1)
    return box
