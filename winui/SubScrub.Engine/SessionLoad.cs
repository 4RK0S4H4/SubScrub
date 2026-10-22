using System.Text.Json.Nodes;

namespace SubScrub.Engine;

public sealed partial class Session
{
    // files being read: up to two at once where the drives allow it (the rule the save uses, RunTogether)
    private readonly Dictionary<int, (Thread Thread, CancellationTokenSource Cancel, string Drive)> _loads = [];
    private readonly Dictionary<string, (string Key, bool Ssd)> _drives = [];
    private CancellationTokenSource _loadCancel = new();
    private readonly AutoResetEvent _dirty = new(false);
    private volatile bool _reviewDirty;
    private volatile bool _closed;
    private Thread? _notifier;

    /// <summary>Finds FFmpeg and MKVToolNix and starts sending views to the window (the helper's start-up).</summary>
    public void Start(string version)
    {
        Tools = Media.FindTools(Cfg.Tools);
        Media.AppVersion = version;
        Changed = review =>
        {
            if (review)
                _reviewDirty = true;
            _dirty.Set();
        };
        PumpLoader = PumpLoaderNow;
        StartNames();
        _notifier = new Thread(NotifyLoop) { IsBackground = true, Name = "notify" };
        _notifier.Start();
        Log($"SubScrub {version} (engine). FFmpeg: {Tools.Ffmpeg ?? "not found"}; MKVToolNix: {Tools.Mkvmerge ?? "not found"}");
    }

    private void NotifyLoop()
    {
        while (!_closed)
        {
            _dirty.WaitOne();
            if (_closed)
                return;
            try
            {
                JsonObject view;
                bool review;
                int? uid;
                lock (Lock)
                {
                    view = FilesView();
                    review = _reviewDirty;
                    _reviewDirty = false;
                    uid = ReviewUid;
                }
                Emit("files", view);
                if (review && uid != null)
                    Emit("review_changed", new JsonObject { ["uid"] = uid });
            }
            catch (Exception e)
            {
                Log($"Couldn't update the window: {e.Message}");
            }
            Thread.Sleep(100);
        }
    }

    public void Close()
    {
        _saveCancel.Cancel();
        _loadCancel.Cancel();
        List<Thread> loads;
        lock (Lock)
            loads = _loads.Values.Select(l => l.Thread).ToList();
        foreach (var t in loads.Prepend(_saver))
            t?.Join(TimeSpan.FromSeconds(60));
        lock (Lock)
        {
            foreach (var it in Items)
            {
                if (it.Tmpdir != null)
                    TryDeleteDir(it.Tmpdir);
            }
        }
        _closed = true;
        _dirty.Set();
    }

    internal static void TryDeleteDir(string dir)
    {
        try
        {
            Directory.Delete(dir, true);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
        }
    }

    public JsonObject RescanTools()
    {
        lock (Lock)
        {
            Tools = Media.FindTools(Cfg.Tools);
            PumpLoader();
            Changed(false);
            return ToolsView();
        }
    }

    // ================================================================ adding and removing

    public JsonArray Leftovers(List<string> paths)
    {
        var outList = new JsonArray();
        var k = 0;
        foreach (var lo in Media.FindLeftovers(paths))
        {
            outList.Add(new JsonObject
            {
                ["id"] = k++, ["path"] = lo.Path, ["name"] = lo.Name, ["size"] = FmtSize(lo.Size),
                ["original"] = !string.IsNullOrEmpty(lo.Original),
            });
        }
        return outList;
    }

    private static JsonObject Message(string title, string text) => new() { ["title"] = title, ["text"] = text };

