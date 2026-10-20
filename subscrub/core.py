"""SubScrub without a window: the file list, loading, title lookups, Review and the save, driven by commands.

The native Windows app (winui/) talks to this through subscrub/helper.py (JSON lines over stdin/stdout). It is the
Qt window's logic (ui/main_window.py, ui/workers.py) with the widgets taken out: the same verdicts, wording, track
picking, questions at save time and save path, so both apps behave the same. Everything the window shows comes back
as plain dicts ("views"); the window only draws them.

Threads: commands run one at a time on the caller's thread. Reading files, title lookups and the save each run on
their own background thread. All state is guarded by one lock; whenever it changes, `changed()` asks the notifier
to send a fresh files view (at most ten a second).
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path

from . import APP_NAME, __version__, batch, media, naming, subformats as sf
from .batch import Item
from .cleaner import RULE_NAMES, CleanOptions, diff_html, visible_text  # noqa: F401
from .subformats import to_seconds

CANCELLED = "__cancelled__"

# Edit types shown in Review and as defaults in Settings: option key, title, example, rule ids counted
# (the same list as ui/dialogs.py, kept here so the helper doesn't need Qt)
RULES = [
    ("brackets", "Sounds in [Brackets]", "[door slams], [Grace] Hi.", ("brackets",)),
    ("parentheses", "Sounds in (Parentheses)", "(sighs), (ON PHONE)", ("parentheses",)),
    ("speaker_labels", "Speaker Names", "JOHN: Get down!  →  Get down!", ("labels",)),
    ("music_cues", "Music Cues", "♪ tense music playing ♪, ♪♪", ("music",)),
    ("dialogue_dashes", "Dialogue Dashes", "- Hi. / - Hey.  →  Hi. / Hey.", ("dashes",)),
    ("onscreen_text", "On-Screen Text Notes", "[sign reads: EXIT]  →  EXIT", ("onscreen",)),
    ("sound_words", "Sound Words", "whoosh, pew pew pew, ka-boom on their own line", ("sound_words",)),
    ("quote_italics", "Quoted Voices → Italics", "'Report to Oa.'  →  Report to Oa. (in italics)", ("quotes",)),
    ("ocr_fixes", "OCR Repairs", "Fixes ♪ and “I” misread in disc-converted tracks", ("ocr",)),
    ("mixed_case_labels", "Every “Name:” Label", "Also in files that don't use them throughout (risky)", ()),
]
RULE_KEYS = [r[0] for r in RULES]
VIDEO_MODES = {"replace": "Replace Original", "new": "New File", "sidecar": "Subtitle File"}
SUB_MODES = {"embed": "Embed in Video", "replace": "Replace File", "new": "New File"}


def lyric_mode(o: CleanOptions) -> str:
    return "remove" if o.lyrics else ("italic" if o.lyric_symbols else "keep")


def with_lyric_mode(o: CleanOptions, mode: str) -> CleanOptions:
    return o.copy(lyrics=mode == "remove", lyric_symbols=mode == "italic")


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n:,} {word if n == 1 else (many or word + 's')}"


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def fmt_duration(sec: float) -> str:
    if not sec:
        return ""
    sec = int(round(sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


fmt_clock = fmt_duration


def cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def load_docs(info: media.MediaInfo, paths: dict) -> tuple[dict, dict]:
    docs, errors = {}, {}
    for tr in info.subs:
        if not tr.text_based:
            continue
        p = paths.get(tr.index)
        if not p:
            errors[tr.index] = "Couldn't read this track."
            continue
        try:
            docs[tr.index] = sf.load(p, tr.fmt)
        except Exception as e:  # noqa: BLE001
            errors[tr.index] = str(e)
    return docs, errors


def final_changed(res) -> int:
    """How many lines the save will change (cached until the user's decisions change)."""
    key = tuple(sorted(res.overrides.items()))
    cached = getattr(res, "_final_changed", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    n = sum(1 for i, c in enumerate(res.doc.cues) if res.final(i) != c.text)
    res._final_changed = (key, n)
    return n


def throttled(emit, interval: float = 0.08):
    last = {"t": 0.0, "m": None}

    def prog(frac, msg):
        now = time.monotonic()
        if msg != last["m"] or frac >= 1.0 or frac < 0 or now - last["t"] >= interval:
            last["t"], last["m"] = now, msg
            emit(frac, msg)
    return prog


# ============================================================================ settings

class Store:
    """The app's settings as strings, where the Qt app keeps them: on Windows the registry key
    HKEY_CURRENT_USER\\Software\\SubScrub\\SubScrub (QSettings' place), so both apps share one set of settings and one
    TMDB key. With a file (tests, screenshots, or not Windows) a JSON file instead, never the real settings."""

    KEY = r"Software\SubScrub\SubScrub"

    def __init__(self, path: str | None = None):
        self.path = path
        self._file: dict = {}
        if path is None and os.name != "nt":
            self.path = os.path.join(os.path.expanduser("~"), ".config", "SubScrub", "settings.json")
        if self.path and os.path.isfile(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    self._file = {str(k): str(v) for k, v in json.load(f).items()}
            except (OSError, ValueError):
                self._file = {}

    def get(self, key: str, default: str = "") -> str:
        if self.path:
            return self._file.get(key, default)
        import winreg
        sub, _, name = key.rpartition("/")
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.KEY + ("\\" + sub.replace("/", "\\") if sub else "")) as k:
                value, kind = winreg.QueryValueEx(k, name)
        except OSError:
            return default
        if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or not isinstance(value, str):
            return default
        return value[1:] if value.startswith("@@") else value        # QSettings escapes a leading @

    def set(self, key: str, value: str):
        value = str(value)
        if self.path:
            self._file[key] = value
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._file, f, indent=1, ensure_ascii=False)
            os.replace(tmp, self.path)
            return
        import winreg
        sub, _, name = key.rpartition("/")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.KEY + ("\\" + sub.replace("/", "\\") if sub else "")) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, "@" + value if value.startswith("@") else value)


class Config:
    """Every setting the save and the file list depend on, with the Qt app's keys and defaults."""

    def __init__(self, store: Store):
        self.store = store
        s = store.get
        try:
            stored = json.loads(s("clean_options_v2", "") or "null")
        except (TypeError, ValueError):
            stored = None
        self.defaults = CleanOptions.from_dict(stored) if stored else CleanOptions()
        vm, sm = s("video_mode", "replace"), s("sub_mode", "embed")
        self.video_mode = vm if vm in VIDEO_MODES else "replace"
        self.sub_mode = sm if sm in SUB_MODES else "embed"
        self.keep_sdh = s("keep_sdh", "false") == "true"
        self.make_default = s("make_default", "true") == "true"
        self.recycle_sub = s("recycle_sub", "true") == "true"
        self.confirm_replace = s("confirm_replace", "true") == "true"
        self.track_name = s("track_name", "")
        self.preview_filter = s("preview_filter", "changes")
        self.rename_on = s("rename_on", "true") == "true"
        self.name_year = s("name_year", "false") == "true"
        self.drop_langs = s("drop_langs", "true") == "true"
        self.tmdb_key = s("tmdb_key", "")
        self.update_check = s("update_check", "true") == "true"
        self.theme = s("theme", "system")
        self.tools = {k: s(f"tools/{k}", "") for k in ("ffmpeg", "ffprobe", "mkvmerge")}

    def save(self):
        b = lambda v: "true" if v else "false"  # noqa: E731
        put = self.store.set
        put("clean_options_v2", json.dumps(self.defaults.to_dict()))
        put("video_mode", self.video_mode)
        put("sub_mode", self.sub_mode)
        put("keep_sdh", b(self.keep_sdh))
        put("make_default", b(self.make_default))
        put("recycle_sub", b(self.recycle_sub))
        put("confirm_replace", b(self.confirm_replace))
        put("track_name", self.track_name)
        put("rename_on", b(self.rename_on))
        put("name_year", b(self.name_year))
        put("drop_langs", b(self.drop_langs))
        put("tmdb_key", self.tmdb_key)
        put("update_check", b(self.update_check))
        put("theme", self.theme)
        put("preview_filter", self.preview_filter)

    def view(self) -> dict:
        d = self.defaults
        return {"video_mode": self.video_mode, "sub_mode": self.sub_mode, "keep_sdh": self.keep_sdh,
                "make_default": self.make_default, "recycle_sub": self.recycle_sub,
                "confirm_replace": self.confirm_replace, "track_name": self.track_name,
                "rename_on": self.rename_on, "name_year": self.name_year, "drop_langs": self.drop_langs,
                "tmdb_key": self.tmdb_key, "update_check": self.update_check, "theme": self.theme,
                "rules": {k: bool(getattr(d, k)) for k in RULE_KEYS}, "lyrics": lyric_mode(d)}

    def apply(self, v: dict):
        for key in ("video_mode", "sub_mode", "track_name", "tmdb_key", "theme"):
            if key in v:
                setattr(self, key, str(v[key] or "").strip())
        for key in ("keep_sdh", "make_default", "recycle_sub", "confirm_replace", "rename_on", "name_year",
                    "drop_langs", "update_check"):
            if key in v:
                setattr(self, key, bool(v[key]))
        if self.video_mode not in VIDEO_MODES:
            self.video_mode = "replace"
        if self.sub_mode not in SUB_MODES:
            self.sub_mode = "embed"
        if "rules" in v or "lyrics" in v:
            o = self.defaults.copy(**{k: bool(x) for k, x in (v.get("rules") or {}).items() if k in RULE_KEYS})
            self.defaults = with_lyric_mode(o, v.get("lyrics") or lyric_mode(o))


# ============================================================================ the session

