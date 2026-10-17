"""Glass surfaces in the S4 Glass style.

  * GlassRoot: the window's backdrop. Light: a flat off-white with a faint accent tint. Dark: a near-black gradient
    with a faint bloom of the two accents (top right, bottom left). Rendered once per size and theme and cached,
    then blitted, so repaints are cheap. It also paints the soft shadows of the cards on it.
  * Panel: a card. Frosted white (62% light, 4.5% dark), a hairline border and a thin white highlight on top.
  * GlassDialog: dialogs open inside the window: a blurred, dimmed copy of the window behind a frosted card with
    16px corners, instead of a separate square window.
  * Optional Windows 11 Mica (opt-in in Settings) replaces the base fill; the blooms still go on top.
"""
from __future__ import annotations

import sys
import weakref

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .theme import qc
from .widgets import T, icon_button, paint_backlight, rounded, soft_glow

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


def paint_backdrop(p: QPainter, rect: QRectF, t: dict, system: bool):
    """The page: S4 Glass --page (flat in light; gradient plus two accent blooms in dark)."""
    w, h = rect.width(), rect.height()
    if not system:
        if len(t["page"]) > 2:
            # linear-gradient(165deg, ...): the angle runs top-left to bottom-right, a little steeper than vertical
            import math
            a = math.radians(165)
            dx, dy = math.sin(a), -math.cos(a)
            half = abs(w * dx) / 2 + abs(h * dy) / 2
            c = rect.center()
            g = QLinearGradient(c.x() - dx * half, c.y() - dy * half, c.x() + dx * half, c.y() + dy * half)
            for stop, col in t["page"]:
                g.setColorAt(stop, qc(col))
            p.fillRect(rect, g)
        else:
            p.fillRect(rect, qc(t["bg"]))
    for fx, fy, rx, ry, col in t.get("blooms", []):
        # radial-gradient(rx ry at fx fy, col, transparent 60%)
        cx, cy = rect.left() + fx * w, rect.top() + fy * h
        p.save()
        p.translate(cx, cy)
        p.scale(1.0, ry / rx)
        g = QRadialGradient(QPointF(0, 0), rx)
        c0 = qc(col)
        c1 = QColor(c0)
        c1.setAlpha(0)
        g.setColorAt(0, c0)
        g.setColorAt(0.6, c1)
        p.fillRect(QRectF(-cx, -cy * rx / ry, w, h * rx / ry), g)
        p.restore()


