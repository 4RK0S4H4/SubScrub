"""Everything that touches video files: finding FFmpeg/MKVToolNix, reading tracks,
pulling subtitles out, and putting the cleaned track back in.

No re-encoding ever happens: audio and video are copied as-is, so a remux takes about as
long as copying the file.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

IS_WIN = os.name == "nt"
Progress = Callable[[float, str], None]      # (0..1 or -1 for indeterminate, message)
Log = Callable[[str], None]


class ToolError(Exception):
    pass


class Cancelled(Exception):
    pass


# ============================================================================ tools

@dataclass
class Tools:
    ffmpeg: str | None = None
    ffprobe: str | None = None
    mkvmerge: str | None = None

    @property
    def ready(self) -> bool:
        return bool(self.ffmpeg and self.ffprobe)


def _exe(name: str) -> str:
    return name + ".exe" if IS_WIN else name


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _registry_path_dirs() -> list[str]:
    """PATH as it is *now* in the registry (winget installs update it, but a running app
    only sees the PATH it was started with)."""
    if not IS_WIN:
        return []
    import winreg
    dirs: list[str] = []
    for root, key in ((winreg.HKEY_CURRENT_USER, r"Environment"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(root, key) as k:
                val, _ = winreg.QueryValueEx(k, "Path")
                dirs += [os.path.expandvars(p) for p in str(val).split(";") if p.strip()]
        except OSError:
            pass
    return dirs


def _candidate_dirs(name: str) -> list[str]:
    dirs = [str(app_dir() / "tools"), str(app_dir())]
    dirs += os.environ.get("PATH", "").split(os.pathsep)
    dirs += _registry_path_dirs()
    if IS_WIN:
        la = os.environ.get("LOCALAPPDATA", "")
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        home = os.path.expanduser("~")
        dirs += [
            os.path.join(la, "Microsoft", "WinGet", "Links"),
            os.path.join(pf, "MKVToolNix"), os.path.join(pf86, "MKVToolNix"),
            os.path.join(la, "Programs", "MKVToolNix"),
            r"C:\ffmpeg\bin", os.path.join(pf, "ffmpeg", "bin"),
            os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "chocolatey", "bin"),
            os.path.join(home, "scoop", "shims"),
        ]
        # winget "portable" packages (Gyan.FFmpeg, BtbN.FFmpeg...) unpack here
        dirs += glob.glob(os.path.join(la, "Microsoft", "WinGet", "Packages", "*FFmpeg*", "*", "bin"))
        dirs += glob.glob(os.path.join(la, "Microsoft", "WinGet", "Packages", "*FFmpeg*", "bin"))
    seen, out = set(), []
    for d in dirs:
        key = os.path.normcase(os.path.abspath(d)) if d else ""
        if d and key not in seen:
            seen.add(key)
            out.append(d)
    return out


def find_tool(name: str, override: str | None = None, near: str | None = None) -> str | None:
    if override and os.path.isfile(override):
        return override
    if near:
        p = os.path.join(os.path.dirname(near), _exe(name))
        if os.path.isfile(p):
            return p
    for d in _candidate_dirs(name):
        p = os.path.join(d, _exe(name))
        if os.path.isfile(p):
            return p
    return shutil.which(name)


def find_tools(overrides: dict | None = None) -> Tools:
    o = overrides or {}
    ffmpeg = find_tool("ffmpeg", o.get("ffmpeg"))
    ffprobe = find_tool("ffprobe", o.get("ffprobe"), near=ffmpeg)
    mkvmerge = find_tool("mkvmerge", o.get("mkvmerge"))
    return Tools(ffmpeg, ffprobe, mkvmerge)


def tool_version(path: str | None) -> str:
    if not path:
        return ""
    try:
        out = subprocess.run([path, "-version" if "ff" in os.path.basename(path).lower() else "--version"],
                             **_popen_kwargs(capture=True), timeout=10).stdout
        first = out.splitlines()[0] if out else ""
        m = re.search(r"version (\S+)|v(\d+\.\d+(?:\.\d+)?)", first)
        return (m.group(1) or m.group(2)) if m else first[:40]
    except Exception:
        return ""


# ============================================================================ processes

def _popen_kwargs(capture: bool = False, merge: bool = False) -> dict:
    kw: dict = dict(stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
    if capture:
        kw.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if merge:
        kw.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if IS_WIN:
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kw


def _fmt_cmd(cmd: list[str]) -> str:
    return " ".join(f'"{c}"' if (" " in c or not c) else c for c in cmd)


def run_capture(cmd: list[str], log: Log | None = None, timeout: float | None = None) -> str:
    if log:
        log("$ " + _fmt_cmd(cmd))
    p = subprocess.run(cmd, **_popen_kwargs(capture=True), timeout=timeout)
    if p.returncode != 0:
        msg = (p.stderr or p.stdout or "").strip()
        raise ToolError(f"{os.path.basename(cmd[0])} failed (exit {p.returncode}): {msg[-800:]}")
    return p.stdout


def run_stream(cmd: list[str], on_line: Callable[[str], None], log: Log | None = None,
               cancel: threading.Event | None = None, ok_codes=(0,)) -> list[str]:
    """Run a tool, feeding each output line to on_line. Kills it if `cancel` gets set."""
    if log:
        log("$ " + _fmt_cmd(cmd))
    proc = subprocess.Popen(cmd, **_popen_kwargs(merge=True), bufsize=1)
    if cancel is not None:
        def watch():
            while proc.poll() is None:
                if cancel.wait(0.2):
                    proc.kill()
                    return
        threading.Thread(target=watch, daemon=True).start()
    tail: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\r\n")
        on_line(line)
        if line and not line.startswith(("out_time", "frame=", "fps=", "stream_", "bitrate=", "total_size=",
                                          "dup_frames", "drop_frames", "speed=", "progress=", "#GUI#progress")):
            tail.append(line)
            tail = tail[-60:]
    proc.wait()
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    if proc.returncode not in ok_codes:
        detail = "\n".join(tail[-15:]).strip()
        raise ToolError(f"{os.path.basename(cmd[0])} failed (exit {proc.returncode}).\n{detail}")
    if log and tail:
        log("\n".join(tail[-15:]))
    return tail


# ============================================================================ probing

TEXT_CODECS = {
    # codec -> (extracted format, ffmpeg codec for extraction)
    "subrip": ("srt", "copy"), "srt": ("srt", "srt"),
    "ass": ("ass", "copy"), "ssa": ("ass", "ass"),
    "webvtt": ("vtt", "copy"),
    "mov_text": ("srt", "srt"), "text": ("srt", "srt"),
    "microdvd": ("srt", "srt"), "subviewer": ("srt", "srt"), "subviewer1": ("srt", "srt"),
    "realtext": ("srt", "srt"), "sami": ("srt", "srt"), "stl": ("srt", "srt"),
    "mpl2": ("srt", "srt"), "pjs": ("srt", "srt"), "vplayer": ("srt", "srt"), "jacosub": ("srt", "srt"),
}
CODEC_LABELS = {
    "subrip": "SRT", "srt": "SRT", "ass": "ASS", "ssa": "SSA", "webvtt": "WebVTT", "mov_text": "MP4 text",
    "hdmv_pgs_subtitle": "PGS", "dvd_subtitle": "VobSub", "dvb_subtitle": "DVB", "xsub": "XSUB",
    "dvb_teletext": "Teletext", "arib_caption": "ARIB",
}

LANGS = {  # ISO 639-2 -> (name, ISO 639-1)
    "eng": ("English", "en"), "spa": ("Spanish", "es"), "fre": ("French", "fr"), "fra": ("French", "fr"),
    "ger": ("German", "de"), "deu": ("German", "de"), "ita": ("Italian", "it"), "por": ("Portuguese", "pt"),
    "jpn": ("Japanese", "ja"), "kor": ("Korean", "ko"), "chi": ("Chinese", "zh"), "zho": ("Chinese", "zh"),
    "rus": ("Russian", "ru"), "ara": ("Arabic", "ar"), "hin": ("Hindi", "hi"), "dut": ("Dutch", "nl"),
    "nld": ("Dutch", "nl"), "swe": ("Swedish", "sv"), "nor": ("Norwegian", "no"), "nob": ("Norwegian", "nb"),
    "dan": ("Danish", "da"), "fin": ("Finnish", "fi"), "pol": ("Polish", "pl"), "tur": ("Turkish", "tr"),
    "gre": ("Greek", "el"), "ell": ("Greek", "el"), "heb": ("Hebrew", "he"), "tha": ("Thai", "th"),
    "vie": ("Vietnamese", "vi"), "ind": ("Indonesian", "id"), "may": ("Malay", "ms"), "msa": ("Malay", "ms"),
    "ukr": ("Ukrainian", "uk"), "cze": ("Czech", "cs"), "ces": ("Czech", "cs"), "hun": ("Hungarian", "hu"),
    "rum": ("Romanian", "ro"), "ron": ("Romanian", "ro"), "bul": ("Bulgarian", "bg"), "hrv": ("Croatian", "hr"),
    "srp": ("Serbian", "sr"), "slv": ("Slovenian", "sl"), "slo": ("Slovak", "sk"), "slk": ("Slovak", "sk"),
    "ben": ("Bengali", "bn"), "tam": ("Tamil", "ta"), "tel": ("Telugu", "te"), "fil": ("Filipino", "fil"),
    "tgl": ("Tagalog", "tl"), "per": ("Persian", "fa"), "fas": ("Persian", "fa"), "cat": ("Catalan", "ca"),
    "ice": ("Icelandic", "is"), "isl": ("Icelandic", "is"), "est": ("Estonian", "et"), "lav": ("Latvian", "lv"),
    "lit": ("Lithuanian", "lt"), "und": ("Unknown language", ""),
}
SDH_NAME = re.compile(r"\b(sdh|hi|cc|hoh|hearing[ -]?impaired|closed captions?|deaf)\b", re.I)


def lang_name(code: str) -> str:
    code = (code or "und").lower()
    if code in LANGS:
        return LANGS[code][0]
    return code.upper() if code else "Unknown language"


def lang_short(code: str) -> str:
    code = (code or "").lower().replace("_", "-").split("-")[0]
    return LANGS.get(code, (None, code))[1] if code != "und" else ""


@dataclass
class SubTrack:
    index: int                # absolute stream index in the file
    sub_pos: int              # position among subtitle tracks
    codec: str
    language: str
    title: str
    default: bool
    forced: bool
    hearing_impaired: bool
    disposition: dict = field(default_factory=dict)
    external: str = ""        # path of a separate subtitle file next to the video ("" = inside the video)
    cleaned: bool = False     # a track SubScrub wrote on an earlier run (tagged SUBSCRUB=cleaned)

    @property
    def from_subscrub(self) -> bool:
        return self.cleaned or (self.title or "").lower().endswith("(dialogue only)")

    @property
    def text_based(self) -> bool:
        return self.codec in TEXT_CODECS

    @property
    def fmt(self) -> str:
        return TEXT_CODECS.get(self.codec, ("srt", "srt"))[0]

    @property
    def codec_label(self) -> str:
        return CODEC_LABELS.get(self.codec, self.codec.upper() or "Unknown")

    @property
    def lang_label(self) -> str:
        return lang_name(self.language)

    @property
    def sdh_flagged(self) -> bool:
        return self.hearing_impaired or bool(SDH_NAME.search(self.title or ""))

    def dispo_flags(self, drop_default: bool) -> str:
        flags = [k for k, v in (self.disposition or {}).items() if v and not (drop_default and k == "default")]
        return "+".join(flags) if flags else "0"


@dataclass
class MediaInfo:
    path: str
    kind: str                 # mkv | mp4 | webm | other
    format_name: str
    duration: float
    size: int
    streams: list[dict]
    subs: list[SubTrack]
    companion: str | None = None      # standalone subtitle file: the video it belongs to
    chapters: int = 0

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    def count(self, codec_type: str) -> int:
        return sum(1 for s in self.streams if s.get("codec_type") == codec_type)


VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".mov", ".webm", ".avi", ".ts", ".m2ts", ".mts", ".wmv", ".ogm", ".mk3d",
              ".flv", ".mpg", ".mpeg", ".vob", ".3gp"}
TEXT_SUB_EXTS = {".srt", ".ass", ".ssa", ".vtt"}
IMAGE_SUB_EXTS = {".sup", ".sub", ".idx"}
SDH_TOKENS = {"sdh", "hi", "cc", "hoh"}
_SHORT_TO_3 = {}
for _code, (_n, _short) in LANGS.items():
    if _short and len(_short) == 2 and _code != "und" and _short not in SDH_TOKENS:     # ".hi." means hearing-impaired
        _SHORT_TO_3.setdefault(_short, _code)


def container_kind(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in (".mkv", ".mk3d", ".mka"):
        return "mkv"
    if ext in (".mp4", ".m4v", ".mov"):
        return "mp4"
    if ext == ".webm":
        return "webm"
    if ext in TEXT_SUB_EXTS:
        return "subtitle"
    return "other"


# ============================================================================ standalone subtitle files

def _name_tokens(path: str | Path) -> list[str]:
    return Path(path).stem.split(".")


def guess_language(path: str | Path) -> str:
    """'Movie (2020).en.sdh.srt' -> 'eng'"""
    for tok in reversed(_name_tokens(path)[1:]):
        t = tok.lower().split("-")[0].split("_")[0]
        if t in LANGS and t != "und":
            return t
        if t in _SHORT_TO_3:
            return _SHORT_TO_3[t]
    return "und"


def find_matching_video(sub_path: str | Path) -> str | None:
    """The video a subtitle file belongs to: same name, or the subtitle name plus .lang/.sdh/... tags."""
    p = Path(sub_path)
    stem = p.stem.lower()
    best: Path | None = None
    try:
        entries = list(p.parent.iterdir())
    except OSError:
        return None
    for f in entries:
        if f.suffix.lower() not in VIDEO_EXTS or ".subscrub-tmp" in f.name or not f.is_file():
            continue
        vs = f.stem.lower()
        if stem == vs or stem.startswith(vs + "."):
            if best is None or len(f.stem) > len(best.stem):
                best = f
    return str(best) if best else None


SIDECAR_BASE = 100000      # track index given to subtitle files that sit next to a video


def find_sidecars(video_path: str | Path) -> list[str]:
    """Subtitle files next to a video that belong to it ('Movie.eng.srt', 'Movie.en.sdh.srt', 'Movie.srt')."""
    v = Path(video_path)
    out = []
    try:
        entries = sorted(v.parent.iterdir())
    except OSError:
        return out
    for f in entries:
        if f.suffix.lower() not in TEXT_SUB_EXTS or not f.is_file():
            continue
        if not f.stem.lower().startswith(v.stem.lower()):
            continue
        match = find_matching_video(f)
        if match and os.path.normcase(os.path.abspath(match)) == os.path.normcase(os.path.abspath(str(v))):
            out.append(str(f))
    return out


def sidecar_track(path: str, k: int) -> tuple[SubTrack, object]:
    """A subtitle file next to a video, presented as one more track of that video."""
    from . import subformats
    doc = subformats.load(path)
    toks = {t.lower() for t in _name_tokens(path)[1:]}
    codec = {"srt": "subrip", "ass": "ass", "vtt": "webvtt"}[doc.fmt]
    tr = SubTrack(index=SIDECAR_BASE + k, sub_pos=1000 + k, codec=codec, language=guess_language(path),
                  title=Path(path).name, default=False, forced="forced" in toks,
                  hearing_impaired=bool(toks & SDH_TOKENS), external=path)
    return tr, doc


def clean_subtitle_name(sub_path: str | Path, fmt: str) -> Path:
    """'Movie.en.sdh.srt' -> 'Movie.en.srt' (drops SDH/HI/CC tags). Same name if there were none."""
    p = Path(sub_path)
    toks = _name_tokens(p)
    kept = toks[:1] + [t for t in toks[1:] if t.lower() not in SDH_TOKENS]
    return p.with_name(".".join(kept) + "." + fmt)


def probe_subtitle_file(path: str) -> tuple[MediaInfo, object]:
    from . import subformats
    doc = subformats.load(path)
    toks = {t.lower() for t in _name_tokens(path)[1:]}
    codec = {"srt": "subrip", "ass": "ass", "vtt": "webvtt"}[doc.fmt]
    duration = max((subformats.to_seconds(c.end) for c in doc.cues), default=0.0)
    track = SubTrack(index=0, sub_pos=0, codec=codec, language=guess_language(path), title=Path(path).name,
                     default=False, forced="forced" in toks, hearing_impaired=bool(toks & SDH_TOKENS))
    info = MediaInfo(path, "subtitle", doc.fmt, duration, os.path.getsize(path), [], [track],
                     companion=find_matching_video(path))
    return info, doc


def is_network_path(path: str | Path) -> bool:
    p = os.path.abspath(str(path))
    if not IS_WIN:
        return False
    if p.startswith("\\\\"):
        return True
    try:
        import ctypes
        return ctypes.windll.kernel32.GetDriveTypeW(os.path.splitdrive(p)[0] + "\\") == 4   # DRIVE_REMOTE
    except Exception:
        return False


def recycle(path: str | Path, log: Log | None = None) -> str:
    """Send a file to the Recycle Bin (or delete it where there is none). Returns what happened."""
    path = Path(path)
    if is_network_path(path):
        os.remove(path)
        return "deleted (network drives have no Recycle Bin)"
    try:
        from send2trash import send2trash
        send2trash(str(path))
        return "moved to the Recycle Bin"
    except Exception as e:
        if log:
            log(f"Recycle Bin unavailable for {path.name}: {e}")
        os.remove(path)
        return "deleted (Recycle Bin unavailable)"


# ============================================================================ leftovers of an interrupted save

TMP_MARK = ".subscrub-tmp"
STALE_TEMP_PREFIXES = ("subscrub_mux_", "subscrub_verify_", "subscrub_")


@dataclass
class Leftover:
    path: str                 # the half-written "Name.subscrub-tmp.mkv"
    original: str | None      # the video it was made from, if that is still there
    size: int = 0

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


def _leftover(path: str) -> Leftover | None:
    p = Path(path)
    stem, mark, _ = p.name.partition(TMP_MARK)
    if not mark or p.suffix.lower() not in VIDEO_EXTS or not stem:
        return None
    try:
        size = p.stat().st_size
    except OSError:
        return None
    orig = next((str(p.with_name(stem + e)) for e in sorted({p.suffix, *VIDEO_EXTS})
                 if (p.with_name(stem + e)).is_file()), None)
    return Leftover(str(p), orig, size)


def find_leftovers(paths: list[str], limit: int = 2000) -> list[Leftover]:
    """Unfinished "Name.subscrub-tmp.ext" files in the folders given, or next to the files given."""
    seen: dict[str, Leftover] = {}

    def look(folder: str, only_stems: set | None = None):
        try:
            names = os.listdir(folder)
        except OSError:
            return
        for n in names:
            if TMP_MARK in n and (only_stems is None or n.partition(TMP_MARK)[0].lower() in only_stems):
                lo = _leftover(os.path.join(folder, n))
                if lo is not None:
                    seen[os.path.normcase(os.path.abspath(lo.path))] = lo

    for p in paths:
        if os.path.isdir(p):
            count = 0
            for root, dirs, _files in os.walk(p):
                dirs.sort()
                look(root)
                count += 1
                if count >= limit:
                    break
        elif os.path.isfile(p):
            if TMP_MARK in os.path.basename(p):
                lo = _leftover(p)
                if lo is not None:
                    seen[os.path.normcase(os.path.abspath(lo.path))] = lo
            else:
                look(os.path.dirname(os.path.abspath(p)), {Path(p).stem.lower()})
    return sorted(seen.values(), key=lambda x: x.path.lower())


def restore_leftover(lo: Leftover) -> str:
    """The original is gone, so the half-written copy may be all that is left: give it its real name back.
    Never overwrites anything."""
    p = Path(lo.path)
    target = p.with_name(p.name.partition(TMP_MARK)[0] + p.suffix)
    if target.exists():
        raise ToolError(f"“{target.name}” already exists, so “{p.name}” was left as it is.")
    os.rename(p, target)
    return str(target)


def remove_stale_temp_dirs(max_age: float = 86400.0, root: str | None = None) -> int:
    """Delete SubScrub's own working folders in %TEMP% that a crash left behind (older than a day, so a second
    running copy is never touched)."""
    root = root or tempfile.gettempdir()
    removed = 0
    cutoff = time.time() - max_age
    try:
        names = os.listdir(root)
    except OSError:
        return 0
    for n in names:
        full = os.path.join(root, n)
        if n.startswith(STALE_TEMP_PREFIXES) and os.path.isdir(full) and not os.path.islink(full):
            try:
                if os.path.getmtime(full) < cutoff:
                    shutil.rmtree(full, ignore_errors=True)
                    removed += 0 if os.path.exists(full) else 1
            except OSError:
                continue
    return removed


def probe(tools: Tools, path: str, log: Log | None = None) -> MediaInfo:
    if not tools.ffprobe:
        raise ToolError("FFprobe was not found. Install FFmpeg first.")
    out = run_capture([tools.ffprobe, "-v", "error", "-print_format", "json",
                       "-show_format", "-show_streams", "-show_chapters", path], log, timeout=120)
    data = json.loads(out or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    subs: list[SubTrack] = []
    for s in streams:
        if s.get("codec_type") != "subtitle":
            continue
        tags = {k.lower(): v for k, v in (s.get("tags") or {}).items()}
        disp = {k: int(v) for k, v in (s.get("disposition") or {}).items() if isinstance(v, int)}
        title = tags.get("title") or ""
        if not title:
            h = tags.get("handler_name") or ""
            if h and not re.fullmatch(r"(Subtitle|Text|Sub)Handler|.*Media Handler|", h, re.I):
                title = h
        subs.append(SubTrack(
            index=int(s.get("index", 0)), sub_pos=len(subs), codec=s.get("codec_name") or "",
            language=(tags.get("language") or "und").lower(), title=title,
            default=bool(disp.get("default")), forced=bool(disp.get("forced")),
            hearing_impaired=bool(disp.get("hearing_impaired")), disposition=disp,
            cleaned=(tags.get("subscrub") or "").lower() == "cleaned",
        ))
    try:
        duration = float(fmt.get("duration") or 0)
    except ValueError:
        duration = 0.0
    size = int(fmt.get("size") or 0) or os.path.getsize(path)
    return MediaInfo(path, container_kind(path), fmt.get("format_name", ""), duration, size, streams, subs,
                     chapters=len(data.get("chapters") or []))


# ============================================================================ extraction

def _ffmpeg_progress(duration: float, on_progress: Progress | None, label: str):
    def on_line(line: str):
        if not on_progress:
            return
        if line.startswith(("out_time_us=", "out_time_ms=")):
            try:
                us = int(line.split("=", 1)[1])
            except ValueError:
                return
            if duration > 0 and us > 0:
                on_progress(min(0.999, us / 1e6 / duration), label)
    return on_line


def extract_subs(tools: Tools, info: MediaInfo, tracks: list[SubTrack], out_dir: str,
                 on_progress: Progress | None = None, cancel: threading.Event | None = None,
                 log: Log | None = None) -> dict[int, str]:
    """Pull all given text tracks out in a single read of the file. Returns {stream index: path}."""
    tracks = [t for t in tracks if t.text_based]
    if not tracks:
        return {}
    os.makedirs(out_dir, exist_ok=True)
    paths = {t.index: os.path.join(out_dir, f"track{t.index}.{t.fmt}") for t in tracks}
    cmd = [tools.ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
           "-progress", "pipe:1", "-nostats", "-i", info.path]
    for t in tracks:
        cmd += ["-map", f"0:{t.index}", "-c:s", TEXT_CODECS[t.codec][1], paths[t.index]]
    try:
        run_stream(cmd, _ffmpeg_progress(info.duration, on_progress, "Reading subtitles"), log, cancel)
    except ToolError:
        if len(tracks) == 1:
            raise
        # one bad track shouldn't sink the rest: retry them one by one
        if log:
            log("Combined extraction failed; retrying tracks one at a time.")
        good: dict[int, str] = {}
        for t in tracks:
            try:
                good.update(extract_subs(tools, info, [t], out_dir, on_progress, cancel, log))
            except ToolError as e:
                if log:
                    log(f"Track {t.index}: {e}")
        return good
    return {i: p for i, p in paths.items() if os.path.exists(p)}


# ============================================================================ saving

@dataclass
class SaveOptions:
    # video sources:    replace | new | sidecar
    # subtitle sources: embed   | replace | new
    mode: str = "replace"
    keep_original: bool = False       # keep the SDH track next to the cleaned one
    make_default: bool = True
    track_name: str = ""
    language: str = ""
    overwrite_sidecar: bool = True
    recycle_source_sub: bool = True   # after embedding a standalone subtitle file
    recycle_paths: list = field(default_factory=list)   # subtitle files next to the video to remove once it's in
    drop_other_languages: bool = False   # remove subtitle tracks in other languages (forced ones stay)
    keep_language: str = "eng"
    rename_to: str = ""                  # new file name for the video (replace mode), "" = keep the name
    rename_sidecars: list = field(default_factory=list)   # subtitle files next to it that keep their place: rename too


@dataclass
class SaveResult:
    path: str
    note: str = ""


# ============================================================================ files in use (Windows)

def _own_child_pids() -> set[int]:
    """PIDs of the tools SubScrub itself is running right now (ffprobe, mkvextract…)."""
    if not IS_WIN:
        return set()
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                        ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                        ("szExeFile", ctypes.c_wchar * 260)]
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        snap = k32.CreateToolhelp32Snapshot(0x2, 0)                 # TH32CS_SNAPPROCESS
        if not snap or snap == wintypes.HANDLE(-1).value:
            return set()
        me, kids = os.getpid(), set()
        try:
            e = PROCESSENTRY32W()
            e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = k32.Process32FirstW(snap, ctypes.byref(e))
            while ok:
                if e.th32ParentProcessID == me:
                    kids.add(e.th32ProcessID)
                ok = k32.Process32NextW(snap, ctypes.byref(e))
        finally:
            k32.CloseHandle(snap)
        return kids
    except Exception:
        return set()


def who_has_open(paths: list[str]) -> dict[str, list[str]]:
    """Which other programs have each file open, e.g. {path: ["qBittorrent"]}. Uses the Windows Restart
    Manager (the same thing installers use to say "close these apps first"). Empty dict when nothing has
    them open, on other systems, or if Windows can't tell."""
    paths = [p for p in dict.fromkeys(paths) if p and os.path.isfile(p)]
    if not IS_WIN or not paths:
        return {}
    try:
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

        class RM_UNIQUE_PROCESS(ctypes.Structure):
            _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", FILETIME)]

        class RM_PROCESS_INFO(ctypes.Structure):
            _fields_ = [("Process", RM_UNIQUE_PROCESS), ("strAppName", ctypes.c_wchar * 256),
                        ("strServiceShortName", ctypes.c_wchar * 64), ("ApplicationType", ctypes.c_int),
                        ("AppStatus", wintypes.ULONG), ("TSSessionId", wintypes.DWORD),
                        ("bRestartable", wintypes.BOOL)]

        rm = ctypes.WinDLL("rstrtmgr")
        rm.RmStartSession.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.c_wchar_p]
        rm.RmRegisterResources.argtypes = [wintypes.DWORD, wintypes.UINT, ctypes.POINTER(ctypes.c_wchar_p),
                                           wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p]
        rm.RmGetList.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT),
                                 ctypes.POINTER(RM_PROCESS_INFO), ctypes.POINTER(wintypes.DWORD)]
        rm.RmEndSession.argtypes = [wintypes.DWORD]
    except Exception:
        return {}
    ours = _own_child_pids() | {os.getpid()}
    found: dict[str, list[str]] = {}
    for path in paths:
        session = wintypes.DWORD(0)
        key = ctypes.create_unicode_buffer(64)
        try:
            if rm.RmStartSession(ctypes.byref(session), 0, key) != 0:
                continue
        except Exception:
            return found
        try:
            files = (ctypes.c_wchar_p * 1)(os.path.abspath(path))
            if rm.RmRegisterResources(session.value, 1, files, 0, None, 0, None) != 0:
                continue
            size = 16
            infos = (RM_PROCESS_INFO * size)()
            needed, count, reasons = wintypes.UINT(0), wintypes.UINT(size), wintypes.DWORD(0)
            r = rm.RmGetList(session.value, ctypes.byref(needed), ctypes.byref(count), infos, ctypes.byref(reasons))
            if r == 234:                                            # ERROR_MORE_DATA
                found[path] = ["several programs"]
                continue
            if r != 0:
                continue
            names = []
            for i in range(min(count.value, size)):
                pi = infos[i]
                if pi.Process.dwProcessId in ours:
                    continue
                names.append(pi.strAppName.strip() or f"program #{pi.Process.dwProcessId}")
            if names:
                found[path] = sorted(set(names))
        except Exception:
            continue
        finally:
            try:
                rm.RmEndSession(session.value)
            except Exception:
                pass
    return found


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    for i in range(2, 1000):
        cand = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not cand.exists():
            return cand
    raise ToolError(f"Couldn't find a free file name next to {p}")


