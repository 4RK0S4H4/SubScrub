# SubScrub for Windows (WinUI 3)

SubScrub 4: a native Windows app in WinUI 3 (C#, .NET 8, Windows App SDK 1.8) in the S4 Glass look, with its engine
in C# (`SubScrub.Engine`) running inside the app. It replaced the Python/Qt app of SubScrub 1–3. The Python engine was
ported here stage by stage, checked side by side until both gave identical answers, then retired (issue #44); its
answers are frozen in `tests/golden` and the C# engine is checked against them on every push.

Settings live where the Qt app kept them (`HKEY_CURRENT_USER\Software\SubScrub\SubScrub`), so an upgrade from 3.x
keeps them and the TMDB key. The rename history is `%LOCALAPPDATA%\SubScrub\renames.jsonl`, the same file and format
as before, so Undo Renames works across versions.

## Build (Windows)

    dotnet publish winui/SubScrub/SubScrub.csproj -c Release -r win-x64 -p:Platform=x64 -o winui-out
    winui-out\SubScrub.exe [files or folders...] [--log <file>]

Unpackaged and self-contained, ReadyToRun and trimmed (partial): the output folder runs as it is. Paths on the
command line are added at start; starting it again while it runs hands the new paths to the open window
(`Program.cs`), which is how Explorer's "Clean subtitles with SubScrub" (added by the installer) opens several files
in one window. `installer/SubScrub-WinUI.iss` builds `SubScrub-Setup.exe` (Inno Setup, per user, no admin).

## The engine and its checks

`SubScrub.Engine` is plain .NET 8 with no Windows dependency (the Windows parts are in `WinApi.cs` with Linux
fallbacks), so it builds and is tested on Linux too.

    python3 -m tests.make_samples --library      # generated sample videos (needs FFmpeg; MKVToolNix for some)
    python3 -m tests.make_samples --edge
    python3 -m tests.make_samples
    dotnet run --project winui/SubScrub.Engine.Parity -c Release -- golden tests/golden .
    dotnet run --project winui/SubScrub.Engine.Tests -c Release -- .

- The **golden check** replays everything the retired Python engine answered: every cleaner call its tests made, the
  fixtures and every sample track under each option, five sessions of file-list views, Review and save questions,
  reading the sample videos, 120+ saves, 938 title matches and the commands end to end. See `tests/golden/README.md`,
  including how to update the answers when a rule changes on purpose (`--update`).
- The **engine tests** run on real videos: the safe swap and the Recycle Bin, the pipeline, awkward files, the files
  in use check, undoing renames, settings in the registry, memory after saving and two saves at once.
- `SubScrub.Engine.Tests --qa-check <qa.json> <library>` checks what the scripted QA run saved.

## Scripted QA and screenshots

    SubScrub.exe --shots <folder> --library <copy of samples\library> --theme dark|light [--size 1920x1080] [--no-mica]

Drives the real app on a copy of the sample library with a throwaway settings file and a stand-in for TMDB: adds the
videos, opens Review (to check, changes, a kept line, look closer, regular subtitles only), picks a title, opens
Settings, then saves everything, answering each question. Every screen is saved twice: `02-files-dark.png` is WinUI's
own render of the window, `screen-02-files-dark.png` a capture of the real screen. It also writes
`timings-dark.txt` (start, memory, read and save times) and `qa-dark.json`, which `--qa-check` verifies. CI runs it
in both themes and at 1000x680, and pushes the images to the throwaway branch `winui-screenshots`.

## Layout

- `Program.cs`: the entry point (one window; a second start forwards its paths). `App.xaml`, `MainWindow.xaml` and
  its partial files: the window (backdrop, top bar, welcome, Files, Review, footer, the drop veil, updates, window
  placement, forwarded paths).
- `Themes/Tokens.xaml` (colours) and `Themes/Controls.xaml` (fonts, text styles and an S4 template for every control
  used). `Controls/SolarData.g.cs` holds the Solar icons and `Assets/Fonts` Inter with the open digits built in, one
  family per weight. These were first generated from the Qt app's sources; they are now the sources themselves.
- `Controls/`: `SolarIcon`, `Segmented`, `OptionRow`, `FileRow`, `Parts.cs` (badge, banner, track tile, edit row,
  choice button), `Preview.cs` (caption screen, timeline, the before/after list).
- `Views/`: `SettingsDialog` (built once and reused) and `Dialogs.cs` (questions, the title picker, the log).
- `Models/Views.cs`: what the engine sends back. `Services/`: `Core` (the engine, in-process, answering commands with
  JSON), theme lookups, DPI, the QA run (`Shots`) and the XAML check (`Diagnose`).
- `../SubScrub.Engine/`: the engine. `Cleaner.cs` (the rules, risk grading, OCR fixes, names), `SubFormats.cs`
  (SRT/ASS/VTT), `Media*.cs` (FFmpeg/MKVToolNix: probing, extracting, remuxing, the verified swap), `WinApi.cs`
  (Recycle Bin, Restart Manager, drives), `Naming*.cs` and `Tmdb.cs` (TMDB names, rename history), `Batch.cs`
  (track picking, sidecars), `Session*.cs` (the file list, Review, the questions at save time, the batch save,
  reading), `Commands.cs` (the command protocol), `Py*.cs` (the few Python string, sorting and path rules the
  results were frozen with).
- `../SubScrub.Engine.Parity/`: the golden check. `../SubScrub.Engine.Tests/`: the engine tests and `--qa-check`.
