"""Make test videos with SDH subtitles to try SubScrub on without touching real files.

    python -m tests.make_samples              ->  samples/Sample (SDH test).mkv / .mp4
    python -m tests.make_samples --library    ->  samples/library/: six episode- and movie-length videos with
                                                  long, made-up SDH tracks in every state the UI shows
                                                  (ready, lines to check, look closer, regular subtitles only,
                                                  a .srt next to the video, other-language tracks)

Everything is generated: the dialogue is invented, the video is a black frame. Needs ffmpeg.
"""
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

from subscrub import media

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"


def _runner(tools):
    kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    return lambda cmd: subprocess.run(cmd, check=True, **kw)


def main():
    tools = media.find_tools()
    if not tools.ffmpeg:
        sys.exit("FFmpeg not found - run install-tools.bat first.")
    if "--library" in sys.argv:
        library(tools)
        return
    if "--edge" in sys.argv:
        edge_cases(tools, ROOT / "samples" / "edge")
        return
    out = ROOT / "samples"
    out.mkdir(exist_ok=True)
    mkv, mp4 = pipeline_samples(tools, out)
    print(f"Made:\n  {mkv}\n  {mp4}")


_CHAPTERS = """;FFMETADATA1
[CHAPTER]
TIMEBASE=1/1000
START=0
END=30000
title=One
[CHAPTER]
TIMEBASE=1/1000
START=30000
END=60000
title=Two
"""


def pipeline_samples(tools, out: Path) -> tuple[Path, Path]:
    """The two videos tests.test_pipeline runs on: an MKV with a regular and an SDH English track, a Spanish ASS
    track, a font attachment and chapters (what the save has to carry over), and an MP4 with a regular and an SDH
    track."""
    out.mkdir(parents=True, exist_ok=True)
    run = _runner(tools)
    av = out / "_av.mp4"
    run([tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "60", "-c:v", "libx264",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(av)])
    chap = out / "_chapters.txt"
    chap.write_text(_CHAPTERS, encoding="utf-8")
    font = out / "SubScrub Test.ttf"
    font.write_bytes(bytes(range(256)) * 16)          # not a real font: the attachment only has to survive
    mkv = out / "Sample (SDH test).mkv"
    run([tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(av),
         "-i", str(FIX / "plain_en.srt"), "-i", str(FIX / "sdh_en.srt"), "-i", str(FIX / "sdh_es.ass"), "-i", str(chap),
         "-map", "0", "-map", "1", "-map", "2", "-map", "3", "-map_metadata", "4", "-map_chapters", "4", "-c", "copy",
         "-metadata:s:s:0", "language=eng", "-metadata:s:s:0", "title=English",
         "-metadata:s:s:1", "language=eng", "-metadata:s:s:1", "title=English SDH",
         "-metadata:s:s:2", "language=spa", "-metadata:s:s:2", "title=Español (SDH)",
         "-disposition:s:0", "0", "-disposition:s:1", "default+hearing_impaired", "-disposition:s:2", "0",
         "-attach", str(font), "-metadata:s:t:0", "mimetype=font/ttf", str(mkv)])
    mp4 = out / "Sample (SDH test).mp4"
    run([tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(av), "-i", str(FIX / "plain_en.srt"),
         "-i", str(FIX / "sdh_en.srt"), "-map", "0", "-map", "1", "-map", "2", "-c", "copy", "-c:s", "mov_text",
         "-metadata:s:s:0", "language=eng", "-metadata:s:s:0", "handler_name=English",       # MP4 keeps names here
         "-metadata:s:s:1", "language=eng", "-metadata:s:s:1", "handler_name=English SDH",
         "-disposition:s:0", "0", "-disposition:s:1", "default", str(mp4)])
    for f in (av, chap, font):
        f.unlink()
    return mkv, mp4


# ============================================================================ edge cases

