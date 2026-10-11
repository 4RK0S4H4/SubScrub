"""Small custom widgets: toggle switch, option rows, segmented control, track cards,
and line icons drawn with QPainter (so nothing depends on icon fonts)."""
from __future__ import annotations

from PySide6.QtCore import (Property, QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt,
                            Signal)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)


def repolish(w: QWidget):
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def set_prop(w: QWidget, name: str, value):
    if w.property(name) != value:
        w.setProperty(name, value)
        repolish(w)


# ============================================================================ icons

# our names -> (Solar name, style). Linear for small controls; bold duotone where the icon carries the screen.
_ICON_MAP = {
    "film": ("clapperboard-play", "duo"), "sub": ("subtitles", "duo"), "upload": ("upload-minimalistic", "duo"),
    "check": ("check-read", "lin"), "check-circle": ("check-circle", "lin"), "alert": ("danger-triangle", "lin"),
    "folder": ("folder", "duo"), "more": ("menu-dots", "lin"), "chev-up": ("alt-arrow-up", "lin"),
    "chev-down": ("alt-arrow-down", "lin"), "chev-right": ("alt-arrow-right", "lin"), "close": ("close", "lin"),
    "back": ("arrow-left", "lin"), "gear": ("settings", "lin"), "flag": ("flag", "lin"), "undo": ("undo-left", "lin"),
    "sun": ("sun", "lin"), "moon": ("moon", "lin"), "keyboard": ("keyboard", "lin"), "magic": ("magic-stick", "duo"),
}


def draw_icon(kind: str, color: str, size: int = 20, dpr: float = 2.0) -> QPixmap:
    """A Solar icon (CC BY 4.0, 480 Design) in the given colour. `color` may be '#rrggbb' or 'rgba(...)'."""
    from PySide6.QtCore import QByteArray
    from PySide6.QtSvg import QSvgRenderer
    from . import solar
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    name, style = _ICON_MAP.get(kind, ("close", "lin"))
    body = (solar.DUOTONE if style == "duo" else solar.LINEAR).get(name) or solar.LINEAR["close"]
    from .theme import qc
    c = qc(color)
    hexcol = c.name()
    body = body.replace("currentColor", hexcol)
    if c.alpha() < 255:
        body = f'<g opacity="{c.alphaF():.3f}">{body}</g>'
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">{body}</svg>'
    r = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, size, size))
    p.end()
    return pm


def icon(kind: str, color: str, size: int = 20) -> QIcon:
    return QIcon(draw_icon(kind, color, size))


def logo_pixmap(size: int = 64, dpr: float = 2.0) -> QPixmap:
    """App mark: near-black rounded square, a red and a white subtitle line, a faded [ ] tag scrubbed away."""
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    from PySide6.QtGui import QLinearGradient
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor("#2E2E33"))
    g.setColorAt(1, QColor("#0F0F11"))
    p.setBrush(g)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.24, size * 0.24)
    u = size / 64
    p.setBrush(QColor("#C81E3A"))
    p.drawRoundedRect(QRectF(14 * u, 30 * u, 36 * u, 7 * u), 3.5 * u, 3.5 * u)
    p.setBrush(QColor(255, 255, 255))
    p.drawRoundedRect(QRectF(19 * u, 42 * u, 26 * u, 7 * u), 3.5 * u, 3.5 * u)
    # a faded "[ ]" sound tag above the dialogue lines: the bit that gets scrubbed away
    pen = QPen(QColor(255, 255, 255, 120), 3.2 * u, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPolyline([QPointF(27 * u, 12 * u), QPointF(23 * u, 12 * u), QPointF(23 * u, 22 * u), QPointF(27 * u, 22 * u)])
    p.drawPolyline([QPointF(37 * u, 12 * u), QPointF(41 * u, 12 * u), QPointF(41 * u, 22 * u), QPointF(37 * u, 22 * u)])
    p.end()
    return pm


# ============================================================================ toggle switch

class ToggleSwitch(QAbstractButton):
    def __init__(self, parent=None, checked=False):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)
        self.colors = {"on": "#C81E3A", "off": "#3B3B41", "knob": "#FFFFFF", "knob_off": "#C9C9CF"}
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def sizeHint(self):
        return QSize(40, 22)

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

    def setChecked(self, on):   # keep knob position right when set programmatically
        super().setChecked(on)
        if hasattr(self, "_anim") and self._anim.state() != QPropertyAnimation.Running:
            self._pos = 1.0 if on else 0.0
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(1, 1, self.width() - 2, self.height() - 2)
        on_c, off_c = QColor(self.colors["on"]), QColor(self.colors["off"])
        t = self._pos
        track = QColor(
            int(off_c.red() + (on_c.red() - off_c.red()) * t),
            int(off_c.green() + (on_c.green() - off_c.green()) * t),
            int(off_c.blue() + (on_c.blue() - off_c.blue()) * t),
        )
        if not self.isEnabled():
            track.setAlpha(90)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.left() + 3 + (r.width() - 6 - d) * t
        knob = QColor(self.colors["knob"]) if t > 0.5 else QColor(self.colors["knob_off"])
        if not self.isEnabled():
            knob.setAlpha(150)
        p.setBrush(knob)
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))