    /// <summary>Add files and folders. Returns messages to show (picture subtitles, nothing found, FFmpeg needed).</summary>
    public JsonObject Add(List<string> paths)
    {
        lock (Lock)
        {
            if (Busy())
                throw new InvalidOperationException("Wait for the save to finish.");
            var messages = new JsonArray();
            var (files, skipped) = Batch.ExpandPaths(paths);
            if (skipped.Count > 0)
            {
                var m = Message("Picture subtitles can't be cleaned",
                                "These are picture-based subtitle files (Blu-ray or DVD), which can't be edited as text:");
                m["rows"] = Rows(skipped.Take(8).Select(p => (PyPath.Name(p), "")));
                messages.Add(m);
            }
            if (files.Count == 0)
            {
                if (paths.Count > 0 && skipped.Count == 0)
                    messages.Add(Message("Nothing to add", "No video or subtitle files were found there."));
                return new JsonObject { ["added"] = 0, ["messages"] = messages };
            }
            var expl = paths.Where(File.Exists).ToList();
            var known = Items.Where(i => i.Kind == "video" && i.Pending).Select(i => i.Path).ToList();
            var plan = Batch.PairUp(files, expl, Cfg.SubMode == "embed", known);
            files = [.. plan.Videos, .. plan.Subtitles];
            if (files.Count == 0)
            {
                if (plan.Orphans.Count > 0)
                    messages.Add(Message("Nothing to add", "Only subtitle files without a matching video were found there."));
                return new JsonObject { ["added"] = 0, ["messages"] = messages };
            }
            var videos = plan.Videos.ToList();
            if (videos.Count > 0 && !Tools.Ready)
            {
                Tools = Media.FindTools(Cfg.Tools);
                if (!Tools.Ready)
                {
                    var m = Message("FFmpeg is needed", "SubScrub needs FFmpeg to read video files. Set it up, then add them again.");
                    m["tools"] = true;
                    messages.Add(m);
                    files = files.Where(f => !videos.Contains(f)).ToList();
                    if (files.Count == 0)
                        return new JsonObject { ["added"] = 0, ["messages"] = messages };
                }
            }
            var existing = new Dictionary<string, Item>();
            foreach (var i in Items)
                existing[PyPath.NormCase(i.Path)] = i;
            var added = 0;
            foreach (var f in files)
            {
                if (existing.TryGetValue(PyPath.NormCase(f), out var old))
                {
                    if (old.Pending)
                    {
                        old.Dropped.UnionWith(plan.DroppedWith.GetValueOrDefault(f) ?? []);
                        continue;
                    }
                    RemoveItem(old);                      // finished before: start it over
                }
                var it = new Item(f, Batch.KindFor(f)!) { Dropped = (plan.DroppedWith.GetValueOrDefault(f) ?? []).ToHashSet() };
                Items.Add(it);
                added++;
            }
            var paired = plan.DroppedWith.Values.Sum(v => v.Count);
            var bits = new List<string>();
            if (paired != 0)
                bits.Add($"{PyFmt.Plural(paired, "subtitle file")} matched to {PyFmt.Plural(plan.DroppedWith.Count, "video")}");
            if (plan.Orphans.Count > 0)
                bits.Add($"{PyFmt.Plural(plan.Orphans.Count, "subtitle file")} without a matching video left alone");
            AddNote = bits.Count > 0 ? string.Join("; ", bits) + "." : "";
            StatusMsg = ("", "");
            PumpLoader();
            Changed(false);
            return new JsonObject { ["added"] = added, ["messages"] = messages, ["last_dir"] = PyPath.Dir(files[0]) };
        }
    }

    private void RemoveItem(Item it)
    {
        if (_loads.TryGetValue(it.Uid, out var load))
            load.Cancel.Cancel();
        if (it.Tmpdir != null)
            TryDeleteDir(it.Tmpdir);
        Items.Remove(it);
        if (ReviewUid == it.Uid)
            ReviewUid = null;
    }

    public void Remove(int uid)
    {
        lock (Lock)
        {
            if (Busy())
                throw new InvalidOperationException("Wait for the save to finish.");
            var it = Item(uid);
            if (it != null)
                RemoveItem(it);
            if (Items.Count == 0)
            {
                AddNote = "";
                StatusMsg = ("", "");
            }
            PumpLoader();
            Changed(false);
        }
    }

    public void Clear()
    {
        lock (Lock)
        {
            if (Busy())
                throw new InvalidOperationException("Wait for the save to finish.");
            foreach (var it in Items.ToList())
                RemoveItem(it);
            AddNote = "";
            StatusMsg = ("", "");
            Changed(false);
        }
    }

    // ================================================================ background loading

