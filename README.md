# SubScrub

Strips the non-dialogue parts out of SDH / closed-caption subtitles — `[gunshot in background]`, `(sighs)`,
`JOHN:`, `♪ tense music ♪`, dialogue dashes — and puts the cleaned track back into the video as the default.
Audio and video are copied bit-for-bit (no re-encoding), so it takes about as long as copying the file.

Version 2 was tuned on a real library (299 videos, 72 English SDH tracks from WEB-DL, Blu-ray and OCR'd releases).
On that set, 70 of 72 tracks come out with no judgement calls at all; the other two (both OCR'd from disc images)
have 1–4 lines each that the app asks about.

## Setup (once)

1. **Unblock the zip first**: right-click the downloaded zip → Properties → tick **Unblock** → OK, then extract it.
2. Double-click **`build.bat`**. It sets up a private Python environment in this folder, runs the self-tests,
   and builds `dist\SubScrub.exe`. If Python isn't installed it offers to install it with winget.
3. Double-click **`install-tools.bat`** to install FFmpeg (required) and MKVToolNix (recommended for MKV).
   You can also do this from inside the app — it detects missing tools and has install buttons.

After that, just run `dist\SubScrub.exe` (build.bat can add a desktop shortcut). You can move the exe anywhere.

## What's new in 2.8

**A rebuilt interface (S4 Glass), and a faster Review screen.** Nothing about how files are cleaned, renamed or
saved has changed.

- A top bar with **Files** and **Review** tabs. Settings, the light/dark switch and the ⋯ menu stay in the same place
  on every screen, and the main button sits in the footer.
- Glass cards over a softly tinted backdrop: the greys now carry a faint hint of the crimson accent. Crimson still
  only marks the main button, switches, the selected item and lines to check.
- Each file shows its state as a coloured bar on its left edge plus one badge.
- Settings, About, the activity log and every question open inside the window over a blurred background instead of
  as separate windows. Menus and tooltips have rounded corners.
- Labels and buttons in Title Case; messages and questions in plain sentences.
- Review responds faster: "Keep Original" on a line is about 4× quicker, stepping through lines about 4×, switching
  filters and scrolling about 2×.
- On a small window the Review toolbar shows "Keep Original" as an icon so the filters always fit.
- About shows the version and credits.

## What's new in 2.6

**A glass look.** Frosted, translucent panels with lit edges over a soft backdrop, in dark and light. SubScrub paints its own backdrop by default and colours the title bar to match; Settings → "Use Windows' Mica backdrop (Windows 11)" switches to Mica instead (restart needed).

## What's new in 2.7

**A new look (S4 Studio style).** One crimson accent (`#C81E3A`) and everything else monochrome: neutral off-white in light mode with white cards; in dark mode, glass (translucent cards over a near-black gradient). Crimson is kept for the main button, switches, the selected pill and track, and anything that needs checking. Inter (bundled), Solar icons, 9-12px corners, hairline borders, badge-style statuses, and a crimson app icon. The Windows 11 Mica backdrop is still an opt-in in Settings. Nothing about how files are cleaned has changed.

2.7.1 also updates the pipeline test for the "English" track name introduced in 2.5.

2.7.2 fixes from a full visual check: hyphens no longer look spaced out ("Spider-Man", "On-screen"); status badges hug their text instead of stretching; "Add a TMDB key…" is said once in the summary instead of on every row; the file list card fits its rows; no greyed-out "All done" button after saving; the choice buttons in the "not sure" and title dialogs have a bold title over a quieter description; saved rows say "renamed from <old name>".

2.7.3: the selected option in segmented controls (Settings, Review filters, Song lyrics) is a rounded pill again instead of a red rectangle on some displays.

Credits: Inter (SIL OFL 1.1, see `assets/fonts/OFL-Inter.txt`); Solar icon set by 480 Design (CC BY 4.0).

## What's new in 2.5

**One-step prep for new downloads.** Besides cleaning the subtitles, SubScrub can now:

- **Rename videos from TMDB.** `Batman V Super Dawn of Justice 2160 HDR DV DDP.mkv` becomes `Batman v Superman - Dawn of Justice.mkv`. Episodes become `The Walking Dead S7 E2.mkv` (two-part files read `S2 E1-E2`). Names are made Windows-safe (`:` becomes ` - `, no `? * " < > | / \`). Pure renaming: no folders are created, and the year is off unless you turn on "Add the year to movie names". Matching sidecar `.srt` files are renamed with the video.
- **Ask when unsure.** If the title isn't a clear match, the file shows "Not sure which title this is". Choose from the closest matches, search by title, or keep the name (Review, or when you save).
- **Tidy the subtitle tracks.** The cleaned track is called just "English" (or whatever language it is). Subtitle tracks in other languages are removed; forced tracks and tracks with no language stay.
- **Undo.** ⋯ → "Undo last renames…" puts the previous names back.

Setup: renaming needs a free TMDB API key (themoviedb.org → Settings → API). Paste it in Settings → Names and tracks. Renaming and the tidy-up apply when originals are replaced, and like any rewrite they stop torrents from seeding the file.

## What's new in 2.4

A redesigned interface: a bundled typeface, a quieter file list with line counts,
a Review screen with the track and edit choices in a sidebar, and the caption "screen". Saving is also safer:
replacing a video can no longer end with neither the old nor the new file, whatever Windows reports.

## Using it

1. **Drop files** on the window (videos, a season folder, or `.srt` / `.ass` / `.vtt` files).
2. Each file gets a status once it's read:
   - **Ready** — confident, nothing to look at.
   - **3 to check** — a few judgement calls (e.g. words in parentheses that might be dialogue).
   - **Look closer** — lots of judgement calls, usually a track OCR'd from disc images.
   - **No SDH** — the file only has regular subtitles; it's skipped (open it to clean it anyway).
   - **Can't edit** — picture-based subtitles (Blu-ray PGS / DVD VobSub).
3. **Clean & save all.** If some files have judgement calls, you choose on the spot: review them now, save with
   SubScrub's best guess, save but leave those lines untouched, or save only the other files.

### Videos with subtitle files next to them

Drop the whole folder (or select everything in it). A subtitle file named after a video —
`Show - S01E01 - Title.eng.srt` next to `Show - S01E01 - Title.mkv`, also `.en.sdh.srt`, `.srt`, `.forced.srt` —
becomes part of that video instead of a separate item. SubScrub picks the SDH version (inside the video or the
file), puts the cleaned track **into the video** as the default, and then moves the `.srt` (and any other
same-language subtitle files next to that video) to the Recycle Bin, so **each video ends up as one file**.
Forced-subtitle files are never removed; subtitle files in the folder with no matching video are left alone.
Turn this off with *Settings → One file per video*. Dropping just the `.srt` files works the same way.

### Review

Click any file (or **Review**) to see it up close:

- **Subtitle track** — the SDH track is picked automatically (by its flag/name, or by its content when it isn't
  labelled). Commentary, forced and signs/songs tracks are never picked. The regular track in the same file is
  used as a cross-check: if it has words at the same moment that SubScrub removed, that line is flagged.
- **What to remove** (left sidebar) — every kind of edit with how many lines it touches; switch one off to see the result
  without it. *Use these for all files* makes them your default.
- **Preview** — a dark "screen" shows the selected line the way it will look on the TV. The timeline marks removed lines (light), trimmed lines (grey) and lines to check (red). The list
  shows Before | After. **To check** lists just the judgement calls with the reason. **Keep original** (or
  double-click, or Ctrl+Z) puts any line back exactly as it was.

## What gets cleaned

| Edit | Example | Default |
|---|---|---|
| Sounds in [brackets] (incl. `[Grace]` speaker IDs) | `[door creaks] Hello?` → `Hello?` | on |
| Sounds in (parentheses) | `(sighs) Fine.` → `Fine.` | on |
| Speaker names | `JOHN: Get down!`, `NAOMI:Take it easy.`, `Nice.HOLDEN: Shut up.` | on |
| Music cues | `♪ tense music playing ♪`, `♪♪`, `(MUSIC FADES)` | on |
| Song lyrics | `♪ Walking down the street ♪` → *Walking down the street* | italic, no ♪ |
| Dialogue dashes | `- Hi.` / `- Hey.` → `Hi.` / `Hey.` | on |
| On-screen text notes | `[sign reads: EXIT]` → `EXIT` | on |
| OCR repairs (disc-converted tracks only) | `j" mad world` → *mad world*, `How can [help you?` → `How can I help you?` | on |
| Every `Name:` label | `Man on TV: Hello` → `Hello` even in files that don't use them throughout | off |

Things it handles that trip up simpler tools (all seen in real files):

- Speaker names with no space, glued to the previous sentence, mid-line (`something nice? -IAN: No.`),
  with quotes (`WILLIAM "BILLY" MACON:`), with qualifiers (`MAN [OVER RADIO]:`), `McDOWELL:`, `COMPUTER;`.
- Files that write names like `Harrington:` throughout are detected and cleaned; a normal file's
  `Warning: the following images are graphic.` is left alone.
- Lyrics vs. music descriptions: `♪ ...how this ends ♪` and `♪ When the music starts ♪` are lyrics, not cues.
- Nested and multi-line notes: `(MUSIC FADING UP, "BRANDY (YOU'RE A FINE GIRL)" PLAYING)`.
- On-screen text cards (`PASSIVE SCAN / MAXIMUM RANGE`, title cards) are kept.
- Italics, `{\an8}` positioning and ASS styling are preserved; lines nothing touches stay byte-for-byte identical.
- Original line breaks are kept — nothing is reflowed.

## Settings (⚙)

- **Videos**: *Replace original* (default; new file is verified before the original goes to the Recycle Bin),
  *New file* (`Movie.dialogue.mkv`), or *Subtitle file* (`Movie.en.srt`, nothing remuxed).
  *Make the cleaned track the default* (on), *Keep the SDH track too* (off), track name.
- **Subtitle files**: *One file per video* (on — see above). For subtitle files with no matching video:
  *Embed in video*, *Replace file* or *New file*.
- **Default edits**, lyrics style, theme (*Automatic* follows Windows live), and whether to ask before rewriting.

**Jellyfin:** after replacing files, run *Refresh metadata* on them so Jellyfin sees the new track.
**Files that are in use (seeding, playing, Jellyfin scanning):** a file another program has open can't be
rewritten. SubScrub checks before it changes anything: files open elsewhere show **In use** with the program's name
(e.g. *open in qBittorrent*), and pressing save first lists them so you can pause those torrents and click
**Check again** (or skip them). The list re-checks whenever you switch back to SubScrub. If a file gets opened
mid-save anyway, nothing is changed and no extra copy is left behind. Note that once a file is rewritten its
torrent can't keep seeding it — remove the torrent afterwards (keep the files), or use *New file* to keep the
original for seeding.

## Limits

- **Picture-based subtitles can't be edited** (Blu-ray PGS / DVD VobSub). Several movies in the library only have
  those (Dune, The Batman, Iron Man 2, Fargo, The Mandalorian…); they'd need OCR first.
- Tracks OCR'd by release groups contain garbled text the app can't know about (`E d ith.`); it fixes the common
  patterns and flags the rest.

## Testing against your library

`tools\extract-subs.ps1` dumps every English text subtitle track from a drive (videos are only read):

```
powershell -ExecutionPolicy Bypass -File tools\extract-subs.ps1 -Root "F:\" -Out "F:\subdump" -MaxPerFolder 3 -AllFrom Movies
```

Then `python tools\corpus_check.py F:\subdump` shows what SubScrub would do to each track: its confidence,
every line it would ask about, and anything that still looks like SDH after cleaning.

## For tinkering

```
.venv\Scripts\python run.py                     # run from source
.venv\Scripts\python -m tests.test_cleaner      # rule tests
.venv\Scripts\python -m tests.test_library      # regression cases from the real library
.venv\Scripts\python -m tests.test_pipeline <folder with test videos>   # end-to-end remux tests
```

- `subscrub/cleaner.py` – the rules, per-line risk grading, file-level detection (OCR, `Name:` style) and the
  cross-check against the regular track
- `subscrub/batch.py` – picking the SDH track and its regular partner
- `subscrub/subformats.py` – SRT / ASS / WebVTT reading and writing
- `subscrub/media.py` – FFmpeg/MKVToolNix discovery, extraction, remuxing, verification
- `subscrub/ui/` – the PySide6 interface (`main_window.py` Files/Review/Settings, `preview.py` the timeline + list)
