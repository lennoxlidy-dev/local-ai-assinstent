"""Tray/app icons drawn at runtime (crisp at every DPI, follow the accent color, no asset files)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _orb(size: int, color: QColor, *, paused: bool, badge: QColor | None, ring: bool) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    m = size * 0.06
    r = QRectF(m, m, size - 2 * m, size - 2 * m)
    c = r.center()

    base = QColor("#8A8F98") if paused else color
    grad = QRadialGradient(QPointF(c.x() - r.width() * 0.18, c.y() - r.height() * 0.22), r.width() * 0.75)
    grad.setColorAt(0.0, base.lighter(165))
    grad.setColorAt(0.55, base)
    grad.setColorAt(1.0, base.darker(170))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(grad)
    p.drawEllipse(r)

    if ring:  # actively listening: bright outline
        pen = QPen(QColor(255, 255, 255, 220), max(1.0, size * 0.07))
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(r.adjusted(size * 0.05, size * 0.05, -size * 0.05, -size * 0.05))
        p.setPen(Qt.PenStyle.NoPen)

    if paused:  # two white bars
        bw, bh = size * 0.12, size * 0.40
        gap = size * 0.08
        p.setBrush(QColor(255, 255, 255, 235))
        for x in (c.x() - gap / 2 - bw, c.x() + gap / 2):
            path = QPainterPath()
            path.addRoundedRect(QRectF(x, c.y() - bh / 2, bw, bh), bw * 0.3, bw * 0.3)
            p.drawPath(path)
    else:  # subtle highlight
        p.setBrush(QColor(255, 255, 255, 70))
        p.drawEllipse(QRectF(c.x() - r.width() * 0.28, c.y() - r.height() * 0.34, r.width() * 0.3, r.height() * 0.2))

    if badge is not None:  # small status dot, bottom-right (e.g. game mode)
        d = size * 0.38
        br = QRectF(size - d - size * 0.02, size - d - size * 0.02, d, d)
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
