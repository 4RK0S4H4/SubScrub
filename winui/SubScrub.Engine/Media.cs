using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace SubScrub.Engine;

/// <summary>The video side of the engine: FFmpeg/MKVToolNix, tracks, and the subtitle files next to a video. A port
/// of subscrub/media.py (stage 2: the track model, languages and the file matching; probing and muxing follow).</summary>
public sealed class Tools
{
    public string? Ffmpeg { get; set; }
    public string? Ffprobe { get; set; }
    public string? Mkvmerge { get; set; }

    public bool Ready => !string.IsNullOrEmpty(Ffmpeg) && !string.IsNullOrEmpty(Ffprobe);
}

public sealed class ToolError(string message) : Exception(message);

public sealed class Cancelled() : Exception("Cancelled");

public sealed class SubTrack
{
    public int Index { get; set; }                  // absolute stream index in the file
    public int SubPos { get; set; }                 // position among subtitle tracks
    public string Codec { get; set; } = "";
    public string Language { get; set; } = "";
    public string Title { get; set; } = "";
    public bool Default { get; set; }
    public bool Forced { get; set; }
    public bool HearingImpaired { get; set; }
    public Dictionary<string, int> Disposition { get; set; } = [];
    public string External { get; set; } = "";      // a separate subtitle file next to the video ("" = inside it)
    public bool Cleaned { get; set; }               // a track SubScrub wrote on an earlier run (tagged SUBSCRUB=cleaned)

    public bool FromSubscrub => Cleaned || (Title ?? "").ToLowerInvariant().EndsWith("(dialogue only)", StringComparison.Ordinal);

    public bool TextBased => Media.TextCodecs.ContainsKey(Codec);

    public string Fmt => Media.TextCodecs.TryGetValue(Codec, out var f) ? f.Fmt : "srt";

    public string CodecLabel => Media.CodecLabels.TryGetValue(Codec, out var l) ? l : (Codec.Length > 0 ? Codec.ToUpperInvariant() : "Unknown");

    public string LangLabel => Media.LangName(Language);

    public bool SdhFlagged => HearingImpaired || Media.SdhName.IsMatch(Title ?? "");

    public string DispoFlags(bool dropDefault)
    {
        var flags = Disposition.Where(kv => kv.Value != 0 && !(dropDefault && kv.Key == "default")).Select(kv => kv.Key).ToList();
        return flags.Count > 0 ? string.Join("+", flags) : "0";
    }
}

public sealed class MediaInfo
{
    public string Path { get; set; } = "";
    public string Kind { get; set; } = "";          // mkv | mp4 | webm | other | subtitle
    public string FormatName { get; set; } = "";
    public double Duration { get; set; }
    public long Size { get; set; }
    public List<JsonObject> Streams { get; set; } = [];
    public List<SubTrack> Subs { get; set; } = [];
    public string? Companion { get; set; }          // a standalone subtitle file: the video it belongs to
    public int Chapters { get; set; }

    public string Name => PyPath.Name(Path);

    public int Count(string codecType) => Streams.Count(s => s["codec_type"]?.GetValue<string>() == codecType);
}

public sealed class SaveOptions
{
    // video sources: replace | new | sidecar; subtitle sources: embed | replace | new
    public string Mode { get; set; } = "replace";
    public bool KeepOriginal { get; set; }
    public bool MakeDefault { get; set; } = true;
    public string TrackName { get; set; } = "";
    public string Language { get; set; } = "";
    public bool OverwriteSidecar { get; set; } = true;
    public bool RecycleSourceSub { get; set; } = true;
    public List<string> RecyclePaths { get; set; } = [];
    public bool DropOtherLanguages { get; set; }
    public string KeepLanguage { get; set; } = "eng";
    public string RenameTo { get; set; } = "";
    public List<string> RenameSidecars { get; set; } = [];
}

public sealed record SaveResult(string Path, string Note = "");

public static partial class Media
{
    public static readonly Dictionary<string, (string Fmt, string Codec)> TextCodecs = new()
    {
        ["subrip"] = ("srt", "copy"), ["srt"] = ("srt", "srt"),
        ["ass"] = ("ass", "copy"), ["ssa"] = ("ass", "ass"),
        ["webvtt"] = ("vtt", "copy"),
        ["mov_text"] = ("srt", "srt"), ["text"] = ("srt", "srt"),
        ["microdvd"] = ("srt", "srt"), ["subviewer"] = ("srt", "srt"), ["subviewer1"] = ("srt", "srt"),
        ["realtext"] = ("srt", "srt"), ["sami"] = ("srt", "srt"), ["stl"] = ("srt", "srt"),
        ["mpl2"] = ("srt", "srt"), ["pjs"] = ("srt", "srt"), ["vplayer"] = ("srt", "srt"), ["jacosub"] = ("srt", "srt"),
    };

