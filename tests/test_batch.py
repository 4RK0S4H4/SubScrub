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


def test_picks_the_plain_track_in_the_default_language():
    # the plain English track wins over the SDH one (#20); Spanish is never considered when English exists
    it = item_with([track(2, 0, "eng", "English", default=True), track(3, 1, "eng", "English SDH"),
                    track(4, 2, "spa", "Spanish SDH")], [PLAIN, SDH, SDH])
    assert batch.pick_track(it, None) == 2
    # with only SDH tracks in English, the SDH one is picked
    it = item_with([track(3, 0, "eng", "English SDH", default=True), track(4, 1, "spa", "Spanish")], [SDH, PLAIN])
    assert batch.pick_track(it, None) == 3


def test_prefers_the_plain_track():
    it = item_with([track(2, 0, "eng", "English SDH"), track(3, 1, "eng", "English", default=True),
                    track(4, 2, "spa", "Spanish")], [SDH, PLAIN, PLAIN])
    assert batch.pick_track(it, None) == 3
    assert batch.plain_pick(it, it.info.subs[1]) and batch.auto_cleaned(it, it.info.subs[1])
    assert batch.plain_partner(it) is None or it.sel is None      # nothing to cross-check a plain track against
    it.sel = 3
    assert batch.plain_partner(it) is None
    assert not batch.plain_to_default(it, it.info.subs[1])        # already the default
    it2 = item_with([track(2, 0, "eng", "English SDH", default=True), track(3, 1, "eng", "English")], [SDH, PLAIN])
    assert batch.pick_track(it2, None) == 3 and batch.plain_to_default(it2, it2.info.subs[1])


def test_plain_only_and_sdh_only_behave_as_before():
    only_plain = item_with([track(2, 0, "eng", "English", default=True)], [PLAIN])
    assert batch.pick_track(only_plain, None) == 2
    assert not batch.plain_pick(only_plain, only_plain.info.subs[0])
    assert not batch.auto_cleaned(only_plain, only_plain.info.subs[0])     # skipped on save, as today
    only_sdh = item_with([track(2, 0, "eng", "English SDH", default=True), track(3, 1, "spa", "Spanish")],
                         [SDH, PLAIN])
    assert batch.pick_track(only_sdh, None) == 2 and batch.auto_cleaned(only_sdh, only_sdh.info.subs[0])


def test_hand_pick_of_the_sdh_track_wins():
    pref = batch.pref_from(track(9, 0, "eng", "English SDH", hi=True))
    it = item_with([track(2, 0, "eng", "English SDH"), track(3, 1, "eng", "English", default=True)], [SDH, PLAIN])
    assert batch.pick_track(it, pref) == 2


def test_sidecars_kept_when_plain_is_cleaned():
    d = tempfile.mkdtemp()
    it = item_with([track(2, 0, "eng", "English", default=True)], [PLAIN])
    it.path = os.path.join(d, "x.mkv")
    sdh_side = media.SubTrack(10, 1, "subrip", "eng", "", False, False, True, {}, external=os.path.join(d, "x.en.sdh.srt"))
    it.sidecars = [sdh_side]
    it.docs[10] = sf.parse(SDH, "srt")
    batch.measure(it)
    it.sel = 2
    assert batch.plain_pick(it, it.track())
    assert batch.removable_sidecars(it, False, "replace") == []     # the SDH file stays


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
    # the plain .srt wins over the SDH track inside the video (#20): it is cleaned and embedded, the SDH track stays
    assert it.track().external.endswith("Movie.en.srt") and batch.plain_pick(it, it.track())
    assert batch.plain_partner(it) is None
    assert [os.path.basename(p) for p in batch.removable_sidecars(it, False, "replace")] == ["Movie.en.srt"]
    assert [os.path.basename(p) for p in batch.removable_sidecars(it, True, "replace")] == ["Movie.en.srt"]


def test_stale_temp_folders_are_removed_only_when_old_and_ours():
    root = tempfile.mkdtemp()
    old, fresh, other = (os.path.join(root, n) for n in ("subscrub_mux_old", "subscrub_fresh", "someone_else"))
    for p in (old, fresh, other):
        os.makedirs(p)
    long_ago = 1_000_000_000
    for p in (old, other):
        os.utime(p, (long_ago, long_ago))
    assert media.remove_stale_temp_dirs(root=root) == 1
    assert not os.path.exists(old) and os.path.exists(fresh) and os.path.exists(other)


def test_leftover_detection():
    d = tempfile.mkdtemp()
    for n in ("Movie.mkv", "Movie.subscrub-tmp.mkv", "Gone.subscrub-tmp.mkv", "Other.mkv", "notes.subscrub-tmp.txt"):
        open(os.path.join(d, n), "w", encoding="utf-8").write("x")
    by = {lo.name: lo for lo in media.find_leftovers([d])}
    assert set(by) == {"Movie.subscrub-tmp.mkv", "Gone.subscrub-tmp.mkv"}
    assert by["Movie.subscrub-tmp.mkv"].original and by["Gone.subscrub-tmp.mkv"].original is None
    # picking just the video finds its own leftover and nobody else's
    assert [lo.name for lo in media.find_leftovers([os.path.join(d, "Movie.mkv")])] == ["Movie.subscrub-tmp.mkv"]
    assert media.find_leftovers([os.path.join(d, "Other.mkv")]) == []
    new = media.restore_leftover(by["Gone.subscrub-tmp.mkv"])
    assert os.path.basename(new) == "Gone.mkv" and os.path.exists(new)
    try:
        media.restore_leftover(by["Movie.subscrub-tmp.mkv"])
        raise AssertionError("must not overwrite the original")
    except media.ToolError:
        pass


if __name__ == "__main__":
    import inspect, sys
    fns = [f for n, f in sorted(globals().items()) if n.startswith("test_") and inspect.isfunction(f)]
    for f in fns:
        f()
        print("ok ", f.__name__)
    print(f"{len(fns)} tests passed")
    sys.exit(0)
