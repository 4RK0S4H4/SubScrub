# SubScrub

A Windows desktop app (Python 3.11+, PySide6/Qt) that strips non-dialogue SDH text from subtitle tracks, such as
`[door creaks]`, `JOHN:` and `♪ music ♪`. It works on MKV and other videos and on standalone .srt/.ass/.vtt files.
The cleaned track goes back into the video as the default, and the SDH track is dropped. Optionally it also renames
videos from TMDB (`The Walking Dead S7 E2.mkv`) and removes other-language subtitle tracks. It never re-encodes
video. FFmpeg and MKVToolNix do the muxing.

Current version: see `subscrub/__init__.py` (2.7.3 at hand-off). The history of every change is in `README.md`
("What's new") and `docs/HISTORY.md`.

## How we work together

The user is the product manager. You are the whole development team.

- **The user describes, you execute.** They give features and bugs in plain English, often with a phone screenshot.
  You do all coding, installing, building, testing, committing, merging and releasing. Never ask them to run
  commands, install anything or edit files.
- **Branch, PR, merge yourself.** Work on a branch, open a PR and let the `build` workflow pass. Small verified
  changes you merge without asking. After a release, give them the GitHub Release link. That is where they
  download `SubScrub.exe` (it replaces the old "run build.bat" step).
- **Backlog and batching.** When they say "log it", "add to the list", or ask you not to execute yet, file each
  request or bug as a GitHub issue (`bug` / `enhancement`), reply with the links, and stop. When they say "go" or
  "do the batch", do every open issue in one branch and PR, run the checks once, merge, close the issues and send
  the release link.
- **Ask before deciding what's theirs to decide.** Cleaning rules, naming formats and anything that changes their
  files are confirmed with them and recorded in `docs/OPEN-ITEMS.md`. Never silently change a rule below.
- **Short, plain-language replies.** Say what changed, what they need to do (usually nothing) and the link. Say
  honestly what wasn't verified, for example "not run on a real Windows PC" or "TMDB not called for real".
- **When feedback says "not quite", change only that.** Keep everything else stable.
- **Design work follows the user's S4 Studio skill** (load it if it's available in this session). The look is
  already decided (see "Look" below). For colour questions, publish a switchable HTML sample instead of static
  images. Every sample page must work on a phone.

## Commands

```
python -m pip install -r requirements.txt pyinstaller pillow   # dev setup (any OS)
python run.py                                                   # run the app (Windows for the real thing)

# tests (set QT_QPA_PLATFORM=offscreen on a machine without a display)
python -m tests.test_cleaner
python -m tests.test_batch
python -m tests.test_naming
python -m tests.test_swap
python -m tests.test_library
python -m tests.test_pipeline <folder of sample videos>         # needs ffmpeg + mkvmerge, see docs/OPEN-ITEMS.md

python -m tests.make_samples                                    # makes samples/Sample (SDH test).mkv/.mp4 (ffmpeg)
python tools/ui_screenshots.py <out> <video folder> dark|light [--save]   # 1080p screenshots for the visual check
build.bat                                                       # local Windows build -> dist\SubScrub.exe
```

CI: `.github/workflows/build.yml` runs the tests and builds `SubScrub.exe` on `windows-latest` for every push and
PR (the exe is attached to the run). Pushing a `vX.Y.Z` tag also publishes a GitHub Release with the exe. To ship
a version: bump `subscrub/__init__.py`, add a "What's new" entry to README.md, merge, then tag `vX.Y.Z` and push
the tag.

## Layout

- `subscrub/cleaner.py`: the cleaning rules, per-line risk grading ("to check"), OCR fixes, `Name:` detection, and
  a cross-check against the plain subtitle track in the same file.
- `subscrub/subformats.py`: SRT/ASS/VTT read and write.
- `subscrub/media.py`: probing, extracting and muxing with FFmpeg/MKVToolNix. Also the safe swap (the new file is
  verified before the original goes to the Recycle Bin), the "files in use" check (Windows Restart Manager) and
  the track tidy-up.
- `subscrub/naming.py`: TMDB lookup and scoring, Windows-safe names, and the rename history used for undo
  (`%LOCALAPPDATA%\SubScrub\renames.jsonl`).
