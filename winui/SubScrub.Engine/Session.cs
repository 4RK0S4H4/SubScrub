using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace SubScrub.Engine;

/// <summary>Where the settings live, as strings (see Store in subscrub/core.py).</summary>
public interface IStore
{
    string Get(string key, string def = "");
    void Set(string key, string value);
}

/// <summary>Settings in a JSON file (tests, screenshots, or not Windows): never the real settings.</summary>
public sealed class FileStore : IStore
{
    private readonly string _path;
    private readonly Dictionary<string, string> _file = [];

    public FileStore(string path)
    {
        _path = path;
        try
        {
            if (File.Exists(path) && JsonNode.Parse(File.ReadAllText(path, Encoding.UTF8)) is JsonObject o)
            {
                foreach (var (k, v) in o)
                    _file[k] = v is JsonValue jv && jv.TryGetValue<string>(out var s) ? s : v?.ToJsonString() ?? "None";
            }
        }
        catch (Exception e) when (e is IOException or JsonException or UnauthorizedAccessException)
        {
            _file.Clear();
        }
    }

    public string Get(string key, string def = "") => _file.TryGetValue(key, out var v) ? v : def;

    public void Set(string key, string value)
    {
        lock (_file)
        {
            _file[key] = value;
            var dir = Path.GetDirectoryName(Path.GetFullPath(_path));
            if (!string.IsNullOrEmpty(dir))
                Directory.CreateDirectory(dir);
            var obj = new JsonObject();
            foreach (var (k, v) in _file)
                obj[k] = v;
            var tmp = _path + ".tmp";
            File.WriteAllText(tmp, obj.ToJsonString(Session.JsonOut), new UTF8Encoding(false));
            File.Move(tmp, _path, true);
        }
    }
}

/// <summary>Every setting the save and the file list depend on, with the Qt app's keys and defaults.</summary>
public sealed class Config
{
    public static readonly string[] VideoModeKeys = ["replace", "new", "sidecar"];
    public static readonly string[] SubModeKeys = ["embed", "replace", "new"];
    public static readonly Dictionary<string, string> SubModes = new()
    {
        ["embed"] = "Embed in Video", ["replace"] = "Replace File", ["new"] = "New File",
    };

    public IStore Store { get; }
    public CleanOptions Defaults { get; set; }
    public string VideoMode { get; set; }
    public string SubMode { get; set; }
    public bool KeepSdh { get; set; }
    public bool MakeDefault { get; set; }
    public bool RecycleSub { get; set; }
    public bool ConfirmReplace { get; set; }
    public string TrackName { get; set; }
    public string PreviewFilter { get; set; }
    public bool RenameOn { get; set; }
    public bool NameYear { get; set; }
    public bool DropLangs { get; set; }
    public string TmdbKey { get; set; }
    public bool UpdateCheck { get; set; }
    public string Theme { get; set; }
    public Dictionary<string, string> Tools { get; set; }

    public Config(IStore store)
    {
        Store = store;
        string S(string k, string d) => store.Get(k, d);
        Dictionary<string, bool>? stored = null;
        try
        {
            var raw = S("clean_options_v2", "");
            if (JsonNode.Parse(raw.Length > 0 ? raw : "null") is JsonObject o)
                stored = o.ToDictionary(kv => kv.Key, kv => Truthy(kv.Value));
        }
        catch (Exception e) when (e is JsonException or InvalidOperationException or FormatException)
        {
            stored = null;
        }
        Defaults = stored is { Count: > 0 } ? CleanOptions.FromDict(stored) : new CleanOptions();
        string vm = S("video_mode", "replace"), sm = S("sub_mode", "embed");
        VideoMode = VideoModeKeys.Contains(vm) ? vm : "replace";
        SubMode = SubModeKeys.Contains(sm) ? sm : "embed";
        KeepSdh = S("keep_sdh", "false") == "true";
        MakeDefault = S("make_default", "true") == "true";
        RecycleSub = S("recycle_sub", "true") == "true";
        ConfirmReplace = S("confirm_replace", "true") == "true";
        TrackName = S("track_name", "");
        PreviewFilter = S("preview_filter", "changes");
        RenameOn = S("rename_on", "true") == "true";
        NameYear = S("name_year", "false") == "true";
        DropLangs = S("drop_langs", "true") == "true";
        TmdbKey = S("tmdb_key", "");
        UpdateCheck = S("update_check", "true") == "true";
        Theme = S("theme", "system");
        Tools = new[] { "ffmpeg", "ffprobe", "mkvmerge" }.ToDictionary(k => k, k => S($"tools/{k}", ""));
    }

