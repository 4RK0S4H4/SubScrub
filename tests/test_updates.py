"""The update check and download, with a stand-in for GitHub (no network).  python -m tests.test_updates"""
import hashlib
import io
import json
import os
import tempfile
import threading
import urllib.error

from subscrub import updates


class Resp(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.length = len(data)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def release(tag="v2.9.0", assets=None, **kw):
    d = {"tag_name": tag, "html_url": "https://github.com/x/y/releases/tag/" + tag, "assets": assets or [], **kw}
    return lambda req, timeout=None: Resp(json.dumps(d).encode())


def asset(data=b"installer-bytes", **kw):
    return {"name": "SubScrub-Setup.exe", "browser_download_url": "https://example.test/SubScrub-Setup.exe",
            "size": len(data), "digest": "sha256:" + hashlib.sha256(data).hexdigest(), **kw}


def test_versions():
    assert updates.parse_version("v2.8.0") == (2, 8, 0) and updates.parse_version("2.9") == (2, 9)
    assert updates.parse_version("nightly") is None and updates.parse_version("") is None
    assert updates.is_newer("v2.9.0", "2.8.0") and updates.is_newer("2.10.0", "2.9.9") and updates.is_newer("3", "2.9.9")
    assert not updates.is_newer("2.8.0", "2.8.0") and not updates.is_newer("2.8", "2.8.0")
    assert not updates.is_newer("2.7.3", "2.8.0") and not updates.is_newer("junk", "2.8.0")


def test_a_newer_release_is_found_with_its_installer():
    u = updates.check("2.8.0", opener=release(assets=[{"name": "SubScrub.exe", "browser_download_url": "x"}, asset()]))
    assert u.version == "2.9.0" and u.can_install and u.sha256 and u.installer_size == len(b"installer-bytes")
    assert u.page.endswith("v2.9.0")


def test_no_installer_means_only_the_page():
    u = updates.check("2.8.0", opener=release(assets=[{"name": "SubScrub.exe", "browser_download_url": "x"}]))
    assert u is not None and not u.can_install


def test_up_to_date_draft_prerelease_and_no_release():
    assert updates.check("2.9.0", opener=release("v2.9.0")) is None
    assert updates.check("2.8.0", opener=release("v2.7.3")) is None
    assert updates.check("2.8.0", opener=release("v3.0.0", draft=True)) is None
    assert updates.check("2.8.0", opener=release("v3.0.0", prerelease=True)) is None

    def none_yet(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    assert updates.check("2.8.0", opener=none_yet) is None


def test_failures_say_what_happened():
    def offline(req, timeout=None):
        raise urllib.error.URLError("no network")

    def limited(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "rate limit", {}, None)

    def garbage(req, timeout=None):
        return Resp(b"<html>")
    for opener, text in ((offline, "Couldn't reach GitHub"), (limited, "limiting requests"), (garbage, "couldn't be read")):
        try:
            updates.check("2.8.0", opener=opener)
            raise AssertionError("expected an error")
        except updates.UpdateError as e:
            assert text in str(e), (text, str(e))


def test_download_is_checked():
    data = b"installer-bytes" * 5000
    u = updates.check("2.8.0", opener=release(assets=[asset(data)]))
    seen = []
    path = updates.download(u, tempfile.mkdtemp(), seen.append, opener=lambda req, timeout=None: Resp(data))
    assert open(path, "rb").read() == data and seen and seen[-1] == 1.0

    for bad, why in ((data[:-1], "incomplete"), (b"x" * len(data), "checksum")):
        try:
            updates.download(u, tempfile.mkdtemp(), opener=lambda req, timeout=None, b=bad: Resp(b))
            raise AssertionError("expected an error")
        except updates.UpdateError as e:
            assert why in str(e)
    folder = tempfile.mkdtemp()
    try:
        updates.download(u, folder, opener=lambda req, timeout=None: Resp(data[:-1]))
    except updates.UpdateError:
        pass
    assert not os.listdir(folder), "a bad download is not left behind"


def test_download_can_be_stopped():
    data = b"x" * 300000
    u = updates.Update("2.9.0", "p", "https://example.test/s", len(data))
    ev = threading.Event()
    ev.set()
    try:
        updates.download(u, tempfile.mkdtemp(), cancel=ev, opener=lambda req, timeout=None: Resp(data))
        raise AssertionError("expected an error")
    except updates.UpdateError as e:
        assert "stopped" in str(e)


def test_only_an_installed_copy_updates_itself():
    assert not updates.is_installed()                     # running from source


if __name__ == "__main__":
    import inspect
    import sys
    fns = [f for n, f in sorted(globals().items()) if n.startswith("test_") and inspect.isfunction(f)]
    for f in fns:
        f()
        print("ok ", f.__name__)
    print(f"{len(fns)} tests passed")
    sys.exit(0)