def default_track_name(track: SubTrack) -> str:
    """Just the language ("English"); SubScrub finds its own tracks again by a hidden tag, not the name."""
    return track.lang_label if track.language != "und" else "Subtitles"


def is_other_language(t: SubTrack, keep: str) -> bool:
    """A subtitle track in another language that the tidy-up removes. Forced tracks, tracks with no language
    set, and everything when the language to keep isn't known, are always left in."""
    mine, theirs = lang_short(keep), lang_short(t.language)
    return bool(mine) and bool(theirs) and theirs != mine and not t.forced


def output_path(info: MediaInfo, opts: SaveOptions, fmt: str) -> Path:
    """Where a save will end up (used for the UI's hints and confirmations too)."""
    src = Path(info.path)
    if info.kind == "subtitle":
        if opts.mode == "embed":
            return Path(info.companion) if info.companion else src
        clean = clean_subtitle_name(src, fmt)
        if opts.mode == "replace":
            return clean if (clean != src and not clean.exists()) else src.with_suffix("." + fmt)
        if clean != src and not clean.exists():
            return clean
        return unique_path(src.with_name(f"{clean.stem}.dialogue.{fmt}"))
    if opts.mode == "sidecar":
        short = lang_short(opts.language)
        if short in SDH_TOKENS:
            short = (opts.language or "").lower()[:3]      # Hindi: "Movie.hin.srt", as ".hi." would read as SDH
        name = f"{src.stem}.{short}.{fmt}" if short else f"{src.stem}.dialogue.{fmt}"
        return src.with_name(name)
    ext = src.suffix if info.kind != "other" else ".mkv"
    if opts.mode == "replace":
        if opts.rename_to:
            return src.with_name(Path(opts.rename_to).stem + ext)
        return src.with_name(src.stem + ext)
    return unique_path(src.with_name(f"{src.stem}.dialogue{ext}"))