    /// <summary>bool(x) of a JSON value.</summary>
    public static bool Truthy(JsonNode? v)
    {
        if (v is not JsonValue jv)
            return v is JsonObject o ? o.Count > 0 : v is JsonArray a && a.Count > 0;
        if (jv.TryGetValue<bool>(out var b))
            return b;
        if (jv.TryGetValue<double>(out var d))
            return d != 0;
        if (jv.TryGetValue<string>(out var s))
            return s.Length > 0;
        return false;
    }

    /// <summary>json.dumps(options.to_dict()), as Python writes it (both apps read the same setting).</summary>
    public static string OptionsJson(CleanOptions o) =>
        "{" + string.Join(", ", CleanOptions.Keys.Select(k => $"\"{k}\": {(o.Get(k) ? "true" : "false")}")) + "}";

    public void Save()
    {
        static string B(bool v) => v ? "true" : "false";
        var put = Store.Set;
        put("clean_options_v2", OptionsJson(Defaults));
        put("video_mode", VideoMode);
        put("sub_mode", SubMode);
        put("keep_sdh", B(KeepSdh));
        put("make_default", B(MakeDefault));
        put("recycle_sub", B(RecycleSub));
        put("confirm_replace", B(ConfirmReplace));
        put("track_name", TrackName);
        put("rename_on", B(RenameOn));
        put("name_year", B(NameYear));
        put("drop_langs", B(DropLangs));
        put("tmdb_key", TmdbKey);
        put("update_check", B(UpdateCheck));
        put("theme", Theme);
        put("preview_filter", PreviewFilter);
    }

    public JsonObject View()
    {
        var d = Defaults;
        var rules = new JsonObject();
        foreach (var k in Session.RuleKeys)
            rules[k] = d.Get(k);
        return new JsonObject
        {
            ["video_mode"] = VideoMode, ["sub_mode"] = SubMode, ["keep_sdh"] = KeepSdh, ["make_default"] = MakeDefault,
            ["recycle_sub"] = RecycleSub, ["confirm_replace"] = ConfirmReplace, ["track_name"] = TrackName,
            ["rename_on"] = RenameOn, ["name_year"] = NameYear, ["drop_langs"] = DropLangs, ["tmdb_key"] = TmdbKey,
            ["update_check"] = UpdateCheck, ["theme"] = Theme, ["rules"] = rules, ["lyrics"] = Session.LyricMode(d),
        };
    }

    public void Apply(JsonObject v)
    {
        string Str(string key) => v[key] is JsonValue jv && jv.TryGetValue<string>(out var s) ? s : (Truthy(v[key]) ? v[key]!.ToJsonString() : "");
        if (v.ContainsKey("video_mode")) VideoMode = Py.Strip(Str("video_mode"));
        if (v.ContainsKey("sub_mode")) SubMode = Py.Strip(Str("sub_mode"));
        if (v.ContainsKey("track_name")) TrackName = Py.Strip(Str("track_name"));
        if (v.ContainsKey("tmdb_key")) TmdbKey = Py.Strip(Str("tmdb_key"));
        if (v.ContainsKey("theme")) Theme = Py.Strip(Str("theme"));
        if (v.ContainsKey("keep_sdh")) KeepSdh = Truthy(v["keep_sdh"]);
        if (v.ContainsKey("make_default")) MakeDefault = Truthy(v["make_default"]);
        if (v.ContainsKey("recycle_sub")) RecycleSub = Truthy(v["recycle_sub"]);
        if (v.ContainsKey("confirm_replace")) ConfirmReplace = Truthy(v["confirm_replace"]);
        if (v.ContainsKey("rename_on")) RenameOn = Truthy(v["rename_on"]);
        if (v.ContainsKey("name_year")) NameYear = Truthy(v["name_year"]);
        if (v.ContainsKey("drop_langs")) DropLangs = Truthy(v["drop_langs"]);
        if (v.ContainsKey("update_check")) UpdateCheck = Truthy(v["update_check"]);
        if (!VideoModeKeys.Contains(VideoMode))
            VideoMode = "replace";
        if (!SubModeKeys.Contains(SubMode))
            SubMode = "embed";
        if (v.ContainsKey("rules") || v.ContainsKey("lyrics"))
        {
            var changes = (v["rules"] as JsonObject ?? []).Where(kv => Session.RuleKeys.Contains(kv.Key))
                .Select(kv => KeyValuePair.Create(kv.Key, Truthy(kv.Value)));
            var o = Defaults.With(changes);
            var lyr = v["lyrics"] is JsonValue lv && lv.TryGetValue<string>(out var ls) && ls.Length > 0 ? ls : Session.LyricMode(o);
            Defaults = Session.WithLyricMode(o, lyr);
        }
    }
}

