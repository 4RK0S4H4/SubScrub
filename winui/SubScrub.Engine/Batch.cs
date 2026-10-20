using System.Text.RegularExpressions;

namespace SubScrub.Engine;

/// <summary>One file in the list.</summary>
public sealed class Item
{
    private static int _ids;

    public string Path { get; set; }
    public string Kind { get; set; }                     // video | subtitle
    public int Uid { get; set; } = Interlocked.Increment(ref _ids);
    public string State { get; set; } = "queued";        // queued probing reading ready saving done skipped failed
    public MediaInfo? Info { get; set; }
    private Dictionary<int, SubtitleDoc>? _docs = [];
    private Dictionary<int, byte[]>? _packed;                // the docs while released (see Release)
    private CleanResult? _result;
    private Func<Item, CleanResult?>? _rebuild;

    /// <summary>The subtitle tracks read, by track index. A released item unpacks them on first use.</summary>
    public Dictionary<int, SubtitleDoc> Docs
    {
        get
        {
            if (_docs == null)
            {
                _docs = _packed!.ToDictionary(kv => kv.Key, kv => SubFormats.Unpack(kv.Value));
                _packed = null;
            }
            return _docs;
        }
        set
        {
            _docs = value;
            _packed = null;
        }
    }
    public Dictionary<int, string> DocErrors { get; set; } = [];
    public Dictionary<int, int> SdhCounts { get; set; } = [];   // track index -> lines the default rules would clean
    public int? Sel { get; set; }
    public bool Manual { get; set; }
    public CleanOptions? Opts { get; set; }              // per-file edits (null = the global defaults)
    public Dictionary<int, string> Overrides { get; set; } = [];   // cue index -> "orig" | "clean" chosen in Review
    public bool Reviewed { get; set; }
    public bool Force { get; set; }                      // clean even though the track isn't SDH (the user asked)
    public string? Marked { get; set; }                  // cleaned by this SubScrub version earlier: shown as Done, not read
    public bool ReadAnyway { get; set; }                 // the user opened a marked file in Review: read and clean it again
    public List<SubTrack> Sidecars { get; set; } = [];   // subtitle files sitting next to the video
    public HashSet<string> Dropped { get; set; } = [];   // subtitle files the user dropped along with the video
    public Dictionary<string, List<string>> Locked { get; set; } = [];   // file this save would change -> programs
    public string? Tmpdir { get; set; }
    public double Progress { get; set; } = -1.0;
    public string Message { get; set; } = "";
    public string? Output { get; set; }
    public int Cleaned { get; set; }
    /// <summary>The cleaning of the picked track. A released item works it out again on first use.</summary>
    public CleanResult? Result
    {
        get
        {
            if (_rebuild is { } rebuild)
            {
                _rebuild = null;
                _result = rebuild(this);
            }
            return _result;
        }
        set
        {
            _result = value;
            _rebuild = null;
        }
    }

    /// <summary>True while the docs and the cleaning are packed away (a saved file nobody is looking at).</summary>
    public bool Released => _packed != null || _rebuild != null;

    /// <summary>Frees the parsed tracks and the cleaning once a file is saved: the tracks are kept packed (a small
    /// compressed copy, exact to the byte) and both come back as they were the next time anything reads them.
    /// rebuild works the cleaning out again (Session.ComputeResult).</summary>
    public void Release(Func<Item, CleanResult?> rebuild)
    {
        if (_docs == null || _rebuild != null)
            return;
        _packed = _docs.ToDictionary(kv => kv.Key, kv => SubFormats.Pack(kv.Value));
        _docs = null;
        _result = null;
        _rebuild = rebuild;
    }
    public TitleMatch? Match { get; set; }                    // what TMDB says this video is
    public object? NamePick { get; set; }                // a Choice the user picked, "keep" (don't rename) or null
    public string NameState { get; set; } = "";          // "" | looking | done | error | nokey
    public string NameNote { get; set; } = "";
    public string DoneText { get; set; } = "";           // what the row says once saved

    public Item(string path, string kind)
    {
        Path = path;
        Kind = kind;
    }

    /// <summary>For a copy that keeps its number (the save works on a copy of the item).</summary>
    public static void SeenUid(int uid)
    {
        int cur;
        while ((cur = _ids) < uid && Interlocked.CompareExchange(ref _ids, uid, cur) != cur)
        {
        }
    }