def save(tools: Tools, info: MediaInfo, track: SubTrack, cleaned_text: str, fmt: str, opts: SaveOptions,
         on_progress: Progress | None = None, cancel: threading.Event | None = None,
         log: Log | None = None) -> SaveResult:
    lang = (opts.language or track.language or "und").strip() or "und"
    name = opts.track_name.strip() or default_track_name(track)
    opts.language = lang

    if info.kind == "subtitle":
        return _save_subtitle_source(tools, info, cleaned_text, fmt, opts, lang, name, on_progress, cancel, log)

    if opts.mode == "sidecar":
        final = output_path(info, opts, fmt)
        src_file = Path(track.external) if track.external else None
        same = src_file is not None and os.path.normcase(str(final)) == os.path.normcase(str(src_file))
        if final.exists() and not opts.overwrite_sidecar and not same:
            final = unique_path(final)
        note = "Subtitle file saved next to the video."
        if same:
            note = f"“{src_file.name}” replaced with the cleaned version (old one {recycle(src_file, log)})."
        final.write_text(cleaned_text, encoding="utf-8-sig", newline="")
        if log:
            log(f"Wrote {final}")
        if not same:
            note += _recycle_extras([p for p in opts.recycle_paths if src_file and p == str(src_file)], log)
        return SaveResult(str(final), note)
    # a subtitle file next to the video is added as a new track. Nothing inside the video is dropped, except
    # a cleaned track from an earlier SubScrub run in the same language, which the new one replaces.
    inside = track
    if track.external:
        inside = next((t for t in info.subs if t.text_based and t.from_subscrub
                       and lang_short(t.language) == lang_short(lang)), None)
        if inside is not None:
            opts = replace(opts, keep_original=False)
            if log:
                log(f"Replacing the earlier cleaned track “{inside.title}” in {info.path}")
    _check_rename_target(info, opts, fmt)
    res = _remux(tools, info, inside, cleaned_text, fmt, opts, lang, name, on_progress, cancel, log)
    return _after_video_save(info, opts, res, log)