/// <summary>SubScrub without a window: the file list, Review and the questions at save time, as JSON views the
/// window draws. A port of subscrub/core.py (Session), kept identical to it: tools/engine_parity.py compares every
/// view. Stage 2: the views; reading, title lookups and the save still come from the Python helper.</summary>
public sealed partial class Session
{
    public const string CancelledMark = "__cancelled__";

    public static readonly JsonSerializerOptions JsonOut = new()
    {
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        TypeInfoResolver = new System.Text.Json.Serialization.Metadata.DefaultJsonTypeInfoResolver(),
    };

    // Edit types shown in Review and as defaults in Settings: option key, title, example, rule ids counted
    public static readonly (string Key, string Title, string Example, string[] Rules)[] Rules =
    [
        ("brackets", "Sounds in [Brackets]", "[door slams], [Grace] Hi.", ["brackets"]),
        ("parentheses", "Sounds in (Parentheses)", "(sighs), (ON PHONE)", ["parentheses"]),
        ("speaker_labels", "Speaker Names", "JOHN: Get down!  →  Get down!", ["labels"]),
        ("music_cues", "Music Cues", "♪ tense music playing ♪, ♪♪", ["music"]),
        ("dialogue_dashes", "Dialogue Dashes", "- Hi. / - Hey.  →  Hi. / Hey.", ["dashes"]),
        ("onscreen_text", "On-Screen Text Notes", "[sign reads: EXIT]  →  EXIT", ["onscreen"]),
        ("sound_words", "Sound Words", "whoosh, pew pew pew, ka-boom on their own line", ["sound_words"]),
        ("quote_italics", "Quoted Voices → Italics", "'Report to Oa.'  →  Report to Oa. (in italics)", ["quotes"]),
        ("ocr_fixes", "OCR Repairs", "Fixes ♪ and “I” misread in disc-converted tracks", ["ocr"]),
        ("mixed_case_labels", "Every “Name:” Label", "Also in files that don't use them throughout (risky)", []),
    ];
    public static readonly string[] RuleKeys = Rules.Select(r => r.Key).ToArray();

    public static string LyricMode(CleanOptions o) => o.Lyrics ? "remove" : (o.LyricSymbols ? "italic" : "keep");

    public static CleanOptions WithLyricMode(CleanOptions o, string mode) =>
        o with { Lyrics = mode == "remove", LyricSymbols = mode == "italic" };

    public static string FmtSize(double n)
    {
        foreach (var unit in new[] { "B", "KB", "MB", "GB", "TB" })
        {
            if (n < 1024 || unit == "TB")
                return unit is "B" or "KB" ? $"{PyFmt.Fixed(n, 0)} {unit}" : $"{PyFmt.Fixed(n, 1)} {unit}";
            n /= 1024;
        }
        return "";
    }

    public static string FmtDuration(double sec)
    {
        if (sec == 0)
            return "";
        var t = (long)Math.Round(sec, MidpointRounding.ToEven);
        long h = t / 3600, m = t % 3600 / 60, s = t % 60;
        return h != 0 ? $"{h}:{m:00}:{s:00}" : $"{m}:{s:00}";
    }

    public static string FmtClock(double sec) => FmtDuration(sec);

    public static string Cap(string text) => PyFmt.Cap(text);

    private static readonly ConditionalWeakTable<CleanResult, Tuple<string, int>> FinalCache = new();

    /// <summary>How many lines the save will change (cached until the user's decisions change).</summary>
    public static int FinalChanged(CleanResult res)
    {
        var key = string.Join(";", res.Overrides.OrderBy(kv => kv.Key).Select(kv => $"{kv.Key}={kv.Value}"));
        if (FinalCache.TryGetValue(res, out var cached) && cached.Item1 == key)
            return cached.Item2;
        var n = 0;
        for (var i = 0; i < res.Doc.Cues.Count; i++)
        {
            if (res.Final(i) != res.Doc.Cues[i].Text)
                n++;
        }
        FinalCache.AddOrUpdate(res, Tuple.Create(key, n));
        return n;
    }

    // ================================================================ state

    public Config Cfg { get; }
    public readonly object Lock = new();
    public List<Item> Items { get; } = [];
    public TrackPref? Pref { get; set; }
    public int? ReviewUid { get; set; }
    public string AddNote { get; set; } = "";
    public (string Text, string Tone) StatusMsg { get; set; } = ("", "");
    public string? LastOutput { get; set; }
    public string LocksBanner { get; set; } = "";
    public Tools Tools { get; set; } = new();
    public List<string> LogLines { get; } = [];
    public string? LogFile { get; set; }
    private readonly Dictionary<string, Choice> _choices = [];
    private bool _saving;
    public (string Text, double Frac) SaveStatus { get; set; } = ("", -1.0);

