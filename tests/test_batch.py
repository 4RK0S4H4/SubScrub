"""Batch/track-picking tests (no FFmpeg needed).  python -m tests.test_batch"""
import os
import tempfile

from subscrub import batch, media, subformats as sf
from subscrub.batch import Item

SDH = "1\n00:00:01,000 --> 00:00:02,000\n[door slams]\n\n2\n00:00:03,000 --> 00:00:04,000\nJOHN: Hi.\n\n" \
      "3\n00:00:05,000 --> 00:00:06,000\n(sighs) Fine.\n\n4\n00:00:07,000 --> 00:00:08,000\n♪♪\n"
PLAIN = "1\n00:00:03,000 --> 00:00:04,000\nHi.\n\n2\n00:00:05,000 --> 00:00:06,000\nFine.\n"


def track(i, pos, lang, title, default=False, hi=False):
    return media.SubTrack(i, pos, "subrip", lang, title, default, False, hi, {"default": int(default)})


def item_with(tracks, texts):
    it = Item(path="x.mkv", kind="video")
    it.info = media.MediaInfo("x.mkv", "mkv", "matroska", 10, 1, [], tracks)
    it.docs = {t.index: sf.parse(txt, "srt") for t, txt in zip(tracks, texts)}
    batch.measure(it)
    return it


def test_picks_sdh_in_default_language():
    it = item_with([track(2, 0, "eng", "English", default=True), track(3, 1, "eng", "English SDH"),
                    track(4, 2, "spa", "Spanish SDH")], [PLAIN, SDH, SDH])
    assert batch.pick_track(it, None) == 3


def test_preference_carries_over():
    pref = batch.pref_from(track(9, 0, "spa", "Spanish SDH", hi=True))
    it = item_with([track(2, 0, "eng", "English SDH", default=True), track(3, 1, "spa", "Spanish SDH")], [SDH, SDH])
    assert batch.pick_track(it, pref) == 3


def test_names_and_languages():
    assert media.guess_language("Movie (2020).en.sdh.srt") == "eng"
    assert media.guess_language("Show.S01E01.spa.forced.srt") == "spa"
    assert media.guess_language("Movie.hi.srt") == "und"          # .hi = hearing impaired, not Hindi
    assert media.clean_subtitle_name("Movie (2020).en.sdh.srt", "srt").name == "Movie (2020).en.srt"
    assert media.clean_subtitle_name("Movie.en.srt", "srt").name == "Movie.en.srt"


def test_matching_video_and_folder_expansion():
    d = tempfile.mkdtemp()
    for name in ("Movie (2020).mkv", "Movie (2020) Extras.mkv", "Movie (2020).en.sdh.srt", "notes.txt",
                 "Movie (2020).sup"):
        open(os.path.join(d, name), "w", encoding="utf-8").close()
    os.makedirs(os.path.join(d, "Season 01"))
    open(os.path.join(d, "Season 01", "E01.mkv"), "w", encoding="utf-8").close()
    assert media.find_matching_video(os.path.join(d, "Movie (2020).en.sdh.srt")) == os.path.join(d, "Movie (2020).mkv")
    files, skipped = batch.expand_paths([d])
    assert sorted(os.path.basename(f) for f in files) == ["E01.mkv", "Movie (2020) Extras.mkv",
                                                          "Movie (2020).en.sdh.srt", "Movie (2020).mkv"]
    plan = batch.pair_up(files, explicit=set())
    assert sorted(os.path.basename(f) for f in plan.videos) == ["E01.mkv", "Movie (2020) Extras.mkv", "Movie (2020).mkv"]
    assert plan.dropped_with == {os.path.join(d, "Movie (2020).mkv"): [os.path.join(d, "Movie (2020).en.sdh.srt")]}
    assert not plan.subtitles and not plan.orphans
    files, skipped = batch.expand_paths([os.path.join(d, "Movie (2020).sup"), os.path.join(d, "Movie (2020).en.sdh.srt")])
    assert len(files) == 1 and len(skipped) == 1
    assert media.find_sidecars(os.path.join(d, "Movie (2020).mkv")) == [os.path.join(d, "Movie (2020).en.sdh.srt")]
    assert media.find_sidecars(os.path.join(d, "Movie (2020) Extras.mkv")) == []