- `subscrub/batch.py`: queue items, track picking, sidecar `.srt` pairing, rename helpers.
- `subscrub/app.py`: entry point. Loads the bundled Inter font.
- `subscrub/ui/main_window.py`: welcome, file list, review (sidebar plus preview), settings and the dialogs.
- `subscrub/ui/preview.py`: the caption "screen", the timeline and the before/after list.
- `subscrub/ui/theme.py`: colour tokens (DARK/LIGHT), the style sheet, fonts, `num_font()`, the title-bar colour.
- `subscrub/ui/glass.py`: window backdrop, card panels, optional Windows 11 Mica.
- `subscrub/ui/widgets.py`: rows, badges (`StatusTag`), the segmented control, toggles, Solar icon drawing.
- `subscrub/ui/solar.py`: Solar icon SVGs (CC BY 4.0). `subscrub/ui/fonts.py`: Inter as base64 (OFL).
- `tests/`: the test suites and fixtures. `tools/`: screenshot driver, corpus checker, PowerShell extractor.

## Rules the user decided (don't change without asking)

- Strip: sounds in [brackets] and (parentheses), speaker names, music cues, on-screen-text notes
  (`[sign reads: EXIT]` becomes `EXIT`) and sound words on their own line.
- Song lyrics stay, in italics, without ♪. Dialogue dashes are always removed. Never reflow or merge lines.
- Save defaults: replace the original, make the cleaned track the default, drop the SDH track. The original goes
  to the Recycle Bin only after the new file checks out.
- Uncertain lines: ask at save time (review / best guess / leave alone / skip). Never learn from corrections.
- No whole-library mode. Only files the user picks.
- A folder of videos with matching `.srt` sidecars becomes one file per video, with the cleaned subtitle inside.
- Warn up front about files another program has open (seeding torrents), before anything is changed.
- Renaming: TMDB, no year by default (an option), no folders, pure rename. Episodes look like `Show S7 E2.mkv`.
  The cleaned track is named just "English". Non-English subtitle tracks are deleted unless they are forced
  (und tracks are kept). The user doesn't use Radarr/Sonarr. The TMDB key is entered in Settings; never commit one.
- English tracks only matter to the user.

## Look (S4 Studio style, decided 2026-10-10)

- One accent, crimson `#C81E3A` (accent-2 `#9F1239`, light `#FDA4AF`). Everything else is monochrome: neutral greys,
  no red-tinted surfaces, and only a faint neutral bloom in dark mode.
- Crimson is used only for the primary button, switches, the selected segment or track, and things that need
  checking. Status badges: ok green, warn amber, attention red.
- Inter (bundled) with `ss01` and `cv09` app-wide. Tabular figures (`num_font()`) go on counts and timecodes only:
  Inter's `tnum` widens hyphens.
- Solar icons. 9–12px corners, hairline borders, soft shadows. The primary button glows only in dark mode.
- Qt trap: a border-radius larger than half the widget's height is drawn square. Pill shapes take their radius from
  the real height at runtime (see `Segmented.resizeEvent`). Never hard-code pill radii.
- Badges hug their text. Cards fit their content. A hint that applies to every row is said once in the summary.
  When there's nothing left to do, hide the primary button instead of greying it out.

## Screenshots and the visual check (do this for any UI change)

1. `python tools/ui_screenshots.py screenshots/ <video folder> dark` and again with `light`. It uses a throwaway
   settings file, so the user's real settings and TMDB key are never touched.
2. Open every image at full size and check for: clipped or overlapping text, odd spacing inside words, badges
   stretched across a column, square corners on pills, repeated messages, cards stretched over empty space,
   greyed-out leftover buttons, low-contrast text in dark mode, wrong capitalisation, missing icons.
3. Fix what you find and capture again. Send the user separate 1920×1080 images, one screen per file, named like
   `03-review-dark.png`. Never combine screens into one image. Show dialogs over the dimmed window.
4. Say what the check found. The native title bar, Mica and DPI scaling can't be seen offscreen, so say so.

## Data and privacy

The repo may be public. Never commit videos, real subtitle files from the user's library, screenshots of their
library, or API keys. Test media lives in git-ignored folders (`samples/`, `media/`, `screenshots/`). Only the
small hand-written fixtures in `tests/fixtures/` are committed.

## Things only a real Windows PC can confirm

The title-bar colour (DWM), Mica, Recycle Bin moves, the Restart Manager "files in use" check, the font loading
inside the PyInstaller exe, and real TMDB calls. The CI smoke test only proves the exe starts. When a change touches
these, tell the user what to try and what they should see.
