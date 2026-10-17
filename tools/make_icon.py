"""Regenerate assets/subscrub.ico and assets/subscrub.png from logo_pixmap, so the exe, installer and window icon
always match the in-app mark. Every size is drawn by Qt at that size (16-24 px get the bolder small version), not
scaled down from 256.

    python tools/make_icon.py
"""
import io
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from subscrub.ui.widgets import logo_pixmap  # noqa: E402

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render(size: int) -> Image.Image:
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    logo_pixmap(size, 1.0).toImage().save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


def main():
    QApplication([])
    images = [render(s) for s in SIZES]
    big = images[-1]
    big.save(ROOT / "assets" / "subscrub.png", optimize=True)
    big.save(ROOT / "assets" / "subscrub.ico", sizes=[(s, s) for s in SIZES], append_images=images[:-1])
    print("wrote assets/subscrub.ico", ", ".join(map(str, SIZES)), "and assets/subscrub.png")


if __name__ == "__main__":
    main()