    public string Name => PyPath.Name(Path);

    public bool Loaded => Info != null && State is not ("queued" or "probing" or "reading");

    public bool Pending => State is "queued" or "probing" or "reading" or "ready" or "failed";

    /// <summary>Tracks inside the video plus subtitle files next to it.</summary>
    public List<SubTrack> Tracks() => (Info?.Subs ?? []).Concat(Sidecars).ToList();

    public SubTrack? Track()
    {
        if (Info == null || Sel == null)
            return null;
        return Tracks().FirstOrDefault(t => t.Index == Sel);
    }
}

public sealed class Plan
{
    public List<string> Videos { get; set; } = [];        // video paths to add
    public List<string> Subtitles { get; set; } = [];     // subtitle files to clean on their own
    public Dictionary<string, List<string>> DroppedWith { get; set; } = [];   // video -> subtitle files dropped with it
    public List<string> Orphans { get; set; } = [];       // subtitle files in a folder with no matching video
}

public sealed class TrackPref
{
    public string Language { get; set; } = "";
    public string Title { get; set; } = "";
    public bool Sdh { get; set; }
}

/// <summary>Batch bookkeeping that doesn't depend on the window: queue items and picking the right track. A port of
/// the retired subscrub/batch.py, kept identical to it (the golden check, tests/golden).</summary>
public static class Batch
{
    // How "SDH-ish" a track is, independent of the user's toggles: only real SDH annotations count
    // (not lyrics or dialogue dashes, which regular subtitles have too).
    public static readonly CleanOptions ReferenceOpts = new()
    {
        Lyrics = false, LyricSymbols = false, DialogueDashes = false, OnscreenText = false, OcrFixes = false,
    };
    private static readonly HashSet<string> SdhRules = ["brackets", "parentheses", "labels", "music", "sound_words"];
    private static readonly Regex NotDialogueTrack =
        new(@"\b(?:comment|sign|song|forced|karaoke|lyrics only|descriptive|audio desc)",
            RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

    public static string? KindFor(string path)
    {
        var ext = PyPath.Suffix(path).ToLowerInvariant();
        if (Media.TextSubExts.Contains(ext))
            return "subtitle";
        if (Media.ImageSubExts.Contains(ext))
            return null;
        return "video";
    }

    /// <summary>Files as given; folders expanded (recursively) to the videos and subtitle files inside. Returns
    /// (files, skipped picture-subtitle files). Use PairUp next.</summary>
    public static (List<string> Files, List<string> Skipped) ExpandPaths(IEnumerable<string> paths, int limit = 500)
    {
        var outList = new List<string>();
        var skipped = new List<string>();
        foreach (var p in paths)
        {
            if (Directory.Exists(p))
                Walk(p, outList, limit * 3);
            else if (File.Exists(p))
            {
                if (KindFor(p) == null)
                    skipped.Add(p);
                else
                    outList.Add(p);
            }
        }
        var seen = new HashSet<string>();
        var uniq = new List<string>();
        foreach (var p in outList)
        {
            if (seen.Add(PyPath.Key(p)))
                uniq.Add(PyPath.Abs(p));
        }
        return (uniq, skipped);
    }

    // os.walk, top-down, folders in sorted order; stops once there are enough files
    private static bool Walk(string root, List<string> outList, int max)
    {
        List<string> entries;
        try
        {
            entries = PyPath.List(root);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return false;
        }
        var dirs = new List<string>();
        var files = new List<string>();
        foreach (var e in entries)
        {
            var name = PyPath.Name(e);
            if (Directory.Exists(e))
            {
                // os.walk doesn't follow links to folders
                if (new DirectoryInfo(e).LinkTarget == null)
                    dirs.Add(name);
            }
            else
                files.Add(name);
        }
        dirs.Sort(PyFmt.Order);
        files.Sort(PyFmt.Order);
        foreach (var f in files)
        {
            var ext = PyPath.Suffix(f).ToLowerInvariant();
            if (!f.Contains(Media.TmpMark) && (Media.VideoExts.Contains(ext) || Media.TextSubExts.Contains(ext)))
                outList.Add(PyPath.Join(root, f));
        }
        if (outList.Count >= max)
            return true;
        foreach (var d in dirs)
        {
            if (Walk(PyPath.Join(root, d), outList, max))
                return true;
        }
        return false;
    }

    /// <summary>Sort dropped files out. A subtitle file whose video is also there (or already in the list, or, when
    /// subtitle files get embedded, sitting next to it on disk) becomes part of that video instead of a separate
    /// item, so each video ends up as one file with the cleaned subtitles inside. `explicit` = files the user picked
    /// one by one (anything else came from a dropped folder).</summary>
    public static Plan PairUp(List<string> files, IEnumerable<string>? explicitFiles = null, bool embed = true,
                              IEnumerable<string>? knownVideos = null, int limit = 500)
    {
        var expl = (explicitFiles ?? []).Select(PyPath.Key).ToHashSet();
        var known = (knownVideos ?? []).Select(PyPath.NormCase).ToHashSet();
        var vids = files.Where(f => KindFor(f) == "video").ToList();
        var vidKeys = vids.Select(PyPath.NormCase).ToHashSet();
        vidKeys.UnionWith(known);
        var plan = new Plan { Videos = [.. vids] };
        foreach (var f in files)
        {
            if (KindFor(f) != "subtitle")
                continue;
            var fromFolder = !expl.Contains(PyPath.Key(f));
            var match = Media.FindMatchingVideo(f);
            var key = match != null ? PyPath.NormCase(match) : null;
            if (match != null && (vidKeys.Contains(key!) || (embed && !fromFolder)))
            {
                if (!vidKeys.Contains(key!))
                {
                    plan.Videos.Add(match);
                    vidKeys.Add(key!);
                }
                if (!plan.DroppedWith.TryGetValue(match, out var list))
                    plan.DroppedWith[match] = list = [];
                list.Add(f);
            }
            else if (fromFolder)
                plan.Orphans.Add(f);
            else
                plan.Subtitles.Add(f);
        }
        if (plan.Videos.Count > limit)
            plan.Videos = plan.Videos[..limit];
        return plan;
    }

    /// <summary>Find the subtitle files next to a video and add them to it as extra tracks.</summary>
    public static void AttachSidecars(Item item, Action<string>? log = null)
    {
        if (item.Kind != "video")
            return;
        item.Sidecars = [];
        var k = 0;
        foreach (var path in Media.FindSidecars(item.Path))
        {
            try
            {
                var (tr, doc) = Media.SidecarTrack(path, k);
                item.Sidecars.Add(tr);
                item.Docs[tr.Index] = doc;
            }
            catch (Exception e)
            {
                log?.Invoke($"Couldn't read {path}: {e.Message}");
            }
            k++;
        }
    }

    /// <summary>Subtitle files next to the video that become redundant once the cleaned track is inside it: the one
    /// that was cleaned (unless the SDH version is being kept) and other files in the same language. Forced-subtitle
    /// files are never touched, and neither is an SDH file when a plain track was the one cleaned.</summary>
    public static List<string> RemovableSidecars(Item item, bool keepSdh, string mode)
    {
        var tr = item.Track();
        if (tr == null || item.Sidecars.Count == 0)
            return [];
        var plain = PlainPick(item, tr);
        keepSdh = keepSdh && !plain;
        if (mode == "sidecar")
            return tr.External.Length > 0 && !keepSdh ? [tr.External] : [];
        if (mode != "replace")
            return [];
        var lang = Media.LangShort(tr.Language);
        var outList = new List<string>();
        foreach (var s in item.Sidecars)
        {
            if (s.Forced)
                continue;
            if (s.Index == tr.Index)
            {
                if (!keepSdh)
                    outList.Add(s.External);
            }
            else if (lang.Length > 0 && (Media.LangShort(s.Language) == lang || Media.LangShort(s.Language) == "")
                     && !(plain && IsSdh(item, s)))
                outList.Add(s.External);
        }
        return outList;
    }

    /// <summary>Number of lines with real SDH annotations (sounds, speaker names, music cues).</summary>
    public static int SdhScore(SubtitleDoc doc)
    {
        var res = Cleaner.CleanDoc(doc, ReferenceOpts);
        return res.Results.Count(r => r.Changed && r.Rules.Overlaps(SdhRules));
    }

    /// <summary>Record how many SDH annotations each track has (used to spot the SDH track).</summary>
    public static void Measure(Item item) =>
        item.SdhCounts = item.Docs.ToDictionary(kv => kv.Key, kv => SdhScore(kv.Value));

    /// <summary>Flagged as SDH, or full of sound descriptions even though nobody labelled it.</summary>
    public static bool IsSdh(Item item, SubTrack t)
    {
        var doc = item.Docs.GetValueOrDefault(t.Index);
        var n = item.SdhCounts.GetValueOrDefault(t.Index);
        return t.SdhFlagged || (doc != null && n >= Math.Max(3, 0.04 * doc.Cues.Count));
    }

    private static bool SideTrack(SubTrack t) => t.Forced || (NotDialogueTrack.IsMatch(t.Title ?? "") && !t.SdhFlagged);

    private static bool InDropped(Item item, SubTrack t, HashSet<string> dropped) =>
        t.External.Length > 0 && dropped.Contains(PyPath.NormCase(t.External));

    /// <summary>Choose which subtitle track to clean.
    ///
    /// With a preference (the user picked a track in some file) the closest match wins: same language, then same
    /// track name, then same SDH-ness. Otherwise stay in the default track's language (English if there is one) and
    /// take the SDH track, flagged or detected by its content. Commentary, forced and signs/songs tracks are never
    /// picked unless they're all there is.</summary>
    public static int? PickTrack(Item item, TrackPref? pref)
    {
        if (item.Info == null)
            return null;
        var text = item.Tracks().Where(t => t.TextBased && item.Docs.ContainsKey(t.Index)).ToList();
        if (text.Count == 0)
            text = item.Tracks().Where(t => t.TextBased).ToList();
        if (text.Count == 0)
            return null;
        var main = text.Where(t => !SideTrack(t)).ToList();
        if (main.Count == 0)
            main = text;
        List<SubTrack> cands;
        if (pref != null)
        {
            int Score(SubTrack t)
            {
                var s = 0;
                if (t.Language == pref.Language)
                    s += 1000;
                if (pref.Title.Length > 0 && Py.Strip(t.Title ?? "").ToLowerInvariant() == Py.Strip(pref.Title).ToLowerInvariant())
                    s += 100;
                if (t.SdhFlagged == pref.Sdh)
                    s += 10;
                return s;
            }
            var best = main.Max(Score);
            cands = main.Where(t => Score(t) == best).ToList();
        }
        else
        {
            var eng = main.Where(t => Media.LangShort(t.Language) == "en").ToList();
            var defaultLang = main.FirstOrDefault(t => t.Default)?.Language;
            if (eng.Count > 0)
                cands = eng;
            else if (defaultLang != null)
                cands = main.Where(t => t.Language == defaultLang).ToList();
            else
                cands = main;
        }
        var dropped = item.Dropped.Select(PyPath.NormCase).ToHashSet();
        // a plain track already in the file wins over the SDH one (the user's decision, #20): clean that one for
        // strays and make it the default. Only when there's no plain track is the SDH track cleaned.
        var plain = cands.Where(t => !IsSdh(item, t)).ToList();
        if (plain.Count > 0 && pref == null)
        {
            return First(plain, t => (item.Docs.TryGetValue(t.Index, out var d) ? d.Cues.Count : 0,
                                      InDropped(item, t, dropped), t.Default, -t.SubPos)).Index;
        }
        return First(cands, t => (IsSdh(item, t), InDropped(item, t, dropped), item.SdhCounts.GetValueOrDefault(t.Index, -1),
                                  t.Default, -t.SubPos)).Index;
    }

    /// <summary>max(seq, key=...): the first element with the largest key.</summary>
    public static T First<T, TKey>(IEnumerable<T> seq, Func<T, TKey> key)
    {
        var cmp = Comparer<TKey>.Default;
        var has = false;
        T best = default!;
        TKey bestKey = default!;
        foreach (var x in seq)
        {
            var k = key(x);
            if (!has || cmp.Compare(k, bestKey) > 0)
            {
                best = x;
                bestKey = k;
                has = true;
            }
        }
        return has ? best : throw new InvalidOperationException("max() of an empty list");
    }

    /// <summary>min(seq, key=...): the first element with the smallest key.</summary>
    public static T Least<T, TKey>(IEnumerable<T> seq, Func<T, TKey> key)
    {
        var cmp = Comparer<TKey>.Default;
        var has = false;
        T best = default!;
        TKey bestKey = default!;
        foreach (var x in seq)
        {
            var k = key(x);
            if (!has || cmp.Compare(k, bestKey) < 0)
            {
                best = x;
                bestKey = k;
                has = true;
            }
        }
        return has ? best : throw new InvalidOperationException("min() of an empty list");
    }

    /// <summary>`t` is a regular track although the file also has an SDH track in the same language. Cleaning it
    /// catches strays, the cleaned copy replaces it as the default, and the SDH track stays (just not the default).</summary>
    public static bool PlainPick(Item item, SubTrack t)
    {
        if (item.Kind != "video" || IsSdh(item, t))
            return false;
        var lang = Media.LangShort(t.Language);
        return item.Tracks().Any(o => o.Index != t.Index && o.TextBased && item.Docs.ContainsKey(o.Index)
                                      && Media.LangShort(o.Language) == lang && !SideTrack(o) && IsSdh(item, o));
    }

    /// <summary>Cleaned on save without the user forcing it: a subtitle file, an SDH track, or the plain track chosen
    /// over an SDH one.</summary>
    public static bool AutoCleaned(Item item, SubTrack t) => t.External.Length > 0 || IsSdh(item, t) || PlainPick(item, t);

    /// <summary>A plain track with nothing to clean still has to be made the default.</summary>
    public static bool PlainToDefault(Item item, SubTrack t) => t.External.Length == 0 && PlainPick(item, t) && !t.Default;

    /// <summary>The regular (non-SDH) track in the same language as the selected one, to check our work against.</summary>
    public static int? PlainPartner(Item item)
    {
        var sel = item.Track();
        if (sel == null || item.Info == null || !IsSdh(item, sel))
            return null;                     // a plain track has nothing to be checked against
        var lang = Media.LangShort(sel.Language);
        var cands = item.Tracks().Where(t => t.TextBased && t.Index != sel.Index && item.Docs.ContainsKey(t.Index)
                                             && Media.LangShort(t.Language) == lang && !SideTrack(t) && !IsSdh(item, t)).ToList();
        if (cands.Count == 0)
            return null;
        return Least(cands, t => (item.SdhCounts.GetValueOrDefault(t.Index), -item.Docs[t.Index].Cues.Count)).Index;
    }

    /// <summary>Clean the selected track with this file's options, cross-checked against its plain track.</summary>
    public static CleanResult? CleanItem(Item item, CleanOptions defaults)
    {
        var doc = item.Sel is { } s ? item.Docs.GetValueOrDefault(s) : null;
        if (doc == null)
            return null;
        var p = PlainPartner(item);
        var res = Cleaner.CleanDoc(doc, item.Opts ?? defaults, p is { } pi ? item.Docs.GetValueOrDefault(pi) : null);
        res.Overrides = item.Overrides;
        return res;
    }

    public static TrackPref PrefFrom(SubTrack track) => new() { Language = track.Language, Title = track.Title ?? "", Sdh = track.SdhFlagged };

    // ================================================================ names and tidy-up

    /// <summary>The identity this video will be renamed after, or null (keep its name / nothing confident).</summary>
    public static Choice? NameChoice(Item item)
    {
        if (item.NamePick is "keep")
            return null;
        if (item.NamePick is Choice c)
            return c;
        var m = item.Match;
        return m != null && m.Confident ? m.Best : null;
    }

    /// <summary>TMDB found possible matches but none clearly enough, and the user hasn't picked yet.</summary>
    public static bool NameUnsure(Item item)
    {
        var m = item.Match;
        return item.NamePick == null && m != null && !m.Confident && m.Choices.Count > 0;
    }

    /// <summary>The new file name, or null when it stays as it is.</summary>
    public static string? NewName(Item item, bool withYear = false)
    {
        if (item.Kind != "video")
            return null;
        var c = NameChoice(item);
        if (c == null)
            return null;
        var name = Naming.Render(c, PyPath.Suffix(item.Path), withYear);
        return name == PyPath.Name(item.Path) ? null : name;
    }

    /// <summary>Subtitle tracks inside the video that the tidy-up would remove.</summary>
    public static List<SubTrack> OtherLanguages(Item item, string keep = "eng")
    {
        if (item.Kind != "video" || item.Info == null)
            return [];
        return item.Info.Subs.Where(t => Media.IsOtherLanguage(t, keep)).ToList();
    }
}
