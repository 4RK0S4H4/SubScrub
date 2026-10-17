"""The SubScrub main window.

A glass top bar (the app's mark, Files / Review tabs, tool status, theme, Settings, More) over two screens:
  * Files  – drop files, each gets a status (Ready / to Check / Saved…). One button saves them all.
  * Review – one file up close: which track, which kinds of edits, and a before/after preview where
             judgement calls are flagged and any line can be put back the way it was.
Everything that is set once (how to save, theme, default edits) lives in Settings. Dialogs are in dialogs.py,
background work in workers.py, the look in theme.py / glass.py / widgets.py.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QProgressBar,
                               QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__, batch, media, naming
from ..batch import Item
from ..cleaner import CleanOptions
from . import glass, theme
from .dialogs import (LYRIC_MODES, RULES, SUB_MODES, VIDEO_MODES, AboutDialog, DecisionDialog,  # noqa: F401
                      LockDialog, LogDialog, NameDialog, SettingsDialog, ToolsDialog, ask, lyric_mode, tell,
                      with_lyric_mode)
from .glass import GlassDialog, Panel
from .preview import PreviewPanel
from .theme import qc
from .widgets import (T, Banner, Bar, ElidedLabel, FileRow, Legend, LinkButton, RuleRow, ScreenView, Segmented,
                      StatusTag, Tips, TrackCard, button, draw_icon, hline, icon, icon_button, label, logo_pixmap,
                      hide_tip, make_menu, refresh_icons, set_prop, set_tip, themed_icon)
from .workers import (CANCELLED, BatchWorker, Bus, LockCheck, Worker, final_changed, fmt_duration, fmt_size,  # noqa: F401
                      load_docs, plural)


class DemoScreen(ScreenView):
    """The welcome screen's caption: click to play the scrub again."""
    clicked = Signal()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()


class LogoMark(QWidget):
    """The app's mark in the top bar, with a faint edge in dark mode so it doesn't sink into the bar."""

    def __init__(self, size: int):
        super().__init__()
        self.setFixedSize(size, size)

    def paintEvent(self, _):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawPixmap(0, 0, logo_pixmap(self.width()))
        if t["is_dark"]:
            from PySide6.QtGui import QPen
            p.setPen(QPen(qc("rgba(255,255,255,0.14)"), 1))
            r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            p.drawRoundedRect(r, self.width() * 0.24, self.width() * 0.24)


