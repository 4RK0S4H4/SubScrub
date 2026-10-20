# SubScrub

A Windows desktop app (WinUI 3, C# / .NET 8, in `winui/`) that strips non-dialogue SDH text from subtitle tracks, such as
`[door creaks]`, `JOHN:` and `♪ music ♪`. It works on MKV and other videos and on standalone .srt/.ass/.vtt files.
The cleaned track goes back into the video as the default. If the file has a regular English track, that one is cleaned
and the SDH track is kept; if only an SDH track exists, it is cleaned and dropped. Optionally it also renames
videos from TMDB (`The Walking Dead S7 E2.mkv`) and removes other-language subtitle tracks. It never re-encodes
video. FFmpeg and MKVToolNix do the muxing.

Current version: `<Version>` in `winui/SubScrub/SubScrub.csproj`. The history of every change is in `README.md`
("What's new") and `docs/HISTORY.md`. SubScrub 1–3 were a Python/Qt app; its engine was ported to C#, checked
side by side, and retired in 4.0 (issue #44). Its answers are frozen in `tests/golden` and the C# engine must keep
giving them.

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
  and send the release link.
- **Releases (decided by the user 2026-10-20).** Claude publishes releases without asking. Big updates (like the
  native app) get a new major version (4.0); a release that fixes a major bug gets the next minor number (4.1, 4.2);
  smaller changes get a patch number (4.0.1). The user still decides what gets built ("log it" vs "go").
- **"future plan" issues are never part of a batch.** An issue labelled `future plan` is an idea to discuss and
  design, not work to do. "Go" on all open issues, "do the batch" and similar skip it. Only build one when the user
  names it and says to build it.
- **Branch, PR, merge yourself.** Work on a branch, open a PR and let the `build` workflow pass. Small verified
  changes you merge without asking. SubScrub is a Windows desktop app, so there is no GitHub Pages link: after a
  release, give them the GitHub Release link (that is where they download `SubScrub-Setup.exe`).
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

Building the app needs Windows (WinUI); the engine, its checks and the sample videos work on Linux too.

```
dotnet publish winui/SubScrub/SubScrub.csproj -c Release -r win-x64 -p:Platform=x64 -o winui-out   # the app (Windows)
winui-out\SubScrub.exe [files or folders] [--log <file>]

python3 -m tests.make_samples                 # samples/Sample (SDH test).mkv/.mp4 (needs FFmpeg; the only Python left)
python3 -m tests.make_samples --library       # samples/library: 6 realistic videos in every state
python3 -m tests.make_samples --edge          # samples/edge: awkward files

dotnet run --project winui/SubScrub.Engine.Parity -c Release -- golden tests/golden .     # the frozen answers
dotnet run --project winui/SubScrub.Engine.Parity -c Release -- golden tests/golden . --update   # a rule changed on purpose
dotnet run --project winui/SubScrub.Engine.Tests -c Release -- . [name filter]          # engine tests on real videos
dotnet run --project winui/SubScrub.Engine.Tests -c Release -- --qa-check <qa.json> samples/library

winui-out\SubScrub.exe --shots <out> --library <copy of samples\library> --theme dark|light [--size 1000x680]
```

The golden check must pass on every change. When a rule, a wording or a view changes on purpose, run it, read every
difference, then run it with `--update` and say in the PR what changed (`tests/golden/README.md`).

CI: `.github/workflows/build.yml`. Job `engine` (Linux) makes the sample videos, runs the golden check and the engine
tests. Job `winui` (Windows) builds the app, runs the engine tests on Windows, runs the scripted QA in both themes and
a small window and checks the saved videos, then builds `SubScrub-Setup.exe` and installs, upgrades and uninstalls
it (including the Explorer menu keys and the one-window hand-over). Artifacts: `SubScrub-WinUI-Setup`,
`SubScrub-WinUI` and `winui-screenshots` (also pushed to the throwaway branch `winui-screenshots`, since cloud sessions
can't download artifacts). To ship a version: set `<Version>` in `winui/SubScrub/SubScrub.csproj`, add a "What's new"
entry to README.md, merge, then start the `build` workflow on main with the version typed in (Actions, "Run
workflow", or the `actions_run_trigger` tool). Job `release` checks the version matches the csproj, builds the app
and `SubScrub-Setup.exe`, creates the tag and publishes the Release. (Cloud Claude Code sessions can't push tags, so
don't try `git push` of a tag.)

## Layout

- `winui/SubScrub/`: the app (see `winui/README.md` for the file-by-file layout). `Program.cs` is the entry point
  (one window; a second start forwards its paths), `MainWindow*` the window, `Themes/` the S4 tokens and control
  templates, `Controls/` the painted parts, `Views/` Settings and the dialogs, `Services/Shots.cs` the scripted QA.
- `winui/SubScrub.Engine/`: the engine. `Cleaner.cs` (cleaning rules, risk grading, OCR fixes, `Name:` detection,
  the cross-check against the plain track), `SubFormats.cs` (SRT/ASS/VTT), `Media.cs`, `MediaRead.cs`,
  `MediaSave.cs` (FFmpeg/MKVToolNix: probing, extracting, remuxing, the verified swap, the Recycle Bin rule, the
  tidy-up), `WinApi.cs` (Recycle Bin, Restart Manager "files in use", drive type and SSD detection),
  `Naming.cs`/`NamingMatch.cs`/`Tmdb.cs` (TMDB, Windows-safe names, rename history in
  `%LOCALAPPDATA%\SubScrub\renames.jsonl`), `ReadCache.cs` (files already read, `%LOCALAPPDATA%\SubScrub\cache`), `Batch.cs` (track picking, sidecar pairing), `Session*.cs` (the file
  list, verdicts, Review, questions at save time, the batch save and reading with up to two at once, the
  SUBSCRUB_CLEANED mark), `Commands.cs`
  (the JSON commands the window calls), `Py*.cs` (the Python string, sorting and path rules the answers were frozen
  with: keep them).
- `winui/SubScrub.Engine.Parity/`: the golden check. `winui/SubScrub.Engine.Tests/`: engine tests and `--qa-check`.
- `installer/SubScrub-WinUI.iss`: the installer (per user; Start menu, desktop, Explorer menu, uninstaller).
- `tests/golden/`: the retired Python engine's answers. `tests/fixtures/`: hand-written subtitle fixtures.
  `tests/make_samples.py`: generated sample videos. `tools/extract-subs.ps1`: a PowerShell extractor.

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
  tinted neutrals; the user chose this over the 2.7 pure greys). The colours live in `winui/SubScrub/Themes/Tokens.xaml`. Dark mode is
  a 165° gradient with two faint accent blooms; cards are glass with a hairline border and a lit top edge.
- Crimson is used only for the primary button, switches, the selected tab, segment or track, and things that need
  checking. A file's state is a slim bar on its left edge plus one badge: ok green, warn amber, attention red.
- Labels, buttons, tabs, card titles and options in Title Case. Messages, hints, descriptions and dialog questions
  ("Which title is this?") in sentence case.
- Layout: a top bar (logo, Files/Review tabs, tools state, theme, Settings, ⋯) and a footer bar (status, progress,
  the one primary button). Settings, About, the log and every question open inside the window over a blurred, dimmed
  view (a glass dialog), never as a system message box or a separate window.
- Inter (bundled, the open digits built in) app-wide. Tabular figures go on counts and timecodes only:
  Inter's `tnum` widens hyphens. Solar icons (linear, duotone for big ones).
- 8–12px corners, hairline borders, soft shadows. The primary button glows only in dark mode. Focus rings show for
  keyboard focus only.
- Colours come only from `Tokens.xaml` through the theme resources; every control used has an S4 template in
  `Themes/Controls.xaml`. Each Inter weight is its own font family, so set `FontFamily`, never `FontWeight`.
- Badges hug their text. Cards fit their content. A hint that applies to every row is said once in the summary.
  When there's nothing left to do, hide the primary button instead of greying it out.
- Speed (the user noticed lag in 2.7): the line list is virtualized; when one line changes, update that row
  (`PreviewPanel.UpdateCue`) instead of rebuilding the list.

## Screenshots and the visual check (do this for any UI change)

1. Push and let the `winui` job run the scripted QA (`SubScrub.exe --shots`, both themes and 1000x680, on the
   generated sample library with a throwaway settings file, so real settings and TMDB keys are never touched). The
   images land on the `winui-screenshots` branch: fetch it and look at them. Make sure the screens show realistic
   content and a few states (ready, needs attention, done).
2. Open every image at full size and check for: clipped or overlapping text (including the last row of a list or
   the edge of a scroll area), odd spacing inside words or numbers, digits that don't look like Inter's (open 4, 6
   and 9, flat-top 3), badges stretched across a column, square corners on pills, repeated messages, cards
   stretched over empty space, greyed-out leftover buttons, labels that contradict the badge next to them,
   accent colour on things that don't need attention, inconsistent alignment or corner radii, low-contrast text in
   dark mode, wrong capitalisation (a sentence-case label or button, a Title Case message, a capital after a
   comma), stray punctuation, missing icons. Also check the small window.
3. Fix what you find and capture again, at most two rounds. Send the user separate 1920×1080 images, one screen per
   file, named like `03-review-dark.png`. Never combine screens into one image or shrink a screen to fit more in.
   Show dialogs composited over the dimmed window.
4. Say in a line or two what the check found and fixed. The CI runner has no real Mica or DPI scaling, so say so.
   Never claim a check you didn't do.

Layout rules from the skill that apply here: one primary (accent-filled) button per area, hiding it when there's
nothing to do; global top-bar actions stay in the same place on every screen (disable, don't remove); headings and
card titles in Title Case (the user's choice); headers stay uncluttered, with counts as quiet secondary text; say a
hint once, not on every row; cards fit their content.

## Data and privacy

The repo may be public. Never commit videos, real subtitle files from the user's library, screenshots of their
library, or API keys. Test media lives in git-ignored folders (`samples/`, `media/`, `screenshots/`). Only the
small hand-written fixtures in `tests/fixtures/` are committed.

## Things only a real Windows PC can confirm

The title-bar colour (DWM), Mica, DPI scaling, Recycle Bin moves on the user's drives, the Restart Manager "files in
use" check with a real torrent client, SSD detection, the Explorer right-click menu, and real TMDB calls. CI proves the
app installs, starts, saves the sample library and hands paths over to an open window. When a change touches
these, tell the user what to try and what they should see.
