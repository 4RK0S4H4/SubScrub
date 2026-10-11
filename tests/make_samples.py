"""Make a small test video with SDH subtitles to try SubScrub on without touching real files.

    .venv\\Scripts\\python -m tests.make_samples      ->  samples\\Sample (SDH test).mkv / .mp4
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from subscrub import media

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"


def main():
    tools = media.find_tools()
    if not tools.ffmpeg:
        sys.exit("FFmpeg not found - run install-tools.bat first.")
    out = ROOT / "samples"
    out.mkdir(exist_ok=True)
    av = out / "_av.mp4"
    kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    run = lambda cmd: subprocess.run(cmd, check=True, **kw)
    run([tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "60", "-c:v", "libx264",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(av)])
    mkv = out / "Sample (SDH test).mkv"
    run([tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(av),
         "-i", str(FIX / "plain_en.srt"), "-i", str(FIX / "sdh_en.srt"), "-i", str(FIX / "sdh_es.ass"),
         "-map", "0", "-map", "1", "-map", "2", "-map", "3", "-c", "copy",
         "-metadata:s:s:0", "language=eng", "-metadata:s:s:0", "title=English",
         "-metadata:s:s:1", "language=eng", "-metadata:s:s:1", "title=English SDH",
         "-metadata:s:s:2", "language=spa", "-metadata:s:s:2", "title=Español (SDH)",
         "-disposition:s:0", "0", "-disposition:s:1", "default+hearing_impaired", "-disposition:s:2", "0",
         str(mkv)])
    mp4 = out / "Sample (SDH test).mp4"
    run([tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(av), "-i", str(FIX / "sdh_en.srt"),
         "-map", "0", "-map", "1", "-c", "copy", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng",
         "-disposition:s:0", "default", str(mp4)])
    av.unlink()
    print(f"Made:\n  {mkv}\n  {mp4}")


if __name__ == "__main__":
    main()