def _check_rename_target(info: MediaInfo, opts: SaveOptions, fmt: str):
    if opts.mode == "replace" and opts.rename_to:
        final = output_path(info, opts, fmt)
        if final.exists() and os.path.normcase(str(final)) != os.path.normcase(info.path):
            raise ToolError(f"Can't rename to “{final.name}”: a file with that name is already in the folder. "
                            "Nothing was changed.")


def _after_video_save(info: MediaInfo, opts: SaveOptions, res: SaveResult, log: Log | None) -> SaveResult:
    if opts.mode == "replace":
        res.note += _recycle_extras(opts.recycle_paths, log, protect=[res.path, info.path])
        if opts.rename_to and os.path.normcase(res.path) != os.path.normcase(info.path):
            res.note += _rename_sidecars(info.path, res.path, opts.rename_sidecars, log)
    if not os.path.isfile(res.path):
        raise ToolError(f"“{os.path.basename(res.path)}” is missing right after saving.")
    return res


def _rename_sidecars(old_video: str, new_video: str, sidecars: list, log: Log | None) -> str:
    """'Old.eng.srt' -> 'New.eng.srt' for subtitle files that stay next to a renamed video."""
    old_stem, new_stem = Path(old_video).stem, Path(new_video).stem
    done = []
    for p in sidecars:
        if not p or not os.path.isfile(p):
            continue
        name = os.path.basename(p)
        if not name.lower().startswith(old_stem.lower()):
            continue
        target = Path(p).with_name(new_stem + name[len(old_stem):])
        if target.exists():
            done.append(f"kept “{name}” (“{target.name}” already exists)")
            continue
        try:
            os.rename(p, target)
            if log:
                log(f"Renamed {name} -> {target.name}")
            done.append(f"“{name}” renamed to match")
        except OSError as e:
            done.append(f"couldn't rename “{name}” ({e})")
    return (" " + "; ".join(done) + ".") if done else ""


