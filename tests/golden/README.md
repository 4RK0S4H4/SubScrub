# Golden answers from the retired Python engine

SubScrub's engine was first written in Python and then ported to C# (`winui/SubScrub.Engine`), checked side by side
on every push until the C# engine gave exactly the same answers. When the Python engine was removed (issue #44), its
answers were frozen here so that check keeps running:

- `cleaner.json.br`: every cleaner call the old test suites made (including the corpus of tricky lines), the
  fixtures and every track of the generated sample library under each option, documents at the edges of the "to
  check" grades, and file decoding. For each: the cleaned text, rules, risk, reasons, notes, counts, the saved file
  and a read/write round trip.
- `engine.json.br`: the file list, Review and the save questions for five sessions (1,400+ views and actions), the
  file rules (pairing, sidecars, languages, names), reading the sample videos, 120+ real saves in every mode with
  both tools, TMDB title matching on hundreds of names, and the app's commands end to end.

Both are Brotli-compressed JSON: `{"cases": ..., "expected": ...}` (plus `"layout"`, the folders the engine check
works in). They were made by `freeze_golden.py` (kept here for the record) just before the Python engine was removed, from the
hand-written fixtures in `tests/fixtures` and the generated sample videos only. No real media, no one's subtitles.

## Running the check

```
python -m tests.make_samples --library      # the sample videos the engine check copies by name
python -m tests.make_samples --edge
python -m tests.make_samples
dotnet run --project winui/SubScrub.Engine.Parity -c Release -- golden tests/golden .
```

Paths are written as `<root>`; what depends on a video's exact bytes or the FFmpeg version (file sizes, ffprobe's
raw stream details, uncommon disposition flags, tool paths) is left out on both sides, and durations are compared to
a tenth of a second. The check runs on Linux, where the answers were made (Windows path rules differ; the C# engine
tests and the WinUI QA run cover Windows).

## Changing a rule on purpose

Change the C# engine, run the check, read every difference it lists, then run it again with `--update` to make the
C# answers the new golden ones. Say in the pull request what changed and why.
