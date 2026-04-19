"""Design tokens ported from voice_agent/ui/src/app.jsx (`T` object).

Kept as a module of constants so the rest of the UI code reads exactly like
the React source did — `T.color.ink`, `T.size.base`, etc.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

FONTS_DIR = Path(__file__).resolve().parent / "fonts"

# Font family names that Qt will use AFTER we load the TTFs via QFontDatabase.
# These strings match the variable-font family field, not the filename.
FAMILY_SANS = "Comfortaa"
FAMILY_MONO = "JetBrains Mono"


@dataclass(frozen=True)
class _Size:
    xs: int = 13
    sm: int = 15
    base: int = 17
    md: int = 20
    lg: int = 24
    xl: int = 32
    xxl: int = 44
    display: int = 64


@dataclass(frozen=True)
class _Weight:
    regular: int = 500
    medium: int = 500
    semibold: int = 600
    bold: int = 700


@dataclass(frozen=True)
class _Color:
    bg: str = "#D3C6AA"
    bg_elevated: str = "#DFD4BC"
    surface: str = "#EBE2CD"
    ink: str = "#2D353B"
    ink_soft: str = "rgba(45,53,59,0.72)"
    ink_mute: str = "rgba(45,53,59,0.48)"
    line: str = "rgba(45,53,59,0.12)"
    line_strong: str = "rgba(45,53,59,0.22)"
    accent: str = "#A7C080"
    accent_soft: str = "rgba(167,192,128,0.18)"
    accent_glow: str = "rgba(167,192,128,0.35)"
    live: str = "#2D353B"
    live_soft: str = "rgba(45,53,59,0.10)"
    cloud: str = "#7FBBB3"
    cloud_soft: str = "rgba(127,187,179,0.18)"
    warn: str = "#DBBC7F"
    danger: str = "#E67E80"


@dataclass(frozen=True)
class _Radius:
    sm: int = 8
    md: int = 14
    lg: int = 20
    xl: int = 28
    pill: int = 999


@dataclass(frozen=True)
class _Tokens:
    size: _Size = _Size()
    weight: _Weight = _Weight()
    color: _Color = _Color()
    radius: _Radius = _Radius()
    hit: int = 44


T = _Tokens()
