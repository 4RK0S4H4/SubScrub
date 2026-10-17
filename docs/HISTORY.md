# How SubScrub got here

SubScrub was built in a Claude chat project ("Strip subtitles app") between late September and October 2026, then
moved to this repo. README.md has the user-facing "What's new" for each version. This file records the reasoning
a future maintainer needs.

- **v1:** a rule-based cleaner for one file at a time, with a before/after preview and a scrub bar showing where
  text was removed.
- **v2:** tuned on the user's real library (299 videos, 72 English SDH tracks: WEB-DL, Blu-ray and OCR'd). Added
  per-line risk grading and the "to check" flow, a cross-check against the plain English track in the same file,
  OCR repairs (`j`/`♪`, `[`/`I`), `Name:` detection, batch mode, sidecar `.srt` pairing (one file per video), the
  safe swap (verify the new file, then send the original to the Recycle Bin) and the "files in use" warning for
  torrents being seeded. Result on that set: 70 of 72 tracks needed no judgement calls.
- **v2.4:** redesign (review sidebar, caption "screen", quieter file list).
- **v2.5:** one-step prep: TMDB renaming with Windows-safe names, an "unsure title" dialog, the cleaned track named
  "English", other-language tracks removed (forced and und kept), undo for renames.
- **v2.6:** glass look; the top bar was cleaned up after the user's feedback. Mica made opt-in.
- **v2.7.0:** the user's design skill (then "Saha app style", now "S4 Studio"): Inter, Solar icons, tokens.
- **v2.7.1:** accent picked from an HTML sampler: crimson `#C81E3A`, everything else monochrome.
- **v2.7.2:** fixes from a visual check. Inter's `tnum` widened hyphens, so tabular figures are now only on numbers.
  Badges hug their text, the TMDB hint is said once, the file card fits its rows, no greyed "All done" button,
  dialog choices have a bold title with a quieter description, and saved rows read "renamed from …".
- **v2.7.3:** Qt draws a border-radius larger than half the height as square, which made the selected segment a red
  rectangle. Pill radii now come from the real height at runtime.
- **v2.8.0:** the S4 Glass overhaul. The user approved a mockup on a design canvas first and chose a top bar with
  tabs, Title Case labels and tinted neutrals (replacing 2.7's pure greys). The UI was rebuilt: painted widgets that
  read the theme tokens, in-window glass dialogs instead of QMessageBox, `dialogs.py` and `workers.py` split out of
  `main_window.py`. The user had noticed lag in Review; profiling found a Python event filter on every app event,
  QTextDocuments built for every row height, fonts rebuilt on every paint and 2,000 antialiased timeline marks.
  Caching those and updating one row instead of rebuilding the list made toggling a line about 4× faster.
  `tests/test_ui.py` now guards text contrast and the main flows, and CI attaches screenshots of every screen.
- **v3.0.0:** batches 1-4 (cleaning fixes, safer saving, the regular English track first, Review shortcuts, the
  installer and update check, tests on generated media in CI, the README rewrite) plus a new icon. The user compared
  a dozen drafts on HTML pages and a design canvas, picked "Struck Tag" and asked for a rounded square in the old
  icon's colours. `logo_pixmap` is the single source: `tools/make_icon.py` renders every .ico size from it, with a
  bolder version at 24 px and below rather than a scaled-down 256.
- **After v3.0.0:** the README images showed as broken. The `screenshots/` rule in `.gitignore` (meant for local
  visual-check shots) also matched `docs/screenshots/`, so the pictures were never committed. It now has an
  exception, and the pictures were remade from the sample library.

## Testing approach

- Unit-style suites in `tests/` run anywhere (offscreen Qt).
- `test_pipeline` runs real ffmpeg and mkvmerge against sample videos.
- UI changes are checked by driving the real window offscreen (`tools/ui_screenshots.py`) on the generated sample
  library (`tests.make_samples --library`) and reading every 1080p screenshot at full size, plus a 1000×680 pass.
  The user reviews on a phone, so send separate images, never a collage.
- TMDB was only tested with a fake client (`tests/test_naming.py`); the sandbox had no access to TMDB.

## Release notes of older versions

Moved here from the README, which keeps only the latest two versions.

### Version 2.7

**A new look (S4 Studio style).** One crimson accent (`#C81E3A`) and everything else monochrome: neutral off-white in light mode with white cards; in dark mode, glass (translucent cards over a near-black gradient). Crimson is kept for the main button, switches, the selected pill and track, and anything that needs checking. Inter (bundled), Solar icons, 9-12px corners, hairline borders, badge-style statuses, and a crimson app icon. The Windows 11 Mica backdrop is still an opt-in in Settings. Nothing about how files are cleaned has changed.

2.7.1 also updates the pipeline test for the "English" track name introduced in 2.5.

2.7.2 fixes from a full visual check: hyphens no longer look spaced out ("Spider-Man", "On-screen"); status badges hug their text instead of stretching; "Add a TMDB key…" is said once in the summary instead of on every row; the file list card fits its rows; no greyed-out "All done" button after saving; the choice buttons in the "not sure" and title dialogs have a bold title over a quieter description; saved rows say "renamed from <old name>".

2.7.3: the selected option in segmented controls (Settings, Review filters, Song lyrics) is a rounded pill again instead of a red rectangle on some displays.

Credits: Inter (SIL OFL 1.1, see `assets/fonts/OFL-Inter.txt`); Solar icon set by 480 Design (CC BY 4.0).

### Version 2.6

**A glass look.** Frosted, translucent panels with lit edges over a soft backdrop, in dark and light. SubScrub paints its own backdrop by default and colours the title bar to match; Settings → "Use Windows' Mica backdrop (Windows 11)" switches to Mica instead (restart needed).

### Version 2.5

**One-step prep for new downloads.** Besides cleaning the subtitles, SubScrub can now:

- **Rename videos from TMDB.** `Batman V Super Dawn of Justice 2160 HDR DV DDP.mkv` becomes `Batman v Superman - Dawn of Justice.mkv`. Episodes become `The Walking Dead S7 E2.mkv` (two-part files read `S2 E1-E2`). Names are made Windows-safe (`:` becomes ` - `, no `? * " < > | / \`). Pure renaming: no folders are created, and the year is off unless you turn on "Add the year to movie names". Matching sidecar `.srt` files are renamed with the video.
- **Ask when unsure.** If the title isn't a clear match, the file shows "Not sure which title this is". Choose from the closest matches, search by title, or keep the name (Review, or when you save).
- **Tidy the subtitle tracks.** The cleaned track is called just "English" (or whatever language it is). Subtitle tracks in other languages are removed; forced tracks and tracks with no language stay.
- **Undo.** ⋯ → "Undo last renames…" puts the previous names back.

Setup: renaming needs a free TMDB API key (themoviedb.org → Settings → API). Paste it in Settings → Names and tracks. Renaming and the tidy-up apply when originals are replaced, and like any rewrite they stop torrents from seeding the file.

### Version 2.4

A redesigned interface: a bundled typeface, a quieter file list with line counts,
a Review screen with the track and edit choices in a sidebar, and the caption "screen". Saving is also safer:
replacing a video can no longer end with neither the old nor the new file, whatever Windows reports.
