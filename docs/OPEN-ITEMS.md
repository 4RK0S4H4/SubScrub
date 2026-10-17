# Open items and roadmap

Decisions waiting on the user are marked **(ask)**. Everything else can be done when the user says go.

Decided by the user (2026-10-11): releases are started with the workflow's "Run workflow" button (cloud sessions
can't push tags); the installer does not install FFmpeg/MKVToolNix (the app's install buttons stay); updates show a
banner and the user clicks Update (no automatic download); no code signing, the user clicks "Run anyway" once per
version. Still open: the local AI check.

Decided by the user (2026-10-17, v2.8.0): S4 Glass look with a top bar and Files/Review tabs, Title Case labels,
neutrals with a faint crimson tint (instead of pure greys). Approved from a mockup:
https://claude.ai/artifact/67nXdn4nt8ehRWbJhs6Gbg

Decided by the user (2026-10-17, issue #20): when a file has a regular (non-SDH) English track, SubScrub cleans that
one (to catch strays), makes it the default, names it "English" and keeps the SDH/HI track (not as the default). The
cleaned copy replaces the regular one, so there is no duplicate English track. If the regular track has nothing to
clean it is still made the default (and left alone if it already is). When only an SDH/HI/CC track exists, behaviour
is as before: clean it and drop the original SDH track per the save default. A track picked by hand in Review still
wins. The "to check" grading runs without the SDH cross-check in the regular-track case. Code: `batch.pick_track`,
`batch.plain_pick`, `media.save`. "Keep the SDH Track Too" only matters when an SDH track is the one cleaned.

## Install and update (built in batch 3, not yet released)

- **Installer** `installer/SubScrub.iss` (Inno Setup, built in CI as `dist/SubScrub-Setup.exe` and attached to the
  Release): per-user, no admin prompt, installs to `%LOCALAPPDATA%\Programs\SubScrub`, Start-menu and desktop
  shortcuts, uninstaller, quiet mode (`/VERYSILENT /SUPPRESSMSGBOXES`) that starts the app again afterwards. It never
  touches settings (registry) or `%LOCALAPPDATA%\SubScrub`. It does not install FFmpeg or MKVToolNix (the app's
  buttons do). CI installs it quietly on the runner, starts it, installs over the top and uninstalls.
- **Update check** `subscrub/updates.py`: once a day at start (Settings → Appearance and Safety can switch it off), and
  from ⋯ → Check for Updates and the About screen. A quiet banner "SubScrub X is available" with Update and Later.
  Update downloads the installer, checks its size and SHA-256, runs it quietly and quits; never during a save.
  A copy that wasn't installed by the installer (a loose exe, source) opens the Releases page instead. "Later"
  remembers that version, so the automatic check stays quiet about it; a manual check always shows it.
- **SmartScreen**: unsigned, so Windows says "Windows protected your PC" on first run. The user clicks "Run anyway".
  (Decided 2026-10-11: no code signing.)
- Needs a real Windows PC to confirm: the installer's look, the in-app update from a real Release (first possible
  after two releases have the installer), and the SmartScreen prompt.

## Tests and CI

- Every PR runs, on Windows (with FFmpeg and MKVToolNix from Chocolatey) and again on Linux: the cleaner corpus
  (`tests/fixtures/corpus.tsv`, one row per tricky line, run by `tests.test_cleaner`), `tests.test_pipeline` on
  generated media (`tests.make_samples`: an MKV with a font attachment and chapters, an MP4 with two tracks) and
  `tests.test_edge` (awkward files through the real batch worker: no subtitles, picture subtitles only, two SDH
  tracks, empty and all-SDH tracks, empty/UTF-16/cp1252 sidecars, an emoji-and-symbols name, Cancel, a too-long
  path). `tests.test_ui` also covers double Save, adding a file twice, closing mid-save and TMDB failures.
- Not testable in CI: Recycle Bin behaviour on a user's real drive, the "files in use" check with a torrent client,
  real TMDB calls, the exe on a real PC, the title-bar colour and Mica.
- Done in 2.8.0: CI attaches screenshots of every screen in both themes (job "screenshots", generated media only).

## Ideas the user has mentioned (not started)

- Done in batch 3: Review shortcuts (J/K move between lines, O keeps the original, F3 next change), shown as keycaps
  under the line list (hidden in a small window); the update check in About.
- **(ask)** An optional local AI check for edge cases. The user has an RTX 4070. The earlier recommendation was a
  narrow, opt-in feature that only suggests fixes for lines already marked "to check", run by a small local model,
  never auto-applied. The user wasn't sure it's worth it.
- A whole-library mode was explicitly rejected. Only revisit if the user asks, and only with a proper built-in
  backup step.

## Known limits

- Picture-based subtitles (PGS/VobSub) can't be cleaned (no OCR).
- Only verified offscreen on Linux: the title-bar colour, Mica, Recycle Bin, Restart Manager, real TMDB calls and
  font loading in the exe all need a check on the user's Windows PC.