def tidy_or_rename(tools: Tools, info: MediaInfo, opts: SaveOptions, on_progress: Progress | None = None,
                   cancel: threading.Event | None = None, log: Log | None = None) -> SaveResult:
    """For a video whose subtitles need no cleaning: remove other-language subtitle tracks and/or rename it."""
    drop = {t.index for t in info.subs if opts.drop_other_languages and is_other_language(t, opts.keep_language)}
    if drop:
        _check_rename_target(info, opts, "srt")
        res = _remux(tools, info, None, None, "srt", opts, opts.keep_language, "", on_progress, cancel, log,
                     drop=drop)
        res.note = f"Removed {len(drop)} subtitle track{'s' if len(drop) != 1 else ''} in other languages. " + res.note
        return _after_video_save(info, opts, res, log)
    if opts.rename_to:
        src = Path(info.path)
        final = src.with_name(Path(opts.rename_to).stem + src.suffix)
        if os.path.normcase(str(final)) == os.path.normcase(str(src)):
            return SaveResult(str(src), "Name already right.")
        if final.exists():
            raise ToolError(f"Can't rename to “{final.name}”: a file with that name is already in the folder.")
        who = who_has_open([str(src)]).get(str(src))
        if who:
            raise ToolError(f"“{src.name}” is open in {' and '.join(who)}, so it couldn't be renamed. Nothing was "
                            "changed.")
        os.rename(src, final)
        if log:
            log(f"Renamed {src.name} -> {final.name}")
        res = SaveResult(str(final), "Renamed.")
        res.note += _rename_sidecars(str(src), str(final), opts.rename_sidecars, log)
        return res
    return SaveResult(info.path, "Nothing to change.")


