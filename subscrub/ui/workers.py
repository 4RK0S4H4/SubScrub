"""Work that runs off the UI thread: reading files, the batch save, the "files in use" check. Plus small helpers
the window and dialogs share."""
from __future__ import annotations

import os
import tempfile
import threading
import time

from PySide6.QtCore import QObject, QThread, Signal

from .. import batch, media, naming, subformats as sf
from ..batch import Item

CANCELLED = "__cancelled__"


class Bus(QObject):
    log = Signal(str)


def throttled(emit, interval: float = 0.08):
    """Wrap a progress callback so it fires at most every `interval` seconds (always on a new message, and at the
    end). ffmpeg reports progress many times a second; the UI only needs a dozen updates."""
    last = {"t": 0.0, "m": None}

    def prog(frac, msg):
        now = time.monotonic()
        if msg != last["m"] or frac >= 1.0 or frac < 0 or now - last["t"] >= interval:
            last["t"], last["m"] = now, msg
            emit(frac, msg)
    return prog


class Worker(QThread):
    progress = Signal(float, str)
    ok = Signal(object)
    err = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn
        self.cancel = threading.Event()

    def run(self):
        try:
            result = self.fn(throttled(lambda f, m: self.progress.emit(float(f), m)), self.cancel)
        except media.Cancelled:
            self.err.emit(CANCELLED)
            return
        except Exception as e:  # noqa: BLE001 – surfaced to the user
            self.err.emit(str(e) or e.__class__.__name__)
            return
        self.ok.emit(result)


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
    """How many lines the save will change. Cached on the result until the user's decisions change (the file list,
    the summary and the review header all ask, many times per click)."""
    key = tuple(sorted(res.overrides.items()))
    cached = getattr(res, "_final_changed", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    cues = res.doc.cues
    n = sum(1 for i, c in enumerate(cues) if res.final(i) != c.text)
    res._final_changed = (key, n)
    return n


class BatchWorker(QThread):
    """Cleans and saves every queued file in turn, loading any that haven't been read yet."""
    started_item = Signal(int, int)            # uid, position
    item_loaded = Signal(int, object)          # uid, dict
    item_progress = Signal(int, float, str)
    item_done = Signal(int, object)
    item_skipped = Signal(int, str)
    item_held = Signal(int, str)               # loaded during the save and needs a look first
    item_failed = Signal(int, str)
    all_done = Signal(bool)                    # cancelled?

    def __init__(self, jobs: list[dict], cfg: dict, tools: media.Tools, log, parent=None):
        super().__init__(parent)
        self.jobs, self.cfg, self.tools, self.log = jobs, cfg, tools, log
        self.cancel = threading.Event()

    def run(self):
        cfg = self.cfg
        for k, job in enumerate(self.jobs):
            if self.cancel.is_set():
                break
            uid = job["uid"]
            self.started_item.emit(uid, k)
            prog = throttled(lambda f, m, u=uid: self.item_progress.emit(u, float(f), m))
            try:
                it = Item(path=job["path"], kind=job["kind"])
                it.info, it.docs, it.sdh_counts = job["info"], job["docs"], job["sdh_counts"]
                it.sel, it.manual, it.opts = job["sel"], job["manual"], job["opts"]
                it.overrides = dict(job["overrides"])
                it.sidecars, it.dropped = list(job["sidecars"]), set(job["dropped"])
                fresh = it.info is None or (it.kind == "video" and not it.docs and
                                            (any(t.text_based for t in it.info.subs) or media.find_sidecars(it.path)))
                if fresh:
                    self._load(it, uid, prog)
                if not it.manual or it.sel not in it.docs:
                    it.sel = batch.pick_track(it, cfg["pref"])
                track = it.track()
                tidy = self._tidy_opts(it, job)
                if track is None or it.sel not in it.docs:
                    self._tidy_or_skip(it, uid, tidy, "no text subtitles", prog)
                    continue
                if not job["force"] and not track.external and not batch.is_sdh(it, track):
                    self._tidy_or_skip(it, uid, tidy, "no SDH subtitles (regular subtitles only)", prog)
                    continue
                res = batch.clean_item(it, cfg["opts"])
                policy = job["policy"]
                unresolved = res.unresolved()
                if unresolved:
                    if policy == "hold" or (fresh and policy == "hold_new"):
                        self.item_held.emit(uid, f"{len(unresolved)} lines to check")
                        continue
                    if policy == "orig":
                        for i in unresolved:
                            res.overrides[i] = "orig"
                n = final_changed(res)
                if n == 0 and not job["force"] and not track.external:
                    self._tidy_or_skip(it, uid, tidy, "already clean", prog)
                    continue
                mode = cfg["video_mode"] if it.kind == "video" else cfg["sub_mode"]
                note = ""
                if it.kind == "subtitle" and mode == "embed" and not it.info.companion:
                    mode, note = "new", " (no matching video, so saved as a subtitle file)"
                opts = media.SaveOptions(mode=mode, keep_original=cfg["keep"], make_default=cfg["default"],
                                         track_name=cfg["name"] or "", language=track.language,
                                         overwrite_sidecar=cfg["overwrite"], recycle_source_sub=cfg["recycle"],
                                         recycle_paths=batch.removable_sidecars(it, cfg["keep"], mode)
                                         if cfg["recycle"] and it.kind == "video" else [])
                if tidy is not None:
                    opts.drop_other_languages, opts.keep_language = tidy.drop_other_languages, tidy.keep_language
                    opts.rename_to, opts.rename_sidecars = tidy.rename_to, tidy.rename_sidecars
                out = media.save(self.tools, it.info, track, res.render(), res.doc.fmt, opts, prog, self.cancel,
                                 self.log)
                self._record(it, out.path)
                self.item_done.emit(uid, {"path": out.path, "note": out.note + note, "changed": n, "sel": it.sel,
                                          "text": ""})
            except media.Cancelled:
                self.item_failed.emit(uid, CANCELLED)
                break
            except Exception as e:  # noqa: BLE001
                self.item_failed.emit(uid, str(e) or e.__class__.__name__)
        self.all_done.emit(self.cancel.is_set())

    # --- renaming and track tidy-up (only when an original is being replaced)
    def _tidy_opts(self, it: Item, job: dict):
        cfg = self.cfg
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

    def _tidy_or_skip(self, it: Item, uid: int, tidy, why: str, prog):
        if tidy is None:
            self.item_skipped.emit(uid, why)
            return
        out = media.tidy_or_rename(self.tools, it.info, tidy, prog, self.cancel, self.log)
        self._record(it, out.path)
        self.item_done.emit(uid, {"path": out.path, "note": out.note, "changed": 0, "sel": it.sel, "text": "tidy"})

    def _record(self, it: Item, new_path: str):
        if os.path.normcase(new_path) != os.path.normcase(it.path):
            naming.record_rename(self.cfg.get("batch_id", ""), it.path, new_path)

    def _load(self, it: Item, uid: int, prog):
        payload = {}
        if it.kind == "subtitle":
            info, doc = media.probe_subtitle_file(it.path)
            it.info, it.docs = info, {0: doc}
        else:
            prog(-1, "Opening")
            it.info = media.probe(self.tools, it.path, self.log)
            tmp = tempfile.mkdtemp(prefix="subscrub_")
            paths = media.extract_subs(self.tools, it.info, it.info.subs, tmp,
                                       lambda f, m: prog(f, "Reading subtitles"), self.cancel, self.log)
            it.docs, payload["errors"] = load_docs(it.info, paths)
            payload["tmpdir"] = tmp
            batch.attach_sidecars(it, self.log)
        batch.measure(it)
        payload.update(info=it.info, docs=it.docs, sdh_counts=it.sdh_counts, sidecars=it.sidecars)
        self.item_loaded.emit(uid, payload)


class LockCheck(QThread):
    done = Signal(object)          # {path: [programs]}

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = paths

    def run(self):
        try:
            self.done.emit(media.who_has_open(self.paths))
        except Exception:  # noqa: BLE001
            self.done.emit({})


# ============================================================================ formatting

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


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n:,} {word if n == 1 else (many or word + 's')}"