class OptionRow(QFrame):
    """Title + description on the left, toggle on the right; the whole row is clickable."""
    toggled = Signal(bool)

    def __init__(self, title: str, desc: str = "", checked=False, parent=None):
        super().__init__(parent)
        self.setObjectName("OptRow")
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 6)
        lay.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.title = QLabel(title)
        self.title.setObjectName("OptTitle")
        col.addWidget(self.title)
        self.desc = QLabel(desc)
        self.desc.setObjectName("OptDesc")
        self.desc.setWordWrap(True)
        self.desc.setVisible(bool(desc))
        col.addWidget(self.desc)
        lay.addLayout(col, 1)
        self.switch = ToggleSwitch(checked=checked)
        self.switch.toggled.connect(self.toggled)
        lay.addWidget(self.switch, 0, Qt.AlignVCenter)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.isEnabled() and self.rect().contains(e.position().toPoint()):
            self.switch.toggle()
        super().mouseReleaseEvent(e)

    def isChecked(self):
        return self.switch.isChecked()

    def setChecked(self, v):
        self.switch.setChecked(v)

    def set_colors(self, colors: dict):
        self.switch.colors = colors
        self.switch.update()


class Segmented(QFrame):
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setObjectName("Segmented")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for key, label in options:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
            b.setProperty("key", key)
            b.clicked.connect(lambda _=False, b=b: self.changed.emit(b.property("key")))

    def resizeEvent(self, e):
        """Keep the pill shape at any size. Qt draws a corner radius larger than half the height as a square
        corner, so a fixed radius in the style sheet turns the selected segment into a rectangle on some
        fonts and DPI settings. Size both radii from the real heights instead."""
        super().resizeEvent(e)
        hb = min((b.height() for b in self.buttons.values()), default=0)
        if hb <= 0:
            return
        key = (self.height(), hb)
        if getattr(self, "_rkey", None) == key:
            return
        self._rkey = key
        self.setStyleSheet(f"#Segmented {{ border-radius: {max(4, self.height() // 2 - 1)}px; }}"
                           f"#Segmented QPushButton {{ border-radius: {max(4, hb // 2 - 1)}px; }}")

    def set_options(self, options: list[tuple[str, str]]):
        """Relabel in place (same number of options)."""
        btns = list(self.buttons.values())
        self.buttons = {}
        for b, (key, label) in zip(btns, options):
            b.setText(label)
            b.setProperty("key", key)
            self.buttons[key] = b

    def value(self) -> str:
        for k, b in self.buttons.items():
            if b.isChecked():
                return k
        return next(iter(self.buttons))

    def set_value(self, key: str):
        if key in self.buttons:
            self.buttons[key].setChecked(True)


def badge(text: str, kind: str) -> QLabel:
    b = QLabel(text)
    b.setObjectName("Badge")
    b.setProperty("kind", kind)
    b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return b


