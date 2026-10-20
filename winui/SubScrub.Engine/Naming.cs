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

/// <summary>The rename history, for Undo: %LOCALAPPDATA%\SubScrub\renames.jsonl, one JSON object per line, written
/// the way the Python app writes it so either app can undo the other's renames.</summary>
public static class RenameHistory
{
    public static string HistoryPath()
    {
        var b = Environment.GetEnvironmentVariable("LOCALAPPDATA");
        if (string.IsNullOrEmpty(b))
            b = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".local", "share");
        var p = Path.Combine(b, "SubScrub");
        Directory.CreateDirectory(p);
        return Path.Combine(p, "renames.jsonl");
    }

    /// <summary>json.dumps of a flat object (ASCII only, ", " and ": " between items), as Python writes it.</summary>
    public static string Dumps(IEnumerable<(string Key, object Value)> items)
    {
        var sb = new System.Text.StringBuilder("{");
        var first = true;
        foreach (var (k, v) in items)
        {
            if (!first)
                sb.Append(", ");
            first = false;
            Str(sb, k);
            sb.Append(": ");
            switch (v)
            {
                case bool b: sb.Append(b ? "true" : "false"); break;
                case string s: Str(sb, s); break;
                default: sb.Append(System.Convert.ToString(v, System.Globalization.CultureInfo.InvariantCulture)); break;
            }
        }
        return sb.Append('}').ToString();
    }

    private static void Str(System.Text.StringBuilder sb, string s)
    {
        sb.Append('"');
        foreach (var c in s)
        {
            switch (c)
            {
                case '"': sb.Append("\\\""); break;
                case '\\': sb.Append("\\\\"); break;
                case '\n': sb.Append("\\n"); break;
                case '\r': sb.Append("\\r"); break;
                case '\t': sb.Append("\\t"); break;
                case '\b': sb.Append("\\b"); break;
                case '\f': sb.Append("\\f"); break;
                default:
                    if (c < 0x20 || c > 0x7E)
                        sb.Append($"\\u{(int)c:x4}");
                    else
                        sb.Append(c);
                    break;
            }
        }
        sb.Append('"');
    }

    public static void Record(string batchId, string oldPath, string newPath)
    {
        try
        {
            File.AppendAllText(HistoryPath(), Dumps([("batch", batchId), ("time", DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", System.Globalization.CultureInfo.InvariantCulture)),
                                                     ("old", oldPath), ("new", newPath)]) + "\n", new System.Text.UTF8Encoding(false));
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
        }
    }

    private static List<System.Text.Json.Nodes.JsonObject>? ReadAll()
    {
        try
        {
            var text = File.ReadAllText(HistoryPath(), System.Text.Encoding.UTF8);
            return text.Split('\n').Select(x => x.TrimEnd('\r')).Where(x => Py.Strip(x).Length > 0)
                .Select(x => System.Text.Json.Nodes.JsonNode.Parse(x)!.AsObject()).ToList();
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or System.Text.Json.JsonException
                                      or InvalidOperationException)
        {
            return null;
        }
    }

    private static string S(System.Text.Json.Nodes.JsonObject r, string k) =>
        r[k] is System.Text.Json.Nodes.JsonValue v && v.TryGetValue<string>(out var s) ? s : "";

    /// <summary>The renames of the last batch that wasn't undone: (batch, time, old, new) rows.</summary>
    public static List<(string Batch, string Time, string Old, string New)> LastBatch()
    {
        var rows = ReadAll();
        if (rows == null)
            return [];
        rows = rows.Where(r => !(r["undone"] is System.Text.Json.Nodes.JsonValue u && Session_Truthy(u))).ToList();
        if (rows.Count == 0)
            return [];
        var bid = S(rows[^1], "batch");
        return rows.Where(r => S(r, "batch") == bid).Select(r => (S(r, "batch"), S(r, "time"), S(r, "old"), S(r, "new"))).ToList();
    }

    private static bool Session_Truthy(System.Text.Json.Nodes.JsonNode n) => Config.Truthy(n);

    public static void MarkUndone(string batchId)
    {
        try
        {
            var rows = ReadAll();
            if (rows == null)
                return;
            var sb = new System.Text.StringBuilder();
            foreach (var r in rows)
            {
                if (S(r, "batch") == batchId)
                    r["undone"] = true;
                sb.Append(Dumps(r.Select(kv => (kv.Key, (object)(kv.Value is System.Text.Json.Nodes.JsonValue v && v.TryGetValue<bool>(out var b) ? b : (object)S(r, kv.Key))))));
                sb.Append('\n');
            }
            File.WriteAllText(HistoryPath(), sb.ToString(), new System.Text.UTF8Encoding(false));
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
        }
    }
}

/// <summary>What the engine asks TMDB (the real client, or a stand-in in tests and the scripted QA run).</summary>
public interface ITmdb
{
    List<System.Text.Json.Nodes.JsonObject> SearchMovie(string query, int? year = null);
    System.Text.Json.Nodes.JsonObject Movie(int tmdbId);
    List<System.Text.Json.Nodes.JsonObject> SearchTv(string query, int? year = null);
    System.Text.Json.Nodes.JsonObject Season(int tmdbId, int season);
}

/// <summary>TMDB couldn't be reached or refused the key.</summary>
public sealed class TmdbError(string message) : Exception(message);
