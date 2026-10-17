"""Batch bookkeeping that doesn't depend on the UI: queue items and picking the right track."""
from __future__ import annotations

import itertools
import os
from dataclasses import dataclass, field
from pathlib import Path

import re

from . import media, naming
from .cleaner import CleanOptions, CleanResult, clean_doc

_ids = itertools.count(1)
# How "SDH-ish" a track is, independent of the user's toggles: only real SDH annotations count
# (not lyrics or dialogue dashes, which regular subtitles have too).
REFERENCE_OPTS = CleanOptions(lyrics=False, lyric_symbols=False, dialogue_dashes=False, onscreen_text=False,
                              ocr_fixes=False)
SDH_RULES = {"brackets", "parentheses", "labels", "music", "sound_words"}
NOT_DIALOGUE_TRACK = re.compile(r"\b(?:comment|sign|song|forced|karaoke|lyrics only|descriptive|audio desc)", re.I)


@dataclass
class Item:
    path: str
    kind: str                            # video | subtitle
    uid: int = field(default_factory=lambda: next(_ids))
    state: str = "queued"                # queued probing reading ready saving done skipped failed
    info: media.MediaInfo | None = None
    docs: dict = field(default_factory=dict)
    doc_errors: dict = field(default_factory=dict)
    sdh_counts: dict = field(default_factory=dict)   # track index -> lines the default rules would clean
    sel: int | None = None
    manual: bool = False
    opts: CleanOptions | None = None     # per-file edits (None = the global defaults)
    overrides: dict = field(default_factory=dict)    # cue index -> "orig" | "clean" chosen in Review
    reviewed: bool = False
    force: bool = False                  # clean even though the track isn't SDH (the user asked)
    sidecars: list = field(default_factory=list)     # SubTracks for subtitle files sitting next to the video
    dropped: set = field(default_factory=set)        # subtitle files the user dropped along with the video
    locked: dict = field(default_factory=dict)       # file this save would change -> programs that have it open
    tmpdir: str | None = None
    progress: float = -1.0
    message: str = ""
    output: str | None = None
    cleaned: int = 0
    result: CleanResult | None = None
    match: naming.Match | None = None    # what TMDB says this video is
    name_pick: object = None             # a naming.Choice the user picked, "keep" (don't rename) or None
    name_state: str = ""                 # "" | looking | done | error
    name_note: str = ""
    done_text: str = ""                  # what the row says once saved

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def loaded(self) -> bool:
        return self.info is not None and self.state not in ("queued", "probing", "reading")

    @property
    def pending(self) -> bool:
        return self.state in ("queued", "probing", "reading", "ready", "failed")

    def tracks(self) -> list:
        """Tracks inside the video plus subtitle files next to it."""
        return (list(self.info.subs) if self.info else []) + list(self.sidecars)

    def track(self) -> media.SubTrack | None:
        if not self.info or self.sel is None:
            return None
        return next((t for t in self.tracks() if t.index == self.sel), None)


def kind_for(path: str) -> str | None:
    ext = Path(path).suffix.lower()
    if ext in media.TEXT_SUB_EXTS:
        return "subtitle"
    if ext in media.IMAGE_SUB_EXTS:
        return None
    return "video"


def expand_paths(paths: list[str], limit: int = 500) -> tuple[list[str], list[str]]:
    """Files as given; folders expanded (recursively) to the videos and subtitle files inside.
    Returns (files, skipped picture-subtitle files). Use pair_up() next."""
    out, skipped = [], []
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs.sort()
                for f in sorted(files):
                    ext = Path(f).suffix.lower()
                    if ".subscrub-tmp" not in f and (ext in media.VIDEO_EXTS or ext in media.TEXT_SUB_EXTS):
                        out.append(os.path.join(root, f))
                if len(out) >= limit * 3:
                    break
        elif os.path.isfile(p):
            if kind_for(p) is None:
                skipped.append(p)
            else:
                out.append(p)
    seen, uniq = set(), []
    for p in out:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            uniq.append(os.path.abspath(p))
    return uniq, skipped


@dataclass
class Plan:
    videos: list                 # video paths to add
    subtitles: list              # subtitle files to clean on their own (no video to put them in)
    dropped_with: dict           # video path -> subtitle files the user dropped along with it
    orphans: list                # subtitle files found in a folder with no matching video (left alone)


