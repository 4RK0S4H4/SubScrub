"""SubScrub's controls in the S4 Glass look.

Anything with a shape of its own (tabs and segmented filters, switches, checkboxes, badges, file rows, track tiles,
choice tiles, the settings section list, banners, tooltips) is painted here from the current theme tokens
(`theme.CURRENT`), so it is the same in light and dark and can't be squared off by a style sheet. Buttons, fields
and menus are plain Qt widgets styled by theme.stylesheet().
"""
from __future__ import annotations

import os
import weakref
from collections import OrderedDict

from PySide6.QtCore import (Property, QByteArray, QEasingCurve, QEvent, QObject, QPoint, QPointF,
                            QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication, QIcon, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap, QRadialGradient)
from PySide6.QtWidgets import (QAbstractButton, QApplication, QGridLayout, QHBoxLayout, QLabel, QMenu,
                               QProgressBar, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from . import theme
from .theme import num_font, qc


def T() -> dict:
    """The tokens of the theme that is showing."""
    return theme.CURRENT


def repolish(w: QWidget):
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def set_prop(w: QWidget, name: str, value):
    if w.property(name) != value:
        w.setProperty(name, value)
        repolish(w)


def rounded(r: QRectF, radius: float) -> QPainterPath:
    p = QPainterPath()
    p.addRoundedRect(r, radius, radius)
    return p


def soft_glow(p: QPainter, r: QRectF, radius: float, color, spread: float, blur: float, dy: float = 0.0,
              steps: int = 10):
    """A diffuse light or shadow around a rounded rect (CSS box-shadow, approximately): layered, fading copies."""
    c = qc(color)
    if c.alpha() == 0:
        return
    p.save()
    p.setPen(Qt.NoPen)
    base = c.alphaF()
    for i in range(steps):
        k = (i + 1) / steps                          # outer → inner
        grow = spread + blur * (1 - k)
        cc = QColor(c)
        cc.setAlphaF(base * 1.6 / steps * k)
        p.setBrush(cc)
        rr = r.adjusted(-grow, -grow + dy, grow, grow + dy)
        rad = max(0.0, radius + grow)
        p.drawRoundedRect(rr, rad, rad)
    p.restore()


def paint_backlight(p: QPainter, host: QWidget, buttons):
    """S4 Glass primary buttons: a soft accent shadow underneath in light mode, a faint diffuse backlight in dark."""
    t = T()
    for b in buttons:
        if b is None or not b.isVisible() or not b.isEnabled():
            continue
        r = QRectF(b.geometry()) if b.parentWidget() is host else QRectF(b.rect()).translated(
            QPointF(b.mapTo(host, QPoint(0, 0))))
        if t["is_dark"]:
            soft_glow(p, r, 10, t["backlight"], 4, 30, steps=12)
        else:
            soft_glow(p, r.adjusted(10, 6, -10, 0), 10, t["pri_shadow"], 0, 12, dy=4, steps=8)


# ============================================================================ icons

# our names -> (Solar name, style). Linear for small controls; bold duotone where the icon carries the screen.
_ICON_MAP = {
    "film": ("clapperboard-play", "lin"), "sub": ("subtitles", "lin"), "film-duo": ("clapperboard-play", "duo"),
    "sub-duo": ("subtitles", "duo"), "upload": ("upload-minimalistic", "duo"), "check": ("check-read", "lin"),
    "check-circle": ("check-circle", "lin"), "alert": ("danger-triangle", "lin"), "alert-circle": ("danger-circle", "lin"),
    "folder": ("folder", "lin"), "folder-open": ("folder-open", "lin"), "more": ("menu-dots", "lin"),
    "chev-up": ("alt-arrow-up", "lin"), "chev-down": ("alt-arrow-down", "lin"), "chev-right": ("alt-arrow-right", "lin"),
    "chev-left": ("alt-arrow-left", "lin"), "close": ("close", "lin"), "back": ("arrow-left", "lin"),
    "arrow-right": ("arrow-right", "lin"), "gear": ("settings", "lin"), "flag": ("flag", "lin"),
    "undo": ("undo-left", "lin"), "sun": ("sun", "lin"), "moon": ("moon", "lin"), "keyboard": ("keyboard", "lin"),
    "magic": ("magic-stick-3", "duo"), "add-file": ("document-add", "lin"), "add-folder": ("add-folder", "lin"),
    "info": ("info-circle", "lin"), "history": ("history", "lin"), "log": ("document-text", "lin"),
    "search": ("magnifier", "lin"), "pen": ("pen", "lin"), "restart": ("restart", "lin"), "stop": ("stop-circle", "lin"),
    "copy": ("copy", "lin"), "video-duo": ("videocamera-record", "duo"), "tag-duo": ("tag-horizontal", "duo"),
    "palette-duo": ("pallete-2", "duo"), "stars": ("stars-minimalistic", "duo"), "info-duo": ("info-circle", "duo"),
}
_PIX_CACHE: OrderedDict = OrderedDict()


def _dpr() -> float:
    s = QGuiApplication.primaryScreen()
    return max(2.0, s.devicePixelRatio() if s else 1.0)


def draw_icon(kind: str, color, size: int = 20, dpr: float | None = None, strong_duo: bool = False) -> QPixmap:
    """A Solar icon (CC BY 4.0, 480 Design) in the given colour ('#rrggbb' or 'rgba(...)'). Cached."""
    dpr = dpr or _dpr()
    key = (kind, str(color), size, dpr, strong_duo)
    pm = _PIX_CACHE.get(key)
    if pm is not None:
        _PIX_CACHE.move_to_end(key)
        return pm
    from PySide6.QtSvg import QSvgRenderer
    from . import solar
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    name, style = _ICON_MAP.get(kind, ("close", "lin"))
    body = (solar.DUOTONE if style == "duo" else solar.LINEAR).get(name) or solar.LINEAR["close"]
    c = qc(color)
    body = body.replace("currentColor", c.name())
    if strong_duo:
        body = body.replace('opacity=".5"', 'opacity=".62"')
    if c.alpha() < 255:
        body = f'<g opacity="{c.alphaF():.3f}">{body}</g>'
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">{body}</svg>'
    r = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, size, size))
    p.end()
    _PIX_CACHE[key] = pm
    if len(_PIX_CACHE) > 400:
        _PIX_CACHE.popitem(last=False)
    return pm


def icon(kind: str, color, size: int = 20) -> QIcon:
    ic = QIcon(draw_icon(kind, color, size))
    t = T()
    if t:
        ic.addPixmap(draw_icon(kind, t["text3"], size), QIcon.Disabled)
    return ic


_THEMED: list = []           # (weakref to widget, kind, colour role, size)


def themed_icon(w, kind: str, role: str = "text2", size: int = 18):
    """Give a button or label an icon that follows the theme (re-coloured by refresh_icons)."""
    for i, (ref, *_rest) in enumerate(_THEMED):
        if ref() is w:
            _THEMED[i] = (ref, kind, role, size)
            break
    else:
        _THEMED.append((weakref.ref(w), kind, role, size))
    _apply_icon(w, kind, role, size)


def _apply_icon(w, kind, role, size):
    t = T()
    if not t:
        return
    color = t.get(role, role)
    if isinstance(w, QLabel):
        w.setPixmap(draw_icon(kind, color, size))
    else:
        w.setIcon(icon(kind, color, size))
        w.setIconSize(QSize(size, size))


def refresh_icons():
    alive = []
    for ref, kind, role, size in _THEMED:
        w = ref()
        if w is None:
            continue
        try:
            _apply_icon(w, kind, role, size)
            alive.append((ref, kind, role, size))
        except RuntimeError:                 # the C++ side is gone
            pass
    _THEMED[:] = alive


def _white(a: float) -> QColor:
    c = QColor(255, 255, 255)
    c.setAlphaF(a)
    return c


