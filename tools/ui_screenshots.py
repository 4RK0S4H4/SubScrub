"""Drive the real UI offscreen and save screenshots of every screen and dialog, in one theme.

    QT_QPA_PLATFORM=offscreen python tools/ui_screenshots.py <out dir> <video folder> dark|light [--save] [--size 1280x800]

Make the sample library first with `python -m tests.make_samples --library` (needs ffmpeg) and point this at
samples/library, or at any folder of MKVs with SDH subtitle tracks. Videos are COPIED into <out dir>/work, so the
originals are never touched. TMDB is a stand-in (no network, no key needed). --save also runs a real save (needs
ffmpeg + mkvmerge) and captures the saving and saved screens.

Dialogs open inside the window, so each image is the whole window with the dialog over it, the way the user sees
it. Every image is one screen; never combine them into a grid. After running, open every image at full size and do
the visual check described in CLAUDE.md.
"""
import os
import shutil
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SUBSCRUB_NO_UPDATE_CHECK", "1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPoint, QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

OUT, MEDIA, THEME = os.path.abspath(sys.argv[1]), sys.argv[2], sys.argv[3]
SAVE = "--save" in sys.argv
SIZE = (1920, 1080)
if "--size" in sys.argv:
    w_, h_ = sys.argv[sys.argv.index("--size") + 1].lower().split("x")
    SIZE = (int(w_), int(h_))
TAG = "" if SIZE == (1920, 1080) else f"-{SIZE[0]}x{SIZE[1]}"
os.makedirs(OUT, exist_ok=True)

app = QApplication(["x"])
app.setStyle("Fusion")
from subscrub.ui import theme as _th  # noqa: E402
_th.load_fonts()
app.setFont(_th.app_font())
from subscrub import naming  # noqa: E402
from subscrub.ui import dialogs, main_window as mw  # noqa: E402

# Keep the user's real settings (TMDB key, preferences) safe: the app's QSettings reads and writes a throwaway
# INI file in the output folder instead of the registry / ~/.config.
INI = os.path.join(OUT, "_settings.ini")
if os.path.exists(INI):
    os.remove(INI)


class _ThrowawaySettings(QSettings):
    def __init__(self, *_a, **_k):
        super().__init__(INI, QSettings.IniFormat)


# ---- a stand-in for TMDB, so the screenshots show real-looking new names
MOVIES = {693134: dict(id=693134, title="Dune: Part Two", original_title="Dune: Part Two", release_date="2024-02-27",
                       popularity=95, runtime=167),
          78: dict(id=78, title="Blade Runner", original_title="Blade Runner", release_date="1982-06-25",
                   popularity=60, runtime=117)}
SHOWS = {63639: dict(id=63639, name="The Expanse", original_name="The Expanse", first_air_date="2015-12-14",
                     popularity=60),
         95396: dict(id=95396, name="Severance", original_name="Severance", first_air_date="2022-02-17", popularity=80),
         96074: dict(id=96074, name="Slow Horses", original_name="Slow Horses", first_air_date="2022-04-01",
                     popularity=50),
         96075: dict(id=96075, name="Slow Horses: Inside", original_name="Slow Horses: Inside",
                     first_air_date="2022-04-01", popularity=49),
         231003: dict(id=231003, name="Planet Earth III", original_name="Planet Earth III",
                      first_air_date="2023-10-22", popularity=30)}
SEASONS = {(63639, 1): [dict(episode_number=3, name="Remember the Cant", runtime=44)],
           (95396, 2): [dict(episode_number=4, name="Woe's Hollow", runtime=50)],
           (96074, 4): [], (96075, 4): [],
           (231003, 1): [dict(episode_number=2, name="Ocean", runtime=58)]}


class FakeTMDB:
    def __init__(self, *_a, **_k):
        pass

    def _search(self, pool, field, query, year=None):
        q = naming._norm_tokens(query)
        return [r for r in pool.values() if all(any(x.startswith(w) for x in naming._norm_tokens(r[field])) for w in q)
                and (year is None or (r.get("release_date") or r.get("first_air_date", "")).startswith(str(year)))]

    def search_movie(self, query, year=None):
        return self._search(MOVIES, "title", query, year)

    def movie(self, i):
        return MOVIES.get(i, {})

    def search_tv(self, query, year=None):
        return self._search(SHOWS, "name", query, year)

    def season(self, i, s):
        return {"episodes": SEASONS.get((i, s), [])}


