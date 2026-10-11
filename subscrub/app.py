"""App entry point: sets up Qt, fonts, crash logging, then opens the main window."""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

from . import APP_NAME


def _crash_log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p / "crash.log"


def _install_excepthook():
    def hook(etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        try:
            with open(_crash_log_path(), "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            if QApplication.instance():
                QMessageBox.critical(None, f"{APP_NAME} hit a bug",
                                     f"Something went wrong:\n\n{value}\n\nDetails were saved to:\n{_crash_log_path()}")
        except Exception:
            pass
        sys.__excepthook__(etype, value, tb)
    sys.excepthook = hook


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    _install_excepthook()
    if sys.platform == "win32":
        try:  # own taskbar icon/group instead of python.exe's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SubScrub.App")
        except Exception:
            pass

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .ui import theme
    from .ui.widgets import logo_pixmap
    from .ui.main_window import MainWindow

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setStyle("Fusion")
    theme.load_fonts()                      # Inter, bundled; falls back to Segoe UI
    app.setFont(theme.app_font())
    ico = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ico.addPixmap(logo_pixmap(s, 1.0))
    app.setWindowIcon(ico)

    initial = [a for a in argv[1:] if os.path.exists(a)]
    win = MainWindow(initial)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
