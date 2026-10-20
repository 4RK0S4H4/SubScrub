using System.Text.Json.Nodes;

namespace SubScrub.Engine;

/// <summary>Settings where the Qt app keeps them, HKEY_CURRENT_USER\Software\SubScrub\SubScrub (QSettings' place),
/// so both apps share one set of settings and one TMDB key. "a/b" keys are subkeys; QSettings writes a value that
/// starts with "@" with a second "@" in front.</summary>
public sealed class RegistryStore : IStore
{
    public const string DefaultKey = @"Software\SubScrub\SubScrub";
    private readonly string _root;

    public RegistryStore(string root = DefaultKey) => _root = root;

    private (string Sub, string Name) Split(string key)
    {
        var i = key.LastIndexOf('/');
        return i < 0 ? (_root, key) : (_root + "\\" + key[..i].Replace('/', '\\'), key[(i + 1)..]);
    }

    public string Get(string key, string def = "")
    {
        if (!OperatingSystem.IsWindows())
            return def;
        var (sub, name) = Split(key);
        try
        {
            using var k = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(sub);
            if (k == null)
                return def;
            var kind = k.GetValueKind(name);
            if (kind is not (Microsoft.Win32.RegistryValueKind.String or Microsoft.Win32.RegistryValueKind.ExpandString))
                return def;
            if (k.GetValue(name, null, Microsoft.Win32.RegistryValueOptions.DoNotExpandEnvironmentNames) is not string value)
                return def;
            return value.StartsWith("@@", StringComparison.Ordinal) ? value[1..] : value;   // QSettings escapes a leading @
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or System.Security.SecurityException)
        {
            return def;
        }
    }

    public void Set(string key, string value)
    {
        if (!OperatingSystem.IsWindows())
            return;
        var (sub, name) = Split(key);
        using var k = Microsoft.Win32.Registry.CurrentUser.CreateSubKey(sub);
        k.SetValue(name, value.StartsWith('@') ? "@" + value : value, Microsoft.Win32.RegistryValueKind.String);
    }
}

public sealed partial class Session
{
    private ITmdb? _tmdb;
    private Thread? _namer;

    /// <summary>Makes the TMDB client for a key (the real one, or a stand-in for tests and the scripted QA run).</summary>
    public Func<string, ITmdb> TmdbFactory { get; set; } = key => new Tmdb(key);

    private ITmdb Api()
    {
        _tmdb ??= TmdbFactory(Cfg.TmdbKey);
        return _tmdb;
    }

    /// <summary>Starts looking up titles in the background (part of Start).</summary>
    private void StartNames()
    {
        TmdbChanged = () => _tmdb = null;
        PumpNames = PumpNamesNow;
        NamesBusy = () => _namer != null && _namer.IsAlive;
    }

    /// <summary>Look up one video at a time, once it has been read (its length helps tell titles apart).</summary>
    private void PumpNamesNow()
    {
        if (!Renaming() || (_namer != null && _namer.IsAlive))
            return;
        var todo = Items.Where(i => i.Kind == "video" && i.Pending && i.Loaded && i.NameState == "").ToList();
        if (todo.Count == 0)
            return;
        if (Cfg.TmdbKey.Length == 0)
        {
            foreach (var i in todo)
                i.NameState = "nokey";
            Changed(false);
            return;
        }
        var it = todo[0];
        it.NameState = "looking";
        var (api, name, dur, uid) = (Api(), it.Name, it.Info?.Duration ?? 0.0, it.Uid);
        _namer = new Thread(() =>
        {
            TitleMatch m;
            try
            {
                m = Naming.MatchTitle(api, name, dur);
            }
            catch (Exception e)
            {
                lock (Lock)
                {
                    _namer = null;
                    Log($"Title lookup failed: {e.Message}");
                    foreach (var x in Items)
                    {
                        if (x.Uid == uid || (x.Kind == "video" && x.NameState == ""))
                        {
                            x.NameState = "error";
                            x.NameNote = e.Message.Length > 0 ? e.Message : e.GetType().Name;
                        }
                    }
                    Changed(true);
                }
                return;
            }
            lock (Lock)
            {
                _namer = null;
                var x = Item(uid);
                if (x != null)
                {
                    x.Match = m;
                    x.NameState = "done";
                    x.NameNote = m.Note;
                }
                Changed(uid == ReviewUid);
                PumpNames();
            }
        }) { IsBackground = true, Name = "names" };
        _namer.Start();
        Changed(false);
    }

    public JsonObject NameSearch(int uid, string text)
    {
        ITmdb api;
        TitleMatch? m;
        string name;
        lock (Lock)
        {
            var it = Item(uid) ?? throw new InvalidOperationException("That file isn't in the list.");
            (api, m, name) = (Api(), it.Match, it.Name);
        }
        var kind = m != null && m.Parsed.IsEpisode ? "episode" : "movie";
        var parsed = m?.Parsed ?? Naming.Parse(name);
        List<Choice> found;
        try
        {
            found = Naming.Search(api, Py.Strip(text), kind, parsed);
        }
        catch (TmdbError e)
        {
            return new JsonObject { ["choices"] = new JsonArray(), ["note"] = e.Message };
        }
        return ChoicesFound(uid, found);
    }
}
