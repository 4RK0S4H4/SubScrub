"""Run the cleaner over a subtitle dump made by tools/extract-subs.ps1 and report what it would do.

    python tools/corpus_check.py F:/subdump            (or an unzipped copy of subdump.zip)
    python tools/corpus_check.py F:/subdump --show 40  (print more examples per section)

For every English SDH track it prints the confidence SubScrub would give the file, then lists
  * flagged lines (judgement calls the app would ask about),
  * leftovers that still look like SDH after cleaning (possible misses),
so new rules can be tested against a real library before they ship.
"""
from __future__ import annotations

import argparse
import collections
import csv
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from subscrub import subformats as sf  # noqa: E402
from subscrub.cleaner import CleanOptions, clean_doc, visible_text  # noqa: E402

NON_ENGLISH = re.compile(r"filipino|malay|melayu|bahasa|indonesian|thai|vietnam|chinese|simplified|traditional|"
                         r"spanish|french|portug|ital|german|korean|japanese|arabic|hindi|tamil|telugu", re.I)
LEFTOVER = {
    "brackets": re.compile(r"[\[\]]"),
    "parentheses": re.compile(r"[()]"),
    "music notes": re.compile(r"[♪♫#]"),
    "speaker label": re.compile(r"(^|\s|-)[A-Z][A-Z .'\"]{1,30}:(?!\d)"),
    "dialogue dash": re.compile(r"^\s*-(?!-)"),
}


def resolve(root: str, rel: str) -> str | None:
    p = os.path.join(root, *rel.replace("\\", "/").split("/"))
    if os.path.exists(p):
        return p
    parts = rel.replace("\\", "/").split("/")
    q = os.path.join(root, *[x.rstrip(". ") for x in parts[:-1]], parts[-1])
    return q if os.path.exists(q) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("--show", type=int, default=15)
    a = ap.parse_args()
    rows = list(csv.DictReader(open(os.path.join(a.dump, "manifest.csv"), encoding="utf-8-sig")))
    videos = collections.OrderedDict()
    for r in rows:
        if r["Language"].split("-")[0].lower() not in ("en", "eng", "und", ""):
            continue
        r["path"] = resolve(a.dump, r["File"])
        if r["path"]:
            videos.setdefault(r["Video"], []).append(r)

    opts = CleanOptions()
    conf = collections.Counter()
    flagged, leftovers = [], collections.defaultdict(list)
    for video, tracks in videos.items():
        sdh = [t for t in tracks if t["SDH"] == "True" and t["Forced"] != "True" and "comment" not in t["Name"].lower()]
        plain = [t for t in tracks if t["SDH"] != "True" and t["Forced"] != "True" and "forced" not in t["Name"].lower()
                 and "comment" not in t["Name"].lower() and not NON_ENGLISH.search(t["Name"])]
        for t in sdh:
            try:
                doc = sf.load(t["path"])
                ref = sf.load(plain[0]["path"]) if plain else None
            except Exception as e:  # noqa: BLE001
                print("can't read", t["path"], e)
                continue
            res = clean_doc(doc, opts, plain=ref)
            conf[res.confidence] += 1
            print(f"{res.confidence:5}  {len(res.flagged()):3} to check  {res.stats.changed:5}/{res.stats.total:5} "
                  f"changed   {video}  [{t['Name'] or 'unnamed'}]")
            for i in res.flagged():
                flagged.append((video, doc.cues[i].text, res.results[i].text, res.results[i].why))
            for c, r in zip(doc.cues, res.results):
                if r.text is None:
                    continue
                vis = visible_text(r.text, doc.fmt)
                for name, pat in LEFTOVER.items():
                    if any(pat.search(line) for line in vis.split("\n")):
                        leftovers[name].append((video, c.text, r.text))
    print("\nConfidence:", dict(conf))
    print(f"\n=== {len(flagged)} flagged lines")
    for v, src, out, why in flagged[:a.show]:
        print(f"  {os.path.basename(v)[:30]:30} {src!r:.70} -> {out!r:.50}  ({'; '.join(why)[:80]})")
    for name, items in leftovers.items():
        print(f"\n=== {len(items)} lines still containing {name}")
        for v, src, out in items[:a.show]:
            print(f"  {os.path.basename(v)[:30]:30} {src!r:.70} -> {out!r:.60}")


if __name__ == "__main__":
    main()