    public static readonly Dictionary<string, string> CodecLabels = new()
    {
        ["subrip"] = "SRT", ["srt"] = "SRT", ["ass"] = "ASS", ["ssa"] = "SSA", ["webvtt"] = "WebVTT", ["mov_text"] = "MP4 text",
        ["hdmv_pgs_subtitle"] = "PGS", ["dvd_subtitle"] = "VobSub", ["dvb_subtitle"] = "DVB", ["xsub"] = "XSUB",
        ["dvb_teletext"] = "Teletext", ["arib_caption"] = "ARIB",
    };

    // ISO 639-2 -> (name, ISO 639-1), in the Python dict's order (the first code for a short one wins)
    private static readonly (string Code, string Name, string Short)[] LangTable =
    [
        ("eng", "English", "en"), ("spa", "Spanish", "es"), ("fre", "French", "fr"), ("fra", "French", "fr"),
        ("ger", "German", "de"), ("deu", "German", "de"), ("ita", "Italian", "it"), ("por", "Portuguese", "pt"),
        ("jpn", "Japanese", "ja"), ("kor", "Korean", "ko"), ("chi", "Chinese", "zh"), ("zho", "Chinese", "zh"),
        ("rus", "Russian", "ru"), ("ara", "Arabic", "ar"), ("hin", "Hindi", "hi"), ("dut", "Dutch", "nl"),
        ("nld", "Dutch", "nl"), ("swe", "Swedish", "sv"), ("nor", "Norwegian", "no"), ("nob", "Norwegian", "nb"),
        ("dan", "Danish", "da"), ("fin", "Finnish", "fi"), ("pol", "Polish", "pl"), ("tur", "Turkish", "tr"),
        ("gre", "Greek", "el"), ("ell", "Greek", "el"), ("heb", "Hebrew", "he"), ("tha", "Thai", "th"),
        ("vie", "Vietnamese", "vi"), ("ind", "Indonesian", "id"), ("may", "Malay", "ms"), ("msa", "Malay", "ms"),
        ("ukr", "Ukrainian", "uk"), ("cze", "Czech", "cs"), ("ces", "Czech", "cs"), ("hun", "Hungarian", "hu"),
        ("rum", "Romanian", "ro"), ("ron", "Romanian", "ro"), ("bul", "Bulgarian", "bg"), ("hrv", "Croatian", "hr"),
        ("srp", "Serbian", "sr"), ("slv", "Slovenian", "sl"), ("slo", "Slovak", "sk"), ("slk", "Slovak", "sk"),
        ("ben", "Bengali", "bn"), ("tam", "Tamil", "ta"), ("tel", "Telugu", "te"), ("fil", "Filipino", "fil"),
        ("tgl", "Tagalog", "tl"), ("per", "Persian", "fa"), ("fas", "Persian", "fa"), ("cat", "Catalan", "ca"),
        ("ice", "Icelandic", "is"), ("isl", "Icelandic", "is"), ("est", "Estonian", "et"), ("lav", "Latvian", "lv"),
        ("lit", "Lithuanian", "lt"), ("und", "Unknown language", ""),
    ];

    public static readonly Dictionary<string, (string Name, string Short)> Langs =
        LangTable.ToDictionary(x => x.Code, x => (x.Name, x.Short));

