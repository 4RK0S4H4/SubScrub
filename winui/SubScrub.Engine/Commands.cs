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
    public static readonly HashSet<string> Async =
        ["name_search", "locks", "save", "wait_idle", "leftovers", "leftovers_action", "update_check", "update_install", "winget_install", "tools_versions"];

    private Update? _update;

    /// <summary>The version updates compare against: "4.0.0" for "4.0.0-dev.3".</summary>
    private string PlainVersion => _version.Split('-', '+')[0];

    /// <summary>{"version", "can_install", "button"} when a newer release is out (and, for the automatic check,
    /// not one the user said Later to), {"none": true} when this is the newest, {"error"} when GitHub can't say.
    /// The automatic check runs at most once every 20 hours and only when Settings allow it.</summary>
    private JsonObject UpdateCheck(bool manual)
    {
        var cfg = Session.Cfg;
        if (!manual)
        {
            if (!cfg.UpdateCheck || Environment.GetEnvironmentVariable("SUBSCRUB_NO_UPDATE_CHECK") is { Length: > 0 })
                return new JsonObject { ["skipped"] = true };
            var last = double.TryParse(cfg.Store.Get("update_last", "0"), System.Globalization.NumberStyles.Float,
                                       System.Globalization.CultureInfo.InvariantCulture, out var l) ? l : 0.0;
            if (DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0 - last < 20 * 3600)
                return new JsonObject { ["skipped"] = true };
        }
        Update? upd;
        try
        {
            upd = Updates.Check(PlainVersion);
        }
        catch (UpdateError e)
        {
            Session.Log($"Update check failed: {e.Message}");
            return new JsonObject { ["error"] = e.Message };
        }
        cfg.Store.Set("update_last", (DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0).ToString("R", System.Globalization.CultureInfo.InvariantCulture));
        if (upd == null)
            return new JsonObject { ["none"] = true, ["current"] = _version };
        _update = upd;
        var install = upd.CanInstall && Updates.IsInstalled();
        Session.Log($"Update found: {upd.Version} ({(upd.CanInstall ? "installer" : "no installer")})");
        if (!manual && upd.Version == cfg.Store.Get("update_skipped", ""))
            return new JsonObject { ["skipped"] = true };
        return new JsonObject
        {
            ["version"] = upd.Version, ["current"] = _version, ["can_install"] = install,
            ["button"] = install ? "Update" : "Download",
        };
    }

    /// <summary>Runs "winget install" for FFmpeg or MKVToolNix in its own console window and waits for it, then
    /// looks for the tools again.</summary>
    private JsonObject WingetInstall(string id)
    {
        if (id is not ("Gyan.FFmpeg" or "MoritzBunkus.MKVToolNix"))
            throw new InvalidOperationException("Only FFmpeg and MKVToolNix can be installed from here.");
        var winget = Media.FindTool("winget");
        if (winget == null)
            return new JsonObject { ["missing"] = true };
        string[] cmd = ["install", "-e", "--id", id, "--accept-source-agreements", "--accept-package-agreements"];
        Session.Log("$ winget " + string.Join(" ", cmd));
        var info = new System.Diagnostics.ProcessStartInfo(winget) { UseShellExecute = false, CreateNoWindow = false };
        foreach (var x in cmd)
            info.ArgumentList.Add(x);
        using var p = System.Diagnostics.Process.Start(info)!;
        p.WaitForExit();
        Session.Log($"winget finished with code {p.ExitCode}");
        return new JsonObject { ["code"] = p.ExitCode, ["tools"] = Session.RescanTools() };
    }

    private JsonObject UpdateInstall()
    {
        var upd = _update ?? throw new InvalidOperationException("No update to install.");
        if (Session.Busy())
            throw new InvalidOperationException("Update once the files have finished saving.");
        if (!(upd.CanInstall && Updates.IsInstalled()))
        {
            Updates.OpenPage(upd.Page);                         // a loose copy updates by hand
            Session.Log($"Opened {upd.Page}");
            return new JsonObject { ["opened"] = true };
        }
        var path = Updates.Download(upd, f => Session.Emit("update_progress", new JsonObject { ["fraction"] = f }));
        Session.Log($"Running {path}");
        Updates.RunInstaller(path);
        return new JsonObject { ["installing"] = true };
    }

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
            case "cache_clear":
                var freed = ReadCache.Clear();
                s.Log($"Cleared the read cache ({freed / 1048576.0:F1} MB)");
                return new JsonObject { ["freed"] = freed };
            case "tools_versions":
                var found = s.Tools;
                return new JsonObject
                {
                    ["ffmpeg"] = Media.ToolVersion(found.Ffmpeg), ["ffprobe"] = Media.ToolVersion(found.Ffprobe),
                    ["mkvmerge"] = Media.ToolVersion(found.Mkvmerge), ["mkvextract"] = Media.ToolVersion(found.Mkvextract),
                    ["mkvpropedit"] = Media.ToolVersion(found.Mkvpropedit),
                };
            case "winget_install": return WingetInstall(S(a, "id") ?? "");
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
            case "update_check": return UpdateCheck(B(a, "manual"));
            case "update_later":
                if (_update != null)
                    s.Cfg.Store.Set("update_skipped", _update.Version);
                return null;
            case "update_install":
                try
                {
                    return UpdateInstall();
                }
                catch (UpdateError e)
                {
                    throw new InvalidOperationException(e.Message);
                }
            case "shutdown": return new JsonObject();
        }
        throw new InvalidOperationException($"unknown command '{cmd}'");
    }

    public void Close() => Session.Close();
}