def _recycle_extras(paths: list, log: Log | None, protect: list | None = None) -> str:
    """Remove subtitle files that are now inside the video. Only subtitle files, never the video itself."""
    done = []
    keep = {os.path.normcase(os.path.abspath(x)) for x in (protect or []) if x}
    for p in paths:
        if not p or Path(p).suffix.lower() not in TEXT_SUB_EXTS or os.path.normcase(os.path.abspath(p)) in keep:
            continue
        if os.path.isfile(p):
            try:
                what = recycle(p, log)
                done.append(f"“{os.path.basename(p)}” {what}")
            except OSError as e:
                done.append(f"couldn't remove “{os.path.basename(p)}” ({e})")
    return (" " + "; ".join(done) + ".") if done else ""


def _save_subtitle_source(tools, info, text, fmt, opts, lang, name, on_progress, cancel, log) -> SaveResult:
    src = Path(info.path)
    if opts.mode == "embed":
        if not info.companion or not os.path.isfile(info.companion):
            raise ToolError(f"There's no video named like “{src.name}” in its folder to embed it into.")
        if on_progress:
            on_progress(-1, "Opening the video")
        vinfo = probe(tools, info.companion, log)
        vopts = SaveOptions(mode="replace", keep_original=True, make_default=opts.make_default,
                            track_name=name, language=lang)
        res = _remux(tools, vinfo, None, text, fmt, vopts, lang, name, on_progress, cancel, log)
        if opts.recycle_source_sub and src.exists():
            res.note += f" “{src.name}” {recycle(src, log)}."
        return res
    target = output_path(info, opts, fmt)
    if opts.mode == "replace":
        what = recycle(src, log)                 # old SDH version goes to the Recycle Bin first
        target.write_text(text, encoding="utf-8-sig", newline="")
        return SaveResult(str(target), f"Old version {what}.")
    target.write_text(text, encoding="utf-8-sig", newline="")
    return SaveResult(str(target), "Saved next to the original.")


