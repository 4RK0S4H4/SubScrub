"""Finding out whether a newer SubScrub exists, and installing it.

The newest GitHub Release is the source of truth. If it has a "SubScrub-Setup.exe", an installed copy of the app can
download it, check it, run it quietly and quit (the installer starts the new version). Anything else (a loose
SubScrub.exe, running from source) just opens the Releases page. Nothing is ever downloaded without a click.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import APP_NAME

REPO = "4RK0S4H4/SubScrub"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
INSTALLER_NAME = "SubScrub-Setup.exe"
SILENT_ARGS = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]


class UpdateError(Exception):
    """The update check or download didn't work; the message is for the user."""


@dataclass
class Update:
    version: str                 # "2.9.0"
    page: str                    # the release's web page
    installer_url: str = ""      # "" = no installer attached to that release
    installer_size: int = 0
    sha256: str = ""             # "" = GitHub didn't say

    @property
    def can_install(self) -> bool:
        return bool(self.installer_url)


def parse_version(text: str) -> tuple[int, ...] | None:
    """'v2.9.0' / '2.9' -> (2, 9, 0) / (2, 9). None when it isn't a version."""
    m = re.fullmatch(r"\s*v?(\d+(?:\.\d+){0,3})\s*", text or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def _get(url: str, opener, timeout: float, accept: str = "application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": APP_NAME})
    return (opener or urllib.request.urlopen)(req, timeout=timeout)


def check(current: str, opener=None, timeout: float = 10.0) -> Update | None:
    """The newer release, or None when this is the newest. Raises UpdateError with a plain message."""
    try:
        with _get(API_URL, opener, timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None                                   # no release published yet
        if e.code in (403, 429):
            raise UpdateError("GitHub is limiting requests right now. Try again in a little while.") from e
        raise UpdateError(f"GitHub answered with an error ({e.code}).") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError(f"Couldn't reach GitHub ({getattr(e, 'reason', e)}).") from e
    except ValueError as e:
        raise UpdateError("GitHub's answer couldn't be read.") from e
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "")
    if not is_newer(tag, current):
        return None
    version = ".".join(str(x) for x in parse_version(tag))
    upd = Update(version=version, page=str(data.get("html_url") or RELEASES_URL))
    for a in data.get("assets") or []:
        if a.get("name") == INSTALLER_NAME and a.get("browser_download_url"):
            upd.installer_url = a["browser_download_url"]
            upd.installer_size = int(a.get("size") or 0)
            digest = str(a.get("digest") or "")
            if digest.lower().startswith("sha256:"):
                upd.sha256 = digest[7:].lower()
    return upd


def is_installed() -> bool:
    """Running from an installed copy (the installer puts an uninstaller next to the exe)."""
    return bool(getattr(sys, "frozen", False)) and (Path(sys.executable).parent / "unins000.exe").is_file()


def download(upd: Update, folder: str | None = None, on_progress: Callable[[float], None] | None = None,
             cancel: threading.Event | None = None, opener=None, timeout: float = 30.0) -> str:
    """Download the installer into a fresh folder and check it. Returns its path."""
    if not upd.installer_url:
        raise UpdateError("This release has no installer attached.")
    folder = folder or tempfile.mkdtemp(prefix="subscrub_update_")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, INSTALLER_NAME)
    h = hashlib.sha256()
    got = 0
    try:
        with _get(upd.installer_url, opener, timeout, "application/octet-stream") as r, open(dest, "wb") as f:
            total = int(getattr(r, "length", None) or upd.installer_size or 0)
            while True:
                if cancel is not None and cancel.is_set():
                    raise UpdateError("The download was stopped.")
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if on_progress and total:
                    on_progress(min(1.0, got / total))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        _remove(dest)
        raise UpdateError(f"The download didn't finish ({getattr(e, 'reason', e)}).") from e
    except UpdateError:
        _remove(dest)
        raise
    if upd.installer_size and got != upd.installer_size:
        _remove(dest)
        raise UpdateError("The download is incomplete, so it wasn't installed.")
    if upd.sha256 and h.hexdigest() != upd.sha256:
        _remove(dest)
        raise UpdateError("The download doesn't match its checksum, so it wasn't installed.")
    return dest


def _remove(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


def run_installer(path: str) -> None:
    """Start the installer quietly and leave it running on its own; the caller quits the app right after."""
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([path, *SILENT_ARGS], close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **kw)
