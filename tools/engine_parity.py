"""Check that the C# engine (winui/SubScrub.Engine) gives exactly what the Python one gives, beyond the cleaner:
the file list's logic (subscrub/batch.py), the views of subscrub/core.py (stage 2 of the port) and reading files:
finding the tools, ffprobe, extracting the tracks, subtitle files on their own, unfinished copies (stage 3).

    python tools/engine_parity.py --library samples/library [--keep <folder>]

How: real Python sessions read real files (a copy of the sample library, the tests.make_samples edge cases, a few
subtitle files on their own, and a library copy after a real save), with the stand-in for TMDB. Each session's
state is written out (items, probed tracks, the subtitle text read, title matches, settings) and then a script of
actions runs on it: every view of the Files list and of Review under each filter, track changes, kept lines,
per-file edits, settings changes, and every question at save time walked through with several sets of answers and
programs holding files open. The C# engine loads the same state and runs the same script
(winui/SubScrub.Engine.Parity, needs the .NET 8 SDK). Any difference in any view fails, with the first few listed.

Also compared on the files themselves: kind_for, expand_paths, pair_up (each way of adding), find_matching_video,
find_sidecars, guess_language, clean_subtitle_name and the save's output_path.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from subscrub import batch, core, media, subformats as sf  # noqa: E402
from subscrub.demo_tmdb import FakeTMDB  # noqa: E402

PARITY = ROOT / "winui" / "SubScrub.Engine.Parity" / "SubScrub.Engine.Parity.csproj"

# ============================================================================ recording what was read

DOC_SRC: dict[int, tuple[str, str]] = {}
_real_load = sf.load


def _recording_load(path, fmt=None):
    text = sf.read_text_file(path)
    f = fmt or sf.detect_format(text, str(path))
    doc = sf.parse(text, f)
    DOC_SRC[id(doc)] = (text, f)
    return doc


sf.load = _recording_load

LOCKS: dict = {}


def _fake_who_has_open(paths):
    return {p: list(v) for p, v in LOCKS.items() if p in paths}


media.who_has_open = _fake_who_has_open

# ============================================================================ state out


def track_json(t: media.SubTrack) -> dict:
    return {"index": t.index, "sub_pos": t.sub_pos, "codec": t.codec, "language": t.language, "title": t.title,
            "default": t.default, "forced": t.forced, "hearing_impaired": t.hearing_impaired,
            "disposition": dict(t.disposition or {}), "external": t.external, "cleaned": t.cleaned}


def info_json(i: media.MediaInfo | None):
    if i is None:
        return None
    return {"path": i.path, "kind": i.kind, "format_name": i.format_name, "duration": i.duration, "size": i.size,
            "streams": i.streams, "subs": [track_json(t) for t in i.subs], "companion": i.companion,
            "chapters": i.chapters}


def choice_json(c) -> dict:
    return {"kind": c.kind, "tmdb_id": c.tmdb_id, "title": c.title, "year": c.year, "season": c.season,
            "episodes": list(c.episodes), "episode_titles": list(c.episode_titles), "runtime": c.runtime,
            "score": c.score, "why": list(c.why)}


def match_json(m):
    if m is None:
        return None
    p = m.parsed
    return {"parsed": {"title": p.title, "year": p.year, "season": p.season, "episodes": list(p.episodes),
                       "episode_title": p.episode_title},
            "choices": [choice_json(c) for c in m.choices], "confident": m.confident, "note": m.note}


def item_json(it: batch.Item) -> dict:
    docs = {}
    for k, d in it.docs.items():
        text, fmt = DOC_SRC[id(d)]
        docs[str(k)] = {"text": text, "fmt": fmt}
    pick = it.name_pick
    if pick is not None and pick != "keep":
        pick = {"choice": choice_json(pick)}
    return {"uid": it.uid, "path": it.path, "kind": it.kind, "state": it.state, "info": info_json(it.info),
            "docs": docs, "doc_errors": {str(k): v for k, v in it.doc_errors.items()},
            "sdh_counts": {str(k): v for k, v in it.sdh_counts.items()}, "sel": it.sel, "manual": it.manual,
            "opts": it.opts.to_dict() if it.opts else None, "overrides": {str(k): v for k, v in it.overrides.items()},
            "reviewed": it.reviewed, "force": it.force, "sidecars": [track_json(t) for t in it.sidecars],
            "dropped": sorted(it.dropped), "locked": it.locked, "tmpdir": it.tmpdir, "progress": it.progress,
            "message": it.message, "output": it.output, "cleaned": it.cleaned, "has_result": it.result is not None,
            "match": match_json(it.match), "name_pick": pick, "name_state": it.name_state, "name_note": it.name_note,
            "done_text": it.done_text}


def session_json(s: core.Session) -> dict:
    t = s.tools
    return {"items": [item_json(i) for i in s.items], "pref": s.pref, "review_uid": s.review_uid,
            "add_note": s.add_note, "status_msg": list(s.status_msg), "last_output": s.last_output,
            "locks_banner": s.locks_banner,
            "tools": {"ffmpeg": t.ffmpeg, "ffprobe": t.ffprobe, "mkvmerge": t.mkvmerge}}


# ============================================================================ the script

ANSWER_SETS = [
    {"locks": "skip", "lone": "go", "decision": "guess", "names": "keep", "rewrite": "go", "overwrite": "yes"},
    {"locks": "skip", "lone": "go", "decision": "orig", "names": "keep", "rewrite": "go", "overwrite": "no"},
    {"locks": "skip", "lone": "go", "decision": "skip", "names": "keep", "rewrite": "go", "overwrite": "no"},
    {"locks": "again", "lone": "cancel", "decision": "review", "names": "pick", "rewrite": "cancel"},
]

SETTINGS = [
    {"video_mode": "new"}, {"video_mode": "sidecar"}, {"video_mode": "replace", "keep_sdh": True},
    {"keep_sdh": False, "make_default": False}, {"make_default": True, "recycle_sub": False},
    {"recycle_sub": True, "rename_on": False}, {"rename_on": True, "name_year": True}, {"name_year": False,
                                                                                         "drop_langs": False},
    {"drop_langs": True, "sub_mode": "replace"}, {"sub_mode": "new"}, {"sub_mode": "embed", "confirm_replace": False},
    {"confirm_replace": True, "rules": {"brackets": False, "music_cues": False}, "lyrics": "remove"},
    {"rules": {"brackets": True, "music_cues": True, "mixed_case_labels": True}, "lyrics": "keep"},
    {"rules": {"mixed_case_labels": False}, "lyrics": "italic"},
    {"tmdb_key": ""}, {"tmdb_key": "stand-in"}, {},
]


def make_script(s: core.Session, small: bool = False) -> list:
    ops: list = [["files"]]
    items = list(s.items)
    for it in items:
        ops.append(["batch", it.uid])
        ops.append(["review", it.uid, None, True])
        for show in ("changes", "all", "check"):
            ops.append(["review", it.uid, show, False])
    for it in items:
        if it.kind == "video":
            ops.append(["name_choices", it.uid])
    if small:
        for k, answers in enumerate(ANSWER_SETS[:2]):
            ops.append(["save_walk", None, answers, {}])
        ops.append(["files"])
        return ops
    # kept lines: the first few changed ones and the flagged ones, toggled on and back
    for it in items:
        res = it.result
        if res is None:
            continue
        changed = [i for i, r in enumerate(res.results) if r.changed][:3]
        flagged = res.flagged()[:3]
        for i in changed + flagged:
            ops.append(["toggle", it.uid, i])
        for i in flagged[:1]:
            ops.append(["toggle", it.uid, i])
        ops.append(["review", it.uid, "changes", False])
        for i in changed[:1]:
            ops.append(["toggle", it.uid, i])
    # every track of every video, then back to the picked one
    for it in items:
        tracks = [t for t in it.tracks() if t.text_based] if it.info else []
        if len(tracks) < 2:
            continue
        for t in tracks:
            ops.append(["select", it.uid, t.index, "changes"])
            ops.append(["batch", it.uid])
        ops.append(["files"])
        if it.sel is not None:
            ops.append(["select", it.uid, it.sel, None])
    # per-file edits
    loaded = [i for i in items if i.result is not None]
    for it in loaded[:2]:
        ops.append(["set_rules", it.uid, {"brackets": False, "dialogue_dashes": False}, "remove", None])
        ops.append(["set_rules", it.uid, {"brackets": True}, "", "all"])
        ops.append(["files"])
        ops.append(["reset_rules", it.uid, None])
    if loaded:
        ops.append(["set_rules", loaded[0].uid, {"parentheses": False}, "keep", None])
        ops.append(["promote", loaded[0].uid, None])
        ops.append(["files"])
        ops.append(["settings", {"rules": {"parentheses": True}, "lyrics": "italic"}])
    for it in items:
        if it.kind == "video" and it.match is not None and it.match.choices:
            ops.append(["name_choices", it.uid])
            c = it.match.choices[-1]
            ops.append(["name_pick", it.uid, f"{c.kind}:{c.tmdb_id}:{c.season}:{','.join(map(str, c.episodes))}"])
            ops.append(["review", it.uid, None, False])
            break
    # programs holding files open
    videos = [i for i in items if i.kind == "video"]
    locks_sets = [{}]
    if videos:
        locks_sets.append({videos[0].path: ["qBittorrent"]})
    if len(videos) > 1:
        locks_sets.append({videos[0].path: ["qBittorrent"], videos[1].path: ["Plex Media Server", "qBittorrent"]})
    for locks in locks_sets[1:]:
        ops.append(["locks", locks])
        ops.append(["files"])
    ops.append(["locks", {}])
    # every question at save time, for everything and for each file on its own, under each setting
    for k, cfg in enumerate(SETTINGS):
        ops.append(["settings", cfg])
        ops.append(["files"])
        for answers in ANSWER_SETS:
            ops.append(["save_walk", None, answers, locks_sets[k % len(locks_sets)]])
        for it in items[:6]:
            ops.append(["save_walk", it.uid, ANSWER_SETS[k % 2], {}])
        ops.append(["files"])
    # the states the background work and the save put a file in
    if items:
        it = items[0]
        for poke in ({"state": "queued"}, {"state": "probing"}, {"state": "reading", "progress": 0.425},
                     {"state": "reading", "progress": -1.0}, {"state": "saving", "message": "Writing 40%",
                                                             "progress": 0.4},
                     {"state": "failed", "message": "mkvmerge failed (exit 2)"}, {"state": "skipped",
                                                                                  "message": "already clean"},
                     {"state": "done", "output": it.path, "cleaned": 12, "done_text": ""},
                     {"state": "done", "output": it.path + ".dialogue.mkv", "done_text": ""},
                     {"state": "done", "done_text": "Cleaned track saved into the video, renamed from x.mkv"},
                     {"state": "ready", "message": "", "progress": -1.0, "output": None, "done_text": ""}):
            ops.append(["poke", it.uid, poke])
            ops.append(["files"])
            ops.append(["review", it.uid, None, False])
        for name in ("", "looking", "nokey", "error", "done"):
            ops.append(["poke", it.uid, {"name_state": name, "name_note": "TMDB said no" if name == "error" else ""}])
            ops.append(["files"])
    ops.append(["busy", True, "2 of 5:  Dune, writing 40%", 0.31])
    ops.append(["files"])
    if items:
        ops.append(["review", items[0].uid, None, False])
    ops.append(["save_walk", None, ANSWER_SETS[0], {}])
    ops.append(["busy", False, "", -1.0])
    ops.append(["files"])
    return ops


# ============================================================================ running the script (Python side)

def walk(s: core.Session, only, answers: dict, locks: dict) -> list:
    LOCKS.clear()
    LOCKS.update(locks)
    got, a = [], {}
    for _ in range(12):
        q = s.save_question(only, dict(a))
        got.append(q)
        if "ask" not in q:
            break
        key = q["ask"]["key"]
        ans = answers.get(key)
        if ans is None or ans in ("cancel", "review", "pick", "again"):
            break
        a[key] = ans
    LOCKS.clear()
    return got


def run_op(s: core.Session, op: list):
    kind = op[0]
    try:
        if kind == "files":
            return s.files_view()
        if kind == "batch":
            return batch_probe(s, s.item(op[1]))
        if kind == "review":
            return s.review(op[1], op[2], op[3])
        if kind == "toggle":
            return s.toggle_line(op[1], op[2])
        if kind == "select":
            return s.select_track(op[1], op[2], op[3])
        if kind == "force":
            return s.force_clean(op[1], None)
        if kind == "set_rules":
            return s.set_rules(op[1], op[2], op[3], op[4])
        if kind == "reset_rules":
            return s.reset_rules(op[1], op[2])
        if kind == "promote":
            return s.promote_rules(op[1], op[2])
        if kind == "settings":
            return s.set_settings(op[1])
        if kind == "save_walk":
            return walk(s, op[1], op[2], op[3])
        if kind == "locks":
            LOCKS.clear()
            LOCKS.update(op[1])
            try:
                return s.check_locks()
            finally:
                LOCKS.clear()
        if kind == "name_choices":
            return s.name_choices(op[1])
        if kind == "name_pick":
            return s.name_pick(op[1], op[2])
        if kind == "poke":
            it = s.item(op[1])
            for k, v in op[2].items():
                setattr(it, k, v)
            return {}
        if kind == "busy":
            s._saving = op[1]
            s.save_status = (op[2], op[3])
            return {}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    raise ValueError(kind)


def batch_probe(s: core.Session, it: batch.Item) -> dict:
    tracks = it.tracks()
    batch.measure(it)
    out = {"kind_for": batch.kind_for(it.path), "sdh_counts": {str(k): v for k, v in it.sdh_counts.items()},
           "pick": batch.pick_track(it, None), "pick_pref": [batch.pick_track(it, batch.pref_from(t)) for t in tracks],
           "plain_partner": batch.plain_partner(it), "other_eng": [t.index for t in batch.other_languages(it)],
           "other_spa": [t.index for t in batch.other_languages(it, "spa")],
           "new_name": batch.new_name(it), "new_name_year": batch.new_name(it, True),
           "name_choice": (batch.name_choice(it).label() if batch.name_choice(it) else None),
           "name_unsure": batch.name_unsure(it), "files_touched": s.files_touched(it), "tracks": []}
    for t in tracks:
        out["tracks"].append({"index": t.index, "is_sdh": batch.is_sdh(it, t), "plain_pick": batch.plain_pick(it, t),
                              "auto": batch.auto_cleaned(it, t), "to_default": batch.plain_to_default(it, t),
                              "label": core.Session.track_label(it, t), "sdh_flagged": t.sdh_flagged,
                              "text_based": t.text_based, "fmt": t.fmt, "codec_label": t.codec_label,
                              "lang_label": t.lang_label, "from_subscrub": t.from_subscrub,
                              "dispo": [t.dispo_flags(False), t.dispo_flags(True)],
                              "default_name": media.default_track_name(t)})
    rem = {}
    for keep in (False, True):
        for mode in ("replace", "new", "sidecar"):
            rem[f"{keep}/{mode}"] = batch.removable_sidecars(it, keep, mode)
    out["removable"] = rem
    tr = it.track()
    if it.info is not None:
        paths = {}
        modes = ("embed", "replace", "new") if it.kind == "subtitle" else ("replace", "new", "sidecar")
        for mode in modes:
            for lang in ("eng", "hin", "und", ""):
                o = media.SaveOptions(mode=mode, language=lang, rename_to=batch.new_name(it) or "")
                paths[f"{mode}/{lang}"] = str(media.output_path(it.info, o, tr.fmt if tr else "srt"))
        out["output_path"] = paths
    return out


# ============================================================================ the files themselves

def file_cases(roots: list[Path]) -> tuple[dict, dict]:
    files = sorted(str(p) for r in roots for p in r.rglob("*") if p.is_file())
    subs = [f for f in files if Path(f).suffix.lower() in media.TEXT_SUB_EXTS]
    vids = [f for f in files if Path(f).suffix.lower() in media.VIDEO_EXTS]
    names = ["Movie (2020).en.sdh.srt", "Movie.eng.forced.srt", "Movie.hi.srt", "Movie.hin.srt", "Movie.pt-BR.srt",
             "Movie.zh_Hans.ass", "Movie.srt", "Movie.und.srt", "Movie.fr.cc.vtt", "x.HOH.SRT", ".hidden.srt",
             "Movie.sup", "Movie.idx", "Movie.mkv", "Movie.", "Movie.en.sdh.ssa", "Show.S01E01.nob.srt"]
    expands = [[str(r)] for r in roots] + [[str(r) for r in roots]] + [subs[:3] + vids[:2]] + [vids[:1] + [subs[-1]]]
    cases = {"names": names, "subs": subs, "videos": vids, "expand": expands, "pair": []}
    exp = {"names": [], "subs": [], "videos": [], "expand": [], "pair": []}
    for n in names:
        exp["names"].append({"kind": batch.kind_for(n), "lang": media.guess_language(n),
                             "clean": str(media.clean_subtitle_name(Path("/x") / n, "srt")),
                             "container": media.container_kind(n)})
    for f in subs:
        exp["subs"].append({"match": media.find_matching_video(f), "lang": media.guess_language(f)})
    for f in vids:
        exp["videos"].append({"sidecars": media.find_sidecars(f)})
    for paths in expands:
        files_, skipped = batch.expand_paths(paths)
        exp["expand"].append({"files": files_, "skipped": skipped})
        for embed in (True, False):
            for explicit in ([], [p for p in paths if os.path.isfile(p)], files_[:2]):
                for known in ([], vids[:1]):
                    case = {"files": files_, "explicit": explicit, "embed": embed, "known": known}
                    plan = batch.pair_up(files_, set(explicit), embed, set(known))
                    cases["pair"].append(case)
                    exp["pair"].append({"videos": plan.videos, "subtitles": plan.subtitles,
                                        "dropped_with": plan.dropped_with, "orphans": plan.orphans})
    return cases, exp


# ============================================================================ sessions

def new_session(settings: Path) -> core.Session:
    s = core.Session(core.Store(str(settings)), None, FakeTMDB)
    s.cfg.tmdb_key = "stand-in"
    s.cfg.save()
    return s


def settle(s: core.Session):
    if not s.wait_idle(600):
        raise SystemExit("the Python session didn't finish reading")
    time.sleep(0.4)                   # the "files in use" check that follows reading


def no_pumps(s: core.Session):
    """No more reading or title lookups once the state is written out (the C# side has neither yet)."""
    s._pump_names = lambda: None
    s._pump_loader = lambda: None


def record(name: str, s: core.Session, settings: Path, tmp: Path, small=False) -> tuple[dict, list]:
    cs_settings = tmp / f"{name}.settings-cs.json"
    shutil.copy(settings, cs_settings)
    state = session_json(s)
    script = make_script(s, small)
    expected = [run_op(s, op) for op in script]
    return {"name": name, "settings": str(cs_settings), "state": state, "script": script}, expected


def build_sessions(library: Path, tmp: Path) -> tuple[list, list]:
    cases, expected = [], []
    tools = media.find_tools()
    lib = tmp / "library"
    shutil.copytree(library, lib, ignore=shutil.ignore_patterns("_tmp"))
    from tests import make_samples
    edge = tmp / "edge"
    make_samples.edge_cases(tools, edge)
    subs = tmp / "subs"
    subs.mkdir()
    for f in sorted((ROOT / "tests" / "fixtures").glob("*.srt"))[:4] + sorted((ROOT / "tests" / "fixtures").glob("*.ass"))[:1]:
        shutil.copy(f, subs / f.name)

    def run(name, add, small=False, after=None):
        settings = tmp / f"{name}.settings.json"
        s = new_session(settings)
        for paths in add:
            s.add([str(p) for p in paths])
            settle(s)
        if after:
            after(s)
        no_pumps(s)
        c, e = record(name, s, settings, tmp, small)
        cases.append(c)
        expected.append(e)
        s.close()

    run("library folder", [[lib]])
    run("library files", [sorted(p for p in lib.iterdir() if p.suffix == ".mkv")[:3],
                          sorted(p for p in lib.iterdir() if p.suffix in (".mkv", ".srt"))])
    run("edge cases", [[edge]])
    run("subtitle files", [sorted(subs.iterdir())])

    # a real save, then the views of what was saved
    saved = tmp / "saved"
    shutil.copytree(library, saved, ignore=shutil.ignore_patterns("_tmp"))

    def save_all(s):
        done = threading.Event()
        s.emit = lambda name, data: done.set() if name == "save_done" else None
        a = {}
        for _ in range(10):
            q = s.save(None, dict(a))
            if "ask" not in q:
                break
            a[q["ask"]["key"]] = {"decision": "guess", "names": "keep", "rewrite": "go", "lone": "go",
                                  "overwrite": "yes", "locks": "skip"}[q["ask"]["key"]]
        if not done.wait(900):
            raise SystemExit("the Python save didn't finish")
        s.wait_idle(600)
        time.sleep(0.4)
    run("after saving", [[saved]], small=True, after=save_all)
    return cases, expected


# ============================================================================ reading files (stage 3)

def doc_view(d) -> dict:
    return {"fmt": d.fmt, "cues": [[c.start, c.end, c.text] for c in d.cues], "render": d.render([c.text for c in d.cues])}


def loaded_view(s: core.Session, added: list) -> dict:
    """What reading left behind, with file numbers replaced by their place in the list."""
    pos = {it.uid: k for k, it in enumerate(s.items)}
    items = []
    for it in s.items:
        items.append({"path": it.path, "kind": it.kind, "state": it.state, "info": info_json(it.info),
                      "docs": {str(k): doc_view(d) for k, d in it.docs.items()},
                      "doc_errors": {str(k): (v if v == "Couldn't read this track." else "error")
                                     for k, v in it.doc_errors.items()},
                      "sdh_counts": {str(k): v for k, v in it.sdh_counts.items()}, "sel": it.sel,
                      "sidecars": [track_json(t) for t in it.sidecars], "dropped": sorted(it.dropped),
                      "message": it.message if not it.message.startswith("couldn't read it (") else "couldn't read it",
                      "progress": it.progress, "has_result": it.result is not None,
                      "tmpdir": it.tmpdir is not None})
    fv = s.files_view()
    for r in fv["rows"]:
        r["uid"] = pos[r["uid"]]
        if r["detail"].startswith("Couldn't read it ("):
            r["detail"] = "Couldn't read it"
    fv["review_uid"] = None
    return {"added": added, "items": items, "files": fv}


def reading_cases(tmp: Path) -> tuple[dict, dict]:
    """Fresh sessions on both sides add the same files and folders (no title lookups); what they read must match.
    Also ffprobe and the extracted tracks of every video, subtitle files on their own, and unfinished copies."""
    lib, edge, subs = tmp / "library", tmp / "edge", tmp / "subs"
    vids = sorted(str(p) for r in (lib, edge) for p in r.rglob("*") if p.suffix.lower() in media.VIDEO_EXTS)
    lone = sorted(str(p) for p in subs.iterdir())
    adds = [
        {"name": "library", "adds": [[str(lib)]]},
        {"name": "edge cases", "adds": [[str(edge)]]},
        {"name": "files, then the folder", "adds": [vids[:2], [str(lib)]]},
        {"name": "subtitle files", "adds": [lone, lone[:1]]},
        {"name": "a video with its .srt", "adds": [[str(p) for p in sorted(lib.glob("Slow.Horses*"))]]},
        {"name": "nothing there", "adds": [[str(tmp / "nothing")], [str(tmp / "edge" / "picture subtitles only")]]},
        {"name": "sidecars, subtitle mode New File", "settings": {"sub_mode": "new"},
         "adds": [[str(p) for p in sorted(lib.glob("Slow.Horses*.srt"))]]},
    ]
    (tmp / "nothing").mkdir(exist_ok=True)
    # unfinished copies: with and without their original
    lo = tmp / "leftovers"
    lo.mkdir()
    (lo / "Movie.mkv").write_bytes(b"x")
    (lo / "Movie.subscrub-tmp.mkv").write_bytes(b"xyz")
    (lo / "Gone.subscrub-tmp.mp4").write_bytes(b"ab")
    (lo / "Notes.subscrub-tmp.txt").write_bytes(b"a")
    (lo / "sub").mkdir()
    (lo / "sub" / "Deep.subscrub-tmp.mkv").write_bytes(b"abcd")
    cases = {"adds": adds, "probe": vids, "lone": lone, "tmp": str(tmp / "extract"),
             "leftovers": [[str(lo)], [str(lo / "Movie.mkv")], [str(lo / "Gone.subscrub-tmp.mp4")], [str(lo / "sub")]],
             "restore": str(lo / "Gone.subscrub-tmp.mp4")}
    exp = {"adds": [], "probe": [], "lone": [], "leftovers": [], "tools": None, "restore": None}
    tools = media.find_tools()
    exp["tools"] = {"ffmpeg": tools.ffmpeg, "ffprobe": tools.ffprobe, "mkvmerge": tools.mkvmerge}
    for case in adds:
        settings = tmp / f"read-{len(exp['adds'])}.json"
        st = core.Store(str(settings))
        st.set("rename_on", "false")
        for k, v in (case.get("settings") or {}).items():
            st.set(k, v)
        case["settings_file"] = str(tmp / f"read-{len(exp['adds'])}-cs.json")
        shutil.copy(settings, case["settings_file"])
        sess = core.Session(core.Store(str(settings)))
        added = []
        for paths in case["adds"]:
            r = sess.add(paths)
            if not sess.wait_idle(600):
                raise SystemExit("the Python session didn't finish reading")
            added.append(r)
        exp["adds"].append(loaded_view(sess, added))
        sess.close()
    for k, v in enumerate(vids):
        info = media.probe(tools, v)
        out = tmp / "extract" / f"py{k}"
        got = media.extract_subs(tools, info, info.subs, str(out))
        files = {}
        for idx, path in got.items():
            files[str(idx)] = Path(path).read_bytes().decode("utf-8", "replace")
        exp["probe"].append({"info": info_json(info), "extracted": files})
    for f in lone:
        info, doc = media.probe_subtitle_file(f)
        exp["lone"].append({"info": info_json(info), "doc": doc_view(doc)})
    for paths in cases["leftovers"]:
        exp["leftovers"].append([[x.path, x.original, x.size, x.name] for x in media.find_leftovers(paths)])
    exp["restore"] = media.restore_leftover(media.find_leftovers([cases["restore"]])[0])
    os.rename(exp["restore"], cases["restore"])            # back, for the C# side
    return cases, exp


# ============================================================================ comparing

def norm(x):
    return json.loads(json.dumps(x, ensure_ascii=False, default=str))


def diff(a, b, path="") -> list[str]:
    if isinstance(a, bool) or isinstance(b, bool):
        return [] if (type(a) is type(b) and a == b) else [f"{path}: Python {a!r} / C# {b!r}"]
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: only in {'C#' if k not in a else 'Python'}")
            else:
                out += diff(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: {len(a)} items in Python, {len(b)} in C#"]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, f"{path}[{i}]")
        return out
    return [] if a == b else [f"{path}: Python {a!r} / C# {b!r}"]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:]
    if "--library" not in argv:
        print(__doc__)
        return 2
    library = Path(argv[argv.index("--library") + 1]).resolve()
    keep = Path(argv[argv.index("--keep") + 1]) if "--keep" in argv else None
    tmp = Path(tempfile.mkdtemp(prefix="subscrub_engine_parity_"))
    os.environ["LOCALAPPDATA"] = str(tmp / "appdata")          # the rename history of the real save stays here
    try:
        sessions, expected_sessions = build_sessions(library, tmp)
        fcases, fexpected = file_cases([tmp / "library", tmp / "edge", tmp / "subs"])
        rcases, rexpected = reading_cases(tmp)
        cases = {"files": fcases, "sessions": sessions, "reading": rcases}
        expected = norm({"files": fexpected, "sessions": expected_sessions, "reading": rexpected})
        (tmp / "cases.json").write_text(json.dumps(cases, ensure_ascii=False, default=str), encoding="utf-8")
        run = subprocess.run(["dotnet", "run", "--project", str(PARITY), "-c", "Release", "--", "session",
                              str(tmp / "cases.json"), str(tmp / "csharp.json")], capture_output=True, text=True)
        if run.returncode != 0:
            print(run.stdout[-6000:], run.stderr[-6000:])
            print("the C# side failed to run")
            return 1
        got = json.loads((tmp / "csharp.json").read_text(encoding="utf-8"))
        if keep:
            keep.mkdir(parents=True, exist_ok=True)
            shutil.copy(tmp / "cases.json", keep / "cases.json")
            shutil.copy(tmp / "csharp.json", keep / "csharp.json")
            (keep / "python.json").write_text(json.dumps(expected, ensure_ascii=False, indent=1), encoding="utf-8")
        problems = diff(expected, got)
        n_ops = sum(len(s) for s in expected_sessions)
        n_items = sum(len(c["state"]["items"]) for c in sessions)
        print(f"{len(sessions)} sessions ({n_items} files), {n_ops:,} views and actions, "
              f"{len(fcases['pair']) + len(fcases['subs']) + len(fcases['videos']) + len(fcases['names'])} file cases, "
              f"{len(rcases['adds'])} fresh reads, {len(rcases['probe'])} videos probed and extracted")
        if problems:
            for p in problems[:40]:
                print("  DIFF", p)
            print(f"{len(problems)} differences between the Python and the C# engine")
            return 1
        print("The C# engine reads files and gives the same file list, Review and save questions as the Python one.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
