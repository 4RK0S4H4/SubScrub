# Open items and roadmap

Decisions waiting on the user are marked **(ask)**. Everything else can be done when the user says go.

## Next up: install and update (the user's stated priority)

Today the user downloads a zip, unblocks it, runs `build.bat` (which sets up Python and builds the exe) and runs
`install-tools.bat` for FFmpeg and MKVToolNix. The goal is one normal installer and in-app updates.

1. **Releases from CI.** `.github/workflows/build.yml` already builds `SubScrub.exe` on Windows and attaches it to a
   GitHub Release when a `vX.Y.Z` tag is pushed. Make the first release (`v2.7.3`) and confirm the exe runs on the
   user's PC.
2. **A real installer.** Suggested: Inno Setup, built in CI (`choco install innosetup`, then `iscc`). It installs
   to `%LOCALAPPDATA%\Programs\SubScrub` with no admin prompt, adds Start-menu and desktop shortcuts and an
   uninstaller, and keeps settings (QSettings, registry) and `%LOCALAPPDATA%\SubScrub` on upgrade.
   **(ask)** Whether the installer should also install FFmpeg and MKVToolNix (via winget) or leave that to the
   app's existing "install tools" buttons.
3. **Update check in the app.** On start (at most once a day) and from ⋯ → "Check for updates", call
   `https://api.github.com/repos/<owner>/<repo>/releases/latest`, compare with `__version__`, and show a quiet
   banner: "SubScrub 2.8.0 is available — Update". Update = download the new installer to a temp folder, run it
   silently (`/VERYSILENT /SUPPRESSMSGBOXES`) and quit. Never update mid-save. **(ask)** Automatic download or ask
   first (recommended: ask first, one click).
4. **SmartScreen.** An unsigned exe shows "Windows protected your PC" on first run. Code signing costs money.
   **(ask)** Whether that's worth it, or whether the user is fine clicking "Run anyway" once per version.
   Remove the "Unblock the zip" step from the README once there's an installer.
5. Replace README's Setup section with "Download SubScrub-Setup.exe from Releases", and keep `build.bat` for
   developers only.

## Tests and CI

- `tests.test_pipeline` runs on the original sample set but not fully on `tests.make_samples` output: the sample
  MP4 needs two subtitle tracks, and the sample MKV needs a font attachment and chapters. Extend `make_samples`
  so the pipeline test runs on generated media, then add it to CI (install ffmpeg and mkvtoolnix with choco on
  the runner).
- Add a CI job that runs `tools/ui_screenshots.py` in both themes and uploads the PNGs as an artifact, so every PR
  has screenshots to check.

## Ideas the user has mentioned (not started)

- Keyboard shortcuts in Review (J/K to move between lines, a key for "Keep original"), shown as keycaps.
- An About screen with version, credits (Inter OFL, Solar CC BY 4.0) and the update check.
- **(ask)** An optional local AI check for edge cases. The user has an RTX 4070. The earlier recommendation was a
  narrow, opt-in feature that only suggests fixes for lines already marked "to check", run by a small local model,
  never auto-applied. The user wasn't sure it's worth it.
- A whole-library mode was explicitly rejected. Only revisit if the user asks, and only with a proper built-in
  backup step.

## Known limits

- Picture-based subtitles (PGS/VobSub) can't be cleaned (no OCR).
- Only verified offscreen on Linux: the title-bar colour, Mica, Recycle Bin, Restart Manager, real TMDB calls and
  font loading in the exe all need a check on the user's Windows PC.
