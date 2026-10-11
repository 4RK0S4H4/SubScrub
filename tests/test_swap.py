"""The step that replaces an original video with the new one must never leave you with neither file.
Run with:  python -m tests.test_swap"""
import os
import sys
import tempfile
import types
from pathlib import Path

from subscrub import media

BIN: list[str] = []


def _setup(trash):
    d = Path(tempfile.mkdtemp())
    src, work = d / "Show.mkv", d / "Show.subscrub-tmp.mkv"
    src.write_text("old", encoding="utf-8")
    work.write_text("new", encoding="utf-8")
    sys.modules["send2trash"] = types.SimpleNamespace(send2trash=trash)
    media.who_has_open = lambda paths: {}
    media.is_network_path = lambda p: False
    return d, src, work


def _to_bin(p):
    BIN.append(Path(p).read_text(encoding="utf-8"))
    os.remove(p)


def test_normal_swap():
    d, src, work = _setup(_to_bin)
    note = media._swap_in(src, work, src, None)
    assert src.read_text(encoding="utf-8") == "new" and not work.exists() and BIN[-1] == "old"
    assert "Recycle Bin" in note


def test_bin_reports_error_after_taking_the_file():
    def flaky(p):
        _to_bin(p)
        raise OSError("operation aborted")
    d, src, work = _setup(flaky)
    media._swap_in(src, work, src, None)
    assert src.read_text(encoding="utf-8") == "new"           # the new file survives


def test_bin_refuses_and_nothing_changes():
    def refuse(p):
        raise OSError("path too long")
    d, src, work = _setup(refuse)
    try:
        media._swap_in(src, work, src, None)
        raise AssertionError("expected an error")
    except media.ToolError as e:
        assert "Nothing was changed" in str(e)
    assert src.read_text(encoding="utf-8") == "old" and not work.exists()


def test_open_elsewhere_and_nothing_changes():
    d, src, work = _setup(_to_bin)
    media.who_has_open = lambda paths: {str(src): ["qBittorrent"]}
    try:
        media._swap_in(src, work, src, None)
        raise AssertionError("expected an error")
    except media.ToolError as e:
        assert "qBittorrent" in str(e)
    assert src.read_text(encoding="utf-8") == "old" and not work.exists()


def test_rename_fails_after_original_moved_keeps_new_file():
    d, src, work = _setup(_to_bin)
    real_replace, real_sleep = os.replace, media.time.sleep
    os.replace = lambda a, b: (_ for _ in ()).throw(PermissionError("in use"))
    media.time.sleep = lambda s: None
    try:
        media._swap_in(src, work, src, None)
        raise AssertionError("expected an error")
    except media.ToolError as e:
        assert work.name in str(e)
    finally:
        os.replace, media.time.sleep = real_replace, real_sleep
    assert work.read_text(encoding="utf-8") == "new"           # never deleted once the original has moved


if __name__ == "__main__":
    import inspect
    saved = (media.who_has_open, media.is_network_path, sys.modules.get("send2trash"))
    fns = [f for n, f in sorted(globals().items()) if n.startswith("test_") and inspect.isfunction(f)]
    for f in fns:
        f()
        print("ok ", f.__name__)
    print(f"{len(fns)} tests passed")
    sys.exit(0)
