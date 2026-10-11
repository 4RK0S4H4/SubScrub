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

## Testing approach

- Unit-style suites in `tests/` run anywhere (offscreen Qt).
- `test_pipeline` runs real ffmpeg and mkvmerge against sample videos.
- UI changes are checked by driving the real window offscreen (`tools/ui_screenshots.py`) and reading every 1080p
  screenshot at full size. The user reviews on a phone, so send separate images, never a collage.
- TMDB was only tested with a fake client (`tests/test_naming.py`); the sandbox had no access to TMDB.