def _remux(tools, info, track, text, fmt, opts, lang, name, on_progress, cancel, log, drop=None) -> SaveResult:
    """Write the video again with the cleaned track added (and the old one dropped unless keep_original), and
    other-language subtitle tracks removed when asked. `track` is the subtitle track the text came from, or None
    when it came from a separate file; `text` None means no new track (tidy-up only)."""
    if not tools.ffmpeg:
        raise ToolError("FFmpeg was not found.")
    final = output_path(info, opts, fmt)
    src = Path(info.path)
    free = shutil.disk_usage(src.parent).free
    if free < info.size * 1.02 + 64 * 1024 * 1024:
        raise ToolError(f"Not enough free space on that drive: need about {info.size * 1.02 / 1e9:.1f} GB, "
                        f"have {free / 1e9:.1f} GB.")

    tmpdir = tempfile.mkdtemp(prefix="subscrub_mux_")
    sub_path = None
    if text is not None:
        sub_path = os.path.join(tmpdir, f"cleaned.{fmt}")
        Path(sub_path).write_text(text, encoding="utf-8", newline="")
    drop = set(drop or ())
    if track is not None and not opts.keep_original:
        drop.add(track.index)
    if opts.drop_other_languages:
        drop |= {t.index for t in info.subs if is_other_language(t, opts.keep_language)}

    work = final.with_name(f"{src.stem}.subscrub-tmp{final.suffix}") if opts.mode == "replace" else final
    if work.exists():
        work.unlink()
    use_mkvmerge = info.kind == "mkv" and bool(tools.mkvmerge)
    try:
        if use_mkvmerge:
            _mux_mkvmerge(tools, info, track, drop, sub_path, str(work), lang, name, opts, on_progress, cancel, log)
        else:
            _mux_ffmpeg(tools, info, track, drop, sub_path, str(work), lang, name, opts, on_progress, cancel, log)
        if on_progress:
            on_progress(-1, "Checking the new file")
        expected = len(info.subs) + (1 if sub_path else 0) - len(drop)
        _verify(tools, info, str(work), expected, log, new_text=text if sub_path else None, fmt=fmt,
                new_hint=(lang, name))
    except BaseException:
        try:
            if work.exists():
                work.unlink()
        except OSError:
            pass
        raise
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    note = f"Saved with {'MKVToolNix' if use_mkvmerge else 'FFmpeg'}."
    if opts.mode == "replace":
        note = _swap_in(src, work, final, log)
    return SaveResult(str(final), note)


def _swap_in(src: Path, work: Path, final: Path, log: Log | None) -> str:
    """Put the verified new file in the original's place, never ending up with neither:
      1. nothing happens while another program has the original open;
      2. the original goes to the Recycle Bin (if that fails and it's still there, nothing is changed);
      3. the new file takes its name. From the moment the original has moved, the new file is never
         deleted: if it can't be renamed it stays next to it under its temporary name."""
    def abort(msg: str) -> ToolError:
        try:
            os.remove(work)                          # just the new copy; the original is untouched
        except OSError:
            pass
        return ToolError(msg)

    def in_use() -> ToolError:
        who = who_has_open([str(src)]).get(str(src))
        by = " and ".join(who) if who else "another program (a torrent client, a player, Jellyfin, Explorer's preview…)"
        return abort(f"“{src.name}” is open in {by}, so it couldn't be replaced. Nothing was changed — "
                     "close or pause it there and save again.")

    if who_has_open([str(src)]).get(str(src)):
        raise in_use()
    network = is_network_path(src)
    note = "Original moved to the Recycle Bin."
    if network:
        note = "Original replaced (network drives have no Recycle Bin)."
    else:
        try:
            from send2trash import send2trash
            send2trash(str(src))
        except Exception as e:  # noqa: BLE001
            if os.path.exists(src):
                if isinstance(e, PermissionError) or getattr(e, "winerror", None) in (32, 33):
                    raise in_use()
                raise abort(f"“{src.name}” couldn't be moved to the Recycle Bin ({e}), so it wasn't replaced. "
                            "Nothing was changed.")
            # Windows reported a problem but the original is gone from the folder: carry on, keep the new file
            note = "Original moved out of the folder (Windows reported a Recycle Bin problem: look for it there)."
            if log:
                log(f"Recycle Bin reported {e!r} for {src.name}, but the file was moved.")

    last: OSError | None = None
    for _ in range(12):                              # an indexer or antivirus can hold the new file for a moment
        try:
            os.replace(work, final)
            last = None
            break
        except OSError as e:
            last = e
            if network and os.path.exists(src):      # nothing has changed yet on a network drive
                break
            time.sleep(0.5)
    if last is not None:
        if network and os.path.exists(src):
            if isinstance(last, PermissionError) or getattr(last, "winerror", None) in (32, 33):
                raise in_use()
            raise abort(f"“{src.name}” couldn't be replaced ({last}). Nothing was changed.")
        raise ToolError(f"The original “{src.name}” is in the Recycle Bin, but the new version couldn't take its "
                        f"name ({last}). It's saved next to it as “{work.name}”: rename that to “{final.name}”.")
    if not final.is_file():
        raise ToolError(f"“{final.name}” is missing right after saving. The original is in the Recycle Bin.")
    if network and final != src and os.path.exists(src):
        os.remove(src)                               # renamed on a network drive: the old name goes
    if log:
        log(note)
    return note