def card(title: str | None = None, hint: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("Card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(18, 16, 18, 16)
    lay.setSpacing(10)
    if title:
        head = QHBoxLayout()
        t = QLabel(title)
        t.setObjectName("CardTitle")
        head.addWidget(t)
        head.addStretch(1)
        lay.addLayout(head)
        f.head = head
        if hint:
            h = QLabel(hint)
            h.setObjectName("CardHint")
            h.setWordWrap(True)
            lay.addWidget(h)
    return f, lay


# ============================================================================ track card

class RadioDot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.checked = False
        self.colors = {"on": "#C81E3A", "off": "#4E4E55"}

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(self.colors["on" if self.checked else "off"])
        if not self.isEnabled():
            c.setAlpha(80)
        p.setPen(QPen(c, 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(1.5, 1.5, 15, 15))
        if self.checked:
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawEllipse(QRectF(5, 5, 8, 8))


class TrackCard(QFrame):
    clicked = Signal(int)

    def __init__(self, track, parent=None):
        super().__init__(parent)
        self.track = track
        self.setObjectName("Track")
        self.setProperty("selected", False)
        self.setProperty("enabledTrack", track.text_based)
        self.setCursor(Qt.PointingHandCursor if track.text_based else Qt.ArrowCursor)
        g = QGridLayout(self)
        g.setContentsMargins(12, 11, 12, 11)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(4)
        self.dot = RadioDot()
        self.dot.setEnabled(track.text_based)
        g.addWidget(self.dot, 0, 0, Qt.AlignTop)

        title = track.title or track.lang_label
        if getattr(track, "external", ""):
            import os
            parts = os.path.basename(track.external).split(".")
            tail = [parts.pop()]
            while len(parts) > 1 and len(parts[-1]) <= 7 and parts[-1].replace("-", "").isalnum():
                tail.insert(0, parts.pop())
            title = "Subtitle file  ." + ".".join(tail)
            self.setToolTip(track.external)
        self.title = QLabel(title)
        self.title.setObjectName("TrackTitle")
        self.title.setProperty("muted", not track.text_based)
        self.title.setTextInteractionFlags(Qt.NoTextInteraction)
        g.addWidget(self.title, 0, 1)

        self.codec = badge(track.codec_label, "codec")
        g.addWidget(self.codec, 0, 2, Qt.AlignRight | Qt.AlignTop)

        meta_bits = []
        if getattr(track, "external", ""):
            meta_bits += [f"{track.lang_label}, next to the video"]
        else:
            if track.title and track.title.lower() != track.lang_label.lower():
                meta_bits.append(track.lang_label)
            meta_bits.append(f"track {track.sub_pos + 1}")
        meta = ", ".join(meta_bits)
        self.meta = QLabel(meta[:1].upper() + meta[1:])
        self.meta.setObjectName("TrackMeta")
        g.addWidget(self.meta, 1, 1, 1, 2)

        row = QHBoxLayout()
        row.setSpacing(5)
        row.setContentsMargins(0, 3, 0, 0)
        self.badges = row
        if track.default:
            row.addWidget(badge("Default", "default"))
        if track.forced:
            row.addWidget(badge("Forced", "forced"))
        self.sdh_badge = badge("SDH", "sdh")
        self.sdh_badge.setVisible(track.sdh_flagged)
        row.addWidget(self.sdh_badge)
        if not track.text_based:
            row.addWidget(badge("Picture", "image"))
        row.addStretch(1)
        self.stat = QLabel("")
        self.stat.setObjectName("TrackStat")
        row.addWidget(self.stat)
        g.addLayout(row, 2, 1, 1, 2)
        g.setColumnStretch(1, 1)
        if not track.text_based:
            self.set_stat("Can't edit picture subtitles", "")
            self.setToolTip("This track is made of images (Blu-ray PGS / DVD VobSub), not text.\n"
                            "It would need OCR before it can be cleaned.")

    def set_selected(self, on: bool):
        self.dot.checked = on
        self.dot.update()
        set_prop(self, "selected", on)

    def set_stat(self, text: str, tone: str = ""):
        self.stat.setText(text)
        set_prop(self.stat, "tone", tone)

    def mark_sdh(self, on: bool):
        self.sdh_badge.setVisible(on or self.track.sdh_flagged)

    def set_colors(self, on: str, off: str):
        self.dot.colors = {"on": on, "off": off}
        self.dot.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.track.text_based and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.track.index)
        super().mouseReleaseEvent(e)


# ============================================================================ file queue row

class QueueRow(QFrame):
    clicked = Signal(int)
    remove_clicked = Signal(int)

    def __init__(self, uid: int, name: str, is_sub: bool, parent=None):
        super().__init__(parent)
        self.uid = uid
        self.full_name = name
        self.is_sub = is_sub
        self.setObjectName("QRow")
        self.setProperty("selected", False)
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 6, 8)
        lay.setSpacing(10)
        self.icon = QLabel()
        self.icon.setFixedSize(30, 30)
        self.icon.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.icon)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.name = QLabel(name)
        self.name.setObjectName("TrackTitle")
        self.name.setMinimumWidth(40)
        self.name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.status = QLabel("Waiting…")
        self.status.setObjectName("TrackStat")
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        col.addWidget(self.name)
        col.addWidget(self.status)
        lay.addLayout(col, 1)
        from PySide6.QtWidgets import QToolButton
        self.remove = QToolButton()
        self.remove.setObjectName("RowX")
        self.remove.setToolTip("Remove from the list")
        self.remove.setCursor(Qt.PointingHandCursor)
        self.remove.setFixedSize(26, 26)
        self.remove.clicked.connect(lambda: self.remove_clicked.emit(self.uid))
        lay.addWidget(self.remove, 0, Qt.AlignVCenter)
        self.setToolTip(name)

    def set_colors(self, accent: str, soft: str, dim: str):
        self.icon.setPixmap(draw_icon("sub" if self.is_sub else "film", accent, 18))
        self.icon.setStyleSheet(f"background: {soft}; border-radius: 8px;")
        self.remove.setIcon(icon("close", dim, 16))
        self.remove.setIconSize(QSize(12, 12))

    def set_status(self, text: str, tone: str = ""):
        self._status_text = text
        self._elide()
        set_prop(self.status, "tone", tone)

    def set_selected(self, on: bool):
        set_prop(self, "selected", on)

    def _elide(self):
        w = max(40, self.name.width())
        self.name.setText(self.name.fontMetrics().elidedText(self.full_name, Qt.ElideMiddle, w))
        st = getattr(self, "_status_text", "")
        self.status.setText(self.status.fontMetrics().elidedText(st, Qt.ElideRight, max(40, self.status.width())))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.uid)
        super().mouseReleaseEvent(e)