def test_folder_of_videos_with_subtitle_files():
    # Green Lantern style: every episode has an .eng.srt next to it and no subtitles inside
    d = tempfile.mkdtemp()
    eps = [f"Show - S01E0{n} - Title ({n}) (1080p x265 EDGE2020)" for n in (1, 2, 3)]
    for e in eps:
        open(os.path.join(d, e + ".mkv"), "w", encoding="utf-8").close()
        open(os.path.join(d, e + ".eng.srt"), "w", encoding="utf-8").write(SDH)
    open(os.path.join(d, "random notes.srt"), "w", encoding="utf-8").write(PLAIN)
    files, _ = batch.expand_paths([d])
    plan = batch.pair_up(files, explicit=set())
    assert len(plan.videos) == 3 and len(plan.dropped_with) == 3 and not plan.subtitles
    assert [os.path.basename(p) for p in plan.orphans] == ["random notes.srt"]      # left alone
    # selecting every file by hand gives the same result
    plan2 = batch.pair_up(files, explicit=set(files))
    assert len(plan2.videos) == 3 and [os.path.basename(p) for p in plan2.subtitles] == ["random notes.srt"]
    # dropping only the .srt files still ends up as the videos (when subtitle files are embedded)
    srts = [os.path.join(d, e + ".eng.srt") for e in eps]
    plan3 = batch.pair_up(srts, explicit=set(srts))
    assert sorted(plan3.videos) == sorted(os.path.join(d, e + ".mkv") for e in eps) and not plan3.subtitles
    plan4 = batch.pair_up(srts, explicit=set(srts), embed=False)
    assert not plan4.videos and len(plan4.subtitles) == 3

    # the video item picks up its subtitle file as a track, picks it, and knows to remove it afterwards
    it = Item(path=os.path.join(d, eps[0] + ".mkv"), kind="video")
    it.info = media.MediaInfo(it.path, "mkv", "matroska", 10, 1, [], [])
    batch.attach_sidecars(it)
    batch.measure(it)
    assert len(it.sidecars) == 1 and it.sidecars[0].language == "eng"
    it.sel = batch.pick_track(it, None)
    assert it.track().external.endswith(".eng.srt") and batch.is_sdh(it, it.track())
    assert batch.removable_sidecars(it, keep_sdh=False, mode="replace") == [it.sidecars[0].external]
    assert batch.removable_sidecars(it, keep_sdh=True, mode="replace") == []
    assert batch.removable_sidecars(it, keep_sdh=False, mode="new") == []


def test_embedded_sdh_plus_redundant_srt():
    d = tempfile.mkdtemp()
    v = os.path.join(d, "Movie.mkv")
    open(v, "w", encoding="utf-8").close()
    open(os.path.join(d, "Movie.en.srt"), "w", encoding="utf-8").write(PLAIN)
    open(os.path.join(d, "Movie.forced.en.srt"), "w", encoding="utf-8").write(PLAIN)
    it = item_with([track(2, 0, "eng", "English SDH", hi=True)], [SDH])
    it.path = v
    it.info.path = v
    batch.attach_sidecars(it)
    batch.measure(it)
    it.sel = batch.pick_track(it, None)
    assert it.sel == 2                                     # the SDH track inside the video
    assert batch.plain_partner(it) is not None             # the .srt is used as the cross-check
    assert [os.path.basename(p) for p in batch.removable_sidecars(it, False, "replace")] == ["Movie.en.srt"]


if __name__ == "__main__":
    import inspect, sys
    fns = [f for n, f in sorted(globals().items()) if n.startswith("test_") and inspect.isfunction(f)]
    for f in fns:
        f()
        print("ok ", f.__name__)
    print(f"{len(fns)} tests passed")
    sys.exit(0)