    /// <summary>Which other programs have these files open ({path: ["qBittorrent"]}); empty when nothing does.</summary>
    public Func<List<string>, Dictionary<string, List<string>>> WhoHasOpen { get; set; } = _ => [];

    /// <summary>Sends an event to the window ("files", "review_changed", "save_done").</summary>
    public Action<string, JsonNode?> Emit { get; set; } = (_, _) => { };

    public Session(IStore store)
    {
        Cfg = new Config(store);
    }

    public void Log(string msg)
    {
        var line = $"{DateTime.Now:HH:mm:ss}  {msg}";
        lock (LogLines)
        {
            LogLines.Add(line);
            if (LogLines.Count > 4000)
                LogLines.RemoveRange(0, LogLines.Count - 4000);
        }
        if (LogFile != null)
        {
            try
            {
                File.AppendAllText(LogFile, line + "\n", new UTF8Encoding(false));
            }
            catch (IOException)
            {
            }
        }
    }

    public Item? Item(int? uid) => uid == null ? null : Items.FirstOrDefault(i => i.Uid == uid);

    public bool Busy() => _saving;

    public void SetSaving(bool on) => _saving = on;

    public CleanOptions OptsFor(Item it) => it.Opts ?? Cfg.Defaults;

    /// <summary>Something changed: the window gets a fresh files view (and Review, when asked).</summary>
    public Action<bool> Changed { get; set; } = _ => { };

    /// <summary>Starts the next title lookup (set by the part of the engine that does them).</summary>
    public Action PumpNames { get; set; } = () => { };

    /// <summary>Starts reading the next queued file.</summary>
    public Action PumpLoader { get; set; } = () => { };

    public JsonObject ToolsView()
    {
        var t = Tools;
        return new JsonObject
        {
            ["ffmpeg"] = t.Ffmpeg ?? "", ["ffprobe"] = t.Ffprobe ?? "", ["mkvmerge"] = t.Mkvmerge ?? "", ["ready"] = t.Ready,
        };
    }

    public JsonObject Hello(string version) => new()
    {
        ["version"] = version, ["tools"] = ToolsView(), ["settings"] = Cfg.View(),
        ["rules"] = new JsonArray(Rules.Select(r => (JsonNode)new JsonObject
        {
            ["key"] = r.Key, ["title"] = r.Title, ["example"] = r.Example,
        }).ToArray()),
    };

    // ================================================================ settings

    public JsonObject SetSettings(JsonObject values)
    {
        lock (Lock)
        {
            if (Busy())
                throw new InvalidOperationException("Settings can't change while saving.");
            var (oldDefaults, oldKey, oldRename) = (Cfg.Defaults, Cfg.TmdbKey, Cfg.RenameOn);
            Cfg.Apply(values);
            Cfg.Save();
            if (Cfg.TmdbKey != oldKey)
            {
                TmdbChanged?.Invoke();
                foreach (var it in Items)
                {
                    if (it.Kind == "video" && it.NameState is "nokey" or "error")
                        it.NameState = "";
                }
            }
            if (Cfg.Defaults != oldDefaults)
            {
                foreach (var it in Items)
                {
                    if (it.Opts == null && it.Loaded)
                        ComputeResult(it);
                }
            }
            if (Cfg.RenameOn != oldRename || Cfg.TmdbKey != oldKey)
                PumpNames();
            Changed(true);
            return Cfg.View();
        }
    }

    /// <summary>The TMDB key changed: drop the client that used the old one.</summary>
    public Action? TmdbChanged { get; set; }

    public void SetTheme(string mode)
    {
        lock (Lock)
        {
            Cfg.Theme = mode is "system" or "light" or "dark" ? mode : "system";
            Cfg.Store.Set("theme", Cfg.Theme);
        }
    }

    // ================================================================ renaming (TMDB)

    public bool Renaming() => Cfg.RenameOn && Cfg.VideoMode == "replace";

    public string? NewName(Item it) => Renaming() ? Batch.NewName(it, Cfg.NameYear) : null;

    public List<SubTrack> LangDrops(Item it) =>
        Cfg.DropLangs && Cfg.VideoMode == "replace" ? Batch.OtherLanguages(it) : [];

