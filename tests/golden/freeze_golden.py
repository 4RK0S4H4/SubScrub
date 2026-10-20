"""Freeze the Python engine's answers as golden files, so the C# engine can be checked against them once the Python
engine is gone (issue #44). Run once, on Linux, before the Python engine was removed; kept for the record. It needs
the Python engine (subscrub/ and tools/*_parity.py as of tag v3.0.1 plus the 4.0 batch), so it no longer runs here.

    python tools/freeze_golden.py --library samples/library [--out tests/golden]     (from tools/, back then)

Writes tests/golden/cleaner.json.br and engine.json.br (Brotli-compressed JSON): the cases tools/cleaner_parity.py and
tools/engine_parity.py built, and what the Python engine answered. The C# side replays them with
    dotnet run --project winui/SubScrub.Engine.Parity -c Release -- golden tests/golden <repository>

Made only from the hand-written fixtures in tests/fixtures and the generated sample videos (tests.make_samples), never
real media or the user's subtitles. To make the files portable:
  * the temporary folder is written as "<root>"; the replay rebuilds the same folders, copying the sample videos by
    name from samples/ and writing the small files (settings, leftovers, sidecars) from the golden file itself;
  * what depends on the exact bytes of a generated video or on the FFmpeg version is left out on both sides: file
    sizes, ffprobe's raw stream details (kept: each stream's type and codec) and disposition flags beyond the
    common ones; durations are rounded to a tenth of a second; tool paths are dropped.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import brotli

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import cleaner_parity as cp  # noqa: E402
import engine_parity as ep  # noqa: E402

DISPOSITION = ("default", "forced", "hearing_impaired", "visual_impaired", "original", "comment", "dub", "lyrics",
               "karaoke")
EMBED_LIMIT = 512 * 1024          # small files are written into the golden file; large ones come from samples/
PYTHON_ONLY = {"xdg", "x", "cmd-py", "appdata", "extract", "tracks"}
VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm"}


def project(x):
    """The parts that don't depend on a video's exact bytes or the FFmpeg version (mirrored in GoldenParity.cs)."""
    if isinstance(x, dict):
        if "streams" in x and "subs" in x and "format_name" in x:          # a probed file (info_json)
            x = dict(x)
            x["streams"] = [[s.get("codec_type"), s.get("codec_name")] if isinstance(s, dict) else s
                            for s in x["streams"]]
            x.pop("size", None)
            if isinstance(x.get("duration"), (int, float)):
                x["duration"] = round(float(x["duration"]), 1)
        out = {}
        for k, v in x.items():
            if k == "disposition" and isinstance(v, dict):
                out[k] = {d: v[d] for d in DISPOSITION if d in v}
            elif k == "tools" and isinstance(v, dict) and "ffprobe" in v:
                continue
            else:
                out[k] = project(v)
        return out
    if isinstance(x, list):
        return [project(v) for v in x]
    return x


def rooted(data, tmp: Path):
    text = json.dumps(data, ensure_ascii=False, default=str)
    for form in (str(tmp), json.dumps(str(tmp))[1:-1]):
        text = text.replace(form, "<root>")
    return json.loads(text)


def write(path: Path, data) -> int:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    packed = brotli.compress(raw, quality=11, lgwin=24)
    path.write_bytes(packed)
    return len(packed)


def source_for(rel: Path, sources: dict[str, str]) -> str | None:
    """Where the replay gets a large file: the sample video of the same name."""
    parts = rel.parts
    if parts[0] == "edge":
        return "edge/" + "/".join(parts[1:])
    return sources.get(rel.name)


def layout(tmp: Path) -> dict:
    samples = ROOT / "samples"
    sources = {p.name: "library/" + p.name for p in sorted((samples / "library").iterdir())}
    sources.update({p.name: p.name for p in samples.iterdir() if p.is_file()})
    sources.update({p.name: "edge/" + p.relative_to(samples / "edge").as_posix()
                    for p in sorted((samples / "edge").rglob("*")) if p.is_file()})
    dirs, files = [], {}
    for p in sorted(tmp.rglob("*")):
        rel = p.relative_to(tmp)
        top = rel.parts[0]
        if top in PYTHON_ONLY or (top == "saving" and len(rel.parts) > 1 and rel.parts[1].endswith("-py")):
            continue                           # what only the Python run wrote or read
        if p.is_dir():
            dirs.append(rel.as_posix())
            continue
        data = p.read_bytes()
        entry = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        src = source_for(rel, sources)
        if src is not None and (samples / src).is_file() and (samples / src).stat().st_size == len(data):
            entry["from"] = src
        elif len(data) <= EMBED_LIMIT and top != "saved" and p.suffix.lower() not in VIDEO_EXTS:
            entry["b64"] = base64.b64encode(data).decode("ascii")
        files[rel.as_posix()] = entry          # neither: a placeholder of the same size (only its name is used)
    return {"dirs": dirs, "files": files}


def freeze_cleaner(library: Path, tmp: Path) -> dict:
    cues, docs = cp.record_tests()
    paths = sorted(cp.FIX.glob("*.srt")) + sorted(cp.FIX.glob("*.ass"))
    lib_paths, pairs = cp.library_tracks(library, tmp / "tracks")
    docs += cp.file_docs(paths + lib_paths, pairs) + cp.boundary_docs()
    cases = {"cues": cues, "docs": docs, "decode": cp.decode_cases()}
    return {"cases": cases, "expected": cp.py_results(cases)}


def freeze_engine(library: Path, tmp: Path) -> dict:
    os.environ["LOCALAPPDATA"] = str(tmp / "appdata")
    os.environ["XDG_DATA_HOME"] = str(tmp / "xdg")
    sessions, expected_sessions = ep.build_sessions(library, tmp)
    fcases, fexpected = ep.file_cases([tmp / "library", tmp / "edge", tmp / "subs"])
    rcases, rexpected = ep.reading_cases(tmp)
    scases, sexpected = ep.saving_cases(library, tmp)
    ncases, nexpected = ep.naming_cases(tmp)
    ccase, cexpected = ep.commands_case(library, tmp)
    cases = {"files": fcases, "sessions": sessions, "reading": rcases, "saving": scases, "naming": ncases,
             "commands": ccase}
    expected = ep.norm({"files": fexpected, "sessions": expected_sessions, "reading": rexpected,
                        "saving": sexpected, "naming": nexpected, "commands": cexpected})
    cases = json.loads(json.dumps(cases, ensure_ascii=False, default=str))
    return {"layout": layout(tmp), "cases": rooted(cases, tmp), "expected": rooted(project(expected), tmp)}


def main() -> int:
    argv = sys.argv[1:]
    library = Path(argv[argv.index("--library") + 1]).resolve()
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else ROOT / "tests" / "golden"
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="subscrub_golden_"))
    try:
        n = write(out / "cleaner.json.br", freeze_cleaner(library, tmp / "cleaner"))
        print(f"cleaner.json.br: {n:,} bytes")
        etmp = tmp / "engine"
        etmp.mkdir()
        n = write(out / "engine.json.br", freeze_engine(library, etmp))
        print(f"engine.json.br: {n:,} bytes")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
