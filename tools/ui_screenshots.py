"""Drive the real UI offscreen and save 1920x1080 screenshots of every main screen, in one theme.

    QT_QPA_PLATFORM=offscreen python tools/ui_screenshots.py <out dir> <folder of sample videos> dark|light [--save]

Make sample videos first with `python -m tests.make_samples` (needs ffmpeg), or point it at any folder of MKVs
with SDH subtitle tracks. Videos are COPIED into <out dir>/work, so the originals are never touched.
--save also runs a real save (needs ffmpeg + mkvmerge) and captures the saving / saved screens.

Dialogs are composited over the dimmed window, the way the user sees them. Every image is one screen at
1920x1080; never combine them into a grid. After running, open every image at full size and do the visual
check described in CLAUDE.md.
"""
import os
import shutil
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402


OUT, MEDIA, THEME = os.path.abspath(sys.argv[1]), sys.argv[2], sys.argv[3]
SAVE = "--save" in sys.argv
os.makedirs(OUT, exist_ok=True)

app = QApplication(["x"])

app.setStyle("Fusion")
from subscrub.ui import theme as _th  # noqa: E402
_th.load_fonts()
app.setFont(_th.app_font())
from subscrub.ui import main_window as mw  # noqa: E402

# Keep the user's real settings (TMDB key, preferences) safe: the app's QSettings reads and writes a throwaway
# INI file in the output folder instead of the registry / ~/.config.
INI = os.path.join(OUT, "_settings.ini")
if os.path.exists(INI):
    os.remove(INI)


class _ThrowawaySettings(QSettings):
    def __init__(self, *_a, **_k):
        super().__init__(INI, QSettings.IniFormat)


mw.QSettings = _ThrowawaySettings
st = _ThrowawaySettings()
st.setValue("theme", THEME)
st.setValue("confirm_replace", "false")
st.sync()


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def wait_for(cond, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return
        time.sleep(0.02)
    raise TimeoutError


def composite(win_png, dlg_png, out_png):
    """Put a dialog grab centred over the dimmed window grab, with a soft shadow."""
    from PIL import Image, ImageFilter
    base = Image.open(win_png).convert("RGBA")
    d = Image.open(dlg_png).convert("RGBA")
    base = Image.alpha_composite(base, Image.new("RGBA", base.size, (0, 0, 0, 110 if THEME == "dark" else 70)))
    x, y = (base.width - d.width) // 2, (base.height - d.height) // 2
    sh = Image.new("RGBA", base.size, (0, 0, 0, 0))
    sh.paste((0, 0, 0, 120), (x + 4, y + 10, x + d.width + 4, y + d.height + 10))
    base = Image.alpha_composite(base, sh.filter(ImageFilter.GaussianBlur(18)))
    base.alpha_composite(d, (x, y))
    base.convert("RGB").save(out_png)
    os.remove(win_png)
    os.remove(dlg_png)


def shot(widget, name):
    path = os.path.join(OUT, f"{name}-{THEME}.png")
    widget.grab().save(path)
    return path


work = os.path.join(OUT, "work")
shutil.rmtree(work, ignore_errors=True)
os.makedirs(work)
for n in sorted(os.listdir(MEDIA)):
    if n.lower().endswith((".mkv", ".mp4")):
        shutil.copy(os.path.join(MEDIA, n), os.path.join(work, n))

w = mw.MainWindow()
w.resize(1920, 1080)
w.show()
pump(0.3)
shot(w, "01-welcome")
w.add_paths([work])
wait_for(lambda: w.items and all(i.state in ("ready", "failed", "skipped") for i in w.items))
pump(0.5)
shot(w, "02-files")

flagged = [i for i in w.items if i.result and i.result.unresolved()]
first = flagged[0] if flagged else next((i for i in w.items if i.result), None)
if first:
    w.open_review(first.uid)
    pump(0.6)
    shot(w, "03-review-to-check")
    w.filter.set_value("changes")
    w._filter_changed("changes")
    pump(0.4)
    shot(w, "04-review-changes")
    w.show_files()
    pump(0.3)

bg = shot(w, "_bg")
dlg = mw.SettingsDialog(w)
dlg.show()
pump(0.4)
composite(bg, shot(dlg, "_dlg"), os.path.join(OUT, f"05-settings-{THEME}.png"))
dlg.reject()
pump(0.1)

if flagged:
    others = len([i for i in w.items if i.pending]) - len(flagged)
    dd = mw.DecisionDialog(w, flagged, others, 0)
    dd.show()
    pump(0.3)
    bg = shot(w, "_bg")
    composite(bg, shot(dd, "_dlg"), os.path.join(OUT, f"06-unsure-dialog-{THEME}.png"))
    dd.reject()

if SAVE:
    def fake_exec(self):
        self.choice = "orig"
        return QDialog.Accepted
    mw.DecisionDialog.exec = fake_exec
    w.start_save()
    pump(0.2)
    shot(w, "07-saving")
    wait_for(lambda: not w.busy(), 300)
    pump(0.4)
    shot(w, "08-saved")

w.close()
print("Screenshots in", OUT, "- now open every one at full size and do the visual check.")