# ============================================================================ rule row (toggle + count)

class RuleRow(OptionRow):
    """An edit type in Review: title, example, how many lines it touches in this file, and a toggle."""

    def __init__(self, title: str, desc: str = "", checked=False, parent=None):
        super().__init__(title, desc, checked, parent)
        self.count = QLabel("")
        self.count.setObjectName("RuleCount")
        self.count.setFont(num_font(self.count.font()))
        self.count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.count.setMinimumWidth(34)
        self.layout().insertWidget(1, self.count, 0, Qt.AlignVCenter)

    def set_count(self, n: int | None):
        self.count.setText("" if n is None else f"{n:,}")
        set_prop(self.count, "zero", not n)


# ============================================================================ status tag

from .theme import num_font, qc  # noqa: E402


class StatusTag(QWidget):
    """A badge pill: ok (green), warn (amber) or neutral; red-tinted accent when something needs you."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text, self.kind, self.t = "", "", {}
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set(self, text: str, kind: str, t: dict):
        self.text, self.kind, self.t = text, kind, t
        self.setVisible(bool(text))
        self.updateGeometry()
        self.update()

    def _font(self):
        f = QFont(self.font())
        f.setPointSizeF(max(8.5, f.pointSizeF() - 1.0)) if f.pointSizeF() > 0 else None
        f.setWeight(QFont.DemiBold)
        return f

    def sizeHint(self):
        fm = QFontMetrics(self._font())
        return QSize(fm.horizontalAdvance(self.text) + 22 + (14 if self.kind in ("ready", "done", "check", "low", "failed", "flag") else 0),
                     max(22, fm.height() + 6))

    def paintEvent(self, _):
        if not self.t or not self.text:
            return
        t = self.t
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        alert = self.kind in ("check", "low", "failed", "flag")
        if alert:
            fg = t["bad"] if not t.get("is_dark") else t["accent_ink"]
            bg = t["bad_bg"]
        elif self.kind in ("ready", "done"):
            fg, bg = t["ok"], t["ok_bg"]
        elif self.kind == "busy":
            fg, bg = t["warn"], t["warn_bg"]
        else:
            fg, bg = t["faint"], t["hover"]
        hint = self.sizeHint()                       # hug the text, even when the slot is wider
        w = min(self.width(), hint.width())
        h = min(self.height(), hint.height())
        r = QRectF(0.5, (self.height() - h) / 2 + 0.5, w - 1, h - 1)
        p.setPen(Qt.NoPen)
        p.setBrush(qc(bg))
        p.drawRoundedRect(r, h / 2, h / 2)
        x = 8.0
        mark = {"ready": "check", "done": "check", "check": "alert", "low": "alert", "failed": "alert", "flag": "flag"}.get(self.kind)
        if mark:
            dpr = self.devicePixelRatioF()
            p.drawPixmap(QPointF(x, (self.height() - 12) / 2), draw_icon(mark, fg, 12, dpr))
            x += 16
        p.setFont(self._font())
        p.setPen(qc(fg))
        p.drawText(QRectF(x, 0, w - x - 8, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text)


class Legend(QWidget):
    """Line counts, each with a swatch in the colour those lines have on the timeline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[tuple[str, str, bool]] = []          # (colour, text, emphasise)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_items(self, items: list[tuple[str, str, bool]]):
        self.items = items
        self.setToolTip("")
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        fm = self.fontMetrics()
        w = sum(fm.horizontalAdvance(t) + 36 for _, t, _ in self.items)
        return QSize(w, max(24, fm.height() + 6))

    def minimumSizeHint(self):
        return QSize(40, max(24, self.fontMetrics().height() + 6))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        x, cy = 0.0, self.height() / 2
        hidden = []
        for color, text, strong in self.items:
            f = QFont(self.font())
            f.setBold(strong)
            fm = QFontMetrics(f)
            w = 15 + fm.horizontalAdvance(text)
            if x + w > self.width():
                hidden.append(text)
                continue
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(x, cy - 4.5, 9, 9), 2.5, 2.5)
            p.setFont(f)
            p.setPen(QColor(color) if strong else self.palette().windowText().color())
            p.drawText(QRectF(x + 15, 0, w, self.height()), Qt.AlignVCenter | Qt.AlignLeft, text)
            x += w + 20
        p.end()
        if hidden:
            self.setToolTip("\n".join(hidden))


