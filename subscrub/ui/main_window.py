"""The SubScrub main window.

Two screens:
  * Files  – drop files, each gets a status (Ready / Check / Saved…). One button saves them all.
  * Review – one file up close: which track, which kinds of edits, and a before/after preview where
             judgement calls are flagged and any line can be put back the way it was.
Everything that is set once (how to save, theme, default edits) lives in Settings.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout,
                               QWidget)

from .. import APP_NAME, __version__, batch, media, naming, subformats as sf
from ..batch import Item
from ..cleaner import CleanOptions
from . import glass, theme
from .preview import PreviewPanel
from .widgets import (two_line_button, FileRow, Legend, OptionRow, RuleRow, ScreenView, Segmented, StatusTag, TrackCard, card, draw_icon,
                      icon, logo_pixmap, set_prop)

CANCELLED = "__cancelled__"

# Edit types shown in Review (and as defaults in Settings): option key, title, example, rule ids counted
RULES = [
    ("brackets", "Sounds in [brackets]", "[door slams], [Grace] Hi.", ("brackets",)),
    ("parentheses", "Sounds in (parentheses)", "(sighs), (ON PHONE)", ("parentheses",)),
    ("speaker_labels", "Speaker names", "JOHN: Get down!  →  Get down!", ("labels",)),
    ("music_cues", "Music cues", "♪ tense music playing ♪, ♪♪", ("music",)),
    ("dialogue_dashes", "Dialogue dashes", "- Hi. / - Hey.  →  Hi. / Hey.", ("dashes",)),
    ("onscreen_text", "On-screen text notes", "[sign reads: EXIT]  →  EXIT", ("onscreen",)),
    ("sound_words", "Sound words", "whoosh, pew pew pew, ka-boom on their own line", ("sound_words",)),
    ("quote_italics", "Quoted voices → italics", "'Report to Oa.'  →  Report to Oa. (in italics)", ("quotes",)),
    ("ocr_fixes", "OCR repairs", "Fixes ♪ and “I” misread in disc-converted tracks", ("ocr",)),
    ("mixed_case_labels", "Every “Name:” label", "Also in files that don't use them throughout (risky)", ()),
]
LYRIC_MODES = [("italic", "Italic, no ♪"), ("keep", "Keep ♪"), ("remove", "Remove")]
VIDEO_MODES = [("replace", "Replace original"), ("new", "New file"), ("sidecar", "Subtitle file")]
SUB_MODES = [("embed", "Embed in video"), ("replace", "Replace file"), ("new", "New file")]
THEMES = [("system", "Automatic"), ("light", "Light"), ("dark", "Dark")]


def lyric_mode(o: CleanOptions) -> str:
    return "remove" if o.lyrics else ("italic" if o.lyric_symbols else "keep")


def with_lyric_mode(o: CleanOptions, mode: str) -> CleanOptions:
    return o.copy(lyrics=mode == "remove", lyric_symbols=mode == "italic")


# ============================================================================ plumbing

class Bus(QObject):
    log = Signal(str)


class Worker(QThread):
    progress = Signal(float, str)
    ok = Signal(object)
    err = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn
        self.cancel = threading.Event()

    def run(self):
        try:
            result = self.fn(lambda f, m: self.progress.emit(float(f), m), self.cancel)
        except media.Cancelled:
            self.err.emit(CANCELLED)
            return
        except Exception as e:  # noqa: BLE001 – surfaced to the user
            self.err.emit(str(e) or e.__class__.__name__)
            return
        self.ok.emit(result)


def load_docs(info: media.MediaInfo, paths: dict) -> tuple[dict, dict]:
    docs, errors = {}, {}
    for tr in info.subs:
        if not tr.text_based:
            continue
        p = paths.get(tr.index)
        if not p:
            errors[tr.index] = "Couldn't read this track."
            continue
        try:
            docs[tr.index] = sf.load(p, tr.fmt)
        except Exception as e:  # noqa: BLE001
            errors[tr.index] = str(e)
    return docs, errors


def final_changed(res) -> int:
    return sum(1 for i, c in enumerate(res.doc.cues) if res.final(i) != c.text)


class BatchWorker(QThread):
    """Cleans and saves every queued file in turn, loading any that haven't been read yet."""
    started_item = Signal(int, int)            # uid, position
    item_loaded = Signal(int, object)          # uid, dict
    item_progress = Signal(int, float, str)
    item_done = Signal(int, object)
    item_skipped = Signal(int, str)
    item_held = Signal(int, str)               # loaded during the save and needs a look first
    item_failed = Signal(int, str)
    all_done = Signal(bool)                    # cancelled?

    def __init__(self, jobs: list[dict], cfg: dict, tools: media.Tools, log, parent=None):
        super().__init__(parent)
        self.jobs, self.cfg, self.tools, self.log = jobs, cfg, tools, log
        self.cancel = threading.Event()

    def run(self):
        cfg = self.cfg
        for k, job in enumerate(self.jobs):
            if self.cancel.is_set():
                break
            uid = job["uid"]
            self.started_item.emit(uid, k)
            prog = lambda f, m, u=uid: self.item_progress.emit(u, float(f), m)
            try:
                it = Item(path=job["path"], kind=job["kind"])
                it.info, it.docs, it.sdh_counts = job["info"], job["docs"], job["sdh_counts"]
                it.sel, it.manual, it.opts = job["sel"], job["manual"], job["opts"]
                it.overrides = dict(job["overrides"])
                it.sidecars, it.dropped = list(job["sidecars"]), set(job["dropped"])
                fresh = it.info is None or (it.kind == "video" and not it.docs and
                                            (any(t.text_based for t in it.info.subs) or media.find_sidecars(it.path)))
                if fresh:
                    self._load(it, uid, prog)
                if not it.manual or it.sel not in it.docs:
                    it.sel = batch.pick_track(it, cfg["pref"])
                track = it.track()
                tidy = self._tidy_opts(it, job)
                if track is None or it.sel not in it.docs:
                    self._tidy_or_skip(it, uid, tidy, "no text subtitles", prog)
                    continue
                if not job["force"] and not track.external and not batch.is_sdh(it, track):
                    self._tidy_or_skip(it, uid, tidy, "no SDH subtitles (regular subtitles only)", prog)
                    continue
                res = batch.clean_item(it, cfg["opts"])
                policy = job["policy"]
                unresolved = res.unresolved()
                if unresolved:
                    if policy == "hold" or (fresh and policy == "hold_new"):
                        self.item_held.emit(uid, f"{len(unresolved)} lines to check")
                        continue
                    if policy == "orig":
                        for i in unresolved:
                            res.overrides[i] = "orig"
                n = final_changed(res)
                if n == 0 and not job["force"] and not track.external:
                    self._tidy_or_skip(it, uid, tidy, "already clean", prog)
                    continue
                mode = cfg["video_mode"] if it.kind == "video" else cfg["sub_mode"]
                note = ""
                if it.kind == "subtitle" and mode == "embed" and not it.info.companion:
                    mode, note = "new", " (no matching video, so saved as a subtitle file)"
                opts = media.SaveOptions(mode=mode, keep_original=cfg["keep"], make_default=cfg["default"],
                                         track_name=cfg["name"] or "", language=track.language,
                                         overwrite_sidecar=cfg["overwrite"], recycle_source_sub=cfg["recycle"],
                                         recycle_paths=batch.removable_sidecars(it, cfg["keep"], mode)
                                         if cfg["recycle"] and it.kind == "video" else [])
                if tidy is not None:
                    opts.drop_other_languages, opts.keep_language = tidy.drop_other_languages, tidy.keep_language
                    opts.rename_to, opts.rename_sidecars = tidy.rename_to, tidy.rename_sidecars
                out = media.save(self.tools, it.info, track, res.render(), res.doc.fmt, opts, prog, self.cancel,
                                 self.log)
                self._record(it, out.path)
                self.item_done.emit(uid, {"path": out.path, "note": out.note + note, "changed": n, "sel": it.sel,
                                          "text": ""})
            except media.Cancelled:
                self.item_failed.emit(uid, CANCELLED)
                break
            except Exception as e:  # noqa: BLE001
                self.item_failed.emit(uid, str(e) or e.__class__.__name__)
        self.all_done.emit(self.cancel.is_set())

    # --- renaming and track tidy-up (only when an original is being replaced)
    def _tidy_opts(self, it: Item, job: dict):
        cfg = self.cfg
        if it.kind != "video" or cfg["video_mode"] != "replace":
            return None
        new = job.get("new_name") or ""
        drop = cfg.get("drop_langs", False) and bool(batch.other_languages(it, cfg.get("keep_lang", "eng")))
        if not new and not drop:
            return None
        side = [t.external for t in it.sidecars if t.external] or media.find_sidecars(it.path)
        return media.SaveOptions(mode="replace", drop_other_languages=drop, keep_language=cfg.get("keep_lang", "eng"),
                                 rename_to=new, rename_sidecars=side, make_default=cfg["default"],
                                 track_name=cfg["name"] or "")

    def _tidy_or_skip(self, it: Item, uid: int, tidy, why: str, prog):
        if tidy is None:
            self.item_skipped.emit(uid, why)
            return
        out = media.tidy_or_rename(self.tools, it.info, tidy, prog, self.cancel, self.log)
        self._record(it, out.path)
        self.item_done.emit(uid, {"path": out.path, "note": out.note, "changed": 0, "sel": it.sel, "text": "tidy"})

    def _record(self, it: Item, new_path: str):
        if os.path.normcase(new_path) != os.path.normcase(it.path):
            naming.record_rename(self.cfg.get("batch_id", ""), it.path, new_path)

    def _load(self, it: Item, uid: int, prog):
        payload = {}
        if it.kind == "subtitle":
            info, doc = media.probe_subtitle_file(it.path)
            it.info, it.docs = info, {0: doc}
        else:
            prog(-1, "Opening")
            it.info = media.probe(self.tools, it.path, self.log)
            tmp = tempfile.mkdtemp(prefix="subscrub_")
            paths = media.extract_subs(self.tools, it.info, it.info.subs, tmp,
                                       lambda f, m: prog(f, "Reading subtitles"), self.cancel, self.log)
            it.docs, payload["errors"] = load_docs(it.info, paths)
            payload["tmpdir"] = tmp
            batch.attach_sidecars(it, self.log)
        batch.measure(it)
        payload.update(info=it.info, docs=it.docs, sdh_counts=it.sdh_counts, sidecars=it.sidecars)
        self.item_loaded.emit(uid, payload)


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def fmt_duration(sec: float) -> str:
    if not sec:
        return ""
    sec = int(round(sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n:,} {word if n == 1 else (many or word + 's')}"


# ============================================================================ dialogs

class LogDialog(QDialog):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Activity log")
        self.resize(760, 460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(12)
        hint = QLabel("Commands SubScrub ran and what the tools said. Handy if something goes wrong.")
        hint.setObjectName("Hint")
        lay.addWidget(hint)
        self.box = QPlainTextEdit(text or "Nothing yet.")
        self.box.setObjectName("Log")
        self.box.setReadOnly(True)
        self.box.setLineWrapMode(QPlainTextEdit.NoWrap)
        lay.addWidget(self.box, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.box.toPlainText()))
        row.addWidget(copy)
        close = QPushButton("Close")
        close.setObjectName("Primary")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        theme.apply_titlebar(self, parent.t if parent else theme.palette_for("system"))


class ToolsDialog(QDialog):
    WINGET = {"ffmpeg": "Gyan.FFmpeg", "mkvmerge": "MoritzBunkus.MKVToolNix"}

    def __init__(self, win: "MainWindow"):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Tools")
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(14)
        t = QLabel("Tools SubScrub uses")
        t.setObjectName("CardTitle")
        lay.addWidget(t)
        h = QLabel("FFmpeg is required. MKVToolNix is optional but recommended: it's the most faithful way to "
                   "rewrite MKV files (keeps every tag, chapter and attachment exactly as they were).")
        h.setObjectName("Hint")
        h.setWordWrap(True)
        lay.addWidget(h)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(10)
        lay.addLayout(self.grid)
        self.rows = {}
        for r, (key, label) in enumerate((("ffmpeg", "FFmpeg"), ("ffprobe", "FFprobe"), ("mkvmerge", "MKVToolNix"))):
            name = QLabel(label)
            name.setObjectName("OptTitle")
            status = QLabel()
            path = QLabel()
            path.setObjectName("Faint")
            path.setTextInteractionFlags(Qt.TextSelectableByMouse)
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda _=False, k=key: self.browse(k))
            self.grid.addWidget(name, r, 0)
            self.grid.addWidget(status, r, 1)
            self.grid.addWidget(path, r, 2)
            self.grid.addWidget(browse, r, 3)
            self.rows[key] = (status, path)
        self.grid.setColumnStretch(2, 1)

        row = QHBoxLayout()
        if media.IS_WIN:
            for key, label in (("ffmpeg", "Install FFmpeg"), ("mkvmerge", "Install MKVToolNix")):
                b = QPushButton(label)
                b.setToolTip(f"Runs: winget install -e --id {self.WINGET[key]}")
                b.clicked.connect(lambda _=False, k=key: self.win.winget_install(self.WINGET[k]))
                row.addWidget(b)
        else:
            row.addWidget(QLabel("Install ffmpeg and mkvtoolnix with your package manager."))
        row.addStretch(1)
        rescan = QPushButton("Re-scan")
        rescan.clicked.connect(self.rescan)
        row.addWidget(rescan)
        done = QPushButton("Done")
        done.setObjectName("Primary")
        done.clicked.connect(self.accept)
        row.addWidget(done)
        lay.addLayout(row)
        self.refresh()
        theme.apply_titlebar(self, win.t)

    def refresh(self):
        tools = self.win.tools
        for key, (status, path) in self.rows.items():
            p = getattr(tools, key)
            if p:
                status.setText("✓  " + (media.tool_version(p) or "found"))
                status.setStyleSheet(f"color: {self.win.t['green']}; font-weight: 600;")
                path.setText(p)
            else:
                status.setText("Not found" if key != "mkvmerge" else "Not found (optional)")
                status.setStyleSheet(f"color: {self.win.t['amber' if key != 'mkvmerge' else 'faint']}; font-weight: 600;")
                path.setText("")

    def rescan(self):
        self.win.rescan_tools()
        self.refresh()

    def browse(self, key):
        exe = "Programs (*.exe)" if media.IS_WIN else "All files (*)"
        p, _ = QFileDialog.getOpenFileName(self, f"Locate {key}", "", exe)
        if p:
            self.win.settings.setValue(f"tools/{key}", p)
            self.rescan()