    public bool HasTidy(Item it) => it.Kind == "video" && it.State is "ready" or "skipped" or "failed"
                                    && (NewName(it) != null || LangDrops(it).Count > 0);

    public string TidyNote(Item it)
    {
        if (!HasTidy(it))
            return "";
        var n = LangDrops(it).Count;
        return n > 0 ? $"{PyFmt.Plural(n, "other-language subtitle track")} will be removed" : "";
    }

    /// <summary>(text, tone) for the line under a file's name: the new name, or where the title lookup stands.</summary>
    public (string Text, string Tone) NameLine(Item it)
    {
        if (it.Kind != "video" || !Renaming() || it.State is "done" or "saving" or "queued" or "probing" or "reading")
            return ("", "");
        var st = it.NameState;
        if (st is "" or "looking")
            return ("Looking up the title…", "faint");
        if (st == "nokey")
            return ("", "");
        if (st == "error")
            return (it.NameNote.Length > 0 ? it.NameNote : "Couldn't look up the title", "alert");
        if (it.NamePick is "keep")
            return ("Keeping this name", "faint");
        var nw = NewName(it);
        if (!string.IsNullOrEmpty(nw))
            return (nw, "");
        if (Batch.NameChoice(it) != null)
            return ("", "");
        if (Batch.NameUnsure(it))
            return ("Not sure which title this is, choose one in Review", "alert");
        return ("No title match found, keeping this name", "faint");
    }

    private JsonObject ChoiceView(Item it, Choice c)
    {
        var key = $"{c.Kind}:{c.TmdbId}:{Naming.Num(c.Season)}:{string.Join(",", c.Episodes)}";
        _choices[key] = c;
        return new JsonObject
        {
            ["id"] = key, ["label"] = c.Label(), ["new_name"] = Naming.Render(c, PyPath.Suffix(it.Path), Cfg.NameYear),
        };
    }

    public JsonObject NameChoices(int uid)
    {
        lock (Lock)
        {
            var it = Item(uid);
            if (it == null || it.Kind != "video")
                throw new InvalidOperationException("That file isn't in the list.");
            var m = it.Match;
            return new JsonObject
            {
                ["name"] = it.Name, ["query"] = m != null ? m.Parsed.Title : "",
                ["choices"] = new JsonArray((m?.Choices ?? []).Take(6).Select(c => (JsonNode)ChoiceView(it, c)).ToArray()),
                ["can_search"] = Cfg.TmdbKey.Length > 0,
            };
        }
    }

    /// <summary>Choices found by a title search, as views (the search itself runs elsewhere).</summary>
    public JsonObject ChoicesFound(int uid, List<Choice> found)
    {
        lock (Lock)
        {
            var it = Item(uid) ?? throw new InvalidOperationException("That file isn't in the list.");
            var views = found.Take(6).Select(c => (JsonNode)ChoiceView(it, c)).ToArray();
            return new JsonObject
            {
                ["choices"] = new JsonArray(views), ["note"] = views.Length > 0 ? "" : "No matches. Try a different title below.",
            };
        }
    }

    public JsonObject NamePick(int uid, string pick)
    {
        lock (Lock)
        {
            var it = Item(uid) ?? throw new InvalidOperationException("That file isn't in the list.");
            if (pick == "keep")
                it.NamePick = "keep";
            else if (_choices.TryGetValue(pick, out var c))
                it.NamePick = c;
            else
                throw new InvalidOperationException("That title isn't on offer any more.");
            Changed(true);
            return RowView(it);
        }
    }

    // ================================================================ results

    public void ComputeResult(Item it) => it.Result = it.Sel != null ? Batch.CleanItem(it, Cfg.Defaults) : null;

    // ================================================================ verdicts

    public static string TrackLabel(Item it, SubTrack tr)
    {
        if (it.Kind == "subtitle")
            return tr.LangLabel;
        if (tr.External.Length > 0)
        {
            var name = PyPath.Name(tr.External);
            var stem = PyPath.Stem(it.Path);
            return name.ToLowerInvariant().StartsWith(stem.ToLowerInvariant(), StringComparison.Ordinal)
                ? $"{tr.LangLabel} from the {PySlice(name, PyFmt.Len(stem))} file"
                : $"{tr.LangLabel} from “{name}”";
        }
        return string.IsNullOrEmpty(tr.Title) ? tr.LangLabel : tr.Title;
    }

    /// <summary>s[n:] counting code points.</summary>
    private static string PySlice(string s, int n)
    {
        var cps = PyFmt.CodePoints(s);
        return n >= cps.Length ? "" : PyFmt.FromCodePoints(cps, n, cps.Length);
    }

