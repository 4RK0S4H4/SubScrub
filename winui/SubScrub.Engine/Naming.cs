using System.Text;
using System.Text.RegularExpressions;

namespace SubScrub.Engine;

/// <summary>What a release name says: title, year, season/episodes.</summary>
public sealed class Parsed
{
    public string Title { get; set; } = "";
    public int? Year { get; set; }
    public int? Season { get; set; }
    public List<int> Episodes { get; set; } = [];
    public string EpisodeTitle { get; set; } = "";      // what the file name says (used only if TMDB has nothing)

    public bool IsEpisode => Season != null && Episodes.Count > 0;
}

/// <summary>One possible identity for a file.</summary>
public sealed class Choice
{
    public string Kind { get; set; } = "movie";        // movie | episode
    public int TmdbId { get; set; }
    public string Title { get; set; } = "";             // movie title or show name
    public int? Year { get; set; }
    public int? Season { get; set; }
    public List<int> Episodes { get; set; } = [];
    public List<string> EpisodeTitles { get; set; } = [];
    public int? Runtime { get; set; }                   // minutes, when TMDB knows it
    public double Score { get; set; }
    public List<string> Why { get; set; } = [];

    public string Label()
    {
        if (Kind == "episode")
            return $"{Title}, {EpisodeCode()}" + (EpisodeTitles.Count > 0 ? $": {EpisodeTitles[0]}" : "");
        return Title + (Year is { } y && y != 0 ? $" ({y})" : "");
    }

    /// <summary>'S7 E2' (no zero padding); a multi-episode file reads 'S2 E1-E2'.</summary>
    public string EpisodeCode()
    {
        if (Episodes.Count == 0)
            return "";
        var code = $"S{Naming.Num(Season)} E{Episodes[0]}";
        if (Episodes.Count > 1)
            code += $"-E{Episodes[^1]}";
        return code;
    }
}

public sealed class TitleMatch
{
    public Parsed Parsed { get; set; } = new();
    public List<Choice> Choices { get; set; } = [];
    public bool Confident { get; set; }
    public string Note { get; set; } = "";

    public Choice? Best => Choices.Count > 0 ? Choices[0] : null;
}

/// <summary>Clean file names from TMDB. A port of subscrub/naming.py (stage 2: names; the lookups follow).</summary>
public static partial class Naming
{
    internal static string Num(int? n) => n?.ToString(System.Globalization.CultureInfo.InvariantCulture) ?? "None";

    private static readonly HashSet<string> Reserved = BuildReserved();

    private static HashSet<string> BuildReserved()
    {
        var s = new HashSet<string> { "CON", "PRN", "AUX", "NUL" };
        for (var i = 1; i < 10; i++)
        {
            s.Add($"COM{i}");
            s.Add($"LPT{i}");
        }
        return s;
    }

    private static readonly Regex ColonSpace = new(@"\s*:\s+");
    private static readonly Regex Slash = new(@"\s*/\s*");
    private static readonly Regex Wild = new("[<>?*]");
    private static readonly Regex Control = new("[\x00-\x1f]");
    private static readonly Regex Spaces = new(@"\s{2,}");

    /// <summary>Make a title usable as a Windows file name, the way media managers usually do it:
    /// 'Batman v Superman: Dawn of Justice' -> 'Batman v Superman - Dawn of Justice'.</summary>
    public static string SafeName(string text)
    {
        var t = text.Normalize(NormalizationForm.FormC);
        t = ColonSpace.Replace(t, " - ");
        t = t.Replace(":", "-");
        if (t.Contains('/'))
            t = Slash.Replace(t, "-");
        t = t.Replace("\\", "-").Replace("|", "-");
        t = t.Replace("\"", "'");
        t = Wild.Replace(t, "");
        t = Control.Replace(t, "");
        t = Py.Strip(Spaces.Replace(t, " "));
        t = t.TrimEnd('.', ' ');
        if (Reserved.Contains(t.Split('.')[0].ToUpperInvariant()))
            t += "_";
        return t.Length > 0 ? t : "Untitled";
    }

    /// <summary>The new file name (no folder). Movies: 'Title.mkv' or 'Title (Year).mkv'.
    /// Episodes: 'The Walking Dead S7 E2.mkv' (multi-episode files: 'Show S2 E1-E2.mkv').</summary>
    public static string Render(Choice choice, string ext, bool withYear = false, int maxLen = 180)
    {
        ext = ext.StartsWith('.') ? ext : "." + ext;
        if (choice.Kind == "movie")
        {
            var name = SafeName(choice.Title);
            if (withYear && choice.Year is { } y && y != 0)
                name += $" ({y})";
            return PyFmt.Head(name, maxLen).TrimEnd('.', ' ') + ext;
        }
        var code = choice.EpisodeCode();
        var show = PyFmt.Head(SafeName(choice.Title), Math.Max(10, maxLen - code.Length - 1)).TrimEnd('.', ' ');
        return Py.Strip($"{show} {code}") + ext;
    }
}
