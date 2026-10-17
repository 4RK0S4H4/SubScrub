"""SubScrub's dialogs. All of them open inside the window on the S4 Glass dialog (glass.GlassDialog): the
settings, the tools, the activity log, the choices at save time, the title picker, the "files in use" warning, the
About box and every yes/no question (ask / tell), so nothing pops up in a different style."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__, media, naming
from ..batch import Item
from ..cleaner import CleanOptions
from . import glass, theme
from .glass import GlassDialog
from .theme import num_font, qc
from .widgets import (T, CheckBox, ChoiceButton, ElidedLabel, NavList, OptionRow, Segmented, button, hline,
                      label, logo_pixmap, set_prop)
from .workers import plural

# Edit types shown in Review (and as defaults in Settings): option key, title, example, rule ids counted
RULES = [
    ("brackets", "Sounds in [Brackets]", "[door slams], [Grace] Hi.", ("brackets",)),
    ("parentheses", "Sounds in (Parentheses)", "(sighs), (ON PHONE)", ("parentheses",)),
    ("speaker_labels", "Speaker Names", "JOHN: Get down!  →  Get down!", ("labels",)),
    ("music_cues", "Music Cues", "♪ tense music playing ♪, ♪♪", ("music",)),
    ("dialogue_dashes", "Dialogue Dashes", "- Hi. / - Hey.  →  Hi. / Hey.", ("dashes",)),
    ("onscreen_text", "On-Screen Text Notes", "[sign reads: EXIT]  →  EXIT", ("onscreen",)),
    ("sound_words", "Sound Words", "whoosh, pew pew pew, ka-boom on their own line", ("sound_words",)),
    ("quote_italics", "Quoted Voices → Italics", "'Report to Oa.'  →  Report to Oa. (in italics)", ("quotes",)),
    ("ocr_fixes", "OCR Repairs", "Fixes ♪ and “I” misread in disc-converted tracks", ("ocr",)),
    ("mixed_case_labels", "Every “Name:” Label", "Also in files that don't use them throughout (risky)", ()),
]
LYRIC_MODES = [("italic", "Italic, No ♪"), ("keep", "Keep ♪"), ("remove", "Remove")]
VIDEO_MODES = [("replace", "Replace Original"), ("new", "New File"), ("sidecar", "Subtitle File")]
SUB_MODES = [("embed", "Embed in Video"), ("replace", "Replace File"), ("new", "New File")]
THEMES = [("system", "Automatic"), ("light", "Light"), ("dark", "Dark")]


def lyric_mode(o: CleanOptions) -> str:
    return "remove" if o.lyrics else ("italic" if o.lyric_symbols else "keep")


def with_lyric_mode(o: CleanOptions, mode: str) -> CleanOptions:
    return o.copy(lyrics=mode == "remove", lyric_symbols=mode == "italic")


class WellList(QWidget):
    """A short list of files in a recessed box: name on the left, a quieter count on the right."""

    def __init__(self, rows: list[tuple[str, str]], more: int = 0):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 4)
        lay.setSpacing(0)
        items = list(rows) + ([(f"…and {more:,} more", "")] if more else [])
        for k, (name, count) in enumerate(items):
            row = QHBoxLayout()
            row.setContentsMargins(0, 7, 0, 7)
            row.setSpacing(16)
            n = ElidedLabel(name, "Muted" if k >= len(rows) else "", Qt.ElideMiddle)
            n.setFont(theme.font(13))
            row.addWidget(n, 1)
            if count:
                c = label(count, "Muted")
                c.setFont(num_font(c.font()))
                row.addWidget(c, 0, Qt.AlignRight)
            lay.addLayout(row)
            if k < len(items) - 1:
                lay.addWidget(hline())

    def paintEvent(self, _):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter, QPen
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(qc(t["border"]), 1))
        p.setBrush(qc(t["well"]))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)


# ============================================================================ questions and messages

class MessageDialog(GlassDialog):
    """A question or a message. buttons: (key, label, kind); the primary one is the default (Enter)."""

    def __init__(self, win, title: str, text: str, buttons: list[tuple[str, str, str]], detail: str = "",
                 checkbox: str = "", width: int = 540, rows: list[tuple[str, str]] | None = None, more: int = 0):
        super().__init__(win, title, width)
        self.choice = None
        if text:
            body = label(text, "", wrap=True)
            body.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.body_layout.addWidget(body)
        if rows:
            self.body_layout.addWidget(WellList(rows, more))
        if detail:
            d = label(detail, "Muted", wrap=True)
            self.body_layout.addWidget(d)
        self.check = None
        if checkbox:
            self.check = CheckBox(checkbox)
            self.body_layout.addWidget(self.check)
        btns = []
        for key, text_, kind in buttons:
            b = button(text_, kind if kind != "cancel" else "", "", lambda _=False, k=key: self._pick(k))
            if kind == "primary":
                b.setDefault(True)
                b.setAutoDefault(True)
            btns.append(b)
        self.add_footer_buttons(*btns)

    def _pick(self, key):
        self.choice = key
        self.accept()


def ask(win, title: str, text: str, buttons: list[tuple[str, str, str]], detail: str = "", checkbox: str = "",
        rows=None, more: int = 0) -> tuple[str | None, bool]:
    """Ask a question in a glass dialog. Returns (the key of the button clicked or None, whether the box was ticked)."""
    dlg = MessageDialog(win, title, text, buttons, detail, checkbox, rows=rows, more=more)
    dlg.exec()
    return dlg.choice, bool(dlg.check and dlg.check.isChecked())


def tell(win, title: str, text: str, detail: str = "", extra: tuple[str, object] | None = None, rows=None):
    """A message with an OK button (and optionally one more button, e.g. Show Log)."""
    buttons = [("ok", "OK", "primary")]
    if extra:
        buttons.insert(0, ("extra", extra[0], ""))
    dlg = MessageDialog(win, title, text, buttons, detail, rows=rows)
    dlg.exec()
    if dlg.choice == "extra" and extra:
        extra[1]()


# ============================================================================ activity log, tools, about

class LogDialog(GlassDialog):
    def __init__(self, text: str, parent=None):
        super().__init__(parent, "Activity Log", 860)
        hint = label("Commands SubScrub ran and what the tools said. Handy if something goes wrong.", "Muted", True)
        self.body_layout.addWidget(hint)
        self.box = QPlainTextEdit(text or "Nothing yet.")
        self.box.setObjectName("Log")
        self.box.setReadOnly(True)
        self.box.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.box.setMinimumHeight(380)
        self.body_layout.addWidget(self.box, 1)
        copy = button("Copy", "", "", lambda: QApplication.clipboard().setText(self.box.toPlainText()),
                      icon_kind="copy")
        close = button("Close", "primary", "", self.accept)
        close.setDefault(True)
        self.add_footer_buttons(copy, close)


class ToolsDialog(GlassDialog):
    WINGET = {"ffmpeg": "Gyan.FFmpeg", "mkvmerge": "MoritzBunkus.MKVToolNix"}

    def __init__(self, win):
        super().__init__(win, "Tools", 680)
        self.win = win
        h = label("FFmpeg is required. MKVToolNix is optional but recommended: it's the most faithful way to "
                  "rewrite MKV files (it keeps every tag, chapter and attachment exactly as they were).", "Muted", True)
        self.body_layout.addWidget(h)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        self.rows = {}
        for r, (key, name) in enumerate((("ffmpeg", "FFmpeg"), ("ffprobe", "FFprobe"), ("mkvmerge", "MKVToolNix"))):
            n = label(name, "OptTitle")
            status = label("", "ToolState")
            path = ElidedLabel("", "Faint", Qt.ElideMiddle)
            path.setTextInteractionFlags(Qt.TextSelectableByMouse)
            browse = button("Browse…", "", "sm", lambda _=False, k=key: self.browse(k))
            grid.addWidget(n, r, 0)
            grid.addWidget(status, r, 1)
            grid.addWidget(path, r, 2)
            grid.addWidget(browse, r, 3)
            self.rows[key] = (status, path)
        grid.setColumnStretch(2, 1)
        self.body_layout.addLayout(grid)
        row = QHBoxLayout()
        row.setSpacing(8)
        if media.IS_WIN:
            for key, text in (("ffmpeg", "Install FFmpeg"), ("mkvmerge", "Install MKVToolNix")):
                b = button(text, "", "", lambda _=False, k=key: self.win.winget_install(self.WINGET[k]),
                           tip=f"Runs: winget install -e --id {self.WINGET[key]}")
                row.addWidget(b)
        else:
            row.addWidget(label("Install ffmpeg and mkvtoolnix with your package manager.", "Muted"))
        row.addStretch(1)
        self.body_layout.addLayout(row)
        rescan = button("Scan Again", "", "", self.rescan, icon_kind="restart")
        done = button("Done", "primary", "", self.accept)
        done.setDefault(True)
        self.add_footer_buttons(rescan, done)
        self.refresh()

    def refresh(self):
        tools = self.win.tools
        for key, (status, path) in self.rows.items():
            p = getattr(tools, key)
            if p:
                status.setText("✓  " + (media.tool_version(p) or "Found"))
                set_prop(status, "tone", "good")
                path.setText(p)
            else:
                status.setText("Not Found" if key != "mkvmerge" else "Not Found (Optional)")
                set_prop(status, "tone", "warn" if key != "mkvmerge" else "dim")
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


class AboutDialog(GlassDialog):
    def __init__(self, win):
        super().__init__(win, "About", 520)
        top = QHBoxLayout()
        top.setSpacing(14)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(52))
        top.addWidget(logo, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        name = label(APP_NAME, "H3")
        ver = label(f"Version {__version__}", "Muted")
        ver.setFont(num_font(ver.font()))
        col.addWidget(name)
        col.addWidget(ver)
        top.addLayout(col, 1)
        self.body_layout.addLayout(top)
        self.body_layout.addWidget(label(
            "Removes sound descriptions, speaker names and music cues from SDH subtitles, then puts the cleaned "
            "track back into your videos as the default, without re-encoding.", "", True))
        credits = WellList([("Inter typeface by Rasmus Andersson", "SIL OFL 1.1"),
                            ("Solar icons by 480 Design", "CC BY 4.0"),
                            ("Muxing by FFmpeg and MKVToolNix", "")])
        self.body_layout.addWidget(credits)
        ok = button("Close", "primary", "", self.accept)
        ok.setDefault(True)
        self.add_footer_buttons(ok)


# ============================================================================ settings

def _page(title: str) -> tuple[QScrollArea, QVBoxLayout]:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(24, 18, 24, 22)
    lay.setSpacing(10)
    lay.addWidget(label(title, "H3"))
    lay.addSpacing(2)
    scroll.setWidget(inner)
    return scroll, lay


def _field(lay: QVBoxLayout, text: str, widget: QWidget, hint: str = ""):
    lay.addSpacing(4)
    lay.addWidget(label(text, "FieldLabel"))
    lay.addWidget(widget)
    if hint:
        lay.addWidget(label(hint, "Hint", True))


class SettingsDialog(GlassDialog):
    """Everything that's set once: how files are saved, names and tracks, the default edits, appearance."""
    SECTIONS = [("videos", "Videos", "video-duo"), ("subs", "Subtitle Files", "sub-duo"),
                ("names", "Names and Tracks", "tag-duo"), ("edits", "Default Edits", "magic"),
                ("look", "Appearance and Safety", "palette-duo")]

    def __init__(self, win, section: str = "videos"):
        super().__init__(win, "Settings", 900)
        self.win = win
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(0)
        self.body_layout.addWidget(hline())
        split = QHBoxLayout()
        split.setContentsMargins(0, 0, 0, 0)
        split.setSpacing(0)
        self.nav = NavList(self.SECTIONS)
        split.addWidget(self.nav)
        split.addWidget(_VLine())
        self.pages = QStackedWidget()
        split.addWidget(self.pages, 1)
        self.body_layout.addLayout(split, 1)
        self._keys = [k for k, _, _ in self.SECTIONS]
        self.nav.changed.connect(lambda k: self.pages.setCurrentIndex(self._keys.index(k)))

        # ---- videos
        pg, lay = _page("Videos")
        self.video_mode = Segmented(VIDEO_MODES, wide=True)
        self.video_mode.set_value(win.video_mode)
        _field(lay, "When Saving a Video", self.video_mode)
        self.video_hint = label("", "Hint", True)
        lay.addWidget(self.video_hint)
        lay.addSpacing(4)
        self.default_row = OptionRow("Make the Cleaned Track the Default", "Jellyfin and players pick it automatically.",
                                     win.make_default)
        self.keep_row = OptionRow("Keep the SDH Track Too", "Off: the SDH track is replaced by the clean one.",
                                  win.keep_sdh)
        lay.addWidget(self.default_row)
        lay.addWidget(self.keep_row)
        self.name_edit = QLineEdit(win.track_name)
        self.name_edit.setPlaceholderText("Automatic: the language, e.g. “English”")
        _field(lay, "Track Name", self.name_edit)
        lay.addStretch(1)
        self.pages.addWidget(pg)

        # ---- subtitle files
        pg, lay = _page("Subtitle Files")
        self.recycle_row = OptionRow("One File per Video",
                                     "Subtitle files next to a video (Movie.eng.srt) are cleaned into the video and "
                                     "then moved to the Recycle Bin, along with other files in the same language.",
                                     win.recycle_sub)
        lay.addWidget(self.recycle_row)
        self.sub_mode = Segmented(SUB_MODES, wide=True)
        self.sub_mode.set_value(win.sub_mode)
        _field(lay, "Subtitle Files on Their Own", self.sub_mode,
               "Subtitle files with no matching video, or when you add just the .srt.")
        lay.addStretch(1)
        self.pages.addWidget(pg)

        # ---- names and tracks
        pg, lay = _page("Names and Tracks")
        self.rename_row = OptionRow("Rename Videos to Their TMDB Title",
                                    "“Batman V Super Dawn of Justice 2160 HDR.mkv” becomes “Batman v Superman - Dawn "
                                    "of Justice.mkv”; episodes become “Show S1 E1.mkv”. Files stay in their folder. "
                                    "Only when replacing originals.", win.rename_on)
        self.year_row = OptionRow("Add the Year to Movie Names", "“Dune (2021).mkv” instead of “Dune.mkv”.",
                                  win.name_year)
        lay.addWidget(self.rename_row)
        lay.addWidget(self.year_row)
        self.key_edit = QLineEdit(win.tmdb_key)
        self.key_edit.setPlaceholderText("Paste your API key or read access token")
        self.key_edit.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        _field(lay, "TMDB API Key", self.key_edit,
               "Free: sign in at themoviedb.org, then Settings → API → Create. Titles are looked up there; "
               "nothing else is sent.")
        lay.addSpacing(6)
        self.langs_row = OptionRow("Remove Subtitles in Other Languages",
                                   "Keeps English, forced tracks (any language) and tracks with no language set.",
                                   win.drop_langs)
        lay.addWidget(self.langs_row)
        lay.addStretch(1)
        self.pages.addWidget(pg)

        # ---- default edits
        pg, lay = _page("Default Edits")
        lay.addWidget(label("What gets cleaned out of every file. You can still change these for one file in Review.",
                            "Muted", True))
        lay.addSpacing(4)
        self.rule_rows = {}
        d = win.defaults
        for key, title, desc, _ in RULES:
            r = OptionRow(title, desc, getattr(d, key))
            self.rule_rows[key] = r
            lay.addWidget(r)
        lr = QHBoxLayout()
        lr.setContentsMargins(0, 8, 0, 0)
        lr.addWidget(label("Song Lyrics", "OptTitle"))
        lr.addStretch(1)
        self.lyrics = Segmented(LYRIC_MODES)
        self.lyrics.set_value(lyric_mode(d))
        lr.addWidget(self.lyrics)
        lay.addLayout(lr)
        lay.addStretch(1)
        self.pages.addWidget(pg)

        # ---- appearance and safety
        pg, lay = _page("Appearance and Safety")
        tr = QHBoxLayout()
        tr.addWidget(label("Theme", "OptTitle"))
        tr.addStretch(1)
        self.theme = Segmented(THEMES)
        self.theme.set_value(win.theme_mode)
        self.theme.changed.connect(win.set_theme)
        tr.addWidget(self.theme)
        lay.addLayout(tr)
        lay.addWidget(label("Automatic follows Windows' light or dark setting, live.", "Hint", True))
        lay.addSpacing(6)
        self.confirm_row = OptionRow("Ask Before Rewriting Files in Place", "A last check before originals are "
                                     "replaced.", win.confirm_replace)
        lay.addWidget(self.confirm_row)
        self.glass_row = OptionRow("Use Windows' Mica Backdrop (Windows 11)", "Tints the window with your wallpaper "
                                   "instead of SubScrub's own backdrop. Applies the next time SubScrub starts.",
                                   win.glass)
        self.glass_row.setVisible(glass.system_backdrop_supported())
        lay.addWidget(self.glass_row)
        lay.addSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(button("Tools and Paths…", "", "", win.show_tools))
        row.addWidget(button("Activity Log", "", "", win.show_log))
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        self.pages.addWidget(pg)

        reset = button("Restore Defaults", "subtle", "", self.restore)
        cancel = button("Cancel", "", "", self.reject)
        ok = button("Save", "primary", "", self.accept)
        ok.setDefault(True)
        self.add_footer_buttons(cancel, ok, left=reset)

        self.video_mode.changed.connect(lambda *_: self._hints())
        self.keep_row.toggled.connect(lambda *_: self._hints())
        self.default_row.toggled.connect(lambda *_: self._hints())
        self.rename_row.toggled.connect(lambda *_: self._hints())
        self._hints()
        if section in self._keys:
            self.nav.set_value(section)
            self.pages.setCurrentIndex(self._keys.index(section))

    def _cover(self):
        super()._cover()
        h = self.win.height() if self.win is not None else 800
        self.card.setFixedHeight(min(640, max(420, h - 48)))

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
            t = "Saves “Movie.en.srt” next to the video. Nothing is remuxed; players load it automatically."
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


