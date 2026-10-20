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


# --- files SubScrub no longer needs (a .srt now inside the video): never deleted, kept when the Recycle Bin can't
def _refuse(p):
    raise OSError("no Recycle Bin here")


def test_recycle_failure_keeps_the_file():
    d, src, work = _setup(_refuse)
    srt = d / "Show.en.sdh.srt"
    srt.write_text("x", encoding="utf-8")
    logged = []
    what = media.recycle(srt, logged.append)
    assert srt.exists() and what.startswith(media.KEPT), what
    assert any(str(srt) in line for line in logged), logged          # the log says exactly where it is


def test_network_drive_keeps_the_file():
    d, src, work = _setup(_to_bin)
    media.is_network_path = lambda p: True
    srt = d / "Show.en.sdh.srt"
    srt.write_text("x", encoding="utf-8")
    assert media.trash(srt) == "network drives have no Recycle Bin" and srt.exists()


def test_bin_reports_error_but_the_file_went():
    def flaky(p):
        _to_bin(p)
        raise OSError("operation aborted")
    d, src, work = _setup(flaky)
    srt = d / "Show.en.sdh.srt"
    srt.write_text("x", encoding="utf-8")
    assert media.recycle(srt) == "moved to the Recycle Bin"


def test_extras_are_kept_with_a_note():
    d, src, work = _setup(_refuse)
    srt = d / "Show.en.sdh.srt"
    srt.write_text("x", encoding="utf-8")
    note = media._recycle_extras([str(srt)], None)
    assert srt.exists() and f"“{srt.name}” {media.KEPT}" in note, note


def test_subtitle_replaced_in_place_goes_to_the_bin():
    d, src, work = _setup(_to_bin)
    srt = d / "Show.srt"
    srt.write_text("old", encoding="utf-8")
    note = media._replace_text_file(srt, srt, "new", None)
    assert srt.read_text(encoding="utf-8-sig") == "new" and BIN[-1] == "old" and "Recycle Bin" in note
    assert not (d / f"Show{media.TMP_MARK}.srt").exists()


def test_subtitle_replace_bin_refuses_and_nothing_changes():
    d, src, work = _setup(_refuse)
    srt = d / "Show.srt"
    srt.write_text("old", encoding="utf-8")
    try:
        media._replace_text_file(srt, srt, "new", None)
        raise AssertionError("expected an error")
    except media.ToolError as e:
        assert "Nothing was changed" in str(e)
    assert srt.read_text(encoding="utf-8") == "old"
    assert not (d / f"Show{media.TMP_MARK}.srt").exists()


def _info(subs=1, attach=1, chapters=2, kind="mkv", cleaned=True, **kw):
    streams = [{"codec_type": "video"}, {"codec_type": "audio"}] + [{"codec_type": "attachment"}] * attach
    tracks = [media.SubTrack(2 + i, i, "subrip", "eng", "English", False, False, False, {}, cleaned=cleaned and i == 0)
              for i in range(subs)]
    return media.MediaInfo("x.mkv", kind, "matroska", 10.0, 1000, streams, tracks, chapters=chapters)


def _verify(after, text="1\n00:00:01,000 --> 00:00:02,000\nHi.\n\n2\n00:00:03,000 --> 00:00:04,000\nYo.\n",
            cues=2):
    """Run media._verify with a stubbed probe and a stubbed cue count; returns the error text or ''."""
    from subscrub import subformats
    out = Path(tempfile.mkdtemp()) / "new.mkv"
    out.write_text("data", encoding="utf-8")
    before = _info()
    before.size = 1000
    after.size = 1000
    old_probe, old_extract, old_load = media.probe, media.extract_subs, subformats.load
    media.probe = lambda tools, path, log=None: after
    media.extract_subs = lambda tools, info, tracks, d, *a, **k: {t.index: "x" for t in tracks}
    subformats.load = lambda path, fmt=None: types.SimpleNamespace(cues=[0] * cues)
    try:
        media._verify(None, before, str(out), len(after.subs), None, new_text=text, fmt="srt", new_hint=("eng", "English"))
        return ""
    except media.ToolError as e:
        return str(e)
    finally:
        media.probe, media.extract_subs, subformats.load = old_probe, old_extract, old_load


def test_verify_accepts_a_good_copy():
    assert _verify(_info()) == ""


def test_verify_refuses_lost_fonts():
    assert "attached files" in _verify(_info(attach=0))


def test_verify_refuses_lost_chapters():
    assert "chapters changed" in _verify(_info(chapters=0))


def test_verify_refuses_a_track_that_lost_cues():
    err = _verify(_info(), cues=0)
    assert "0 lines instead of 2" in err and "nothing was replaced" in err


def test_verify_finds_the_new_track_by_name_when_the_tag_is_lost():
    assert _verify(_info(cleaned=False), cues=0).endswith("0 lines instead of 2")
    assert _verify(_info(cleaned=False), cues=2) == ""


if __name__ == "__main__":
    import inspect
    saved = (media.who_has_open, media.is_network_path, sys.modules.get("send2trash"))
    fns = [f for n, f in sorted(globals().items()) if n.startswith("test_") and inspect.isfunction(f)]
    for f in fns:
        f()
        print("ok ", f.__name__)
    print(f"{len(fns)} tests passed")
    sys.exit(0)
