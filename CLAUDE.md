# SubScrub

A Windows desktop app (Python 3.11+, PySide6/Qt) that strips non-dialogue SDH text from subtitle tracks, such as
`[door creaks]`, `JOHN:` and `♪ music ♪`. It works on MKV and other videos and on standalone .srt/.ass/.vtt files.
The cleaned track goes back into the video as the default. If the file has a regular English track, that one is cleaned
and the SDH track is kept; if only an SDH track exists, it is cleaned and dropped. Optionally it also renames
videos from TMDB (`The Walking Dead S7 E2.mkv`) and removes other-language subtitle tracks. It never re-encodes
video. FFmpeg and MKVToolNix do the muxing.

Current version: see `subscrub/__init__.py`. The history of every change is in `README.md`
("What's new") and `docs/HISTORY.md`.

## How we work together

Follows the S4 Studio skill (load it with the Skill tool if it's available; its rules win over this summary when
they differ, except where this file says SubScrub is a desktop app). The user is the product manager. You are the
whole development team.

- **The user describes, you execute.** They give features and bugs in plain English, often with a phone screenshot.
  You do all coding, installing, building, testing, committing, merging and releasing. Never ask them to run
  commands, install anything or edit files.
- **Backlog first, then "go".** When they say "log it", "add to the list", or ask you not to execute yet, file each
  request or bug as a GitHub issue (`bug` / `enhancement`), reply with the links, and stop. Don't build, test or
  screenshot while only logging. They decide when and what to tackle. When they say "go" or "do the batch"
  (or name specific issues), do exactly those in one branch and PR, run the checks once, merge, close the issues
  and send the release link. They also say when to release.
- **Branch, PR, merge yourself.** Work on a branch, open a PR and let the `build` workflow pass. Small verified
  changes you merge without asking. SubScrub is a Windows desktop app, so there is no GitHub Pages link: after a
  release, give them the GitHub Release link (that is where they download `SubScrub.exe`).
- **Ask before deciding what's theirs to decide.** Cleaning rules, naming formats and anything that changes their
  files are confirmed with them and recorded in `docs/OPEN-ITEMS.md`. Never silently change a rule below.
  Ask with the multiple-choice question tool when there is one: a short round (at most four questions), the
  recommended option first and marked "(Recommended)". No open-ended "anything else?" questions.
- **Don't interview for small things.** For a bug, a button or a new field, match what's there and do it. Ask one
  question only if the change is genuinely ambiguous. A new feature or redesign gets one short round of questions.
- **Short, plain-language replies.** Say what changed, what they need to do (usually nothing) and the link. Say
  honestly what wasn't verified, for example "not run on a real Windows PC" or "TMDB not called for real". End a
  hand-over with 2-3 concrete next-step options they can pick from.
- **When feedback says "not quite", change only that.** Keep everything else stable and say exactly what changed.
  If a tweak goes the wrong way (too bright, too strong), move halfway back rather than swinging to the other
  extreme.
- **Design work follows the S4 Studio skill.** The look is already decided (see "Look" below). For colour
  questions, publish a switchable HTML sampler (from the skill) instead of static images, with the app's real
  screen as the mock. Every sample page must work on a phone (390px wide, no sideways scroll, 40px touch targets).

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
python -m tests.test_ui                                         # contrast (WCAG AA), main flows, every dialog
python -m tests.test_pipeline                                    # makes its own sample videos (ffmpeg + mkvmerge)
python -m tests.test_edge                                        # awkward files through the real save path