_TAGS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Tags><Tag><Targets><TargetTypeValue>50</TargetTypeValue></Targets>
<Simple><Name>SUBSCRUB</Name><String>cleaned</String></Simple></Tag></Tags>
"""


def _mux_mkvmerge(tools, info, track, drop, sub_path, out, lang, name, opts, on_progress, cancel, log):
    ident = json.loads(run_capture([tools.mkvmerge, "-J", info.path], log, timeout=120) or "{}")
    mtracks = ident.get("tracks", [])
    sub_ids = [t["id"] for t in mtracks if t.get("type") == "subtitles"]
    if len(sub_ids) != len(info.subs):
        raise ToolError("MKVToolNix and FFprobe disagree about the subtitle tracks in this file.")
    to_id = {t.index: sub_ids[k] for k, t in enumerate(info.subs)}
    drop_ids = {to_id[i] for i in drop if i in to_id}
    keep_ids = [i for i in sub_ids if i not in drop_ids]
    target = to_id.get(track.index) if track is not None else None
    # the cleaned track goes where the one it came from was (or after the last subtitle track kept)
    anchor = target if target is not None else (keep_ids[-1] if keep_ids else None)

    cmd = [tools.mkvmerge, "--gui-mode", "--output-charset", "UTF-8", "-o", out]
    if drop_ids:
        cmd += ["--subtitle-tracks", ",".join(map(str, keep_ids))] if keep_ids else ["--no-subtitles"]
    if opts.make_default and sub_path:
        for i in keep_ids:
            cmd += ["--default-track-flag", f"{i}:0"]
    cmd += [info.path]
    if sub_path:
        tags = os.path.join(os.path.dirname(sub_path), "tags.xml")
        Path(tags).write_text(_TAGS_XML, encoding="utf-8")
        cmd += ["--language", f"0:{lang}", "--track-name", f"0:{name}",
                "--default-track-flag", f"0:{1 if opts.make_default else 0}",
                "--hearing-impaired-flag", "0:0", "--sub-charset", "0:UTF-8", "--tags", f"0:{tags}", sub_path]
        order: list[str] = []
        placed = False
        for t in mtracks:
            tid = t["id"]
            if t.get("type") == "subtitles" and tid in drop_ids:
                if tid == target and not placed:
                    order.append("1:0")
                    placed = True
                continue
            order.append(f"0:{tid}")
            if tid == anchor and not placed:
                order.append("1:0")
                placed = True
        if not placed:
            order.append("1:0")
        cmd += ["--track-order", ",".join(order)]

    def on_line(line: str):
        m = re.match(r"#GUI#progress (\d+)%", line)
        if m and on_progress:
            on_progress(int(m.group(1)) / 100, "Writing new file")

    run_stream(cmd, on_line, log, cancel, ok_codes=(0, 1))   # 1 = finished with warnings


def _mux_ffmpeg(tools, info, track, drop, sub_path, out, lang, name, opts, on_progress, cancel, log):
    out_kind = container_kind(out)
    cmd = [tools.ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
           "-progress", "pipe:1", "-nostats", "-i", info.path]
    if sub_path:
        cmd += ["-i", sub_path]
    mapped = [s for s in info.streams
              if not (s.get("codec_type") in ("data", "attachment") and out_kind != "mkv")]
    subs_idx = [int(s["index"]) for s in mapped if s.get("codec_type") == "subtitle" and int(s["index"]) not in drop]
    main_idx = [int(s["index"]) for s in mapped if s.get("codec_type") in ("video", "audio")]
    anchor = track.index if track is not None else (subs_idx[-1] if subs_idx else (main_idx[-1] if main_idx else None))
    sub_out: list[SubTrack | None] = []          # output subtitle order; None = the new track
    placed = not sub_path
    for s in mapped:
        idx, ctype = int(s.get("index", 0)), s.get("codec_type")
        if idx not in drop:
            cmd += ["-map", f"0:{idx}"]
            if ctype == "subtitle":
                sub_out.append(next(t for t in info.subs if t.index == idx))
        if idx == anchor and not placed:
            cmd += ["-map", "1:0"]
            sub_out.append(None)
            placed = True
    if not placed:
        cmd += ["-map", "1:0"]
        sub_out.append(None)
    cmd += ["-map_metadata", "0", "-map_chapters", "0", "-c", "copy"]
    if sub_path:
        new_pos = sub_out.index(None)
        src_codec = {"srt": "subrip", "ass": "ass", "vtt": "webvtt"}.get(Path(sub_path).suffix.lstrip("."), "subrip")
        new_codec = {"mp4": "mov_text", "webm": "webvtt"}.get(out_kind, "copy")
        if out_kind == "mkv" and src_codec == "webvtt":
            new_codec = "srt"                        # WebVTT in MKV is poorly supported by players
        cmd += [f"-c:s:{new_pos}", new_codec]
    for j, t in enumerate(sub_out):
        if t is None:
            cmd += [f"-disposition:s:{j}", "default" if opts.make_default else "0",
                    f"-metadata:s:s:{j}", f"language={lang}", f"-metadata:s:s:{j}", f"title={name}",
                    f"-metadata:s:s:{j}", "SUBSCRUB=cleaned"]
            if out_kind == "mp4":
                cmd += [f"-metadata:s:s:{j}", f"handler_name={name}"]
        else:
            cmd += [f"-disposition:s:{j}", t.dispo_flags(drop_default=opts.make_default and bool(sub_path))]
    if out_kind == "mkv":
        cmd += ["-default_mode", "passthrough"]
    cmd += ["-max_interleave_delta", "0", out]
    run_stream(cmd, _ffmpeg_progress(info.duration, on_progress, "Writing new file"), log, cancel)


def _new_track_cues(tools: Tools, after: MediaInfo, out: str, fmt: str, hint: tuple, log: Log | None) -> int | None:
    """How many cues the new file's cleaned track has (None when that track can't be told apart from the others).
    The track is found by the tag SubScrub writes, or else by its language and name."""
    from . import subformats
    lang, name = hint
    mine = [t for t in after.subs if t.text_based and t.cleaned]
    if not mine:
        mine = [t for t in after.subs if t.text_based and lang_short(t.language) == lang_short(lang)
                and (t.title or "").strip().lower() == name.strip().lower()]
    if not mine:
        return None
    tmp = tempfile.mkdtemp(prefix="subscrub_verify_")
    try:
        paths = extract_subs(tools, after, mine, tmp, None, None, log)
        counts = []
        for t in mine:
            if t.index in paths:
                counts.append(len(subformats.load(paths[t.index], t.fmt).cues))
        # an earlier cleaned track kept next to this one is another candidate: the new one is the best match
        return max(counts) if counts else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _verify(tools: Tools, before: MediaInfo, out: str, expected_subs: int, log: Log | None,
            new_text: str | None = None, fmt: str = "srt", new_hint: tuple | None = None):
    """Decide whether the new file is good enough to replace the original. Checks the track counts, duration, size,
    and (for MKV) the attachments such as ASS fonts, the chapters, and that the new subtitle track really holds
    the cleaned lines."""
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        raise ToolError("The new file wasn't written.")
    after = probe(tools, out, log)
    problems = []
    if len(after.subs) != expected_subs:
        problems.append(f"expected {expected_subs} subtitle tracks, found {len(after.subs)}")
    for kind in ("video", "audio"):
        if after.count(kind) != before.count(kind):
            problems.append(f"{kind} track count changed ({before.count(kind)} → {after.count(kind)})")
    if before.duration and after.duration and abs(before.duration - after.duration) > max(2.0, before.duration * 0.01):
        problems.append(f"duration changed ({before.duration:.1f}s → {after.duration:.1f}s)")
    if before.size and after.size < before.size * 0.5:
        problems.append("the new file is much smaller than the original")
    if after.kind == "mkv" and after.count("attachment") < before.count("attachment"):
        problems.append(f"attached files (such as fonts) were lost ({before.count('attachment')} → "
                        f"{after.count('attachment')})")
    if after.chapters != before.chapters:
        problems.append(f"chapters changed ({before.chapters} → {after.chapters})")
    cues = None
    if not problems and new_text is not None and new_hint is not None:
        from . import subformats
        want = len(subformats.parse(new_text, fmt).cues)
        cues = _new_track_cues(tools, after, out, fmt, new_hint, log)
        if cues is None:
            if log:
                log("Couldn't tell which subtitle track in the new file is the cleaned one; skipped the line count.")
        elif cues < want * 0.98 - 1:
            problems.append(f"the cleaned subtitle track has {cues} lines instead of {want}")
    if problems:
        raise ToolError("The new file didn't pass checks, so nothing was replaced: " + "; ".join(problems))
    if log:
        log(f"Verified {os.path.basename(out)}: {after.count('video')} video, {after.count('audio')} audio, "
            f"{len(after.subs)} subtitle tracks, {after.count('attachment')} attachments, {after.chapters} chapters, "
            f"{after.duration:.1f}s" + (f", {cues} cleaned lines." if cues is not None else "."))


def open_in_explorer(path: str):
    if IS_WIN:
        subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])