class SettingsDialog(QDialog):
    """Everything that's set once: how files are saved, the default edits, appearance."""

    def __init__(self, win: "MainWindow"):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Settings")
        self.setMinimumWidth(600)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(24, 20, 24, 12)
        lay.setSpacing(8)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        def section(text):
            lay.addSpacing(10)
            s = QLabel(text)
            s.setObjectName("Section")
            lay.addWidget(s)

        sw = {"on": win.t["accent"], "off": win.t["sw_off"], "knob": "#FFFFFF", "knob_off": win.t["sw_knob_off"]}

        section("Videos")
        self.video_mode = Segmented(VIDEO_MODES)
        self.video_mode.set_value(win.video_mode)
        lay.addWidget(self.video_mode)
        self.video_hint = QLabel()
        self.video_hint.setObjectName("Hint")
        self.video_hint.setWordWrap(True)
        lay.addWidget(self.video_hint)
        self.default_row = OptionRow("Make the cleaned track the default", "Jellyfin and players pick it automatically",
                                     win.make_default)
        self.keep_row = OptionRow("Keep the SDH track too", "Off: the SDH track is replaced by the clean one",
                                  win.keep_sdh)
        for r in (self.default_row, self.keep_row):
            r.set_colors(sw)
            lay.addWidget(r)
        nl = QLabel("Track name")
        nl.setObjectName("OptTitle")
        lay.addWidget(nl)
        self.name_edit = QLineEdit(win.track_name)
        self.name_edit.setPlaceholderText("Automatic: the language, e.g. “English”")
        lay.addWidget(self.name_edit)

        section("Subtitle files (.srt, .ass)")
        self.recycle_row = OptionRow("One file per video",
                                     "Subtitle files next to a video (Movie.eng.srt) are cleaned into the video and "
                                     "then moved to the Recycle Bin, along with other files in the same language.",
                                     win.recycle_sub)
        self.recycle_row.set_colors(sw)
        lay.addWidget(self.recycle_row)
        sl = QLabel("Subtitle files with no matching video, or when you add just the .srt:")
        sl.setObjectName("Hint")
        sl.setWordWrap(True)
        lay.addWidget(sl)
        self.sub_mode = Segmented(SUB_MODES)
        self.sub_mode.set_value(win.sub_mode)
        lay.addWidget(self.sub_mode)

        section("Names and tracks")
        self.rename_row = OptionRow("Rename videos to their TMDB title",
                                    "“Batman V Super Dawn of Justice 2160 HDR.mkv” becomes “Batman v Superman - Dawn "
                                    "of Justice.mkv”; episodes become “Show S1 E1.mkv”. Files stay in "
                                    "their folder. Only when replacing originals.", win.rename_on)
        self.year_row = OptionRow("Add the year to movie names", "“Dune (2021).mkv” instead of “Dune.mkv”",
                                  win.name_year)
        self.langs_row = OptionRow("Remove subtitles in other languages",
                                   "Keeps English, forced tracks (any language) and tracks with no language set.",
                                   win.drop_langs)
        for r in (self.rename_row, self.year_row, self.langs_row):
            r.set_colors(sw)
        lay.addWidget(self.rename_row)
        lay.addWidget(self.year_row)
        kl = QLabel("TMDB API key")
        kl.setObjectName("OptTitle")
        lay.addWidget(kl)
        self.key_edit = QLineEdit(win.tmdb_key)
        self.key_edit.setPlaceholderText("Paste your API key or read access token")
        self.key_edit.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        lay.addWidget(self.key_edit)
        kh = QLabel("Free: sign in at themoviedb.org, then Settings → API → Create. Titles are looked up there; "
                    "nothing else is sent.")
        kh.setObjectName("Hint")
        kh.setWordWrap(True)
        lay.addWidget(kh)
        lay.addSpacing(4)
        lay.addWidget(self.langs_row)
        self.rename_row.toggled.connect(lambda *_: self._hints())

        section("Default edits")
        h = QLabel("What gets cleaned out of every file. You can still change these for one file in Review.")
        h.setObjectName("Hint")
        h.setWordWrap(True)
        lay.addWidget(h)
        self.rule_rows = {}
        d = win.defaults
        for key, title, desc, _ in RULES:
            r = OptionRow(title, desc, getattr(d, key))
            r.set_colors(sw)
            self.rule_rows[key] = r
            lay.addWidget(r)
        lr = QHBoxLayout()
        ll = QLabel("Song lyrics")
        ll.setObjectName("OptTitle")
        lr.addWidget(ll)
        lr.addStretch(1)
        self.lyrics = Segmented(LYRIC_MODES)
        self.lyrics.setFixedWidth(300)
        self.lyrics.set_value(lyric_mode(d))
        lr.addWidget(self.lyrics)
        lay.addLayout(lr)

        section("Appearance and safety")
        tr = QHBoxLayout()
        tl = QLabel("Theme")
        tl.setObjectName("OptTitle")
        tr.addWidget(tl)
        tr.addStretch(1)
        self.theme = Segmented(THEMES)
        self.theme.setFixedWidth(300)
        self.theme.set_value(win.theme_mode)
        self.theme.changed.connect(win.set_theme)
        tr.addWidget(self.theme)
        lay.addLayout(tr)
        th = QLabel("Automatic follows Windows' light/dark setting, live.")
        th.setObjectName("Hint")
        lay.addWidget(th)
        self.confirm_row = OptionRow("Ask before rewriting files in place", "", win.confirm_replace)
        self.confirm_row.set_colors(sw)
        lay.addWidget(self.confirm_row)
        self.glass_row = OptionRow("Use Windows' Mica backdrop (Windows 11)", "Tints the window with your wallpaper "
                                   "instead of SubScrub's own backdrop. Applies the next time SubScrub starts.",
                                   win.glass)
        self.glass_row.set_colors(sw)
        self.glass_row.setVisible(glass.system_backdrop_supported())
        lay.addWidget(self.glass_row)
        tools_row = QHBoxLayout()
        tb = QPushButton("Tools && paths…")
        tb.clicked.connect(win.show_tools)
        lb = QPushButton("Activity log")
        lb.clicked.connect(win.show_log)
        tools_row.addWidget(tb)
        tools_row.addWidget(lb)
        tools_row.addStretch(1)
        lay.addSpacing(6)
        lay.addLayout(tools_row)
        lay.addStretch(1)

        foot = QFrame()
        foot.setObjectName("Footer")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(24, 12, 24, 12)
        reset = QPushButton("Restore defaults")
        reset.setObjectName("Ghost")
        reset.clicked.connect(self.restore)
        fl.addWidget(reset)
        fl.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Save")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        fl.addWidget(cancel)
        fl.addWidget(ok)
        outer.addWidget(foot)

        self.video_mode.changed.connect(lambda *_: self._hints())
        self.keep_row.toggled.connect(lambda *_: self._hints())
        self.default_row.toggled.connect(lambda *_: self._hints())
        self._hints()
        self.resize(640, min(860, int(QApplication.primaryScreen().availableGeometry().height() * 0.86))
                    if QApplication.primaryScreen() else 820)
        theme.apply_titlebar(self, win.t)

    def _hints(self):
        m = self.video_mode.value()
        keep, default = self.keep_row.isChecked(), self.default_row.isChecked()
        if m == "replace":
            t = (f"Rewrites the video with the cleaned track{' as the default' if default else ''}"
                 f"{', keeping the SDH track as a backup' if keep else ' in place of the SDH track'}. "
                 "The new file is checked before the old one goes to the Recycle Bin.")
        elif m == "new":
            t = "Saves “Movie.dialogue.mkv” next to the original, which isn't touched."
        else:
            t = "Saves “Movie.en.srt” next to the video — nothing is remuxed. Players load it automatically."
        self.video_hint.setText(t)
        for r in (self.keep_row, self.default_row):
            r.setEnabled(m != "sidecar")
        self.rename_row.setEnabled(m == "replace")
        self.year_row.setEnabled(m == "replace" and self.rename_row.isChecked())
        self.key_edit.setEnabled(self.rename_row.isChecked())
        self.langs_row.setEnabled(m != "sidecar")

    def restore(self):
        d = CleanOptions()
        for key, r in self.rule_rows.items():
            r.setChecked(getattr(d, key))
        self.lyrics.set_value(lyric_mode(d))
        self.video_mode.set_value("replace")
        self.sub_mode.set_value("embed")
        self.default_row.setChecked(True)
        self.keep_row.setChecked(False)
        self.recycle_row.setChecked(True)
        self.name_edit.setText("")
        self.rename_row.setChecked(True)
        self.year_row.setChecked(False)
        self.langs_row.setChecked(True)
        self._hints()

    def options(self) -> CleanOptions:
        o = CleanOptions(**{k: r.isChecked() for k, r in self.rule_rows.items()})
        return with_lyric_mode(o, self.lyrics.value())


