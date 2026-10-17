"""Remake the README's screenshots from the generated sample library (never from real files).

    python -m tests.make_samples --library
    QT_QPA_PLATFORM=offscreen python tools/readme_screenshots.py

Runs tools/ui_screenshots.py at 1600x1000 in both themes (with a real save, so it needs ffmpeg and mkvmerge), then
copies the six screens the README shows into docs/screenshots/<name>-dark.png and -light.png, optimised.
"""
import os
import subprocess
import sys
import tempfile

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PICK = {"welcome": "01-welcome", "files": "02-files", "review": "06-review-look-closer",
        "not-sure": "10-title-dialog", "settings": "08-settings-1-videos", "saved": "17-saved"}


def main():
    out = os.path.join(ROOT, "docs", "screenshots")
    os.makedirs(out, exist_ok=True)
    work = tempfile.mkdtemp(prefix="subscrub_readme_")
    for theme in ("dark", "light"):
        shots = os.path.join(work, theme)
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "ui_screenshots.py"), shots,
                        os.path.join(ROOT, "samples", "library"), theme, "--save", "--size", "1600x1000"], check=True)
        for name, prefix in PICK.items():
            im = Image.open(os.path.join(shots, f"{prefix}-{theme}-1600x1000.png")).convert("RGB")
            im.save(os.path.join(out, f"{name}-{theme}.png"), optimize=True)
    print(f"Wrote {len(PICK) * 2} screenshots to {out}")


if __name__ == "__main__":
    main()
