"""UI checks, offscreen (no FFmpeg needed: subtitle files only).  python -m tests.test_ui

Guards for what has had to be fixed by eye: text contrast in both themes (WCAG AA), the review sidebar overflowing,
focus rings showing when the window opens, the main button showing while saving, and a run through the main flows
and every dialog."""
import os
import shutil
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QWidget  # noqa: E402

app = QApplication.instance() or QApplication(["x"])
app.setStyle("Fusion")

from subscrub.ui import dialogs, theme, widgets  # noqa: E402
from subscrub.ui import main_window as mw  # noqa: E402

theme.load_fonts()
app.setFont(theme.app_font())
FIX = os.path.join(os.path.dirname(__file__), "fixtures")
TMP = tempfile.mkdtemp(prefix="subscrub_ui_")
INI = os.path.join(TMP, "settings.ini")


class _Settings(QSettings):
    def __init__(self, *_a, **_k):
        super().__init__(INI, QSettings.IniFormat)


mw.QSettings = _Settings


def pump(sec=0.15):
    end = time.time() + sec
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def wait_for(cond, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return
        time.sleep(0.01)
    raise TimeoutError


def contrast(fg: str, bg: str) -> float:
    def lum(c):
        r, g, b, _ = theme._rgba(c)
        ch = [x / 255 for x in (r, g, b)]
        ch = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in ch]
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
    a, b = lum(fg), lum(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def test_tokens_parse():
    for t in (theme.LIGHT, theme.DARK):
        for k, v in t.items():
            if isinstance(v, str) and (v.startswith("#") or v.startswith("rgba")):
                theme.qc(v)


def test_text_contrast_both_themes():
    """Every text colour on every surface it is painted on reaches 4.5:1."""
    for name, t in (("light", theme.LIGHT), ("dark", theme.DARK)):
        page = t["page"][0][1]
        card = theme.over(t["glass"], page)
        surfaces = {"page": page, "card": card, "bar": theme.over(t["bar_bg"], page),
                    "selected tile": theme.over(t["tile_on"], card), "hover": theme.over(t["hov"], card),
                    "selected row": theme.over(t["bg_sel"], card)}
        for sname, bg in surfaces.items():
            for role in ("text", "text2") + (("text3",) if sname != "selected tile" else ()):
                c = contrast(t[role], bg)
                assert c >= 4.5, f"{name}: {role} on {sname} is {c:.2f}"
        for kind in ("ok", "warn", "bad"):
            c = contrast(t[kind], theme.over(t[kind + "_bg"], card))
            assert c >= 4.5, f"{name}: {kind} badge is {c:.2f}"
        c = contrast(t["badge_accent"], theme.over(t["accent_soft"], card))
        assert c >= 4.5, f"{name}: accent badge is {c:.2f}"
        c = contrast("#ffffff", t["btn"])
        assert c >= 4.5, f"{name}: primary button label is {c:.2f}"
        c = contrast(t["warn"], theme.over(t["warn_bg"], card))
        assert c >= 4.5, f"{name}: warning banner is {c:.2f}"


def test_main_flows_and_dialogs():
    for th in ("dark", "light"):
        s = _Settings()
        s.clear()
        s.setValue("theme", th)
        s.setValue("sub_mode", "new")                     # no FFmpeg needed to save a subtitle file on its own
        s.setValue("confirm_replace", "false")
        s.sync()
        work = os.path.join(TMP, th)
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work)
        shutil.copy(os.path.join(FIX, "sdh_en.srt"), os.path.join(work, "Show S01E01.en.sdh.srt"))
        w = mw.MainWindow()
        w.resize(1280, 800)
        w.show()
        pump(0.3)
        # nothing shows a focus ring when the window opens
        assert not [x for x in w.findChildren(QWidget) if isinstance(x, widgets.KeyboardFocus) and x.kbd_focus()]
        w.add_paths([os.path.join(work, "Show S01E01.en.sdh.srt")])
        wait_for(lambda: w.items and all(i.state in ("ready", "failed", "skipped") for i in w.items))
        pump(0.2)
        it = w.items[0]
        detail, chip, kind, review = w.verdict(it)
        assert kind in ("ready", "check", "low"), (detail, chip, kind)
        assert w.tabs.value() == "files" and w.tabs._opt("review")["enabled"]

        w.tabs.changed.emit("review")
        pump(0.3)
        assert w.page() == w.PAGE_REVIEW and w.tabs.value() == "review"
        # the sidebar's content fits its card (it once overflowed and pushed everything into the right edge)
        inner = w.side_inner
        assert inner.width() <= inner.parentWidget().width() + 1, (inner.width(), inner.parentWidget().width())
        for key in ("all", "changes"):
            w.filter.set_value(key)
            w._filter_changed(key)
            pump(0.1)
        row = next(r for r in w.preview.rows if r.status != "kept")
        before = w.row_count(it)
        w.toggle_line(row.cue)
        pump(0.1)
        assert it.overrides.get(row.cue) == "orig"
        assert w.preview.current_row() is not None
        w.toggle_line(row.cue)
        assert w.row_count(it) == before
        w._toggle_theme()
        pump(0.2)
        w._toggle_theme()
        pump(0.2)

        for dlg in (dialogs.SettingsDialog(w, "edits"), dialogs.AboutDialog(w), dialogs.ToolsDialog(w),
                    dialogs.LogDialog("one\ntwo", w), dialogs.LockDialog(w, {it.path: ["qBittorrent"]}, [it]),
                    dialogs.MessageDialog(w, "Rewrite in place?", "Text.", [("cancel", "Cancel", ""),
                                                                            ("go", "Rewrite", "primary")],
                                          "Detail.", "Don't ask again")):
            dlg.show()
            pump(0.15)
            assert dlg.geometry().size() == w.size()                 # it covers the window
            assert dlg.card.width() <= w.width() - 48
            dlg.grab()
            dlg.reject()
            pump(0.05)

        # save: the main button hides while saving and when nothing is left
        w.show_files()
        dialogs.DecisionDialog.exec = lambda self: (setattr(self, "choice", "guess"), QDialog.Accepted)[1]
        mw.DecisionDialog.exec = dialogs.DecisionDialog.exec
        w.start_save()
        assert w.busy() and not w.save_btn.isVisible() and w.cancel_btn.isVisible()
        wait_for(lambda: not w.busy(), 60)
        pump(0.2)
        assert it.state == "done", (it.state, it.message)
        assert os.path.isfile(it.output)
        assert not w.save_btn.isVisible()
        w.close()
        pump(0.1)