python -m tests.make_samples                                    # makes samples/Sample (SDH test).mkv/.mp4 (ffmpeg)
python -m tests.make_samples --library                          # samples/library: 6 realistic videos in every state
python tools/ui_screenshots.py <out> samples/library dark|light [--save] [--size 1000x680]   # visual-check shots
python tools/make_solar.py <solar.json>                         # regenerate subscrub/ui/solar.py from Iconify's set
build.bat                                                       # local Windows build -> dist\SubScrub.exe
```

CI: `.github/workflows/build.yml` runs the tests and builds `SubScrub.exe` on `windows-latest` for every push and
PR (the exe is attached to the run). A second job on Linux makes the sample library and attaches screenshots of
every screen in both themes (artifact "screenshots"). Pushing a `vX.Y.Z` tag also publishes a GitHub Release with the exe.
To ship a version: bump `subscrub/__init__.py`, add a "What's new" entry to README.md, merge, then start the
`build` workflow on main with the version typed in (Actions, "Run workflow", or the `actions_run_trigger` tool).
It checks the version matches `__init__.py`, creates the tag and publishes the Release. (Cloud Claude Code sessions
can't push tags, so don't try `git push` of a tag.)

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
- `subscrub/ui/main_window.py`: the window: top bar with tabs, welcome, file list, review (sidebar plus preview),
  footer.
- `subscrub/ui/dialogs.py`: every dialog (Settings, the "not sure" choice, title picker, files in use, tools, log,
  About) and `ask()` / `tell()`, which replace QMessageBox. All are in-window `GlassDialog`s.
- `subscrub/ui/workers.py`: background threads (load, save, lock check) and small formatting helpers.
- `subscrub/ui/preview.py`: the caption "screen", the timeline and the before/after list.
- `subscrub/ui/theme.py`: colour tokens (DARK/LIGHT), the style sheet, fonts, `num_font()`, the title-bar colour.
- `subscrub/ui/glass.py`: window backdrop (cached), card panels, `GlassDialog`, optional Windows 11 Mica.
- `subscrub/ui/widgets.py`: painted controls: rows, badges, the segmented control (also the tabs), toggles, track
  tiles, the settings nav, rounded tooltips (`set_tip`) and menus (`make_menu`), Solar icon drawing.
- `subscrub/ui/solar.py`: Solar icon SVGs (CC BY 4.0). `subscrub/ui/fonts.py`: Inter as base64 (OFL).
- `tests/`: the test suites and fixtures. `tools/`: screenshot driver, Solar generator, corpus checker, PowerShell
  extractor.

## Rules the user decided (don't change without asking)

- Strip: sounds in [brackets] and (parentheses), speaker names, music cues, on-screen-text notes
  (`[sign reads: EXIT]` becomes `EXIT`) and sound words on their own line.
- Song lyrics stay, in italics, without ♪. Dialogue dashes are always removed. Never reflow or merge lines.
- Save defaults: replace the original, make the cleaned track the default. The original goes to the Recycle Bin
  only after the new file checks out.
- Which track gets cleaned (decided 2026-10-17): when a regular (non-SDH) English track exists, clean that one to
  catch strays, make it the default, name it "English" and keep the SDH track (just not as the default). The cleaned
  copy replaces the regular one, so there is no duplicate. When only an SDH/HI/CC track exists, clean it and drop the
  original SDH track. A track picked by hand in Review always wins.
- Uncertain lines: ask at save time (review / best guess / leave alone / skip). Never learn from corrections.
- No whole-library mode. Only files the user picks.
- A folder of videos with matching `.srt` sidecars becomes one file per video, with the cleaned subtitle inside.
- Warn up front about files another program has open (seeding torrents), before anything is changed.
- Renaming: TMDB, no year by default (an option), no folders, pure rename. Episodes look like `Show S7 E2.mkv`.
  The cleaned track is named just "English". Non-English subtitle tracks are deleted unless they are forced
  (und tracks are kept). The user doesn't use Radarr/Sonarr. The TMDB key is entered in Settings; never commit one.
- English tracks only matter to the user.

## Look (S4 Glass, decided 2026-10-17)

Mockup the user approved: https://claude.ai/artifact/67nXdn4nt8ehRWbJhs6Gbg (design canvas, both themes).

- One accent, crimson `#C81E3A` (accent-2 `#9F1239`, light `#FDA4AF`). Neutrals carry a faint crimson tint (S4
  tinted neutrals; the user chose this over the 2.7 pure greys). The formulas live in `theme.tokens()`. Dark mode is
  a 165° gradient with two faint accent blooms; cards are glass with a hairline border and a lit top edge.
- Crimson is used only for the primary button, switches, the selected tab, segment or track, and things that need
  checking. A file's state is a slim bar on its left edge plus one badge: ok green, warn amber, attention red.
