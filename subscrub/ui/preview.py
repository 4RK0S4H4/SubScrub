"""Before/after preview: a timeline scrub bar with removal markers, synced to a
side-by-side list of every changed line. Judgement calls are flagged with the reason, and
any edit can be undone line by line."""
from __future__ import annotations

import bisect
import html
import re
from dataclasses import dataclass

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QTextDocument
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QListView, QPushButton, QStyle,
                               QStyledItemDelegate, QToolTip, QVBoxLayout, QWidget)

from ..cleaner import CleanResult, diff_html, visible_text
from .theme import num_font, qc
from .widgets import ScreenView
from ..subformats import to_seconds


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


@dataclass
class Mark:
    start: float
    end: float
    status: str          # kept | edited | removed | flagged
    text: str


def build(result: CleanResult, show: str) -> tuple[list[Row], list[Mark]]:
    """show: 'check' (judgement calls only) | 'changes' | 'all'"""
    fmt = result.doc.fmt
    rows, marks = [], []
    for i, (cue, r) in enumerate(zip(result.doc.cues, result.results)):
        start, end = parse_time(cue.start), parse_time(cue.end)
        before = visible_text(cue.text, fmt)
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
        marks.append(Mark(start, max(end, start), mark, before))
        if show == "changes" and status == "kept":
            continue
        if show == "check" and not flagged:
            continue
        rows.append(Row(i, start, end, status, before, after, flagged, "; ".join(r.why[:2]) if r.changed else "",
                        override, bool(re.search(r"<i>|\\i1", final or "", re.I))))
    rows.sort(key=lambda x: x.start)
    marks.sort(key=lambda m: m.start)
    return rows, marks