def pair_up(files: list[str], explicit: set | None = None, embed: bool = True,
            known_videos: set | None = None, limit: int = 500) -> Plan:
    """Sort dropped files out. A subtitle file whose video is also there (or already in the list, or —
    when subtitle files get embedded — sitting next to it on disk) becomes part of that video instead of
    a separate item, so each video ends up as one file with the cleaned subtitles inside.
    `explicit` = files the user picked one by one (anything else came from a dropped folder)."""
    explicit = {os.path.normcase(os.path.abspath(p)) for p in (explicit or set())}
    known = {os.path.normcase(v) for v in (known_videos or set())}
    vids = [f for f in files if kind_for(f) == "video"]
    vid_keys = {os.path.normcase(v) for v in vids} | known
    plan = Plan(list(vids), [], {}, [])
    for f in files:
        if kind_for(f) != "subtitle":
            continue
        from_folder = os.path.normcase(os.path.abspath(f)) not in explicit
        match = media.find_matching_video(f)
        key = os.path.normcase(match) if match else None
        if match and (key in vid_keys or (embed and not from_folder)):
            if key not in vid_keys:
                plan.videos.append(match)
                vid_keys.add(key)
            plan.dropped_with.setdefault(match, []).append(f)
        elif from_folder:
            plan.orphans.append(f)
        else:
            plan.subtitles.append(f)
    plan.videos = plan.videos[:limit]
    return plan


def attach_sidecars(item: Item, log=None):
    """Find the subtitle files next to a video and add them to it as extra tracks."""
    if item.kind != "video":
        return
    item.sidecars = []
    for k, path in enumerate(media.find_sidecars(item.path)):
        try:
            tr, doc = media.sidecar_track(path, k)
        except Exception as e:  # noqa: BLE001
            if log:
                log(f"Couldn't read {path}: {e}")
            continue
        item.sidecars.append(tr)
        item.docs[tr.index] = doc


def removable_sidecars(item: Item, keep_sdh: bool, mode: str) -> list[str]:
    """Subtitle files next to the video that become redundant once the cleaned track is inside it:
    the one that was cleaned (unless the SDH version is being kept) and other files in the same
    language. Forced-subtitle files are never touched, and neither is an SDH file when a plain track was the
    one cleaned (the SDH track stays)."""
    tr = item.track()
    if tr is None or not item.sidecars:
        return []
    plain = plain_pick(item, tr)
    keep_sdh = keep_sdh and not plain
    if mode == "sidecar":
        return [tr.external] if tr.external and not keep_sdh else []
    if mode != "replace":
        return []
    lang = media.lang_short(tr.language)
    out = []
    for s in item.sidecars:
        if s.forced:
            continue
        if s.index == tr.index:
            if not keep_sdh:
                out.append(s.external)
        elif lang and media.lang_short(s.language) in (lang, "") and not (plain and is_sdh(item, s)):
            out.append(s.external)
    return out


def sdh_score(doc) -> int:
    """Number of lines with real SDH annotations (sounds, speaker names, music cues)."""
    res = clean_doc(doc, REFERENCE_OPTS)
    return sum(1 for r in res.results if r.changed and (r.rules & SDH_RULES))


def measure(item: Item):
    """Record how many SDH annotations each track has (used to spot the SDH track)."""
    item.sdh_counts = {i: sdh_score(d) for i, d in item.docs.items()}


def is_sdh(item: Item, t: media.SubTrack) -> bool:
    """Flagged as SDH, or full of sound descriptions even though nobody labelled it."""
    doc = item.docs.get(t.index)
    n = item.sdh_counts.get(t.index, 0)
    return t.sdh_flagged or (doc is not None and n >= max(3, 0.04 * len(doc.cues)))


def _side_track(t: media.SubTrack) -> bool:
    return t.forced or bool(NOT_DIALOGUE_TRACK.search(t.title or "")) and not t.sdh_flagged