- Labels, buttons, tabs, card titles and options in Title Case. Messages, hints, descriptions and dialog questions
  ("Which title is this?") in sentence case.
- Layout: a top bar (logo, Files/Review tabs, tools state, theme, Settings, ⋯) and a footer bar (status, progress,
  the one primary button). Settings, About, the log and every question open inside the window over a blurred, dimmed
  view (`GlassDialog`), never as a QMessageBox or a separate window.
- Inter (bundled) with `ss01` and `cv09` app-wide. Tabular figures (`num_font()`) go on counts and timecodes only:
  Inter's `tnum` widens hyphens. Solar icons (linear, duotone for big ones).
- 8–12px corners, hairline borders, soft shadows. The primary button glows only in dark mode. Focus rings show for
  keyboard focus only (`KeyboardFocus`).
- Painted widgets (segmented control, toggles, badges, track tiles, the preview rows) read `theme.CURRENT`. The style
  sheet covers buttons, fields, menus and label roles (`objectName`, e.g. `Hint`, `CardTitle`).
- Qt traps: a border-radius larger than half the widget's height is drawn square, so pills take their radius from
  the real height at runtime; never hard-code pill radii. A dynamic property must not share a name with a Qt
  property (`size`, `icon`): buttons use `btnsize` and `iconOnly`. QToolTip and native menus can't have round
  corners: use `set_tip()` and `make_menu()`.
- Badges hug their text. Cards fit their content. A hint that applies to every row is said once in the summary.
  When there's nothing left to do, hide the primary button instead of greying it out.
- Speed (the user noticed lag in 2.7): no app-wide event filters; cache fonts, icons and row layouts; when one line
  changes, update that row (`PreviewPanel.update_cue`) instead of rebuilding the list.

## Screenshots and the visual check (do this for any UI change)

1. `python tools/ui_screenshots.py screenshots/ <video folder> dark` and again with `light`. It uses a throwaway
   settings file, so the user's real settings and TMDB key are never touched. Use realistic content (real-looking
   file names and numbers, and a few states: ready, needs attention, done), not an empty shell.
2. Open every image at full size and check for: clipped or overlapping text (including the last row of a list or
   the edge of a scroll area), odd spacing inside words or numbers, digits that don't look like Inter's (open 4, 6
   and 9, flat-top 3), badges stretched across a column, square corners on pills, repeated messages, cards
   stretched over empty space, greyed-out leftover buttons, labels that contradict the badge next to them,
   accent colour on things that don't need attention, inconsistent alignment or corner radii, low-contrast text in
   dark mode, wrong capitalisation (a sentence-case label or button, a Title Case message, a capital after a
   comma), stray punctuation, missing icons. Also check a small window (`--size 1000x680`).
3. Fix what you find and capture again, at most two rounds. Send the user separate 1920×1080 images, one screen per
   file, named like `03-review-dark.png`. Never combine screens into one image or shrink a screen to fit more in.
   Show dialogs composited over the dimmed window.
4. Say in a line or two what the check found and fixed. The native title bar, Mica and DPI scaling can't be seen
   offscreen, so say so. Never claim a check you didn't do.

Layout rules from the skill that apply here: one primary (accent-filled) button per area, hiding it when there's
nothing to do; global top-bar actions stay in the same place on every screen (disable, don't remove); headings and
card titles in Title Case (the user's choice); headers stay uncluttered, with counts as quiet secondary text; say a
hint once, not on every row; cards fit their content.

## Data and privacy

The repo may be public. Never commit videos, real subtitle files from the user's library, screenshots of their
library, or API keys. Test media lives in git-ignored folders (`samples/`, `media/`, `screenshots/`). Only the
small hand-written fixtures in `tests/fixtures/` are committed.

## Things only a real Windows PC can confirm

The title-bar colour (DWM), Mica, Recycle Bin moves, the Restart Manager "files in use" check, the font loading
inside the PyInstaller exe, and real TMDB calls. The CI smoke test only proves the exe starts. When a change touches
these, tell the user what to try and what they should see.