    /// <summary>(detail line, status text, status kind, show Review button) for a file.</summary>
    public (string Detail, string Chip, string Kind, bool Review) Verdict(Item it)
    {
        var st = it.State;
        if (st == "queued")
            return ("Waiting", "", "", false);
        if (st == "probing")
            return ("Opening", "", "", false);
        if (st == "reading")
        {
            var pct = it.Progress >= 0 ? $" {(int)(it.Progress * 100)}%" : "";
            return ($"Reading subtitles{pct}", "", "", false);
        }
        if (st == "saving")
            return (it.Message.Length > 0 ? it.Message : "Saving", "Saving", "busy", false);
        if (st == "done")
        {
            var outName = !string.IsNullOrEmpty(it.Output) ? PyPath.Name(it.Output) : "";
            var same = !string.IsNullOrEmpty(it.Output) && PyPath.NormCase(it.Output) == PyPath.NormCase(it.Path);
            if (it.DoneText.Length > 0)
                return (it.DoneText, "Saved", "done", false);
            var what = same && it.Kind == "video" ? "Cleaned track saved into the video"
                : (same || outName.Length == 0 ? "Cleaned and saved" : $"Saved as {outName}");
            return (what, "Saved", "done", false);
        }
        if (st == "failed")
            return (it.Message.Length > 0 ? Cap(it.Message) : "Something went wrong", "Failed", "failed", it.Info != null);
        var tr = it.Track();
        var info = it.Info;
        if (info == null)
            return ("Couldn't read this file", "Failed", "failed", false);
        var hasTidy = HasTidy(it);
        var tidy = TidyNote(it);
        if (tr == null)
        {
            if (info.Subs.Count > 0 && !it.Tracks().Any(t => t.TextBased))
                return ("Only picture-based subtitles (PGS or VobSub), which would need OCR", "Can't Edit", "dim", true);
            if (hasTidy)
                return (tidy.Length > 0 ? Cap(tidy) : "No subtitles; only the name changes", "Ready", "ready", false);
            return ("No subtitles in this file or next to it", "Nothing to Do", "dim", false);
        }
        var label = TrackLabel(it, tr);
        if (it.Sel is { } sel && it.DocErrors.ContainsKey(sel))
            return ($"{label}: couldn't read this track", "Failed", "failed", true);
        if (st == "skipped")
            return ($"{label}: {it.Message}", "Skipped", "dim", true);
        var res = it.Result;
        if (res == null)
            return (label, "", "", true);
        if (!it.Force && !Batch.AutoCleaned(it, tr))
        {
            if (hasTidy)
                return ($"{label}: regular subtitles" + (tidy.Length > 0 ? $", {tidy}" : ""), "Ready", "ready", true);
            return ($"{label}: regular subtitles, nothing to strip", "No SDH", "dim", true);
        }
        var n = FinalChanged(res);
        if (n == 0 && tr.External.Length == 0 && !Batch.PlainToDefault(it, tr))
        {
            if (hasTidy)
                return ($"{label}: nothing to remove" + (tidy.Length > 0 ? $", {tidy}" : ""), "Ready", "ready", true);
            return ($"{label}: nothing to remove", "Already Clean", "dim", true);
        }
        if (it.Locked.Count > 0)
        {
            var who = it.Locked.Values.SelectMany(x => x).Distinct().OrderBy(x => x, PyFmt.Order);
            return ($"{label}: open in {string.Join(" and ", who)}, pause it there before saving", "In Use", "check", true);
        }
        var bits = new List<string> { label };
        if (tr.External.Length > 0 && Cfg.VideoMode != "sidecar")
            bits.Add("goes into the video" + (Cfg.RecycleSub && Cfg.VideoMode == "replace" && !Cfg.KeepSdh
                ? " and the .srt is removed" : ""));
        if (it.Opts != null)
            bits.Add("custom edits");
        if (tidy.Length > 0)
            bits.Add(tidy);
        if (res.Notes.OcrSuspect)
            bits.Add("converted from a disc image");
        var unresolved = res.Unresolved().Count;
        var detail = string.Join(", ", bits);
        if (unresolved == 0)
            return (detail, "Ready", "ready", true);
        if (res.Confidence == "low")
            return (detail, "Look Closer", "low", true);
        return (detail, $"{unresolved} to Check", "check", true);
    }

    /// <summary>How many lines change (the number column in the file list); 0 shows nothing.</summary>
    public int RowCount(Item it)
    {
        if (it.State == "done")
            return it.Cleaned;
        var tr = it.Track();
        if (it.Result == null || it.State is not ("ready" or "failed" or "skipped") || tr == null)
            return 0;
        if (!it.Force && !Batch.AutoCleaned(it, tr))
            return 0;
        return FinalChanged(it.Result);
    }