class Session:
    def __init__(self, store: Store, emit=None, tmdb_factory=None, log_file: str | None = None):
        self.cfg = Config(store)
        self.emit = emit or (lambda *_: None)
        self.tmdb_factory = tmdb_factory or naming.TMDB
        self.lock = threading.RLock()
        self.log_lines: list[str] = []
        self.log_file = log_file
        self.tools = media.find_tools(self.cfg.tools)
        self.items: list[Item] = []
        self.pref: dict | None = None
        self.review_uid: int | None = None
        self.add_note = ""
        self.status_msg: tuple[str, str] = ("", "")
        self.last_output: str | None = None
        self.locks_banner = ""
        self._tmdb = None
        self._choices: dict[str, naming.Choice] = {}
        # background work
        self._loader: threading.Thread | None = None
        self._load_cancel = threading.Event()
        self._loading_uid: int | None = None
        self._namer: threading.Thread | None = None
        self._saver: threading.Thread | None = None
        self._save_cancel = threading.Event()
        self._saving = False
        self.batch_total = self.batch_pos = 0
        self.batch_counts: dict = {}
        self.batch_kept = 0                         # files the Recycle Bin couldn't take, left in their folders
        self.save_status: tuple[str, float] = ("", -1.0)
        # the notifier: one files view per burst of changes
        self._dirty = threading.Event()
        self._review_dirty = False
        self._closed = False
        threading.Thread(target=self._notify_loop, daemon=True, name="notify").start()
        self.log(f"{APP_NAME} {__version__} (helper). FFmpeg: {self.tools.ffmpeg or 'not found'}; "
                 f"MKVToolNix: {self.tools.mkvmerge or 'not found'}")

    # ------------------------------------------------------------------ small helpers
    def log(self, msg: str):
        line = f"{time.strftime('%H:%M:%S')}  {msg}"
        self.log_lines.append(line)
        del self.log_lines[:-4000]
        if self.log_file:
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass

    def item(self, uid) -> Item | None:
        return next((i for i in self.items if i.uid == uid), None) if uid is not None else None

    def busy(self) -> bool:
        return self._saving

    def opts_for(self, it: Item) -> CleanOptions:
        return it.opts or self.cfg.defaults

    def changed(self, review: bool = False):
        if review:
            self._review_dirty = True
        self._dirty.set()

    def _notify_loop(self):
        while not self._closed:
            self._dirty.wait()
            if self._closed:
                return
            self._dirty.clear()
            try:
                with self.lock:
                    view = self.files_view()
                    review = self._review_dirty
                    self._review_dirty = False
                self.emit("files", view)
                if review and self.review_uid is not None:
                    self.emit("review_changed", {"uid": self.review_uid})
            except Exception as e:  # noqa: BLE001
                self.log(f"Couldn't update the window: {e}")
            time.sleep(0.1)

    def close(self):
        self._save_cancel.set()
        self._load_cancel.set()
        for t in (self._saver, self._loader):
            if t is not None:
                t.join(timeout=60)
        with self.lock:
            for it in self.items:
                if it.tmpdir:
                    shutil.rmtree(it.tmpdir, ignore_errors=True)
        self._closed = True
        self._dirty.set()

    def hello(self) -> dict:
        return {"version": __version__, "tools": self.tools_view(), "settings": self.cfg.view(),
                "rules": [{"key": k, "title": t, "example": e} for k, t, e, _ in RULES]}

    def tools_view(self) -> dict:
        t = self.tools
        return {"ffmpeg": t.ffmpeg or "", "ffprobe": t.ffprobe or "", "mkvmerge": t.mkvmerge or "",
                "ready": bool(t.ready)}

    def rescan_tools(self) -> dict:
        with self.lock:
            self.tools = media.find_tools(self.cfg.tools)
            self._pump_loader()
            self.changed()
            return self.tools_view()

    # ------------------------------------------------------------------ settings
    def set_settings(self, values: dict) -> dict:
        with self.lock:
            if self.busy():
                raise RuntimeError("Settings can't change while saving.")
            old_defaults, old_key, old_rename = self.cfg.defaults, self.cfg.tmdb_key, self.cfg.rename_on
            self.cfg.apply(values)
            self.cfg.save()
            if self.cfg.tmdb_key != old_key:
                self._tmdb = None
                for it in self.items:
                    if it.kind == "video" and it.name_state in ("nokey", "error"):
                        it.name_state = ""
            if self.cfg.defaults != old_defaults:
                for it in self.items:
                    if it.opts is None and it.loaded:
                        self._compute_result(it)
            if self.cfg.rename_on != old_rename or self.cfg.tmdb_key != old_key:
                self._pump_names()
            self.changed(review=True)
            return self.cfg.view()

    def set_theme(self, mode: str):
        with self.lock:
            self.cfg.theme = mode if mode in ("system", "light", "dark") else "system"
            self.cfg.store.set("theme", self.cfg.theme)

    # ------------------------------------------------------------------ renaming (TMDB)
    def _renaming(self) -> bool:
        return self.cfg.rename_on and self.cfg.video_mode == "replace"

    def _new_name(self, it: Item) -> str | None:
        return batch.new_name(it, self.cfg.name_year) if self._renaming() else None

    def _lang_drops(self, it: Item) -> list:
        return batch.other_languages(it) if self.cfg.drop_langs and self.cfg.video_mode == "replace" else []

    def _has_tidy(self, it: Item) -> bool:
        return it.kind == "video" and it.state in ("ready", "skipped", "failed") and bool(
            self._new_name(it) or self._lang_drops(it))

    def _tidy_note(self, it: Item) -> str:
        if not self._has_tidy(it):
            return ""
        n = len(self._lang_drops(it))
        return f"{plural(n, 'other-language subtitle track')} will be removed" if n else ""

    def _api(self):
        if self._tmdb is None:
            self._tmdb = self.tmdb_factory(self.cfg.tmdb_key)
        return self._tmdb

    def _pump_names(self):
        """Look up one video at a time, once it has been read (its length helps tell titles apart)."""
        if not self._renaming() or (self._namer is not None and self._namer.is_alive()):
            return
        todo = [i for i in self.items if i.kind == "video" and i.pending and i.loaded and i.name_state == ""]
        if not todo:
            return
        if not self.cfg.tmdb_key:
            for i in todo:
                i.name_state = "nokey"
            self.changed()
            return
        it = todo[0]
        it.name_state = "looking"
        api, name, dur, uid = self._api(), it.name, it.info.duration if it.info else 0.0, it.uid

        def run():
            try:
                m = naming.match(api, name, dur)
            except Exception as e:  # noqa: BLE001
                with self.lock:
                    self._namer = None
                    self.log(f"Title lookup failed: {e}")
                    for x in self.items:
                        if x.uid == uid or (x.kind == "video" and x.name_state == ""):
                            x.name_state, x.name_note = "error", str(e) or e.__class__.__name__
                    self.changed(review=True)
                return
            with self.lock:
                self._namer = None
                x = self.item(uid)
                if x is not None:
                    x.match, x.name_state, x.name_note = m, "done", m.note
                self.changed(review=uid == self.review_uid)
                self._pump_names()
        self._namer = threading.Thread(target=run, daemon=True, name="names")
        self._namer.start()
        self.changed()

    def name_line(self, it: Item) -> tuple[str, str]:
        """(text, tone) for the line under a file's name: the new name, or where the title lookup stands."""
        if it.kind != "video" or not self._renaming() or it.state in ("done", "saving", "queued", "probing", "reading"):
            return "", ""
        st = it.name_state
        if st in ("", "looking"):
            return "Looking up the title…", "faint"
        if st == "nokey":
            return "", ""
        if st == "error":
            return it.name_note or "Couldn't look up the title", "alert"
        if it.name_pick == "keep":
            return "Keeping this name", "faint"
        new = self._new_name(it)
        if new:
            return new, ""
        if batch.name_choice(it) is not None:
            return "", ""
        if batch.name_unsure(it):
            return "Not sure which title this is, choose one in Review", "alert"
        return "No title match found, keeping this name", "faint"

    def _choice_view(self, it: Item, c: naming.Choice) -> dict:
        key = f"{c.kind}:{c.tmdb_id}:{c.season}:{','.join(map(str, c.episodes))}"
        self._choices[key] = c
        return {"id": key, "label": c.label(), "new_name": naming.render(c, Path(it.path).suffix, self.cfg.name_year)}

    def name_choices(self, uid: int) -> dict:
        with self.lock:
            it = self.item(uid)
            if it is None or it.kind != "video":
                raise RuntimeError("That file isn't in the list.")
            m = it.match
            return {"name": it.name, "query": m.parsed.title if m else "",
                    "choices": [self._choice_view(it, c) for c in (m.choices if m else [])[:6]],
                    "can_search": bool(self.cfg.tmdb_key)}

    def name_search(self, uid: int, text: str) -> dict:
        with self.lock:
            it = self.item(uid)
            if it is None:
                raise RuntimeError("That file isn't in the list.")
            api, m = self._api(), it.match
        kind = "episode" if m and m.parsed.is_episode else "movie"
        parsed = m.parsed if m else naming.parse(it.name)
        try:
            found = naming.search(api, text.strip(), kind, parsed)
        except naming.LookupError_ as e:
            return {"choices": [], "note": str(e)}
        with self.lock:
            views = [self._choice_view(it, c) for c in found[:6]]
        return {"choices": views, "note": "" if views else "No matches. Try a different title below."}

    def name_pick(self, uid: int, pick: str) -> dict:
        with self.lock:
            it = self.item(uid)
            if it is None:
                raise RuntimeError("That file isn't in the list.")
            if pick == "keep":
                it.name_pick = "keep"
            elif pick in self._choices:
                it.name_pick = self._choices[pick]
            else:
                raise RuntimeError("That title isn't on offer any more.")
            self.changed(review=True)
            return self.row_view(it)

    def undo_renames_question(self) -> dict | None:
        rows = naming.last_batch()
        if not rows:
            return None
        return {"title": "Undo the last renames?",
                "text": f"Put the original names back on {plural(len(rows), 'file')} from {rows[0]['time']}?",
                "detail": "Only the names change back. Removed subtitle tracks and originals in the Recycle Bin stay "
                          "as they are.",
                "buttons": [{"key": "cancel", "label": "Cancel", "kind": ""},
                            {"key": "go", "label": "Undo Renames", "kind": "primary"}]}

    def undo_renames(self) -> dict:
        with self.lock:
            if self.busy():
                raise RuntimeError("Wait for the save to finish.")
            rows = naming.last_batch()
            done, problems = 0, []
            for r in rows:
                new, old = r["new"], r["old"]
                try:
                    if not os.path.isfile(new):
                        problems.append(f"“{os.path.basename(new)}” isn't there any more")
                    elif os.path.exists(old):
                        problems.append(f"“{os.path.basename(old)}” already exists")
                    else:
                        sides = media.find_sidecars(new)
                        os.rename(new, old)
                        media._rename_sidecars(new, old, sides, self.log)
                        done += 1
                        for it in self.items:
                            if os.path.normcase(it.path) == os.path.normcase(new):
                                it.path = old
                except OSError as e:
                    problems.append(f"“{os.path.basename(new)}”: {e}")
            if rows and not problems:
                naming.mark_undone(rows[0]["batch"])
            self.log(f"Undid {done} rename(s)")
            self.changed()
            if problems:
                return {"title": "Some names couldn't be put back",
                        "text": f"{plural(done, 'file')} renamed back. Not changed:", "rows": [[p, ""] for p in problems[:8]]}
            return {"title": "Renames undone", "text": f"{plural(done, 'file')} renamed back."}

    # ------------------------------------------------------------------ adding and removing
    def leftovers(self, paths: list[str]) -> list[dict]:
        out = []
        for k, lo in enumerate(media.find_leftovers(paths)):
            out.append({"id": k, "path": lo.path, "name": lo.name, "size": fmt_size(lo.size),
                        "original": bool(lo.original)})
        return out

    def leftovers_action(self, paths: list[str], action: str, which: list[str] | None = None) -> dict:
        """'remove': unfinished copies (with their original) go to the Recycle Bin. 'restore': a copy whose
        original is missing gets the original name back."""
        failed = []
        for lo in media.find_leftovers(paths):
            if which is not None and lo.path not in which:
                continue
            try:
                if action == "remove" and lo.original:
                    why = media.trash(lo.path, self.log)
                    if why is not None:
                        failed.append([lo.name, f"Kept: {why}"[:60]])
                        continue
                    self.log(f"Moved the unfinished copy {lo.path} to the Recycle Bin")
                elif action == "restore" and not lo.original:
                    new = media.restore_leftover(lo)
                    self.log(f"Renamed {lo.path} back to {new}")
            except Exception as err:  # noqa: BLE001
                failed.append([lo.name, str(err)[:60]])
        return {"failed": failed}

    def add(self, paths: list[str]) -> dict:
        """Add files and folders. Returns messages to show (picture subtitles, nothing found, FFmpeg needed)."""
        with self.lock:
            if self.busy():
                raise RuntimeError("Wait for the save to finish.")
            messages = []
            files, skipped = batch.expand_paths(paths)
            if skipped:
                messages.append({"title": "Picture subtitles can't be cleaned",
                                 "text": "These are picture-based subtitle files (Blu-ray or DVD), which can't be "
                                         "edited as text:",
                                 "rows": [[os.path.basename(p), ""] for p in skipped[:8]]})
            if not files:
                if paths and not skipped:
                    messages.append({"title": "Nothing to add", "text": "No video or subtitle files were found there."})
                return {"added": 0, "messages": messages}
            explicit = {p for p in paths if os.path.isfile(p)}
            known = {i.path for i in self.items if i.kind == "video" and i.pending}
            plan = batch.pair_up(files, explicit=explicit, embed=self.cfg.sub_mode == "embed", known_videos=known)
            files = plan.videos + plan.subtitles
            if not files:
                if plan.orphans:
                    messages.append({"title": "Nothing to add",
                                     "text": "Only subtitle files without a matching video were found there."})
                return {"added": 0, "messages": messages}
            videos = list(plan.videos)
            if videos and not self.tools.ready:
                self.tools = media.find_tools(self.cfg.tools)
                if not self.tools.ready:
                    messages.append({"title": "FFmpeg is needed",
                                     "text": "SubScrub needs FFmpeg to read video files. Set it up, then add them "
                                             "again.", "tools": True})
                    files = [f for f in files if f not in videos]
                    if not files:
                        return {"added": 0, "messages": messages}
            existing = {os.path.normcase(i.path): i for i in self.items}
            added = 0
            for f in files:
                old = existing.get(os.path.normcase(f))
                if old is not None:
                    if old.pending:
                        old.dropped |= set(plan.dropped_with.get(f, []))
                        continue
                    self._remove(old)                      # finished before: start it over
                it = Item(path=f, kind=batch.kind_for(f))
                it.dropped = set(plan.dropped_with.get(f, []))
                self.items.append(it)
                added += 1
            paired = sum(len(v) for v in plan.dropped_with.values())
            bits = []
            if paired:
                bits.append(f"{plural(paired, 'subtitle file')} matched to {plural(len(plan.dropped_with), 'video')}")
            if plan.orphans:
                bits.append(f"{plural(len(plan.orphans), 'subtitle file')} without a matching video left alone")
            self.add_note = ("; ".join(bits) + ".") if bits else ""
            self.status_msg = ("", "")
            self._pump_loader()
            self.changed()
            return {"added": added, "messages": messages, "last_dir": os.path.dirname(files[0])}

    def _remove(self, it: Item):
        if self._loading_uid == it.uid:
            self._load_cancel.set()
        if it.tmpdir:
            shutil.rmtree(it.tmpdir, ignore_errors=True)
        self.items.remove(it)
        if self.review_uid == it.uid:
            self.review_uid = None

    def remove(self, uid: int):
        with self.lock:
            if self.busy():
                raise RuntimeError("Wait for the save to finish.")
            it = self.item(uid)
            if it is not None:
                self._remove(it)
            if not self.items:
                self.add_note = ""
                self.status_msg = ("", "")
            self._pump_loader()
            self.changed()

    def clear(self):
        with self.lock:
            if self.busy():
                raise RuntimeError("Wait for the save to finish.")
            for it in list(self.items):
                self._remove(it)
            self.add_note = ""
            self.status_msg = ("", "")
            self.changed()

    # ------------------------------------------------------------------ background loading
    def _pump_loader(self):
        if self.busy() or (self._loader is not None and self._loader.is_alive()):
            return
        while True:
            it = next((i for i in self.items if i.state == "queued"), None)
            if it is None:
                return
            if it.kind != "subtitle":
                break
            try:
                info, doc = media.probe_subtitle_file(it.path)
                it.info, it.docs = info, {0: doc}
                self._finish_load(it)
            except Exception as e:  # noqa: BLE001
                it.state, it.message = "failed", f"couldn't read it ({e})"
        if not self.tools.ready:
            return
        it.state = "probing"
        self._load_cancel = threading.Event()
        self._loading_uid = it.uid
        self._loader = threading.Thread(target=self._load, args=(it, self._load_cancel), daemon=True, name="load")
        self._loader.start()

    def _load(self, it: Item, cancel: threading.Event):
        uid, path, tools = it.uid, it.path, self.tools
        tmp = None
        try:
            info = media.probe(tools, path, self.log)
            paths, docs, errors = {}, {}, {}
            if any(t.text_based for t in info.subs):
                with self.lock:
                    if self.item(uid) is None or cancel.is_set():
                        return
                    it.info, it.state, it.progress = info, "reading", -1.0
                    self.changed(review=uid == self.review_uid)
                tmp = tempfile.mkdtemp(prefix="subscrub_")

                def prog(frac, _msg):
                    with self.lock:
                        it.progress = frac
                        self.changed()
                paths = media.extract_subs(tools, info, info.subs, tmp, throttled(prog, 0.15), cancel, self.log)
                docs, errors = load_docs(info, paths)
            with self.lock:
                if self.item(uid) is None or cancel.is_set():
                    if tmp:
                        shutil.rmtree(tmp, ignore_errors=True)
                    return
                it.info, it.docs, it.doc_errors, it.tmpdir = info, docs, errors, tmp
                for idx, err in errors.items():
                    self.log(f"{it.name}: track {idx}: {err}")
                batch.attach_sidecars(it, self.log)
                self._finish_load(it)
        except media.Cancelled:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
        except Exception as e:  # noqa: BLE001
            with self.lock:
                if self.item(uid) is not None:
                    self.log(f"Couldn't read {path}: {e}")
                    it.state, it.message = "failed", "couldn't read this file"
                    self.changed(review=uid == self.review_uid)
        finally:
            with self.lock:
                if self._loading_uid == uid:
                    self._loading_uid = None
                self._loader = None
                self._pump_loader()
                if not any(i.state in ("queued", "probing", "reading") for i in self.items):
                    self._check_locks_async()

    def _finish_load(self, it: Item):
        batch.measure(it)
        if not it.manual or it.sel not in it.docs:
            it.sel = batch.pick_track(it, self.pref)
        it.state = "ready"
        it.progress = -1
        self._compute_result(it)
        self.changed(review=it.uid == self.review_uid)
        self._pump_names()

    def _compute_result(self, it: Item):
        it.result = batch.clean_item(it, self.cfg.defaults) if it.sel is not None else None

    def wait_idle(self, timeout: float = 600) -> bool:
        """For tests and the scripted QA run: wait until nothing is being read or looked up."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self.lock:
                quiet = not any(i.state in ("queued", "probing", "reading") or i.name_state == "looking"
                                for i in self.items) and not (self._namer and self._namer.is_alive()) \
                    and not self.busy()
            if quiet:
                return True
            time.sleep(0.05)
        return False

    # ------------------------------------------------------------------ verdicts
    @staticmethod
    def track_label(it: Item, tr) -> str:
        if it.kind == "subtitle":
            return tr.lang_label
        if tr.external:
            name = os.path.basename(tr.external)
            stem = Path(it.path).stem
            return f"{tr.lang_label} from the {name[len(stem):]} file" if name.lower().startswith(stem.lower()) \
                else f"{tr.lang_label} from “{name}”"
        return tr.title or tr.lang_label

    def verdict(self, it: Item) -> tuple[str, str, str, bool]:
        """(detail line, status text, status kind, show Review button) for a file."""
        st = it.state
        if st == "queued":
            return "Waiting", "", "", False
        if st == "probing":
            return "Opening", "", "", False
        if st == "reading":
            pct = f" {int(it.progress * 100)}%" if it.progress >= 0 else ""
            return f"Reading subtitles{pct}", "", "", False
        if st == "saving":
            return it.message or "Saving", "Saving", "busy", False
        if st == "done":
            out = os.path.basename(it.output) if it.output else ""
            same = it.output and os.path.normcase(it.output) == os.path.normcase(it.path)
            if it.done_text:
                return it.done_text, "Saved", "done", False
            what = "Cleaned track saved into the video" if same and it.kind == "video" else (
                "Cleaned and saved" if same or not out else f"Saved as {out}")
            return what, "Saved", "done", False
        if st == "failed":
            return cap(it.message) if it.message else "Something went wrong", "Failed", "failed", bool(it.info)
        tr = it.track()
        info = it.info
        if info is None:
            return "Couldn't read this file", "Failed", "failed", False
        has_tidy = self._has_tidy(it)
        tidy = self._tidy_note(it)
        if tr is None:
            if info.subs and not any(t.text_based for t in it.tracks()):
                return "Only picture-based subtitles (PGS or VobSub), which would need OCR", "Can't Edit", "dim", True
            if has_tidy:
                return cap(tidy) if tidy else "No subtitles; only the name changes", "Ready", "ready", False
            return "No subtitles in this file or next to it", "Nothing to Do", "dim", False
        label = self.track_label(it, tr)
        if it.sel in it.doc_errors:
            return f"{label}: couldn't read this track", "Failed", "failed", True
        if st == "skipped":
            return f"{label}: {it.message}", "Skipped", "dim", True
        res = it.result
        if res is None:
            return label, "", "", True
        if not it.force and not batch.auto_cleaned(it, tr):
            if has_tidy:
                return f"{label}: regular subtitles" + (f", {tidy}" if tidy else ""), "Ready", "ready", True
            return f"{label}: regular subtitles, nothing to strip", "No SDH", "dim", True
        n = final_changed(res)
        if n == 0 and not tr.external and not batch.plain_to_default(it, tr):
            if has_tidy:
                return f"{label}: nothing to remove" + (f", {tidy}" if tidy else ""), "Ready", "ready", True
            return f"{label}: nothing to remove", "Already Clean", "dim", True
        if it.locked:
            who = sorted({w for names in it.locked.values() for w in names})
            return f"{label}: open in {' and '.join(who)}, pause it there before saving", "In Use", "check", True
        bits = [label]
        if tr.external and self.cfg.video_mode != "sidecar":
            bits.append("goes into the video" + (" and the .srt is removed" if self.cfg.recycle_sub
                                                  and self.cfg.video_mode == "replace" and not self.cfg.keep_sdh else ""))
        if it.opts is not None:
            bits.append("custom edits")
        if tidy:
            bits.append(tidy)
        if res.notes.ocr_suspect:
            bits.append("converted from a disc image")
        unresolved = len(res.unresolved())
        detail = ", ".join(bits)
        if unresolved == 0:
            return detail, "Ready", "ready", True
        if res.confidence == "low":
            return detail, "Look Closer", "low", True
        return detail, f"{unresolved} to Check", "check", True

    def row_count(self, it: Item) -> int:
        """How many lines change (the number column in the file list); 0 shows nothing."""
        if it.state == "done":
            return it.cleaned
        tr = it.track()
        if it.result is None or it.state not in ("ready", "failed", "skipped") or tr is None:
            return 0
        if not it.force and not batch.auto_cleaned(it, tr):
            return 0
        return final_changed(it.result)

    def row_view(self, it: Item) -> dict:
        detail, chip, kind, review = self.verdict(it)
        prog = None
        if it.state == "reading":
            prog = it.progress
        elif it.state == "probing":
            prog = -1.0
        elif it.state == "saving":
            prog = it.progress if it.progress >= 0 else -1.0
        line, tone = self.name_line(it)
        tip = it.path
        if it.state in ("done", "failed", "skipped") and it.message:
            tip += "\n\n" + it.message
        if it.output and it.output != it.path:
            tip += f"\nSaved as {it.output}"
        n = self.row_count(it)
        return {"uid": it.uid, "name": it.name, "path": it.path, "kind": it.kind, "state": it.state,
                "detail": detail, "badge": chip, "badge_kind": kind, "progress": prog,
                "review": bool(review and not self.busy()), "count": n,
                "count_label": ("cleaned" if it.state == "done" else "to clean") if n else "",
                "rename": line, "rename_tone": tone, "tip": tip, "removable": not self.busy()}

    # ------------------------------------------------------------------ the Files view
    def summary_texts(self) -> tuple[str, str]:
        counts = {}
        for it in self.items:
            _, chip, kind, _ = self.verdict(it)
            key = {"ready": "ready", "check": "to check", "low": "to check", "done": "saved", "failed": "failed",
                   "busy": "saving"}.get(kind)
            if it.locked and it.state == "ready":
                key = "in use"
            if it.state in ("queued", "probing", "reading"):
                key = "reading"
            elif kind == "dim":
                key = "with nothing to do"
            if key:
                counts[key] = counts.get(key, 0) + 1
        order = ["ready", "to check", "in use", "reading", "saving", "with nothing to do", "saved", "failed"]
        text = ", ".join(f"{counts[k]} {k}" for k in order if k in counts)
        if any(it.kind == "video" and it.name_state == "nokey" and it.pending for it in self.items):
            text += (". " if text else "") + "Add a TMDB key in Settings to rename videos."
        c = self.cfg
        pending = [i for i in self.items if i.pending and i.loaded and i.track() is not None]
        plain = sum(1 for i in pending if batch.plain_pick(i, i.track()))
        if not pending:
            sdh_note = ""
        elif c.keep_sdh or plain == len(pending) and plain:
            sdh_note = " and the SDH track is kept"
        elif plain:
            sdh_note = (". Files with a regular English track clean that one and keep the SDH track; "
                        "otherwise the SDH track is dropped")
        else:
            sdh_note = " and the SDH track is dropped"
        how = {"replace": "Saving replaces each original, which goes to the Recycle Bin once the new file checks out. "
                          f"The cleaned track {'becomes the default' if c.make_default else 'is added'}{sdh_note}.",
               "new": "Saving makes a new “.dialogue” copy next to each video.",
               "sidecar": "Saving writes a cleaned .srt next to each video."}[c.video_mode]
        if c.recycle_sub and c.video_mode == "replace" and any(i.sidecars for i in self.items):
            how += " Matching .srt files go into their video and are then removed."
        if any(i.kind == "subtitle" for i in self.items):
            how += f" Subtitle files on their own: {SUB_MODES[c.sub_mode].lower()}."
        return text.strip(), how

    def status_view(self) -> dict:
        if self.busy():
            text, frac = self.save_status
            return {"text": text, "tone": "", "progress": frac}
        loading = [i for i in self.items if i.state in ("queued", "probing", "reading")]
        if loading and self.tools.ready:
            done = len([i for i in self.items if i.info is not None])
            cur = next((i for i in loading if i.state == "reading" and i.progress >= 0), None)
            return {"text": f"Reading files… {done} of {len(self.items)}", "tone": "",
                    "progress": cur.progress if cur else -1.0}
        if loading:
            return {"text": "FFmpeg is needed to read the videos: click “FFmpeg Needed” at the top.", "tone": "bad",
                    "progress": None}
        text, tone = self.status_msg
        return {"text": text, "tone": tone, "progress": None}

    def buttons_view(self) -> dict:
        busy = self.busy()
        pending = [i for i in self.items if i.pending]
        n = len(pending)
        if len(self.items) <= 1:
            it = self.items[0] if self.items else None
            text = "Saved" if it and it.state == "done" else "Clean and Save"
        else:
            text = f"Clean and Save {n} Files" if n > 1 else "Clean and Save"
        needs_ffmpeg = any(i.kind == "video" or self.cfg.sub_mode == "embed" for i in pending)
        return {"primary": text, "primary_visible": n > 0 and not busy,
                "primary_enabled": n > 0 and not busy and (self.tools.ready or not needs_ffmpeg),
                "stop_visible": busy, "open_visible": bool(self.last_output) and not busy,
                "clear_visible": len(self.items) > 1 and not busy}

    def files_view(self) -> dict:
        with self.lock:
            summary, how = self.summary_texts() if self.items else ("", "")
            return {"rows": [self.row_view(i) for i in self.items], "summary": summary,
                    "note": cap(self.add_note), "how": how, "locks": self.locks_banner,
                    "status": self.status_view(), "buttons": self.buttons_view(), "busy": self.busy(),
                    "count": len(self.items), "tools": self.tools_view(), "review_uid": self.review_uid}

    # ------------------------------------------------------------------ files other programs have open
    def files_touched(self, it: Item) -> list[str]:
        c = self.cfg
        if it.kind == "video":
            sidecars = [t.external for t in it.sidecars] if it.sidecars else media.find_sidecars(it.path)
            if c.video_mode == "replace":
                return [it.path] + (sidecars if c.recycle_sub else [])
            if c.video_mode == "sidecar":
                return sidecars if c.recycle_sub else []
            return []
        comp = it.info.companion if it.info else media.find_matching_video(it.path)
        if c.sub_mode == "embed" and comp:
            return [comp] + ([it.path] if c.recycle_sub else [])
        return [it.path] if c.sub_mode == "replace" else []

    def _apply_locks(self, found: dict):
        for it in self.items:
            mine = {p: found[p] for p in self.files_touched(it) if p in found} if it.pending else {}
            it.locked = mine
        locked = [i for i in self.items if i.locked]
        if locked:
            who = sorted({w for i in locked for names in i.locked.values() for w in names})
            self.locks_banner = (
                f"{plural(len(locked), 'file')} {'is' if len(locked) == 1 else 'are'} open in {' and '.join(who)}. "
                "If you're seeding, pause those torrents before saving: SubScrub can't rewrite a file another "
                "program has open. This updates when you come back to this window.")
        else:
            self.locks_banner = ""
        self.changed()

    def check_locks(self) -> dict:
        """Which pending files other programs have open (the window asks when it is activated)."""
        with self.lock:
            if self.busy():
                return {"banner": self.locks_banner}
            paths = [p for i in self.items if i.pending for p in self.files_touched(i)]
        found = media.who_has_open(paths) if paths else {}
        with self.lock:
            self._apply_locks(found)
            return {"banner": self.locks_banner}

    def _check_locks_async(self):
        threading.Thread(target=lambda: self.check_locks(), daemon=True, name="locks").start()

    # ------------------------------------------------------------------ Review
    def review(self, uid: int, show: str | None = None, first: bool = False) -> dict:
        with self.lock:
            it = self.item(uid)
            if it is None:
                raise RuntimeError("That file isn't in the list any more.")
            self.review_uid = uid
            it.reviewed = True
            res = it.result
            if show is None or first:
                show = "check" if res is not None and res.unresolved() else (
                    self.cfg.preview_filter if self.cfg.preview_filter != "check" else "changes")
            elif show != "check" and show != self.cfg.preview_filter:
                self.cfg.preview_filter = show
                self.cfg.store.set("preview_filter", show)
            return self._review_view(it, show)

    def _head(self, it: Item) -> dict:
        detail, chip, kind, _ = self.verdict(it)
        n = self.row_count(it)
        text = detail.rstrip(".") + (f", {n:,} lines to clean" if n and it.state != "done" else "")
        notes = list(it.result.notes.notes) if it.result else []
        if notes:
            text += ". " + " ".join(notes)
        tr = it.track()
        nosdh = bool(tr) and it.loaded and not it.force and not batch.auto_cleaned(it, tr)
        nosdh_text = ""
        if nosdh:
            nosdh_text = ("This file only has regular subtitles, with no sound descriptions or speaker names to strip. "
                          + ("Saving only renames it and tidies its tracks; the subtitles stay as they are."
                             if self._has_tidy(it) else "It's skipped when saving."))
        line, tone = self.name_line(it)
        can = (it.kind == "video" and self._renaming() and bool(self.cfg.tmdb_key) and it.name_state == "done"
               and it.pending and it.match is not None and bool(it.match.choices or it.match.parsed.title))
        multi = len(self.items) > 1
        return {"uid": it.uid, "name": it.name, "path": it.path, "detail": text, "badge": chip, "badge_kind": kind,
                "nosdh": nosdh_text, "name_row": can, "name_text": (line or "The name is already right") if can else "",
                "name_tone": tone, "multi": multi,
                "save_one": multi and it.pending and not self.busy()}

    def _tracks(self, it: Item) -> tuple[list, str, str, str]:
        info = it.info
        if info is None:
            return [], ("Couldn't read this file." if it.state == "failed" else "Opening…"), "", ""
        if it.kind == "subtitle":
            n = len(it.docs.get(0).cues) if it.docs.get(0) else 0
            bits = [f"{info.format_name.upper()} subtitle file", f"{n:,} lines",
                    f"belongs to “{os.path.basename(info.companion)}”" if info.companion
                    else "no matching video in its folder"]
        else:
            bits = [info.kind.upper() if info.kind != "other" else Path(info.path).suffix.upper().lstrip(".")]
            if info.duration:
                bits.append(fmt_duration(info.duration))
            bits.append(fmt_size(info.size))
        alltr = it.tracks()
        plain = batch.plain_partner(it)
        tiles = []
        for tr in alltr:
            stat, tone, sdh = "", "", False
            if tr.text_based and it.state == "reading":
                stat = "Reading…"
            elif tr.text_based and tr.index in it.docs:
                n = it.sdh_counts.get(tr.index, 0)
                sdh = batch.is_sdh(it, tr)
                if tr.index in it.doc_errors:
                    stat, tone = "Couldn't read", "warn"
                elif sdh:
                    stat, tone = plural(n, "sound or name note"), "accent" if tr.index == it.sel else ""
                elif tr.index == plain:
                    stat, tone = "Regular, used as a cross-check", "dim"
                elif tr.index == it.sel and batch.plain_pick(it, tr):
                    stat, tone = "Regular, becomes the default", "accent"
                else:
                    stat, tone = "Regular subtitles", "dim"
            elif not tr.text_based:
                stat, tone = "Picture-based, can't be edited", "dim"
            flags = []
            if tr.default:
                flags.append("Default")
            if tr.forced:
                flags.append("Forced")
            if sdh or tr.sdh_flagged:
                flags.append("SDH")
            title = (os.path.basename(tr.external) if tr.external else tr.title) or tr.lang_label
            tiles.append({"index": tr.index, "title": title, "language": tr.lang_label, "codec": tr.codec_label,
                          "flags": flags, "stat": stat, "tone": tone, "selected": tr.index == it.sel,
                          "enabled": bool(tr.text_based) and not self.busy(), "external": bool(tr.external)})
        text_tracks = [t for t in alltr if t.text_based]
        note = ""
        if it.sidecars:
            note = ("Subtitle files next to the video are listed too. Whichever track gets cleaned ends up inside the "
                    "video" + ("; afterwards, files in the same language next to it go to the Recycle Bin so the "
                               "video is a single file." if self.cfg.recycle_sub and self.cfg.video_mode == "replace"
                               else "."))
        if not alltr:
            note = "This file has no subtitle tracks, and there are no subtitle files next to it."
        elif not text_tracks:
            note = ("All of this file's subtitles are picture-based (Blu-ray PGS or DVD VobSub). They'd need OCR to "
                    "become editable text, which SubScrub doesn't do yet.")
        elif len([i for i in self.items if i.kind == "video"]) > 1 and it.kind == "video" and \
                len(text_tracks) > 1 and not it.sidecars:
            note = "Picking a different track here picks the matching track in the other files too."
        return tiles, ", ".join(bits), plural(len(alltr), "track") if alltr else "", note

    def _rules(self, it: Item) -> dict:
        o = self.opts_for(it)
        res = it.result
        rows = []
        for key, title, example, rules in RULES:
            count = sum(res.stats.by_rule.get(r, 0) for r in rules) if res is not None and getattr(o, key) else None
            visible = True
            if key == "ocr_fixes":
                visible = bool(res and res.notes.ocr_suspect)
            elif key == "quote_italics":
                visible = bool(res and res.notes.quote_voices)
            elif key == "mixed_case_labels":
                visible = not (res and res.notes.mixed_names)
            rows.append({"key": key, "title": title, "example": example, "on": bool(getattr(o, key)),
                         "count": count, "visible": visible})
        n_lyr = (res.stats.by_rule.get("lyrics", 0) + res.stats.by_rule.get("lyric_style", 0)) if res else 0
        return {"rows": rows, "lyrics": lyric_mode(o), "lyric_count": n_lyr, "custom": it.opts is not None,
                "state": "Changed for this file" if it.opts is not None else "Your defaults"}

    def _legend(self, it: Item) -> list:
        res = it.result
        st = res.stats
        unresolved = len(res.unresolved())
        kept_by_user = sum(1 for v in it.overrides.values() if v == "orig")
        items = []
        if unresolved:
            items.append({"kind": "check", "text": f"{unresolved:,} to check"})
        items += [{"kind": "removed", "text": f"{st.removed:,} removed"},
                  {"kind": "trim", "text": f"{st.edited:,} trimmed"}]
        untouched = f"{st.total - st.changed:,} untouched"
        if kept_by_user:
            untouched += f", {kept_by_user} kept by you"
        items.append({"kind": "kept", "text": untouched})
        return items

    def _prep(self, res) -> tuple[list, list]:
        doc = res.doc
        cached = getattr(doc, "_preview_prep", None)
        if cached is not None:
            return cached
        times, befores = [], []
        for c in doc.cues:
            try:
                s, e = to_seconds(c.start), to_seconds(c.end)
            except (TypeError, ValueError):
                s = e = 0.0
            times.append((s, max(e, s)))
            befores.append(visible_text(c.text, doc.fmt))
        doc._preview_prep = (times, befores)
        return doc._preview_prep

    def cue_view(self, it: Item, i: int) -> dict:
        """One line of the before/after list: the original with removed parts marked, and what is saved."""
        res = it.result
        times, befores = self._prep(res)
        fmt = res.doc.fmt
        cue, r = res.doc.cues[i], res.results[i]
        start, end = times[i]
        before = befores[i]
        status = "kept" if not r.changed else ("removed" if r.text is None else "edited")
        override = res.overrides.get(i)
        flagged = r.changed and r.risk >= 2
        if not r.changed or override == "orig":
            after, final = before, cue.text
        else:
            after = None if r.text is None else visible_text(r.text, fmt)
            final = r.text or ""
        # the timeline: unchanged lines have no mark ("none"), lines the user kept are grey ("kept")
        mark = "flagged" if flagged and override is None else (
            "kept" if override == "orig" else ("none" if status == "kept" else status))
        # the original, split into kept and struck-out pieces (the Qt app's diff_html, as data)
        segs = []
        if status != "kept" and override != "orig":
            if after is None:
                segs = [[before, True]]
            else:
                for op, a0, a1, _b0, _b1 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
                    seg = before[a0:a1]
                    if not seg:
                        continue
                    gone = op in ("delete", "replace") and bool(seg.strip())
                    if segs and segs[-1][1] == gone:
                        segs[-1][0] += seg
                    else:
                        segs.append([seg, gone])
        else:
            segs = [[before, False]]
        return {"cue": i, "start": start, "end": end, "time": fmt_clock(start) or "0:00", "status": status,
                "before": [{"text": t, "gone": g} for t, g in segs], "after": after, "flagged": bool(flagged), "why": "; ".join(r.why[:2]) if r.changed else "",
                "override": override or "", "italic": bool(re.search(r"<i>|\\i1", final or "", re.I)), "mark": mark}

    def _review_view(self, it: Item, show: str) -> dict:
        tiles, meta, count, note = self._tracks(it)
        view = {"uid": it.uid, "head": self._head(it), "tracks": tiles, "meta": meta, "track_count": count,
                "tracks_note": note, "rules": self._rules(it), "filter": show, "empty": "", "legend": [],
                "rows": [], "marks": [], "duration": 0.0, "check_option": False, "nav": False,
                "preview_only": False, "busy": self.busy(),
                "index": next((k for k, i in enumerate(self.items) if i is it), 0), "total": len(self.items)}
        res = it.result
        if it.info is None or it.state in ("queued", "probing", "reading"):
            view["empty"] = "Couldn't read this file." if it.state == "failed" else "Reading subtitles…"
            return view
        tr = it.track()
        if tr is None:
            view["empty"] = "No editable subtitles in this file."
            return view
        if tr.index in it.doc_errors:
            view["empty"] = "SubScrub couldn't read this track.\n" + it.doc_errors[tr.index][:300]
            return view
        if res is None:
            view["empty"] = "Reading subtitles…"
            return view
        view["legend"] = self._legend(it)
        flagged = len(res.flagged())
        view["check_option"] = flagged > 0
        if show == "check" and not flagged:
            show = view["filter"] = "changes"
        view["preview_only"] = bool(view["head"]["nosdh"])
        if res.stats.changed == 0 and show != "all":
            view["empty"] = ("Nothing to remove in this track. It's already dialogue-only.\n"
                             "Switch to Everything to read through it.")
            return view
        rows, marks = [], []
        for i in range(len(res.results)):
            c = self.cue_view(it, i)
            marks.append({"t": round(c["start"], 2), "k": c["mark"]})
            if show == "changes" and c["status"] == "kept":
                continue
            if show == "check" and not c["flagged"]:
                continue
            rows.append(c)
        rows.sort(key=lambda x: x["start"])
        marks.sort(key=lambda m: m["t"])
        view.update(rows=rows, marks=marks, nav=res.stats.changed > 0,
                    duration=it.info.duration or (marks[-1]["t"] if marks else 0.0))
        return view

    def select_track(self, uid: int, index: int, show: str | None = None) -> dict:
        with self.lock:
            it = self.item(uid)
            if it is None or self.busy():
                raise RuntimeError("Can't change the track right now.")
            if it.sel != index:
                it.overrides = {}
            it.sel, it.manual = index, True
            tr = it.track()
            if tr and it.kind == "video":
                self.pref = batch.pref_from(tr)
                for other in self.items:
                    if other is it or other.manual or not other.loaded or other.kind != "video":
                        continue
                    new = batch.pick_track(other, self.pref)
                    if new != other.sel:
                        other.sel, other.overrides = new, {}
                        self._compute_result(other)
            it.force = bool(tr) and not batch.auto_cleaned(it, tr)
            self._compute_result(it)
            self.changed()
            return self.review(uid, show)

    def force_clean(self, uid: int, show: str | None = None) -> dict:
        with self.lock:
            it = self.item(uid)
            if it is None or self.busy():
                raise RuntimeError("Can't change this file right now.")
            it.force = True
            if it.state == "skipped":
                it.state, it.message = "ready", ""
            self._compute_result(it)
            self.changed()
            return self.review(uid, show)

    def set_rules(self, uid: int, rules: dict, lyrics: str, show: str | None = None) -> dict:
        with self.lock:
            it = self.item(uid)
            if it is None or not it.loaded or self.busy():
                raise RuntimeError("Can't change this file right now.")
            o = self.opts_for(it).copy(**{k: bool(v) for k, v in rules.items() if k in RULE_KEYS})
            o = with_lyric_mode(o, lyrics or lyric_mode(o))
            it.opts = None if o == self.cfg.defaults else o
            self._compute_result(it)
            self.changed()
            return self.review(uid, show)

    def reset_rules(self, uid: int, show: str | None = None) -> dict:
        with self.lock:
            it = self.item(uid)
            if it is None:
                raise RuntimeError("That file isn't in the list any more.")
            it.opts = None
            self._compute_result(it)
            self.changed()
            return self.review(uid, show)

    def promote_rules(self, uid: int, show: str | None = None) -> dict:
        with self.lock:
            it = self.item(uid)
            if it is None or it.opts is None:
                raise RuntimeError("Nothing to save as the default.")
            self.cfg.defaults = it.opts
            it.opts = None
            self.cfg.save()
            for other in self.items:
                if other.opts is None and other.loaded:
                    self._compute_result(other)
            self.status_msg = ("Saved as your default edits for every file.", "good")
            self.changed()
            return self.review(uid, show)

    def toggle_line(self, uid: int, cue: int) -> dict:
        """Keep the original line, or use the cleaned one again. Returns just what changed: the row, its timeline
        mark, the legend and the header (the window updates that one row instead of rebuilding the list)."""
        with self.lock:
            it = self.item(uid)
            if it is None or it.result is None or self.busy():
                raise RuntimeError("Can't change this line right now.")
            r = it.result.results[cue]
            if r.changed:
                if it.overrides.get(cue) == "orig":
                    if r.risk >= 2:
                        it.overrides[cue] = "clean"          # an explicit "yes, clean it" resolves the flag
                    else:
                        it.overrides.pop(cue, None)
                else:
                    it.overrides[cue] = "orig"
                it.result.overrides = it.overrides
            self.changed()
            return {"row": self.cue_view(it, cue), "legend": self._legend(it), "head": self._head(it),
                    "rules": self._rules(it)}

    # ------------------------------------------------------------------ saving
    def save_question(self, only: int | None, answers: dict) -> dict:
        """What to ask before saving, one question at a time. The window shows it, adds the answer to `answers`
        and calls again; {"go": ...} means everything is answered. Nothing is changed on disk here."""
        a = answers or {}
        c = self.cfg
        with self.lock:
            if self.busy():
                return {"stop": True}
            scope = [i for i in self.items if i.pending and (only is None or i.uid == only)]
            if not scope:
                return {"stop": True}
            paths = [p for i in scope for p in self.files_touched(i)]
        found = media.who_has_open(paths) if paths else {}
        with self.lock:
            self._apply_locks(found)
            scope = [i for i in self.items if i.pending and (only is None or i.uid == only)]
            locked = [i for i in scope if i.locked]
            if locked:
                if a.get("locks") == "skip":
                    scope = [i for i in scope if not i.locked]
                    if not scope:
                        return {"stop": True}
                else:
                    progs: dict[str, int] = {}
                    for i in locked:
                        for names in i.locked.values():
                            for n in names:
                                progs[n] = progs.get(n, 0) + 1
                    who = " and ".join(sorted(progs, key=lambda n: -progs[n]))
                    one = len(locked) == 1
                    return {"ask": {
                        "key": "locks", "title": f"{plural(len(locked), 'file')} {'is' if one else 'are'} open in {who}",
                        "rows": [[i.name, ""] for i in locked[:8]], "more": max(0, len(locked) - 8),
                        "text": "SubScrub can't rewrite a file while another program has it open. If you're seeding "
                                "these, pause those torrents now, then click Check Again. Nothing has been changed "
                                "yet.",
                        "detail": "Once a file is rewritten its torrent can't seed it anymore (the contents no longer "
                                  "match): remove the torrent afterwards and keep the files. To keep seeding the "
                                  "original, use Settings → Videos → New File instead.",
                        "buttons": [{"key": "cancel", "label": "Cancel", "kind": ""},
                                    {"key": "skip", "label": f"Skip {'It' if one else 'These'}", "kind": ""},
                                    {"key": "again", "label": "Check Again", "kind": "primary"}]}}
            if self._renaming() and c.tmdb_key and any(
                    i.kind == "video" and i.name_state in ("", "looking") for i in scope):
                return {"tell": {"title": "Still looking up titles",
                                 "text": "SubScrub is still finding the titles for the new names. Try again in a "
                                         "moment."}}
            loaded = [i for i in scope if i.state == "ready" and i.result is not None]
            unseen = [i for i in scope if i.state in ("queued", "probing", "reading")]
            sdh_ok = lambda i: i.force or (i.track() is not None and batch.auto_cleaned(i, i.track()))  # noqa: E731
            flagged = [i for i in loaded if sdh_ok(i) and i.result.unresolved()]
            single = len(scope) == 1
            if single and loaded:
                it = loaded[0]
                if not sdh_ok(it) and not self._has_tidy(it):
                    if a.get("lone") != "go":
                        return {"ask": {
                            "key": "lone", "title": "Clean it anyway?",
                            "text": f"“{it.name}” only has regular subtitles: there are no sound descriptions or "
                                    "speaker names to strip.",
                            "detail": "Cleaning it anyway removes dialogue dashes and restyles song lyrics only.",
                            "buttons": [{"key": "cancel", "label": "Cancel", "kind": ""},
                                        {"key": "go", "label": "Clean Anyway", "kind": "primary"}]}}
                    it.force = True
                    self._compute_result(it)
                    flagged = [it] if it.result and it.result.unresolved() else []
                elif sdh_ok(it) and final_changed(it.result) == 0 and not it.track().external \
                        and not self._has_tidy(it):
                    return {"tell": {"title": "Nothing to remove",
                                     "text": "This track has nothing to clean with the current edits."}}
            policy = "hold_new"
            skip_uids = set()
            if flagged:
                choice = a.get("decision")
                if choice not in ("guess", "orig", "skip"):
                    n = sum(len(i.result.unresolved()) for i in flagged)
                    others = len([i for i in scope if i not in flagged])
                    options = [{"key": "review", "title": "Review Them Now",
                                "desc": "Opens the first one with just those lines listed.", "recommended": True},
                               {"key": "guess", "title": "Save With SubScrub's Best Guess",
                                "desc": "Applies every edit, including the uncertain ones."},
                               {"key": "orig", "title": "Save, but Leave Those Lines Alone",
                                "desc": "Uncertain lines stay exactly as they were."}]
                    if others:
                        options.append({"key": "skip", "title": f"Only Save the Other {plural(others, 'File')}",
                                        "desc": "Come back to these later."})
                    return {"ask": {
                        "key": "decision",
                        "title": f"{plural(len(flagged), 'file')} {'has' if len(flagged) == 1 else 'have'} "
                                 f"{plural(n, 'line')} SubScrub isn't sure about",
                        "rows": [[i.name, plural(len(i.result.unresolved()), 'line')] for i in flagged[:6]],
                        "more": max(0, len(flagged) - 6),
                        "text": "These are things like words in (parentheses) that might be dialogue, or text the "
                                "regular subtitles in the same file disagree with.",
                        "options": options,
                        "detail": f"{plural(len(unseen), 'file')} still being read will follow the same choice."
                                  if unseen else "",
                        "buttons": [{"key": "cancel", "label": "Cancel", "kind": ""}],
                        "review_uid": flagged[0].uid}}
                if choice == "skip":
                    skip_uids = {i.uid for i in flagged}
                else:
                    policy = choice
            jobs_items = [i for i in scope if i.uid not in skip_uids]
            if not jobs_items:
                return {"stop": True}
            unsure = [i for i in jobs_items if self._renaming() and batch.name_unsure(i)]
            if unsure:
                if a.get("names") == "keep":
                    for i in unsure:
                        i.name_pick = "keep"
                    self.changed()
                else:
                    return {"ask": {
                        "key": "names", "title": "Not sure of some titles",
                        "text": f"SubScrub couldn't tell which title {plural(len(unsure), 'file')} "
                                f"{'is' if len(unsure) == 1 else 'are'}:",
                        "rows": [[i.name, ""] for i in unsure[:6]], "more": max(0, len(unsure) - 6),
                        "uids": [i.uid for i in unsure],
                        "buttons": [{"key": "cancel", "label": "Cancel", "kind": ""},
                                    {"key": "keep", "label": "Keep Their Names", "kind": ""},
                                    {"key": "pick", "label": "Choose Titles…", "kind": "primary"}]}}
            renames = [i for i in jobs_items if self._new_name(i)]
            rewrites = [i for i in jobs_items if (i.kind == "video" and c.video_mode == "replace")
                        or (i.kind == "subtitle" and c.sub_mode == "embed" and i.info and i.info.companion)]
            if rewrites and c.confirm_replace and a.get("rewrite") != "go":
                n = len(rewrites)
                what = f"“{rewrites[0].name}”" if n == 1 else f"{n} files"
                return {"ask": {
                    "key": "rewrite", "title": "Rewrite in place?" if n == 1 else f"Rewrite {n} files in place?",
                    "text": f"SubScrub will rewrite {what} with the cleaned subtitles"
                            f"{' as the default track' if c.make_default else ''}"
                            f"{self._rewrite_extras(renames, jobs_items)}. "
                            "Each new file is checked before the old one goes to the Recycle Bin.",
                    "detail": "Very large files may be too big for the Recycle Bin, in which case Windows deletes "
                              "them instead.",
                    "checkbox": "Don't ask again",
                    "buttons": [{"key": "cancel", "label": "Cancel", "kind": ""},
                                {"key": "go", "label": "Rewrite" if n == 1 else f"Rewrite {n} Files",
                                 "kind": "primary"}]}}
            overwrite = True
            if c.video_mode == "sidecar":
                clashes = []
                for i in jobs_items:
                    tr = i.track()
                    if i.kind == "video" and i.info and tr:
                        p = media.output_path(i.info, media.SaveOptions(mode="sidecar", language=tr.language), tr.fmt)
                        if p.exists():
                            clashes.append(p.name)
                if clashes:
                    if a.get("overwrite") not in ("yes", "no"):
                        return {"ask": {
                            "key": "overwrite", "title": "Overwrite subtitle files?", "text": "These already exist:",
                            "detail": "Keep Both saves the new ones under new names.",
                            "rows": [[x, ""] for x in clashes[:8]], "more": max(0, len(clashes) - 8),
                            "buttons": [{"key": "cancel", "label": "Cancel", "kind": ""},
                                        {"key": "no", "label": "Keep Both", "kind": ""},
                                        {"key": "yes", "label": "Overwrite", "kind": "primary"}]}}
                    overwrite = a.get("overwrite") == "yes"
            return {"go": {"uids": [i.uid for i in jobs_items], "policy": policy, "overwrite": overwrite,
                           "single": single}}

    def _rewrite_extras(self, renames: list, items: list) -> str:
        bits = []
        if renames:
            bits.append(f"rename {plural(len(renames), 'file')}")
        drops = sum(1 for i in items if self._lang_drops(i))
        if drops:
            bits.append(f"remove other-language subtitles from {plural(drops, 'file')}")
        return (", and " + " and ".join(bits)) if bits else ""

    def save(self, only: int | None = None, answers: dict | None = None) -> dict:
        """Ask the next question, or start saving when everything is answered (see save_question)."""
        a = dict(answers or {})
        q = self.save_question(only, a)
        if "go" not in q:
            return q
        go = q["go"]
        with self.lock:
            if self.busy():
                return {"stop": True}
            if a.get("dont_ask"):
                self.cfg.confirm_replace = False
                self.cfg.save()
            self._saving = True                    # from here on nothing else starts reading files
            self._load_cancel.set()
            loader = self._loader
        if loader is not None:
            loader.join(timeout=10)
        try:
            return self._start_batch(go)
        except Exception:
            with self.lock:
                self._saving = False
                self.changed()
            raise

    def _start_batch(self, go: dict) -> dict:
        with self.lock:
            self._loader, self._loading_uid = None, None
            items = [self.item(u) for u in go["uids"]]
            items = [i for i in items if i is not None]
            jobs = []
            for i in items:
                if i.state in ("probing", "reading"):
                    if i.tmpdir:
                        shutil.rmtree(i.tmpdir, ignore_errors=True)
                    i.state, i.info, i.docs, i.tmpdir = "queued", None, {}, None
                i.message = ""
                jobs.append({"uid": i.uid, "path": i.path, "kind": i.kind, "info": i.info, "docs": i.docs,
                             "sdh_counts": i.sdh_counts, "sel": i.sel, "manual": i.manual, "opts": i.opts,
                             "overrides": dict(i.overrides),
                             "force": i.force or (go["single"] and i.kind == "subtitle"),
                             "sidecars": list(i.sidecars), "dropped": set(i.dropped), "policy": go["policy"],
                             "new_name": self._new_name(i)})
            c = self.cfg
            cfg = {"opts": c.defaults, "video_mode": c.video_mode, "sub_mode": c.sub_mode, "keep": c.keep_sdh,
                   "default": c.make_default, "name": c.track_name, "recycle": c.recycle_sub,
                   "overwrite": go["overwrite"], "pref": self.pref,
                   "drop_langs": c.drop_langs and c.video_mode == "replace", "keep_lang": "eng",
                   "batch_id": time.strftime("%Y%m%d-%H%M%S")}
            self.log(f"Saving {len(jobs)} file(s): video mode={c.video_mode}, subtitle mode={c.sub_mode}, "
                     f"keep SDH={cfg['keep']}, default={cfg['default']}, uncertain lines={go['policy']}")
            self._saving = True
            self._save_cancel = threading.Event()
            self.batch_total, self.batch_pos = len(jobs), 0
            self.batch_counts = {"done": 0, "skipped": 0, "failed": 0, "held": 0}
            self.batch_kept = 0
            self.save_status = ("Starting…", 0.0)
            self.status_msg = ("", "")
            self._saver = threading.Thread(target=self._run_batch, args=(jobs, cfg, self._save_cancel), daemon=True,
                                           name="save")
            self._saver.start()
            self.changed(review=True)
            return {"started": len(jobs)}

    def cancel(self):
        with self.lock:
            if self.busy():
                self._save_cancel.set()
                self.save_status = ("Stopping…", self.save_status[1])
                self.changed()

    def _overall(self, frac: float, text: str):
        n = max(1, self.batch_total)
        prefix = f"{self.batch_pos + 1} of {self.batch_total}:  " if self.batch_total > 1 else ""
        self.save_status = (prefix + text, (self.batch_pos + max(0.0, min(1.0, frac))) / n)
        self.changed()

    # --- the batch (ui/workers.BatchWorker without Qt)
    def _run_batch(self, jobs: list, cfg: dict, cancel: threading.Event):
        try:
            for k, job in enumerate(jobs):
                if cancel.is_set():
                    break
                uid = job["uid"]
                with self.lock:
                    self.batch_pos = k
                    it_ = self.item(uid)
                    if it_:
                        it_.state, it_.message, it_.progress = "saving", "Starting…", -1
                    self._overall(0.0, it_.name if it_ else "")

                def on_prog(f, m, u=uid):
                    with self.lock:
                        x = self.item(u)
                        if x is None:
                            return
                        pct = f" {int(f * 100)}%" if f >= 0 else ""
                        x.message, x.progress = f"{m}{pct}", f
                        self._overall(f if f >= 0 else 0.0, f"{x.name}, {m.lower()}{pct}")
                prog = throttled(on_prog)
                try:
                    self._save_one(job, cfg, cancel, prog)
                except media.Cancelled:
                    self._job_failed(uid, CANCELLED)
                    break
                except Exception as e:  # noqa: BLE001
                    self._job_failed(uid, str(e) or e.__class__.__name__)
        finally:
            self._all_done(cancel.is_set())

    def _save_one(self, job: dict, cfg: dict, cancel, prog):
        uid = job["uid"]
        it = Item(path=job["path"], kind=job["kind"])
        it.info, it.docs, it.sdh_counts = job["info"], job["docs"], job["sdh_counts"]
        it.sel, it.manual, it.opts = job["sel"], job["manual"], job["opts"]
        it.overrides = dict(job["overrides"])
        it.sidecars, it.dropped = list(job["sidecars"]), set(job["dropped"])
        fresh = it.info is None or (it.kind == "video" and not it.docs and
                                    (any(t.text_based for t in it.info.subs) or media.find_sidecars(it.path)))
        if fresh:
            self._batch_load(it, uid, prog, cancel)
        if not it.manual or it.sel not in it.docs:
            it.sel = batch.pick_track(it, cfg["pref"])
        track = it.track()
        tidy = self._tidy_opts(it, job, cfg)
        if track is None or it.sel not in it.docs:
            return self._tidy_or_skip(it, uid, tidy, "no text subtitles", prog, cfg, cancel)
        if not job["force"] and not batch.auto_cleaned(it, track):
            return self._tidy_or_skip(it, uid, tidy, "no SDH subtitles (regular subtitles only)", prog, cfg, cancel)
        res = batch.clean_item(it, cfg["opts"])
        policy = job["policy"]
        unresolved = res.unresolved()
        if unresolved:
            if policy == "hold" or (fresh and policy == "hold_new"):
                return self._job_held(uid, f"{len(unresolved)} lines to check")
            if policy == "orig":
                for i in unresolved:
                    res.overrides[i] = "orig"
        n = final_changed(res)
        if not any(res.final(i) is not None for i in range(len(res.doc.cues))):
            return self._tidy_or_skip(it, uid, tidy, "this subtitle track is empty" if not res.doc.cues else
                                      "every line would be removed, so the subtitles were left alone", prog, cfg,
                                      cancel)
        if n == 0 and not job["force"] and not track.external and not batch.plain_to_default(it, track):
            return self._tidy_or_skip(it, uid, tidy, "already clean", prog, cfg, cancel)
        mode = cfg["video_mode"] if it.kind == "video" else cfg["sub_mode"]
        note = ""
        if it.kind == "subtitle" and mode == "embed" and not it.info.companion:
            mode, note = "new", " (no matching video, so saved as a subtitle file)"
        keep = cfg["keep"] and not batch.plain_pick(it, track)
        opts = media.SaveOptions(mode=mode, keep_original=keep, make_default=cfg["default"],
                                 track_name=cfg["name"] or "", language=track.language,
                                 overwrite_sidecar=cfg["overwrite"], recycle_source_sub=cfg["recycle"],
                                 recycle_paths=batch.removable_sidecars(it, cfg["keep"], mode)
                                 if cfg["recycle"] and it.kind == "video" else [])
        if tidy is not None:
            opts.drop_other_languages, opts.keep_language = tidy.drop_other_languages, tidy.keep_language
            opts.rename_to, opts.rename_sidecars = tidy.rename_to, tidy.rename_sidecars
        out = media.save(self.tools, it.info, track, res.render(), res.doc.fmt, opts, prog, cancel, self.log)
        self._record(it, out.path, cfg)
        self._job_done(uid, {"path": out.path, "note": out.note + note, "changed": n, "sel": it.sel, "text": ""})

    def _tidy_opts(self, it: Item, job: dict, cfg: dict):
        if it.kind != "video" or cfg["video_mode"] != "replace":
            return None
        new = job.get("new_name") or ""
        drop = cfg.get("drop_langs", False) and bool(batch.other_languages(it, cfg.get("keep_lang", "eng")))
        if not new and not drop:
            return None
        side = [t.external for t in it.sidecars if t.external] or media.find_sidecars(it.path)
        return media.SaveOptions(mode="replace", drop_other_languages=drop, keep_language=cfg.get("keep_lang", "eng"),
                                 rename_to=new, rename_sidecars=side, make_default=cfg["default"],
                                 track_name=cfg["name"] or "")

    def _tidy_or_skip(self, it: Item, uid: int, tidy, why: str, prog, cfg, cancel):
        if tidy is None:
            with self.lock:
                self.batch_counts["skipped"] += 1
                x = self.item(uid)
                if x:
                    x.state, x.message = "skipped", why
                    self.log(f"Skipped {x.path}: {why}")
                self.changed()
            return
        out = media.tidy_or_rename(self.tools, it.info, tidy, prog, cancel, self.log)
        self._record(it, out.path, cfg)
        self._job_done(uid, {"path": out.path, "note": out.note, "changed": 0, "sel": it.sel, "text": "tidy"})

    def _record(self, it: Item, new_path: str, cfg: dict):
        if os.path.normcase(new_path) != os.path.normcase(it.path):
            naming.record_rename(cfg.get("batch_id", ""), it.path, new_path)

    def _batch_load(self, it: Item, uid: int, prog, cancel):
        payload = {}
        if it.kind == "subtitle":
            info, doc = media.probe_subtitle_file(it.path)
            it.info, it.docs = info, {0: doc}
        else:
            prog(-1, "Opening")
            it.info = media.probe(self.tools, it.path, self.log)
            tmp = tempfile.mkdtemp(prefix="subscrub_")
            paths = media.extract_subs(self.tools, it.info, it.info.subs, tmp,
                                       lambda f, m: prog(f, "Reading subtitles"), cancel, self.log)
            it.docs, payload["errors"] = load_docs(it.info, paths)
            payload["tmpdir"] = tmp
            batch.attach_sidecars(it, self.log)
        batch.measure(it)
        with self.lock:
            x = self.item(uid)
            if x is None:
                return
            x.info, x.docs, x.sidecars, x.sdh_counts = it.info, it.docs, it.sidecars, it.sdh_counts
            x.doc_errors = payload.get("errors", {})
            if payload.get("tmpdir"):
                x.tmpdir = payload["tmpdir"]
            if not x.manual or x.sel not in x.docs:
                x.sel = batch.pick_track(x, self.pref)
            self._compute_result(x)

    def _job_done(self, uid: int, payload: dict):
        with self.lock:
            self.batch_counts["done"] += 1
            self.batch_kept += payload["note"].count(media.KEPT)
            it = self.item(uid)
            if it is None:
                return
            it.state, it.output, it.message = "done", payload["path"], payload["note"]
            it.done_text = self._done_text(it, payload)
            it.sel, it.cleaned = payload["sel"], payload["changed"]
            it.progress = -1
            self.last_output = payload["path"]
            if it.kind == "video" and os.path.normcase(payload["path"]) != os.path.normcase(it.path) \
                    and self.cfg.video_mode == "replace":
                it.path = payload["path"]
            self.log(f"Saved {payload['path']}: {payload['changed']} lines cleaned. {payload['note']}")
            self.changed()

    def _done_text(self, it: Item, payload: dict) -> str:
        bits = []
        if payload.get("changed"):
            bits.append("Cleaned track saved into the video")
        if os.path.normcase(payload["path"]) != os.path.normcase(it.path):
            bits.append(f"renamed from {os.path.basename(it.path)}")
        if "Removed" in payload.get("note", "") and "other languages" in payload.get("note", ""):
            bits.append("other languages removed")
        return cap(", ".join(bits))

    def _job_held(self, uid: int, why: str):
        with self.lock:
            self.batch_counts["held"] += 1
            it = self.item(uid)
            if it:
                it.state, it.message = "ready", ""
                self.log(f"Held for review {it.path}: {why}")
            self.changed()

    def _job_failed(self, uid: int, msg: str):
        with self.lock:
            it = self.item(uid)
            if msg == CANCELLED:
                if it:
                    it.state, it.message = ("ready" if it.info else "queued"), ""
                self.changed()
                return
            self.batch_counts["failed"] += 1
            if it:
                it.state, it.message = "failed", msg
                self.log(f"Failed {it.path}: {msg}")
            self.changed()

    def _all_done(self, cancelled: bool):
        with self.lock:
            self._saving = False
            self._saver = None
            c = self.batch_counts
            parts = [f"{c['done']} saved"] if c["done"] else []
            if c["held"]:
                parts.append(f"{c['held']} waiting for you to check")
            if c["skipped"]:
                parts.append(f"{c['skipped']} skipped")
            if c["failed"]:
                parts.append(f"{c['failed']} failed")
            summary = ", ".join(parts) or "nothing saved"
            kept = (f" Kept {plural(self.batch_kept, 'old subtitle file')} the Recycle Bin couldn't take: hover a "
                    "file to see where.") if self.batch_kept else ""
            tell = None
            if cancelled:
                self.status_msg = (f"Stopped: {summary}.{kept}", "bad" if c["failed"] else "")
            elif c["failed"]:
                self.status_msg = (f"Finished with problems: {summary}. Hover a file to see why.{kept}", "bad")
            else:
                single = self.batch_total == 1 and c["done"] == 1
                it = next((i for i in self.items if i.state == "done" and i.output == self.last_output), None) \
                    if single else None
                self.status_msg = (f"Saved “{os.path.basename(it.output)}”. {it.message}".strip() if it
                                   else f"Done: {summary}.{kept}", "good" if c["done"] else "")
            if c["failed"] and self.batch_total == 1:
                it = next((i for i in self.items if i.state == "failed"), None)
                if it:
                    tell = {"title": "Couldn't save", "text": it.message[:1200], "log": True}
            self._pump_loader()
            self._pump_names()
            self.changed(review=True)
            done = {"cancelled": cancelled, "counts": dict(c), "status": self.status_msg[0], "tell": tell,
                    "outputs": [i.output for i in self.items if i.state == "done" and i.output]}
        self.emit("save_done", done)

    def open_folder(self, path: str | None = None):
        p = path or self.last_output
        if p:
            media.open_in_explorer(p)
