using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using SubScrub.Engine;

/// <summary>Checks what the WinUI app's scripted QA run saved (SubScrub.exe --shots ... --library ...) with ffprobe
/// and mkvmerge: every video saved, one default English track named "English" with no sound tags left in it, other
/// languages gone, the regular-track rule kept (an SDH track or .sdh.srt stays when a regular track was cleaned),
/// video and audio untouched, and nothing else left behind in the folder.
///   SubScrub.Engine.Tests --qa-check &lt;qa-dark.json&gt; &lt;pristine sample library&gt;
/// Exits non-zero and says why when something is wrong.</summary>
internal static class QaCheck
{
    private static readonly Regex SoundTag = new(@"\[[^\]\n]{2,}\]");
    private static readonly Regex Music = new("[♪♫]");

    public static int Run(string qaFile, string pristine)
    {
        var qa = JsonNode.Parse(File.ReadAllText(qaFile))!;
        var tools = Media.FindTools();
        var problems = new List<string>();
        var checkedLines = new List<string>();
        var failed = qa["counts"]?["failed"]?.GetValue<int>() ?? 0;
        if (failed != 0)
            problems.Add($"{failed} file(s) failed: {qa["status"]}");
        var originals = Directory.EnumerateFiles(pristine, "*.mkv").ToDictionary(Path.GetFileName, p => Media.Probe(tools, p));
        var outputs = qa["outputs"]?.AsArray().Select(x => x!.GetValue<string>()).ToList() ?? [];
        if (outputs.Count != originals.Count)
            problems.Add($"{outputs.Count} videos saved, expected {originals.Count}");
        var library = qa["library"]!.GetValue<string>();
        foreach (var outPath in outputs)
        {
            var name = Path.GetFileName(outPath);
            if (!File.Exists(outPath))
            {
                problems.Add($"{name}: not there after saving");
                continue;
            }
            var info = Media.Probe(tools, outPath);
            var src = originals.Values.MinBy(o => Math.Abs(o.Duration - info.Duration))!;
            if (Math.Abs(src.Duration - info.Duration) > 1.0)
                problems.Add($"{name}: duration {info.Duration:F1}s doesn't match any original");
            foreach (var kind in new[] { "video", "audio" })
            {
                if (info.Count(kind) != src.Count(kind))
                    problems.Add($"{name}: {info.Count(kind)} {kind} streams, the original had {src.Count(kind)}");
            }
            if (Regex.IsMatch(name, @"\d{3,4}p|BluRay|WEB-DL"))
                problems.Add($"{name}: not renamed");
            var defaults = info.Subs.Where(t => t.Default).ToList();
            if (defaults.Count != 1)
            {
                problems.Add($"{name}: {defaults.Count} default subtitle tracks");
                continue;
            }
            var d = defaults[0];
            if (Media.LangShort(d.Language) != "en" || d.Title != "English")
                problems.Add($"{name}: the default track is {d.Language} “{d.Title}”, not English");
            var others = info.Subs.Where(t => Media.LangShort(t.Language) is not ("en" or "") && !t.Forced).ToList();
            if (others.Count > 0)
                problems.Add($"{name}: other-language tracks left: {string.Join(", ", others.Select(t => t.Language))}");
            var srcSdh = src.Subs.Where(t => t.SdhFlagged).ToList();
            var srcPlain = src.Subs.Where(t => Media.LangShort(t.Language) == "en" && !t.SdhFlagged).ToList();
            var sdhNow = info.Subs.Where(t => t.SdhFlagged).ToList();
            if (srcPlain.Count > 0 && srcSdh.Count > 0 && sdhNow.Count == 0)
                problems.Add($"{name}: had a regular and an SDH English track; the SDH track should have been kept");
            if (srcPlain.Count == 0 && sdhNow.Count > 0)
                problems.Add($"{name}: only had an SDH track; it should have been replaced by the cleaned one");
            var tmp = Directory.CreateTempSubdirectory("subscrub_qa_").FullName;
            try
            {
                var paths = Media.ExtractSubs(tools, info, [d], tmp);
                var doc = SubFormats.Load(paths[d.Index], d.Fmt);
                var tags = doc.Cues.Where(c => SoundTag.IsMatch(c.Text)).Select(c => c.Text).ToList();
                var notes = doc.Cues.Where(c => Music.IsMatch(c.Text)).Select(c => c.Text).ToList();
                if (srcSdh.Count > 0 && srcPlain.Count == 0 && (tags.Count > 0 || notes.Count > 0))
                    problems.Add($"{name}: {tags.Count} sound tags and {notes.Count} ♪ left in the cleaned track, e.g. “{tags.Concat(notes).First()}”");
                checkedLines.Add($"{name}: {info.Subs.Count} subtitle tracks, default “{d.Title}” ({doc.Cues.Count} lines)");
            }
            finally
            {
                Directory.Delete(tmp, true);
            }
        }
        // an SDH sidecar stays when the video's regular track was the one cleaned (the SDH track is kept, renamed with
        // its video); anything else next to the videos is a leftover
        var stems = outputs.Select(o => Path.GetFileNameWithoutExtension(o).ToLowerInvariant()).ToHashSet();
        var left = Directory.EnumerateFileSystemEntries(library).Select(Path.GetFileName).Where(f =>
            f!.Contains(".subscrub-tmp") || Path.GetExtension(f).ToLowerInvariant() is ".srt" or ".ass" or ".vtt"
            && !(f.ToLowerInvariant().Contains(".sdh.") && stems.Any(s => f.ToLowerInvariant().StartsWith(s + ".", StringComparison.Ordinal)))).ToList();
        if (left.Count > 0)
            problems.Add($"left in the folder: {string.Join(", ", left)}");
        Console.WriteLine(string.Join("\n", checkedLines));
        if (problems.Count > 0)
        {
            Console.WriteLine("\nPROBLEMS:\n  " + string.Join("\n  ", problems));
            return 1;
        }
        Console.WriteLine($"\nAll {outputs.Count} saved videos check out ({qa["theme"]}).");
        return 0;
    }
}
