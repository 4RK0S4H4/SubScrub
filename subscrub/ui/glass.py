"""Backdrop and card surfaces in the Saha app style.

  * Light: a flat, neutral off-white window with solid white cards, a tinted hairline and a soft shadow.
  * Dark: glass. Near-black gradient with a faint neutral bloom (tokens bloom/bloom_a); cards are translucent white (.045) with a
    .10 white hairline. No specular lines or rims.
  * Optional Windows 11 Mica (opt-in in Settings) replaces the base fill; the blooms still go on top.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QFrame, QWidget

MICA, ACRYLIC, MICA_ALT = 2, 3, 4          # DWM_SYSTEMBACKDROP_TYPE


def system_backdrop_supported() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return sys.getwindowsversion().build >= 22621
    except Exception:
        return False


def enable_system_backdrop(widget, dark: bool, kind: int = MICA) -> bool:
    """Ask DWM to draw Mica behind the whole window. True only if Windows accepted it."""
    if not system_backdrop_supported():
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class MARGINS(ctypes.Structure):
            _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]

        hwnd = wintypes.HWND(int(widget.winId()))
        dwm = ctypes.windll.dwmapi
        v = ctypes.c_int(1 if dark else 0)
        dwm.DwmSetWindowAttribute(hwnd, ctypes.c_uint(20), ctypes.byref(v), ctypes.sizeof(v))
        m = MARGINS(-1, -1, -1, -1)
        if dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(m)) != 0:
            return False
        k = ctypes.c_int(kind)
        return dwm.DwmSetWindowAttribute(hwnd, ctypes.c_uint(38), ctypes.byref(k), ctypes.sizeof(k)) == 0
    except Exception:
        return False


def _c(r, g, b, a) -> QColor:
    return QColor(r, g, b, int(max(0, min(1, a)) * 255))


def _bloom(p: QPainter, rect: QRectF, cx: float, cy: float, rad: float, col: QColor, a: float):
    w, h = rect.width(), rect.height()
    R = rad * max(w, h)
    g = QRadialGradient(QPointF(rect.left() + cx * w, rect.top() + cy * h), R)
    c = QColor(col)
    c.setAlphaF(a)
    g.setColorAt(0, c)
    c2 = QColor(c)
    c2.setAlphaF(0)
    g.setColorAt(0.6, QColor(c.red(), c.green(), c.blue(), int(c.alpha() * 0.25)))
    g.setColorAt(1, c2)
    p.fillRect(rect, g)


def paint_backdrop(p: QPainter, rect: QRectF, t: dict, system: bool):
    """Dark: near-black gradient + accent blooms. Light: flat tinted off-white."""
    w, h = rect.width(), rect.height()
    if not t["is_dark"]:
        if not system:
            p.fillRect(rect, QColor(t["bg"]))
        return
    if not system:
        base = QLinearGradient(0, 0, w * 0.35, h)
        base.setColorAt(0, QColor("#161617"))
        base.setColorAt(0.55, QColor("#0b0b0c"))
        base.setColorAt(1, QColor("#0d0d0e"))
        p.fillRect(rect, base)
    _bloom(p, rect, 1.0, -0.10, 0.80, QColor(t.get("bloom", t["accent"])), t.get("bloom_a", 0.13))
    _bloom(p, rect, -0.10, 1.15, 0.75, QColor(t.get("bloom2", t["accent2"])), t.get("bloom2_a", 0.11))


class GlassRoot(QWidget):
    """The window's content area: paints the backdrop, children sit on it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.t: dict = {"is_dark": True}
        self.system = False                 # True when Windows is drawing Mica behind us
        self.setAttribute(Qt.WA_StyledBackground, False)

    def set_theme(self, t: dict, system: bool | None = None):
        self.t = t
        if system is not None:
            self.system = system
        self.update()

    def paintEvent(self, e):
        if "bg" not in self.t:
            return
        p = QPainter(self)
        paint_backdrop(p, QRectF(self.rect()), self.t, self.system)


class GlassPanel(QFrame):
    """A card. Put a layout on it; it only draws the surface."""

    def __init__(self, radius: float = 12, parent=None, strong: bool = False):
        super().__init__(parent)
        self.radius = min(radius, 16)
        self.strong = strong
        self.t: dict = {"is_dark": True}
        self.setAttribute(Qt.WA_StyledBackground, False)

    def set_theme(self, t: dict):
        self.t = t
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        dark = self.t.get("is_dark", True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = self.radius
        path = QPainterPath()
        path.addRoundedRect(r, rad, rad)
        if dark:
            p.fillPath(path, _c(255, 255, 255, 0.060 if self.strong else 0.045))
            p.setPen(QPen(_c(255, 255, 255, 0.10), 1))
        else:
            for i, a in ((2, 0.020), (1, 0.035)):          # soft shadow
                sp = QPainterPath()
                sp.addRoundedRect(r.adjusted(0, i, 0, i), rad, rad)
                p.fillPath(sp, _c(0, 0, 0, a))
            p.fillPath(path, QColor("#ffffff"))
            bc = QColor(self.t.get("border_s", self.t.get("border", "#e7e7e7")))
            p.setPen(QPen(bc, 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