class DecisionDialog(QDialog):
    """Asked at save time when some files have judgement calls."""

    def __init__(self, win: "MainWindow", flagged: list[Item], others: int, unseen: int):
        super().__init__(win)
        self.choice = None
        self.setWindowTitle("Some lines need a decision")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        n = sum(len(i.result.unresolved()) for i in flagged if i.result)
        t = QLabel(f"{plural(len(flagged), 'file')} {'has' if len(flagged) == 1 else 'have'} "
                   f"{plural(n, 'line')} SubScrub isn't sure about")
        t.setObjectName("CardTitle")
        t.setWordWrap(True)
        lay.addWidget(t)
        names = "\n".join(f"•  {i.name}  —  {plural(len(i.result.unresolved()), 'line')}" for i in flagged[:6])
        if len(flagged) > 6:
            names += f"\n•  …and {len(flagged) - 6} more"
        lst = QLabel(names)
        lst.setObjectName("Hint")
        lst.setWordWrap(True)
        lay.addWidget(lst)
        h = QLabel("These are things like words in (parentheses) that might be dialogue, or text the regular "
                   "subtitles in the same file disagree with.")
        h.setObjectName("Hint")
        h.setWordWrap(True)
        lay.addWidget(h)
        lay.addSpacing(4)

        def option(key, title, desc, primary=False):
            b = two_line_button(title, desc, "Choice" if not primary else "ChoicePrimary")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda: self._pick(key))
            lay.addWidget(b)
            return b

        option("review", "Review them now", "Opens the first one with just those lines listed.", primary=True)
        option("guess", "Save with SubScrub's best guess", "Apply every edit, including the uncertain ones.")
        option("orig", "Save, but leave those lines alone", "Uncertain lines stay exactly as they were.")
        if others:
            option("skip", f"Only save the other {plural(others, 'file')}", "Come back to these later.")
        if unseen:
            note = QLabel(f"{plural(unseen, 'file')} still being read will follow the same choice.")
            note.setObjectName("Faint")
            lay.addWidget(note)
        row = QHBoxLayout()
        row.addStretch(1)
        c = QPushButton("Cancel")
        c.clicked.connect(self.reject)
        row.addWidget(c)
        lay.addLayout(row)
        theme.apply_titlebar(self, win.t)

    def _pick(self, key):
        self.choice = key
        self.accept()