def _alpha(color: str, a: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(a)
    return c


def _blend(fg: str, bg: str, a: float) -> str:
    f, b = QColor(fg), QColor(bg)
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
        if role == Qt.ToolTipRole:
            return None
        return None


# ============================================================================ list + delegate

PAD_L, TIME_W, GAP, PAD_R, VPAD, FOOT_H = 16, 58, 18, 16, 10, 30


def columns(width: int) -> tuple[int, int, int, int]:
    """(x_time, x_before, x_after, col_w)"""
    x_before = PAD_L + TIME_W + 10
    col_w = max(80, (width - x_before - PAD_R - GAP) // 2)
    return PAD_L, x_before, x_before + col_w + GAP, col_w


class RowDelegate(QStyledItemDelegate):
    def __init__(self, view: "PreviewList"):
        super().__init__(view)
        self.view = view
        self.t: dict = {}
        self._h: dict = {}

    def set_theme(self, t: dict):
        self.t = t
        self._h.clear()

    def clear(self):
        self._h.clear()

    def _html(self, row: Row) -> tuple[str, str]:
        t = self.t
        text_c = t["text"] if row.status != "kept" else t["dim"]
        before_c = t["dim"] if row.status != "kept" else t["faint"]
        rm_bg = _blend(t["removed"], t["bg"], 0.14)
        undone = row.override == "orig"
        before = diff_html(row.before, row.after if row.status != "kept" and not undone else row.before)
        before = before.replace(
            "<del>", f'<span style="color:{t["removed"]}; background-color:{rm_bg}; text-decoration: line-through;">'
        ).replace("</del>", "</span>").replace("\n", "<br>")
        before = f'<div style="color:{before_c}">{before}</div>'
        if row.after is None:
            after = f'<div style="color:{t["faint"]}; font-style: italic;">Removed</div>'
        else:
            body = html.escape(row.after).replace(chr(10), "<br>")
            if row.italic:
                body = f"<i>{body}</i>"
            after = f'<div style="color:{text_c}">{body}</div>'
        return before, after

    def has_footer(self, row: Row) -> bool:
        return row.status != "kept" and (row.flagged or row.override == "orig")

    def _doc(self, html_: str, width: int, font: QFont) -> QTextDocument:
        d = QTextDocument()
        d.setDefaultFont(font)
        d.setDocumentMargin(0)
        d.setHtml(html_)
        d.setTextWidth(width)
        return d

    def sizeHint(self, option, index):
        row: Row = index.data(Qt.UserRole)
        width = self.view.viewport().width()
        key = (id(row), width, row.override)
        h = self._h.get(key)
        if h is None:
            _, _, _, col_w = columns(width)
            b, a = self._html(row)
            h = int(max(self._doc(b, col_w, option.font).size().height(),
                        self._doc(a, col_w, option.font).size().height(),
                        QFontMetrics(option.font).height())) + VPAD * 2
            if self.has_footer(row):
                h += FOOT_H
            self._h[key] = h
        return QSize(width, h)

    def button_rect(self, rect, row: Row) -> QRectF | None:
        """The 'Keep original' / 'Use cleaned' pill in a row's footer."""
        if not self.has_footer(row):
            return None
        x_time, x_before, x_after, col_w = columns(rect.width())
        w = 112
        return QRectF(rect.left() + x_after + col_w - w, rect.bottom() - FOOT_H - 2, w, FOOT_H - 4)

    def paint(self, p: QPainter, option, index):
        row: Row = index.data(Qt.UserRole)
        t = self.t
        r = option.rect
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        if row.flagged and row.override is None:
            p.fillRect(r, _alpha(t["accent"], 0.15 if selected else 0.08))
        elif selected:
            p.fillRect(r, _alpha(t["text"], 0.10 if t.get("is_dark") else 0.06))
        elif hover:
            p.fillRect(r, _alpha(t["text"], 0.05))
        # status edge
        if row.override == "orig":
            edge = t["faint"]
        elif row.flagged:
            edge = t["accent"]
        else:
            edge = {"removed": t["removed"], "edited": t["trim"]}.get(row.status)
        if edge:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(edge))
            p.drawRoundedRect(QRectF(r.left() + 4, r.top() + 8, 3, r.height() - 16), 1.5, 1.5)
        # separator
        p.setPen(QPen(qc(t["border"]), 1))
        p.drawLine(r.left() + PAD_L, r.bottom(), r.right() - PAD_R, r.bottom())

        x_time, x_before, x_after, col_w = columns(r.width())
        f = num_font(QFont(option.font))
        f.setPointSizeF(max(7.5, f.pointSizeF() - 1))
        p.setFont(f)
        p.setPen(QColor(t["accent_ink"] if selected else t["faint"]))
        p.drawText(QRectF(r.left() + x_time, r.top() + VPAD + 1, TIME_W, 20), Qt.AlignLeft | Qt.AlignTop,
                   fmt_clock(row.start))
        b, a = self._html(row)
        for x, h in ((x_before, b), (x_after, a)):
            d = self._doc(h, col_w, option.font)
            p.save()
            p.translate(r.left() + x, r.top() + VPAD)
            d.drawContents(p)
            p.restore()
        # footer: why it's flagged + the undo button
        br = self.button_rect(r, row)
        if br is not None:
            fy = br.top()
            p.setFont(f)
            if row.override == "orig":
                note, colr = "You kept the original line.", t["faint"]
            else:
                note, colr = "Check: " + (row.why or "judgement call"), t["accent_ink"]
            fm = QFontMetrics(f)
            avail = br.left() - (r.left() + x_before) - 10
            p.setPen(QColor(colr))
            p.drawText(QRectF(r.left() + x_before, fy, avail, br.height()), Qt.AlignLeft | Qt.AlignVCenter,
                       fm.elidedText(note, Qt.ElideRight, int(avail)))
            label = "Use cleaned" if row.override == "orig" else "Keep original"
            p.setPen(QPen(qc(t["border_strong"]), 1))
            p.setBrush(qc(t["surface2"]))
            p.drawRoundedRect(br, br.height() / 2, br.height() / 2)
            p.setPen(QColor(t["text"]))
            p.drawText(br, Qt.AlignCenter, label)
        p.restore()


class PreviewList(QListView):
    toggle_requested = Signal(int)            # cue index

    def __init__(self):
        super().__init__()
        self.setObjectName("PreviewList")
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setResizeMode(QListView.Adjust)
        self.setUniformItemSizes(False)
        self.setLayoutMode(QListView.Batched)
        self.setBatchSize(150)
        self.setMouseTracking(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QListView.NoFrame)
        self.delegate = RowDelegate(self)
        self.setItemDelegate(self.delegate)

    def resizeEvent(self, e):
        super().resizeEvent(e)
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
        self.viewport().setCursor(Qt.PointingHandCursor if over else Qt.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseDoubleClickEvent(self, e):
        idx, row = self._row_at(e.position().toPoint())
        if row is not None and row.status != "kept":
            self.toggle_requested.emit(row.cue)
            return
        super().mouseDoubleClickEvent(e)


# ============================================================================ scrub bar

class ScrubBar(QWidget):
    scrubbed = Signal(float)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(66)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.duration = 1.0
        self.marks: list[Mark] = []
        self._starts: list[float] = []
        self.pos_sec = 0.0
        self.dragging = False
        self.hover_x: float | None = None
        self.t: dict = {}

    def set_theme(self, t):
        self.t = t
        self.update()

    def set_data(self, duration: float, marks: list[Mark]):
        last = max((m.end for m in marks), default=0.0)
        self.duration = max(duration or 0.0, last, 1.0)
        self.marks = marks
        self._starts = [m.start for m in marks]
        self.update()

    def set_position(self, sec: float):
        if not self.dragging:
            self.pos_sec = sec
            self.update()

    # geometry
    def _track(self) -> QRectF:
        return QRectF(10, 14, self.width() - 20, 30)

    def _x(self, sec: float) -> float:
        tr = self._track()
        return tr.left() + tr.width() * min(1.0, max(0.0, sec / self.duration))

    def _sec(self, x: float) -> float:
        tr = self._track()
        return max(0.0, min(self.duration, (x - tr.left()) / tr.width() * self.duration))

    def paintEvent(self, _):
        if not self.t:
            return
        t = self.t
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        tr = self._track()
        p.setPen(Qt.NoPen)
        p.setBrush(qc(t["surface3"]))
        p.drawRoundedRect(tr, 6, 6)
        mid = tr.center().y()
        heights = {"kept": 8, "edited": 18, "removed": 24, "flagged": 30}
        colors = {"kept": QColor(t["faint"]), "edited": QColor(t["trim"]), "removed": QColor(t["removed"]),
                  "flagged": QColor(t["accent"])}
        colors["kept"].setAlpha(110)
        for layer in ("kept", "edited", "removed", "flagged"):          # changes drawn on top
            p.setBrush(colors[layer])
            h = heights[layer]
            for m in self.marks:
                if m.status != layer:
                    continue
                x0, x1 = self._x(m.start), self._x(m.end)
                w = max(2.0, x1 - x0)
                p.drawRoundedRect(QRectF(x0, mid - h / 2, w, h), 1, 1)
        # hover guide
        if self.hover_x is not None and not self.dragging:
            c = QColor(t["text"])
            c.setAlpha(60)
            p.setPen(QPen(c, 1))
            p.drawLine(int(self.hover_x), int(tr.top()) - 4, int(self.hover_x), int(tr.bottom()) + 4)
        # playhead
        x = self._x(self.pos_sec)
        p.setPen(QPen(QColor(t["accent"]), 2))
        p.drawLine(QPoint(int(x), int(tr.top()) - 6), QPoint(int(x), int(tr.bottom()) + 6))
        p.setPen(QPen(qc(t["surface_s"]), 2))
        p.setBrush(QColor(t["accent"]))
        p.drawEllipse(QRectF(x - 6, tr.top() - 12, 12, 12))
        # labels
        f = QFont(self.font())
        f.setPointSizeF(max(7.5, f.pointSizeF() - 1.5))
        p.setFont(f)
        p.setPen(QColor(t["faint"]))
        ly = tr.bottom() + 6
        p.drawText(QRectF(tr.left(), ly, 80, 16), Qt.AlignLeft, "0:00")
        p.drawText(QRectF(tr.right() - 80, ly, 80, 16), Qt.AlignRight, fmt_clock(self.duration))
        label = fmt_clock(self.pos_sec)
        fm = QFontMetrics(f)
        lw = fm.horizontalAdvance(label) + 14
        lx = min(max(tr.left() + 40, x - lw / 2), tr.right() - 40 - lw)
        p.setPen(QColor(t["accent"]))
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
            self._emit(e.position().x())

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self.dragging:
            self._emit(x)
        else:
            self.hover_x = x
            m = self._nearest_change(x)
            if m:
                what = {"removed": "Removed", "flagged": "Check this one"}.get(m.status, "Trimmed")
                text = html.escape(m.text.replace("\n", " ")[:90])
                QToolTip.showText(e.globalPosition().toPoint(),
                                  f"<b>{fmt_clock(m.start)}</b>&nbsp;&nbsp;{what}<br>{text}", self)
            else:
                QToolTip.showText(e.globalPosition().toPoint(), fmt_clock(self._sec(x)), self)
            self.update()

    def mouseReleaseEvent(self, e):
        if self.dragging:
            self.dragging = False
            self._emit(e.position().x())

    def leaveEvent(self, e):
        self.hover_x = None
        self.update()

    def _emit(self, x):
        self.pos_sec = self._sec(x)
        self.update()
        self.scrubbed.emit(self.pos_sec)


# ============================================================================ panel

class ColumnHeader(QWidget):
    def __init__(self, view: PreviewList):
        super().__init__()
        self.view = view
        self.setFixedHeight(22)
        self.t: dict = {}

    def paintEvent(self, _):
        if not self.t:
            return
        p = QPainter(self)
        f = QFont(self.font())
        f.setPointSizeF(max(7.5, f.pointSizeF() - 1))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(self.t["faint"]))
        x_time, x_before, x_after, col_w = columns(self.view.viewport().width())
        for x, s in ((x_time, "Time"), (x_before, "Before"), (x_after, "After, as saved")):
            p.drawText(QRectF(x, 0, col_w, self.height()), Qt.AlignLeft | Qt.AlignVCenter, s)
        p.end()


class PreviewPanel(QWidget):
    position_changed = Signal()

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.screen = ScreenView(height=158)
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
        self._syncing = False
        self.scrub.scrubbed.connect(self._on_scrub)
        self.list.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.list.selectionModel().currentChanged.connect(self._on_current)
        self.position_changed.connect(self._update_screen)

    def set_theme(self, t: dict):
        self.list.delegate.set_theme(t)
        self.scrub.set_theme(t)
        self.screen.set_theme(t)
        self.header.t = t
        self.header.update()
        self.list.doItemsLayout()
        self.list.viewport().update()

    def set_result(self, result: CleanResult, show: str, duration: float, keep_position: bool = True):
        keep_cue = None
        cur = self.list.currentIndex()
        if keep_position and cur.isValid() and cur.row() < len(self.rows):
            keep_cue = self.rows[cur.row()].start
        rows, marks = build(result, show)
        self.rows = rows
        self._starts = [r.start for r in rows]
        self.list.delegate.clear()
        self.model.set_rows(rows)
        self.scrub.set_data(duration, marks)
        if rows:
            i = self._row_at(keep_cue) if keep_cue is not None else 0
            self._select(i, top=keep_cue is None)
        else:
            self.scrub.set_position(0)
        self.position_changed.emit()

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
            self.screen.show_line("", when, right, alert, placeholder="Nothing on screen — this whole line is removed")
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
