"""Awkward files through the real save path (the batch worker, run in place). Needs ffmpeg and mkvmerge.

Every case must either save correctly or say clearly why it can't, and in the second case the original has to be
byte for byte what it was.   python -m tests.test_edge
"""
import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
TMP = tempfile.mkdtemp(prefix="subscrub_edge_")
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "xdg")          # the Recycle Bin of a Linux runner stays in here

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(["x"])

from subscrub import batch, media  # noqa: E402
from subscrub.cleaner import CleanOptions  # noqa: E402
from subscrub.ui.workers import BatchWorker  # noqa: E402
from tests import make_samples  # noqa: E402

TOOLS = media.find_tools()
CASES: dict[str, Path] = {}


def cases() -> dict[str, Path]:
    if not CASES:
        CASES.update(make_samples.edge_cases(TOOLS, Path(TMP) / "edge"))
    return CASES


def digest(folder: Path) -> dict:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def save(path: Path, tools=None, **cfg_over) -> dict:
    """Run the batch worker on one video exactly as the Save button does. Returns what it reported."""
    cfg = {"opts": CleanOptions(), "video_mode": "replace", "sub_mode": "replace", "keep": False, "default": True,
           "name": "", "recycle": True, "overwrite": True, "pref": None, "drop_langs": False, "keep_lang": "eng",
           "batch_id": "test"}
    cfg.update(cfg_over)
    job = {"uid": 1, "path": str(path), "kind": "video", "info": None, "docs": {}, "sdh_counts": {}, "sel": None,
           "manual": False, "opts": None, "overrides": {}, "force": False, "sidecars": [], "dropped": [],
           "policy": "guess", "new_name": ""}
    got = {"done": None, "skipped": None, "failed": None, "held": None}
    w = BatchWorker([job], cfg, tools or TOOLS, lambda *_: None)
    w.item_done.connect(lambda u, d: got.__setitem__("done", d))
    w.item_skipped.connect(lambda u, m: got.__setitem__("skipped", m))
    w.item_failed.connect(lambda u, m: got.__setitem__("failed", m))
    w.item_held.connect(lambda u, m: got.__setitem__("held", m))
    w.run()
    return got


def leftovers(folder: Path) -> list:
    return [p.name for p in folder.rglob("*") if media.TMP_MARK in p.name]


def test_no_subtitles():
    p = cases()["no subtitles"]
    before = digest(p.parent)
    got = save(p)
    assert got["skipped"] and not got["failed"], got
    assert digest(p.parent) == before


def test_picture_subtitles_only():
    p = cases()["picture subtitles only"]
    info = media.probe(TOOLS, str(p))
    if not info.subs or info.subs[0].text_based:
        print("    (picture-subtitle sample could not be made on this machine; skipped)")
        return
    before = digest(p.parent)
    got = save(p)
    assert got["skipped"] == "no text subtitles", got
    assert digest(p.parent) == before


def test_two_sdh_tracks_one_replaces_its_original():
    p = cases()["two sdh"]
    got = save(p)
    assert got["done"] and not got["failed"], got
    after = media.probe(TOOLS, str(p))
    assert len(after.subs) == 2 and sum(t.default for t in after.subs) == 1
    assert [t.title for t in after.subs if t.default] == ["English"]
    assert not leftovers(p.parent)


def test_empty_sdh_track_changes_nothing():
    if "empty sdh" not in cases():
        print("    (no mkvmerge: skipped)")
        return
    p = cases()["empty sdh"]
    before = digest(p.parent)
    got = save(p)
    assert not got["failed"] and not got["done"], got        # nothing to clean: left alone
    assert digest(p.parent) == before


def test_track_where_every_line_is_sdh():
    p = cases()["every line is sdh"]
    before = digest(p.parent)
    got = save(p)
    # a cleaned copy with no lines at all would replace the SDH track with nothing: refused, original untouched
    assert got["skipped"] and "every line" in got["skipped"], got
    assert digest(p.parent) == before


def test_awkward_file_name():
    p = cases()["It's & 100% [Rock] Café 🎬"]
    got = save(p)
    assert got["done"] and not got["failed"], got
    assert p.exists() and not leftovers(p.parent)
    after = media.probe(TOOLS, str(p))
    assert [t.title for t in after.subs] == ["English"] and after.subs[0].default


def test_awkward_file_name_without_mkvmerge():
    # a fresh copy, so the FFmpeg path is used as well
    src = cases()["It's & 100% [Rock] Café 🎬"].parent
    dst = Path(TMP) / "copy2"
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    got = save(next(dst.glob("*.mkv")), tools=media.Tools(TOOLS.ffmpeg, TOOLS.ffprobe, None))
    assert got["done"] or got["skipped"], got


def _sidecar_case(name: str):
    v = cases()[name]
    srt = next(v.parent.glob("*.srt"))
    before = digest(v.parent)
    got = save(v)
    return v, srt, before, got


def test_zero_byte_sidecar_is_ignored_safely():
    v, srt, before, got = _sidecar_case("zero byte sidecar")
    assert got["skipped"] and "empty" in got["skipped"], got      # nothing to embed
    assert digest(v.parent) == before                              # the video and the (empty) .srt are untouched


def test_utf16_sidecar_is_cleaned_and_embedded():
    v, srt, before, got = _sidecar_case("utf-16 sidecar")
    assert got["done"], got
    info = media.probe(TOOLS, str(v))
    assert [t.title for t in info.subs] == ["English"]
    assert not srt.exists(), "the .srt goes to the Recycle Bin once it is inside the video"


def test_cp1252_sidecar_keeps_its_accents():
    v, srt, before, got = _sidecar_case("cp1252 sidecar")
    assert got["done"], got
    info = media.probe(TOOLS, str(v))
    out = Path(TMP) / "cp1252.srt"
    paths = media.extract_subs(TOOLS, info, info.subs, str(out.with_suffix("")))
    assert "Café is closed." in Path(next(iter(paths.values()))).read_text(encoding="utf-8")


def test_path_that_is_too_long_for_windows_is_refused_first():
    if os.name == "nt":
        print("    (needs a path that Windows itself can't create here: skipped)")
        return
    d = Path(TMP) / "long"
    for _ in range(3):
        d = d / ("x" * 90)
    d.mkdir(parents=True)
    v = d / ("Show with a rather long name " * 3 + ".mkv")
    shutil.copy(cases()["two sdh"], v)
    before = digest(d)
    old = media.PATH_LIMIT
    media.PATH_LIMIT = 259
    try:
        got = save(v)
    finally:
        media.PATH_LIMIT = old
    assert got["failed"] and "too long" in got["failed"].lower(), got
    assert digest(d) == before


def test_cancel_leaves_no_temp_file_and_the_original_alone():
    src = cases()["two sdh"].parent
    dst = Path(TMP) / "cancel"
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    v = next(dst.glob("*.mkv"))
    before = digest(dst)
    real = media._verify

    def cancelled(*a, **k):                      # the new file exists at this point; the user presses Cancel
        raise media.Cancelled()
    media._verify = cancelled
    try:
        got = save(v)
    finally:
        media._verify = real
    assert got["failed"] == "__cancelled__", got
    assert not leftovers(dst) and digest(dst) == before


if __name__ == "__main__":
    import inspect
    if not (TOOLS.ffmpeg and TOOLS.ffprobe):
        sys.exit("FFmpeg is needed for these tests.")
    fns = [f for n, f in sorted(globals().items()) if n.startswith("test_") and inspect.isfunction(f)]
    try:
        for f in fns:
            f()
            print("ok ", f.__name__)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"{len(fns)} tests passed")