class _VLine(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedWidth(1)

    def paintEvent(self, _):
        from PySide6.QtGui import QPainter
        p = QPainter(self)
        p.fillRect(self.rect(), qc(T()["border"]))


# ============================================================================ choices at save time

class DecisionDialog(GlassDialog):
    """Asked at save time when some files have judgement calls."""

    def __init__(self, win, flagged: list[Item], others: int, unseen: int):
        n = sum(len(i.result.unresolved()) for i in flagged if i.result)
        title = (f"{plural(len(flagged), 'file')} {'has' if len(flagged) == 1 else 'have'} "
                 f"{plural(n, 'line')} SubScrub isn't sure about")
        super().__init__(win, title, 600)
        self.choice = None
        rows = [(i.name, plural(len(i.result.unresolved()), 'line')) for i in flagged[:6]]
        self.body_layout.addWidget(WellList(rows, max(0, len(flagged) - 6)))
        self.body_layout.addWidget(label("These are things like words in (parentheses) that might be dialogue, or "
                                         "text the regular subtitles in the same file disagree with.", "Muted", True))
        choices = QVBoxLayout()
        choices.setSpacing(8)

        def option(key, title_, desc, recommended=False):
            b = ChoiceButton(title_, desc, recommended, "Recommended" if recommended else "")
            b.clicked.connect(lambda: self._pick(key))
            choices.addWidget(b)
            return b

        first = option("review", "Review Them Now", "Opens the first one with just those lines listed.", True)
        option("guess", "Save With SubScrub's Best Guess", "Applies every edit, including the uncertain ones.")
        option("orig", "Save, but Leave Those Lines Alone", "Uncertain lines stay exactly as they were.")
        if others:
            option("skip", f"Only Save the Other {plural(others, 'File')}", "Come back to these later.")
        self.body_layout.addLayout(choices)
        if unseen:
            self.body_layout.addWidget(label(f"{plural(unseen, 'file')} still being read will follow the same choice.",
                                             "Faint", True))
        self.add_footer_buttons(button("Cancel", "", "", self.reject))
        first.setFocus()

    def _pick(self, key):
        self.choice = key
        self.accept()


class NameDialog(GlassDialog):
    """Pick which title a video is, when SubScrub isn't sure (or the user disagrees)."""

    def __init__(self, win, it: Item, api: naming.TMDB, with_year: bool):
        super().__init__(win, "Which title is this?", 620)
        self.pick = None
        self.it, self.api, self.with_year = it, api, with_year
        self.ext = Path(it.path).suffix
        name = ElidedLabel(it.name, "", Qt.ElideMiddle)
        name.setFont(theme.font(14, 600))
        self.body_layout.addWidget(name)
        self.body_layout.addWidget(label("Pick the closest match. The file is renamed to the name shown under it.",
                                         "Muted", True))
        self.box = QVBoxLayout()
        self.box.setSpacing(8)
        self.body_layout.addLayout(self.box)
        self.note = label("", "Faint", True)
        self.body_layout.addWidget(self.note)
        sr = QHBoxLayout()
        sr.setSpacing(8)
        self.q = QLineEdit()
        self.q.setPlaceholderText("Search TMDB by title")
        self.q.setText(it.match.parsed.title if it.match else "")
        self.q.returnPressed.connect(self.search)
        sr.addWidget(self.q, 1)
        sr.addWidget(button("Search", "", "", self.search, icon_kind="search"))
        self.body_layout.addLayout(sr)
        keep = button("Keep Current Name", "", "", lambda: self._set("keep"))
        self.add_footer_buttons(button("Cancel", "", "", self.reject), left=keep)
        self.show_choices(it.match.choices if it.match else [])

    def show_choices(self, choices: list):
        while self.box.count():
            w = self.box.takeAt(0).widget()
            if w:
                w.deleteLater()
        for c in choices[:6]:
            new = naming.render(c, self.ext, self.with_year)
            b = ChoiceButton(c.label(), new)
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


class LockDialog(GlassDialog):
    """Shown before saving when files that would be changed are open in another program (e.g. seeding)."""

    def __init__(self, win, locked: dict[str, list[str]], items: list[Item]):
        progs: dict[str, int] = {}
        for names in locked.values():
            for n in names:
                progs[n] = progs.get(n, 0) + 1
        who = " and ".join(sorted(progs, key=lambda n: -progs[n]))
        super().__init__(win, f"{plural(len(items), 'file')} {'is' if len(items) == 1 else 'are'} open in {who}", 580)
        self.choice = None
        self.body_layout.addWidget(WellList([(i.name, "") for i in items[:8]], max(0, len(items) - 8)))
        self.body_layout.addWidget(label(
            "SubScrub can't rewrite a file while another program has it open. If you're seeding these, pause those "
            "torrents now, then click Check Again. Nothing has been changed yet.", "", True))
        self.body_layout.addWidget(label(
            "Once a file is rewritten its torrent can't seed it anymore (the contents no longer match): remove the "
            "torrent afterwards and keep the files. To keep seeding the original, use Settings → Videos → "
            "New File instead.", "Muted", True))
        cancel = button("Cancel", "", "", self.reject)
        skip = button(f"Skip {'It' if len(items) == 1 else 'These'}", "", "", lambda: self._pick("skip"))
        again = button("Check Again", "primary", "", lambda: self._pick("again"))
        again.setDefault(True)
        self.add_footer_buttons(cancel, skip, again)

    def _pick(self, key):
        self.choice = key
        self.accept()