    public JsonObject RowView(Item it)
    {
        var (detail, chip, kind, review) = Verdict(it);
        double? prog = null;
        if (it.State == "reading")
            prog = it.Progress;
        else if (it.State == "probing")
            prog = -1.0;
        else if (it.State == "saving")
            prog = it.Progress >= 0 ? it.Progress : -1.0;
        var (line, tone) = NameLine(it);
        var tip = it.Path;
        if (it.State is "done" or "failed" or "skipped" && it.Message.Length > 0)
            tip += "\n\n" + it.Message;
        if (!string.IsNullOrEmpty(it.Output) && it.Output != it.Path)
            tip += $"\nSaved as {it.Output}";
        var n = RowCount(it);
        return new JsonObject
        {
            ["uid"] = it.Uid, ["name"] = it.Name, ["path"] = it.Path, ["kind"] = it.Kind, ["state"] = it.State,
            ["detail"] = detail, ["badge"] = chip, ["badge_kind"] = kind, ["progress"] = prog,
            ["review"] = review && !Busy(), ["count"] = n,
            ["count_label"] = n != 0 ? (it.State == "done" ? "cleaned" : "to clean") : "",
            ["rename"] = line, ["rename_tone"] = tone, ["tip"] = tip, ["removable"] = !Busy(),
        };
    }

    // ================================================================ the Files view

    public (string Text, string How) SummaryTexts()
    {
        var counts = new Dictionary<string, int>();
        foreach (var it in Items)
        {
            var (_, _, kind, _) = Verdict(it);
            string? key = kind switch
            {
                "ready" => "ready", "check" => "to check", "low" => "to check", "done" => "saved", "failed" => "failed",
                "busy" => "saving", _ => null,
            };
            if (it.Locked.Count > 0 && it.State == "ready")
                key = "in use";
            if (it.State is "queued" or "probing" or "reading")
                key = "reading";
            else if (kind == "dim")
                key = "with nothing to do";
            if (key != null)
                counts[key] = counts.GetValueOrDefault(key) + 1;
        }
        string[] order = ["ready", "to check", "in use", "reading", "saving", "with nothing to do", "saved", "failed"];
        var text = string.Join(", ", order.Where(counts.ContainsKey).Select(k => $"{counts[k]} {k}"));
        if (Items.Any(it => it.Kind == "video" && it.NameState == "nokey" && it.Pending))
            text += (text.Length > 0 ? ". " : "") + "Add a TMDB key in Settings to rename videos.";
        var c = Cfg;
        var pending = Items.Where(i => i.Pending && i.Loaded && i.Track() != null).ToList();
        var plain = pending.Count(i => Batch.PlainPick(i, i.Track()!));
        string sdhNote;
        if (pending.Count == 0)
            sdhNote = "";
        else if (c.KeepSdh || (plain == pending.Count && plain != 0))
            sdhNote = " and the SDH track is kept";
        else if (plain != 0)
            sdhNote = ". Files with a regular English track clean that one and keep the SDH track; otherwise the SDH track is dropped";
        else
            sdhNote = " and the SDH track is dropped";
        var how = c.VideoMode switch
        {
            "replace" => "Saving replaces each original, which goes to the Recycle Bin once the new file checks out. "
                         + $"The cleaned track {(c.MakeDefault ? "becomes the default" : "is added")}{sdhNote}.",
            "new" => "Saving makes a new “.dialogue” copy next to each video.",
            _ => "Saving writes a cleaned .srt next to each video.",
        };
        if (c.RecycleSub && c.VideoMode == "replace" && Items.Any(i => i.Sidecars.Count > 0))
            how += " Matching .srt files go into their video and are then removed.";
        if (Items.Any(i => i.Kind == "subtitle"))
            how += $" Subtitle files on their own: {Config.SubModes[c.SubMode].ToLowerInvariant()}.";
        return (Py.Strip(text), how);
    }

    public JsonObject StatusView()
    {
        if (Busy())
        {
            var (text, frac) = SaveStatus;
            return new JsonObject { ["text"] = text, ["tone"] = "", ["progress"] = frac };
        }
        var loading = Items.Where(i => i.State is "queued" or "probing" or "reading").ToList();
        if (loading.Count > 0 && Tools.Ready)
        {
            var done = Items.Count(i => i.Info != null);
            var cur = loading.FirstOrDefault(i => i.State == "reading" && i.Progress >= 0);
            return new JsonObject
            {
                ["text"] = $"Reading files… {done} of {Items.Count}", ["tone"] = "", ["progress"] = cur?.Progress ?? -1.0,
            };
        }
        if (loading.Count > 0)
        {
            return new JsonObject
            {
                ["text"] = "FFmpeg is needed to read the videos: click “FFmpeg Needed” at the top.", ["tone"] = "bad",
                ["progress"] = null,
            };
        }
        return new JsonObject { ["text"] = StatusMsg.Text, ["tone"] = StatusMsg.Tone, ["progress"] = null };
    }