class NameDialog(QDialog):
    """Pick which title a video is, when SubScrub isn't sure (or the user disagrees)."""

    def __init__(self, win: "MainWindow", it: Item, api: naming.TMDB, with_year: bool):
        super().__init__(win)
        self.pick = None
        self.it, self.api, self.with_year = it, api, with_year
        self.ext = Path(it.path).suffix
        self.setWindowTitle("Choose the title")
        self.setMinimumWidth(580)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(24, 22, 24, 20)
        self.lay.setSpacing(10)
        t = QLabel(f"Which title is “{it.name}”?")
        t.setObjectName("CardTitle")
        t.setWordWrap(True)
        self.lay.addWidget(t)
        h = QLabel("Pick the closest match. The file is renamed to the title shown under it.")
        h.setObjectName("Hint")
        h.setWordWrap(True)
        self.lay.addWidget(h)
        self.box = QVBoxLayout()
        self.box.setSpacing(6)
        self.lay.addLayout(self.box)
        self.note = QLabel("")
        self.note.setObjectName("Faint")
        self.note.setWordWrap(True)
        self.lay.addWidget(self.note)
        sr = QHBoxLayout()
        self.q = QLineEdit()
        self.q.setPlaceholderText("Search TMDB by title")
        self.q.setText(it.match.parsed.title if it.match else "")
        self.q.returnPressed.connect(self.search)
        sr.addWidget(self.q, 1)
        b = QPushButton("Search")
        b.clicked.connect(self.search)
        sr.addWidget(b)
        self.lay.addLayout(sr)
        row = QHBoxLayout()
        keep = QPushButton("Keep current name")
        keep.clicked.connect(lambda: self._set("keep"))
        row.addWidget(keep)
        row.addStretch(1)
        c = QPushButton("Cancel")
        c.clicked.connect(self.reject)
        row.addWidget(c)
        self.lay.addLayout(row)
        self.show_choices(it.match.choices if it.match else [])
        theme.apply_titlebar(self, win.t)

    def show_choices(self, choices: list):
        while self.box.count():
            w = self.box.takeAt(0).widget()
            if w:
                w.deleteLater()
        for c in choices[:6]:
            new = naming.render(c, self.ext, self.with_year)
            b = two_line_button(c.label(), new, "NameChoice")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, ch=c: self._set(ch))
            self.box.addWidget(b)
        self.note.setText("" if choices else "No matches. Try a different title below.")
        self.note.setVisible(bool(self.note.text()))

    def search(self):
        text = self.q.text().strip()
        if not text:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            kind = "episode" if self.it.match and self.it.match.parsed.is_episode else "movie"
            self.show_choices(naming.search(self.api, text, kind, self.it.match.parsed))
        except naming.LookupError_ as e:
            self.note.setText(str(e))
            self.note.setVisible(True)
        finally:
            QApplication.restoreOverrideCursor()

    def _set(self, pick):
        self.pick = pick
        self.accept()


class LockDialog(QDialog):
    """Shown before saving when files that would be changed are open in another program (e.g. seeding)."""

    def __init__(self, win: "MainWindow", locked: dict[str, list[str]], items: list[Item]):
        super().__init__(win)
        self.choice = None
        self.setWindowTitle("Some files are in use")
        W = 560
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        lay.setSizeConstraint(QVBoxLayout.SetFixedSize)

        def label(text, name):
            lb = QLabel(text)
            lb.setObjectName(name)
            lb.setWordWrap(True)
            lb.setFixedWidth(W)
            lay.addWidget(lb)
            return lb

        progs: dict[str, int] = {}
        for names in locked.values():
            for n in names:
                progs[n] = progs.get(n, 0) + 1
        who = " and ".join(sorted(progs, key=lambda n: -progs[n]))
        label(f"{plural(len(items), 'file')} {'is' if len(items) == 1 else 'are'} open in {who}", "CardTitle")
        fm = self.fontMetrics()
        names = "\n".join("•  " + fm.elidedText(i.name, Qt.ElideMiddle, W - 30) for i in items[:8])
        if len(items) > 8:
            names += f"\n•  …and {len(items) - 8} more"
        label(names, "Hint")
        label("SubScrub can't rewrite a file while another program has it open. If you're seeding these, pause "
              "those torrents now, then click Check again. Nothing has been changed yet.", "Hint")
        label("Once a file is rewritten its torrent can't seed it anymore (the contents no longer match) — remove "
              "the torrent afterwards and keep the files. To keep seeding the original, use Settings → Videos → "
              "New file instead.", "Faint")
        lay.addSpacing(4)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        skip = QPushButton(f"Skip {'it' if len(items) == 1 else 'these'}")
        skip.clicked.connect(lambda: self._pick("skip"))
        again = QPushButton("Check again")
        again.setObjectName("Primary")
        again.setDefault(True)
        again.clicked.connect(lambda: self._pick("again"))
        row.addWidget(cancel)
        row.addWidget(skip)
        row.addWidget(again)
        lay.addLayout(row)
        theme.apply_titlebar(self, win.t)

    def _pick(self, key):
        self.choice = key
        self.accept()


class LockCheck(QThread):
    done = Signal(object)          # {path: [programs]}

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = paths

    def run(self):
        try:
            self.done.emit(media.who_has_open(self.paths))
        except Exception:  # noqa: BLE001
            self.done.emit({})


# ============================================================================ main window