def _tiny_video(run, tools, out: Path, subs: list[tuple[Path, str, str, str]], seconds: int = 20):
    """A short black-frame MKV with subtitle tracks (file, language, title, disposition); none when `subs` is empty."""
    cmd = [tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
           f"color=c=black:s=160x90:r=1:d={seconds}", "-f", "lavfi", "-i", f"anullsrc=r=8000:cl=mono:d={seconds}"]
    for f, *_ in subs:
        cmd += ["-i", str(f)]
    cmd += ["-map", "0:v", "-map", "1:a"] + [x for k in range(len(subs)) for x in ("-map", f"{k + 2}")]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "8k", "-c:s", "srt"]
    for k, (_, lang, title, disp) in enumerate(subs):
        cmd += [f"-metadata:s:s:{k}", f"language={lang}", f"-metadata:s:s:{k}", f"title={title}",
                f"-disposition:s:{k}", disp]
    run(cmd + ["-shortest", str(out)])


def _cues(path: Path, texts: list[str], encoding: str = "utf-8"):
    path.write_bytes("".join(f"{i}\n{_ts(2 * i)} --> {_ts(2 * i + 1.5)}\n{t}\n\n" for i, t in enumerate(texts, 1))
                     .encode(encoding, errors="replace"))


SDH_TEXTS = ["[door creaks]", "JOHN: Where were you?", "- Home.\n- [chuckles] Sure.", "(sighs) Fine.", "♪ tense music ♪",
             "I told you not to come here.", "[phone buzzing]", "Café is closed."]


def edge_cases(tools, out: Path) -> dict[str, Path]:
    """Awkward inputs for tests.test_edge. Returns {case: path} (the video, or the folder for sidecar cases)."""
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    tmp = out / "_tmp"
    tmp.mkdir()
    run = _runner(tools)
    cases: dict[str, Path] = {}
    sdh, plain, empty = tmp / "sdh.srt", tmp / "plain.srt", tmp / "empty.srt"
    _cues(sdh, SDH_TEXTS)
    _cues(plain, [t for t in SDH_TEXTS if t[0] not in "[(♪" and ":" not in t[:6]])
    empty.write_text("", encoding="utf-8")

    def make(name: str, subs) -> Path:
        d = out / name
        d.mkdir()
        p = d / f"{name}.mkv"
        _tiny_video(run, tools, p, subs)
        cases[name] = p
        return p

    make("no subtitles", [])
    make("two sdh", [(sdh, "eng", "English SDH", "default+hearing_impaired"),
                     (sdh, "eng", "English (HI)", "hearing_impaired")])
    all_sdh = tmp / "all.srt"
    _cues(all_sdh, ["[door creaks]", "(sighs)", "[phone buzzing]", "♪♪", "[music fades]"])
    make("every line is sdh", [(all_sdh, "eng", "English SDH", "default+hearing_impaired")])
    # a flagged SDH track with no cues in it: mkvmerge takes an empty .srt where FFmpeg refuses
    if tools.mkvmerge:
        base = tmp / "base.mkv"
        _tiny_video(run, tools, base, [])
        d = out / "empty sdh"
        d.mkdir()
        p = d / "empty sdh.mkv"
        run([tools.mkvmerge, "-q", "-o", str(p), str(base), "--language", "0:eng", "--track-name", "0:English SDH",
             "--hearing-impaired-flag", "0:1", "--default-track-flag", "0:1", str(empty)])
        cases["empty sdh"] = p
    # picture subtitles only: the codec id of a text track is rewritten to PGS (same length, so the file stays valid)
    p = make("picture subtitles only", [(sdh, "eng", "English", "default")])
    data = p.read_bytes()
    if b"S_TEXT/UTF8" in data:
        p.write_bytes(data.replace(b"S_TEXT/UTF8", b"S_HDMV/PGS\0"))
    # a name with characters that trip up command lines, paths and XML
    make("It's & 100% [Rock] Café 🎬", [(sdh, "eng", "English SDH", "default+hearing_impaired")])
    # subtitle files next to a video: empty, UTF-16 and cp1252
    for name, writer in (("zero byte sidecar", lambda f: f.write_bytes(b"")),
                         ("utf-16 sidecar", lambda f: _cues(f, SDH_TEXTS, "utf-16")),
                         ("cp1252 sidecar", lambda f: _cues(f, SDH_TEXTS, "cp1252"))):
        d = out / name
        d.mkdir()
        v = d / f"{name}.mkv"
        _tiny_video(run, tools, v, [])
        writer(d / f"{name}.eng.sdh.srt")
        cases[name] = v
    shutil.rmtree(tmp)
    return cases