    public static readonly Regex SdhName = new(@"\b(sdh|hi|cc|hoh|hearing[ -]?impaired|closed captions?|deaf)\b",
                                                RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

    public static readonly HashSet<string> VideoExts =
    [
        ".mkv", ".mp4", ".m4v", ".mov", ".webm", ".avi", ".ts", ".m2ts", ".mts", ".wmv", ".ogm", ".mk3d",
        ".flv", ".mpg", ".mpeg", ".vob", ".3gp",
    ];
    public static readonly HashSet<string> TextSubExts = [".srt", ".ass", ".ssa", ".vtt"];
    public static readonly HashSet<string> ImageSubExts = [".sup", ".sub", ".idx"];
    public static readonly HashSet<string> SdhTokens = ["sdh", "hi", "cc", "hoh"];
    public const string TmpMark = ".subscrub-tmp";
    public const int SidecarBase = 100000;          // track index given to subtitle files that sit next to a video

    private static readonly Dictionary<string, string> ShortTo3 = BuildShortTo3();

    private static Dictionary<string, string> BuildShortTo3()
    {
        var d = new Dictionary<string, string>();
        foreach (var (code, _, s) in LangTable)
        {
            if (s.Length == 2 && code != "und" && !SdhTokens.Contains(s))      // ".hi." means hearing-impaired
                d.TryAdd(s, code);
        }
        return d;
    }

    public static string LangName(string? code)
    {
        var c = (string.IsNullOrEmpty(code) ? "und" : code).ToLowerInvariant();
        if (Langs.TryGetValue(c, out var l))
            return l.Name;
        return c.Length > 0 ? c.ToUpperInvariant() : "Unknown language";
    }

    public static string LangShort(string? code)
    {
        var c = (code ?? "").ToLowerInvariant().Replace('_', '-').Split('-')[0];
        if (c == "und")
            return "";
        return Langs.TryGetValue(c, out var l) ? l.Short : c;
    }

    public static string ContainerKind(string path)
    {
        var ext = PyPath.Suffix(path).ToLowerInvariant();
        if (ext is ".mkv" or ".mk3d" or ".mka")
            return "mkv";
        if (ext is ".mp4" or ".m4v" or ".mov")
            return "mp4";
        if (ext == ".webm")
            return "webm";
        return TextSubExts.Contains(ext) ? "subtitle" : "other";
    }

    // ================================================================ subtitle files next to a video

    private static string[] NameTokens(string path) => PyPath.Stem(path).Split('.');

    /// <summary>'Movie (2020).en.sdh.srt' -> 'eng'</summary>
    public static string GuessLanguage(string path)
    {
        var toks = NameTokens(path);
        for (var i = toks.Length - 1; i >= 1; i--)
        {
            var t = toks[i].ToLowerInvariant().Split('-')[0].Split('_')[0];
            if (Langs.ContainsKey(t) && t != "und")
                return t;
            if (ShortTo3.TryGetValue(t, out var c))
                return c;
        }
        return "und";
    }

    /// <summary>The video a subtitle file belongs to: same name, or the subtitle name plus .lang/.sdh/... tags.</summary>
    public static string? FindMatchingVideo(string subPath)
    {
        var stem = PyPath.Stem(subPath).ToLowerInvariant();
        string? best = null;
        List<string> entries;
        try
        {
            entries = PyPath.List(PyPath.Dir(subPath));
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or ArgumentException)
        {
            return null;
        }
        foreach (var f in entries)
        {
            if (!VideoExts.Contains(PyPath.Suffix(f).ToLowerInvariant()) || PyPath.Name(f).Contains(TmpMark) || !File.Exists(f))
                continue;
            var vs = PyPath.Stem(f).ToLowerInvariant();
            if (stem == vs || stem.StartsWith(vs + ".", StringComparison.Ordinal))
            {
                if (best == null || PyFmt.Len(PyPath.Stem(f)) > PyFmt.Len(PyPath.Stem(best)))
                    best = f;
            }
        }
        return best;
    }

    /// <summary>Subtitle files next to a video that belong to it ('Movie.eng.srt', 'Movie.en.sdh.srt', 'Movie.srt').</summary>
    public static List<string> FindSidecars(string videoPath)
    {
        var outList = new List<string>();
        List<string> entries;
        try
        {
            entries = PyPath.Sorted(PyPath.List(PyPath.Dir(videoPath)));
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or ArgumentException)
        {
            return outList;
        }
        var vstem = PyPath.Stem(videoPath).ToLowerInvariant();
        foreach (var f in entries)
        {
            if (!TextSubExts.Contains(PyPath.Suffix(f).ToLowerInvariant()) || !File.Exists(f))
                continue;
            if (!PyPath.Stem(f).ToLowerInvariant().StartsWith(vstem, StringComparison.Ordinal))
                continue;
            var match = FindMatchingVideo(f);
            if (match != null && PyPath.Key(match) == PyPath.Key(videoPath))
                outList.Add(f);
        }
        return outList;
    }

    private static readonly Dictionary<string, string> CodecOf = new() { ["srt"] = "subrip", ["ass"] = "ass", ["vtt"] = "webvtt" };

    /// <summary>A subtitle file next to a video, presented as one more track of that video.</summary>
    public static (SubTrack Track, SubtitleDoc Doc) SidecarTrack(string path, int k)
    {
        var doc = SubFormats.Load(path);
        var toks = NameTokens(path).Skip(1).Select(t => t.ToLowerInvariant()).ToHashSet();
        var tr = new SubTrack
        {
            Index = SidecarBase + k, SubPos = 1000 + k, Codec = CodecOf[doc.Fmt], Language = GuessLanguage(path),
            Title = PyPath.Name(path), Default = false, Forced = toks.Contains("forced"),
            HearingImpaired = toks.Overlaps(SdhTokens), External = path,
        };
        return (tr, doc);
    }

    /// <summary>'Movie.en.sdh.srt' -> 'Movie.en.srt' (drops SDH/HI/CC tags). Same name if there were none.</summary>
    public static string CleanSubtitleName(string subPath, string fmt)
    {
        var toks = NameTokens(subPath);
        var kept = toks.Take(1).Concat(toks.Skip(1).Where(t => !SdhTokens.Contains(t.ToLowerInvariant())));
        return PyPath.WithName(subPath, string.Join(".", kept) + "." + fmt);
    }

    public static (MediaInfo Info, SubtitleDoc Doc) ProbeSubtitleFile(string path)
    {
        var doc = SubFormats.Load(path);
        var toks = NameTokens(path).Skip(1).Select(t => t.ToLowerInvariant()).ToHashSet();
        var duration = doc.Cues.Count > 0 ? doc.Cues.Max(c => SubFormats.ToSeconds(c.End)) : 0.0;
        var track = new SubTrack
        {
            Index = 0, SubPos = 0, Codec = CodecOf[doc.Fmt], Language = GuessLanguage(path), Title = PyPath.Name(path),
            Default = false, Forced = toks.Contains("forced"), HearingImpaired = toks.Overlaps(SdhTokens),
        };
        var info = new MediaInfo
        {
            Path = path, Kind = "subtitle", FormatName = doc.Fmt, Duration = duration, Size = new FileInfo(path).Length,
            Subs = [track], Companion = FindMatchingVideo(path),
        };
        return (info, doc);
    }

    // ================================================================ where a save goes

    public static string UniquePath(string p)
    {
        if (!Path.Exists(p))
            return p;
        for (var i = 2; i < 1000; i++)
        {
            var cand = PyPath.WithName(p, $"{PyPath.Stem(p)} ({i}){PyPath.Suffix(p)}");
            if (!Path.Exists(cand))
                return cand;
        }
        throw new ToolError($"Couldn't find a free file name next to {p}");
    }

    /// <summary>Just the language ("English"); SubScrub finds its own tracks again by a hidden tag, not the name.</summary>
    public static string DefaultTrackName(SubTrack track) => track.Language != "und" ? track.LangLabel : "Subtitles";

    /// <summary>A subtitle track in another language that the tidy-up removes. Forced tracks, tracks with no
    /// language set, and everything when the language to keep isn't known, are always left in.</summary>
    public static bool IsOtherLanguage(SubTrack t, string keep)
    {
        string mine = LangShort(keep), theirs = LangShort(t.Language);
        return mine.Length > 0 && theirs.Length > 0 && theirs != mine && !t.Forced;
    }

    /// <summary>Where a save will end up (used for the hints and confirmations too).</summary>
    public static string OutputPath(MediaInfo info, SaveOptions opts, string fmt)
    {
        var src = info.Path;
        if (info.Kind == "subtitle")
        {
            if (opts.Mode == "embed")
                return info.Companion ?? src;
            var clean = CleanSubtitleName(src, fmt);
            if (opts.Mode == "replace")
                return !PyPath.Same(clean, src) && !Path.Exists(clean) ? clean : PyPath.WithSuffix(src, "." + fmt);
            if (!PyPath.Same(clean, src) && !Path.Exists(clean))
                return clean;
            return UniquePath(PyPath.WithName(src, $"{PyPath.Stem(clean)}.dialogue.{fmt}"));
        }
        if (opts.Mode == "sidecar")
        {
            var shortLang = LangShort(opts.Language);
            if (SdhTokens.Contains(shortLang))
                shortLang = PyFmt.Head((opts.Language ?? "").ToLowerInvariant(), 3);    // Hindi: "Movie.hin.srt"
            var name = shortLang.Length > 0 ? $"{PyPath.Stem(src)}.{shortLang}.{fmt}" : $"{PyPath.Stem(src)}.dialogue.{fmt}";
            return PyPath.WithName(src, name);
        }
        var ext = info.Kind != "other" ? PyPath.Suffix(src) : ".mkv";
        if (opts.Mode == "replace")
        {
            if (opts.RenameTo.Length > 0)
                return PyPath.WithName(src, PyPath.Stem(opts.RenameTo) + ext);
            return PyPath.WithName(src, PyPath.Stem(src) + ext);
        }
        return UniquePath(PyPath.WithName(src, $"{PyPath.Stem(src)}.dialogue{ext}"));
    }
}