def test_close_mid_save_waits_for_the_worker():
    """Closing while a save runs must not give up on the thread (a leftover .subscrub-tmp file, or a crash on exit)."""
    import threading
    from PySide6.QtCore import QThread

    class Slow(QThread):
        def __init__(self):
            super().__init__()
            self.cancel = threading.Event()
            self.finished_cleanly = False

        def run(self):
            self.cancel.wait(10)
            time.sleep(0.6)                      # the tool takes a moment to exit and the temp file to go
            self.finished_cleanly = True

    s = _Settings()
    s.clear()
    s.sync()
    w = mw.MainWindow()
    w.show()
    pump(0.2)
    worker = Slow()
    w.batch_worker = worker
    worker.start()
    old_ask = mw.ask
    mw.ask = lambda *a, **k: ("stop", False)
    try:
        w.close()
    finally:
        mw.ask = old_ask
    assert not worker.isRunning() and worker.finished_cleanly
    pump(0.1)


def test_unfinished_copies_are_offered_never_deleted_silently():
    import types
    import sys
    d = tempfile.mkdtemp(dir=TMP)
    for name in ("A.mkv", "A.subscrub-tmp.mkv", "B.subscrub-tmp.mp4"):
        with open(os.path.join(d, name), "wb") as f:
            f.write(b"x" * 10)
    los = mw.media.find_leftovers([d])
    assert [(lo.name, bool(lo.original)) for lo in los] == [("A.subscrub-tmp.mkv", True), ("B.subscrub-tmp.mp4", False)]
    s = _Settings()
    s.clear()
    s.sync()
    w = mw.MainWindow()
    asked = []
    old_ask, old_tell = mw.ask, mw.tell
    trashed = []
    sys.modules["send2trash"] = types.SimpleNamespace(send2trash=lambda p: (trashed.append(p), os.remove(p)))
    try:
        mw.ask = lambda win, title, *a, **k: (asked.append(title), ("keep" if title.startswith("Unfinished") else "leave"),
                                              False)[1:]
        w._offer_leftovers(los)
        assert asked == ["Unfinished Copies Found", "The Original Is Missing"]
        assert os.path.exists(os.path.join(d, "A.subscrub-tmp.mkv")) and not trashed       # "Keep Them" removes nothing
        assert os.path.exists(os.path.join(d, "B.subscrub-tmp.mp4"))                       # "Leave It" too
        mw.ask = lambda win, title, *a, **k: ("remove" if title.startswith("Unfinished") else "restore", False)
        w._offer_leftovers(los)
    finally:
        mw.ask, mw.tell = old_ask, old_tell
    assert trashed == [os.path.join(d, "A.subscrub-tmp.mkv")] and os.path.exists(os.path.join(d, "A.mkv"))
    assert os.path.exists(os.path.join(d, "B.mp4")) and not os.path.exists(os.path.join(d, "B.subscrub-tmp.mp4"))
    w.close()


def _window(**settings):
    s = _Settings()
    s.clear()
    s.setValue("sub_mode", "new")
    s.setValue("confirm_replace", "false")
    for k, v in settings.items():
        s.setValue(k, v)
    s.sync()
    w = mw.MainWindow()
    w.resize(1280, 800)
    w.show()
    pump(0.2)
    return w