class MainWindow(QMainWindow):
    PAGE_WELCOME, PAGE_FILES, PAGE_REVIEW = 0, 1, 2

    def __init__(self, initial_paths: list[str] | str | None = None):
        super().__init__()
        self.settings = QSettings(APP_NAME, APP_NAME)
        self.theme_mode = self.settings.value("theme", "system")
        self.t = theme.palette_for(self.theme_mode)
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
        self.batch_total = 0
        self.batch_pos = 0
        self.last_output: str | None = None
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
        self._lock_thread: LockCheck | None = None

        self.setWindowTitle(APP_NAME)
        self.setAcceptDrops(True)
        self.setMinimumSize(1000, 680)
        self.sysglass = False                   # becomes True once Windows has accepted the Mica backdrop
        self.glass_wanted = self.glass and glass.system_backdrop_supported()
        if self.glass_wanted:
            self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.panels: list = []
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
        return bool(self.batch_worker and self.batch_worker.isRunning())

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
        self.root = root
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._build_header())
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_welcome())
        self.stack.addWidget(self._build_files())
        self.stack.addWidget(self._build_review())
        v.addWidget(self.stack, 1)
        self.footer = self._build_footer()
        v.addWidget(self.footer)
        self.footer.hide()

        self.veil = QFrame(root)
        self.veil.setObjectName("DragVeil")
        self.veil.setAttribute(Qt.WA_TransparentForMouseEvents)
        vl = QVBoxLayout(self.veil)
        self.veil_label = QLabel("Drop to add")
        self.veil_label.setObjectName("DragVeilText")
        self.veil_label.setAlignment(Qt.AlignCenter)
        vl.addWidget(self.veil_label)
        self.veil.hide()

    def _panel(self, radius: float = 12, strong: bool = False) -> "glass.GlassPanel":
        p = glass.GlassPanel(radius, strong=strong)
        self.panels.append(p)
        return p

    def _build_header(self):
        h = QFrame()
        h.setObjectName("Header")
        lay = QHBoxLayout(h)
        lay.setContentsMargins(20, 6, 12, 2)         # the title bar above already shows the icon and name
        lay.setSpacing(6)
        lay.addStretch(1)
        self.tools_pill = QPushButton()
        self.tools_pill.setObjectName("Pill")
        self.tools_pill.setCursor(Qt.PointingHandCursor)
        self.tools_pill.clicked.connect(self.show_tools)
        lay.addWidget(self.tools_pill)
        lay.addSpacing(4)
        self.settings_btn = QToolButton()
        self.settings_btn.setObjectName("IconButton")
        self.settings_btn.setToolTip("Settings (Ctrl+,)")
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        self.settings_btn.clicked.connect(self.show_settings)
        lay.addWidget(self.settings_btn)
        self.more_btn = QToolButton()
        self.more_btn.setObjectName("IconButton")
        self.more_btn.setPopupMode(QToolButton.InstantPopup)
        self.more_btn.setCursor(Qt.PointingHandCursor)
        self.more_btn.setToolTip("More")
        menu = QMenu(self)
        menu.addAction("Settings…", self.show_settings)
        menu.addAction("Tools && paths…", self.show_tools)
        menu.addAction("Activity log", self.show_log)
        menu.addAction("Undo last renames…", self.undo_renames)
        menu.addSeparator()
        menu.addAction(f"About {APP_NAME}", self.show_about)
        self.more_btn.setMenu(menu)
        lay.addWidget(self.more_btn)
        return h

    def _build_welcome(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(40, 24, 40, 40)
        outer.addStretch(2)
        colw = self._panel(16, strong=True)
        colw.setFixedWidth(760)
        col = QVBoxLayout(colw)
        col.setContentsMargins(40, 38, 40, 36)
        col.setSpacing(0)
        # the one bold moment: a caption on a dark screen losing its sound tag
        self.demo = ScreenView(height=230)
        self.demo.demo = ("[door creaks]", "Hello? Is anyone there?")
        self.demo.setCursor(Qt.PointingHandCursor)
        self.demo.setToolTip("Click to play again")
        self.demo.mouseReleaseEvent = lambda e: self.play_demo(0)
        col.addWidget(self.demo)
        self._demo_anim = QPropertyAnimation(self.demo, b"demo_t", self)
        self._demo_anim.setDuration(1700)
        self._demo_anim.setStartValue(0.0)
        self._demo_anim.setEndValue(2.0)
        self._demo_anim.setEasingCurve(QEasingCurve.InOutCubic)
        col.addSpacing(30)
        t = QLabel("Drop videos, folders or subtitle files")
        t.setObjectName("Hero")
        col.addWidget(t)
        col.addSpacing(10)
        sub = QLabel("SubScrub takes the sound descriptions, speaker names and music cues out of SDH subtitles, "
                     "then puts the cleaned track back into the video. You check anything it isn't sure about.")
        sub.setObjectName("HeroSub")
        sub.setWordWrap(True)
        col.addWidget(sub)
        col.addSpacing(26)
        row = QHBoxLayout()
        row.setSpacing(10)
        b = QPushButton("Choose files")
        b.setObjectName("Primary")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(self.choose_files)
        row.addWidget(b)
        f = QPushButton("Choose a folder")
        f.setObjectName("Secondary")
        f.setCursor(Qt.PointingHandCursor)
        f.clicked.connect(self.choose_folder)
        row.addWidget(f)
        row.addStretch(1)
        note = QLabel("MKV, MP4, MOV, WebM, .srt, .ass, .vtt")
        note.setObjectName("Faint")
        row.addWidget(note, 0, Qt.AlignVCenter)
        col.addLayout(row)
        col.addSpacing(22)
        self.welcome_banner = self._banner(
            "FFmpeg isn't installed yet. SubScrub needs it to read and write video files.",
            "Set up FFmpeg", self.show_tools, tone="warn")
        col.addWidget(self.welcome_banner)
        outer.addWidget(colw, 0, Qt.AlignHCenter)
        outer.addStretch(3)
        self.drop = colw
        self.drop_icon = QLabel()            # kept for older callers; the screen replaces the upload glyph
        return page

    def play_demo(self, delay: int = 500):
        if not hasattr(self, "_demo_anim"):
            return
        self._demo_anim.stop()
        self.demo.setDemo(0.0)
        QTimer.singleShot(delay, self._demo_anim.start)

    def _banner(self, text, button_text=None, slot=None, tone="info"):
        f = QFrame()
        f.setObjectName("Banner")
        f.setProperty("tone", tone)
        lay = QHBoxLayout(f)
        lay.setContentsMargins(14, 10, 10, 10)
        lay.setSpacing(10)
        f.icon = QLabel()
        lay.addWidget(f.icon, 0, Qt.AlignTop)
        f.label = QLabel(text)
        f.label.setWordWrap(True)
        lay.addWidget(f.label, 1)
        if button_text:
            btn = QPushButton(button_text)
            btn.clicked.connect(slot)
            lay.addWidget(btn, 0, Qt.AlignVCenter)
            f.button = btn
        return f

    def _build_files(self):
        page = QWidget()
        outer = QHBoxLayout(page)
        outer.setContentsMargins(32, 28, 32, 10)
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
        t = QLabel("Files")
        t.setObjectName("PageTitle")
        head.addWidget(t, 0, Qt.AlignBottom)
        head.addSpacing(4)
        self.files_count = QLabel()
        self.files_count.setObjectName("Count")
        head.addWidget(self.files_count, 0, Qt.AlignBottom)
        head.addStretch(1)
        self.clear_btn = QPushButton("Clear list")
        self.clear_btn.setObjectName("Ghost")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(self.clear_all)
        head.addWidget(self.clear_btn)
        for text, slot in (("Add files", self.choose_files), ("Add folder", self.choose_folder)):
            b = QPushButton(text)
            b.setObjectName("Secondary")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            head.addWidget(b)
        col.addLayout(head)
        col.addSpacing(8)

        self.summary = QLabel()
        self.summary.setObjectName("Summary")
        self.summary.setWordWrap(True)
        col.addWidget(self.summary)
        self.add_note_label = QLabel()
        self.add_note_label.setObjectName("Faint")
        self.add_note_label.setWordWrap(True)
        self.add_note_label.hide()
        col.addWidget(self.add_note_label)
        col.addSpacing(16)
        self.lock_banner = self._banner("", "Check again", lambda: self.check_locks(force=True), tone="warn")
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
        self.files_box.setSpacing(2)
        self.files_box.addStretch(1)
        self.files_scroll.setWidget(fw)
        fp = self._panel(12)
        fl = QVBoxLayout(fp)
        fl.setContentsMargins(8, 8, 4, 8)
        fl.addWidget(self.files_scroll)
        self.files_panel = fp
        self.files_inner = fw
        col.addWidget(fp, 1000)                 # takes the space up to its max height, the stretch below gets the rest

        how = QHBoxLayout()
        how.setContentsMargins(0, 12, 0, 0)
        self.save_how = QLabel()
        self.save_how.setObjectName("Faint")
        self.save_how.setWordWrap(True)
        how.addWidget(self.save_how, 1)
        ch = QPushButton("Change")
        ch.setObjectName("Link")
        ch.setCursor(Qt.PointingHandCursor)
        ch.clicked.connect(self.show_settings)
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
                hint = w.heightForWidth(self.files_scroll.viewport().width()) if w.hasHeightForWidth() else -1
                h += max(hint, w.sizeHint().height(), w.minimumSizeHint().height()) + lay.spacing()
        h = max(h, self.files_box.minimumSize().height(), self.files_box.sizeHint().height())
        m = self.files_panel.layout().contentsMargins()
        h += m.top() + m.bottom() + 4
        self.files_panel.setMaximumHeight(max(80, h))

    def _build_review(self):
        page = QWidget()
        body = QHBoxLayout(page)
        body.setContentsMargins(16, 14, 0, 14)
        body.setSpacing(0)

        # ---- sidebar: which track, which kinds of edits
        side = self._panel(12, strong=True)
        side.setObjectName("Sidebar")
        side.setFixedWidth(384)
        self.left = side
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(0)
        top = QHBoxLayout()
        top.setContentsMargins(14, 14, 14, 6)
        self.back_btn = QPushButton(" All files")
        self.back_btn.setObjectName("Back")
        self.back_btn.setCursor(Qt.PointingHandCursor)
        self.back_btn.setToolTip("Back to the file list (Esc)")
        self.back_btn.clicked.connect(self.show_files)
        top.addWidget(self.back_btn)
        top.addStretch(1)
        sv.addLayout(top)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(24, 10, 20, 24)
        col.setSpacing(0)
        scroll.setWidget(inner)
        sv.addWidget(scroll, 1)

        def section(text, right: QLabel | None = None):
            row = QHBoxLayout()
            row.setSpacing(8)
            lab = QLabel(text)
            lab.setObjectName("Section")
            row.addWidget(lab)
            row.addStretch(1)
            if right is not None:
                row.addWidget(right, 0, Qt.AlignBottom)
            col.addLayout(row)

        self.track_count = QLabel()
        self.track_count.setObjectName("SectionNote")
        section("Subtitle track", self.track_count)
        col.addSpacing(4)
        self.file_meta = QLabel()
        self.file_meta.setObjectName("Hint")
        self.file_meta.setWordWrap(True)
        col.addWidget(self.file_meta)
        col.addSpacing(14)
        self.tracks_box = QVBoxLayout()
        self.tracks_box.setSpacing(8)
        col.addLayout(self.tracks_box)
        self.tracks_note = QLabel()
        self.tracks_note.setObjectName("Hint")
        self.tracks_note.setWordWrap(True)
        self.tracks_note.setContentsMargins(0, 12, 0, 0)
        self.tracks_note.hide()
        col.addWidget(self.tracks_note)
        col.addSpacing(34)

        self.edits_state = QLabel()
        self.edits_state.setObjectName("SectionNote")
        section("What to remove", self.edits_state)
        col.addSpacing(8)
        self.rule_rows: dict[str, RuleRow] = {}
        for key, title, desc, _ in RULES:
            r = RuleRow(title, desc)
            r.toggled.connect(lambda *_: self._rule_timer.start())
            self.rule_rows[key] = r
            col.addWidget(r)
        col.addSpacing(18)
        lr = QHBoxLayout()
        lr.setSpacing(8)
        ll = QLabel("Song lyrics")
        ll.setObjectName("OptTitle")
        lr.addWidget(ll)
        self.lyric_count = QLabel()
        self.lyric_count.setObjectName("RuleCount")
        lr.addWidget(self.lyric_count)
        lr.addStretch(1)
        col.addLayout(lr)
        col.addSpacing(8)
        self.lyrics_seg = Segmented(LYRIC_MODES)
        self.lyrics_seg.changed.connect(lambda *_: self._rule_timer.start())
        col.addWidget(self.lyrics_seg)
        col.addSpacing(18)
        links = QHBoxLayout()
        self.make_default_btn = QPushButton("Use these for all files")
        self.make_default_btn.setObjectName("Link")
        self.make_default_btn.setCursor(Qt.PointingHandCursor)
        self.make_default_btn.clicked.connect(self.promote_review_rules)
        self.reset_rules_btn = QPushButton("Back to defaults")
        self.reset_rules_btn.setObjectName("LinkQuiet")
        self.reset_rules_btn.setCursor(Qt.PointingHandCursor)
        self.reset_rules_btn.clicked.connect(self.reset_review_rules)
        links.addWidget(self.make_default_btn)
        links.addStretch(1)
        links.addWidget(self.reset_rules_btn)
        col.addLayout(links)
        col.addStretch(1)
        body.addWidget(side)

        # ---- main: the file, what will change, and the preview
        main = QWidget()
        v = QVBoxLayout(main)
        v.setContentsMargins(30, 12, 28, 0)
        v.setSpacing(0)
        bar = QHBoxLayout()
        bar.setSpacing(12)
        self.review_title = QLabel()
        self.review_title.setObjectName("PageTitle")
        self.review_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        bar.addWidget(self.review_title, 1)
        self.review_status = StatusTag()
        bar.addWidget(self.review_status, 0, Qt.AlignVCenter)
        self.review_chip = self.review_status          # older name
        bar.addSpacing(6)
        self.prev_file = QToolButton()
        self.next_file = QToolButton()
        for b, tip, d in ((self.prev_file, "Previous file", -1), (self.next_file, "Next file", 1)):
            b.setObjectName("IconButton")
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, d=d: self.step_file(d))
            bar.addWidget(b)
        v.addLayout(bar)
        v.addSpacing(4)
        self.review_detail = QLabel()
        self.review_detail.setObjectName("Summary")
        self.review_detail.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        v.addWidget(self.review_detail)
        self.name_btn = QPushButton()
        self.name_btn.setObjectName("LinkQuiet")
        self.name_btn.setCursor(Qt.PointingHandCursor)
        self.name_btn.setFlat(True)
        self.name_btn.setStyleSheet("text-align: left;")
        self.name_btn.clicked.connect(lambda: self.choose_name())
        self.name_btn.hide()
        v.addWidget(self.name_btn, 0, Qt.AlignLeft)
        self.review_notes = QLabel()
        self.review_notes.setObjectName("Hint")
        self.review_notes.setWordWrap(True)
        self.review_notes.setContentsMargins(0, 6, 0, 0)
        self.review_notes.hide()
        v.addWidget(self.review_notes)
        self.notes_banner = self._banner("", tone="info")          # kept for older callers; notes are a hint now
        self.notes_banner.hide()
        self.nosdh_banner = self._banner("This file only has regular subtitles, with no sound descriptions or "
                                         "speaker names to strip, so it's skipped when saving.", "Clean anyway",
                                         self.force_clean, tone="info")
        self.nosdh_banner.hide()
        v.addSpacing(14)
        v.addWidget(self.nosdh_banner)
        v.addSpacing(6)

        ph = QHBoxLayout()
        ph.setSpacing(14)
        self.filter = Segmented([("check", "To check"), ("changes", "Changes"), ("all", "Everything")])
        self.filter.changed.connect(self._filter_changed)
        ph.addWidget(self.filter)
        self.legend = Legend()
        ph.addWidget(self.legend, 1)
        self.chips = {}                                   # legacy: counts now live in the legend
        self.undo_btn = QPushButton(" Keep original")
        self.undo_btn.setObjectName("Ghost")
        self.undo_btn.setCursor(Qt.PointingHandCursor)
        self.undo_btn.setToolTip("Put the selected line back the way it was (Ctrl+Z, or double-click a line)")
        self.undo_btn.setMinimumWidth(136)
        self.undo_btn.clicked.connect(self.toggle_current_line)
        ph.addWidget(self.undo_btn)
        nav = QHBoxLayout()
        nav.setSpacing(0)
        self.prev_btn, self.next_btn = QToolButton(), QToolButton()
        for b, tip in ((self.prev_btn, "Previous change (Shift+F3)"), (self.next_btn, "Next change (F3)")):
            b.setObjectName("IconButton")
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
        self.prev_btn.clicked.connect(lambda: self.preview.step(-1))
        self.next_btn.clicked.connect(lambda: self.preview.step(1))
        self.nav_count = QLabel()
        self.nav_count.setObjectName("NavCount")
        self.nav_count.setMinimumWidth(78)
        self.nav_count.setAlignment(Qt.AlignCenter)
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.nav_count)
        nav.addWidget(self.next_btn)
        ph.addLayout(nav)
        v.addLayout(ph)
        v.addSpacing(14)
        self.preview_stack = QStackedWidget()
        self.preview_empty = QLabel("Pick a subtitle track to see what will change.")
        self.preview_empty.setObjectName("Empty")
        self.preview_empty.setAlignment(Qt.AlignCenter)
        self.preview_empty.setWordWrap(True)
        self.preview = PreviewPanel()
        self.preview.position_changed.connect(self._update_nav)
        self.preview.list.toggle_requested.connect(self.toggle_line)
        self.preview_stack.addWidget(self.preview_empty)
        self.preview_stack.addWidget(self.preview)
        pp = self._panel(12)
        pl = QVBoxLayout(pp)
        pl.setContentsMargins(4, 4, 4, 4)
        pl.addWidget(self.preview_stack)
        v.addWidget(pp, 1)
        body.addWidget(main, 1)
        return page

    def _build_footer(self):
        f = QFrame()
        f.setObjectName("Footer")
        lay = QHBoxLayout(f)
        lay.setContentsMargins(20, 12, 18, 12)
        lay.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(6)
        self.status = QLabel("")
        self.status.setObjectName("Status")
        col.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(320)
        self.progress.hide()
        col.addWidget(self.progress, 0, Qt.AlignLeft)
        lay.addLayout(col)
        lay.addStretch(1)
        self.open_btn = QPushButton("Show in folder")
        self.open_btn.setObjectName("Ghost")
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.clicked.connect(lambda: self.last_output and media.open_in_explorer(self.last_output))
        self.open_btn.hide()
        lay.addWidget(self.open_btn)
        self.cancel_btn = QPushButton("Stop")
        self.cancel_btn.setObjectName("Danger")
        self.cancel_btn.setMinimumWidth(150)
        self.cancel_btn.clicked.connect(self.cancel_save)
        self.cancel_btn.hide()
        lay.addWidget(self.cancel_btn)
        self.save_one_btn = QPushButton("Save this file")
        self.save_one_btn.setCursor(Qt.PointingHandCursor)
        self.save_one_btn.setMinimumWidth(140)
        self.save_one_btn.clicked.connect(lambda: self.start_save(only=self.review_uid))
        lay.addWidget(self.save_one_btn)
        self.save_btn = QPushButton("Clean and save")
        self.save_btn.setObjectName("Primary")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.setMinimumWidth(190)
        self.save_btn.clicked.connect(lambda: self.start_save())
        lay.addWidget(self.save_btn)
        return f

    # ------------------------------------------------------------------ theme
    def set_theme(self, mode: str):
        self.theme_mode = mode
        self.settings.setValue("theme", mode)
        self.apply_theme()

    def apply_theme(self):
        t = self.t = theme.palette_for(self.theme_mode)
        app = QApplication.instance()
        app.setPalette(theme.qpalette(t))
        app.setStyleSheet(theme.stylesheet(t))
        self.root.set_theme(t, self.sysglass)
        for pnl in self.panels:
            pnl.set_theme(t)
        sw = {"on": t["accent"], "off": t["sw_off"], "knob": "#FFFFFF", "knob_off": t["sw_knob_off"]}
        for r in self.rule_rows.values():
            r.set_colors(sw)
        for c in self.cards.values():
            c.set_colors(t["accent"], t["radio_off"])
        for r in self.rows.values():
            r.set_theme(t)
        self.demo.set_theme(t)
        self.more_btn.setIcon(icon("more", t["dim"], 20))
        self.settings_btn.setIcon(icon("gear", t["dim"], 20))
        self.back_btn.setIcon(icon("back", t["dim"], 18))
        self.prev_file.setIcon(icon("chev-up", t["dim"], 18))
        self.next_file.setIcon(icon("chev-down", t["dim"], 18))
        self.undo_btn.setIcon(icon("undo", t["dim"], 16))
        self.welcome_banner.icon.setPixmap(draw_icon("alert", t["accent"], 18))
        self.notes_banner.icon.setPixmap(draw_icon("flag", t["dim"], 18))
        self.nosdh_banner.icon.setPixmap(draw_icon("alert", t["dim"], 18))
        self.lock_banner.icon.setPixmap(draw_icon("alert", t["accent"], 18))
        self.preview.set_theme(t)
        self.prev_btn.setIcon(icon("chev-up", t["dim"], 18))
        self.next_btn.setIcon(icon("chev-down", t["dim"], 18))
        self._update_tools_pill()
        self._preview_key = None
        self.render_review()
        theme.apply_titlebar(self, t, self.sysglass)

    def showEvent(self, e):
        super().showEvent(e)
        if self.glass_wanted and not self.sysglass:
            self.sysglass = glass.enable_system_backdrop(self, self.t["is_dark"])
            self.root.set_theme(self.t, self.sysglass)
        theme.apply_titlebar(self, self.t, self.sysglass)
        if not getattr(self, "_demo_played", False) and self.page() == self.PAGE_WELCOME:
            self._demo_played = True
            self.play_demo(700)

    # ------------------------------------------------------------------ tools
    def _update_tools_pill(self):
        tl = self.tools
        if not tl.ready:
            self.tools_pill.setText(" FFmpeg needed")
            self.tools_pill.setIcon(icon("alert", self.t["accent"], 14))
            set_prop(self.tools_pill, "tone", "bad")
            self.tools_pill.setToolTip("FFmpeg wasn't found. Click to set it up.")
        else:
            self.tools_pill.setText(" Tools ready" if tl.mkvmerge else " FFmpeg ready")
            self.tools_pill.setIcon(icon("check", self.t["dim"], 14))
            set_prop(self.tools_pill, "tone", "")
            self.tools_pill.setToolTip("FFmpeg and MKVToolNix found. MKVToolNix is used for MKV files."
                                       if tl.mkvmerge else
                                       "FFmpeg found. Installing MKVToolNix is recommended for MKV files.")
        self.welcome_banner.setVisible(not tl.ready)
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
            QMessageBox.information(
                self, "winget not available",
                "Windows Package Manager (winget) isn't available on this PC.\n\n"
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
        if not self._has_tidy(it):
            return ""
        bits = []
        if self._new_name(it):
            bits.append("will be renamed")
        n = len(self._lang_drops(it))
        if n:
            bits.append(f"{plural(n, 'other-language subtitle track')} will be removed")
        text = " and ".join(bits)
        return text[:1].upper() + text[1:] + "."

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
        """(text, tone) for the line under a file's name."""
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
            return "Rename to " + new, ""
        if batch.name_choice(it) is not None:
            return "", ""                                  # the name is already right
        if batch.name_unsure(it):
            return "Not sure which title this is — choose one in Review", "alert"
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
            QMessageBox.information(self, "Nothing to undo", "There are no recent renames to undo.")
            return
        ans = QMessageBox.question(
            self, "Undo last renames",
            f"Put the original names back on {plural(len(rows), 'file')} from {rows[0]['time']}?\n\n"
            "Only the names change back. Removed subtitle tracks and originals in the Recycle Bin stay as they are.",
            QMessageBox.Yes | QMessageBox.Cancel)
        if ans != QMessageBox.Yes:
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
        QMessageBox.information(self, "Undo last renames",
                                f"{plural(done, 'file')} renamed back." +
                                (("\n\nNot changed:\n" + "\n".join(problems[:8])) if problems else ""))

    # ------------------------------------------------------------------ settings, log, about
    def show_settings(self):
        if self.busy():
            return
        old_theme = self.theme_mode
        dlg = SettingsDialog(self)
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
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<b>{APP_NAME} {__version__}</b><br><br>Removes sound descriptions, speaker names and music "
                          "cues from SDH subtitles, then puts the cleaned track back into your videos as the default — "
                          "without re-encoding.<br><br>Uses FFmpeg and MKVToolNix.")

    # ------------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls()) and not self.busy():
            e.acceptProposedAction()
            self.veil_label.setText("Drop to add" if self.items else "Drop to open")
            self.veil.setGeometry(self.root.rect().adjusted(12, 12, -12, -12))
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
            QMessageBox.information(self, "Picture subtitles",
                                    "These are picture-based subtitle files (Blu-ray/DVD), which can't be edited as "
                                    "text:\n\n" + "\n".join(os.path.basename(p) for p in skipped[:8]))
        if not files:
            if paths and not skipped:
                QMessageBox.information(self, "Nothing to add", "No video or subtitle files were found there.")
            return
        explicit = {p for p in paths if os.path.isfile(p)}
        known = {i.path for i in self.items if i.kind == "video" and i.pending}
        plan = batch.pair_up(files, explicit=explicit, embed=self.sub_mode == "embed", known_videos=known)
        files = plan.videos + plan.subtitles
        if not files:
            if plan.orphans:
                QMessageBox.information(self, "Nothing to add", "Only subtitle files without a matching video "
                                        "were found there.")
            return
        videos = list(plan.videos)
        if videos and not self.tools.ready:
            self.rescan_tools()
            if not self.tools.ready:
                QMessageBox.warning(self, "FFmpeg needed",
                                    "SubScrub needs FFmpeg to read video files. Set it up, then add them again.")
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
            row.set_theme(self.t)
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
        self.files_count.setText(str(n) if n else "")
        self.clear_btn.setVisible(n > 1)
        for it in self.items:
            self._update_row(it)
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
            return it.message or "Something went wrong", "Failed", "failed", bool(it.info)
        tr = it.track()
        info = it.info
        if info is None:
            return "Couldn't read this file", "Failed", "failed", False
        if tr is None:
            if info.subs and not any(t.text_based for t in it.tracks()):
                return "Only picture-based subtitles (PGS or VobSub), which would need OCR", "Can't edit", "dim", True
            tidy = self._tidy_note(it)
            if tidy:
                return tidy, "Ready", "ready", False
            return "No subtitles in this file or next to it", "Nothing to do", "dim", False
        label = self.track_label(it, tr)
        if it.sel in it.doc_errors:
            return f"{label}: couldn't read this track", "Failed", "failed", True
        if st == "skipped":
            return f"{label}: {it.message}", "Skipped", "dim", True
        res = it.result
        if res is None:
            return label, "", "", True
        tidy = self._tidy_note(it)
        if not it.force and not tr.external and not batch.is_sdh(it, tr):
            if tidy:
                return f"{label}: regular subtitles. {tidy}", "Ready", "ready", True
            return f"{label}: regular subtitles, nothing to strip", "No SDH", "dim", True
        n = final_changed(res)
        if n == 0 and not tr.external:
            if tidy:
                return f"{label}: nothing to remove. {tidy}", "Ready", "ready", True
            return f"{label}: nothing to remove", "Already clean", "dim", True
        if it.locked:
            who = sorted({w for names in it.locked.values() for w in names})
            return (f"{label}: open in {' and '.join(who)}, pause it there before saving",
                    "In use", "check", True)
        bits = [label]
        if tr.external and self.video_mode != "sidecar":
            bits.append("goes into the video" + (" and the .srt is removed" if self.recycle_sub
                                                  and self.video_mode == "replace" and not self.keep_sdh else ""))
        if it.opts is not None:
            bits.append("custom edits")
        tidy = self._tidy_note(it)
        if tidy:
            bits.append(tidy[:1].lower() + tidy[1:])
        if res.notes.ocr_suspect:
            bits.append("converted from a disc image")
        unresolved = len(res.unresolved())
        detail = ", ".join(bits)
        if unresolved == 0:
            return detail, "Ready", "ready", True
        if res.confidence == "low":
            return detail, "Look closer", "low", True
        return detail, f"{unresolved} to check", "check", True

    def row_count(self, it: Item) -> str:
        """The number column in the file list: how many lines change."""
        if it.state == "done":
            return f"{it.cleaned:,} cleaned" if it.cleaned else ""
        if it.result is None or it.state not in ("ready", "failed", "skipped") or it.track() is None:
            return ""
        n = final_changed(it.result)
        return f"{n:,} to clean" if n else ""

    @staticmethod
    def track_label(it: Item, tr) -> str:
        if it.kind == "subtitle":
            return tr.lang_label
        if tr.external:
            name = os.path.basename(tr.external)
            stem = Path(it.path).stem
            return f"{tr.lang_label}, from the {name[len(stem):]} file" if name.lower().startswith(stem.lower()) \
                else f"{tr.lang_label}, from “{name}”"
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
        row.setToolTip(tip)
        if it.uid == self.review_uid and self.page() == self.PAGE_REVIEW:
            self._update_review_head()

    def _update_summary(self):
        QTimer.singleShot(0, self._fit_files)
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
                key = "nothing to do"
            if key:
                counts[key] = counts.get(key, 0) + 1
        order = ["ready", "to check", "in use", "reading", "saving", "saved", "nothing to do", "failed"]
        parts = [f"{counts[k]} {k}" for k in order if k in counts]
        text = (", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1] + ".") if parts else ""
        if any(it.kind == "video" and it.name_state == "nokey" and it.pending for it in self.items):
            text += "  Add a TMDB key in Settings to rename videos."
        self.summary.setText(text.strip())
        note = getattr(self, "add_note", "")
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
            self.set_status("FFmpeg is needed to read the videos — click “FFmpeg needed” at the top.", "bad")
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
                "If you're seeding, pause those torrents before saving — SubScrub can't rewrite a file another "
                "program has open. This updates when you come back to this window.")
        self.lock_banner.setVisible(bool(locked))
        self.lock_gap.setVisible(bool(locked))
        self._update_summary()

    def changeEvent(self, e):
        super().changeEvent(e)
        from PySide6.QtCore import QEvent
        if e.type() == QEvent.ActivationChange and self.isActiveWindow() and self.items and not self.busy():
            self._lock_timer.start()

    # ------------------------------------------------------------------ pages
    def show_files(self):
        self.stack.setCurrentIndex(self.PAGE_FILES if self.items else self.PAGE_WELCOME)
        self.setWindowTitle(APP_NAME)
        for it in self.items:
            self._update_row(it)
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
        self.setWindowTitle(f"{it.name} — {APP_NAME}")
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
            self.track_count.setText(str(len(alltr)) if alltr else "")
            plain = batch.plain_partner(it)
            for tr in alltr:
                c = TrackCard(tr)
                c.set_colors(self.t["accent"], self.t["radio_off"])
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
                        c.set_stat(f"{plural(n, 'sound/name note')}", "accent" if tr.index == it.sel else "")
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

    def _note(self, text):
        self.tracks_note.setText(text)
        self.tracks_note.show()

    def _update_review_head(self):
        it = self.cur
        if it is None:
            return
        fm = self.review_title.fontMetrics()
        self.review_title.setText(fm.elidedText(it.name, Qt.ElideMiddle, max(100, self.review_title.width())))
        self.review_title.setToolTip(it.path)
        detail, chip, kind, _ = self.verdict(it)
        count = self.row_count(it)
        self.review_detail.setText(
            detail.rstrip(".") + (f", {count.replace('to clean', 'lines to clean')}" if count else ""))
        self.review_status.set(chip, kind, self.t)
        multi = len(self.items) > 1
        self.prev_file.setVisible(multi)
        self.next_file.setVisible(multi)
        tr = it.track()
        self.nosdh_banner.setVisible(bool(tr) and it.loaded and not it.force and not tr.external
                                     and not batch.is_sdh(it, tr))
        line, _tone = self.name_line(it)
        can = it.kind == "video" and self._renaming() and bool(self.tmdb_key) and it.name_state == "done" \
            and it.pending and it.match is not None and bool(it.match.choices or it.match.parsed.title)
        self.name_btn.setVisible(can)
        if can:
            self.name_btn.setText((line or "Name is already right") + "  ·  Change…")
        notes = list(it.result.notes.notes) if it.result else []
        self.review_notes.setText(" ".join(notes))
        self.review_notes.setVisible(bool(notes))

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
        unresolved = len(res.unresolved())
        flagged = len(res.flagged())
        kept_by_user = sum(1 for v in it.overrides.values() if v == "orig")
        t = self.t
        items = []
        if unresolved:
            items.append((t["accent"], f"{unresolved:,} to check", True))
        items += [(t["removed"], f"{st.removed:,} removed", False), (t["trim"], f"{st.edited:,} trimmed", False)]
        untouched = f"{st.total - st.changed:,} untouched"
        if kept_by_user:
            untouched += f", {kept_by_user} kept by you"
        items.append((t["faint"], untouched, False))
        self.legend.set_items(items)
        self.filter.buttons["check"].setVisible(flagged > 0)
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
        self.nav_count.setText(f"{k} of {n}" if k else f"{n} changes")
        row = self.preview.current_row()
        ok = row is not None and row.status != "kept"
        self.undo_btn.setEnabled(ok)
        self.undo_btn.setText(" Use cleaned" if ok and row.override == "orig" else " Keep original")

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
            text = "Saved" if it and it.state == "done" else "Clean and save"
        else:
            text = f"Clean and save {n} files" if n > 1 else ("Clean and save" if n else "All done")
        self.save_btn.setText(text)
        self.save_btn.setVisible(n > 0 or not self.items)        # nothing left to save: no greyed-out "All done"
        needs_ffmpeg = any(i.kind == "video" or self.sub_mode == "embed" for i in pending)
        self.save_btn.setEnabled(n > 0 and not busy and (self.tools.ready or not needs_ffmpeg))
        it = self.cur
        one = (self.page() == self.PAGE_REVIEW and it is not None and len(self.items) > 1 and it.pending
               and not busy)
        self.save_one_btn.setVisible(one)
        self.left.setEnabled(not busy)

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
            QMessageBox.information(self, "Still looking up titles",
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
                box = QMessageBox(QMessageBox.Question, "No SDH subtitles",
                                  f"“{it.name}” only has regular subtitles — there are no sound descriptions or "
                                  "speaker names to strip.\n\nClean it anyway (dialogue dashes and lyric styling only)?",
                                  QMessageBox.NoButton, self)
                go = box.addButton("Clean anyway", QMessageBox.AcceptRole)
                box.addButton(QMessageBox.Cancel)
                box.exec()
                if box.clickedButton() is not go:
                    return
                it.force = True
                self._compute_result(it)
                flagged = [it] if it.result and it.result.unresolved() else []
            elif sdh_ok(it) and final_changed(it.result) == 0 and not it.track().external and not self._has_tidy(it):
                QMessageBox.information(self, "Nothing to remove",
                                        "This track has nothing to clean with the current edits.")
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
            box = QMessageBox(QMessageBox.Question, "Not sure of some titles",
                              f"SubScrub couldn't tell which title {plural(len(unsure), 'file')} "
                              f"{'is' if len(unsure) == 1 else 'are'}:\n\n" +
                              "\n".join(f"•  {i.name}" for i in unsure[:6]) + ("\n•  …" if len(unsure) > 6 else ""),
                              QMessageBox.NoButton, self)
            pick = box.addButton("Choose titles…", QMessageBox.AcceptRole)
            keep = box.addButton("Keep their names", QMessageBox.DestructiveRole)
            box.addButton(QMessageBox.Cancel)
            box.exec()
            if box.clickedButton() is pick:
                for i in unsure:
                    dlg = NameDialog(self, i, self._api(), self.name_year)
                    if dlg.exec() != QDialog.Accepted or dlg.pick is None:
                        return
                    i.name_pick = dlg.pick
                    self._update_row(i)
            elif box.clickedButton() is keep:
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
            box = QMessageBox(QMessageBox.Question, "Rewrite in place?",
                              f"SubScrub will rewrite {what} with the cleaned subtitles"
                              f"{' as the default track' if self.make_default else ''}"
                              f"{self._rewrite_extras(renames, jobs_items)}. "
                              "Each new file is checked before the old one goes to the Recycle Bin.\n\n"
                              "Tip: very large files may be too big for the Recycle Bin, in which case Windows "
                              "deletes them instead.", QMessageBox.NoButton, self)
            go = box.addButton("Rewrite" if n == 1 else f"Rewrite {n} files", QMessageBox.AcceptRole)
            box.addButton(QMessageBox.Cancel)
            cb = QCheckBox("Don't ask again")
            box.setCheckBox(cb)
            box.exec()
            if box.clickedButton() is not go:
                return
            if cb.isChecked():
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
                ans = QMessageBox.question(self, "Overwrite subtitle files?",
                                           "These already exist:\n\n" + "\n".join(clashes[:8]) +
                                           ("\n…" if len(clashes) > 8 else "") + "\n\nOverwrite them? "
                                           "(No saves under new names instead.)",
                                           QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
                if ans == QMessageBox.Cancel:
                    return
                overwrite = ans == QMessageBox.Yes

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
        self.save_btn.setVisible(not on)
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

    def _b_overall(self, frac: float, label: str):
        n = max(1, self.batch_total)
        self.progress.setRange(0, 1000)
        self.progress.setValue(int((self.batch_pos + max(0.0, min(1.0, frac))) / n * 1000))
        prefix = f"{self.batch_pos + 1} of {self.batch_total}:  " if self.batch_total > 1 else ""
        self.set_status(prefix + label)

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
            self.set_status(f"Stopped — {summary}.", "bad" if c["failed"] else "")
        elif c["failed"]:
            self.set_status(f"Finished with problems — {summary}. Hover a file to see why.", "bad")
        else:
            single = self.batch_total == 1 and c["done"] == 1
            it = next((i for i in self.items if i.state == "done" and i.output == self.last_output), None) \
                if single else None
            self.set_status(f"Saved “{os.path.basename(it.output)}”. {it.message}" if it
                            else f"Done — {summary}.", "good" if c["done"] else "")
        if self.last_output:
            self.open_btn.show()
        if c["failed"] and self.batch_total == 1:
            it = next((i for i in self.items if i.state == "failed"), None)
            if it:
                box = QMessageBox(QMessageBox.Warning, "Couldn't save", it.message[:1200], QMessageBox.Ok, self)
                box.addButton("Show log", QMessageBox.HelpRole).clicked.connect(self.show_log)
                box.exec()
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
            self.veil.setGeometry(self.root.rect().adjusted(12, 12, -12, -12))
        if self.page() == self.PAGE_REVIEW:
            self._update_review_head()

    def closeEvent(self, e):
        if self.busy():
            if QMessageBox.question(self, "Still saving",
                                    "Files are still being written. Stop and discard the one in progress?") \
                    != QMessageBox.Yes:
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
