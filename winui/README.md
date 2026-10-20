# SubScrub for Windows (WinUI 3), in progress

A native rewrite of SubScrub's window in WinUI 3 (C#, .NET 8, Windows App SDK 1.8), keeping the S4 Glass look. The
Python/Qt app in `subscrub/` is still the one that ships; this folder grows alongside it until it matches. It ships as
SubScrub 4.0 once it does (until then the csproj says `4.0.0-dev.N`; the Qt app stays on 3.x).

**Stage 2 (now): the real engine.** The window is C#; the cleaning, muxing, TMDB lookups, safe swap and the "files in
use" check are the Python engine, bundled with PyInstaller as `core\subscrub-core.exe` next to `SubScrub.exe`. The
window starts it and talks to it with one JSON object per line over stdin/stdout (`subscrub/helper.py` describes the
protocol; `subscrub/core.py` is the Qt window's logic without Qt). Working: Add Files, Add Folder, drag and drop,
reading with real states and badges, `.srt` sidecars, the files-in-use warning, every question at save time
(lines it isn't sure about, unsure titles, rewriting in place, overwriting), the TMDB title picker, saving with
progress, Stop, Review (tracks, edits per file, the caption screen, timeline and before/after list with one-row
updates) and Settings saved to disk.

Settings live where the Qt app keeps them (`HKEY_CURRENT_USER\Software\SubScrub\SubScrub`), so both apps share one set
of settings and one TMDB key.

**Porting the engine to C# (in stages, decided 2026-10-19).** Each stage moves one part of the Python engine into
`winui/SubScrub.Engine` (plain .NET 8, no Windows dependency, so it builds and is checked on Linux too) and must give
*identical* results: `python tools/cleaner_parity.py --library samples/library` runs both on every cleaner call the
test suites make, the fixtures and every sample track under each option, and fails on any difference in text,
rules, risk, reasons, notes, counts or the saved file. `python tools/engine_parity.py --library samples/library`
does the same for the rest of the engine: real Python sessions read the sample library, the edge cases, subtitle files
on their own and a library after a real save, then a script of actions (every view, track changes, kept lines,
per-file edits, settings, every save question with several sets of answers and programs holding files open) runs on
both engines from the same state. CI runs both checks on Linux (`screenshots` job) and on Windows (`winui` job). The
window keeps using the Python helper for anything not ported yet.

- Stage 1 (done): the cleaner (`Cleaner.cs`) and the subtitle formats (`SubFormats.cs`), with `Py.cs` for the
  few Python string rules the results depend on. Change `subscrub/cleaner.py` first, then the port, then run the check.
- Stage 2 (done): the file list's logic (`Batch.cs`: track picking, sidecars, pairing dropped files, names) and the
  views of `core.py` (`Session.cs`, `SessionReview.cs`, `SessionSave.cs`: verdicts, rows, the summary, Review, the
  questions at save time), returning the same JSON the helper sends. `Media.cs` has the track model and file
  matching, `Naming.cs` the name rendering, `PyLib.cs` Python's formatting, sorting, path rules and difflib.
- Next: probing and extracting, then muxing and the safe swap, TMDB and the files-in-use check. When the last one is
  done, the `core` folder goes.

## Build (Windows)

    dotnet publish winui/SubScrub/SubScrub.csproj -c Release -r win-x64 -p:Platform=x64 -o winui-out
    python -m PyInstaller --noconfirm --onedir --console --name subscrub-core --distpath helper-dist ^
        --exclude-module PySide6 --exclude-module tkinter --hidden-import send2trash run_core.py
    xcopy /e /i helper-dist\subscrub-core winui-out\core
    winui-out\SubScrub.exe

Unpackaged and self-contained: the output folder runs as it is. FFmpeg and MKVToolNix are found the same way as the
Qt app finds them. Without `core\subscrub-core.exe`, a build run from a source checkout starts `python -m
subscrub.helper` in the repository instead (or `SUBSCRUB_CORE`, a full command line). CI (`winui` job in
`.github/workflows/build.yml`) builds both on every push and attaches the folder as `SubScrub-WinUI`.

## Scripted QA and screenshots

    SubScrub.exe --shots <folder> --library <copy of samples\library> --theme dark|light [--size 1920x1080] [--no-mica]

Drives the real app on a copy of the sample library (`python -m tests.make_samples --library`) with a throwaway
settings file and a stand-in for TMDB: adds the videos, opens Review (to check, changes, a kept line, look closer,
regular subtitles only), picks a title, opens Settings, then saves everything, answering each question. Every screen
is saved twice: `02-files-dark.png` is WinUI's own render of the window (the S4 page where Mica would be),
`screen-02-files-dark.png` a capture of the real screen. It also writes `timings-dark.txt` and `qa-dark.json`, which
`python tools/winui_qa_check.py qa-dark.json samples/library` checks with ffprobe and mkvmerge. CI runs it in both
themes and at 1000x680, and pushes the images to the throwaway branch `winui-screenshots`.

## Generated files

`Themes/Tokens.xaml` (colours from `subscrub/ui/theme.py`), `Controls/SolarData.g.cs` (icons from
`subscrub/ui/solar.py`) and `Assets/Fonts/*.ttf` (Inter with the open digits and flat-top 3 built in, one family per
weight) come from `python tools/make_winui.py`. Change the sources and run it again; don't edit the outputs.

## Layout

- `App.xaml`, `MainWindow.xaml`: the window (backdrop, top bar, welcome, Files, Review, footer, the drop veil).
- `Themes/Controls.xaml`: fonts, text styles and an S4 template for every control used (buttons, segmented tabs,
  settings nav, switch, text and password fields, the line list, the dialog).
- `Controls/`: `SolarIcon`, `Segmented`, `OptionRow`, `FileRow`, `Parts.cs` (badge, banner, track tile, edit row,
  choice button), `Preview.cs` (caption screen, timeline, the before/after list).
- `Views/`: `SettingsDialog` (built once and reused) and `Dialogs.cs` (questions, the title picker, the log).
- `../SubScrub.Engine/`: the engine in C# (stage 1: `Cleaner.cs`, `SubFormats.cs`); `../SubScrub.Engine.Parity/`
  runs it for the parity check.
- `Models/Views.cs`: what the engine sends back. `Services/`: `Core` (the engine process), `Job` (the engine ends
  with the app), theme lookups, DPI, the QA run (`Shots`) and the XAML check (`Diagnose`).
