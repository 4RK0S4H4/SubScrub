"""End-to-end pipeline test on real media (needs ffmpeg; mkvmerge optional).

    python -m tests.test_pipeline <folder with sample videos>
"""
import os
import shutil
import sys
import tempfile
import threading

from subscrub import media, subformats as sf
from subscrub.cleaner import CleanOptions, clean_doc  # noqa


def run_case(tools, video, pick, opts, label):
    logs = []
    info = media.probe(tools, video, logs.append)
    track = pick(info)
    tmp = tempfile.mkdtemp()
    paths = media.extract_subs(tools, info, info.subs, tmp, log=logs.append)
    doc = sf.load(paths[track.index], track.fmt)
    res = clean_doc(doc, CleanOptions())
    out = media.save(tools, info, track, res.render(), track.fmt, opts, log=logs.append)
    after = media.probe(tools, out.path)
    print(f"--- {label}: {os.path.basename(out.path)}  ({out.note})")
    for s in after.subs:
        print(f"    #{s.index} {s.codec:8} {s.language} default={int(s.default)} forced={int(s.forced)} "
              f"hi={int(s.hearing_impaired)} title={s.title!r}")
    shutil.rmtree(tmp, ignore_errors=True)
    return info, after, out


def main(folder):
    tools = media.find_tools()
    print("tools:", tools)
    work = tempfile.mkdtemp()
    for f in os.listdir(folder):
        if f.endswith((".mkv", ".mp4")):
            shutil.copy(os.path.join(folder, f), work)
    # the samples from `python -m tests.make_samples` work too
    for src, dst in (("Sample (SDH test).mkv", "Test Movie (2024).mkv"), ("Sample (SDH test).mp4", "Test Clip.mp4")):
        if not os.path.exists(os.path.join(work, dst)) and os.path.exists(os.path.join(work, src)):
            os.replace(os.path.join(work, src), os.path.join(work, dst))
    mkv = os.path.join(work, "Test Movie (2024).mkv")
    mp4 = os.path.join(work, "Test Clip.mp4")
    sdh = lambda i: next(t for t in i.subs if t.sdh_flagged and t.language == "eng")

    # 1. MKV via mkvmerge, new file, keep original
    before, after, out = run_case(tools, mkv, sdh, media.SaveOptions(mode="new", keep_original=True), "mkv/mkvmerge/new")
    assert len(after.subs) == len(before.subs) + 1
    new = [s for s in after.subs if s.default]            # the cleaned track is the default one, named just "English"
    assert len(new) == 1 and new[0].default and not new[0].hearing_impaired
    assert sum(s.default for s in after.subs) == 1
    assert new[0].sub_pos == 2, "cleaned track should sit right after the original"
    assert after.count("attachment") == before.count("attachment"), "attachments (fonts) must survive"

    # 2. MKV via ffmpeg (no mkvmerge), replace mode, drop original
    t2 = media.Tools(tools.ffmpeg, tools.ffprobe, None)
    before, after, out = run_case(t2, mkv, sdh, media.SaveOptions(mode="replace", keep_original=False), "mkv/ffmpeg/replace")
    assert out.path == mkv and len(after.subs) == len(before.subs)
    assert not any(s.title == "English SDH" for s in after.subs)
    assert after.count("attachment") == before.count("attachment"), "attachments (fonts) must survive"
    assert sum(s.default for s in after.subs) == 1

    # 3. MP4 (mov_text) via ffmpeg, new file
    before, after, out = run_case(tools, mp4, lambda i: i.subs[0], media.SaveOptions(mode="new", keep_original=True), "mp4/ffmpeg/new")
    assert len(after.subs) == 3 and after.subs[1].codec == "mov_text" and after.subs[1].default

    # 4. sidecar
    before, after, out = run_case(tools, mp4, lambda i: i.subs[0], media.SaveOptions(mode="sidecar"), "sidecar")
    assert out.path.endswith("Test Clip.en.srt")

    # 5. cancel mid-way leaves nothing behind
    info = media.probe(tools, mkv)
    ev = threading.Event(); ev.set()
    try:
        media.save(tools, info, info.subs[0], "1\n00:00:01,000 --> 00:00:02,000\nHi\n", "srt",
                   media.SaveOptions(mode="new"), cancel=ev)
        raise AssertionError("should have been cancelled")
    except media.Cancelled:
        pass
    leftovers = [f for f in os.listdir(work) if "subscrub-tmp" in f]
    assert not leftovers, leftovers
    # 6. standalone subtitle file next to a video: embed it (and recycle the .srt)
    vid = os.path.join(work, "Episode 01.mkv")
    orig = os.path.join(folder, "Test Movie (2024).mkv")
    shutil.copy(orig if os.path.exists(orig) else os.path.join(folder, "Sample (SDH test).mkv"), vid)
    srt = os.path.join(work, "Episode 01.en.sdh.srt")
    shutil.copy(os.path.join(os.path.dirname(__file__), "fixtures", "sdh_en.srt"), srt)
    sinfo, sdoc = media.probe_subtitle_file(srt)
    assert sinfo.companion == vid and sinfo.subs[0].language == "eng" and sinfo.subs[0].sdh_flagged
    vbefore = media.probe(tools, vid)
    res = clean_doc(sdoc, CleanOptions())
    out = media.save(tools, sinfo, sinfo.subs[0], res.render(), "srt", media.SaveOptions(mode="embed"))
    vafter = media.probe(tools, vid)
    assert out.path == vid and len(vafter.subs) == len(vbefore.subs) + 1
    assert [s.title for s in vafter.subs if s.default] == ["English"]
    assert not os.path.exists(srt), "the SDH .srt should have been recycled"
    print("--- embed standalone:", out.note)

    # 7. standalone subtitle, replace: Movie.en.sdh.srt -> Movie.en.srt
    srt2 = os.path.join(work, "Other.en.sdh.srt")
    shutil.copy(os.path.join(os.path.dirname(__file__), "fixtures", "sdh_en.srt"), srt2)
    sinfo, sdoc = media.probe_subtitle_file(srt2)
    assert sinfo.companion is None
    out = media.save(tools, sinfo, sinfo.subs[0], clean_doc(sdoc, CleanOptions()).render(), "srt",
                     media.SaveOptions(mode="replace"))
    assert out.path.endswith("Other.en.srt") and not os.path.exists(srt2)
    print("--- replace standalone:", os.path.basename(out.path), out.note)

    # 8. default options now = replace + drop SDH + make default
    info = media.probe(tools, vid)
    tr = next(t for t in info.subs if t.title == "English SDH")
    paths = media.extract_subs(tools, info, [tr], tempfile.mkdtemp())
    out = media.save(tools, info, tr, clean_doc(sf.load(paths[tr.index]), CleanOptions()).render(), "srt",
                     media.SaveOptions())
    after = media.probe(tools, vid)
    assert "English SDH" not in [s.title for s in after.subs]
    assert sum(s.default for s in after.subs) == 1
    print("--- defaults:", [(s.title, s.default) for s in after.subs])
    print("files:", sorted(os.listdir(work)))
    print("pipeline OK")


if __name__ == "__main__":
    main(sys.argv[1])