    public static (Dictionary<int, SubtitleDoc> Docs, Dictionary<int, string> Errors) LoadDocs(MediaInfo info, Dictionary<int, string> paths)
    {
        var docs = new Dictionary<int, SubtitleDoc>();
        var errors = new Dictionary<int, string>();
        foreach (var tr in info.Subs)
        {
            if (!tr.TextBased)
                continue;
            if (!paths.TryGetValue(tr.Index, out var p) || string.IsNullOrEmpty(p))
            {
                errors[tr.Index] = "Couldn't read this track.";
                continue;
            }
            try
            {
                docs[tr.Index] = SubFormats.Load(p, tr.Fmt);
            }
            catch (Exception e)
            {
                errors[tr.Index] = e.Message;
            }
        }
        return (docs, errors);
    }

    private void PumpLoaderNow()
    {
        while (!Busy() && StartNextLoad())
        {
        }
    }

    private (string Key, bool Ssd) DriveFor(string path)
    {
        var dir = PyPath.Dir(PyPath.Abs(path));
        if (!_drives.TryGetValue(dir, out var d))
            _drives[dir] = d = Media.DriveOf(dir);
        return d;
    }

    /// <summary>Starts reading the next file in the list if a reader is free for its drive. False when nothing started.</summary>
    private bool StartNextLoad()
    {
        Item? it;
        while (true)
        {
            it = Items.FirstOrDefault(i => i.State == "queued");
            if (it == null)
                return false;
            if (it.Kind != "subtitle")
                break;
            try
            {
                var (info, doc) = Media.ProbeSubtitleFile(it.Path);
                it.Info = info;
                it.Docs = new Dictionary<int, SubtitleDoc> { [0] = doc };
                FinishLoad(it);
            }
            catch (Exception e)
            {
                it.State = "failed";
                it.Message = $"couldn't read it ({e.Message})";
            }
        }
        if (!Tools.Ready)
            return false;
        // files start in list order; a second one only on an SSD or another disk, as with saving
        var (drive, ssd) = DriveFor(it.Path);
        if (_loads.Count >= (_loads.Count == 0 ? 1 : Media.MaxParallelSaves)
            || _loads.Values.Count(l => l.Drive == drive) >= (ssd ? 2 : 1) || (_loads.Count > 0 && drive.Length == 0))
            return false;
        it.State = "probing";
        if (_loadCancel.IsCancellationRequested)
            _loadCancel = new CancellationTokenSource();
        var mine = CancellationTokenSource.CreateLinkedTokenSource(_loadCancel.Token);
        var item = it;
        var thread = new Thread(() => LoadOne(item, mine.Token)) { IsBackground = true, Name = "load" };
        _loads[it.Uid] = (thread, mine, drive);
        thread.Start();
        return true;
    }

    /// <summary>throttled(): passes progress on at most every `interval` seconds (always the first, the last and a
    /// new message).</summary>
    public static Action<double, string> Throttled(Action<double, string> emit, double interval = 0.08)
    {
        var last = 0.0;
        string? lastMsg = null;
        var clock = System.Diagnostics.Stopwatch.StartNew();
        return (frac, msg) =>
        {
            var now = clock.Elapsed.TotalSeconds;
            if (msg != lastMsg || frac >= 1.0 || frac < 0 || now - last >= interval)
            {
                last = now;
                lastMsg = msg;
                emit(frac, msg);
            }
        };
    }