# ============================================================================ the library

LINES = [
    "We need to move.", "Keep your voice down.", "You're late again.", "Where were you last night?",
    "I told you not to come here.", "It's not what it looks like.", "Then what is it?", "Give me a minute.",
    "We don't have a minute.", "The door was already open.", "Who else knows?", "Nobody. I swear.",
    "Stay close.", "Get down!", "Is anyone there?", "I think we're being followed.", "Morning.",
    "The floor closes at six.", "Did you read the report?", "Every word of it.", "That's impossible.",
    "Then explain the numbers.", "I can't.", "We should call someone.", "Not yet.", "Listen to me.",
    "This was never part of the plan.", "Plans change.", "How long do we have?", "Less than an hour.",
    "Put that down.", "You're not supposed to be here.", "Neither are you.", "Fair enough.",
    "Can you hear me?", "Loud and clear.", "Hold on.", "Where's the key?", "In the drawer, under the maps.",
    "I'll be right back.", "Don't do anything stupid.", "Too late.", "Are you okay?", "I'm fine.",
    "We were never here.", "Say that again.", "Let's go.", "Open it.", "What did he say?", "Nothing useful.",
]
NAMES = ["JOHN", "MARA", "HELEN", "DAVID", "MAN", "WOMAN", "OFFICER", "DOCTOR", "MAN (ON RADIO)", "WOMAN (ON PHONE)"]
SOUNDS = ["door creaks", "footsteps approaching", "phone buzzing", "engine starts", "alarm blaring", "glass shatters",
          "elevator dings", "keyboard clacking", "distant siren", "door slams", "dog barking", "rain pattering",
          "indistinct chatter", "gunshot in the distance", "thunder rumbling", "breathing heavily"]
PAREN = ["sighs", "chuckles", "gasps", "whispering", "groans", "laughs", "SNIFFS", "CLEARS THROAT", "IN SPANISH"]
MUSIC = ["tense music playing", "soft piano music", "upbeat pop music playing", "ominous music", "music fades"]
LYRICS = ["The stars are falling down", "Walking home in the rain", "Hold on to the night", "Nobody knows my name"]
SIGNS = ["RESTRICTED", "EXIT", "LEVEL 4", "NO ENTRY", "ARCHIVE"]
RISKY = ["Don't look back.", "Not now.", "Trust me.", "Run.", "Wait for it.", "Keep going.", "Say nothing."]


def _ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _cue(rng: random.Random, sdh: bool) -> str:
    """One cue: dialogue, plus the kinds of SDH notes real tracks carry."""
    line = rng.choice(LINES)
    if not sdh:
        return line if rng.random() > 0.15 else f"- {line}\n- {rng.choice(LINES)}"
    r = rng.random()
    if r < 0.12:
        return f"[{rng.choice(SOUNDS)}]"
    if r < 0.17:
        return f"♪ {rng.choice(MUSIC)} ♪"
    if r < 0.20:
        return f"♪ {rng.choice(LYRICS)} ♪"
    if r < 0.32:
        return f"{rng.choice(NAMES)}: {line}"
    if r < 0.42:
        return f"- [{rng.choice(SOUNDS)}]\n- {line}"
    if r < 0.52:
        return f"({rng.choice(PAREN)}) {line}"
    if r < 0.60:
        return f"- {line}\n- {rng.choice(LINES)}"
    if r < 0.62:
        return f"[sign reads: {rng.choice(SIGNS)}]"
    if r < 0.70:
        return f"{line} [{rng.choice(SOUNDS)}]"
    return line


def _srt(path: Path, minutes: float, seed: int, sdh: bool = True, risky: int = 0, gap: float = 3.4):
    """A subtitle track as long as the video; `risky` lines with dialogue in (parentheses) to check."""
    rng = random.Random(seed)
    t, cues = 4.0, []
    end = minutes * 60 - 5
    while t < end:
        dur = rng.uniform(1.4, 3.2)
        cues.append([t, t + dur, _cue(rng, sdh)])
        t += dur + rng.uniform(0.3, gap)
    for k in range(risky):                       # spread the judgement calls through the episode
        c = cues[int((k + 0.5) / risky * len(cues))]
        c[2] = f"({rng.choice(RISKY)}) {rng.choice(LINES)}"
    path.write_text("".join(f"{i}\n{_ts(a)} --> {_ts(b)}\n{text}\n\n" for i, (a, b, text) in enumerate(cues, 1)),
                    encoding="utf-8")