def pick_track(item: Item, pref: dict | None) -> int | None:
    """Choose which subtitle track to clean.

    With a preference (the user picked a track in some file) the closest match wins: same language,
    then same track name, then same SDH-ness. Otherwise stay in the default track's language (English
    if there is one) and take the SDH track — flagged, or detected by its content. Commentary, forced
    and signs/songs tracks are never picked unless they're all there is.
    """
    info = item.info
    if not info:
        return None
    text = [t for t in item.tracks() if t.text_based and t.index in item.docs] or \
        [t for t in item.tracks() if t.text_based]
    if not text:
        return None
    main = [t for t in text if not _side_track(t)] or text
    if pref:
        def score(t):
            s = 0
            if t.language == pref.get("language"):
                s += 1000
            if pref.get("title") and (t.title or "").strip().lower() == pref["title"].strip().lower():
                s += 100
            if t.sdh_flagged == pref.get("sdh"):
                s += 10
            return s
        best = max(score(t) for t in main)
        cands = [t for t in main if score(t) == best]
    else:
        eng = [t for t in main if media.lang_short(t.language) == "en"]
        default_lang = next((t.language for t in main if t.default), None)
        if eng:
            cands = eng
        elif default_lang:
            cands = [t for t in main if t.language == default_lang]
        else:
            cands = main
    dropped = {os.path.normcase(p) for p in item.dropped}
    # a plain track already in the file wins over the SDH one (the user's decision, #20): clean that one for
    # strays and make it the default. Only when there's no plain track is the SDH track cleaned.
    plain = [t for t in cands if not is_sdh(item, t)]
    if plain and not pref:
        return max(plain, key=lambda t: (len(item.docs[t.index].cues) if t.index in item.docs else 0,
                                         bool(t.external) and os.path.normcase(t.external) in dropped,
                                         t.default, -t.sub_pos)).index
    return max(cands, key=lambda t: (is_sdh(item, t), bool(t.external) and os.path.normcase(t.external) in dropped,
                                     item.sdh_counts.get(t.index, -1), t.default, -t.sub_pos)).index


def plain_pick(item: Item, t: media.SubTrack) -> bool:
    """`t` is a regular track although the file also has an SDH track in the same language. Cleaning it catches
    strays, the cleaned copy replaces it as the default, and the SDH track stays (just no longer the default)."""
    if item.kind != "video" or is_sdh(item, t):
        return False
    lang = media.lang_short(t.language)
    return any(o.index != t.index and o.text_based and o.index in item.docs and media.lang_short(o.language) == lang
               and not _side_track(o) and is_sdh(item, o) for o in item.tracks())


def auto_cleaned(item: Item, t: media.SubTrack) -> bool:
    """Cleaned on save without the user forcing it: a subtitle file, an SDH track, or the plain track chosen
    over an SDH one."""
    return bool(t.external) or is_sdh(item, t) or plain_pick(item, t)


def plain_to_default(item: Item, t: media.SubTrack) -> bool:
    """A plain track with nothing to clean still has to be made the default."""
    return not t.external and plain_pick(item, t) and not t.default


def plain_partner(item: Item) -> int | None:
    """The regular (non-SDH) track in the same language as the selected one, to check our work against."""
    sel = item.track()
    if sel is None or not item.info or not is_sdh(item, sel):
        return None                      # a plain track has nothing to be checked against
    lang = media.lang_short(sel.language)
    cands = [t for t in item.tracks() if t.text_based and t.index != sel.index and t.index in item.docs
             and media.lang_short(t.language) == lang and not _side_track(t) and not is_sdh(item, t)]
    if not cands:
        return None
    return min(cands, key=lambda t: (item.sdh_counts.get(t.index, 0), -len(item.docs[t.index].cues))).index


def clean_item(item: Item, defaults: CleanOptions) -> CleanResult | None:
    """Clean the selected track with this file's options, cross-checked against its plain track."""
    doc = item.docs.get(item.sel) if item.sel is not None else None
    if doc is None:
        return None
    p = plain_partner(item)
    res = clean_doc(doc, item.opts or defaults, plain=item.docs.get(p) if p is not None else None)
    res.overrides = item.overrides
    return res


def pref_from(track: media.SubTrack) -> dict:
    return {"language": track.language, "title": track.title or "", "sdh": track.sdh_flagged}


# ============================================================================ names and tidy-up

def name_choice(item: Item) -> naming.Choice | None:
    """The identity this video will be renamed after, or None (keep its name / nothing confident)."""
    if item.name_pick == "keep":
        return None
    if isinstance(item.name_pick, naming.Choice):
        return item.name_pick
    m = item.match
    return m.best if m is not None and m.confident else None


def name_unsure(item: Item) -> bool:
    """TMDB found possible matches but none clearly enough, and the user hasn't picked yet."""
    m = item.match
    return item.name_pick is None and m is not None and not m.confident and bool(m.choices)


def new_name(item: Item, with_year: bool = False) -> str | None:
    """The new file name, or None when it stays as it is."""
    if item.kind != "video":
        return None
    c = name_choice(item)
    if c is None:
        return None
    name = naming.render(c, Path(item.path).suffix, with_year)
    return None if name == os.path.basename(item.path) else name


def other_languages(item: Item, keep: str = "eng") -> list:
    """Subtitle tracks inside the video that the tidy-up would remove."""
    if item.kind != "video" or item.info is None:
        return []
    return [t for t in item.info.subs if media.is_other_language(t, keep)]