class DragVeil(QWidget):
    """Shown while files are dragged over the window: a soft accent fill, an icon and "Drop to Add"."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.text = "Drop to Add"

    def paintEvent(self, _):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), qc(theme.alpha(t["bg"], .72)))
        r = QRectF(self.rect()).adjusted(16, 16, -16, -16)
        p.setPen(Qt.NoPen)
        p.setBrush(qc(t["tile_on"]))
        p.drawRoundedRect(r, 16, 16)
        c = r.center()
        p.drawPixmap(int(c.x() - 24), int(c.y() - 52), draw_icon("upload", t["btn"] if not t["is_dark"] else t["acc"], 48))
        p.setFont(theme.font(22, 700))
        p.setPen(qc(t["text"]))
        p.drawText(QRectF(r.left(), c.y() + 6, r.width(), 34), Qt.AlignCenter, self.text)


class MainWindow(QMainWindow):
    PAGE_WELCOME, PAGE_FILES, PAGE_REVIEW = 0, 1, 2

    def __init__(self, initial_paths: list[str] | str | None = None):
        super().__init__()
        self.settings = QSettings(APP_NAME, APP_NAME)
        self.theme_mode = self.settings.value("theme", "system")
        self.t = theme.palette_for(self.theme_mode)
        theme.set_current(self.t)
        Tips.install(QApplication.instance())
        self.bus = Bus()
        self.bus.log.connect(self._append_log)
        self.log_lines: list[str] = []
        self.tools = media.find_tools(self._tool_overrides())

        self.items: list[Item] = []
        self.review_uid: int | None = None
        self.pref: dict | None = None             # the kind of track the user picked, applied to every file
        self.rows: dict[int, FileRow] = {}
        self.cards: dict[int, TrackCard] = {}
        self.loader: Worker | None = None
        self.name_worker: Worker | None = None
        self._tmdb = None
        self.loading_uid: int | None = None
        self.batch_worker: BatchWorker | None = None
        self._saving = False                      # set before the save thread starts, cleared when it's done
        self.batch_total = 0
        self.batch_pos = 0
        self.last_output: str | None = None
        self.add_note = ""
        self._recalc: list[int] = []
        self._winget_procs: list[subprocess.Popen] = []
        self._preview_key = None
        self._load_prefs()

        self._recalc_timer = QTimer(self)
        self._recalc_timer.setInterval(0)
        self._recalc_timer.timeout.connect(self._recalc_step)
        self._rule_timer = QTimer(self)
        self._rule_timer.setSingleShot(True)
        self._rule_timer.setInterval(90)
        self._rule_timer.timeout.connect(self._apply_review_rules)
        self._lock_timer = QTimer(self)
        self._lock_timer.setSingleShot(True)
        self._lock_timer.setInterval(400)
        self._lock_timer.timeout.connect(self.check_locks)
        self._summary_timer = QTimer(self)        # many changes in one go → one summary update
        self._summary_timer.setSingleShot(True)
        self._summary_timer.setInterval(0)
        self._summary_timer.timeout.connect(self._do_update_summary)
        self._lock_thread: LockCheck | None = None

        self.setWindowTitle(APP_NAME)
        self.setAcceptDrops(True)
        self.setMinimumSize(1000, 680)
        self.sysglass = False                   # becomes True once Windows has accepted the Mica backdrop
        self.glass_wanted = self.glass and glass.system_backdrop_supported()
        if self.glass_wanted:
            self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._build()
        self.apply_theme()
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        else:
            scr = QApplication.primaryScreen()
            w, h = 1340, 900
            if scr is not None:
                a = scr.availableGeometry()
                w, h = min(w, int(a.width() * 0.92)), min(h, int(a.height() * 0.92))
            self.resize(w, h)

        QShortcut(QKeySequence.Open, self, activated=self.choose_files)
        QShortcut(QKeySequence.Save, self, activated=lambda: self.start_save())
        QShortcut(QKeySequence("F3"), self, activated=lambda: self.preview.step(1))
        QShortcut(QKeySequence("Shift+F3"), self, activated=lambda: self.preview.step(-1))
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.toggle_current_line)
        QShortcut(QKeySequence("Escape"), self, activated=lambda: self.show_files() if self.page() == 2 else None)
        QShortcut(QKeySequence("Alt+Left"), self, activated=lambda: self.show_files() if self.page() == 2 else None)
        QShortcut(QKeySequence("Ctrl+,"), self, activated=self.show_settings)
        try:
            QApplication.styleHints().colorSchemeChanged.connect(
                lambda *_: self.apply_theme() if self.theme_mode == "system" else None)
        except Exception:
            pass
        self.log(f"{APP_NAME} {__version__}. FFmpeg: {self.tools.ffmpeg or 'not found'}; "
                 f"MKVToolNix: {self.tools.mkvmerge or 'not found'}")
        if initial_paths:
            paths = [initial_paths] if isinstance(initial_paths, str) else list(initial_paths)
            QTimer.singleShot(0, lambda: self.add_paths(paths))

    # ------------------------------------------------------------------ small helpers
    def item(self, uid: int | None) -> Item | None:
        return next((i for i in self.items if i.uid == uid), None)

    @property
    def cur(self) -> Item | None:
        return self.item(self.review_uid)

    def busy(self) -> bool:
        return self._saving or bool(self.batch_worker and self.batch_worker.isRunning())

    def page(self) -> int:
        return self.stack.currentIndex()

    def opts_for(self, it: Item) -> CleanOptions:
        return it.opts or self.defaults

    # ------------------------------------------------------------------ prefs
    def _tool_overrides(self) -> dict:
        return {k: self.settings.value(f"tools/{k}", "") for k in ("ffmpeg", "ffprobe", "mkvmerge")}

    def _load_prefs(self):
        s = self.settings
        try:
            stored = json.loads(s.value("clean_options_v2", "") or "null")
        except (TypeError, ValueError):
            stored = None
        self.defaults = CleanOptions.from_dict(stored) if stored else CleanOptions()
        vm, sm = s.value("video_mode", "replace"), s.value("sub_mode", "embed")
        self.video_mode = vm if vm in dict(VIDEO_MODES) else "replace"
        self.sub_mode = sm if sm in dict(SUB_MODES) else "embed"
        self.keep_sdh = s.value("keep_sdh", "false") == "true"
        self.make_default = s.value("make_default", "true") == "true"
        self.recycle_sub = s.value("recycle_sub", "true") == "true"
        self.confirm_replace = s.value("confirm_replace", "true") == "true"
        self.track_name = s.value("track_name", "") or ""
        self.preview_filter = s.value("preview_filter", "changes")
        self.rename_on = s.value("rename_on", "true") == "true"
        self.name_year = s.value("name_year", "false") == "true"
        self.drop_langs = s.value("drop_langs", "true") == "true"
        self.glass = s.value("mica", "false") == "true"
        self.tmdb_key = s.value("tmdb_key", "") or ""

    def _save_prefs(self):
        s = self.settings
        s.setValue("clean_options_v2", json.dumps(self.defaults.to_dict()))
        s.setValue("video_mode", self.video_mode)
        s.setValue("sub_mode", self.sub_mode)
        s.setValue("keep_sdh", "true" if self.keep_sdh else "false")
        s.setValue("make_default", "true" if self.make_default else "false")
        s.setValue("recycle_sub", "true" if self.recycle_sub else "false")
        s.setValue("confirm_replace", "true" if self.confirm_replace else "false")
        s.setValue("track_name", self.track_name)
        s.setValue("rename_on", "true" if self.rename_on else "false")
        s.setValue("name_year", "true" if self.name_year else "false")
        s.setValue("drop_langs", "true" if self.drop_langs else "false")
        s.setValue("tmdb_key", self.tmdb_key)
        s.setValue("mica", "true" if self.glass else "false")

    # ------------------------------------------------------------------ build
    def _build(self):
        root = glass.GlassRoot()
        root.setObjectName("Root")
        root.setFocusPolicy(Qt.ClickFocus)           # the window starts with focus here: no control shows a ring
        self.root = root
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._build_topbar())
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_welcome())
        self.stack.addWidget(self._build_files())
        self.stack.addWidget(self._build_review())
        v.addWidget(self.stack, 1)
        self.footer = self._build_footer()
        v.addWidget(self.footer)
        self.footer.hide()
        self.veil = DragVeil(root)
        self.veil.hide()

    def _panel(self, radius: float = 12, strong: bool = False) -> Panel:
        p = Panel(radius, strong=strong)
        self.root.register(p)
        return p

    def _build_topbar(self):
        bar = Bar("bottom")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 10, 16, 10)
        lay.setSpacing(18)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        brand.addWidget(LogoMark(28))
        brand.addWidget(label(APP_NAME, "Brand"))
        lay.addLayout(brand)
        self.tabs = Segmented([("files", "Files"), ("review", "Review")], tabs=True)
        self.tabs.changed.connect(self._tab_changed)
        lay.addWidget(self.tabs, 0, Qt.AlignVCenter)
        lay.addStretch(1)
        acts = QHBoxLayout()
        acts.setSpacing(4)
        self.tools_btn = button("Tools Ready", "subtle", "sm", self.show_tools, icon_kind="check", icon_role="text2")
        acts.addWidget(self.tools_btn)
        acts.addSpacing(4)
        self.theme_btn = icon_button("sun", "Switch to light mode", self._toggle_theme)
        acts.addWidget(self.theme_btn)
        self.settings_btn = icon_button("gear", "Settings (Ctrl+,)", self.show_settings)
        acts.addWidget(self.settings_btn)
        self.more_btn = icon_button("more", "More", self._show_more)
        self.more_menu = make_menu(self)
        self._menu_actions = [
            ("gear", self.more_menu.addAction("Settings…", self.show_settings)),
            ("restart", self.more_menu.addAction("Tools and Paths…", self.show_tools)),
            ("log", self.more_menu.addAction("Activity Log", self.show_log)),
            ("history", self.more_menu.addAction("Undo Last Renames…", self.undo_renames)),
        ]
        self.more_menu.addSeparator()
        self._menu_actions.append(("info", self.more_menu.addAction(f"About {APP_NAME}", self.show_about)))
        acts.addWidget(self.more_btn)
        lay.addLayout(acts)
        self.topbar = bar
        return bar

    def _show_more(self):
        b = self.more_btn
        self.more_menu.adjustSize()
        pos = b.mapToGlobal(QPoint(b.width() - self.more_menu.sizeHint().width(), b.height() + 6))
        self.more_menu.exec(pos)

    def _build_welcome(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(40, 24, 40, 40)
        outer.addStretch(2)
        card = self._panel(12)
        card.setFixedWidth(760)
        col = QVBoxLayout(card)
        col.setContentsMargins(40, 36, 40, 34)
        col.setSpacing(0)
        # the one bold moment: a caption on a dark screen losing its sound tag
        self.demo = DemoScreen(height=220)
        self.demo.demo = ("[door creaks]", "Hello? Is anyone there?")
        self.demo.setCursor(Qt.PointingHandCursor)
        set_tip(self.demo, "Click to play again")
        self.demo.clicked.connect(lambda: self.play_demo(0))
        col.addWidget(self.demo)
        self._demo_anim = QPropertyAnimation(self.demo, b"demo_t", self)
        self._demo_anim.setDuration(1700)
        self._demo_anim.setStartValue(0.0)
        self._demo_anim.setEndValue(2.0)
        self._demo_anim.setEasingCurve(QEasingCurve.InOutCubic)
        col.addSpacing(30)
        col.addWidget(label("Drop videos, folders or subtitle files", "Hero"))
        col.addSpacing(10)
        col.addWidget(label("SubScrub takes the sound descriptions, speaker names and music cues out of SDH subtitles, "
                            "then puts the cleaned track back into the video. You check anything it isn't sure about.",
                            "HeroSub", True))
        col.addSpacing(26)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.choose_btn = button("Choose Files", "primary", "", self.choose_files)
        card.glow.append(self.choose_btn)
        row.addWidget(self.choose_btn)
        row.addWidget(button("Choose a Folder", "", "", self.choose_folder))
        row.addStretch(1)
        row.addWidget(label("MKV, MP4, MOV, WebM, .srt, .ass, .vtt", "Faint"), 0, Qt.AlignVCenter)
        col.addLayout(row)
        self.welcome_banner_wrap = QWidget()
        wl = QVBoxLayout(self.welcome_banner_wrap)
        wl.setContentsMargins(0, 22, 0, 0)
        self.welcome_banner = Banner("FFmpeg isn't installed yet. SubScrub needs it to read and write video files.",
                                     "Set Up FFmpeg", self.show_tools, tone="warn")
        wl.addWidget(self.welcome_banner)
        col.addWidget(self.welcome_banner_wrap)
        outer.addWidget(card, 0, Qt.AlignHCenter)
        outer.addStretch(3)
        self.drop = card
        return page

    def play_demo(self, delay: int = 500):
        if not hasattr(self, "_demo_anim"):
            return
        self._demo_anim.stop()
        self.demo.setDemo(0.0)
        QTimer.singleShot(delay, self._demo_anim.start)

    def _build_files(self):
        page = QWidget()
        outer = QHBoxLayout(page)
        outer.setContentsMargins(32, 28, 32, 12)
        outer.addStretch(1)
        colw = QWidget()
        colw.setMaximumWidth(1120)
        colw.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        col = QVBoxLayout(colw)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        outer.addWidget(colw, 100)
        outer.addStretch(1)

        head = QHBoxLayout()
        head.setSpacing(8)
        ttl = QVBoxLayout()
        ttl.setSpacing(4)
        ttl.addWidget(label("Files", "H1"))
        self.summary = label("", "Sub", True)
        ttl.addWidget(self.summary)
        head.addLayout(ttl, 1)
        self.clear_btn = button("Clear List", "subtle", "", self.clear_all)
        head.addWidget(self.clear_btn, 0, Qt.AlignBottom)
        head.addWidget(button("Add Files", "", "", self.choose_files, icon_kind="add-file"), 0, Qt.AlignBottom)
        head.addWidget(button("Add Folder", "", "", self.choose_folder, icon_kind="add-folder"), 0, Qt.AlignBottom)
        col.addLayout(head)
        self.add_note_label = label("", "Hint", True)
        self.add_note_label.setContentsMargins(0, 6, 0, 0)
        self.add_note_label.hide()
        col.addWidget(self.add_note_label)
        col.addSpacing(16)
        self.lock_banner = Banner("", "Check Again", lambda: self.check_locks(force=True), tone="warn")
        self.lock_banner.hide()
        col.addWidget(self.lock_banner)
        self.lock_gap = QWidget()
        self.lock_gap.setFixedHeight(12)
        self.lock_gap.hide()
        col.addWidget(self.lock_gap)

        self.files_scroll = QScrollArea()
        self.files_scroll.setWidgetResizable(True)
        self.files_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        fw = QWidget()
        self.files_box = QVBoxLayout(fw)
        self.files_box.setContentsMargins(0, 0, 0, 0)
        self.files_box.setSpacing(0)
        self.files_box.addStretch(1)
        self.files_scroll.setWidget(fw)
        fp = self._panel(12)
        fl = QVBoxLayout(fp)
        fl.setContentsMargins(0, 6, 2, 6)
        fl.addWidget(self.files_scroll)
        self.files_panel = fp
        self.files_inner = fw
        col.addWidget(fp, 1000)                 # takes the space up to its max height, the stretch below gets the rest

        how = QHBoxLayout()
        how.setContentsMargins(0, 12, 0, 0)
        how.setSpacing(12)
        self.save_how = label("", "Faint", True)
        how.addWidget(self.save_how, 1)
        ch = LinkButton("Change", size=12.5)
        ch.clicked.connect(lambda: self.show_settings("videos"))
        how.addWidget(ch, 0, Qt.AlignTop)
        col.addLayout(how)
        col.addStretch(1)                       # the card hugs its rows; spare space goes below
        return page

    def _fit_files(self):
        """Let the file card be as tall as its rows (it scrolls once they don't fit)."""
        if not hasattr(self, "files_panel"):
            return
        lay = self.files_box
        h = 0
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if w is not None and not w.isHidden():
                if w.layout() is not None:
                    w.layout().activate()
                h += max(w.sizeHint().height(), w.minimumSizeHint().height())
        m = self.files_panel.layout().contentsMargins()
        h += m.top() + m.bottom() + 2
        self.files_panel.setMaximumHeight(max(60, h))

    def _build_review(self):
        page = QWidget()
        body = QHBoxLayout(page)
        body.setContentsMargins(16, 16, 16, 12)
        body.setSpacing(16)

        # ---- sidebar: which track, which kinds of edits
        side = self._panel(12)
        side.setFixedWidth(356)
        self.left = side
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(18, 16, 16, 18)
        col.setSpacing(0)
        scroll.setWidget(inner)
        sv.addWidget(scroll, 1)

        def section(text, right: QLabel):
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(label(text, "CardTitle"))
            row.addStretch(1)
            row.addWidget(right, 0, Qt.AlignBottom)
            col.addLayout(row)

        self.track_count = label("", "SecSub")
        section("Subtitle Track", self.track_count)
        col.addSpacing(2)
        self.file_meta = label("", "Hint", True)
        col.addWidget(self.file_meta)
        col.addSpacing(10)
        self.tracks_box = QVBoxLayout()
        self.tracks_box.setSpacing(8)
        col.addLayout(self.tracks_box)
        self.tracks_note = label("", "Hint", True)
        self.tracks_note.setContentsMargins(0, 10, 0, 0)
        self.tracks_note.hide()
        col.addWidget(self.tracks_note)
        col.addSpacing(18)
        col.addWidget(hline())
        col.addSpacing(16)

        self.edits_state = label("", "SecSub")
        section("What to Remove", self.edits_state)
        col.addSpacing(6)
        self.rule_rows: dict[str, RuleRow] = {}
        for key, title, desc, _ in RULES:
            r = RuleRow(title, desc)
            r.toggled.connect(lambda *_: self._rule_timer.start())
            self.rule_rows[key] = r
            col.addWidget(r)
        col.addSpacing(10)
        lr = QHBoxLayout()
        lr.setSpacing(8)
        lr.addWidget(label("Song Lyrics", "OptTitle"))
        lr.addStretch(1)
        self.lyric_count = label("", "RuleCount")
        self.lyric_count.setFont(theme.num_font(theme.font(13, 600)))
        lr.addWidget(self.lyric_count)
        col.addLayout(lr)
        col.addSpacing(8)
        self.lyrics_seg = Segmented(LYRIC_MODES, wide=True)
        self.lyrics_seg.changed.connect(lambda *_: self._rule_timer.start())
        col.addWidget(self.lyrics_seg)
        col.addSpacing(14)
        links = QHBoxLayout()
        self.make_default_btn = LinkButton("Use These for All Files")
        self.make_default_btn.clicked.connect(self.promote_review_rules)
        self.reset_rules_btn = LinkButton("Back to Defaults")
        self.reset_rules_btn.clicked.connect(self.reset_review_rules)
        links.addWidget(self.make_default_btn)
        links.addStretch(1)
        links.addWidget(self.reset_rules_btn)
        col.addLayout(links)
        self.side_inner = inner
        body.addWidget(side, 0, Qt.AlignTop)

        # ---- main: the file, what will change, and the preview
        main = QWidget()
        self.review_main = main
        v = QVBoxLayout(main)
        v.setContentsMargins(4, 0, 0, 0)
        v.setSpacing(0)
        bar = QHBoxLayout()
        bar.setSpacing(12)
        self.review_title = ElidedLabel("", "H1", Qt.ElideMiddle)
        bar.addWidget(self.review_title, 1)
        self.review_status = StatusTag()
        bar.addWidget(self.review_status, 0, Qt.AlignVCenter)
        self.prev_file = icon_button("chev-up", "Previous file", lambda: self.step_file(-1), "sm")
        self.next_file = icon_button("chev-down", "Next file", lambda: self.step_file(1), "sm")
        nav2 = QHBoxLayout()
        nav2.setSpacing(2)
        nav2.addWidget(self.prev_file)
        nav2.addWidget(self.next_file)
        bar.addLayout(nav2)
        v.addLayout(bar)
        v.addSpacing(2)
        self.review_detail = label("", "Sub", True)
        v.addWidget(self.review_detail)
        self.name_row = QWidget()
        nr = QHBoxLayout(self.name_row)
        nr.setContentsMargins(0, 4, 0, 0)
        nr.setSpacing(8)
        self.name_icon = QLabel()
        self.name_icon.setFixedSize(15, 15)
        themed_icon(self.name_icon, "arrow-right", "text2", 15)
        nr.addWidget(self.name_icon)
        self.name_label = label("", "")
        self.name_label.setFont(theme.font(13))
        nr.addWidget(self.name_label)
        self.name_btn = LinkButton("Change Title")
        self.name_btn.clicked.connect(lambda: self.choose_name())
        nr.addWidget(self.name_btn)
        nr.addStretch(1)
        self.name_row.hide()
        v.addWidget(self.name_row)
        self.nosdh_wrap = QWidget()
        nl = QVBoxLayout(self.nosdh_wrap)
        nl.setContentsMargins(0, 12, 0, 0)
        self.nosdh_banner = Banner("", "Clean Anyway", self.force_clean, tone="info")
        nl.addWidget(self.nosdh_banner)
        self.nosdh_wrap.hide()
        v.addWidget(self.nosdh_wrap)
        v.addSpacing(14)

        ph = QHBoxLayout()
        ph.setSpacing(14)
        self.filter = Segmented([("check", "To Check"), ("changes", "Changes"), ("all", "Everything")])
        self.filter.changed.connect(self._filter_changed)
        ph.addWidget(self.filter)
        self.legend = Legend()
        ph.addWidget(self.legend, 1)
        self.undo_btn = button("Keep Original", "", "sm", self.toggle_current_line, icon_kind="undo",
                               tip="Put the selected line back the way it was (Ctrl+Z, or double-click a line)")
        self.undo_btn.setMinimumWidth(132)
        self._undo_text, self._undo_compact = " Keep Original", False
        ph.addWidget(self.undo_btn)
        nav = QHBoxLayout()
        nav.setSpacing(2)
        self.prev_btn = icon_button("chev-up", "Previous change (Shift+F3)", lambda: self.preview.step(-1), "sm")
        self.next_btn = icon_button("chev-down", "Next change (F3)", lambda: self.preview.step(1), "sm")
        self.nav_count = label("", "Muted")
        self.nav_count.setFont(theme.num_font(theme.font(13, 600)))
        self.nav_count.setMinimumWidth(78)
        self.nav_count.setAlignment(Qt.AlignCenter)
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.nav_count)
        nav.addWidget(self.next_btn)
        ph.addLayout(nav)
        v.addLayout(ph)
        v.addSpacing(12)
        self.preview_stack = QStackedWidget()
        self.preview_empty = label("Pick a subtitle track to see what will change.", "Empty", True)
        self.preview_empty.setAlignment(Qt.AlignCenter)
        self.preview = PreviewPanel()
        self.preview.position_changed.connect(self._update_nav)
        self.preview.list.toggle_requested.connect(self.toggle_line)
        self.preview_stack.addWidget(self.preview_empty)
        self.preview_stack.addWidget(self.preview)
        pp = self._panel(12)
        pl = QVBoxLayout(pp)
        pl.setContentsMargins(6, 6, 4, 4)
        pl.addWidget(self.preview_stack)
        v.addWidget(pp, 1)
        body.addWidget(main, 1)
        return page

    def _build_footer(self):
        f = Bar("top")
        lay = QHBoxLayout(f)
        lay.setContentsMargins(20, 12, 20, 12)
        lay.setSpacing(10)
        col = QVBoxLayout()
        col.setSpacing(6)
        self.status = label("", "Status")
        col.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(320)
        self.progress.hide()
        col.addWidget(self.progress, 0, Qt.AlignLeft)
        lay.addLayout(col)
        lay.addStretch(1)
        self.open_btn = button("Show in Folder", "subtle", "",
                               lambda: self.last_output and media.open_in_explorer(self.last_output),
                               icon_kind="folder-open", icon_role="text2")
        self.open_btn.hide()
        lay.addWidget(self.open_btn)
        self.cancel_btn = button("Stop", "danger", "", self.cancel_save, icon_kind="stop", icon_role="bad")
        self.cancel_btn.setMinimumWidth(120)
        self.cancel_btn.hide()
        lay.addWidget(self.cancel_btn)
        self.save_one_btn = button("Save This File", "", "", lambda: self.start_save(only=self.review_uid))
        lay.addWidget(self.save_one_btn)
        self.save_btn = button("Clean and Save", "primary", "", lambda: self.start_save())
        self.save_btn.setMinimumWidth(180)
        lay.addWidget(self.save_btn)
        f.glow = [self.save_btn]
        return f

    # ------------------------------------------------------------------ theme
    def set_theme(self, mode: str):
        self.theme_mode = mode
        self.settings.setValue("theme", mode)
        self.apply_theme()

    def _toggle_theme(self):
        self.set_theme("light" if self.t["is_dark"] else "dark")

    def apply_theme(self):
        t = self.t = theme.palette_for(self.theme_mode)
        theme.set_current(t)
        app = QApplication.instance()
        app.setPalette(theme.qpalette(t))
        app.setStyleSheet(theme.stylesheet(t))
        self.root.set_theme(t, self.sysglass)
        refresh_icons()
        dark = t["is_dark"]
        themed_icon(self.theme_btn, "sun" if dark else "moon", "text2", 18)
        set_tip(self.theme_btn, "Switch to light mode" if dark else "Switch to dark mode")
        self.theme_btn.setAccessibleName(self.theme_btn.toolTip())
        for kind, act in self._menu_actions:
            act.setIcon(icon(kind, t["text2"], 16))
        self.preview.set_theme(t)
        self._update_tools_pill()
        for it in self.items:
            self._update_row(it)
        self._preview_key = None
        self.render_review()
        for w in QApplication.topLevelWidgets():
            if isinstance(w, GlassDialog) and w.isVisible():
                w.refresh_backdrop()
        for w in self.findChildren(QWidget):
            w.update()
        theme.apply_titlebar(self, t, self.sysglass)

    def showEvent(self, e):
        super().showEvent(e)
        if self.glass_wanted and not self.sysglass:
            self.sysglass = glass.enable_system_backdrop(self, self.t["is_dark"])
            self.root.set_theme(self.t, self.sysglass)
        theme.apply_titlebar(self, self.t, self.sysglass)
        QTimer.singleShot(0, lambda: self.root.setFocus(Qt.OtherFocusReason))
        if not getattr(self, "_demo_played", False) and self.page() == self.PAGE_WELCOME:
            self._demo_played = True
            self.play_demo(700)

    # ------------------------------------------------------------------ tabs
    def _tab_changed(self, key: str):
        if key == "files":
            self.show_files()
            return
        uid = self._review_target()
        if uid is None or self.busy():
            self._sync_tabs()
            return
        self.show_review(uid)

    def _review_target(self) -> int | None:
        if self.item(self.review_uid) is not None:
            return self.review_uid
        for test in (lambda i: i.result is not None and bool(i.result.unresolved()) and i.pending,
                     lambda i: i.loaded, lambda i: True):
            it = next((i for i in self.items if test(i)), None)
            if it is not None:
                return it.uid
        return None

    def _sync_tabs(self):
        self.tabs.set_count("files", len(self.items) if self.items else None)
        self.tabs.set_option_enabled("review", bool(self.items) and not self.busy())
        want = "review" if self.page() == self.PAGE_REVIEW else "files"
        if self.tabs.value() != want:
            self.tabs.set_value(want)

    # ------------------------------------------------------------------ tools
    def _update_tools_pill(self):
        tl = self.tools
        b = self.tools_btn
        if not tl.ready:
            b.setText(" FFmpeg Needed")
            set_prop(b, "kind", "attn")
            themed_icon(b, "alert", "btn_fg", 15)
            set_tip(b, "FFmpeg wasn't found. Click to set it up.")
        else:
            b.setText(" Tools Ready" if tl.mkvmerge else " FFmpeg Ready")
            set_prop(b, "kind", "subtle")
            themed_icon(b, "check", "text2", 15)
            set_tip(b, "FFmpeg and MKVToolNix found. MKVToolNix is used for MKV files."
                    if tl.mkvmerge else "FFmpeg found. Installing MKVToolNix is recommended for MKV files.")
        self.welcome_banner_wrap.setVisible(not tl.ready)
        self._update_buttons()

    def rescan_tools(self):
        self.tools = media.find_tools(self._tool_overrides())
        self.log(f"Tools: ffmpeg={self.tools.ffmpeg} ffprobe={self.tools.ffprobe} mkvmerge={self.tools.mkvmerge}")
        self._update_tools_pill()
        self._pump_loader()

    def show_tools(self):
        ToolsDialog(self).exec()
        self.rescan_tools()

    def winget_install(self, package_id: str):
        if not shutil.which("winget"):
            tell(self, "winget isn't available", "Windows Package Manager (winget) isn't available on this PC.",
                 "Download FFmpeg from https://www.gyan.dev/ffmpeg/builds/ or MKVToolNix from "
                 "https://mkvtoolnix.download/, then use Browse… to point SubScrub at it.")
            return
        cmd = ["winget", "install", "-e", "--id", package_id,
               "--accept-source-agreements", "--accept-package-agreements"]
        self.log("$ " + " ".join(cmd))
        proc = subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        self._winget_procs.append(proc)
        timer = QTimer(self)

        def poll():
            if proc.poll() is not None:
                timer.stop()
                self.log(f"winget finished with code {proc.returncode}")
                self.rescan_tools()
                for w in QApplication.topLevelWidgets():
                    if isinstance(w, ToolsDialog) and w.isVisible():
                        w.refresh()
        timer.timeout.connect(poll)
        timer.start(1000)

    # ------------------------------------------------------------------ renaming (TMDB)
    def _renaming(self) -> bool:
        return self.rename_on and self.video_mode == "replace"

    def _new_name(self, it: Item) -> str | None:
        return batch.new_name(it, self.name_year) if self._renaming() else None

    def _lang_drops(self, it: Item) -> list:
        return batch.other_languages(it) if self.drop_langs and self.video_mode == "replace" else []

    def _has_tidy(self, it: Item) -> bool:
        return it.kind == "video" and it.state in ("ready", "skipped", "failed") and bool(
            self._new_name(it) or self._lang_drops(it))

    def _tidy_note(self, it: Item) -> str:
        """What else happens to the file. The new name is on its own line (the arrow), so it isn't repeated here."""
        if not self._has_tidy(it):
            return ""
        n = len(self._lang_drops(it))
        if not n:
            return ""
        return f"{plural(n, 'other-language subtitle track')} will be removed"

    def _api(self) -> naming.TMDB:
        if self._tmdb is None:
            self._tmdb = naming.TMDB(self.tmdb_key)
        return self._tmdb

    def _pump_names(self):
        """Look up one video at a time; each one is read before it is looked up (the length helps tell titles apart)."""
        if not self._renaming() or (self.name_worker is not None and self.name_worker.isRunning()):
            return
        todo = [i for i in self.items if i.kind == "video" and i.pending and i.loaded and i.name_state == ""]
        if not todo:
            return
        if not self.tmdb_key:
            for i in todo:
                i.name_state = "nokey"
                self._update_row(i)
            self._update_summary()
            return
        it = todo[0]
        it.name_state = "looking"
        self._update_row(it)
        api, name, dur, uid = self._api(), it.name, it.info.duration if it.info else 0.0, it.uid
        w = Worker(lambda prog, cancel: naming.match(api, name, dur), self)
        w.ok.connect(lambda m, u=uid: self._name_found(u, m))
        w.err.connect(lambda msg, u=uid: self._name_failed(u, msg))
        self.name_worker = w
        w.start()

    def _name_found(self, uid: int, m):
        self.name_worker = None
        it = self.item(uid)
        if it is not None:
            it.match, it.name_state, it.name_note = m, "done", m.note
            self._update_row(it)
            if uid == self.review_uid:
                self._update_review_head()
            self._update_summary()
            self._update_buttons()
        QTimer.singleShot(0, self._pump_names)

    def _name_failed(self, uid: int, msg: str):
        self.name_worker = None
        self.log(f"Title lookup failed: {msg}")
        for it in self.items:
            if it.uid == uid or (it.kind == "video" and it.name_state == ""):
                it.name_state, it.name_note = "error", msg
                self._update_row(it)
        self._update_summary()
        self._update_buttons()

    def name_line(self, it: Item) -> tuple[str, str]:
        """(text, tone) for the line under a file's name: the new name (shown with an arrow), or where the title
        lookup stands."""
        if it.kind != "video" or not self._renaming() or it.state in ("done", "saving", "queued", "probing", "reading"):
            return "", ""
        st = it.name_state
        if st in ("", "looking"):
            return "Looking up the title…", "faint"
        if st == "nokey":
            return "", ""                                  # said once in the summary, not on every row
        if st == "error":
            return it.name_note or "Couldn't look up the title", "alert"
        if it.name_pick == "keep":
            return "Keeping this name", "faint"
        new = self._new_name(it)
        if new:
            return new, ""
        if batch.name_choice(it) is not None:
            return "", ""                                  # the name is already right
        if batch.name_unsure(it):
            return "Not sure which title this is, choose one in Review", "alert"
        return "No title match found, keeping this name", "faint"

    def choose_name(self, uid: int | None = None):
        it = self.item(uid if uid is not None else self.review_uid)
        if it is None or self.busy() or it.kind != "video" or not self.tmdb_key:
            return
        dlg = NameDialog(self, it, self._api(), self.name_year)
        if dlg.exec() == QDialog.Accepted and dlg.pick is not None:
            it.name_pick = dlg.pick
            self._update_row(it)
            if it.uid == self.review_uid:
                self._update_review_head()
            self._update_summary()
            self._update_buttons()

    def undo_renames(self):
        if self.busy():
            return
        rows = naming.last_batch()
        if not rows:
            tell(self, "Nothing to undo", "There are no recent renames to undo.")
            return
        choice, _ = ask(self, "Undo the last renames?",
                        f"Put the original names back on {plural(len(rows), 'file')} from {rows[0]['time']}?",
                        [("cancel", "Cancel", ""), ("go", "Undo Renames", "primary")],
                        "Only the names change back. Removed subtitle tracks and originals in the Recycle Bin stay "
                        "as they are.")
        if choice != "go":
            return
        done, problems = 0, []
        for r in rows:
            new, old = r["new"], r["old"]
            try:
                if not os.path.isfile(new):
                    problems.append(f"“{os.path.basename(new)}” isn't there any more")
                elif os.path.exists(old):
                    problems.append(f"“{os.path.basename(old)}” already exists")
                else:
                    sides = media.find_sidecars(new)
                    os.rename(new, old)
                    media._rename_sidecars(new, old, sides, self.log)
                    done += 1
                    for it in self.items:
                        if os.path.normcase(it.path) == os.path.normcase(new):
                            it.path = old
                            row = self.rows.get(it.uid)
                            if row:
                                row.set_name(it.name)
            except OSError as e:
                problems.append(f"“{os.path.basename(new)}”: {e}")
        if not problems:
            naming.mark_undone(rows[0]["batch"])
        self.log(f"Undid {done} rename(s)")
        if problems:
            tell(self, "Some names couldn't be put back", f"{plural(done, 'file')} renamed back. Not changed:",
                 rows=[(p_, "") for p_ in problems[:8]])
        else:
            tell(self, "Renames undone", f"{plural(done, 'file')} renamed back.")

    # ------------------------------------------------------------------ settings, log, about
    def show_settings(self, section: str = "videos"):
        if self.busy():
            return
        old_theme = self.theme_mode
        dlg = SettingsDialog(self, section if isinstance(section, str) else "videos")
        if dlg.exec() != QDialog.Accepted:
            if self.theme_mode != old_theme:
                self.set_theme(old_theme)
            return
        self.video_mode = dlg.video_mode.value()
        self.sub_mode = dlg.sub_mode.value()
        self.make_default = dlg.default_row.isChecked()
        self.keep_sdh = dlg.keep_row.isChecked()
        self.recycle_sub = dlg.recycle_row.isChecked()
        self.confirm_replace = dlg.confirm_row.isChecked()
        self.track_name = dlg.name_edit.text().strip()
        old_key = self.tmdb_key
        self.glass = dlg.glass_row.isChecked() if glass.system_backdrop_supported() else self.glass
        self.rename_on = dlg.rename_row.isChecked()
        self.name_year = dlg.year_row.isChecked()
        self.drop_langs = dlg.langs_row.isChecked()
        self.tmdb_key = dlg.key_edit.text().strip()
        if self.tmdb_key != old_key:
            self._tmdb = None
            for it in self.items:
                if it.pending and it.kind == "video" and it.name_state in ("error", "nokey"):
                    it.name_state, it.match = "", None
        new = dlg.options()
        changed = new != self.defaults
        self.defaults = new
        self._save_prefs()
        self._pump_names()
        for it in self.items:
            self._update_row(it)
        if changed:
            for it in self.items:
                if it.opts is None and it.loaded:
                    self._queue_recalc(it)
            if self.cur is not None and self.cur.opts is None:
                self._compute_result(self.cur)
                self.render_review()
        self._update_summary()

    def log(self, msg: str):
        self.bus.log.emit(msg)

    def _append_log(self, msg: str):
        self.log_lines.append(time.strftime("[%H:%M:%S] ") + msg)
        self.log_lines = self.log_lines[-4000:]

    def show_log(self):
        LogDialog("\n".join(self.log_lines), self).exec()

    def show_about(self):
        AboutDialog(self).exec()

    # ------------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls()) and not self.busy():
            e.acceptProposedAction()
            self.veil.text = "Drop to Add" if self.items else "Drop to Open"
            self.veil.setGeometry(self.root.rect())
            self.veil.raise_()
            self.veil.show()

    def dragLeaveEvent(self, e):
        self.veil.hide()

    def dropEvent(self, e):
        self.veil.hide()
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths and not self.busy():
            self.add_paths(paths)

    # ------------------------------------------------------------------ adding & removing files
    def choose_files(self):
        if self.busy():
            return
        start = self.settings.value("last_dir", "") or str(Path.home())
        vids = " ".join(f"*{e}" for e in sorted(media.VIDEO_EXTS))
        subs = " ".join(f"*{e}" for e in sorted(media.TEXT_SUB_EXTS))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose videos or subtitle files", start,
            f"Videos and subtitles ({vids} {subs});;Videos ({vids});;Subtitle files ({subs});;All files (*)")
        if paths:
            self.add_paths(paths)

    def choose_folder(self):
        if self.busy():
            return
        start = self.settings.value("last_dir", "") or str(Path.home())
        d = QFileDialog.getExistingDirectory(self, "Choose a folder of videos", start)
        if d:
            self.add_paths([d])

    def add_paths(self, paths: list[str]):
        if self.busy():
            return
        files, skipped = batch.expand_paths(paths)
        if skipped:
            tell(self, "Picture subtitles can't be cleaned",
                 "These are picture-based subtitle files (Blu-ray or DVD), which can't be edited as text:",
                 rows=[(os.path.basename(p_), "") for p_ in skipped[:8]])
        if not files:
            if paths and not skipped:
                tell(self, "Nothing to add", "No video or subtitle files were found there.")
            return
        explicit = {p for p in paths if os.path.isfile(p)}
        known = {i.path for i in self.items if i.kind == "video" and i.pending}
        plan = batch.pair_up(files, explicit=explicit, embed=self.sub_mode == "embed", known_videos=known)
        files = plan.videos + plan.subtitles
        if not files:
            if plan.orphans:
                tell(self, "Nothing to add", "Only subtitle files without a matching video were found there.")
            return
        videos = list(plan.videos)
        if videos and not self.tools.ready:
            self.rescan_tools()
            if not self.tools.ready:
                tell(self, "FFmpeg is needed", "SubScrub needs FFmpeg to read video files. Set it up, then add them "
                     "again.")
                self.show_tools()
                files = [f for f in files if f not in videos] if not self.tools.ready else files
                if not files:
                    return
        self.settings.setValue("last_dir", os.path.dirname(files[0]))
        existing = {os.path.normcase(i.path): i for i in self.items}
        for f in files:
            old = existing.get(os.path.normcase(f))
            if old is not None:
                if old.pending:
                    old.dropped |= set(plan.dropped_with.get(f, []))
                    continue
                self._remove_item(old.uid, refresh=False)     # finished before: start it over
            it = Item(path=f, kind=batch.kind_for(f))
            it.dropped = set(plan.dropped_with.get(f, []))
            self.items.append(it)
            row = FileRow(it.uid, it.name, it.kind == "subtitle")
            row.clicked.connect(self.open_review)
            row.remove_clicked.connect(self._remove_item)
            self.rows[it.uid] = row
            self.files_box.insertWidget(self.files_box.count() - 1, row)
        if self.page() == self.PAGE_WELCOME:
            self.stack.setCurrentIndex(self.PAGE_FILES)
        self.footer.show()
        self._layout_files()
        self._pump_loader()
        self._update_buttons()
        paired = sum(len(v) for v in plan.dropped_with.values())
        bits = []
        if paired:
            bits.append(f"{plural(paired, 'subtitle file')} matched to {plural(len(plan.dropped_with), 'video')}")
        if plan.orphans:
            bits.append(f"{plural(len(plan.orphans), 'subtitle file')} without a matching video left alone")
        self.add_note = ("; ".join(bits) + ".") if bits else ""
        self._update_summary()

    def _layout_files(self):
        n = len(self.items)
        self.clear_btn.setVisible(n > 1)
        first = True
        for i in range(self.files_box.count()):
            w = self.files_box.itemAt(i).widget()
            if isinstance(w, FileRow):
                w.sep = not first
                first = False
                w.update()
        for it in self.items:
            self._update_row(it)
        self._sync_tabs()
        self._update_summary()

    def _remove_item(self, uid: int, refresh: bool = True):
        if self.busy():
            return
        it = self.item(uid)
        if it is None:
            return
        if self.loading_uid == uid and self.loader is not None:
            self.loader.cancel.set()
            self.loader.wait(3000)
            self.loader, self.loading_uid = None, None
        if it.tmpdir:
            shutil.rmtree(it.tmpdir, ignore_errors=True)
        self.items.remove(it)
        row = self.rows.pop(uid, None)
        if row:
            row.setParent(None)
            row.deleteLater()
        if not refresh:
            return
        if self.review_uid == uid:
            self.review_uid = None
            if self.page() == self.PAGE_REVIEW:
                self.stack.setCurrentIndex(self.PAGE_FILES)
        if not self.items:
            self._go_empty()
            return
        self._layout_files()
        self._pump_loader()
        self._update_buttons()

    def _go_empty(self):
        self.review_uid = None
        self.add_note = ""
        self._clear_tracks()
        self.stack.setCurrentIndex(self.PAGE_WELCOME)
        self.play_demo()
        self.footer.hide()
        self.setWindowTitle(APP_NAME)
        self._layout_files()
        self.set_status("")

    def clear_all(self):
        if self.busy():
            return
        for it in list(self.items):
            self._remove_item(it.uid, refresh=False)
        self._go_empty()

    # ------------------------------------------------------------------ background loading
    def _pump_loader(self):
        if self.busy() or (self.loader is not None and self.loader.isRunning()):
            return
        it = next((i for i in self.items if i.state == "queued"), None)
        if it is None:
            self._update_status_idle()
            return
        if it.kind == "subtitle":
            try:
                info, doc = media.probe_subtitle_file(it.path)
                it.info, it.docs = info, {0: doc}
                self._finish_load(it)
            except Exception as e:  # noqa: BLE001
                self._fail(it, f"couldn't read it ({e})")
            QTimer.singleShot(0, self._pump_loader)
            return
        if not self.tools.ready:
            return
        it.state = "probing"
        self._update_row(it)
        uid, tools = it.uid, self.tools
        w = Worker(lambda prog, cancel: media.probe(tools, it.path, self.log), self)
        w.ok.connect(lambda info, u=uid: self._probed(u, info))
        w.err.connect(lambda msg, u=uid: self._load_failed(u, msg))
        self.loader, self.loading_uid = w, uid
        w.start()
        self._update_status_idle()

    def _probed(self, uid: int, info: media.MediaInfo):
        it = self.item(uid)
        self.loader = None
        if it is None:
            self._pump_loader()
            return
        it.info = info
        if not any(t.text_based for t in info.subs):
            batch.attach_sidecars(it, self.log)
            self._finish_load(it)
            self._pump_loader()
            return
        it.state = "reading"
        it.tmpdir = tempfile.mkdtemp(prefix="subscrub_")
        self._update_row(it)
        if uid == self.review_uid:
            self.show_review(uid)
        tools, tmp = self.tools, it.tmpdir
        w = Worker(lambda prog, cancel: media.extract_subs(tools, info, info.subs, tmp, prog, cancel, self.log), self)
        w.progress.connect(lambda f, m, u=uid: self._load_progress(u, f))
        w.ok.connect(lambda paths, u=uid: self._extracted(u, paths))
        w.err.connect(lambda msg, u=uid: self._load_failed(u, msg))
        self.loader, self.loading_uid = w, uid
        w.start()

    def _load_progress(self, uid, frac):
        it = self.item(uid)
        if it and not self.busy():
            it.progress = frac
            self._update_row(it)
            self._update_status_idle()

    def _extracted(self, uid: int, paths: dict):
        it = self.item(uid)
        self.loader, self.loading_uid = None, None
        if it is None:
            self._pump_loader()
            return
        it.docs, it.doc_errors = load_docs(it.info, paths)
        for idx, err in it.doc_errors.items():
            self.log(f"{it.name}: track {idx}: {err}")
        batch.attach_sidecars(it, self.log)
        self._finish_load(it)
        self._pump_loader()

    def _finish_load(self, it: Item):
        batch.measure(it)
        if not it.manual or it.sel not in it.docs:
            it.sel = batch.pick_track(it, self.pref)
        it.state = "ready"
        it.progress = -1
        self._compute_result(it)
        self._update_row(it)
        if it.uid == self.review_uid:
            self.show_review(it.uid)
        self._update_summary()
        self._update_buttons()
        self._pump_names()

    def _load_failed(self, uid: int, msg: str):
        self.loader, self.loading_uid = None, None
        it = self.item(uid)
        if it is not None and msg != CANCELLED:
            self.log(f"Couldn't read {it.path}: {msg}")
            self._fail(it, "couldn't read this file")
            if uid == self.review_uid:
                self.show_review(uid)
        self._pump_loader()

    def _fail(self, it: Item, msg: str):
        it.state, it.message = "failed", msg
        self._update_row(it)
        self._update_buttons()

    # ------------------------------------------------------------------ analysis
    def _compute_result(self, it: Item):
        it.result = batch.clean_item(it, self.defaults) if it.sel is not None else None

    def _queue_recalc(self, it: Item):
        if it.uid not in self._recalc:
            self._recalc.append(it.uid)
        if not self._recalc_timer.isActive():
            self._recalc_timer.start()

    def _recalc_step(self):
        if not self._recalc:
            self._recalc_timer.stop()
            self._update_summary()
            self._update_buttons()
            return
        it = self.item(self._recalc.pop(0))
        if it is not None and it.loaded:
            self._compute_result(it)
            self._update_row(it)

    def verdict(self, it: Item) -> tuple[str, str, str, bool]:
        """(detail line, status text, status kind, show Review button) for a file."""
        st = it.state
        if st == "queued":
            return "Waiting", "", "", False
        if st == "probing":
            return "Opening", "", "", False
        if st == "reading":
            pct = f" {int(it.progress * 100)}%" if it.progress >= 0 else ""
            return f"Reading subtitles{pct}", "", "", False
        if st == "saving":
            return it.message or "Saving", "Saving", "busy", False
        if st == "done":
            out = os.path.basename(it.output) if it.output else ""
            same = it.output and os.path.normcase(it.output) == os.path.normcase(it.path)
            if it.done_text:
                return it.done_text, "Saved", "done", False
            what = "Cleaned track saved into the video" if same and it.kind == "video" else (
                "Cleaned and saved" if same or not out else f"Saved as {out}")
            return what, "Saved", "done", False
        if st == "failed":
            return it.message[:1].upper() + it.message[1:] if it.message else "Something went wrong", "Failed", \
                "failed", bool(it.info)
        tr = it.track()
        info = it.info
        if info is None:
            return "Couldn't read this file", "Failed", "failed", False
        has_tidy = self._has_tidy(it)
        tidy = self._tidy_note(it)
        if tr is None:
            if info.subs and not any(t.text_based for t in it.tracks()):
                return "Only picture-based subtitles (PGS or VobSub), which would need OCR", "Can't Edit", "dim", True
            if has_tidy:
                return (tidy[:1].upper() + tidy[1:]) if tidy else "No subtitles; only the name changes", \
                    "Ready", "ready", False
            return "No subtitles in this file or next to it", "Nothing to Do", "dim", False
        label_ = self.track_label(it, tr)
        if it.sel in it.doc_errors:
            return f"{label_}: couldn't read this track", "Failed", "failed", True
        if st == "skipped":
            return f"{label_}: {it.message}", "Skipped", "dim", True
        res = it.result
        if res is None:
            return label_, "", "", True
        if not it.force and not tr.external and not batch.is_sdh(it, tr):
            if has_tidy:
                return f"{label_}: regular subtitles" + (f", {tidy}" if tidy else ""), "Ready", "ready", True
            return f"{label_}: regular subtitles, nothing to strip", "No SDH", "dim", True
        n = final_changed(res)
        if n == 0 and not tr.external:
            if has_tidy:
                return f"{label_}: nothing to remove" + (f", {tidy}" if tidy else ""), "Ready", "ready", True
            return f"{label_}: nothing to remove", "Already Clean", "dim", True
        if it.locked:
            who = sorted({w for names in it.locked.values() for w in names})
            return (f"{label_}: open in {' and '.join(who)}, pause it there before saving",
                    "In Use", "check", True)
        bits = [label_]
        if tr.external and self.video_mode != "sidecar":
            bits.append("goes into the video" + (" and the .srt is removed" if self.recycle_sub
                                                  and self.video_mode == "replace" and not self.keep_sdh else ""))
        if it.opts is not None:
            bits.append("custom edits")
        if tidy:
            bits.append(tidy)
        if res.notes.ocr_suspect:
            bits.append("converted from a disc image")
        unresolved = len(res.unresolved())
        detail = ", ".join(bits)
        if unresolved == 0:
            return detail, "Ready", "ready", True
        if res.confidence == "low":
            return detail, "Look Closer", "low", True
        return detail, f"{unresolved} to Check", "check", True

    def row_count(self, it: Item) -> str:
        """The number column in the file list: how many lines change."""
        if it.state == "done":
            return f"{it.cleaned:,} cleaned" if it.cleaned else ""
        tr = it.track()
        if it.result is None or it.state not in ("ready", "failed", "skipped") or tr is None:
            return ""
        if not it.force and not tr.external and not batch.is_sdh(it, tr):
            return ""                                 # regular subtitles are skipped when saving: nothing is cleaned
        n = final_changed(it.result)
        return f"{n:,} to clean" if n else ""

    @staticmethod
    def track_label(it: Item, tr) -> str:
        if it.kind == "subtitle":
            return tr.lang_label
        if tr.external:
            name = os.path.basename(tr.external)
            stem = Path(it.path).stem
            return f"{tr.lang_label} from the {name[len(stem):]} file" if name.lower().startswith(stem.lower()) \
                else f"{tr.lang_label} from “{name}”"
        return tr.title or tr.lang_label

    def _update_row(self, it: Item):
        row = self.rows.get(it.uid)
        if row is None:
            return
        detail, chip, kind, review = self.verdict(it)
        prog = None
        if it.state == "reading":
            prog = it.progress
        elif it.state == "probing":
            prog = -1
        elif it.state == "saving":
            prog = it.progress if it.progress >= 0 else -1
        row.set_state(detail, chip, kind, prog, review and not self.busy(), self.row_count(it))
        row.set_busy(self.busy())
        line, tone = self.name_line(it)
        row.set_rename(line, tone)
        tip = it.path
        if it.state in ("done", "failed", "skipped") and it.message:
            tip += "\n\n" + it.message
        if it.output and it.output != it.path:
            tip += f"\nSaved as {it.output}"
        set_tip(row, tip)
        lines = (bool(line), prog is None, review and not self.busy())
        if getattr(row, "_lines", None) != lines:        # a line came or went: the card still fits its rows
            row._lines = lines
            QTimer.singleShot(0, self._fit_files)
        if it.uid == self.review_uid and self.page() == self.PAGE_REVIEW:
            self._update_review_head()

    def _update_summary(self):
        """Schedule one summary update for everything that changed in this go."""
        self._summary_timer.start()

    def _do_update_summary(self):
        QTimer.singleShot(0, self._fit_files)
        self._sync_tabs()
        if not self.items:
            self.summary.setText("")
            self.add_note_label.hide()
            return
        counts = {}
        for it in self.items:
            _, chip, kind, _ = self.verdict(it)
            key = {"ready": "ready", "check": "to check", "low": "to check", "done": "saved", "failed": "failed",
                   "busy": "saving"}.get(kind)
            if it.locked and it.state == "ready":
                key = "in use"
            if it.state in ("queued", "probing", "reading"):
                key = "reading"
            elif kind == "dim":
                key = "with nothing to do"
            if key:
                counts[key] = counts.get(key, 0) + 1
        order = ["ready", "to check", "in use", "reading", "saving", "with nothing to do", "saved", "failed"]
        parts = [f"{counts[k]} {k}" for k in order if k in counts]
        text = ", ".join(parts)
        if any(it.kind == "video" and it.name_state == "nokey" and it.pending for it in self.items):
            text += (". " if text else "") + "Add a TMDB key in Settings to rename videos."
        self.summary.setText(text.strip())
        note = self.add_note
        self.add_note_label.setText(note[:1].upper() + note[1:])
        self.add_note_label.setVisible(bool(note))
        how = {"replace": "Saving replaces each original, which goes to the Recycle Bin once the new file checks out. "
                          f"The cleaned track {'becomes the default' if self.make_default else 'is added'}"
                          f"{' and the SDH track is kept' if self.keep_sdh else ' and the SDH track is dropped'}.",
               "new": "Saving makes a new “.dialogue” copy next to each video.",
               "sidecar": "Saving writes a cleaned .srt next to each video."}[self.video_mode]
        if self.recycle_sub and self.video_mode == "replace" and any(i.sidecars for i in self.items):
            how += " Matching .srt files go into their video and are then removed."
        if any(i.kind == "subtitle" for i in self.items):
            how += f" Subtitle files on their own: {dict(SUB_MODES)[self.sub_mode].lower()}."
        self.save_how.setText(how)

    def _update_status_idle(self):
        if self.busy():
            return
        loading = [i for i in self.items if i.state in ("queued", "probing", "reading")]
        if loading and self.tools.ready:
            done = len([i for i in self.items if i.info is not None])
            self.set_status(f"Reading files… {done} of {len(self.items)}")
            cur = next((i for i in loading if i.state == "reading" and i.progress >= 0), None)
            self.progress.setVisible(True)
            if cur:
                self.progress.setRange(0, 1000)
                self.progress.setValue(int(cur.progress * 1000))
            else:
                self.progress.setRange(0, 0)
        elif loading:
            self.progress.hide()
            self.set_status("FFmpeg is needed to read the videos: click “FFmpeg Needed” at the top.", "bad")
        else:
            self.progress.hide()
            if not self.status.property("tone"):
                self.set_status("")
            if self.items:
                self._lock_timer.start()
        self._update_summary()

    def set_status(self, text: str, tone: str = ""):
        self.status.setText(text)
        set_prop(self.status, "tone", tone)

    # ------------------------------------------------------------------ files other programs have open
    def files_touched(self, it: Item) -> list[str]:
        """Files a save would rewrite or remove for this item (the ones that mustn't be open elsewhere)."""
        if it.kind == "video":
            sidecars = [t.external for t in it.sidecars] if it.sidecars else media.find_sidecars(it.path)
            if self.video_mode == "replace":
                return [it.path] + (sidecars if self.recycle_sub else [])
            if self.video_mode == "sidecar":
                return sidecars if self.recycle_sub else []
            return []
        comp = it.info.companion if it.info else media.find_matching_video(it.path)
        if self.sub_mode == "embed" and comp:
            return [comp] + ([it.path] if self.recycle_sub else [])
        return [it.path] if self.sub_mode == "replace" else []

    def check_locks(self, force: bool = False):
        """Look (in the background) for files that are open in other programs, e.g. a torrent client seeding them."""
        if self.busy() or (self._lock_thread and self._lock_thread.isRunning()):
            return
        if not force and any(i.state in ("queued", "probing", "reading") for i in self.items):
            return
        pending = [i for i in self.items if i.pending]
        paths = [p for i in pending for p in self.files_touched(i)]
        if not paths:
            self._apply_locks({})
            return
        th = LockCheck(paths, self)
        th.done.connect(self._apply_locks)
        self._lock_thread = th
        th.start()

    def _apply_locks(self, found: dict):
        for it in self.items:
            mine = {p: found[p] for p in self.files_touched(it) if p in found} if it.pending else {}
            if mine != it.locked:
                it.locked = mine
                self._update_row(it)
        locked = [i for i in self.items if i.locked]
        if locked:
            who = sorted({w for i in locked for names in i.locked.values() for w in names})
            self.lock_banner.label.setText(
                f"{plural(len(locked), 'file')} {'is' if len(locked) == 1 else 'are'} open in {' and '.join(who)}. "
                "If you're seeding, pause those torrents before saving: SubScrub can't rewrite a file another "
                "program has open. This updates when you come back to this window.")
        self.lock_banner.setVisible(bool(locked))
        self.lock_gap.setVisible(bool(locked))
        self._update_summary()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.ActivationChange:
            if not self.isActiveWindow():
                hide_tip()
            elif self.items and not self.busy():
                self._lock_timer.start()

    # ------------------------------------------------------------------ pages
    def show_files(self):
        self.stack.setCurrentIndex(self.PAGE_FILES if self.items else self.PAGE_WELCOME)
        self.setWindowTitle(APP_NAME)
        for it in self.items:
            self._update_row(it)
        self._sync_tabs()
        self._update_summary()
        self._update_buttons()

    def open_review(self, uid: int):
        if self.busy():
            return
        self.show_review(uid)

    def step_file(self, d: int):
        if not self.items or self.cur is None:
            return
        k = self.items.index(self.cur)
        self.show_review(self.items[(k + d) % len(self.items)].uid)

    def _clear_tracks(self):
        while self.tracks_box.count():
            w = self.tracks_box.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.cards = {}

    def show_review(self, uid: int | None):
        it = self.item(uid)
        if it is None:
            return
        first = self.review_uid != uid or self.page() != self.PAGE_REVIEW
        self.review_uid = uid
        self.stack.setCurrentIndex(self.PAGE_REVIEW)
        self._sync_tabs()
        self.setWindowTitle(f"{it.name} – {APP_NAME}")
        it.reviewed = True
        self._clear_tracks()
        info = it.info
        self.tracks_note.hide()
        if info is None:
            self.file_meta.setText("Couldn't read this file." if it.state == "failed" else "Opening…")
            self.track_count.setText("")
        else:
            if it.kind == "subtitle":
                n = len(it.docs.get(0).cues) if it.docs.get(0) else 0
                bits = [f"{info.format_name.upper()} subtitle file", f"{n:,} lines"]
                bits.append(f"belongs to “{os.path.basename(info.companion)}”" if info.companion
                            else "no matching video in its folder")
            else:
                bits = [info.kind.upper() if info.kind != "other" else Path(info.path).suffix.upper().lstrip(".")]
                if info.duration:
                    bits.append(fmt_duration(info.duration))
                bits.append(fmt_size(info.size))
            self.file_meta.setText(", ".join(bits))
            alltr = it.tracks()
            self.track_count.setText(plural(len(alltr), "track") if alltr else "")
            plain = batch.plain_partner(it)
            for tr in alltr:
                c = TrackCard(tr)
                c.clicked.connect(self.select_track)
                if tr.text_based and it.state == "reading":
                    c.set_stat("Reading…")
                elif tr.text_based and tr.index in it.docs:
                    n = it.sdh_counts.get(tr.index, 0)
                    sdh = batch.is_sdh(it, tr)
                    c.mark_sdh(sdh)
                    if tr.index in it.doc_errors:
                        c.set_stat("Couldn't read", "warn")
                    elif sdh:
                        c.set_stat(f"{plural(n, 'sound or name note')}", "accent" if tr.index == it.sel else "")
                    elif tr.index == plain:
                        c.set_stat("Regular, used as a cross-check", "dim")
                    else:
                        c.set_stat("Regular subtitles", "dim")
                c.set_selected(tr.index == it.sel)
                self.cards[tr.index] = c
                self.tracks_box.addWidget(c)
            text_tracks = [t for t in alltr if t.text_based]
            if it.sidecars:
                self._note("Subtitle files next to the video are listed too. Whichever track gets cleaned "
                           "ends up inside the video" +
                           ("; afterwards, files in the same language next to it go to the Recycle Bin so the "
                            "video is a single file." if self.recycle_sub and self.video_mode == "replace" else "."))
            if not alltr:
                self._note("This file has no subtitle tracks, and there are no subtitle files next to it.")
            elif not text_tracks:
                self._note("All of this file's subtitles are picture-based (Blu-ray PGS or DVD VobSub). "
                           "They'd need OCR to become editable text, which SubScrub doesn't do yet.")
            elif len([i for i in self.items if i.kind == "video"]) > 1 and it.kind == "video" and \
                    len(text_tracks) > 1 and not it.sidecars:
                self._note("Picking a different track here picks the matching track in the other files too.")
        self._sync_rule_rows(it)
        if first:
            unresolved = it.result.unresolved() if it.result else []
            self.filter.set_value("check" if unresolved else
                                  (self.preview_filter if self.preview_filter != "check" else "changes"))
            self._preview_key = None
        self.render_review()
        self._update_buttons()
        QTimer.singleShot(0, self._fit_side)

    def _note(self, text):
        self.tracks_note.setText(text)
        self.tracks_note.show()

    def _fit_side(self):
        """The sidebar card is as tall as what's in it (it scrolls once that doesn't fit)."""
        lay = self.side_inner.layout()
        lay.activate()
        h = lay.sizeHint().height() + 2
        avail = self.stack.height() - 28
        self.left.setFixedHeight(max(160, min(h, avail)))

    def _update_review_head(self):
        it = self.cur
        if it is None:
            return
        self.review_title.setText(it.name)
        self.review_title.set_tip(it.path)
        detail, chip, kind, _ = self.verdict(it)
        count = self.row_count(it)
        text = detail.rstrip(".") + (f", {count.replace('to clean', 'lines to clean')}" if count else "")
        notes = list(it.result.notes.notes) if it.result else []
        if notes:
            text += ". " + " ".join(notes)
        self.review_detail.setText(text)
        self.review_status.set(chip, kind)
        multi = len(self.items) > 1
        self.prev_file.setVisible(multi)
        self.next_file.setVisible(multi)
        tr = it.track()
        nosdh = bool(tr) and it.loaded and not it.force and not tr.external and not batch.is_sdh(it, tr)
        self.nosdh_wrap.setVisible(nosdh)
        self.preview.set_preview_only(nosdh)
        if nosdh:
            self.nosdh_banner.label.setText(
                "This file only has regular subtitles, with no sound descriptions or speaker names to strip. " +
                ("Saving only renames it and tidies its tracks; the subtitles stay as they are." if self._has_tidy(it)
                 else "It's skipped when saving."))
        line, tone = self.name_line(it)
        can = it.kind == "video" and self._renaming() and bool(self.tmdb_key) and it.name_state == "done" \
            and it.pending and it.match is not None and bool(it.match.choices or it.match.parsed.title)
        self.name_row.setVisible(can)
        if can:
            self.name_label.setText(line or "The name is already right")
            themed_icon(self.name_icon, "alert" if tone == "alert" else "arrow-right",
                        "warn" if tone == "alert" else "text2", 15)

    def select_track(self, index: int):
        it = self.cur
        if it is None or self.busy():
            return
        if it.sel != index:
            it.overrides = {}
        it.sel, it.manual = index, True
        tr = it.track()
        if tr and it.kind == "video":
            self.pref = batch.pref_from(tr)
            for other in self.items:
                if other is it or other.manual or not other.loaded or other.kind != "video":
                    continue
                new = batch.pick_track(other, self.pref)
                if new != other.sel:
                    other.sel, other.overrides = new, {}
                    self._queue_recalc(other)
        it.force = bool(tr) and not tr.external and not batch.is_sdh(it, tr)   # picked a regular track on purpose
        self._compute_result(it)
        self._update_row(it)
        self.show_review(it.uid)

    def force_clean(self):
        it = self.cur
        if it is None or self.busy():
            return
        it.force = True
        if it.state == "skipped":
            it.state, it.message = "ready", ""
        self._compute_result(it)
        self._update_row(it)
        self._update_summary()
        self.render_review()
        self._update_buttons()

    # ------------------------------------------------------------------ per-file edits
    def _sync_rule_rows(self, it: Item):
        o = self.opts_for(it)
        for key, r in self.rule_rows.items():
            r.blockSignals(True)
            r.switch.blockSignals(True)
            r.setChecked(getattr(o, key))
            r.switch.blockSignals(False)
            r.blockSignals(False)
        self.lyrics_seg.set_value(lyric_mode(o))
        self.reset_rules_btn.setVisible(it.opts is not None)
        self.make_default_btn.setVisible(it.opts is not None)
        self.edits_state.setText("Changed for this file" if it.opts is not None else "Your defaults")

    def _review_rule_opts(self) -> CleanOptions:
        o = CleanOptions(**{k: r.isChecked() for k, r in self.rule_rows.items()})
        return with_lyric_mode(o, self.lyrics_seg.value())

    def _apply_review_rules(self):
        it = self.cur
        if it is None or not it.loaded:
            return
        o = self._review_rule_opts()
        it.opts = None if o == self.defaults else o
        self._compute_result(it)
        self._sync_rule_rows(it)
        self._update_row(it)
        self.render_review()
        self._update_summary()

    def promote_review_rules(self):
        it = self.cur
        if it is None or it.opts is None:
            return
        self.defaults = it.opts
        it.opts = None
        self._save_prefs()
        for other in self.items:
            if other.opts is None and other.loaded:
                self._queue_recalc(other)
        self._sync_rule_rows(it)
        self.set_status("Saved as your default edits for every file.", "good")

    def reset_review_rules(self):
        it = self.cur
        if it is None:
            return
        it.opts = None
        self._compute_result(it)
        self._sync_rule_rows(it)
        self._update_row(it)
        self.render_review()

    # ------------------------------------------------------------------ line decisions
    def toggle_line(self, cue: int):
        it = self.cur
        if it is None or it.result is None or self.busy():
            return
        r = it.result.results[cue]
        if not r.changed:
            return
        if it.overrides.get(cue) == "orig":
            if r.risk >= 2:
                it.overrides[cue] = "clean"            # an explicit "yes, clean it" resolves the flag
            else:
                it.overrides.pop(cue, None)
        else:
            it.overrides[cue] = "orig"
        it.result.overrides = it.overrides
        self._update_row(it)
        self._update_summary()
        # the quick path: only that row, its timeline mark and the counts change
        if self.preview_stack.currentIndex() == 1 and self.preview.update_cue(it.result, cue):
            self._update_legend(it)
            self._update_nav()
            return
        self.render_review(keep_cue=cue)

    def toggle_current_line(self):
        if self.page() != self.PAGE_REVIEW:
            return
        row = self.preview.current_row()
        if row is not None:
            self.toggle_line(row.cue)

    # ------------------------------------------------------------------ preview
    def _filter_changed(self, key):
        if key != "check":
            self.preview_filter = key
            self.settings.setValue("preview_filter", key)
        self._preview_key = None
        self.render_review()

    def _update_legend(self, it: Item) -> int:
        """The counts next to the filter. Returns how many lines are judgement calls (flagged)."""
        res = it.result
        st = res.stats
        unresolved = len(res.unresolved())
        kept_by_user = sum(1 for v in it.overrides.values() if v == "orig")
        items = []
        if unresolved:
            items.append(("btn", f"{unresolved:,} to check", True))
        items += [("removed", f"{st.removed:,} removed", False), ("trim", f"{st.edited:,} trimmed", False)]
        untouched = f"{st.total - st.changed:,} untouched"
        if kept_by_user:
            untouched += f", {kept_by_user} kept by you"
        items.append(("mute_line", untouched, False))
        self.legend.set_items(items)
        return len(res.flagged())

    def render_review(self, keep_cue: int | None = None):
        if not hasattr(self, "preview"):
            return
        self.legend.set_items([])
        self._set_nav_visible(False)
        it = self.cur
        if it is None:
            return
        self._update_review_head()
        o = self.opts_for(it)
        res = it.result
        counts = {}
        if res is not None:
            for key, _, _, rules in RULES:
                counts[key] = sum(res.stats.by_rule.get(rr, 0) for rr in rules) if getattr(o, key) else None
            n_lyr = res.stats.by_rule.get("lyrics", 0) + res.stats.by_rule.get("lyric_style", 0)
            self.lyric_count.setText(f"{n_lyr:,}" if n_lyr else "")
        for key, r in self.rule_rows.items():
            r.set_count(counts.get(key))
        self.rule_rows["ocr_fixes"].setVisible(bool(res and res.notes.ocr_suspect))
        self.rule_rows["quote_italics"].setVisible(bool(res and res.notes.quote_voices))
        self.rule_rows["mixed_case_labels"].setVisible(not (res and res.notes.mixed_names))
        QTimer.singleShot(0, self._fit_side)

        if it.info is None or it.state in ("queued", "probing", "reading"):
            self._empty("Couldn't read this file." if it.state == "failed" else "Reading subtitles…")
            return
        tr = it.track()
        if tr is None:
            self._empty("No editable subtitles in this file.")
            return
        if tr.index in it.doc_errors:
            self._empty("SubScrub couldn't read this track.\n" + it.doc_errors[tr.index][:300])
            return
        if res is None:
            self._empty("Reading subtitles…")
            return
        st = res.stats
        flagged = self._update_legend(it)
        self.filter.set_option_visible("check", flagged > 0)
        QTimer.singleShot(0, self._fit_toolbar)
        show = self.filter.value()
        if show == "check" and not flagged:
            show = "changes"
            self.filter.set_value(show)
        if st.changed == 0 and show != "all":
            self._empty("Nothing to remove in this track. It's already dialogue-only.\n"
                        "Switch to Everything to read through it.")
            return
        key = (it.uid, show)
        same = self._preview_key == key
        self._preview_key = key
        self.preview.set_result(res, show, it.info.duration, keep_position=same)
        if keep_cue is not None:
            self.preview.select_cue(keep_cue)
        self._set_nav_visible(st.changed > 0)
        self.preview_stack.setCurrentIndex(1)
        self._update_nav()

    def _set_nav_visible(self, on: bool):
        for w in (self.prev_btn, self.next_btn, self.nav_count, self.undo_btn):
            w.setVisible(on)

    def _update_nav(self):
        k, n = self.preview.nav_state()
        self.nav_count.setText(f"{k:,} of {n:,}" if k else f"{n:,} changes")
        row = self.preview.current_row()
        ok = row is not None and row.status != "kept"
        self.undo_btn.setEnabled(ok)
        self._undo_text = " Use Cleaned" if ok and row.override == "orig" else " Keep Original"
        self._apply_undo_text()

    def _apply_undo_text(self):
        b = self.undo_btn
        label = self._undo_text.strip()
        b.setText("" if self._undo_compact else self._undo_text)
        what = ("Put the selected line back the way it was" if label == "Keep Original"
                else "Use the cleaned text for the selected line again")
        set_tip(b, (f"{label}: {what[0].lower()}{what[1:]}" if self._undo_compact else what) +
                " (Ctrl+Z, or double-click a line)")

    def _fit_toolbar(self):
        """On a narrow window the line button shows just its icon, so the filter is never cut off."""
        need = (self.filter.sizeHint().width() + 132 + self.prev_btn.width() * 2 + 78 + 4 + 14 * 3 + 90)
        compact = self.review_main.width() < need
        if compact == self._undo_compact:
            return
        self._undo_compact = compact
        b = self.undo_btn
        set_prop(b, "iconOnly", compact)
        if compact:
            b.setFixedWidth(32)
        else:
            b.setMinimumWidth(132)
            b.setMaximumWidth(16777215)
        self._apply_undo_text()

    def _empty(self, text):
        self.preview_empty.setText(text)
        self.preview_stack.setCurrentIndex(0)

    # ------------------------------------------------------------------ saving
    def _update_buttons(self):
        if not hasattr(self, "save_btn"):
            return
        busy = self.busy()
        pending = [i for i in self.items if i.pending]
        n = len(pending)
        if len(self.items) <= 1:
            it = self.items[0] if self.items else None
            text = "Saved" if it and it.state == "done" else "Clean and Save"
        else:
            text = f"Clean and Save {n} Files" if n > 1 else "Clean and Save"
        self.save_btn.setText(text)
        self.save_btn.setVisible(n > 0 and not busy)        # nothing left to save: no greyed-out "All done"
        needs_ffmpeg = any(i.kind == "video" or self.sub_mode == "embed" for i in pending)
        self.save_btn.setEnabled(n > 0 and not busy and (self.tools.ready or not needs_ffmpeg))
        it = self.cur
        one = (self.page() == self.PAGE_REVIEW and it is not None and len(self.items) > 1 and it.pending
               and not busy)
        self.save_one_btn.setVisible(one)
        self.left.setEnabled(not busy)
        self._sync_tabs()
        self.footer.update()

    def start_save(self, only: int | None = None):
        if self.busy() or not self.save_btn.isEnabled() and only is None:
            return
        scope = [i for i in self.items if i.pending and (only is None or i.uid == only)]
        if not scope:
            return
        # first of all: files another program has open (a torrent client seeding them, a player, Jellyfin…)
        while True:
            paths = [p for i in scope for p in self.files_touched(i)]
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                found = media.who_has_open(paths)
            finally:
                QApplication.restoreOverrideCursor()
            self._apply_locks(found)
            locked = [i for i in scope if i.locked]
            if not locked:
                break
            dlg = LockDialog(self, {p: n for i in locked for p, n in i.locked.items()}, locked)
            if dlg.exec() != QDialog.Accepted or not dlg.choice:
                return
            if dlg.choice == "skip":
                scope = [i for i in scope if not i.locked]
                if not scope:
                    return
                break
        if self._renaming() and self.tmdb_key and any(
                i.kind == "video" and i.name_state in ("", "looking") for i in scope):
            tell(self, "Still looking up titles",
                 "SubScrub is still finding the titles for the new names. Try again in a moment.")
            return
        loaded = [i for i in scope if i.state == "ready" and i.result is not None]
        unseen = [i for i in scope if i.state in ("queued", "probing", "reading")]
        sdh_ok = lambda i: i.force or (i.track() is not None and (bool(i.track().external) or batch.is_sdh(i, i.track())))
        flagged = [i for i in loaded if sdh_ok(i) and i.result.unresolved()]
        single = len(scope) == 1

        # nothing useful to do for a lone file? say so instead of silently skipping
        if single and loaded:
            it = loaded[0]
            if not sdh_ok(it) and not self._has_tidy(it):
                choice, _ = ask(self, "Clean it anyway?",
                                f"“{it.name}” only has regular subtitles: there are no sound descriptions or speaker "
                                "names to strip.",
                                [("cancel", "Cancel", ""), ("go", "Clean Anyway", "primary")],
                                "Cleaning it anyway removes dialogue dashes and restyles song lyrics only.")
                if choice != "go":
                    return
                it.force = True
                self._compute_result(it)
                flagged = [it] if it.result and it.result.unresolved() else []
            elif sdh_ok(it) and final_changed(it.result) == 0 and not it.track().external and not self._has_tidy(it):
                tell(self, "Nothing to remove", "This track has nothing to clean with the current edits.")
                return

        policy = "hold_new"                       # files read during the save hold if they turn out uncertain
        skip_uids = set()
        if flagged:
            dlg = DecisionDialog(self, flagged, len([i for i in scope if i not in flagged]), len(unseen))
            if dlg.exec() != QDialog.Accepted or not dlg.choice:
                return
            if dlg.choice == "review":
                self.show_review(flagged[0].uid)
                self.filter.set_value("check")
                self._preview_key = None
                self.render_review()
                return
            if dlg.choice == "skip":
                skip_uids = {i.uid for i in flagged}
            else:
                policy = dlg.choice                 # "guess" | "orig" – also used for files read during the save
        jobs_items = [i for i in scope if i.uid not in skip_uids]
        if not jobs_items:
            return

        unsure = [i for i in jobs_items if self._renaming() and batch.name_unsure(i)]
        if unsure:
            choice, _ = ask(self, "Not sure of some titles",
                            f"SubScrub couldn't tell which title {plural(len(unsure), 'file')} "
                            f"{'is' if len(unsure) == 1 else 'are'}:",
                            [("cancel", "Cancel", ""), ("keep", "Keep Their Names", ""),
                             ("pick", "Choose Titles…", "primary")],
                            rows=[(i.name, "") for i in unsure[:6]], more=max(0, len(unsure) - 6))
            if choice == "pick":
                for i in unsure:
                    dlg = NameDialog(self, i, self._api(), self.name_year)
                    if dlg.exec() != QDialog.Accepted or dlg.pick is None:
                        return
                    i.name_pick = dlg.pick
                    self._update_row(i)
            elif choice == "keep":
                for i in unsure:
                    i.name_pick = "keep"
                    self._update_row(i)
            else:
                return

        renames = [i for i in jobs_items if self._new_name(i)]
        rewrites = [i for i in jobs_items if (i.kind == "video" and self.video_mode == "replace")
                    or (i.kind == "subtitle" and self.sub_mode == "embed" and i.info and i.info.companion)]
        if rewrites and self.confirm_replace:
            n = len(rewrites)
            what = f"“{rewrites[0].name}”" if n == 1 else f"{n} files"
            choice, dont_ask = ask(
                self, "Rewrite in place?" if n == 1 else f"Rewrite {n} files in place?",
                f"SubScrub will rewrite {what} with the cleaned subtitles"
                f"{' as the default track' if self.make_default else ''}"
                f"{self._rewrite_extras(renames, jobs_items)}. "
                "Each new file is checked before the old one goes to the Recycle Bin.",
                [("cancel", "Cancel", ""), ("go", "Rewrite" if n == 1 else f"Rewrite {n} Files", "primary")],
                "Very large files may be too big for the Recycle Bin, in which case Windows deletes them instead.",
                checkbox="Don't ask again")
            if choice != "go":
                return
            if dont_ask:
                self.confirm_replace = False
                self._save_prefs()
        overwrite = True
        if self.video_mode == "sidecar":
            clashes = []
            for i in jobs_items:
                tr = i.track()
                if i.kind == "video" and i.info and tr:
                    p = media.output_path(i.info, media.SaveOptions(mode="sidecar", language=tr.language), tr.fmt)
                    if p.exists():
                        clashes.append(p.name)
            if clashes:
                choice, _ = ask(self, "Overwrite subtitle files?", "These already exist:",
                                [("cancel", "Cancel", ""), ("no", "Keep Both", ""), ("yes", "Overwrite", "primary")],
                                "Keep Both saves the new ones under new names.",
                                rows=[(c, "") for c in clashes[:8]], more=max(0, len(clashes) - 8))
                if choice not in ("yes", "no"):
                    return
                overwrite = choice == "yes"

        # hand everything to one worker; stop background loading so they don't fight over the disk
        if self.loader is not None and self.loader.isRunning():
            self.loader.cancel.set()
            self.loader.wait(5000)
        self.loader, self.loading_uid = None, None
        jobs = []
        for i in jobs_items:
            if i.state in ("probing", "reading"):
                if i.tmpdir:
                    shutil.rmtree(i.tmpdir, ignore_errors=True)
                i.state, i.info, i.docs, i.tmpdir = "queued", None, {}, None
            i.message = ""
            jobs.append({"uid": i.uid, "path": i.path, "kind": i.kind, "info": i.info, "docs": i.docs,
                         "sdh_counts": i.sdh_counts, "sel": i.sel, "manual": i.manual, "opts": i.opts,
                         "overrides": i.overrides, "force": i.force or (single and i.kind == "subtitle"),
                         "sidecars": i.sidecars, "dropped": i.dropped,
                         "policy": policy, "new_name": self._new_name(i)})
        cfg = {"opts": self.defaults, "video_mode": self.video_mode, "sub_mode": self.sub_mode,
               "keep": self.keep_sdh, "default": self.make_default, "name": self.track_name,
               "recycle": self.recycle_sub, "overwrite": overwrite, "pref": self.pref,
               "drop_langs": self.drop_langs and self.video_mode == "replace", "keep_lang": "eng",
               "batch_id": time.strftime("%Y%m%d-%H%M%S")}
        self.log(f"Saving {len(jobs)} file(s): video mode={self.video_mode}, subtitle mode={self.sub_mode}, "
                 f"keep SDH={cfg['keep']}, default={cfg['default']}, uncertain lines={policy}")
        w = BatchWorker(jobs, cfg, self.tools, self.log, self)
        w.started_item.connect(self._b_started)
        w.item_loaded.connect(self._b_loaded)
        w.item_progress.connect(self._b_progress)
        w.item_done.connect(self._b_done)
        w.item_skipped.connect(self._b_skipped)
        w.item_held.connect(self._b_held)
        w.item_failed.connect(self._b_failed)
        w.all_done.connect(self._b_all_done)
        self.batch_worker = w
        self.batch_total, self.batch_pos = len(jobs), 0
        self.batch_counts = {"done": 0, "skipped": 0, "failed": 0, "held": 0}
        if self.page() == self.PAGE_REVIEW and len(self.items) > 1:
            self.show_files()
        self._set_busy(True)
        self.set_status("Starting…")
        w.start()

    def cancel_save(self):
        if self.busy():
            self.batch_worker.cancel.set()
            self.set_status("Stopping…")

    def _set_busy(self, on: bool):
        self._saving = on
        self.cancel_btn.setVisible(on)
        self.progress.setVisible(on)
        if on:
            self.progress.setRange(0, 1000)
            self.progress.setValue(0)
            self.open_btn.hide()
        for it in self.items:
            self._update_row(it)
        self._update_buttons()

    def _b_started(self, uid, k):
        self.batch_pos = k
        it = self.item(uid)
        if it:
            it.state, it.message, it.progress = "saving", "Starting…", -1
            self._update_row(it)
            row = self.rows.get(uid)
            if row and self.page() == self.PAGE_FILES:
                self.files_scroll.ensureWidgetVisible(row)
        self._b_overall(0.0, it.name if it else "")

    def _b_overall(self, frac: float, text: str):
        n = max(1, self.batch_total)
        self.progress.setRange(0, 1000)
        self.progress.setValue(int((self.batch_pos + max(0.0, min(1.0, frac))) / n * 1000))
        prefix = f"{self.batch_pos + 1} of {self.batch_total}:  " if self.batch_total > 1 else ""
        self.set_status(prefix + text)

    def _b_loaded(self, uid, payload: dict):
        it = self.item(uid)
        if it is None:
            return
        it.info, it.docs = payload["info"], payload["docs"]
        it.sidecars = payload.get("sidecars", it.sidecars)
        it.sdh_counts = payload.get("sdh_counts", {})
        it.doc_errors = payload.get("errors", {})
        if payload.get("tmpdir"):
            it.tmpdir = payload["tmpdir"]
        if not it.manual or it.sel not in it.docs:
            it.sel = batch.pick_track(it, self.pref)
        self._compute_result(it)

    def _b_progress(self, uid, frac, msg):
        it = self.item(uid)
        if it is None:
            return
        pct = f" {int(frac * 100)}%" if frac >= 0 else ""
        it.message = f"{msg}{pct}"
        it.progress = frac
        self._update_row(it)
        self._b_overall(frac if frac >= 0 else 0.0, f"{it.name}, {msg.lower()}{pct}")

    def _b_done(self, uid, payload: dict):
        it = self.item(uid)
        self.batch_counts["done"] += 1
        if it is None:
            return
        it.state, it.output, it.message = "done", payload["path"], payload["note"]
        it.done_text = self._done_text(it, payload)
        it.sel, it.cleaned = payload["sel"], payload["changed"]
        it.progress = -1
        self.last_output = payload["path"]
        if it.kind == "video" and os.path.normcase(payload["path"]) != os.path.normcase(it.path) \
                and self.video_mode == "replace":
            it.path = payload["path"]                # it was renamed: the row follows the file
            row = self.rows.get(uid)
            if row:
                row.set_name(it.name)
        self.log(f"Saved {payload['path']}: {payload['changed']} lines cleaned. {payload['note']}")
        self._update_row(it)

    def _rewrite_extras(self, renames: list, items: list) -> str:
        bits = []
        if renames:
            bits.append(f"rename {plural(len(renames), 'file')}")
        drops = sum(1 for i in items if self._lang_drops(i))
        if drops:
            bits.append(f"remove other-language subtitles from {plural(drops, 'file')}")
        return (", and " + " and ".join(bits)) if bits else ""

    def _done_text(self, it: Item, payload: dict) -> str:
        bits = []
        if payload.get("changed"):
            bits.append("Cleaned track saved into the video")
        if os.path.normcase(payload["path"]) != os.path.normcase(it.path):
            bits.append(f"renamed from {os.path.basename(it.path)}")   # the row already shows the new name
        if "Removed" in payload.get("note", "") and "other languages" in payload.get("note", ""):
            bits.append("other languages removed")
        text = ", ".join(bits)
        return text[:1].upper() + text[1:]

    def _b_skipped(self, uid, why):
        it = self.item(uid)
        self.batch_counts["skipped"] += 1
        if it:
            it.state, it.message = "skipped", why
            self._update_row(it)
            self.log(f"Skipped {it.path}: {why}")

    def _b_held(self, uid, why):
        it = self.item(uid)
        self.batch_counts["held"] += 1
        if it:
            it.state, it.message = "ready", ""
            self._update_row(it)
            self.log(f"Held for review {it.path}: {why}")

    def _b_failed(self, uid, msg):
        it = self.item(uid)
        if msg == CANCELLED:
            if it:
                it.state, it.message = ("ready" if it.info else "queued"), ""
                self._update_row(it)
            return
        self.batch_counts["failed"] += 1
        if it:
            it.state, it.message = "failed", msg
            self._update_row(it)
            self.log(f"Failed {it.path}: {msg}")

    def _b_all_done(self, cancelled: bool):
        self.batch_worker = None
        self._set_busy(False)
        self._pump_loader()                      # resume reading anything still queued (before the summary)
        self._pump_names()
        c = self.batch_counts
        parts = [f"{c['done']} saved"] if c["done"] else []
        if c["held"]:
            parts.append(f"{c['held']} waiting for you to check")
        if c["skipped"]:
            parts.append(f"{c['skipped']} skipped")
        if c["failed"]:
            parts.append(f"{c['failed']} failed")
        summary = ", ".join(parts) or "nothing saved"
        if cancelled:
            self.set_status(f"Stopped: {summary}.", "bad" if c["failed"] else "")
        elif c["failed"]:
            self.set_status(f"Finished with problems: {summary}. Hover a file to see why.", "bad")
        else:
            single = self.batch_total == 1 and c["done"] == 1
            it = next((i for i in self.items if i.state == "done" and i.output == self.last_output), None) \
                if single else None
            self.set_status(f"Saved “{os.path.basename(it.output)}”. {it.message}".strip() if it
                            else f"Done: {summary}.", "good" if c["done"] else "")
        if self.last_output:
            self.open_btn.show()
        if c["failed"] and self.batch_total == 1:
            it = next((i for i in self.items if i.state == "failed"), None)
            if it:
                tell(self, "Couldn't save", it.message[:1200], extra=("Show Log", self.show_log))
        for it in self.items:
            self._update_row(it)
        self._update_summary()
        if self.page() == self.PAGE_REVIEW and self.cur:
            self.show_review(self.review_uid)
        self._update_buttons()

    # ------------------------------------------------------------------ lifecycle
    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.veil.isVisible():
            self.veil.setGeometry(self.root.rect())
        if self.page() == self.PAGE_REVIEW:
            QTimer.singleShot(0, self._fit_side)
            QTimer.singleShot(0, self._fit_toolbar)

    def closeEvent(self, e):
        if self.busy():
            choice, _ = ask(self, "Still saving", "Files are still being written. Stop and discard the one in "
                            "progress?", [("cancel", "Keep Saving", ""), ("stop", "Stop and Close", "danger")])
            if choice != "stop":
                e.ignore()
                return
            self.batch_worker.cancel.set()
            self.batch_worker.wait(15000)
        if self.loader is not None and self.loader.isRunning():
            self.loader.cancel.set()
            self.loader.wait(3000)
        for it in self.items:
            if it.tmpdir:
                shutil.rmtree(it.tmpdir, ignore_errors=True)
        self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(e)