    private void LoadOne(Item it, CancellationToken cancel)
    {
        var (uid, path, tools) = (it.Uid, it.Path, Tools);
        string? tmp = null;
        try
        {
            var reader = ReadCache.Reader(tools);
            var cached = ReadCache.Get(path, reader);
            var probe = cached?.Probe ?? Media.ProbeText(tools, path, Log);
            var info = Media.ParseProbe(path, probe);
            if (!it.ReadAnyway && info.Subs.FirstOrDefault(t => t.CleanedBy.Length > 0) is { } mark)
            {
                // cleaned by SubScrub before (and unchanged since, or the tag would be gone): Done, nothing to read
                lock (Lock)
                {
                    if (Item(uid) == null || cancel.IsCancellationRequested)
                        return;
                    it.Info = info;
                    it.Marked = mark.CleanedBy;
                    it.State = "done";
                    it.Output = path;
                    it.DoneText = $"Already cleaned by SubScrub {mark.CleanedBy}";
                    it.Progress = -1;
                    Log($"{it.Name}: already cleaned by SubScrub {mark.CleanedBy} (track {mark.Index})");
                    Changed(uid == ReviewUid);
                    PumpNames();
                }
                return;
            }
            var paths = new Dictionary<int, string>();
            var docs = new Dictionary<int, SubtitleDoc>();
            var errors = new Dictionary<int, string>();
            if (info.Subs.Any(t => t.TextBased))
            {
                lock (Lock)
                {
                    if (Item(uid) == null || cancel.IsCancellationRequested)
                        return;
                    it.Info = info;
                    it.State = "reading";
                    it.Progress = -1.0;
                    Changed(uid == ReviewUid);
                }
                tmp = Directory.CreateTempSubdirectory("subscrub_").FullName;
                var prog = Throttled((frac, _) =>
                {
                    lock (Lock)
                    {
                        it.Progress = frac;
                        Changed(false);
                    }
                }, 0.15);
                paths = cached != null ? Restore(cached, info, tmp) : Media.ExtractSubs(tools, info, info.Subs, tmp, prog, cancel, Log);
                (docs, errors) = LoadDocs(info, paths);
            }
            if (cached == null)
                ReadCache.Put(path, reader, probe, paths);
            else
                Log($"{it.Name}: read from the cache");
            lock (Lock)
            {
                if (Item(uid) == null || cancel.IsCancellationRequested)
                {
                    if (tmp != null)
                        TryDeleteDir(tmp);
                    return;
                }
                it.Info = info;
                it.Docs = docs;
                it.DocErrors = errors;
                it.Tmpdir = tmp;
                foreach (var (idx, err) in errors)
                    Log($"{it.Name}: track {idx}: {err}");
                Batch.AttachSidecars(it, Log);
                FinishLoad(it);
            }
        }
        catch (Cancelled)
        {
            if (tmp != null)
                TryDeleteDir(tmp);
        }
        catch (Exception e)
        {
            lock (Lock)
            {
                if (Item(uid) != null)
                {
                    Log($"Couldn't read {path}: {e}");
                    var why = e.Message.Split('\n')[0].Trim();
                    it.State = "failed";
                    it.Message = why.Length == 0 ? "couldn't read this file"
                        : $"couldn't read this file ({PyFmt.Head(why, 110)}: {e.GetType().Name})";
                    Changed(uid == ReviewUid);
                }
            }
        }
        finally
        {
            lock (Lock)
            {
                _loads.Remove(uid);
                PumpLoader();
                if (!Items.Any(i => i.State is "queued" or "probing" or "reading"))
                    CheckLocksAsync();
            }
        }
    }

    /// <summary>A cached file's tracks, written back as extraction would have left them.</summary>
    private static Dictionary<int, string> Restore(ReadCache.Entry cached, MediaInfo info, string tmp)
    {
        var paths = new Dictionary<int, string>();
        foreach (var t in info.Subs.Where(t => t.TextBased && cached.Tracks.ContainsKey(t.Index)))
        {
            paths[t.Index] = PyPath.Join(tmp, $"track{t.Index}.{t.Fmt}");
            File.WriteAllBytes(paths[t.Index], cached.Tracks[t.Index]);
        }
        return paths;
    }

    private void FinishLoad(Item it)
    {
        Batch.Measure(it);
        if (!it.Manual || it.Sel is not { } s || !it.Docs.ContainsKey(s))
            it.Sel = Batch.PickTrack(it, Pref);
        it.State = "ready";
        it.Progress = -1;
        ComputeResult(it);
        Changed(it.Uid == ReviewUid);
        PumpNames();
    }

    private void CheckLocksAsync() =>
        new Thread(() =>
        {
            try
            {
                CheckLocks();
            }
            catch (Exception e)
            {
                Log($"Couldn't check which files are in use: {e.Message}");
            }
        }) { IsBackground = true, Name = "locks" }.Start();

    /// <summary>Whether a title lookup is running (set by the part of the engine that does them).</summary>
    public Func<bool> NamesBusy { get; set; } = () => false;

    /// <summary>For tests and the scripted QA run: wait until nothing is being read or looked up.</summary>
    public bool WaitIdle(double timeout = 600)
    {
        var end = DateTime.UtcNow.AddSeconds(timeout);
        while (DateTime.UtcNow < end)
        {
            bool quiet;
            lock (Lock)
            {
                quiet = !Items.Any(i => i.State is "queued" or "probing" or "reading" || i.NameState == "looking")
                        && !NamesBusy() && !Busy();
            }
            if (quiet)
                return true;
            Thread.Sleep(50);
        }
        return false;
    }
}
