"""Check that the C# engine (winui/SubScrub.Engine) cleans exactly like the Python one (subscrub/cleaner.py).

    python tools/cleaner_parity.py [--library samples/library] [--keep <folder>]

Records the cases, runs the C# port on them (winui/SubScrub.Engine.Parity, needs the .NET 8 SDK) and compares every
result: the cleaned text, whether it changed, the rules, the risk, the reasons, the document notes, the counts, the
saved file (render) and a parse/render round trip. Any difference fails, with the first few listed.

The cases:
  * every clean_cue / clean_doc call the cleaner test suites make (tests.test_cleaner, tests.test_library),
    recorded by wrapping the two functions while those tests run (the corpus table is in there too);
  * the fixtures in tests/fixtures under each option on its own switched the other way, and with lyrics removed;
  * with --library: every text track of every sample video and the .srt files next to them, the same way, plus
    each SDH track cross-checked against the regular English track of the same video;
  * documents right at the edges of the "to check" grades;
  * file decoding: UTF-8 with and without a BOM, UTF-16 LE/BE, Windows-1252 and Latin-1 bytes.
"""
from __future__ import annotations

import base64
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from subscrub import cleaner, subformats as sf  # noqa: E402
from subscrub.cleaner import CleanOptions  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
FLIPS = list(CleanOptions().to_dict())


def opt_variants() -> list[dict]:
    base = CleanOptions().to_dict()
    out = [base]
    for k in FLIPS:
        out.append(dict(base, **{k: not base[k]}))
    out.append(dict(base, lyrics=True))
    out.append(dict(base, lyrics=False, lyric_symbols=False))
    return out


# ============================================================================ recording the test suites

def record_tests() -> tuple[list, list]:
    cue_calls, doc_calls = {}, {}
    real_cue, real_doc = cleaner.clean_cue, cleaner.clean_doc

    def rec_cue(text, opts, fmt="srt", ctx=None):
        c = None
        if ctx is not None:
            c = {"continues_next": ctx.continues_next, "continued_from_prev": ctx.continued_from_prev,
                 "mixed_names": ctx.mixed_names, "ocr_notes": ctx.ocr_notes, "ocr_suspect": ctx.ocr_suspect,
                 "quote_voices": ctx.quote_voices}
        case = {"text": text, "opts": opts.to_dict(), "fmt": fmt, "ctx": c}
        cue_calls.setdefault(json.dumps(case, sort_keys=True, ensure_ascii=False), case)
        return real_cue(text, opts, fmt, ctx)

    def rec_doc(doc, opts, plain=None):
        content = doc.render([c.text for c in doc.cues])
        case = {"content": content, "fmt": doc.fmt, "opts": opts.to_dict()}
        if plain is not None:
            case["plain"], case["plain_fmt"] = plain.render([c.text for c in plain.cues]), plain.fmt
        key = json.dumps(case, sort_keys=True, ensure_ascii=False)
        if key not in doc_calls:
            doc_calls[key] = dict(case, name=f"test call {len(doc_calls) + 1}")
        return real_doc(doc, opts, plain)

    cleaner.clean_cue, cleaner.clean_doc = rec_cue, rec_doc
    try:
        import importlib
        for name in ("tests.test_cleaner", "tests.test_library"):
            mod = importlib.import_module(name)
            for fname, fn in sorted(vars(mod).items()):
                if fname.startswith("test_") and inspect.isfunction(fn):
                    fn()
    finally:
        cleaner.clean_cue, cleaner.clean_doc = real_cue, real_doc
    return list(cue_calls.values()), list(doc_calls.values())


# ============================================================================ files

def file_docs(paths: list[Path], plain_pairs: list[tuple[str, str, Path, Path]] = ()) -> list[dict]:
    docs = []
    for p in paths:
        text = sf.read_text_file(p)
        fmt = sf.detect_format(text, str(p))
        for k, o in enumerate(opt_variants()):
            docs.append({"name": f"{p.name} [{k}]", "content": text, "fmt": fmt, "opts": o})
    for name, fmt, sdh, plain in plain_pairs:
        docs.append({"name": f"{name} cross-checked", "content": sf.read_text_file(sdh), "fmt": fmt,
                     "opts": CleanOptions().to_dict(), "plain": sf.read_text_file(plain), "plain_fmt": "srt"})
    return docs


def library_tracks(folder: Path, tmp: Path) -> tuple[list[Path], list]:
    from subscrub import batch, media
    tools = media.find_tools()
    paths, pairs = [], []
    for video in sorted(folder.glob("*.mkv")):
        info = media.probe(tools, str(video))
        out = tmp / video.stem
        out.mkdir(parents=True, exist_ok=True)
        got = media.extract_subs(tools, info, info.subs, str(out))
        tracks = {t.index: Path(got[t.index]) for t in info.subs if t.index in got}
        paths += tracks.values()
        sdh = [t for t in info.subs if t.index in tracks and t.sdh_flagged]
        plain = [t for t in info.subs if t.index in tracks and not t.sdh_flagged and media.lang_short(t.language) == "en"]
        for s in sdh:
            for pl in plain:
                pairs.append((f"{video.stem} track {s.index}", "srt", tracks[s.index], tracks[pl.index]))
    paths += sorted(folder.glob("*.srt"))
    _ = batch
    return paths, pairs


