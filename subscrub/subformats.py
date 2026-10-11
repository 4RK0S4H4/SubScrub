"""Read and write text subtitle formats (SRT, ASS/SSA, WebVTT) without losing anything.

Each format is parsed into a SubtitleDoc: a list of cues plus whatever surrounds them
(headers, styles, comments). Cue text is stored with lines joined by "\n" (ASS "\\N"
breaks are converted), and rendering puts everything back byte-for-byte except the cues
that were changed or removed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Cue:
    start: str
    end: str
    text: str                 # lines joined with "\n"
    # format-specific bits needed to write the cue back
    extra: dict = field(default_factory=dict)


@dataclass
class SubtitleDoc:
    fmt: str                  # "srt" | "ass" | "vtt"
    cues: list[Cue]
    # For ass/vtt: ordered items, each ("raw", str) or ("cue", cue_index)
    items: list[tuple] = field(default_factory=list)
    newline: str = "\n"

    def render(self, texts: list[str | None]) -> str:
        """Render the document with replacement cue texts (None = drop the cue)."""
        if len(texts) != len(self.cues):
            raise ValueError("texts must match cues")
        if self.fmt == "srt":
            return _render_srt(self, texts)
        if self.fmt == "vtt":
            return _render_vtt(self, texts)
        if self.fmt == "ass":
            return _render_ass(self, texts)
        raise ValueError(self.fmt)


# --------------------------------------------------------------------------- reading

def read_text_file(path: str | Path) -> str:
    data = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-16"):
        if enc == "utf-16" and not data[:2] in (b"\xff\xfe", b"\xfe\xff"):
            continue
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def load(path: str | Path, fmt: str | None = None) -> SubtitleDoc:
    text = read_text_file(path)
    if fmt is None:
        fmt = detect_format(text, str(path))
    return parse(text, fmt)


def detect_format(text: str, name: str = "") -> str:
    head = text.lstrip()[:400]
    if head.startswith("WEBVTT"):
        return "vtt"
    if "[Script Info]" in head or re.search(r"^\[V4\+? Styles\]", text, re.M):
        return "ass"
    low = name.lower()
    if low.endswith((".ass", ".ssa")):
        return "ass"
    if low.endswith(".vtt"):
        return "vtt"
    return "srt"


def parse(text: str, fmt: str) -> SubtitleDoc:
    newline = "\r\n" if "\r\n" in text[:5000] else "\n"
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if fmt == "srt":
        doc = _parse_srt(text)
    elif fmt == "vtt":
        doc = _parse_vtt(text)
    elif fmt == "ass":
        doc = _parse_ass(text)
    else:
        raise ValueError(f"Unsupported subtitle format: {fmt}")
    doc.newline = newline
    return doc


# --------------------------------------------------------------------------- SRT

_SRT_TIME = re.compile(
    r"^\s*(\d{1,3}:\d{1,2}:\d{1,2}[,.]\d{1,3})\s*-->\s*(\d{1,3}:\d{1,2}:\d{1,2}[,.]\d{1,3})(.*)$"
)


def _parse_srt(text: str) -> SubtitleDoc:
    cues: list[Cue] = []
    lines_acc: list[list[str]] = []
    for line in text.split("\n"):
        m = _SRT_TIME.match(line)
        if m:
            # The line just before a timing line is the cue number: drop it from the
            # previous cue (only when it sits after a blank line or at the very top).
            if lines_acc:
                prev = lines_acc[-1]
                if prev and prev[-1].strip().isdigit() and (len(prev) == 1 or not prev[-2].strip()):
                    prev.pop()
            cues.append(Cue(m.group(1), m.group(2), "", {"settings": m.group(3)}))
            lines_acc.append([])
            continue
        if lines_acc:
            lines_acc[-1].append(line)
    for cue, lines in zip(cues, lines_acc):
        cue.text = "\n".join(l.rstrip() for l in lines if l.strip())
    return SubtitleDoc("srt", cues)


def _render_srt(doc: SubtitleDoc, texts: list[str | None]) -> str:
    out = []
    n = 0
    for cue, t in zip(doc.cues, texts):
        if t is None or not t.strip():
            continue
        n += 1
        out.append(f"{n}\n{cue.start} --> {cue.end}{cue.extra.get('settings', '')}\n{t}\n")
    return "\n".join(out).replace("\n", doc.newline)


# --------------------------------------------------------------------------- WebVTT

_VTT_TIME = re.compile(r"^\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})(.*)$")


def _parse_vtt(text: str) -> SubtitleDoc:
    blocks = re.split(r"\n[ \t]*\n", text)
    doc = SubtitleDoc("vtt", [])
    for i, block in enumerate(blocks):
        if not block.strip():
            continue
        lines = block.split("\n")
        # skip leading empty lines inside a block (from multiple blank lines)
        while lines and not lines[0].strip():
            lines.pop(0)
        if i == 0 or lines[0].startswith(("NOTE", "STYLE", "REGION", "WEBVTT")):
            doc.items.append(("raw", "\n".join(lines)))
            continue
        tline = 0
        if not _VTT_TIME.match(lines[0]) and len(lines) > 1 and _VTT_TIME.match(lines[1]):
            tline = 1
        m = _VTT_TIME.match(lines[tline])
        if not m:
            doc.items.append(("raw", "\n".join(lines)))
            continue
        ident = lines[0] if tline == 1 else None
        cue = Cue(m.group(1), m.group(2), "\n".join(lines[tline + 1:]),
                  {"settings": m.group(3), "id": ident})
        doc.items.append(("cue", len(doc.cues)))
        doc.cues.append(cue)
    return doc


def _render_vtt(doc: SubtitleDoc, texts: list[str | None]) -> str:
    out = []
    for kind, val in doc.items:
        if kind == "raw":
            out.append(val)
            continue
        cue = doc.cues[val]
        t = texts[val]
        if t is None or not t.strip():
            continue
        head = f"{cue.extra['id']}\n" if cue.extra.get("id") else ""
        out.append(f"{head}{cue.start} --> {cue.end}{cue.extra.get('settings', '')}\n{t}")
    return ("\n\n".join(out) + "\n").replace("\n", doc.newline)


# --------------------------------------------------------------------------- ASS / SSA

def _parse_ass(text: str) -> SubtitleDoc:
    doc = SubtitleDoc("ass", [])
    section = ""
    fmt_fields: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped.lower()
            doc.items.append(("raw", line))
            continue
        if section == "[events]":
            if stripped.lower().startswith("format:"):
                fmt_fields = [f.strip().lower() for f in stripped.split(":", 1)[1].split(",")]
                doc.items.append(("raw", line))
                continue
            if stripped.startswith("Dialogue:") and fmt_fields:
                body = line.split(":", 1)[1]
                lead = body[: len(body) - len(body.lstrip())]
                parts = body.lstrip().split(",", len(fmt_fields) - 1)
                if len(parts) == len(fmt_fields):
                    rec = dict(zip(fmt_fields, parts))
                    prefix = "Dialogue:" + lead + ",".join(parts[:-1]) + ","
                    cue = Cue(rec.get("start", ""), rec.get("end", ""),
                              parts[-1].replace("\\N", "\n"), {"prefix": prefix, "style": rec.get("style", "")})
                    doc.items.append(("cue", len(doc.cues)))
                    doc.cues.append(cue)
                    continue
        doc.items.append(("raw", line))
    return doc


def _render_ass(doc: SubtitleDoc, texts: list[str | None]) -> str:
    out = []
    for kind, val in doc.items:
        if kind == "raw":
            out.append(val)
            continue
        t = texts[val]
        if t is None:
            continue
        out.append(doc.cues[val].extra["prefix"] + t.replace("\n", "\\N"))
    return "\n".join(out).replace("\n", doc.newline)


def to_seconds(t: str) -> float:
    """'01:02:03,450' / '1:02:03.45' / '02:03.450' -> seconds"""
    m = re.match(r"\s*(?:(\d+):)?(\d+):(\d+)(?:[.,](\d+))?", t or "")
    if not m:
        return 0.0
    h, mi, s = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    frac = m.group(4) or "0"
    return h * 3600 + mi * 60 + s + int(frac) / (10 ** len(frac))