def _sub_file(name: str) -> str:
    d = tempfile.mkdtemp(dir=TMP)
    p = os.path.join(d, name)
    shutil.copy(os.path.join(FIX, "sdh_en.srt"), p)
    return p


def test_adding_the_same_file_twice_adds_it_once():
    w = _window()
    p = _sub_file("Show S01E02.en.sdh.srt")
    w.add_paths([p])
    w.add_paths([p])
    w.add_paths([os.path.join(os.path.dirname(p), ".", os.path.basename(p))])      # spelled differently
    wait_for(lambda: w.items and all(i.state in ("ready", "failed", "skipped") for i in w.items))
    assert len(w.items) == 1 and len(w.rows) == 1, [i.path for i in w.items]
    w.close()


def test_save_pressed_twice_starts_one_batch():
    w = _window()
    w.add_paths([_sub_file("Show S01E03.en.sdh.srt")])
    wait_for(lambda: w.items and all(i.state in ("ready", "failed", "skipped") for i in w.items))
    made = []
    real = mw.BatchWorker

    class Counting(real):
        def __init__(self, *a, **k):
            made.append(1)
            super().__init__(*a, **k)

    old_exec = dialogs.DecisionDialog.exec
    dialogs.DecisionDialog.exec = lambda self: (setattr(self, "choice", "guess"), QDialog.Accepted)[1]
    mw.DecisionDialog.exec = dialogs.DecisionDialog.exec
    mw.BatchWorker = Counting
    try:
        w.start_save()
        w.start_save()
        w.start_save()
        wait_for(lambda: not w.busy(), 60)
    finally:
        mw.BatchWorker = real
        dialogs.DecisionDialog.exec = old_exec
        mw.DecisionDialog.exec = old_exec
    assert len(made) == 1, made
    w.close()


def test_tmdb_offline_or_wrong_key_leaves_names_alone():
    import urllib.error
    from subscrub import batch, media, naming

    def offline(req, timeout=None):
        raise urllib.error.URLError("no network")

    def refused(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    old_sleep = naming.time.sleep
    naming.time.sleep = lambda _s: None
    try:
        for opener, text in ((offline, "Couldn't reach TMDB"), (refused, "didn't accept the API key")):
            w = _window(rename_on="true", video_mode="replace", tmdb_key="k" * 32)
            w._tmdb = naming.TMDB("k" * 32, opener=opener)
            it = mw.Item(path=os.path.join(TMP, "Some.Show.S01E02.1080p.mkv"), kind="video")
            it.info = media.MediaInfo(it.path, "mkv", "matroska", 600.0, 1000, [], [])
            it.state = "ready"
            w.items.append(it)
            w._pump_names()
            wait_for(lambda: it.name_state in ("error", "done"), 20)
            assert it.name_state == "error" and text in it.name_note, (it.name_state, it.name_note)
            assert batch.new_name(it) is None and w._new_name(it) is None          # the file keeps its name
            assert w.name_line(it)[1] == "alert" and text in w.name_line(it)[0]
            w.items.remove(it)
            w.close()
    finally:
        naming.time.sleep = old_sleep


def test_review_shortcuts_move_and_keep_original():
    from PySide6.QtGui import QKeySequence, QShortcut

    def press(key: str):
        # an offscreen window is never the "active" one, so the keystroke itself can't be sent: fire its shortcut
        sc = [x for x in w.findChildren(QShortcut) if x.key() == QKeySequence(key)]
        assert len(sc) == 1, (key, len(sc))
        sc[0].activated.emit()

    w = _window()
    w.add_paths([_sub_file("Show S01E04.en.sdh.srt")])
    wait_for(lambda: w.items and all(i.state in ("ready", "failed", "skipped") for i in w.items))
    w.tabs.changed.emit("review")
    pump(0.3)
    w.filter.set_value("all")
    w._filter_changed("all")
    pump(0.2)
    first = w.preview.current_row()
    press("J")
    pump(0.05)
    second = w.preview.current_row()
    assert second is not None and (first is None or second.cue != first.cue)
    press("K")
    pump(0.05)
    assert w.preview.current_row().cue == (first.cue if first is not None else second.cue) or first is None
    # O puts the selected changed line back as it was, and again takes the cleaned version
    it = w.items[0]
    w.preview.step(1)
    row = w.preview.current_row()
    assert row is not None and row.status != "kept"
    press("O")
    pump(0.1)
    assert it.overrides.get(row.cue) == "orig"
    press("O")
    pump(0.1)
    assert it.overrides.get(row.cue) != "orig"
    # the keys do nothing on the Files screen
    w.show_files()
    pump(0.1)
    press("O")
    assert it.overrides.get(row.cue) != "orig"
    w.close()


if __name__ == "__main__":
    import inspect
    import sys
    fns = [f for n, f in sorted(globals().items()) if n.startswith("test_") and inspect.isfunction(f)]
    try:
        for f in fns:
            f()
            print("ok ", f.__name__)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"{len(fns)} tests passed")
    sys.exit(0)
