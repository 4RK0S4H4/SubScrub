# SubScrub for Windows (WinUI 3), in progress

A native rewrite of SubScrub's window in WinUI 3 (C#, .NET 8, Windows App SDK 1.8), keeping the S4 Glass look. The
Python/Qt app in `subscrub/` is still the one that ships; this folder grows alongside it until it matches.

**Stage 1 (now):** the window with real Mica and the top bar as the title bar, the Files and Review tabs, the Files list
(state bars and badges) and Settings as a native dialog, all with **sample data**. It reads and changes nothing on disk,
and Settings is kept in memory only.

Next: a Python helper exe (`subscrub-core.exe`) that the window drives with JSON commands (probe, clean, save, rename),
then porting the engine to C# module by module, checked against `tests/fixtures/`.

## Build (Windows)

    dotnet publish winui/SubScrub/SubScrub.csproj -c Release -r win-x64 -p:Platform=x64 -o winui-out
    winui-out\SubScrub.exe

Unpackaged and self-contained: the output folder runs as it is, with nothing else to install. CI (`winui` job in
`.github/workflows/build.yml`) builds it on every push and attaches the folder as `SubScrub-WinUI`.

## Screenshots

    SubScrub.exe --shots <folder> --theme dark|light [--size 1920x1080] [--no-mica]

Saves every screen twice: `01-files-dark.png` is WinUI's own render of the window (the S4 page shows where Mica would
be), `screen-01-files-dark.png` is a capture of the real screen (Mica, caption buttons, acrylic). Also writes
`timings-dark.txt`: how long Settings takes to open. CI attaches them as `winui-screenshots`.

## Generated files

`Themes/Tokens.xaml` (colours from `subscrub/ui/theme.py`), `Controls/SolarData.g.cs` (icons from
`subscrub/ui/solar.py`) and `Assets/Fonts/*.ttf` (Inter with the open digits and flat-top 3 built in, one family per
weight) come from `python tools/make_winui.py`. Change the sources and run it again; don't edit the outputs.

## Layout

- `App.xaml`, `MainWindow.xaml`: the window (backdrop, top bar, Files, Review, footer).
- `Themes/Controls.xaml`: fonts, text styles and an S4 template for every control used (buttons, segmented tabs,
  settings nav, switch, text and password fields, the dialog).
- `Controls/`: `SolarIcon`, `Segmented`, `OptionRow`, `FileRow`.
- `Views/SettingsDialog`: Settings as a `ContentDialog`, built once and reused.
- `Models/`: the sample files and the settings model. `Services/`: theme lookups, DPI, the screenshot mode.