mw.QSettings = _ThrowawaySettings
mw.naming.TMDB = FakeTMDB
st = _ThrowawaySettings()
st.setValue("theme", THEME)
st.setValue("tmdb_key", "stand-in")
st.sync()


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def wait_for(cond, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return
        time.sleep(0.02)
    raise TimeoutError


def path_for(name):
    return os.path.join(OUT, f"{name}-{THEME}{TAG}.png")


def shot(widget, name):
    widget.grab().save(path_for(name))


def shot_dialog(dlg, name, before=None):
    """A dialog opens over the whole window: its grab is the full screen as the user sees it."""
    dlg.show()
    pump(0.35)
    if before:
        before(dlg)
        pump(0.25)
    shot(dlg, name)
    dlg.reject()
    pump(0.15)


def shot_popup(win, popup, at: QPoint, name):
    """A menu is a separate window: paint it onto the window grab where it would open."""
    from PySide6.QtGui import QPainter
    popup.popup(at)
    pump(0.3)
    pm = win.grab()
    p = QPainter(pm)
    p.drawPixmap(win.mapFromGlobal(at), popup.grab())      # where it opens (the offscreen screen is small)
    p.end()
    pm.save(path_for(name))
    popup.hide()
    pump(0.1)


work = os.path.join(OUT, "work")
shutil.rmtree(work, ignore_errors=True)
os.makedirs(work)
for n in sorted(os.listdir(MEDIA)):
    if n.lower().endswith((".mkv", ".mp4", ".srt")):
        shutil.copy(os.path.join(MEDIA, n), os.path.join(work, n))

w = mw.MainWindow()
w.resize(*SIZE)
w.show()
pump(0.3)
w.demo.setDemo(1.0)                                   # mid-scrub: the struck-out sound tag
pump(0.1)
shot(w, "01-welcome")
w.add_paths([work])
wait_for(lambda: w.items and all(i.state in ("ready", "failed", "skipped") for i in w.items)
         and all(i.name_state not in ("", "looking") for i in w.items if i.kind == "video"))
pump(0.6)
shot(w, "02-files")
shot_popup(w, w.more_menu, w.more_btn.mapToGlobal(QPoint(w.more_btn.width() - w.more_menu.sizeHint().width(),
                                                         w.more_btn.height() + 6)), "03-menu")

flagged = [i for i in w.items if i.result and i.result.unresolved() and i.result.confidence != "low"]
if flagged:
    w.open_review(flagged[0].uid)
    pump(0.6)
    shot(w, "04-review-to-check")
    w.filter.set_value("changes")
    w._filter_changed("changes")
    pump(0.4)
    w.preview.step(1)
    pump(0.2)
    shot(w, "05-review-changes")
    w.show_files()
    pump(0.3)
low = next((i for i in w.items if i.result and i.result.confidence == "low"), None)
if low:
    w.open_review(low.uid)
    pump(0.6)
    shot(w, "06-review-look-closer")
    w.show_files()
    pump(0.3)
plain = next((i for i in w.items if i.result and not mw.batch.is_sdh(i, i.track())), None) if w.items else None
if plain:
    w.open_review(plain.uid)
    pump(0.6)
    shot(w, "07-review-no-sdh")
    w.show_files()
    pump(0.3)

for k, section in enumerate(("videos", "names", "edits", "look")):
    dlg = dialogs.SettingsDialog(w, section)
    shot_dialog(dlg, f"08-settings-{k + 1}-{section}")

if flagged:
    others = len([i for i in w.items if i.pending]) - len(flagged)
    shot_dialog(dialogs.DecisionDialog(w, flagged, others, 0), "09-unsure-dialog")
unsure = next((i for i in w.items if mw.batch.name_unsure(i)), None)
if unsure:
    shot_dialog(dialogs.NameDialog(w, unsure, FakeTMDB(), False), "10-title-dialog")
locked = [i for i in w.items if i.kind == "video"][:2]
shot_dialog(dialogs.LockDialog(w, {i.path: ["qBittorrent"] for i in locked}, locked), "11-in-use-dialog")
d = dialogs.MessageDialog(w, "Rewrite 4 files in place?",
                          "SubScrub will rewrite 4 files with the cleaned subtitles as the default track, and rename "
                          "4 files. Each new file is checked before the old one goes to the Recycle Bin.",
                          [("cancel", "Cancel", ""), ("go", "Rewrite 4 Files", "primary")],
                          "Very large files may be too big for the Recycle Bin, in which case Windows deletes them "
                          "instead.", "Don't ask again")
shot_dialog(d, "12-confirm-dialog")
shot_dialog(dialogs.ToolsDialog(w), "13-tools-dialog")
shot_dialog(dialogs.AboutDialog(w), "14-about-dialog")
shot_dialog(dialogs.LogDialog("\n".join(w.log_lines), w), "15-activity-log")

if SAVE:
    def fake_exec(self):
        self.choice = "orig"
        return QDialog.Accepted
    dialogs.DecisionDialog.exec = fake_exec
    mw.DecisionDialog.exec = fake_exec
    mw.ask = lambda *a, **k: ("keep" if "titles" in str(a[1:2]) else "go", False)
    w.confirm_replace = False
    w.start_save()
    pump(0.25)
    shot(w, "16-saving")
    wait_for(lambda: not w.busy(), 600)
    pump(0.5)
    shot(w, "17-saved")

# a tooltip, the drag-and-drop veil, and a fresh window without FFmpeg (warning banner, "FFmpeg Needed" button)
from PySide6.QtGui import QPainter  # noqa: E402
w.show_files()
pump(0.2)
row = next(iter(w.rows.values()), None)
if row is not None:
    from subscrub.ui.widgets import Tips  # noqa: E402
    tips = Tips.install(app)
    at = row.mapToGlobal(QPoint(220, row.height() - 6))
    tips.show_text(at, row.toolTip(), row)
    pump(0.2)
    pm = w.grab()
    p = QPainter(pm)
    p.drawPixmap(w.mapFromGlobal(at) + QPoint(12, 20), tips.win.grab())
    p.end()
    pm.save(path_for("18-tooltip"))
    tips.hide()
w.veil.text = "Drop to Add"
w.veil.setGeometry(w.root.rect())
w.veil.raise_()
w.veil.show()
pump(0.2)
shot(w, "19-drop")
w.veil.hide()
w.close()
mw.media.find_tools = lambda *_a, **_k: mw.media.Tools()
w3 = mw.MainWindow()
w3.resize(*SIZE)
w3.show()
pump(0.3)
w3.demo.setDemo(2.0)
pump(0.1)
shot(w3, "20-welcome-no-ffmpeg")
w3.close()
print("Screenshots in", OUT, "- now open every one at full size and do the visual check.")