    public JsonObject ButtonsView()
    {
        var busy = Busy();
        var pending = Items.Where(i => i.Pending).ToList();
        var n = pending.Count;
        string text;
        if (Items.Count <= 1)
        {
            var it = Items.FirstOrDefault();
            text = it != null && it.State == "done" ? "Saved" : "Clean and Save";
        }
        else
            text = n > 1 ? $"Clean and Save {n} Files" : "Clean and Save";
        var needsFfmpeg = pending.Any(i => i.Kind == "video" || Cfg.SubMode == "embed");
        return new JsonObject
        {
            ["primary"] = text, ["primary_visible"] = n > 0 && !busy,
            ["primary_enabled"] = n > 0 && !busy && (Tools.Ready || !needsFfmpeg),
            ["stop_visible"] = busy, ["open_visible"] = !string.IsNullOrEmpty(LastOutput) && !busy,
            ["clear_visible"] = Items.Count > 1 && !busy,
        };
    }

    public JsonObject FilesView()
    {
        lock (Lock)
        {
            var (summary, how) = Items.Count > 0 ? SummaryTexts() : ("", "");
            return new JsonObject
            {
                ["rows"] = new JsonArray(Items.Select(i => (JsonNode)RowView(i)).ToArray()), ["summary"] = summary,
                ["note"] = Cap(AddNote), ["how"] = how, ["locks"] = LocksBanner, ["status"] = StatusView(),
                ["buttons"] = ButtonsView(), ["busy"] = Busy(), ["count"] = Items.Count, ["tools"] = ToolsView(),
                ["review_uid"] = ReviewUid,
            };
        }
    }

    // ================================================================ files other programs have open

    public List<string> FilesTouched(Item it)
    {
        var c = Cfg;
        if (it.Kind == "video")
        {
            var sidecars = it.Sidecars.Count > 0 ? it.Sidecars.Select(t => t.External).ToList() : Media.FindSidecars(it.Path);
            if (c.VideoMode == "replace")
                return [it.Path, .. c.RecycleSub ? sidecars : []];
            if (c.VideoMode == "sidecar")
                return c.RecycleSub ? sidecars : [];
            return [];
        }
        var comp = it.Info != null ? it.Info.Companion : Media.FindMatchingVideo(it.Path);
        if (c.SubMode == "embed" && !string.IsNullOrEmpty(comp))
            return [comp, .. c.RecycleSub ? new[] { it.Path } : []];
        return c.SubMode == "replace" ? [it.Path] : [];
    }

    public void ApplyLocks(Dictionary<string, List<string>> found)
    {
        foreach (var it in Items)
        {
            var mine = new Dictionary<string, List<string>>();
            if (it.Pending)
            {
                foreach (var p in FilesTouched(it))
                {
                    if (found.TryGetValue(p, out var names))
                        mine[p] = names;
                }
            }
            it.Locked = mine;
        }
        var locked = Items.Where(i => i.Locked.Count > 0).ToList();
        if (locked.Count > 0)
        {
            var who = locked.SelectMany(i => i.Locked.Values.SelectMany(x => x)).Distinct().OrderBy(x => x, PyFmt.Order);
            LocksBanner = $"{PyFmt.Plural(locked.Count, "file")} {(locked.Count == 1 ? "is" : "are")} open in {string.Join(" and ", who)}. "
                          + "If you're seeding, pause those torrents before saving: SubScrub can't rewrite a file another "
                          + "program has open. This updates when you come back to this window.";
        }
        else
            LocksBanner = "";
        Changed(false);
    }

    /// <summary>Which pending files other programs have open (the window asks when it is activated).</summary>
    public JsonObject CheckLocks()
    {
        List<string> paths;
        lock (Lock)
        {
            if (Busy())
                return new JsonObject { ["banner"] = LocksBanner };
            paths = Items.Where(i => i.Pending).SelectMany(FilesTouched).ToList();
        }
        var found = paths.Count > 0 ? WhoHasOpen(paths) : [];
        lock (Lock)
        {
            ApplyLocks(found);
            return new JsonObject { ["banner"] = LocksBanner };
        }
    }
}