# ============================================================================ file row (main list)

class FileRow(QFrame):
    """One file in the main list: name and track on the left, line count and status on the right."""
    clicked = Signal(int)
    remove_clicked = Signal(int)

    def __init__(self, uid: int, name: str, is_sub: bool, parent=None):
        super().__init__(parent)
        self.uid = uid
        self.full_name = name
        self.is_sub = is_sub
        self.t: dict = {}
        self.setObjectName("FileRow")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(68)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 10, 12)
        lay.setSpacing(14)
        self.edge = QFrame()
        self.edge.setFixedWidth(3)
        lay.addWidget(self.edge)
        self.icon = QLabel()
        self.icon.setFixedSize(22, 22)
        self.icon.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.icon, 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(3)
        self.name = QLabel(name)
        self.name.setObjectName("FileName")
        self.name.setMinimumWidth(60)
        self.name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.detail = QLabel("Waiting")
        self.detail.setObjectName("FileDetail")
        self.detail.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        col.addWidget(self.name)
        self.rename = QLabel("")
        self.rename.setObjectName("FileRename")
        self.rename.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.rename.hide()
        col.addWidget(self.rename)
        col.addWidget(self.detail)
        lay.addLayout(col, 1)
        from PySide6.QtWidgets import QProgressBar, QToolButton
        self.bar = QProgressBar()
        self.bar.setObjectName("RowBar")
        self.bar.setTextVisible(False)
        self.bar.setFixedWidth(110)
        self.bar.setRange(0, 1000)
        self.bar.hide()
        lay.addWidget(self.bar, 0, Qt.AlignVCenter)
        self.count = QLabel("")
        self.count.setObjectName("FileCount")
        self.count.setFont(num_font(self.count.font()))
        self.count.setFixedWidth(96)
        self.count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.count, 0, Qt.AlignVCenter)
        self.status = StatusTag()
        self.status.setFixedWidth(150)
        lay.addWidget(self.status, 0, Qt.AlignVCenter)
        self.review = QPushButton("Review")
        self.review.setObjectName("RowButton")
        self.review.setCursor(Qt.PointingHandCursor)
        self.review.clicked.connect(lambda: self.clicked.emit(self.uid))
        lay.addWidget(self.review, 0, Qt.AlignVCenter)
        self.remove = QToolButton()
        self.remove.setObjectName("RowX")
        self.remove.setToolTip("Remove from the list")
        self.remove.setCursor(Qt.PointingHandCursor)
        self.remove.setFixedSize(28, 28)
        self.remove.clicked.connect(lambda: self.remove_clicked.emit(self.uid))
        lay.addWidget(self.remove, 0, Qt.AlignVCenter)
        self._kind = ""

    def set_theme(self, t: dict):
        self.t = t
        self.icon.setPixmap(draw_icon("sub" if self.is_sub else "film", t["dim"], 20))
        self.remove.setIcon(icon("close", t["faint"], 16))
        self.remove.setIconSize(QSize(12, 12))
        self._paint_edge()

    def set_colors(self, fg: str, soft: str, dim: str):       # kept for older callers
        self.icon.setPixmap(draw_icon("sub" if self.is_sub else "film", fg, 20))
        self.remove.setIcon(icon("close", dim, 16))
        self.remove.setIconSize(QSize(12, 12))

    def _paint_edge(self):
        alert = self._kind in ("check", "low", "failed")
        col = self.t.get("accent", "#C81E3A") if alert else "transparent"
        self.edge.setStyleSheet(f"background: {col}; border-radius: 1px;")

    def set_state(self, detail: str, chip: str = "", kind: str = "", progress: float | None = None,
                  review: bool = True, count: str = ""):
        self._detail = detail
        self._kind = kind
        self._elide()
        self.status.set(chip, kind, self.t)
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
        self._paint_edge()

    def set_busy(self, on: bool):
        self.remove.setEnabled(not on)

    def set_rename(self, text: str, tone: str = ""):
        """The line under the name: the new file name, or where the title lookup stands."""
        self._rename = text
        set_prop(self.rename, "tone", tone)
        self.rename.setVisible(bool(text))
        self._elide()

    def set_name(self, name: str):
        self.full_name = name
        self._elide()

    def _elide(self):
        w = max(60, self.name.width())
        self.name.setText(self.name.fontMetrics().elidedText(self.full_name, Qt.ElideMiddle, w))
        r = getattr(self, "_rename", "")
        self.rename.setText(self.rename.fontMetrics().elidedText(r, Qt.ElideMiddle, max(60, self.rename.width())))
        d = getattr(self, "_detail", "")
        self.detail.setText(self.detail.fontMetrics().elidedText(d, Qt.ElideRight, max(60, self.detail.width())))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.uid)
        super().mouseReleaseEvent(e)


