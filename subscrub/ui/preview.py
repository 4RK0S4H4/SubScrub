"""Before/after preview: the caption "screen", a timeline with removal markers, and a side-by-side list of every
changed line. Judgement calls are flagged with the reason, and any edit can be undone line by line.

Performance: cue times and visible text are worked out once per track, row heights and laid-out text are cached by
content (so rebuilding the list after a click reuses them), the timeline's marks are painted once into a cached
layer, and putting one line back updates just that row."""
from __future__ import annotations

import bisect
import html
import re
from collections import OrderedDict
from dataclasses import dataclass

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap, QTextDocument
from PySide6.QtWidgets import (QAbstractItemView, QListView, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from ..cleaner import CleanResult, diff_html, visible_text
from ..subformats import to_seconds
from . import theme
from .theme import num_font, qc
from .widgets import T, ScreenView, hide_tip, show_tip


# ============================================================================ data

parse_time = to_seconds


def fmt_clock(sec: float) -> str:
    sec = max(0, int(sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@dataclass
class Row:
    cue: int
    start: float
    end: float
    status: str          # removed | edited | kept
    before: str          # visible text
    after: str | None    # what will be saved (None = line removed)
    flagged: bool = False
    why: str = ""
    override: str | None = None    # "orig" when the user kept the original line
    italic: bool = False           # the saved line is in italics (lyrics, quoted voices)

    @property
    def key(self) -> tuple:
        return (self.cue, self.status, self.before, self.after, self.flagged, self.why, self.override, self.italic)


@dataclass
class Mark:
    start: float
    end: float
    status: str          # kept | edited | removed | flagged
    text: str


def _prep(result: CleanResult) -> tuple[list[tuple[float, float]], list[str]]:
    """Cue times and visible text, once per track (they don't change when edits are switched on and off)."""
    doc = result.doc
    cached = getattr(doc, "_preview_prep", None)
    if cached is not None:
        return cached
    times, befores = [], []
    for cue in doc.cues:
        s, e = parse_time(cue.start), parse_time(cue.end)
        times.append((s, max(e, s)))
        befores.append(visible_text(cue.text, doc.fmt))
    doc._preview_prep = (times, befores)
    return doc._preview_prep


def row_for(result: CleanResult, i: int) -> tuple[Row, Mark]:
    times, befores = _prep(result)
    fmt = result.doc.fmt
    cue, r = result.doc.cues[i], result.results[i]
    start, end = times[i]
    before = befores[i]
    status = "kept" if not r.changed else ("removed" if r.text is None else "edited")
    override = result.overrides.get(i)
    flagged = r.changed and r.risk >= 2
    if not r.changed or override == "orig":
        after = before
        final = cue.text
    else:
        after = None if r.text is None else visible_text(r.text, fmt)
        final = r.text or ""
    mark = "flagged" if flagged and override is None else ("kept" if override == "orig" else status)
    row = Row(i, start, end, status, before, after, flagged, "; ".join(r.why[:2]) if r.changed else "",
              override, bool(re.search(r"<i>|\\i1", final or "", re.I)))
    return row, Mark(start, end, mark, before)


def build(result: CleanResult, show: str) -> tuple[list[Row], list[Mark]]:
    """show: 'check' (judgement calls only) | 'changes' | 'all'"""
    rows, marks = [], []
    for i in range(len(result.results)):
        row, mark = row_for(result, i)
        marks.append(mark)
        if show == "changes" and row.status == "kept":
            continue
        if show == "check" and not row.flagged:
            continue
        rows.append(row)
    rows.sort(key=lambda x: x.start)
    marks.sort(key=lambda m: m.start)
    return rows, marks


def _blend(fg: str, bg: str, a: float) -> str:
    f, b = qc(fg), qc(bg)
    return QColor(round(f.red() * a + b.red() * (1 - a)), round(f.green() * a + b.green() * (1 - a)),
                  round(f.blue() * a + b.blue() * (1 - a))).name()


class PreviewModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.rows: list[Row] = []

    def set_rows(self, rows: list[Row]):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def replace(self, i: int, row: Row):
        self.rows[i] = row
        idx = self.index(i)
        self.dataChanged.emit(idx, idx)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role == Qt.UserRole:
            return row
        if role == Qt.DisplayRole:
            return row.before
        return None


# ============================================================================ list + delegate

PAD_L, TIME_W, GAP, PAD_R, VPAD, FOOT_H = 18, 58, 18, 16, 10, 34
BTN_W = 116


def columns(width: int) -> tuple[int, int, int, int]:
    """(x_time, x_before, x_after, col_w)"""
    x_before = PAD_L + TIME_W + 4
    col_w = max(80, (width - x_before - PAD_R - GAP) // 2)
    return PAD_L, x_before, x_before + col_w + GAP, col_w


class _LRU(OrderedDict):
    def __init__(self, cap: int):
        super().__init__()
        self.cap = cap

    def get_(self, k):
        v = self.get(k)
        if v is not None:
            self.move_to_end(k)
        return v

    def put(self, k, v):
        self[k] = v
        self.move_to_end(k)
        if len(self) > self.cap:
            self.popitem(last=False)


class RowDelegate(QStyledItemDelegate):
    def __init__(self, view: "PreviewList"):
        super().__init__(view)
        self.view = view
        self._h = _LRU(20000)           # (row key, width) -> height
        self._docs = _LRU(1200)         # (row key, side, width, theme) -> laid-out QTextDocument
        self._html = _LRU(6000)         # (row key, theme) -> (before html, after html)
        self._tkey = None

    def set_theme(self, *_):
        self.clear(all_=True)

    def clear(self, all_: bool = False):
        """Width or theme changed: heights and layouts go. (A new result keeps them: they are keyed by content.)"""
        if all_:
            self._h.clear()
            self._html.clear()
        self._docs.clear()

    def _theme_key(self):
        t = T()
        return (t.get("is_dark"), t.get("text"))

    def _row_html(self, row: Row) -> tuple[str, str]:
        key = (row.key, self._theme_key())
        hit = self._html.get_(key)
        if hit is not None:
            return hit
        t = T()
        text_c = qc(t["text"] if row.status != "kept" else t["text2"]).name()
        before_c = qc(t["text2"] if row.status != "kept" else t["text3"]).name()
        rm_c = qc(t["removed"]).name()
        rm_bg = _blend(t["removed"], t["bg"], 0.13)
        undone = row.override == "orig"
        before = diff_html(row.before, row.after if row.status != "kept" and not undone else row.before)
        before = before.replace(
            "<del>", f'<span style="color:{rm_c}; background-color:{rm_bg}; text-decoration: line-through;">'
        ).replace("</del>", "</span>").replace("\n", "<br>")
        before = f'<div style="color:{before_c}">{before}</div>'
        if row.after is None:
            after = f'<div style="color:{qc(t["text3"]).name()}; font-style: italic;">Removed</div>'
        else:
            body = html.escape(row.after).replace(chr(10), "<br>")
            if row.italic:
                body = f"<i>{body}</i>"
            after = f'<div style="color:{text_c}">{body}</div>'
        self._html.put(key, (before, after))
        return before, after

    def has_footer(self, row: Row) -> bool:
        return row.status != "kept" and (row.flagged or row.override == "orig")

    def _doc(self, row: Row, side: int, width: int, font: QFont) -> QTextDocument:
        key = (row.key, side, width, self._theme_key())
        d = self._docs.get_(key)
        if d is None:
            d = QTextDocument()
            d.setDefaultFont(font)
            d.setDocumentMargin(0)
            d.setHtml(self._row_html(row)[side])
            d.setTextWidth(width)
            self._docs.put(key, d)
        return d

    def _text_h(self, text: str, width: int, font: QFont, italic: bool = False) -> int:
        """Height of wrapped plain text. Font metrics are far cheaper than laying out a QTextDocument, and the row
        HTML only changes colours (and italics, measured with an italic font)."""
        if italic:
            font = QFont(font)
            font.setItalic(True)
        fm = QFontMetrics(font)
        lines = 0
        for part in (text or " ").split("\n"):
            r = fm.boundingRect(0, 0, max(10, width), 100000, Qt.TextWordWrap, part or " ")
            lines += max(1, round(r.height() / fm.lineSpacing()))
        return lines * fm.lineSpacing()

    def sizeHint(self, option, index):
        row: Row = index.data(Qt.UserRole)
        width = self.view.viewport().width()
        key = (row.key, width)
        h = self._h.get_(key)
        if h is None:
            _, _, _, col_w = columns(width)
            after = "Removed" if row.after is None else row.after
            h = max(self._text_h(row.before, col_w, option.font),
                    self._text_h(after, col_w, option.font, row.italic or row.after is None)) + VPAD * 2 + 2
            if self.has_footer(row):
                h += FOOT_H
            self._h.put(key, h)
        return QSize(width, h)

    def button_rect(self, rect, row: Row) -> QRectF | None:
        """The 'Keep Original' / 'Use Cleaned' button in a row's footer."""
        if not self.has_footer(row):
            return None
        x_time, x_before, x_after, col_w = columns(rect.width())
        return QRectF(rect.left() + x_after + col_w - BTN_W, rect.bottom() - FOOT_H + 1, BTN_W, 26)

    def paint(self, p: QPainter, option, index):
        row: Row = index.data(Qt.UserRole)
        t = T()
        r = option.rect
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        box = QRectF(r).adjusted(4, 1, -4, -1)
        if selected or hover:                      # rows and selections stay neutral grey (S4 Glass)
            p.setPen(Qt.NoPen)
            p.setBrush(qc(t["bg_sel"] if selected else t["bg_hover"]))
            p.drawRoundedRect(box, 8, 8)
        # status: a slim bar at the row's left edge, never a tinted row
        if row.override == "orig":
            edge = t["mute_line"]
        elif row.flagged:
            edge = t["btn"]
        else:
            edge = {"removed": t["removed"], "edited": t["trim"]}.get(row.status)
        if edge:
            p.setPen(Qt.NoPen)
            p.setBrush(qc(edge))
            p.drawRoundedRect(QRectF(r.left() + 8, r.top() + 10, 3, r.height() - 20), 1.5, 1.5)
        if index.row() > 0 and not selected and not hover:
            p.setPen(QPen(qc(t["border"]), 1))
            p.drawLine(QPointF(r.left() + PAD_L, r.top() + 0.5), QPointF(r.right() - PAD_R, r.top() + 0.5))

        x_time, x_before, x_after, col_w = columns(r.width())
        tf = num_font(theme.font(12.5, 600 if selected else 400))
        p.setFont(tf)
        p.setPen(qc(t["text"] if selected else t["text3"]))
        p.drawText(QRectF(r.left() + x_time, r.top() + VPAD + 1, TIME_W, 20), Qt.AlignLeft | Qt.AlignTop,
                   fmt_clock(row.start))
        for side, x in ((0, x_before), (1, x_after)):
            d = self._doc(row, side, col_w, option.font)
            p.save()
            p.translate(r.left() + x, r.top() + VPAD)
            d.drawContents(p)
            p.restore()
        br = self.button_rect(r, row)
        if br is not None:
            f = theme.font(12.5)
            fb = theme.font(12.5, 600)
            avail = br.left() - (r.left() + x_before) - 12
            ty = QRectF(r.left() + x_before, br.top(), avail, br.height())
            if row.override == "orig":
                p.setFont(f)
                p.setPen(qc(t["text2"]))
                p.drawText(ty, Qt.AlignLeft | Qt.AlignVCenter, "You kept the original line.")
            else:
                head = "Check: "
                p.setFont(fb)
                p.setPen(qc(t["text"]))
                p.drawText(ty, Qt.AlignLeft | Qt.AlignVCenter, head)
                hw = QFontMetrics(fb).horizontalAdvance(head)
                p.setFont(f)
                p.setPen(qc(t["text2"]))
                rest = QFontMetrics(f).elidedText(row.why or "judgement call", Qt.ElideRight, int(avail - hw))
                p.drawText(ty.adjusted(hw, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, rest)
            over = self.view.hover_button == row.cue
            p.setPen(QPen(qc(t["border2"]), 1))
            p.setBrush(qc(t["hov"] if over else t["bg2"]))
            p.drawRoundedRect(br.adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
            p.setFont(theme.font(12.5, 500))
            p.setPen(qc(t["text"]))
            p.drawText(br, Qt.AlignCenter, "Use Cleaned" if row.override == "orig" else "Keep Original")
        p.restore()


class PreviewList(QListView):
    toggle_requested = Signal(int)            # cue index

    def __init__(self):
        super().__init__()
        self.setObjectName("PreviewList")
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setResizeMode(QListView.Adjust)
        self.setUniformItemSizes(False)
        self.setLayoutMode(QListView.Batched)
        self.setBatchSize(200)
        self.setMouseTracking(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QListView.NoFrame)
        self.viewport().setAutoFillBackground(False)
        self.hover_button: int | None = None
        self.delegate = RowDelegate(self)
        self.setItemDelegate(self.delegate)
        self._w = 0

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.viewport().width() != self._w:
            self._w = self.viewport().width()
            self.delegate.clear()

    def _row_at(self, pos):
        idx = self.indexAt(pos)
        return (idx, idx.data(Qt.UserRole)) if idx.isValid() else (idx, None)

    def mouseReleaseEvent(self, e):
        idx, row = self._row_at(e.position().toPoint())
        if row is not None and e.button() == Qt.LeftButton:
            br = self.delegate.button_rect(self.visualRect(idx), row)
            if br is not None and br.contains(e.position()):
                self.toggle_requested.emit(row.cue)
                return
        super().mouseReleaseEvent(e)

    def mouseMoveEvent(self, e):
        idx, row = self._row_at(e.position().toPoint())
        over = row is not None and (br := self.delegate.button_rect(self.visualRect(idx), row)) is not None \
            and br.contains(e.position())
        cue = row.cue if over else None
        if cue != self.hover_button:
            self.hover_button = cue
            self.viewport().update()
        self.viewport().setCursor(Qt.PointingHandCursor if over else Qt.ArrowCursor)
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        if self.hover_button is not None:
            self.hover_button = None
            self.viewport().update()
        super().leaveEvent(e)

    def mouseDoubleClickEvent(self, e):
        idx, row = self._row_at(e.position().toPoint())
        if row is not None and row.status != "kept":
            self.toggle_requested.emit(row.cue)
            return
        super().mouseDoubleClickEvent(e)


# ============================================================================ timeline

class ScrubBar(QWidget):
    scrubbed = Signal(float)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(64)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.duration = 1.0
        self.marks: list[Mark] = []
        self.pos_sec = 0.0
        self.dragging = False
        self.hover_x: float | None = None
        self._layer: QPixmap | None = None
        self._layer_key = None
        self._tip = ""

    def set_theme(self, *_):
        self._layer = None
        self.update()

    def set_data(self, duration: float, marks: list[Mark]):
        last = max((m.end for m in marks), default=0.0)
        self.duration = max(duration or 0.0, last, 1.0)
        self.marks = marks
        self._layer = None
        self.update()

    def update_mark(self, start: float, status: str):
        """One line's status changed (kept or cleaned again)."""
        for m in self.marks:
            if m.start == start and m.status != status:
                m.status = status
                self._layer = None
                self.update()
                break

    def set_position(self, sec: float):
        if not self.dragging:
            self.pos_sec = sec
            self.update()

    # geometry
    def _track(self) -> QRectF:
        return QRectF(10, 12, self.width() - 20, 30)

    def _x(self, sec: float) -> float:
        tr = self._track()
        return tr.left() + tr.width() * min(1.0, max(0.0, sec / self.duration))

    def _sec(self, x: float) -> float:
        tr = self._track()
        return max(0.0, min(self.duration, (x - tr.left()) / max(1.0, tr.width()) * self.duration))

    def _marks_layer(self) -> QPixmap:
        t = T()
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr, t.get("is_dark"), t.get("text"))
        if self._layer is not None and self._layer_key == key:
            return self._layer
        pm = QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        tr = self._track()
        p.setPen(Qt.NoPen)
        p.setBrush(qc(t["mute_bg"]))
        p.drawRoundedRect(tr, 6, 6)
        mid = tr.center().y()
        heights = {"kept": 8, "edited": 18, "removed": 24, "flagged": 30}
        colors = {"kept": qc(t["mute_line"]), "edited": qc(t["trim"]), "removed": qc(t["removed"]),
                  "flagged": qc(t["btn"])}
        left, span = tr.left(), tr.width() / self.duration
        for layer in ("kept", "edited", "removed", "flagged"):          # changes drawn on top
            col = colors[layer]
            h = heights[layer]
            for m in self.marks:
                if m.status != layer:
                    continue
                x0 = left + min(self.duration, m.start) * span
                x1 = left + min(self.duration, m.end) * span
                p.fillRect(QRectF(x0, mid - h / 2, max(2.0, x1 - x0), h), col)
        p.end()
        self._layer, self._layer_key = pm, key
        return pm

    def paintEvent(self, _):
        t = T()
        if not t:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawPixmap(0, 0, self._marks_layer())
        tr = self._track()
        if self.hover_x is not None and not self.dragging:
            c = qc(t["text"])
            c.setAlpha(70)
            p.setPen(QPen(c, 1))
            p.drawLine(QPointF(self.hover_x, tr.top() - 4), QPointF(self.hover_x, tr.bottom() + 4))
        x = self._x(self.pos_sec)
        p.setPen(QPen(qc(t["btn"]), 2))
        p.drawLine(QPointF(x, tr.top() - 4), QPointF(x, tr.bottom() + 4))
        p.setPen(QPen(qc(t["pop_bg"]), 2))
        p.setBrush(qc(t["btn"]))
        p.drawEllipse(QRectF(x - 6, tr.top() - 10, 12, 12))
        f = num_font(theme.font(11.5))
        p.setFont(f)
        p.setPen(qc(t["text3"]))
        ly = tr.bottom() + 5
        p.drawText(QRectF(tr.left(), ly, 80, 16), Qt.AlignLeft, "0:00")
        p.drawText(QRectF(tr.right() - 80, ly, 80, 16), Qt.AlignRight, fmt_clock(self.duration))
        label = fmt_clock(self.pos_sec)
        fb = num_font(theme.font(11.5, 600))
        lw = QFontMetrics(fb).horizontalAdvance(label) + 14
        lx = min(max(tr.left() + 44, x - lw / 2), tr.right() - 44 - lw)
        p.setFont(fb)
        p.setPen(qc(t["text"]))
        p.drawText(QRectF(lx, ly, lw, 16), Qt.AlignCenter, label)
        p.end()

    def _nearest_change(self, x: float, tol_px=5.0) -> Mark | None:
        best, best_d = None, tol_px
        for m in self.marks:
            if m.status == "kept":
                continue
            x0, x1 = self._x(m.start), max(self._x(m.end), self._x(m.start) + 2)
            d = 0 if x0 <= x <= x1 else min(abs(x - x0), abs(x - x1))
            if d <= best_d:
                best, best_d = m, d
        return best

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.dragging = True
            hide_tip()
            self._emit(e.position().x())

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self.dragging:
            self._emit(x)
            return
        self.hover_x = x
        m = self._nearest_change(x)
        if m:
            what = {"removed": "Removed", "flagged": "Check this one"}.get(m.status, "Trimmed")
            text = html.escape(m.text.replace("\n", " ")[:90])
            tip = f"<b>{fmt_clock(m.start)}</b>&nbsp;&nbsp;{what}<br>{text}"
        else:
            tip = fmt_clock(self._sec(x))
        if tip != self._tip:
            self._tip = tip
            show_tip(e.globalPosition().toPoint(), tip, self)
        self.update()

    def mouseReleaseEvent(self, e):
        if self.dragging:
            self.dragging = False
            self._emit(e.position().x())

    def leaveEvent(self, e):
        self.hover_x = None
        self._tip = ""
        hide_tip()
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layer = None

    def _emit(self, x):
        self.pos_sec = self._sec(x)
        self.update()
        self.scrubbed.emit(self.pos_sec)


# ============================================================================ panel

class ColumnHeader(QWidget):
    def __init__(self, view: PreviewList):
        super().__init__()
        self.view = view
        self.setFixedHeight(24)

    def paintEvent(self, _):
        t = T()
        p = QPainter(self)
        p.setFont(theme.font(12, 600))
        p.setPen(qc(t["text2"]))
        x_time, x_before, x_after, col_w = columns(self.view.viewport().width())
        for x, s in ((x_time, "Time"), (x_before, "Before"), (x_after, "After, as Saved")):
            p.drawText(QRectF(x, 0, col_w, self.height()), Qt.AlignLeft | Qt.AlignVCenter, s)
        p.end()


class PreviewPanel(QWidget):
    position_changed = Signal()

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.screen = ScreenView(height=150)
        lay.addWidget(self.screen)
        self.scrub = ScrubBar()
        lay.addWidget(self.scrub)
        self.list = PreviewList()
        self.model = PreviewModel()
        self.list.setModel(self.model)
        self.header = ColumnHeader(self.list)
        lay.addWidget(self.header)
        lay.addWidget(self.list, 1)
        self.rows: list[Row] = []
        self._starts: list[float] = []
        self._result: CleanResult | None = None
        self._syncing = False
        self.scrub.scrubbed.connect(self._on_scrub)
        self.list.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.list.selectionModel().currentChanged.connect(self._on_current)
        self.position_changed.connect(self._update_screen)

    def set_theme(self, *_):
        self.list.delegate.set_theme()
        self.scrub.set_theme()
        self.screen.update()
        self.header.update()
        self.list.doItemsLayout()
        self.list.viewport().update()

    def set_result(self, result: CleanResult, show: str, duration: float, keep_position: bool = True):
        keep_cue = None
        cur = self.list.currentIndex()
        if keep_position and cur.isValid() and cur.row() < len(self.rows):
            keep_cue = self.rows[cur.row()].start
        rows, marks = build(result, show)
        self._result = result
        self.rows = rows
        self._starts = [r.start for r in rows]
        self.model.set_rows(rows)
        self.scrub.set_data(duration, marks)
        if rows:
            i = self._row_at(keep_cue) if keep_cue is not None else 0
            self._select(i, top=keep_cue is None)
        else:
            self.scrub.set_position(0)
        self.position_changed.emit()

    def update_cue(self, result: CleanResult, cue: int) -> bool:
        """One line was kept or cleaned again: update just its row and mark. False if a full rebuild is needed."""
        if result is not self._result:
            return False
        k = next((j for j, r in enumerate(self.rows) if r.cue == cue), None)
        row, mark = row_for(result, cue)
        self.scrub.update_mark(mark.start, mark.status)
        if k is None:
            return True
        old_h = self.list.delegate.sizeHint(_opt(self.list), self.model.index(k)).height()
        self.rows[k] = row
        self.model.replace(k, row)
        new_h = self.list.delegate.sizeHint(_opt(self.list), self.model.index(k)).height()
        if new_h != old_h:
            self.list.doItemsLayout()
        self.position_changed.emit()
        return True

    def _update_screen(self):
        row = self.current_row()
        if row is None:
            self.screen.show_line("", placeholder="Pick a line below to see it as it will appear")
            return
        when = fmt_clock(row.start)
        if row.override == "orig":
            right, alert = "You kept the original", False
        elif row.flagged:
            right, alert = "Check: " + (row.why or "judgement call"), True
        else:
            right, alert = {"removed": "Removed", "edited": "Cleaned", "kept": "Unchanged"}[row.status], False
        if row.after is None:
            self.screen.show_line("", when, right, alert, placeholder="Nothing on screen: this whole line is removed")
        else:
            self.screen.show_line(row.after, when, right, alert, row.italic)

    def changed_rows(self) -> list[int]:
        return [i for i, r in enumerate(self.rows) if r.status != "kept"]

    def current_row(self) -> Row | None:
        cur = self.list.currentIndex()
        return self.rows[cur.row()] if cur.isValid() and cur.row() < len(self.rows) else None

    def select_cue(self, cue: int):
        i = next((k for k, r in enumerate(self.rows) if r.cue == cue), None)
        if i is not None:
            self._select(i, center=True)

    def nav_state(self) -> tuple[int, int]:
        """(1-based position of the current row among changes, total changes)"""
        ch = self.changed_rows()
        cur = self.list.currentIndex().row()
        if cur < 0 or not ch:
            return 0, len(ch)
        k = bisect.bisect_right(ch, cur)
        return (k if k and ch[k - 1] == cur else 0), len(ch)

    def step(self, direction: int):
        ch = self.changed_rows()
        if not ch:
            return
        cur = self.list.currentIndex().row()
        if direction > 0:
            nxt = next((i for i in ch if i > cur), ch[0])
        else:
            nxt = next((i for i in reversed(ch) if i < cur), ch[-1])
        self._select(nxt, center=True)

    # ------------------------------------------------------------------ internals
    def _row_at(self, sec: float) -> int:
        if not self._starts:
            return -1
        return max(0, bisect.bisect_right(self._starts, sec) - 1)

    def _select(self, i: int, top=False, center=False):
        if i < 0 or i >= len(self.rows):
            return
        idx = self.model.index(i)
        self._syncing = True
        self.list.setCurrentIndex(idx)
        hint = QAbstractItemView.PositionAtTop if top else (
            QAbstractItemView.PositionAtCenter if center else QAbstractItemView.EnsureVisible)
        self.list.scrollTo(idx, hint)
        self._syncing = False
        self.scrub.set_position(self.rows[i].start)
        self.position_changed.emit()

    def _on_scrub(self, sec: float):
        i = self._row_at(sec)
        if i < 0:
            return
        idx = self.model.index(i)
        self._syncing = True
        self.list.setCurrentIndex(idx)
        self.list.scrollTo(idx, QAbstractItemView.PositionAtCenter)
        self._syncing = False
        self.position_changed.emit()

    def _on_scroll(self, _):
        if self._syncing or self.scrub.dragging or not self.rows:
            return
        cur = self.list.currentIndex()
        vr = self.list.viewport().rect()
        if cur.isValid() and self.list.visualRect(cur).intersects(vr):
            return                     # the selected row is still on screen: keep the playhead on it
        top = self.list.indexAt(QPoint(10, 4))
        if top.isValid():
            self.scrub.set_position(self.rows[top.row()].start)

    def _on_current(self, cur, _prev):
        if cur.isValid() and not self._syncing and not self.scrub.dragging:
            self.scrub.set_position(self.rows[cur.row()].start)
        self.position_changed.emit()


def _opt(view: QListView):
    from PySide6.QtWidgets import QStyleOptionViewItem
    o = QStyleOptionViewItem()
    o.font = view.font()
    o.rect = view.viewport().rect()
    return o