def _video(run, tools, out: Path, minutes: float, subs: list[tuple[Path, str, str, str]]):
    """A black-frame MKV of the given length with subtitle tracks (file, language, title, disposition)."""
    cmd = [tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
           f"color=c=black:s=160x90:r=1:d={minutes * 60:.0f}", "-f", "lavfi", "-i",
           f"anullsrc=r=8000:cl=mono:d={minutes * 60:.0f}"]
    for f, *_ in subs:
        cmd += ["-i", str(f)]
    cmd += ["-map", "0:v", "-map", "1:a"] + [x for k in range(len(subs)) for x in ("-map", f"{k + 2}")]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage", "-c:a", "aac", "-b:a", "8k",
            "-c:s", "srt"]
    for k, (_, lang, title, disp) in enumerate(subs):
        cmd += [f"-metadata:s:s:{k}", f"language={lang}", f"-metadata:s:s:{k}", f"title={title}",
                f"-disposition:s:{k}", disp]
    run(cmd + ["-shortest", str(out)])


def library(tools):
    out = ROOT / "samples" / "library"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    tmp = out / "_tmp"
    tmp.mkdir()
    run = _runner(tools)

    def sub(name, minutes, seed, **kw):
        p = tmp / f"{name}.srt"
        _srt(p, minutes, seed, **kw)
        return p

    # 1. ready: an English SDH track, the regular English track and one other language
    m = 49.2
    _video(run, tools, out / "The.Expanse.S01E03.Remember.the.Cant.1080p.BluRay.x264.mkv", m, [
        (sub("exp_plain", m, 11, sdh=False), "eng", "English", "0"),
        (sub("exp_sdh", m, 11), "eng", "English SDH", "default+hearing_impaired"),
        (sub("exp_spa", m, 12, sdh=False), "spa", "Español", "0")])
    # 2. three lines to check, two other languages
    m = 52.1
    _video(run, tools, out / "Severance.S02E04.Woes.Hollow.2160p.ATVP.WEB-DL.DDP5.1.mkv", m, [
        (sub("sev_plain", m, 21, sdh=False), "eng", "English", "0"),
        (sub("sev_sdh", m, 21, risky=3), "eng", "English SDH", "default+hearing_impaired"),
        (sub("sev_spa", m, 22, sdh=False), "spa", "Español (Latinoamérica)", "0"),
        (sub("sev_fre", m, 23, sdh=False), "fre", "Français", "0")])
    # 3. SDH as a .srt next to the video (one file per video), regular English inside
    m = 47.6
    v = out / "Slow.Horses.S04E01.Identity.Theft.1080p.ATVP.WEB-DL.mkv"
    _video(run, tools, v, m, [(sub("sh_plain", m, 31, sdh=False), "eng", "English", "default")])
    _srt(out / (v.stem + ".eng.sdh.srt"), m, 31)
    # 4. regular subtitles only: nothing to strip
    m = 58.0
    _video(run, tools, out / "Planet.Earth.III.S01E02.Ocean.2160p.iP.WEB-DL.mkv", m, [
        (sub("pe_plain", m, 41, sdh=False), "eng", "English", "default")])
    # 5. a long film, ready
    m = 166.0
    _video(run, tools, out / "Dune.Part.Two.2024.2160p.UHD.BluRay.x265.mkv", m, [
        (sub("dune_sdh", m, 51, gap=5.0), "eng", "English SDH", "default+hearing_impaired")])
    # 6. lots of judgement calls: "look closer"
    m = 117.0
    _video(run, tools, out / "Blade.Runner.1982.The.Final.Cut.1080p.BluRay.mkv", m, [
        (sub("br_sdh", m, 61, risky=90, gap=4.0), "eng", "English SDH", "default+hearing_impaired")])
    shutil.rmtree(tmp)
    print("Made:\n" + "\n".join(f"  {p}" for p in sorted(out.iterdir())))


if __name__ == "__main__":
    main()
