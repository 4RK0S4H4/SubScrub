namespace SubScrub.Models;

/// <summary>Made-up files for the Stage 1 slice, the same as the Python app's sample library (tests/make_samples.py),
/// so screenshots of both apps can be compared side by side. No real media is involved.</summary>
public static class SampleData
{
    public static IReadOnlyList<FileItem> Files { get; } =
    [
        new("Blade.Runner.1982.The.Final.Cut.1080p.BluRay.mkv", "Blade Runner.mkv", null,
            "English SDH", 1130, FileState.Bad, "Look Closer"),
        new("Dune.Part.Two.2024.2160p.UHD.BluRay.x265.mkv", "Dune - Part Two.mkv", null,
            "English SDH", 1405, FileState.Ok, "Ready"),
        new("Planet.Earth.III.S01E02.Ocean.2160p.iP.WEB-DL.mkv", "Planet Earth III S1 E2.mkv", null,
            "English: regular subtitles", 0, FileState.Ok, "Ready"),
        new("Severance.S02E04.Woes.Hollow.2160p.ATVP.WEB-DL.DDP5.1.mkv", "Severance S2 E4.mkv", null,
            "English, 2 other-language subtitle tracks will be removed", 125, FileState.Ok, "Ready"),
        new("Slow.Horses.S04E01.Identity.Theft.1080p.ATVP.WEB-DL.mkv", null,
            "Not sure which title this is, choose one in Review", "English", 108, FileState.Ok, "Ready"),
        new("The.Expanse.S01E03.Remember.the.Cant.1080p.BluRay.x264.mkv", "The Expanse S1 E3.mkv", null,
            "English, 1 other-language subtitle track will be removed", 109, FileState.Ok, "Ready"),
    ];
}
