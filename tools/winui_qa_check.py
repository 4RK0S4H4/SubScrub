"""Check what the WinUI app's scripted QA run saved (SubScrub.exe --shots ... --library ...), with ffprobe and
mkvmerge: every video saved, one default English track named "English" with no sound tags left in it, other
languages gone, the regular-track rule kept (an SDH track or .sdh.srt stays when a regular track was cleaned),
video and audio untouched, and nothing else left behind in the folder.

    python tools/winui_qa_check.py <qa-dark.json> <pristine sample library>

Exits non-zero and says why when something is wrong.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from subscrub import media, subformats as sf  # noqa: E402

SOUND_TAG = re.compile(r"\[[^\]\n]{2,}\]")
MUSIC = re.compile(r"[♪♫]")


def main() -> int:
    qa = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    pristine = Path(sys.argv[2])
    tools = media.find_tools()
    problems, checked = [], []

    def bad(msg):
        problems.append(msg)

    counts = qa.get("counts", {})
    if counts.get("failed"):
        bad(f"{counts['failed']} file(s) failed: {qa.get('status')}")
    originals = {p.name: media.probe(tools, str(p)) for p in pristine.glob("*.mkv")}
    outputs = qa.get("outputs", [])
    if len(outputs) != len(originals):
        bad(f"{len(outputs)} videos saved, expected {len(originals)}")
    library = Path(qa["library"])
    for out in outputs:
        p = Path(out)
        if not p.exists():
            bad(f"{p.name}: not there after saving")
            continue
        info = media.probe(tools, str(p))
        src = min(originals.values(), key=lambda o: abs(o.duration - info.duration))
        name = p.name
        if abs(src.duration - info.duration) > 1.0:
            bad(f"{name}: duration {info.duration:.1f}s doesn't match any original")
        for kind in ("video", "audio"):
            if info.count(kind) != src.count(kind):
                bad(f"{name}: {info.count(kind)} {kind} streams, the original had {src.count(kind)}")
        if re.search(r"\d{3,4}p|BluRay|WEB-DL", name):
            bad(f"{name}: not renamed")
        defaults = [t for t in info.subs if t.default]
        if len(defaults) != 1:
            bad(f"{name}: {len(defaults)} default subtitle tracks")
            continue
        d = defaults[0]
        if media.lang_short(d.language) != "en" or d.title != "English":
            bad(f"{name}: the default track is {d.language} “{d.title}”, not English")
        others = [t for t in info.subs if media.lang_short(t.language) not in ("en", "") and not t.forced]
        if others:
            bad(f"{name}: other-language tracks left: {[t.language for t in others]}")
        src_sdh = [t for t in src.subs if t.sdh_flagged]
        src_plain = [t for t in src.subs if media.lang_short(t.language) == "en" and not t.sdh_flagged]
        sdh_now = [t for t in info.subs if t.sdh_flagged]
        if src_plain and src_sdh and not sdh_now:
            bad(f"{name}: had a regular and an SDH English track; the SDH track should have been kept")
        if not src_plain and sdh_now:
            bad(f"{name}: only had an SDH track; it should have been replaced by the cleaned one")
        with tempfile.TemporaryDirectory() as tmp:
            paths = media.extract_subs(tools, info, [d], tmp, None, None, None)
            doc = sf.load(paths[d.index], d.fmt)
            tags = [c.text for c in doc.cues if SOUND_TAG.search(c.text)]
            notes = [c.text for c in doc.cues if MUSIC.search(c.text)]
            if src_sdh and not src_plain and (tags or notes):
                bad(f"{name}: {len(tags)} sound tags and {len(notes)} ♪ left in the cleaned track, e.g. {(tags or notes)[0]!r}")
            checked.append(f"{name}: {len(info.subs)} subtitle tracks, default “{d.title}” ({len(doc.cues)} lines)")
    # an SDH sidecar stays when the video's regular track was the one cleaned (the SDH track is kept, renamed with
    # its video); anything else next to the videos is a leftover
    stems = {Path(o).stem.lower() for o in outputs}
    left = [f.name for f in library.iterdir() if ".subscrub-tmp" in f.name or f.suffix.lower() in (".srt", ".ass", ".vtt")
            and not (".sdh." in f.name.lower() and any(f.name.lower().startswith(s + ".") for s in stems))]
    if left:
        bad(f"left in the folder: {left}")
    print("\n".join(checked))
    if problems:
        print("\nPROBLEMS:\n  " + "\n  ".join(problems))
        return 1
    print(f"\nAll {len(outputs)} saved videos check out ({qa.get('theme')}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
