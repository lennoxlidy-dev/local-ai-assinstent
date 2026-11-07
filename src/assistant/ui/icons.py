"""Tray/app icons drawn at runtime (crisp at every DPI, follow the accent color, no asset files)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _gradient_pair(color: QColor) -> tuple[QColor, QColor]:
    """The accent and an analogous colour (hue -65°: violet -> cyan) for a lively two-tone orb."""
    h, sat, v = color.hsvHueF(), color.hsvSaturationF(), color.valueF()
    other = QColor.fromHsvF(((h if h >= 0 else 0.7) - 65 / 360) % 1.0, max(0.7, sat), min(1.0, v * 1.05 + 0.1))
    return color, other


def _orb(size: int, color: QColor, *, paused: bool, badge: QColor | None, ring: bool) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    m = size * 0.04
    r = QRectF(m, m, size - 2 * m, size - 2 * m)
    c, d = r.center(), r.width()

    # body: a two-tone gradient sphere (grey when paused)
    a, b = (QColor("#9AA0AA"), QColor("#5F6570")) if paused else _gradient_pair(color)
    body = QLinearGradient(r.topLeft(), r.bottomRight())
    body.setColorAt(0.0, b)
    body.setColorAt(0.55, a)
    body.setColorAt(1.0, a.darker(160))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(body)
    p.drawEllipse(r)
    # soft light from the top left + a thin rim, so it reads as a glowing orb on light and dark taskbars
    shine = QRadialGradient(QPointF(c.x() - d * 0.2, c.y() - d * 0.25), d * 0.55)
    shine.setColorAt(0.0, QColor(255, 255, 255, 85))
    shine.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setBrush(shine)
    p.drawEllipse(r)
    if size >= 24:
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 70), max(1.0, size / 48)))
        p.drawEllipse(r.adjusted(size / 96, size / 96, -size / 96, -size / 96))
        p.setPen(Qt.PenStyle.NoPen)

    if ring:  # actively listening: bright outline
        p.setPen(QPen(QColor(255, 255, 255, 230), max(1.0, size * 0.07)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(r.adjusted(size * 0.05, size * 0.05, -size * 0.05, -size * 0.05))
        p.setPen(Qt.PenStyle.NoPen)

    p.setBrush(QColor(255, 255, 255, 240))
    if paused:  # two bars
        bw, bh, gap = d * 0.13, d * 0.42, d * 0.09
        bars = [(c.x() - gap / 2 - bw, bh), (c.x() + gap / 2, bh)]
    else:       # a voice waveform: 3 bars when tiny, 5 from 24 px
        heights = (0.42, 0.78, 0.42) if size < 24 else (0.26, 0.5, 0.74, 0.5, 0.26)
        bw = d * (0.15 if size < 24 else 0.085)
        gap = bw * (0.7 if size < 24 else 0.8)
        total = len(heights) * bw + (len(heights) - 1) * gap
        bars = [(c.x() - total / 2 + i * (bw + gap), h * d * 0.62) for i, h in enumerate(heights)]
    for x, bh in bars:
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, c.y() - bh / 2, bw, bh), bw / 2, bw / 2)
        p.drawPath(path)

    if badge is not None:  # small status dot, bottom-right (e.g. game mode)
        dd = size * 0.38
        br = QRectF(size - dd - size * 0.02, size - dd - size * 0.02, dd, dd)
        p.setBrush(QColor(20, 20, 24))
        p.drawEllipse(br.adjusted(-size * 0.04, -size * 0.04, size * 0.04, size * 0.04))
        p.setBrush(badge)
        p.drawEllipse(br)
    p.end()
    return pm


def orb_icon(accent: str = "#7C5CFF", *, paused: bool = False, game_mode: bool = False,
             listening: bool = False) -> QIcon:
    color = QColor(accent)
    if not color.isValid():
        color = QColor("#7C5CFF")
    badge = QColor("#F5A524") if game_mode else None
    icon = QIcon()
    for s in SIZES:
        icon.addPixmap(_orb(s, color, paused=paused, badge=badge, ring=listening))
    return icon


def app_icon(accent: str = "#7C5CFF") -> QIcon:
    """Window/taskbar icon (title bars, taskbar buttons, dialogs) - the orb in its normal state."""
    return orb_icon(accent)