def boundary_docs() -> list[dict]:
    """Documents right at the edges of the grades: 12 and 13 lines to check ("check" vs "look closer"), and an OCR'd
    track with 4 and 5."""
    docs = []
    for n in (11, 12, 13, 14):
        cues = "".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},500\nMom (she's in the car) is fine.\n\n"
                       for i in range(1, n + 1))
        docs.append({"name": f"{n} lines to check", "content": cues, "fmt": "srt", "opts": CleanOptions().to_dict()})
    for n in (4, 5):
        ocr = "".join(f"{i}\n00:00:{i:02d},000 --> 00:00:{i:02d},500\nj\" Somewhere over the rainbow j\"\n\n"
                      for i in range(1, 4))
        risky = "".join(f"{i}\n00:01:{i:02d},000 --> 00:01:{i:02d},500\nMom (she's in the car) is fine.\n\n"
                        for i in range(1, n + 1))
        docs.append({"name": f"OCR'd, {n} to check", "content": ocr + risky, "fmt": "srt", "opts": CleanOptions().to_dict()})
    return docs


def decode_cases() -> list[dict]:
    text = "Café — “quoted” ♪ naïve\nZweite Zeile"
    blobs = [text.encode("utf-8"), b"\xef\xbb\xbf" + text.encode("utf-8"), b"\xff\xfe" + text.encode("utf-16-le"),
             b"\xfe\xff" + text.encode("utf-16-be"), "Café “quoted” naïve".encode("cp1252"), b"caf\xe9 \x81\x8d na\xefve"]
    return [{"b64": base64.b64encode(b).decode("ascii")} for b in blobs]


# ============================================================================ the expected results

def py_cue(r) -> dict:
    return {"text": r.text, "changed": r.changed, "rules": sorted(r.rules), "risk": r.risk, "why": list(r.why)}


def py_results(cases: dict) -> dict:
    out = {"cues": [], "docs": [], "decode": []}
    for c in cases["cues"]:
        ctx = None
        if c["ctx"] is not None:
            ctx = cleaner.CueCtx(**c["ctx"])
        out["cues"].append(py_cue(cleaner.clean_cue(c["text"], CleanOptions.from_dict(c["opts"]), c["fmt"], ctx)))
    for d in cases["docs"]:
        doc = sf.parse(d["content"], d["fmt"])
        plain = sf.parse(d["plain"], d.get("plain_fmt", "srt")) if d.get("plain") is not None else None
        res = cleaner.clean_doc(doc, CleanOptions.from_dict(d["opts"]), plain)
        n = res.notes
        out["docs"].append({
            "name": d["name"],
            "cues": [{"start": c.start, "end": c.end, "text": c.text, "seconds": sf.to_seconds(c.start)} for c in doc.cues],
            "results": [py_cue(r) for r in res.results],
            "notes": {"mixed_names": dict(sorted(n.mixed_names.items())), "quote_voices": n.quote_voices,
                      "ocr_notes": n.ocr_notes, "ocr_suspect": n.ocr_suspect, "notes": list(n.notes)},
            "stats": {"total": res.stats.total, "removed": res.stats.removed, "edited": res.stats.edited,
                      "risky": res.stats.risky, "glance": res.stats.glance, "by_rule": dict(sorted(res.stats.by_rule.items()))},
            "confidence": res.confidence,
            "render": res.render(),
            "roundtrip": doc.render([c.text for c in doc.cues]),
        })
    for c in cases["decode"]:
        tmp = Path(tempfile.mkdtemp()) / "x.srt"
        tmp.write_bytes(base64.b64decode(c["b64"]))
        out["decode"].append(sf.read_text_file(tmp))
        shutil.rmtree(tmp.parent)
    return out


def diff(a, b, path="") -> list[str]:
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
    argv = sys.argv[1:]
    library = Path(argv[argv.index("--library") + 1]) if "--library" in argv else None
    keep = Path(argv[argv.index("--keep") + 1]) if "--keep" in argv else None
    tmp = Path(tempfile.mkdtemp(prefix="subscrub_parity_"))
    try:
        cues, docs = record_tests()
        paths = sorted(FIX.glob("*.srt")) + sorted(FIX.glob("*.ass"))
        pairs = []
        if library is not None:
            lib_paths, pairs = library_tracks(library, tmp / "tracks")
            paths += lib_paths
        docs += file_docs(paths, pairs) + boundary_docs()
        cases = {"cues": cues, "docs": docs, "decode": decode_cases()}
        (tmp / "cases.json").write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
        expected = py_results(cases)
        proj = ROOT / "winui" / "SubScrub.Engine.Parity" / "SubScrub.Engine.Parity.csproj"
        run = subprocess.run(["dotnet", "run", "--project", str(proj), "-c", "Release", "--",
                              str(tmp / "cases.json"), str(tmp / "csharp.json")], capture_output=True, text=True)
        if run.returncode != 0:
            print(run.stdout[-4000:], run.stderr[-4000:])
            print("the C# side failed to run")
            return 1
        got = json.loads((tmp / "csharp.json").read_text(encoding="utf-8"))
        if keep:
            keep.mkdir(parents=True, exist_ok=True)
            for f in ("cases.json", "csharp.json"):
                shutil.copy(tmp / f, keep / f)
            (keep / "python.json").write_text(json.dumps(expected, ensure_ascii=False), encoding="utf-8")
        problems = diff(expected, got)
        n_lines = sum(len(d["results"]) for d in expected["docs"]) + len(expected["cues"])
        print(f"{len(cues)} recorded cue calls, {len(docs)} documents ({n_lines:,} lines in all), "
              f"{len(expected['decode'])} decodings")
        if problems:
            for p in problems[:40]:
                print("  DIFF", p)
            print(f"{len(problems)} differences between the Python and the C# engine")
            return 1
        print("The C# engine matches the Python one exactly.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
