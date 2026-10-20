using System.Text.Json.Nodes;

namespace SubScrub.Engine;

/// <summary>The engine's commands by name, with JSON arguments and replies: the protocol subscrub/helper.py speaks
/// over stdin/stdout, now answered in-process. The window's view objects stay exactly as they were.</summary>
public sealed class Commands
{
    public const int Protocol = 1;
    public Session Session { get; }
    private readonly string _version;

    /// <summary>settings: a JSON file instead of the registry (tests, screenshots). demoTmdb: the stand-in for TMDB.</summary>
    public Commands(string version, string? settings = null, bool demoTmdb = false, string? logFile = null,
                    Action<string, JsonNode?>? emit = null)
    {
        _version = version;
        IStore store = settings != null ? new FileStore(settings) : OperatingSystem.IsWindows() ? new RegistryStore()
            : new FileStore(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".config", "SubScrub", "settings.json"));
        Session = new Session(store) { LogFile = logFile };
        if (emit != null)
            Session.Emit = emit;
        if (demoTmdb)
        {
            Session.TmdbFactory = _ => FakeTmdb.Demo();
            if (Session.Cfg.TmdbKey.Length == 0)
                Session.Cfg.TmdbKey = "stand-in";
        }
        Session.Start(version);
        new Thread(() => Media.RemoveStaleTempDirs()) { IsBackground = true, Name = "tidy temp" }.Start();
    }

    public JsonObject Hello()
    {
        var h = Session.Hello(_version);
        h["protocol"] = Protocol;
        return h;
    }

    /// <summary>Commands that may wait on the network or Windows (the helper answered these from their own thread).</summary>
    public static readonly HashSet<string> Async = ["name_search", "locks", "save", "wait_idle", "leftovers", "leftovers_action"];

    private static string? S(JsonObject a, string k) => a[k] is JsonValue v && v.TryGetValue<string>(out var s) ? s : null;

    private static int I(JsonObject a, string k) => a[k] is JsonValue v && v.TryGetValue<int>(out var i)
        ? i : throw new InvalidOperationException($"missing argument {k}");

    private static int? NI(JsonObject a, string k) => a[k] is JsonValue v && v.TryGetValue<int>(out var i) ? i : null;

    private static bool B(JsonObject a, string k) => Config.Truthy(a[k]);

    private static List<string> L(JsonObject a, string k) =>
        (a[k] as JsonArray ?? []).Select(x => x is JsonValue v && v.TryGetValue<string>(out var s) ? s : "").ToList();

    /// <summary>Runs one command. Throws InvalidOperationException (or another exception) with the message to show.</summary>
    public JsonNode? Run(string cmd, JsonObject? args)
    {
        var a = args ?? [];
        var s = Session;
        switch (cmd)
        {
            case "hello": return Hello();
            case "settings_set": return s.SetSettings(a["values"] as JsonObject ?? []);
            case "theme": s.SetTheme(S(a, "mode") ?? "system"); return null;
            case "tools_rescan": return s.RescanTools();
            case "add": return s.Add(L(a, "paths"));
            case "leftovers": return s.Leftovers(L(a, "paths"));
            case "leftovers_action": return s.LeftoversAction(L(a, "paths"), S(a, "action") ?? "", a["which"] is JsonArray ? L(a, "which") : null);
            case "remove": s.Remove(I(a, "uid")); return null;
            case "clear": s.Clear(); return null;
            case "files": return s.FilesView();
            case "locks": return s.CheckLocks();
            case "review": return s.Review(I(a, "uid"), S(a, "show"), B(a, "first"));
            case "select_track": return s.SelectTrack(I(a, "uid"), I(a, "index"), S(a, "show"));
            case "force_clean": return s.ForceClean(I(a, "uid"), S(a, "show"));
            case "set_rules": return s.SetRules(I(a, "uid"), a["rules"] as JsonObject ?? [], S(a, "lyrics") ?? "", S(a, "show"));
            case "reset_rules": return s.ResetRules(I(a, "uid"), S(a, "show"));
            case "promote_rules": return s.PromoteRules(I(a, "uid"), S(a, "show"));
            case "toggle_line": return s.ToggleLine(I(a, "uid"), I(a, "cue"));
            case "name_choices": return s.NameChoices(I(a, "uid"));
            case "name_search": return s.NameSearch(I(a, "uid"), S(a, "text") ?? "");
            case "name_pick": return s.NamePick(I(a, "uid"), S(a, "pick") ?? "");
            case "save": return s.Save(NI(a, "only"), a["answers"] as JsonObject);
            case "cancel": s.Cancel(); return null;
            case "open_folder": s.OpenFolder(S(a, "path")); return null;
            case "undo_renames_question": return s.UndoRenamesQuestion();
            case "undo_renames": return s.UndoRenames();
            case "log":
                lock (s.LogLines)
                    return new JsonObject { ["text"] = string.Join("\n", s.LogLines) };
            case "wait_idle":
                var timeout = a["timeout"] is JsonValue tv && tv.TryGetValue<double>(out var t) ? t : 600;
                return new JsonObject { ["idle"] = s.WaitIdle(timeout) };
            case "shutdown": return new JsonObject();
        }
        throw new InvalidOperationException($"unknown command '{cmd}'");
    }

    public void Close() => Session.Close();
}