def _brackets(p: QPainter, u: float, xs: tuple, y0: float, y1: float, alpha: float, width: float):
    """The faded [ ] sound tag: xs = (outer left, inner left, inner right, outer right) on the 256 grid."""
    p.setPen(QPen(_white(alpha), width * u, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    ol, il, ir, orr = xs
    p.drawPolyline([QPointF(il * u, y0 * u), QPointF(ol * u, y0 * u), QPointF(ol * u, y1 * u), QPointF(il * u, y1 * u)])
    p.drawPolyline([QPointF(ir * u, y0 * u), QPointF(orr * u, y0 * u), QPointF(orr * u, y1 * u), QPointF(ir * u, y1 * u)])


def _bar(p: QPainter, u: float, x: float, y: float, w: float, h: float, color: QColor):
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(QRectF(x * u, y * u, w * u, h * u), h / 2 * u, h / 2 * u)


def logo_pixmap(size: int = 64, dpr: float = 2.0) -> QPixmap:
    """App mark (3.0): a near-black rounded square, a faded [ ] sound tag struck through in crimson, and two clean
    caption lines on a glass plate. Drawn on a 256 grid; 24 px and below use a bolder version without the plate."""
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    u = size / 256
    crimson = QColor("#E5304F")
    small = size <= 24
    inset, radius = (4, 52) if small else (8, 50)
    tile = QRectF(inset * u, inset * u, (256 - 2 * inset) * u, (256 - 2 * inset) * u)
    g = QLinearGradient(0, tile.top(), 0, tile.bottom())
    g.setColorAt(0, QColor("#2E2E33"))
    g.setColorAt(1, QColor("#0F0F11"))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(tile, radius * u, radius * u)
    if small:
        _brackets(p, u, (72, 96, 160, 184), 38, 112, 0.6, 22)
        p.setPen(QPen(crimson, 22 * u, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(44 * u, 75 * u), QPointF(212 * u, 75 * u))
        _bar(p, u, 50, 146, 156, 30, _white(1))
        _bar(p, u, 80, 192, 96, 30, _white(1))
        p.end()
        return pm
    hi = QRadialGradient(QPointF(75 * u, 22 * u), 192 * u)
    hi.setColorAt(0, _white(0.1))
    hi.setColorAt(1, _white(0))
    p.setBrush(hi)
    p.drawRoundedRect(tile, radius * u, radius * u)
    edge = QLinearGradient(0, tile.top(), 0, tile.bottom())
    edge.setColorAt(0, _white(0.5))
    edge.setColorAt(0.3, _white(0.08))
    edge.setColorAt(1, QColor(0, 0, 0, 56))
    p.setPen(QPen(edge, 3 * u))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(tile.adjusted(1.5 * u, 1.5 * u, -1.5 * u, -1.5 * u), 48.5 * u, 48.5 * u)
    _brackets(p, u, (83, 100, 156, 173), 48, 102, 0.5, 13)
    _bar(p, u, 110, 69, 36, 12, _white(0.3))
    p.setPen(QPen(QColor(0, 0, 0, 41), 12 * u, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(62 * u, 78 * u), QPointF(194 * u, 78 * u))
    p.setPen(QPen(crimson, 12 * u, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(62 * u, 75 * u), QPointF(194 * u, 75 * u))
    p.setPen(QPen(_white(0.16), 2 * u))
    p.setBrush(_white(0.07))
    p.drawRoundedRect(QRectF(44 * u, 130 * u, 168 * u, 80 * u), 22 * u, 22 * u)
    _bar(p, u, 66, 151, 124, 15, _white(1))
    _bar(p, u, 88, 175, 80, 15, _white(0.9))
    p.end()
    return pm


class KeyboardFocus:
    """Draw a focus ring only when focus came from the keyboard (CSS :focus-visible), never after a click or when
    a window opens."""
    _kbd = False

    def focusInEvent(self, e):
        self._kbd = e.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e):
        self._kbd = False
        super().focusOutEvent(e)
        self.update()

    def kbd_focus(self) -> bool:
        return self._kbd and self.hasFocus()


# ============================================================================ buttons

def button(text: str = "", kind: str = "", size: str = "", slot=None, tip: str = "", icon_kind: str = "",
           icon_role: str = "", icon_size: int = 0) -> QPushButton:
    """The one button rule with its variants: kind '' | primary | subtle | danger | attn, size '' | sm."""
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if size:
        b.setProperty("btnsize", size)            # not "size": QWidget already has a property by that name
    b.setCursor(Qt.PointingHandCursor)
    b.setFocusPolicy(Qt.TabFocus)                       # a focus ring for the keyboard only, not after a click
    if tip:
        set_tip(b, tip)
    if slot is not None:
        b.clicked.connect(slot)
    if icon_kind:
        role = icon_role or ("text" if kind not in ("primary", "attn") else "btn_fg")
        themed_icon(b, icon_kind, role, icon_size or (15 if size == "sm" else 17))
        if text:
            b.setText(" " + text)
    return b


def icon_button(kind_icon: str, tip: str, slot=None, size: str = "", role: str = "text2") -> QPushButton:
    """A square subtle button with just an icon (34px, or 28px for size 'sm')."""
    b = button("", "subtle", size, slot, tip)
    b.setProperty("iconOnly", True)
    side = 28 if size == "sm" else 34
    b.setFixedSize(side, side)
    b.setAccessibleName(tip)
    themed_icon(b, kind_icon, role, 15 if size == "sm" else 18)
    return b


class LinkButton(KeyboardFocus, QAbstractButton):
    """Text that acts: dotted underline, the accent colour on hover (normal text colour in dark mode)."""

    def __init__(self, text: str, parent=None, size: float = 13):
        super().__init__(parent)
        self.setText(text)
        self._px = size
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setAttribute(Qt.WA_Hover)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def _font(self):
        return theme.font(self._px, 500)

    def sizeHint(self):
        fm = QFontMetrics(self._font())
        return QSize(fm.horizontalAdvance(self.text()) + 2, fm.height() + 6)

    def paintEvent(self, _):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = self._font()
        p.setFont(f)
        hover = self.underMouse() and self.isEnabled()
        col = qc(t["accent_text"] if hover else (t["text2"] if self.isEnabled() else t["text3"]))
        fm = QFontMetrics(f)
        y = (self.height() - fm.height()) / 2
        p.setPen(col)
        p.drawText(QRectF(1, y, self.width() - 1, fm.height()), Qt.AlignLeft | Qt.AlignVCenter, self.text())
        pen = QPen(col, 1, Qt.DotLine)
        p.setPen(pen)
        uy = y + fm.ascent() + 3
        p.drawLine(QPointF(1, uy), QPointF(1 + fm.horizontalAdvance(self.text()), uy))
        if self.kbd_focus():
            p.setPen(QPen(qc(t["focus"]), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)


# ============================================================================ switch, checkbox, radio

class ToggleSwitch(KeyboardFocus, QAbstractButton):
    """S4 switch: 34 x 20, a white knob; the track is the accent when on."""

    def __init__(self, parent=None, checked=False):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def sizeHint(self):
        return QSize(38, 24)

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def getPos(self):
        return self._pos

    def setPos(self, v):
        self._pos = v
        self.update()

    knob = Property(float, getPos, setPos)

    def setChecked(self, on):   # keep the knob in place when set from code
        super().setChecked(on)
        if hasattr(self, "_anim") and self._anim.state() != QPropertyAnimation.Running:
            self._pos = 1.0 if on else 0.0
            self.update()

    def paintEvent(self, _):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF((self.width() - 34) / 2, (self.height() - 20) / 2, 34, 20)
        off, on = qc(t["border2"]), qc(t["btn"])
        k = self._pos
        track = QColor(round(off.red() + (on.red() - off.red()) * k), round(off.green() + (on.green() - off.green()) * k),
                       round(off.blue() + (on.blue() - off.blue()) * k),
                       round(off.alpha() + (on.alpha() - off.alpha()) * k))
        if not self.isEnabled():
            track.setAlphaF(track.alphaF() * 0.45)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, 10, 10)
        x = r.left() + 2 + 14 * k
        knob = QRectF(x, r.top() + 2, 16, 16)
        p.setBrush(QColor(0, 0, 0, 60 if self.isEnabled() else 25))
        p.drawEllipse(knob.translated(0, 0.8))
        p.setBrush(QColor(255, 255, 255, 255 if self.isEnabled() else 170))
        p.drawEllipse(knob)
        if self.kbd_focus():
            p.setPen(QPen(qc(t["focus"]), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(-2, -2, 2, 2), 12, 12)


class CheckBox(KeyboardFocus, QAbstractButton):
    """S4 checkbox: a soft rounded square, solid accent with a white tick when on; the label sits to the right."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setAttribute(Qt.WA_Hover)

    def sizeHint(self):
        fm = QFontMetrics(theme.font(13))
        return QSize(26 + fm.horizontalAdvance(self.text()), max(24, fm.height() + 4))

    def paintEvent(self, _):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        box = QRectF(1, (self.height() - 18) / 2, 18, 18)
        if self.isChecked():
            p.setPen(Qt.NoPen)
            p.setBrush(qc(t["btn"]))
            p.drawRoundedRect(box, 6, 6)
            pen = QPen(QColor("#ffffff"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            path = QPainterPath(QPointF(box.left() + 4.6, box.top() + 9.4))
            path.lineTo(box.left() + 7.6, box.top() + 12.4)
            path.lineTo(box.left() + 13.6, box.top() + 6.0)
            p.drawPath(path)
        else:
            hover = self.underMouse() and self.isEnabled()
            p.setPen(QPen(qc(t["acc"] if hover else t["border2"]), 1.5))
            p.setBrush(qc(t["bg_hover"] if hover else t["bg2"]))
            p.drawRoundedRect(box.adjusted(0.75, 0.75, -0.75, -0.75), 5.5, 5.5)
        p.setFont(theme.font(13))
        p.setPen(qc(t["text"] if self.isEnabled() else t["text3"]))
        p.drawText(QRectF(26, 0, self.width() - 26, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text())
        if self.kbd_focus():
            p.setPen(QPen(qc(t["focus"]), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(box.adjusted(-2.5, -2.5, 2.5, 2.5), 8, 8)


class RadioDot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.checked = False

    def paintEvent(self, _):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(1, 1, 16, 16)
        if self.checked:
            c = qc(t["btn"])
            if not self.isEnabled():
                c.setAlphaF(0.45)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawEllipse(r)
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(QRectF(5.5, 5.5, 7, 7))
        else:
            c = qc(t["border2"] if self.isEnabled() else t["border"])
            p.setPen(QPen(c, 1.5))
            p.setBrush(qc(t["bg2"]))
            p.drawEllipse(r.adjusted(0.75, 0.75, -0.75, -0.75))


# ============================================================================ segmented control (and the tabs)

class Segmented(KeyboardFocus, QWidget):
    """S4 segmented control: a pill track; the selected option is an accent pill that slides when clicked (never
    on arrival or when set from code). Options can carry a quieter count ("Files 5"), and can be hidden or
    disabled. `wide=True` shares the full width equally; `tabs=True` is the larger top-bar variant."""
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], parent=None, wide: bool = False, tabs: bool = False):
        super().__init__(parent)
        self._opts = [dict(key=k, label=l, count=None, enabled=True, visible=True) for k, l in options]
        self._cur = self._opts[0]["key"] if self._opts else ""
        self._hover: str | None = None
        self._pill: QRectF | None = None
        self.wide, self.tabs = wide, tabs
        self._seg_h = 30 if tabs else 28
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._slide)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_Hover)
        self.setFocusPolicy(Qt.TabFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding if wide else QSizePolicy.Fixed, QSizePolicy.Fixed)

    # ---- options
    def set_options(self, options: list[tuple[str, str]]):
        keep = {o["key"]: o for o in self._opts}
        self._opts = [dict(keep.get(k, {}), key=k, label=l) for k, l in options]
        for o in self._opts:
            o.setdefault("count", None)
            o.setdefault("enabled", True)
            o.setdefault("visible", True)
        self._relayout()

    def _opt(self, key):
        return next((o for o in self._opts if o["key"] == key), None)

    def set_count(self, key: str, n):
        o = self._opt(key)
        if o is not None and o["count"] != n:
            o["count"] = n
            self._relayout()

    def set_option_enabled(self, key: str, on: bool):
        o = self._opt(key)
        if o is not None and o["enabled"] != on:
            o["enabled"] = on
            self.update()

    def set_option_visible(self, key: str, on: bool):
        o = self._opt(key)
        if o is not None and o["visible"] != on:
            o["visible"] = on
            self._relayout()

    def value(self) -> str:
        return self._cur

    def set_value(self, key: str, animate: bool = False):
        if self._opt(key) is None:
            return
        old = self._rects().get(self._cur)
        self._cur = key
        new = self._rects().get(key)
        if animate and old is not None and new is not None and old != new:
            self._anim.stop()
            self._anim.setStartValue(old)
            self._anim.setEndValue(new)
            self._anim.start()
        else:
            self._anim.stop()
            self._pill = new
        self.update()

    def _slide(self, v):
        self._pill = v
        self.update()

    # ---- geometry
    def _fonts(self):
        px = 13 if self.tabs else 12.5
        return theme.font(px, 500), theme.font(px, 600), num_font(theme.font(px, 600))

    def _text_w(self, o) -> float:
        _, bold, numf = self._fonts()
        w = QFontMetrics(bold).horizontalAdvance(o["label"])
        if o["count"] is not None:
            w += 6 + QFontMetrics(numf).horizontalAdvance(f"{o['count']:,}")
        return w

    def _pad(self) -> int:
        return 16 if self.tabs else 14

    def sizeHint(self):
        vis = [o for o in self._opts if o["visible"]]
        w = sum(self._text_w(o) + 2 * self._pad() for o in vis) + 4 * max(0, len(vis) - 1) + 10
        return QSize(int(w + 0.5), self._seg_h + 10)

    def minimumSizeHint(self):
        return self.sizeHint() if not self.wide else QSize(120, self._seg_h + 10)

    def _rects(self) -> dict[str, QRectF]:
        vis = [o for o in self._opts if o["visible"]]
        if not vis:
            return {}
        x, y, h = 5.0, 5.0, float(self._seg_h)
        natural = [self._text_w(o) + 2 * self._pad() for o in vis]
        avail = self.width() - 10 - 4 * (len(vis) - 1)
        if self.wide and avail > sum(natural):
            natural = [avail / len(vis)] * len(vis)
        out = {}
        for o, w in zip(vis, natural):
            out[o["key"]] = QRectF(x, y, w, h)
            x += w + 4
        return out

    def _relayout(self):
        self.updateGeometry()
        if self._anim.state() != QVariantAnimation.Running:
            self._pill = self._rects().get(self._cur)
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._anim.stop()
        self._pill = self._rects().get(self._cur)

    # ---- painting
    def paintEvent(self, _):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = track.height() / 2
        p.setPen(QPen(qc(t["seg_line"]), 1) if qc(t["seg_line"]).alpha() else Qt.NoPen)
        p.setBrush(qc(t["seg_track"]))
        p.drawRoundedRect(track, rad, rad)
        rects = self._rects()
        reg, bold, numf = self._fonts()
        for key, r in rects.items():
            o = self._opt(key)
            if key == self._hover and key != self._cur and o["enabled"] and self.isEnabled():
                p.setPen(Qt.NoPen)
                p.setBrush(qc(t["hov"]))
                p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        pill = self._pill if self._pill is not None else rects.get(self._cur)
        if pill is not None:
            if t["is_dark"]:
                soft_glow(p, pill, pill.height() / 2, t["seg_glow"], 0, 10, dy=2, steps=6)
            p.setPen(Qt.NoPen)
            c = qc(t["btn"])
            if not self.isEnabled():
                c = qc(t["btn_disabled"])
            p.setBrush(c)
            p.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
        for key, r in rects.items():
            o = self._opt(key)
            on = key == self._cur
            # the text turns white once the sliding pill is over it, so nothing flashes mid-slide
            covered = pill is not None and abs(pill.center().x() - r.center().x()) < r.width() / 2
            enabled = o["enabled"] and self.isEnabled()
            if covered and on:
                col = QColor("#ffffff")
            elif not enabled:
                col = qc(t["text3"])
                col.setAlphaF(col.alphaF() * 0.7)
            else:
                col = qc(t["text"] if key == self._hover else t["text2"])
            f = bold if on else reg
            tw = self._text_w(o)
            x = r.left() + (r.width() - tw) / 2
            p.setFont(f)
            p.setPen(col)
            lw = QFontMetrics(f).horizontalAdvance(o["label"])
            p.drawText(QRectF(x, r.top(), lw + 2, r.height()), Qt.AlignVCenter | Qt.AlignLeft, o["label"])
            if o["count"] is not None:
                cc = QColor(col)
                cc.setAlphaF(cc.alphaF() * (0.92 if on else 0.75))
                p.setPen(cc)
                p.setFont(numf)
                p.drawText(QRectF(x + QFontMetrics(bold).horizontalAdvance(o["label"]) + 6, r.top(), 60, r.height()),
                           Qt.AlignVCenter | Qt.AlignLeft, f"{o['count']:,}")
        if self.kbd_focus():
            p.setPen(QPen(qc(t["focus"]), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(track.adjusted(1, 1, -1, -1), rad - 1, rad - 1)

    # ---- input
    def _key_at(self, pos) -> str | None:
        for key, r in self._rects().items():
            if r.adjusted(-2, -5, 2, 5).contains(QPointF(pos)):
                return key
        return None

    def mouseMoveEvent(self, e):
        k = self._key_at(e.position())
        if k != self._hover:
            self._hover = k
            o = self._opt(k) if k else None
            self.setCursor(Qt.PointingHandCursor if o and o["enabled"] else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self._hover = None
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        k = self._key_at(e.position())
        o = self._opt(k) if k else None
        if o is None or not o["enabled"]:
            return
        if k != self._cur:
            self.set_value(k, animate=True)
        self.changed.emit(k)

    def keyPressEvent(self, e):
        keys = [o["key"] for o in self._opts if o["visible"] and o["enabled"]]
        if self._cur in keys and e.key() in (Qt.Key_Left, Qt.Key_Right):
            i = keys.index(self._cur) + (1 if e.key() == Qt.Key_Right else -1)
            if 0 <= i < len(keys):
                self.set_value(keys[i], animate=True)
                self.changed.emit(keys[i])
            return
        super().keyPressEvent(e)


# ============================================================================ badges and chips

class Badge(QWidget):
    """S4 status badge: it hugs its text, never stretches. kind: ok | warn | bad | accent | mute."""
    ICONS = {"ok": "check", "accent": "alert", "bad": "alert-circle"}

    def __init__(self, text: str = "", kind: str = "mute", parent=None, with_icon: bool = True):
        super().__init__(parent)
        self.text, self.kind, self.with_icon = text, kind, with_icon
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setVisible(bool(text))

    def set(self, text: str, kind: str = "mute", *_legacy):
        self.text, self.kind = text, kind
        self.setVisible(bool(text))
        self.updateGeometry()
        self.update()

    def _font(self):
        return num_font(theme.font(11.5, 600))

    def _has_icon(self):
        return self.with_icon and self.kind in self.ICONS

    def sizeHint(self):
        fm = QFontMetrics(self._font())
        return QSize(fm.horizontalAdvance(self.text) + 16 + (17 if self._has_icon() else 0), 22)

    def colors(self):
        t = T()
        return {"ok": (t["ok"], t["ok_bg"]), "warn": (t["warn"], t["warn_bg"]), "bad": (t["bad"], t["bad_bg"]),
                "accent": (t["badge_accent"], t["accent_soft"])}.get(self.kind, (t["text2"], t["mute_bg"]))

    def paintEvent(self, _):
        if not self.text:
            return
        fg, bg = self.colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        hint = self.sizeHint()
        w, h = min(self.width(), hint.width()), 20.0
        r = QRectF(0, (self.height() - h) / 2, w, h)
        p.setPen(Qt.NoPen)
        p.setBrush(qc(bg))
        p.drawRoundedRect(r, 10, 10)
        x = 8.0
        if self._has_icon():
            p.drawPixmap(QPointF(x, r.top() + 4), draw_icon(self.ICONS[self.kind], fg, 12))
            x += 17
        p.setFont(self._font())
        p.setPen(qc(fg))
        p.drawText(QRectF(x, r.top(), w - x - 6, h), Qt.AlignVCenter | Qt.AlignLeft, self.text)


# the app's verdict kinds -> badge colours
BADGE_KIND = {"ready": "ok", "done": "ok", "check": "accent", "low": "accent", "flag": "accent", "failed": "bad",
              "busy": "warn", "dim": "mute", "": "mute"}


class StatusTag(Badge):
    """A file's status badge, from the verdict kinds (ready, check, low, done, failed, busy, dim)."""

    def set(self, text: str, kind: str = "", *_legacy):
        super().set(text, BADGE_KIND.get(kind, "mute"))


def chip(text: str) -> QLabel:
    """A small code: SRT, ASS, PGS."""
    c = QLabel(text)
    c.setObjectName("Chip")
    c.setFont(num_font(c.font()))
    c.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    c.setAlignment(Qt.AlignCenter)
    return c


def label(text: str = "", name: str = "", wrap: bool = False, parent=None) -> QLabel:
    lb = QLabel(text, parent)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


class ElidedLabel(QLabel):
    """One line that ends in "…" when it doesn't fit, with the full text as a tooltip only when it is cut off."""

    def __init__(self, text: str = "", name: str = "", mode=Qt.ElideRight, parent=None):
        super().__init__(parent)
        if name:
            self.setObjectName(name)
        self._full = text
        self._mode = mode
        self._tip = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(40)
        self._elide()

    def setText(self, text: str):
        self._full = text or ""
        self._elide()

    def full_text(self) -> str:
        return self._full

    def set_tip(self, tip: str):
        """A tooltip that shows even when the text fits (e.g. the full path)."""
        self._tip = tip
        self._elide()

    def _elide(self):
        shown = self.fontMetrics().elidedText(self._full, self._mode, max(20, self.width()))
        super().setText(shown)
        set_tip(self, self._tip or (self._full if shown != self._full else ""))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._elide()

    def sizeHint(self):
        s = super().sizeHint()
        return QSize(min(s.width(), 400), s.height())


# ============================================================================ option rows

class OptionRow(QWidget):
    """Title + description on the left, switch on the right; the whole row is clickable."""
    toggled = Signal(bool)

    def __init__(self, title: str, desc: str = "", checked=False, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 5, 0, 5)
        lay.setSpacing(14)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.title = label(title, "OptTitle")
        col.addWidget(self.title)
        self.desc = label(desc, self._desc_name(), wrap=True)
        self.desc.setVisible(bool(desc))
        col.addWidget(self.desc)
        lay.addLayout(col, 1)
        self.switch = ToggleSwitch(checked=checked)
        self.switch.toggled.connect(self.toggled)
        lay.addWidget(self.switch, 0, Qt.AlignVCenter)

    def _desc_name(self):
        return "OptDesc"

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.isEnabled() and self.rect().contains(e.position().toPoint()):
            self.switch.toggle()
        super().mouseReleaseEvent(e)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.EnabledChange:
            for w in (self.title, self.desc):
                w.setEnabled(self.isEnabled())

    def isChecked(self):
        return self.switch.isChecked()

    def setChecked(self, v):
        self.switch.setChecked(v)


class RuleRow(OptionRow):
    """An edit type in Review: title, example, how many lines it touches in this file, and a switch."""

    def __init__(self, title: str, desc: str = "", checked=False, parent=None):
        super().__init__(title, desc, checked, parent)
        self.layout().setContentsMargins(0, 4, 0, 4)
        self.count = QLabel("")
        self.count.setObjectName("RuleCount")
        self.count.setFont(num_font(theme.font(13, 600)))
        self.count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.count.setMinimumWidth(36)
        self.layout().insertWidget(1, self.count, 0, Qt.AlignVCenter)

    def _desc_name(self):
        return "RuleDesc"

    def set_count(self, n: int | None):
        self.count.setText("" if n is None else f"{n:,}")
        set_prop(self.count, "zero", not n)


# ============================================================================ banners

class Banner(QWidget):
    """A message box inside the page: warn (amber), info (accent tint) or bad (red); icon, text, optional button."""

    def __init__(self, text: str = "", button_text: str | None = None, slot=None, tone: str = "info", parent=None):
        super().__init__(parent)
        self.tone = tone
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 10, 10)
        lay.setSpacing(10)
        self.icon = QLabel()
        self.icon.setFixedSize(18, 18)
        lay.addWidget(self.icon, 0, Qt.AlignTop)
        self.label = label(text, "BannerText", wrap=True)
        self.label.setProperty("tone", tone)
        lay.addWidget(self.label, 1)
        self.button = None
        if button_text:
            self.button = button(button_text, "", "sm", slot)
            lay.addWidget(self.button, 0, Qt.AlignVCenter)
        self.set_tone(tone)

    def set_tone(self, tone: str):
        self.tone = tone
        set_prop(self.label, "tone", tone)
        themed_icon(self.icon, "alert" if tone in ("warn", "bad") else "info",
                    {"warn": "warn", "bad": "bad"}.get(tone, "text2"), 18)
        self.update()

    def paintEvent(self, _):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg = {"warn": t["warn_bg"], "bad": t["bad_bg"], "quiet": t["mute_bg"]}.get(self.tone, t["accent_soft"])
        p.setPen(Qt.NoPen)
        p.setBrush(qc(bg))
        p.drawRoundedRect(QRectF(self.rect()), 10, 10)


# ============================================================================ track tile (Review sidebar)

class TrackCard(QWidget):
    """One subtitle track. Selected: a soft accent fill (no outline) and a filled radio dot."""
    clicked = Signal(int)

    def __init__(self, track, parent=None):
        super().__init__(parent)
        self.track = track
        self.selected = False
        self.setAttribute(Qt.WA_Hover)
        self.setCursor(Qt.PointingHandCursor if track.text_based else Qt.ArrowCursor)
        g = QGridLayout(self)
        g.setContentsMargins(12, 10, 12, 10)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(3)
        self.dot = RadioDot()
        self.dot.setEnabled(track.text_based)
        g.addWidget(self.dot, 0, 0, Qt.AlignVCenter)

        title = track.title or track.lang_label
        if getattr(track, "external", ""):
            parts = os.path.basename(track.external).split(".")
            tail = [parts.pop()]
            while len(parts) > 1 and len(parts[-1]) <= 7 and parts[-1].replace("-", "").isalnum():
                tail.insert(0, parts.pop())
            title = "Subtitle File ." + ".".join(tail)
            set_tip(self, track.external)
        self.title = ElidedLabel(title, "TrackTitle")
        self.title.setFont(theme.font(14, 600))
        self.title.setEnabled(track.text_based)
        g.addWidget(self.title, 0, 1)
        self.codec = chip(track.codec_label)
        g.addWidget(self.codec, 0, 2, Qt.AlignRight | Qt.AlignVCenter)

        meta_bits = []
        if getattr(track, "external", ""):
            meta_bits += [f"{track.lang_label}, next to the video"]
        else:
            if track.title and track.lang_label.lower() not in track.title.lower():
                meta_bits.append(track.lang_label)
            meta_bits.append(f"track {track.sub_pos + 1}")
        meta = ", ".join(meta_bits)
        line = QHBoxLayout()
        line.setSpacing(10)
        self.meta = ElidedLabel(meta[:1].upper() + meta[1:], "Hint")    # the description gives way, never the count
        line.addWidget(self.meta, 1)
        self.stat = QLabel("")
        self.stat.setObjectName("TrackStat")
        self.stat.setFont(num_font(self.stat.font()))
        line.addWidget(self.stat, 0, Qt.AlignRight | Qt.AlignVCenter)
        g.addLayout(line, 1, 1, 1, 2)

        self.badge_row = QWidget()
        self.badges = QHBoxLayout(self.badge_row)
        self.badges.setSpacing(6)
        self.badges.setContentsMargins(0, 4, 0, 0)
        self._fixed_badges = 0
        for on, text in ((track.default, "Default"), (track.forced, "Forced"), (not track.text_based, "Picture")):
            if on:
                self.badges.addWidget(Badge(text, "mute"))
                self._fixed_badges += 1
        self.sdh_badge = Badge("SDH", "accent", with_icon=False)
        self.badges.insertWidget(min(1, self._fixed_badges) if track.default else 0, self.sdh_badge)
        self.sdh_badge.setVisible(track.sdh_flagged)
        self.badges.addStretch(1)
        g.addWidget(self.badge_row, 2, 1, 1, 2)
        self.badge_row.setVisible(self._fixed_badges > 0 or track.sdh_flagged)
        g.setColumnStretch(1, 1)
        if not track.text_based:
            self.set_stat("Can't edit picture subtitles", "dim")
            set_tip(self, "This track is made of images (Blu-ray PGS / DVD VobSub), not text.\n"
                          "It would need OCR before it can be cleaned.")

    def set_selected(self, on: bool):
        self.selected = on
        set_prop(self.meta, "on", on)            # the faintest grey is too light on the accent tint
        self.dot.checked = on
        self.dot.update()
        self.update()

    def set_stat(self, text: str, tone: str = ""):
        set_prop(self.stat, "tone", tone)
        self.stat.setText(text)

    def mark_sdh(self, on: bool):
        show = on or self.track.sdh_flagged
        self.sdh_badge.setVisible(show)
        self.badge_row.setVisible(self._fixed_badges > 0 or show)

    def paintEvent(self, _):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.selected:
            p.setPen(Qt.NoPen)
            p.setBrush(qc(t["tile_on"]))
        elif not self.track.text_based:
            p.setPen(QPen(qc(t["border"]), 1))
            p.setBrush(Qt.NoBrush)
        else:
            p.setPen(QPen(qc(t["border"]), 1))
            p.setBrush(qc(t["hov"] if self.underMouse() else t["bg2"]))
        p.drawRoundedRect(r, 12, 12)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.track.text_based and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.track.index)
        super().mouseReleaseEvent(e)


# ============================================================================ file row (main list)

class FileRow(QWidget):
    """One file in the list: a slim status bar at the left edge plus one badge (never a tinted row); name, the new
    name and a detail line; the line count; Review and remove."""
    clicked = Signal(int)
    remove_clicked = Signal(int)
    EDGE = {"ready": "ok", "done": "ok", "check": "btn", "low": "btn", "failed": "bad", "busy": "warn", "dim": "mute_line"}

    def __init__(self, uid: int, name: str, is_sub: bool, parent=None):
        super().__init__(parent)
        self.uid = uid
        self.is_sub = is_sub
        self.sep = False                       # a hairline above this row (every row but the first)
        self._kind = ""
        self._done = False
        self.setAttribute(Qt.WA_Hover)
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 12, 12, 12)
        lay.setSpacing(16)
        self.icon = QLabel()
        self.icon.setFixedSize(20, 20)
        themed_icon(self.icon, "sub" if is_sub else "film", "text2", 20)
        lay.addWidget(self.icon, 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.name = ElidedLabel(name, "FileName", Qt.ElideMiddle)
        self.name.setFont(theme.font(14, 600))
        col.addWidget(self.name)
        self.rename_row = QWidget()
        rl = QHBoxLayout(self.rename_row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        self.rename_icon = QLabel()
        self.rename_icon.setFixedSize(14, 14)
        rl.addWidget(self.rename_icon)
        self.rename = ElidedLabel("", "FileRename", Qt.ElideMiddle)
        rl.addWidget(self.rename, 1)
        self.rename_row.hide()
        col.addWidget(self.rename_row)
        self.detail = ElidedLabel("Waiting", "FileDetail")
        col.addWidget(self.detail)
        lay.addLayout(col, 1)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedWidth(110)
        self.bar.setRange(0, 1000)
        self.bar.hide()
        lay.addWidget(self.bar, 0, Qt.AlignVCenter)
        self.count = QLabel("")
        self.count.setObjectName("FileCount")
        self.count.setFont(num_font(theme.font(13)))
        self.count.setTextFormat(Qt.RichText)
        self.count.setMinimumWidth(96)
        self.count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.count, 0, Qt.AlignVCenter)
        slot = QWidget()                       # a fixed slot so badges line up, each hugging its text
        slot.setFixedWidth(118)
        sl = QHBoxLayout(slot)
        sl.setContentsMargins(0, 0, 0, 0)
        self.status = StatusTag()
        sl.addWidget(self.status, 0, Qt.AlignLeft | Qt.AlignVCenter)
        sl.addStretch(1)
        lay.addWidget(slot, 0, Qt.AlignVCenter)
        self.review = button("Review", "", "sm", lambda: self.clicked.emit(self.uid))
        lay.addWidget(self.review, 0, Qt.AlignVCenter)
        self.remove = icon_button("close", "Remove from the list", lambda: self.remove_clicked.emit(self.uid), "sm")
        lay.addWidget(self.remove, 0, Qt.AlignVCenter)

    def set_state(self, detail: str, chip_text: str = "", kind: str = "", progress: float | None = None,
                  review: bool = True, count: str = ""):
        self._kind = kind
        self.detail.setText(detail)
        self.status.set(chip_text, kind)
        self._done = kind == "done"
        set_prop(self.name, "done", self._done)
        if count and " " in count:
            n, rest = count.split(" ", 1)
            t = T()
            count = f'<span style="color:{qc(t["text"]).name()}; font-weight:600">{n}</span> {rest}'
        self.count.setText(count)
        self.count.setVisible(progress is None)
        if progress is None:
            self.bar.hide()
        else:
            self.bar.show()
            if progress < 0:
                self.bar.setRange(0, 0)
            else:
                self.bar.setRange(0, 1000)
                self.bar.setValue(int(progress * 1000))
        self.review.setVisible(review)
        self.update()

    def set_busy(self, on: bool):
        self.remove.setEnabled(not on)

    def set_rename(self, text: str, tone: str = ""):
        """The line under the name: the new file name, or where the title lookup stands."""
        self.rename_row.setVisible(bool(text))
        if not text:
            return
        self.rename.setText(text)
        set_prop(self.rename, "tone", tone)
        kind, role = {"alert": ("alert", "warn"), "faint": ("info", "text3")}.get(tone, ("arrow-right", "text2"))
        themed_icon(self.rename_icon, kind, role, 14)

    def set_name(self, name: str):
        self.name.setText(name)

    def paintEvent(self, _):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        if self.sep:
            p.setPen(QPen(qc(t["border"]), 1))
            p.drawLine(QPointF(r.left() + 14, r.top() + 0.5), QPointF(r.right() - 14, r.top() + 0.5))
        if self.underMouse():
            p.setPen(Qt.NoPen)
            p.setBrush(qc(t["bg_hover"]))
            p.drawRoundedRect(r.adjusted(6, 2, -6, -2), 10, 10)
        role = self.EDGE.get(self._kind)
        if role:
            p.setPen(Qt.NoPen)
            p.setBrush(qc(t[role]))
            p.drawRoundedRect(QRectF(r.left() + 12, r.top() + 14, 3, r.height() - 28), 1.5, 1.5)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()) and self.review.isVisible():
            self.clicked.emit(self.uid)
        super().mouseReleaseEvent(e)


# ============================================================================ legend

class KeyHints(QWidget):
    """A quiet line of keyboard shortcuts: each is one or more keycaps and what they do. Keys that are alternatives
    for one action are written together ("J" "K"), and the text says what they do."""

    def __init__(self, hints: list[tuple[list[str], str]], parent=None):
        super().__init__(parent)
        self.hints = hints
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(26)

    def _fonts(self):
        text = theme.font(12)
        key = QFont(theme.font(11, 600))
        return text, key

    def _layout(self) -> list:
        """[(kind, text, x, width)] for everything that fits."""
        text_f, key_f = self._fonts()
        tm, km = QFontMetrics(text_f), QFontMetrics(key_f)
        out, x = [], 0
        for keys, what in self.hints:
            w = sum(max(20, km.horizontalAdvance(k) + 12) + 4 for k in keys) + 4 + tm.horizontalAdvance(what)
            if x + w > self.width() and out:
                break
            for k in keys:
                kw = max(20, km.horizontalAdvance(k) + 12)
                out.append(("key", k, x, kw))
                x += kw + 4
            x += 4
            tw = tm.horizontalAdvance(what)
            out.append(("text", what, x, tw))
            x += tw + 18
        return out

    def sizeHint(self):
        return QSize(self.width() or 400, 26)

    def paintEvent(self, _):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        text_f, key_f = self._fonts()
        h = self.height()
        for kind, text, x, w in self._layout():
            if kind == "text":
                p.setFont(text_f)
                p.setPen(qc(t["text3"]))
                p.drawText(QRectF(x, 0, w + 2, h), Qt.AlignVCenter | Qt.AlignLeft, text)
                continue
            r = QRectF(x, (h - 20) / 2, w, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(qc(t["border2"]))                       # the key's lower edge
            p.drawRoundedRect(r.translated(0, 1.5), 5, 5)
            p.setBrush(qc(t["pop_bg"] if t["is_dark"] else "#ffffff"))
            p.setPen(QPen(qc(t["border2"]), 1))
            p.drawRoundedRect(r, 5, 5)
            p.setFont(key_f)
            p.setPen(qc(t["text2"]))
            p.drawText(r, Qt.AlignCenter, text)


class Legend(QWidget):
    """Line counts, each with a swatch in the colour those lines have on the timeline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[tuple[str, str, bool]] = []          # (colour role or value, text, emphasise)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_items(self, items: list[tuple[str, str, bool]]):
        self.items = items
        set_tip(self, "")
        self.updateGeometry()
        self.update()

    def _fonts(self):
        f = num_font(theme.font(13))
        b = QFont(f)
        b.setWeight(QFont.DemiBold)
        return f, b

    def sizeHint(self):
        f, b = self._fonts()
        w = sum(QFontMetrics(b if s else f).horizontalAdvance(t) + 36 for _, t, s in self.items)
        return QSize(w, 28)

    def minimumSizeHint(self):
        return QSize(40, 28)

    def paintEvent(self, _):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        x, cy = 0.0, self.height() / 2
        hidden = []
        reg, bold = self._fonts()
        for color, text, strong in self.items:
            f = bold if strong else reg
            fm = QFontMetrics(f)
            w = 16 + fm.horizontalAdvance(text)
            if x + w > self.width():
                hidden.append(text)
                continue
            p.setPen(Qt.NoPen)
            p.setBrush(qc(t.get(color, color)))
            p.drawRoundedRect(QRectF(x, cy - 4.5, 9, 9), 2.5, 2.5)
            p.setFont(f)
            p.setPen(qc(t["text"] if strong else t["text2"]))
            p.drawText(QRectF(x + 16, 0, w, self.height()), Qt.AlignVCenter | Qt.AlignLeft, text)
            x += w + 18
        p.end()
        if hidden:
            set_tip(self, "\n".join(hidden))


# ============================================================================ the screen

class ScreenView(QWidget):
    """An always-dark strip that shows a subtitle line the way it will look on the TV: white caption text with a
    soft outline, the timecode in one corner and what happened to the line in the other."""

    def __init__(self, parent=None, height: int = 150):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(height)
        self.caption = ""
        self.corner_left = ""
        self.corner_right = ""
        self.right_alert = False
        self.italic = False
        self.placeholder = ""
        self.demo: tuple[str, str] | None = None        # welcome demo: a sound tag struck out and scrubbed away
        self._demo_t = 0.0

    def show_line(self, caption: str, left: str = "", right: str = "", alert: bool = False, italic: bool = False,
                  placeholder: str = ""):
        self.caption, self.corner_left, self.corner_right = caption, left, right
        self.right_alert, self.italic, self.placeholder = alert, italic, placeholder
        self.update()

    def getDemo(self):
        return self._demo_t

    def setDemo(self, v):
        self._demo_t = v
        self.update()

    demo_t = Property(float, getDemo, setDemo)

    def paintEvent(self, _):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QPen(qc(t["screen_line"]), 1))
        p.setBrush(qc(t["screen"]))
        p.drawRoundedRect(r, 10, 10)
        small = num_font(theme.font(12, 600))
        p.setFont(small)
        pad = 16
        if self.corner_left:
            p.setPen(QColor(255, 255, 255, 128))
            p.drawText(QRectF(pad, 10, self.width() / 2, 20), Qt.AlignLeft | Qt.AlignVCenter, self.corner_left)
        if self.corner_right:
            fm = QFontMetrics(small)
            txt = fm.elidedText(self.corner_right, Qt.ElideRight, int(self.width() * 0.62))
            p.setPen(QColor(t["accent_light"]) if self.right_alert else QColor(255, 255, 255, 128))
            p.drawText(QRectF(self.width() * 0.3, 10, self.width() * 0.7 - pad, 20), Qt.AlignRight | Qt.AlignVCenter, txt)
        cap = theme.font(22 if self.height() >= 200 else 20, 700, self.italic)
        fm = QFontMetrics(cap)
        if self.demo:
            self._paint_demo(p, cap, fm)
            p.end()
            return
        text = self.caption
        if not text:
            if self.placeholder:
                p.setFont(theme.font(13, 500))
                p.setPen(QColor(255, 255, 255, 110))
                p.drawText(QRectF(0, 0, self.width(), self.height()), Qt.AlignCenter, self.placeholder)
            p.end()
            return
        lines = text.split("\n")[:3]
        lh = fm.height() + 2
        y0 = self.height() - 18 - lh * len(lines)
        for i, line in enumerate(lines):
            line = fm.elidedText(line, Qt.ElideRight, self.width() - 2 * pad)
            w = fm.horizontalAdvance(line)
            self._outlined(p, cap, (self.width() - w) / 2, y0 + i * lh + fm.ascent(), line)
        p.end()

    def _outlined(self, p: QPainter, font: QFont, x: float, y: float, text: str, alpha: int = 255):
        path = QPainterPath()
        path.addText(QPointF(x, y), font, text)
        p.setPen(QPen(QColor(0, 0, 0, int(alpha * 0.85)), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, alpha))
        p.drawPath(path)

    def _paint_demo(self, p: QPainter, font: QFont, fm: QFontMetrics):
        tag, line = self.demo
        k = self._demo_t                   # 0..1 strike, 1..2 fade the tag out and close the gap
        gap = fm.horizontalAdvance(" ")
        tw = fm.horizontalAdvance(tag)
        lw = fm.horizontalAdvance(line)
        fade = max(0.0, min(1.0, k - 1.0))
        total = (tw + gap) * (1 - fade) + lw
        x = (self.width() - total) / 2
        y = self.height() - 24 - fm.descent()
        if fade < 1.0:
            self._outlined(p, font, x - (tw + gap) * fade, y, tag, int(255 * (1 - fade) * 0.55))
            strike = min(1.0, k)
            if strike > 0:
                p.setPen(QPen(QColor(T()["accent_light"]), 3, Qt.SolidLine, Qt.RoundCap))
                sy = y - fm.ascent() * 0.33
                left = x - (tw + gap) * fade
                p.setOpacity(1 - fade)
                p.drawLine(QPointF(left - 2, sy), QPointF(left + (tw + 2) * strike, sy))
                p.setOpacity(1.0)
        self._outlined(p, font, x + (tw + gap) * (1 - fade), y, line)


# ============================================================================ choice tiles (dialogs)

class ChoiceButton(KeyboardFocus, QAbstractButton):
    """A big choice: a semibold title over a quieter description, an optional badge and a chevron.
    The recommended one is a soft accent fill (no outline)."""

    def __init__(self, title: str, desc: str = "", recommended: bool = False, badge: str = "", parent=None):
        super().__init__(parent)
        self.recommended = recommended
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setAttribute(Qt.WA_Hover)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 11, 12, 11)
        lay.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = label(title, "ChoiceTitle", wrap=True)
        self.title.setFont(theme.font(14, 600))
        self.desc = label(desc, "OptDesc", wrap=True)
        self.desc.setVisible(bool(desc))
        for w in (self.title, self.desc):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
            col.addWidget(w)
        lay.addLayout(col, 1)
        if badge:
            b = Badge(badge, "accent", with_icon=False)
            b.setAttribute(Qt.WA_TransparentForMouseEvents)
            lay.addWidget(b, 0, Qt.AlignVCenter)
        self.chev = QLabel()
        self.chev.setFixedSize(15, 15)
        self.chev.setAttribute(Qt.WA_TransparentForMouseEvents)
        themed_icon(self.chev, "chev-right", "text3", 15)
        lay.addWidget(self.chev, 0, Qt.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    def sizeHint(self):
        return self.layout().sizeHint()

    def minimumSizeHint(self):
        return self.layout().minimumSize()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self.layout().heightForWidth(w)

    def paintEvent(self, _):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        hover = self.underMouse()
        if self.recommended:
            p.setPen(Qt.NoPen)
            p.setBrush(qc(t["tile_on"]))
            p.drawRoundedRect(r, 12, 12)
            if hover or self.isDown():
                p.setBrush(qc(t["hov"]))
                p.drawRoundedRect(r, 12, 12)
        else:
            p.setPen(QPen(qc(t["border"]), 1))
            p.setBrush(qc(t["press"] if self.isDown() else (t["hov"] if hover else t["bg2"])))
            p.drawRoundedRect(r, 12, 12)
        if self.kbd_focus():
            p.setPen(QPen(qc(t["focus"]), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 11, 11)


# ============================================================================ section list (Settings)

class NavList(KeyboardFocus, QWidget):
    """A vertical list of sections. The active one is the accent gradient with a white label and icon and a faint
    glow; it slides when clicked."""
    changed = Signal(str)
    ITEM_H, GAP = 40, 6

    def __init__(self, items: list[tuple[str, str, str]], parent=None):
        super().__init__(parent)
        self.items = items                         # (key, label, icon)
        self._cur = items[0][0] if items else ""
        self._hover = None
        self._y: float | None = None
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._slide)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_Hover)
        self.setFocusPolicy(Qt.TabFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.setFixedWidth(244)

    def sizeHint(self):
        return QSize(244, 12 * 2 + len(self.items) * (self.ITEM_H + self.GAP))

    def value(self):
        return self._cur

    def _rect(self, i) -> QRectF:
        return QRectF(12, 12 + i * (self.ITEM_H + self.GAP), self.width() - 24, self.ITEM_H)

    def _index(self, key):
        return next((i for i, it in enumerate(self.items) if it[0] == key), 0)

    def set_value(self, key: str, animate: bool = False):
        old = self._rect(self._index(self._cur)).top()
        self._cur = key
        new = self._rect(self._index(key)).top()
        if animate and old != new:
            self._anim.stop()
            self._anim.setStartValue(float(old))
            self._anim.setEndValue(float(new))
            self._anim.start()
        else:
            self._y = None
            self.update()

    def _slide(self, v):
        self._y = v
        self.update()

    def paintEvent(self, _):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        ci = self._index(self._cur)
        active = self._rect(ci)
        if self._y is not None and self._anim.state() == QVariantAnimation.Running:
            active.moveTop(self._y)
        for i, (key, text, ic) in enumerate(self.items):
            r = self._rect(i)
            if key == self._hover and key != self._cur:
                p.setPen(Qt.NoPen)
                p.setBrush(qc(t["hov"]))
                p.drawRoundedRect(r, 12, 12)
        soft_glow(p, active, 12, t["nav_glow"], 0, 12 if t["is_dark"] else 6, dy=0 if t["is_dark"] else 3, steps=7)
        g = QLinearGradient(active.topLeft(), active.bottomRight())
        g.setColorAt(0, qc(t["nav1"]))
        g.setColorAt(0.55, qc(t["nav2"]))
        g.setColorAt(1, qc(t["nav3"]))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(active, 12, 12)
        for i, (key, text, ic) in enumerate(self.items):
            r = self._rect(i)
            covered = abs(active.center().y() - r.center().y()) < r.height() / 2
            on = key == self._cur and covered
            col = "#ffffff" if on else (t["text"] if key == self._hover else t["text2"])
            p.drawPixmap(QPointF(r.left() + 12, r.top() + 10), draw_icon(ic, col, 20, strong_duo=on))
            p.setFont(theme.font(14, 600 if on else 500))
            p.setPen(qc(col))
            p.drawText(QRectF(r.left() + 42, r.top(), r.width() - 48, r.height()), Qt.AlignVCenter | Qt.AlignLeft, text)
        if self.kbd_focus():
            p.setPen(QPen(qc(t["focus"]), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(self._rect(ci).adjusted(-2, -2, 2, 2), 13, 13)

    def _key_at(self, pos):
        for i, it in enumerate(self.items):
            if self._rect(i).contains(QPointF(pos)):
                return it[0]
        return None

    def mouseMoveEvent(self, e):
        k = self._key_at(e.position())
        if k != self._hover:
            self._hover = k
            self.update()

    def leaveEvent(self, e):
        self._hover = None
        self.update()

    def mousePressEvent(self, e):
        k = self._key_at(e.position())
        if k and k != self._cur:
            self.set_value(k, animate=True)
            self.changed.emit(k)

    def keyPressEvent(self, e):
        i = self._index(self._cur)
        if e.key() in (Qt.Key_Up, Qt.Key_Down):
            j = i + (1 if e.key() == Qt.Key_Down else -1)
            if 0 <= j < len(self.items):
                self.set_value(self.items[j][0], animate=True)
                self.changed.emit(self.items[j][0])
            return
        super().keyPressEvent(e)


# ============================================================================ bars

class Bar(QWidget):
    """The top bar and the bottom action bar: a glass strip with a hairline, and the primary button's glow."""

    def __init__(self, edge: str = "bottom", parent=None):
        super().__init__(parent)
        self.edge = edge
        self.glow: list = []

    def paintEvent(self, _):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.fillRect(r, qc(t["bar_bg"]))
        p.setPen(QPen(qc(t["border"]), 1))
        y = r.bottom() - 0.5 if self.edge == "bottom" else r.top() + 0.5
        p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        paint_backlight(p, self, self.glow)


# ============================================================================ tooltips and menus

class TipWindow(QLabel):
    """S4 tooltip: dark rounded glass with white text. Qt's own tooltip can't round its corners."""

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setWordWrap(True)
        self.setTextFormat(Qt.AutoText)
        self.setContentsMargins(11, 7, 11, 8)
        self.setStyleSheet("color: #ffffff; background: transparent;")
        self.setFont(theme.font(13))
        self.setMaximumWidth(480)

    def paintEvent(self, e):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        line = qc(t["tip_line"])
        p.setPen(QPen(line, 1) if line.alpha() else Qt.NoPen)
        p.setBrush(qc(t["tip_bg"]))
        p.drawRoundedRect(r, 10, 10)
        p.end()
        super().paintEvent(e)


class Tips(QObject):
    """Shows TipWindow instead of Qt's square tooltip, for widgets given a tip with set_tip() (and show_text calls).
    It watches only those widgets: an app-wide event filter would run Python on every event in the app."""
    _inst = None

    def __init__(self, app: QApplication):
        super().__init__(app)
        self.win = TipWindow()
        self.owner = None
        self._hide = QTimer(self)
        self._hide.setSingleShot(True)
        self._hide.timeout.connect(self.hide)
        Tips._inst = self

    def watch(self, w: QWidget):
        if not w.property("_tipwatch"):
            w.setProperty("_tipwatch", True)
            w.installEventFilter(self)

    @classmethod
    def install(cls, app: QApplication):
        return cls._inst or cls(app)

    def show_text(self, gpos: QPoint, text: str, owner=None):
        if not text:
            self.hide()
            return
        if text != self.win.text():
            self.win.setText(text)
            self.win.adjustSize()
        self.owner = owner
        scr = QGuiApplication.screenAt(gpos) or QGuiApplication.primaryScreen()
        geo = scr.availableGeometry() if scr else QRect(0, 0, 4000, 4000)
        x, y = gpos.x() + 12, gpos.y() + 20
        if x + self.win.width() > geo.right() - 4:
            x = geo.right() - 4 - self.win.width()
        if y + self.win.height() > geo.bottom() - 4:
            y = gpos.y() - 12 - self.win.height()
        self.win.move(max(geo.left() + 4, x), max(geo.top() + 4, y))
        self.win.show()
        self.win.raise_()
        self._hide.start(10000)

    def hide(self):
        self.win.hide()
        self.owner = None

    def eventFilter(self, obj, ev):
        et = ev.type()
        if et == QEvent.ToolTip and isinstance(obj, QWidget):
            text = obj.toolTip()
            if text:
                self.show_text(ev.globalPos(), text, obj)
                return True
            return False
        if et in (QEvent.Leave, QEvent.MouseButtonPress, QEvent.Wheel, QEvent.KeyPress, QEvent.Hide) \
                and self.win.isVisible():
            self.hide()
        return False


def set_tip(w: QWidget, text: str):
    """A tooltip in the S4 Glass look (rounded, dark glass)."""
    w.setToolTip(text)
    app = QApplication.instance()
    if text and app is not None:
        Tips.install(app).watch(w)


def show_tip(gpos: QPoint, text: str, owner=None):
    if Tips._inst is not None:
        Tips._inst.show_text(gpos, text, owner)


def hide_tip():
    if Tips._inst is not None:
        Tips._inst.hide()


def make_menu(parent=None) -> QMenu:
    """A pop-over menu on one solid surface with 12px corners (the window must be see-through for the corners)."""
    m = QMenu(parent)
    m.setWindowFlags(m.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    m.setAttribute(Qt.WA_TranslucentBackground)
    return m


def hline() -> QWidget:
    """A hairline divider."""
    w = _Hairline()
    w.setFixedHeight(1)
    return w


class _Hairline(QWidget):
    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), qc(T()["border"]))