class GlassRoot(QWidget):
    """The window's content area: paints the backdrop (cached) and the cards' shadows; children sit on it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.t: dict = {}
        self.system = False                 # True when Windows is drawing Mica behind us
        self._cache: QPixmap | None = None
        self._key = None
        self._panels: list = []
        self.setAttribute(Qt.WA_StyledBackground, False)

    def set_theme(self, t: dict, system: bool | None = None):
        self.t = t
        if system is not None:
            self.system = system
        self._cache = None
        self.update()

    def register(self, panel: "Panel"):
        self._panels.append(weakref.ref(panel))
        panel.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.Move, QEvent.Resize, QEvent.Show, QEvent.Hide):
            self.update()
        return False

    def _backdrop(self) -> QPixmap:
        dpr = self.devicePixelRatioF()
        key = (self.size().width(), self.size().height(), dpr, self.t.get("is_dark"), self.t.get("bg"), self.system)
        if self._cache is None or self._key != key:
            pm = QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            paint_backdrop(p, QRectF(0, 0, self.width(), self.height()), self.t, self.system)
            p.end()
            self._cache, self._key = pm, key
        return self._cache

    def paintEvent(self, e):
        if "bg" not in self.t:
            return
        p = QPainter(self)
        p.drawPixmap(0, 0, self._backdrop())
        p.setRenderHint(QPainter.Antialiasing)
        alive = []
        for ref in self._panels:
            pnl = ref()
            if pnl is None:
                continue
            alive.append(ref)
            if pnl.shadow and pnl.isVisible():
                r = QRectF(QPointF(pnl.mapTo(self, QPoint(0, 0))), QSize(pnl.width(), pnl.height()).toSizeF())
                if r.adjusted(-48, -48, 48, 48).intersects(QRectF(e.rect())):
                    self._shadow(p, r, pnl.radius)
        self._panels = alive

    def _shadow(self, p: QPainter, r: QRectF, radius: float):
        t = self.t
        clip = QPainterPath()
        clip.addRect(r.adjusted(-60, -60, 60, 60))
        clip = clip.subtracted(rounded(r, radius))       # a box-shadow is never painted under the card itself
        p.save()
        p.setClipPath(clip, Qt.IntersectClip)
        if t["is_dark"]:
            soft_glow(p, r, radius, t["card_shadow"], 0, 26, dy=6, steps=10)
        else:
            soft_glow(p, r, radius, t["card_shadow"], 0, 26, dy=8, steps=10)
            soft_glow(p, r, radius, t["card_shadow"], 0, 2, dy=1, steps=2)
        p.restore()


class Panel(QFrame):
    """A card. Put a layout on it; it only draws the surface. Its shadow is painted by the GlassRoot under it."""

    def __init__(self, radius: float = 12, parent=None, strong: bool = False, shadow: bool = True):
        super().__init__(parent)
        self.radius = radius
        self.strong = strong
        self.shadow = shadow
        self.glow: list = []                    # primary buttons inside that get the S4 backlight
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, e):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = rounded(r, self.radius)
        p.fillPath(path, qc(t["glass2"] if self.strong else t["glass"]))
        p.save()
        p.setClipPath(path)
        p.setPen(QPen(qc(t["stroke"]), 1))
        p.drawLine(QPointF(r.left() + self.radius / 2, r.top() + 1.5), QPointF(r.right() - self.radius / 2, r.top() + 1.5))
        p.restore()
        p.setPen(QPen(qc(t["border"]), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        if self.glow:
            paint_backlight(p, self, self.glow)



# ============================================================================ dialogs inside the window

class DialogCard(QFrame):
    """The dialog's frosted card (16px corners, a hairline and the glass top highlight)."""

    def paintEvent(self, e):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = rounded(r, 16)
        p.fillPath(path, qc(t["modal_bg"]))
        p.save()
        p.setClipPath(path)
        p.setPen(QPen(qc(t["stroke"]), 1))
        p.drawLine(QPointF(r.left() + 8, r.top() + 1.5), QPointF(r.right() - 8, r.top() + 1.5))
        p.restore()
        p.setPen(QPen(qc(t["modal_line"]), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)


class GlassDialog(QDialog):
    """A dialog drawn inside the main window: the window behind it blurred and dimmed, the content on a frosted
    card. Subclasses fill `body_layout` and `footer_layout`; `exec()` works as usual (Esc cancels)."""

    def __init__(self, win, title: str = "", width: int = 560, closable: bool = True, footer: bool = True):
        super().__init__(win)
        self.win = win.window() if win is not None else None
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle(title)
        self._snap: QPixmap | None = None
        self._width = width
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.card = DialogCard()
        self.card.setFocusPolicy(Qt.ClickFocus)      # holds focus on open, so no control shows a focus ring
        self.card.setFixedWidth(width)
        self.focus_widget = None                     # a field that should take focus instead (e.g. a search box)
        row.addWidget(self.card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)
        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(24, 20, 16, 8)
        head.setSpacing(10)
        self.title = QLabel(title)
        self.title.setObjectName("H2")
        self.title.setWordWrap(True)
        head.addWidget(self.title, 1, Qt.AlignTop)
        self.close_btn = None
        if closable:
            self.close_btn = icon_button("close", "Close (Esc)", self.reject, "sm")
            head.addWidget(self.close_btn, 0, Qt.AlignTop)
        cl.addLayout(head)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(24, 6, 24, 20)
        self.body_layout.setSpacing(14)
        cl.addWidget(self.body, 1)
        self.footer = _DialogFooter()
        self.footer_layout = QHBoxLayout(self.footer)
        self.footer_layout.setContentsMargins(24, 14, 20, 14)
        self.footer_layout.setSpacing(10)
        cl.addWidget(self.footer)
        self.footer.setVisible(footer)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(140)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)

    def set_title(self, text: str):
        self.title.setText(text)
        self.setWindowTitle(text)

    def add_footer_buttons(self, *buttons, left=None):
        """Footer: an optional quiet action on the left, then the buttons on the right (primary last)."""
        if left is not None:
            self.footer_layout.addWidget(left)
        self.footer_layout.addStretch(1)
        for b in buttons:
            self.footer_layout.addWidget(b)
            if b.property("kind") == "primary":
                self.footer.glow.append(b)

    # ---- covering the window
    def _cover(self):
        w = self.win
        if w is None:
            scr = QGuiApplication.primaryScreen()
            g = scr.availableGeometry() if scr else QRect(0, 0, 1280, 800)
            self.setGeometry(g)
            return
        tl = w.mapToGlobal(QPoint(0, 0))
        self.setGeometry(QRect(tl, w.size()))
        self.card.setFixedWidth(min(self._width, max(320, w.width() - 48)))
        self.card.setMaximumHeight(max(240, w.height() - 48))

    def refresh_backdrop(self):
        """Grab the window again (after a theme change while the dialog is open)."""
        self._snap = None
        self.update()

    def _backdrop(self) -> QPixmap | None:
        if self._snap is not None or self.win is None:
            return self._snap
        t = T()
        shot = self.win.grab()
        dpr = shot.devicePixelRatio()
        base = QPixmap(shot.size())
        base.setDevicePixelRatio(dpr)
        base.fill(qc(t["bg"]))
        p = QPainter(base)
        p.drawPixmap(0, 0, shot)
        p.end()
        # a cheap blur: shrink and enlarge again (S4 Glass blurs the scrim by about 6px)
        small = base.scaled(max(1, base.width() // 5), max(1, base.height() // 5), Qt.IgnoreAspectRatio,
                            Qt.SmoothTransformation)
        big = small.scaled(base.width(), base.height(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        big.setDevicePixelRatio(dpr)
        self._snap = big
        return big

    def showEvent(self, e):
        self._cover()
        self._snap = None
        super().showEvent(e)
        QTimer.singleShot(0, lambda: (self.focus_widget or self.card).setFocus(Qt.OtherFocusReason))
        if QGuiApplication.platformName() not in ("offscreen", "minimal"):
            self.setWindowOpacity(0.0)
            self._fade.stop()
            self._fade.setStartValue(0.0)
            self._fade.setEndValue(1.0)
            self._fade.start()

    def paintEvent(self, e):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        snap = self._backdrop()
        if snap is not None:
            p.drawPixmap(0, 0, snap)
        else:
            p.fillRect(self.rect(), qc(t["bg"]))
        p.fillRect(self.rect(), qc(t["scrim"]))
        r = QRectF(self.card.geometry())
        clip = QPainterPath()
        clip.addRect(QRectF(self.rect()))
        p.setClipPath(clip.subtracted(rounded(r, 16)))
        soft_glow(p, r, 16, t["shadow_deep"], 0, 46, dy=18, steps=12)


class _DialogFooter(QWidget):
    """The dialog's footer: a hairline on top and the primary button's backlight."""

    def __init__(self):
        super().__init__()
        self.glow: list = []

    def paintEvent(self, _):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(qc(t["border"]), 1))
        p.drawLine(QPointF(0, 0.5), QPointF(self.width(), 0.5))
        paint_backlight(p, self, self.glow)