# ============================================================================ the screen

class ScreenView(QWidget):
    """An always-dark strip that shows a subtitle line the way it will look on the TV:
    white caption text with a soft outline, the timecode in the corner."""

    def __init__(self, parent=None, height: int = 150):
        super().__init__(parent)
        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(height)
        self.t: dict = {}
        self.caption = ""
        self.corner_left = ""
        self.corner_right = ""
        self.right_alert = False
        self.italic = False
        self.placeholder = ""
        # welcome demo: a struck-out sound tag fading away in front of the dialogue
        self.demo: tuple[str, str] | None = None
        self._demo_t = 0.0

    def set_theme(self, t: dict):
        self.t = t
        self.update()

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
        if not self.t:
            return
        t = self.t
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QPen(QColor(t["screen_line"]), 1))
        p.setBrush(QColor(t["screen"]))
        p.drawRoundedRect(r, 10, 10)

        small = QFont(self.font())
        small.setPointSizeF(max(8.0, small.pointSizeF() - 1.5))
        small.setBold(True)
        p.setFont(small)
        pad = 16
        if self.corner_left:
            p.setPen(QColor(255, 255, 255, 120))
            p.drawText(QRectF(pad, 10, self.width() / 2, 22), Qt.AlignLeft | Qt.AlignVCenter, self.corner_left)
        if self.corner_right:
            fm = QFontMetrics(small)
            txt = fm.elidedText(self.corner_right, Qt.ElideRight, int(self.width() * 0.6))
            p.setPen(QColor(t["accent_ink"]) if self.right_alert else QColor(255, 255, 255, 120))
            p.drawText(QRectF(self.width() / 2 - pad, 10, self.width() / 2, 22), Qt.AlignRight | Qt.AlignVCenter, txt)

        cap = QFont(self.font())
        cap.setPointSizeF(cap.pointSizeF() + 6)
        cap.setBold(True)
        cap.setItalic(self.italic)
        fm = QFontMetrics(cap)
        if self.demo:
            self._paint_demo(p, cap, fm)
            p.end()
            return
        text = self.caption
        if not text:
            if self.placeholder:
                p.setFont(small)
                p.setPen(QColor(255, 255, 255, 90))
                p.drawText(QRectF(0, 0, self.width(), self.height()), Qt.AlignCenter, self.placeholder)
            p.end()
            return
        lines = text.split("\n")[:3]
        lh = fm.height() + 2
        y0 = self.height() - 18 - lh * len(lines)
        for i, line in enumerate(lines):
            line = fm.elidedText(line, Qt.ElideRight, self.width() - 2 * pad)
            w = fm.horizontalAdvance(line)
            x = (self.width() - w) / 2
            self._outlined(p, cap, x, y0 + i * lh + fm.ascent(), line)
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
        tw, lw = fm.horizontalAdvance(tag), fm.horizontalAdvance(line)
        fade = max(0.0, min(1.0, k - 1.0))
        total = (tw + gap) * (1 - fade) + lw
        x = (self.width() - total) / 2
        y = self.height() - 22 - fm.descent()
        if fade < 1.0:
            self._outlined(p, font, x - (tw + gap) * fade, y, tag, int(255 * (1 - fade) * 0.55))
            strike = min(1.0, k)
            if strike > 0:
                p.setPen(QPen(QColor(self.t["accent_ink"]), 3, Qt.SolidLine, Qt.RoundCap))
                sy = y - fm.ascent() * 0.33
                left = x - (tw + gap) * fade
                p.setOpacity(1 - fade)
                p.drawLine(QPointF(left - 2, sy), QPointF(left + (tw + 2) * strike, sy))
                p.setOpacity(1.0)
        self._outlined(p, font, x + (tw + gap) * (1 - fade), y, line)


def two_line_button(title: str, desc: str, name: str) -> QPushButton:
    """A big choice button: a semibold title over a quieter description (a plain button can't style two lines)."""
    b = QPushButton()
    b.setObjectName(name)
    lay = QVBoxLayout(b)
    lay.setContentsMargins(16, 11, 16, 11)
    lay.setSpacing(2)
    for text, obj in ((title, "ChoiceTitle"), (desc, "ChoiceDesc")):
        lab = QLabel(text)
        lab.setObjectName(obj)
        lab.setWordWrap(True)
        lab.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(lab)
    b.setMinimumHeight(lay.sizeHint().height())
    b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    return b
