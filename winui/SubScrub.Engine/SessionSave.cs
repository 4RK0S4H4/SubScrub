using System.Text.Json.Nodes;

namespace SubScrub.Engine;

public sealed partial class Session
{
    private Thread? _saver;
    private CancellationTokenSource _saveCancel = new();

    private static JsonObject Button(string key, string label, string kind = "") =>
        new() { ["key"] = key, ["label"] = label, ["kind"] = kind };

    private static JsonArray Rows(IEnumerable<(string, string)> rows) =>
        new(rows.Select(r => (JsonNode)new JsonArray(r.Item1, r.Item2)).ToArray());

    private static string Answer(JsonObject a, string key) =>
        a[key] is JsonValue v && v.TryGetValue<string>(out var s) ? s : "";

    /// <summary>What to ask before saving, one question at a time. The window shows it, adds the answer to
    /// `answers` and calls again; {"go": ...} means everything is answered. Nothing is changed on disk here.</summary>
    public JsonObject SaveQuestion(int? only, JsonObject? answers)
    {
        var a = answers ?? [];
        var c = Cfg;
        List<string> paths;
        lock (Lock)
        {
            if (Busy())
                return new JsonObject { ["stop"] = true };
            var first = Items.Where(i => i.Pending && (only == null || i.Uid == only)).ToList();
            if (first.Count == 0)
                return new JsonObject { ["stop"] = true };
            paths = first.SelectMany(FilesTouched).ToList();
        }
        var found = paths.Count > 0 ? WhoHasOpen(paths) : [];
        lock (Lock)
        {
            ApplyLocks(found);
            var scope = Items.Where(i => i.Pending && (only == null || i.Uid == only)).ToList();
            var locked = scope.Where(i => i.Locked.Count > 0).ToList();
            if (locked.Count > 0)
            {
                if (Answer(a, "locks") == "skip")
                {
                    scope = scope.Where(i => i.Locked.Count == 0).ToList();
                    if (scope.Count == 0)
                        return new JsonObject { ["stop"] = true };
                }
                else
                {
                    var progs = new Dictionary<string, int>();
                    foreach (var i in locked)
                    {
                        foreach (var names in i.Locked.Values)
                        {
                            foreach (var n in names)
                                progs[n] = progs.GetValueOrDefault(n) + 1;
                        }
                    }
                    // sorted(progs, key=-count): stable, so ties keep the order they were first seen in
                    var who = string.Join(" and ", progs.Keys.Select((k, idx) => (k, idx)).OrderBy(x => -progs[x.k])
                        .ThenBy(x => x.idx).Select(x => x.k));
                    var one = locked.Count == 1;
                    return new JsonObject
                    {
                        ["ask"] = new JsonObject
                        {
                            ["key"] = "locks",
                            ["title"] = $"{PyFmt.Plural(locked.Count, "file")} {(one ? "is" : "are")} open in {who}",
                            ["rows"] = Rows(locked.Take(8).Select(i => (i.Name, ""))), ["more"] = Math.Max(0, locked.Count - 8),
                            ["text"] = "SubScrub can't rewrite a file while another program has it open. If you're seeding "
                                       + "these, pause those torrents now, then click Check Again. Nothing has been changed "
                                       + "yet.",
                            ["detail"] = "Once a file is rewritten its torrent can't seed it anymore (the contents no longer "
                                         + "match): remove the torrent afterwards and keep the files. To keep seeding the "
                                         + "original, use Settings → Videos → New File instead.",
                            ["buttons"] = new JsonArray(Button("cancel", "Cancel"), Button("skip", $"Skip {(one ? "It" : "These")}"),
                                                        Button("again", "Check Again", "primary")),
                        },
                    };
                }
            }
            if (Renaming() && c.TmdbKey.Length > 0 && scope.Any(i => i.Kind == "video" && i.NameState is "" or "looking"))
            {
                return new JsonObject
                {
                    ["tell"] = new JsonObject
                    {
                        ["title"] = "Still looking up titles",
                        ["text"] = "SubScrub is still finding the titles for the new names. Try again in a moment.",
                    },
                };
            }
            var loaded = scope.Where(i => i.State == "ready" && i.Result != null).ToList();
            var unseen = scope.Where(i => i.State is "queued" or "probing" or "reading").ToList();
            bool SdhOk(Item i) => i.Force || (i.Track() is { } t && Batch.AutoCleaned(i, t));
            var flagged = loaded.Where(i => SdhOk(i) && i.Result!.Unresolved().Count > 0).ToList();
            var single = scope.Count == 1;
            if (single && loaded.Count > 0)
            {
                var it = loaded[0];
                if (!SdhOk(it) && !HasTidy(it))
                {
                    if (Answer(a, "lone") != "go")
                    {
                        return new JsonObject
                        {
                            ["ask"] = new JsonObject
                            {
                                ["key"] = "lone", ["title"] = "Clean it anyway?",
                                ["text"] = $"“{it.Name}” only has regular subtitles: there are no sound descriptions or "
                                           + "speaker names to strip.",
                                ["detail"] = "Cleaning it anyway removes dialogue dashes and restyles song lyrics only.",
                                ["buttons"] = new JsonArray(Button("cancel", "Cancel"), Button("go", "Clean Anyway", "primary")),
                            },
                        };
                    }
                    it.Force = true;
                    ComputeResult(it);
                    flagged = it.Result != null && it.Result.Unresolved().Count > 0 ? [it] : [];
                }
                else if (SdhOk(it) && FinalChanged(it.Result!) == 0 && it.Track()!.External.Length == 0 && !HasTidy(it))
                {
                    return new JsonObject
                    {
                        ["tell"] = new JsonObject
                        {
                            ["title"] = "Nothing to remove", ["text"] = "This track has nothing to clean with the current edits.",
                        },
                    };
                }
            }
            var policy = "hold_new";
            var skipUids = new HashSet<int>();
            if (flagged.Count > 0)
            {
                var choice = Answer(a, "decision");
                if (choice is not ("guess" or "orig" or "skip"))
                {
                    var n = flagged.Sum(i => i.Result!.Unresolved().Count);
                    var others = scope.Count(i => !flagged.Contains(i));
                    var options = new JsonArray(
                        new JsonObject
                        {
                            ["key"] = "review", ["title"] = "Review Them Now",
                            ["desc"] = "Opens the first one with just those lines listed.", ["recommended"] = true,
                        },
                        new JsonObject
                        {
                            ["key"] = "guess", ["title"] = "Save With SubScrub's Best Guess",
                            ["desc"] = "Applies every edit, including the uncertain ones.",
                        },
                        new JsonObject
                        {
                            ["key"] = "orig", ["title"] = "Save, but Leave Those Lines Alone",
                            ["desc"] = "Uncertain lines stay exactly as they were.",
                        });
                    if (others != 0)
                        options.Add(new JsonObject
                        {
                            ["key"] = "skip", ["title"] = $"Only Save the Other {PyFmt.Plural(others, "File")}",
                            ["desc"] = "Come back to these later.",
                        });
                    return new JsonObject
                    {
                        ["ask"] = new JsonObject
                        {
                            ["key"] = "decision",
                            ["title"] = $"{PyFmt.Plural(flagged.Count, "file")} {(flagged.Count == 1 ? "has" : "have")} "
                                        + $"{PyFmt.Plural(n, "line")} SubScrub isn't sure about",
                            ["rows"] = Rows(flagged.Take(6).Select(i => (i.Name, PyFmt.Plural(i.Result!.Unresolved().Count, "line")))),
                            ["more"] = Math.Max(0, flagged.Count - 6),
                            ["text"] = "These are things like words in (parentheses) that might be dialogue, or text the "
                                       + "regular subtitles in the same file disagree with.",
                            ["options"] = options,
                            ["detail"] = unseen.Count > 0 ? $"{PyFmt.Plural(unseen.Count, "file")} still being read will follow the same choice." : "",
                            ["buttons"] = new JsonArray(Button("cancel", "Cancel")),
                            ["review_uid"] = flagged[0].Uid,
                        },
                    };
                }
                if (choice == "skip")
                    skipUids = flagged.Select(i => i.Uid).ToHashSet();
                else
                    policy = choice;
            }
            var jobsItems = scope.Where(i => !skipUids.Contains(i.Uid)).ToList();
            if (jobsItems.Count == 0)
                return new JsonObject { ["stop"] = true };
            var unsure = jobsItems.Where(i => Renaming() && Batch.NameUnsure(i)).ToList();
            if (unsure.Count > 0)
            {
                if (Answer(a, "names") == "keep")
                {
                    foreach (var i in unsure)
                        i.NamePick = "keep";
                    Changed(false);
                }
                else
                {
                    return new JsonObject
                    {
                        ["ask"] = new JsonObject
                        {
                            ["key"] = "names", ["title"] = "Not sure of some titles",
                            ["text"] = $"SubScrub couldn't tell which title {PyFmt.Plural(unsure.Count, "file")} "
                                       + $"{(unsure.Count == 1 ? "is" : "are")}:",
                            ["rows"] = Rows(unsure.Take(6).Select(i => (i.Name, ""))), ["more"] = Math.Max(0, unsure.Count - 6),
                            ["uids"] = new JsonArray(unsure.Select(i => (JsonNode)i.Uid).ToArray()),
                            ["buttons"] = new JsonArray(Button("cancel", "Cancel"), Button("keep", "Keep Their Names"),
                                                        Button("pick", "Choose Titles…", "primary")),
                        },
                    };
                }
            }
            var renames = jobsItems.Where(i => !string.IsNullOrEmpty(NewName(i))).ToList();
            var rewrites = jobsItems.Where(i => (i.Kind == "video" && c.VideoMode == "replace")
                                                || (i.Kind == "subtitle" && c.SubMode == "embed" && i.Info != null
                                                    && !string.IsNullOrEmpty(i.Info.Companion))).ToList();
            if (rewrites.Count > 0 && c.ConfirmReplace && Answer(a, "rewrite") != "go")
            {
                var n = rewrites.Count;
                var what = n == 1 ? $"“{rewrites[0].Name}”" : $"{n} files";
                return new JsonObject
                {
                    ["ask"] = new JsonObject
                    {
                        ["key"] = "rewrite", ["title"] = n == 1 ? "Rewrite in place?" : $"Rewrite {n} files in place?",
                        ["text"] = $"SubScrub will rewrite {what} with the cleaned subtitles"
                                   + (c.MakeDefault ? " as the default track" : "")
                                   + $"{RewriteExtras(renames, jobsItems)}. "
                                   + "Each new file is checked before the old one goes to the Recycle Bin.",
                        ["detail"] = "Very large files may be too big for the Recycle Bin, in which case Windows deletes "
                                     + "them instead.",
                        ["checkbox"] = "Don't ask again",
                        ["buttons"] = new JsonArray(Button("cancel", "Cancel"),
                                                    Button("go", n == 1 ? "Rewrite" : $"Rewrite {n} Files", "primary")),
                    },
                };
            }
            var overwrite = true;
            if (c.VideoMode == "sidecar")
            {
                var clashes = new List<string>();
                foreach (var i in jobsItems)
                {
                    var tr = i.Track();
                    if (i.Kind == "video" && i.Info != null && tr != null)
                    {
                        var p = Media.OutputPath(i.Info, new SaveOptions { Mode = "sidecar", Language = tr.Language }, tr.Fmt);
                        if (Path.Exists(p))
                            clashes.Add(PyPath.Name(p));
                    }
                }
                if (clashes.Count > 0)
                {
                    var ow = Answer(a, "overwrite");
                    if (ow is not ("yes" or "no"))
                    {
                        return new JsonObject
                        {
                            ["ask"] = new JsonObject
                            {
                                ["key"] = "overwrite", ["title"] = "Overwrite subtitle files?", ["text"] = "These already exist:",
                                ["detail"] = "Keep Both saves the new ones under new names.",
                                ["rows"] = Rows(clashes.Take(8).Select(x => (x, ""))), ["more"] = Math.Max(0, clashes.Count - 8),
                                ["buttons"] = new JsonArray(Button("cancel", "Cancel"), Button("no", "Keep Both"),
                                                            Button("yes", "Overwrite", "primary")),
                            },
                        };
                    }
                    overwrite = ow == "yes";
                }
            }
            return new JsonObject
            {
                ["go"] = new JsonObject
                {
                    ["uids"] = new JsonArray(jobsItems.Select(i => (JsonNode)i.Uid).ToArray()), ["policy"] = policy,
                    ["overwrite"] = overwrite, ["single"] = single,
                },
            };
        }
    }

    private string RewriteExtras(List<Item> renames, List<Item> items)
    {
        var bits = new List<string>();
        if (renames.Count > 0)
            bits.Add($"rename {PyFmt.Plural(renames.Count, "file")}");
        var drops = items.Count(i => LangDrops(i).Count > 0);
        if (drops != 0)
            bits.Add($"remove other-language subtitles from {PyFmt.Plural(drops, "file")}");
        return bits.Count > 0 ? ", and " + string.Join(" and ", bits) : "";
    }

    // ================================================================ the save itself

    private int _batchTotal, _batchFinished;
    private readonly SortedDictionary<int, double> _batchRunning = [];   // job number -> its progress
    private readonly AsyncLocal<List<object>?> _sink = new();   // a job's log lines and ordered steps while another runs
    private Dictionary<string, int> _batchCounts = [];
    private int _batchKept;                          // files the Recycle Bin couldn't take, left in their folders

    /// <summary>Ask the next question, or start saving when everything is answered (see SaveQuestion).</summary>
    public JsonObject Save(int? only = null, JsonObject? answers = null)
    {
        var a = (JsonObject?)answers?.DeepClone() ?? [];
        var q = SaveQuestion(only, a);
        if (q["go"] is not JsonObject go)
            return q;
        List<Thread> loaders;
        lock (Lock)
        {
            if (Busy())
                return new JsonObject { ["stop"] = true };
            if (Config.Truthy(a["dont_ask"]))
            {
                Cfg.ConfirmReplace = false;
                Cfg.Save();
            }
            _saving = true;                         // from here on nothing else starts reading files
            _loadCancel.Cancel();
            loaders = _loads.Values.Select(l => l.Thread).ToList();
        }
        foreach (var loader in loaders)
            loader.Join(TimeSpan.FromSeconds(10));
        try
        {
            return StartBatch(go);
        }
        catch
        {
            lock (Lock)
            {
                _saving = false;
                Changed(false);
            }
            throw;
        }
    }

    private sealed class Job
    {
        public int Uid;
        public string Path = "";
        public string Kind = "";
        public MediaInfo? Info;
        public Dictionary<int, SubtitleDoc> Docs = [];
        public Dictionary<int, int> SdhCounts = [];
        public int? Sel;
        public bool Manual;
        public CleanOptions? Opts;
        public Dictionary<int, string> Overrides = [];
        public bool Force;
        public List<SubTrack> Sidecars = [];
        public HashSet<string> Dropped = [];
        public string Policy = "";
        public string? NewName;
        public HashSet<string> Touched = [];             // files this save reads, replaces, removes or renames to
        public string Drive = "";
        public bool Ssd;
    }

    private sealed class BatchCfg
    {
        public CleanOptions Opts = new();
        public string VideoMode = "", SubMode = "", Name = "", KeepLang = "eng", BatchId = "";
        public bool Keep, Default, Recycle, Overwrite, DropLangs;
        public TrackPref? Pref;
    }

    private JsonObject StartBatch(JsonObject go)
    {
        lock (Lock)
        {
            _loads.Clear();
            var single = Config.Truthy(go["single"]);
            var policy = go["policy"]!.GetValue<string>();
            var items = go["uids"]!.AsArray().Select(u => Item(u!.GetValue<int>())).Where(i => i != null).Select(i => i!).ToList();
            var jobs = new List<Job>();
            foreach (var i in items)
            {
                if (i.State is "probing" or "reading")
                {
                    if (i.Tmpdir != null)
                        TryDeleteDir(i.Tmpdir);
                    i.State = "queued";
                    i.Info = null;
                    i.Docs = [];
                    i.Tmpdir = null;
                }
                i.Message = "";
                jobs.Add(new Job
                {
                    Uid = i.Uid, Path = i.Path, Kind = i.Kind, Info = i.Info, Docs = i.Docs, SdhCounts = i.SdhCounts, Sel = i.Sel,
                    Manual = i.Manual, Opts = i.Opts, Overrides = new(i.Overrides), Force = i.Force || (single && i.Kind == "subtitle"),
                    Sidecars = [.. i.Sidecars], Dropped = [.. i.Dropped], Policy = policy, NewName = NewName(i),
                    Touched = [.. FilesTouched(i).Append(i.Path).Append(NewName(i) is { Length: > 0 } nn ? PyPath.Join(PyPath.Dir(i.Path), nn) : "")
                        .Where(x => x.Length > 0).Select(PyPath.Key)],
                });
            }
            var c = Cfg;
            var cfg = new BatchCfg
            {
                Opts = c.Defaults, VideoMode = c.VideoMode, SubMode = c.SubMode, Keep = c.KeepSdh, Default = c.MakeDefault,
                Name = c.TrackName, Recycle = c.RecycleSub, Overwrite = Config.Truthy(go["overwrite"]), Pref = Pref,
                DropLangs = c.DropLangs && c.VideoMode == "replace", KeepLang = "eng", BatchId = DateTime.Now.ToString("yyyyMMdd-HHmmss", System.Globalization.CultureInfo.InvariantCulture),
            };
            Log($"Saving {jobs.Count} file(s): video mode={c.VideoMode}, subtitle mode={c.SubMode}, "
                + $"keep SDH={(cfg.Keep ? "True" : "False")}, default={(cfg.Default ? "True" : "False")}, uncertain lines={policy}");
            _saving = true;
            _saveCancel = new CancellationTokenSource();
            _batchTotal = jobs.Count;
            _batchFinished = 0;
            _batchRunning.Clear();
            _batchCounts = new() { ["done"] = 0, ["skipped"] = 0, ["failed"] = 0, ["held"] = 0 };
            _batchKept = 0;
            SaveStatus = ("Starting…", 0.0);
            StatusMsg = ("", "");
            var token = _saveCancel.Token;
            _saver = new Thread(() => RunBatch(jobs, cfg, token)) { IsBackground = true, Name = "save" };
            _saver.Start();
            Changed(true);
            return new JsonObject { ["started"] = jobs.Count };
        }
    }

    public void Cancel()
    {
        lock (Lock)
        {
            if (Busy())
            {
                _saveCancel.Cancel();
                SaveStatus = ("Stopping…", SaveStatus.Frac);
                Changed(false);
            }
        }
    }

    /// <summary>Job k's progress. The status line follows the first file still saving; the bar adds up all of them.</summary>
    private void Overall(int k, double frac, string text)
    {
        _batchRunning[k] = Math.Max(0.0, Math.Min(1.0, frac));
        var n = Math.Max(1, _batchTotal);
        var first = _batchRunning.Keys.First();
        if (k == first)
            SaveStatus = ((_batchTotal > 1 ? $"{k + 1} of {_batchTotal}:  " : "") + text, SaveStatus.Frac);
        SaveStatus = (SaveStatus.Text, (_batchFinished + _batchRunning.Values.Sum()) / n);
        Changed(false);
    }

    /// <summary>A step that must happen in list order (the log, the rename history, the last file saved): straight
    /// away, or when the files before this one are done if two are saving at once.</summary>
    private void InOrder(Action step)
    {
        if (_sink.Value is { } sink)
        {
            lock (sink)
                sink.Add(step);
        }
        else
            step();
    }

    /// <summary>How many files to save at once: 2 when an SSD or two different disks are involved, else 1.</summary>
    private static int SaveSlots(List<Job> jobs)
    {
        if (jobs.Count < 2 || Media.MaxParallelSaves < 2)
            return 1;
        var drives = jobs.Where(j => j.Drive.Length > 0).Select(j => j.Drive).Distinct().Count();
        return jobs.Any(j => j.Ssd) || drives > 1 ? Media.MaxParallelSaves : 1;
    }

    private void RunBatch(List<Job> jobs, BatchCfg cfg, CancellationToken cancel)
    {
        try
        {
            var drives = new Dictionary<string, (string Key, bool Ssd)>();
            foreach (var job in jobs)
            {
                var dir = PyPath.Dir(PyPath.Abs(job.Path));
                if (!drives.TryGetValue(dir, out var d))
                    drives[dir] = d = Media.DriveOf(dir);
                (job.Drive, job.Ssd) = d;
            }
            var slots = SaveSlots(jobs);
            if (slots <= 1)
            {
                for (var k = 0; k < jobs.Count; k++)
                {
                    if (cancel.IsCancellationRequested || RunJob(k, jobs[k], cfg, cancel))
                        break;
                }
            }
            else
                RunTogether(jobs, cfg, cancel, slots);
        }
        finally
        {
            AllDone(cancel.IsCancellationRequested);
        }
    }

    /// <summary>Saves up to `slots` files at once. A file starts in list order once its disk has room (an SSD takes
    /// two, any other disk one) and no file it touches is being saved. Each file's log lines, rename record and
    /// "last saved" are written in list order when the files before it are done, so the log reads as if they had
    /// been saved one by one.</summary>
    private void RunTogether(List<Job> jobs, BatchCfg cfg, CancellationToken cancel, int slots)
    {
        Log($"Saving up to {slots} files at a time");
        var gate = new object();
        var next = 0;
        var flushed = 0;
        var stop = false;
        var running = new List<Job>();
        var finished = new List<object>?[jobs.Count];

        bool Fits(Job j) =>
            running.Count < slots && !running.Any(r => r.Touched.Overlaps(j.Touched))
            && running.Count(r => r.Drive == j.Drive) < (j.Ssd ? 2 : 1);

        void Worker()
        {
            while (true)
            {
                int k;
                lock (gate)
                {
                    while (true)
                    {
                        if (stop || cancel.IsCancellationRequested || next >= jobs.Count)
                            return;
                        if (Fits(jobs[next]))
                            break;
                        Monitor.Wait(gate, 250);
                    }
                    k = next++;
                    running.Add(jobs[k]);
                }
                var sink = new List<object>();
                _sink.Value = sink;
                var halt = true;
                try
                {
                    halt = RunJob(k, jobs[k], cfg, cancel);
                }
                finally
                {
                    _sink.Value = null;
                    lock (gate)
                    {
                        running.Remove(jobs[k]);
                        stop |= halt;
                        finished[k] = sink;
                        for (; flushed < jobs.Count && finished[flushed] is { } done; flushed++)
                        {
                            finished[flushed] = null;
                            foreach (var step in done)
                            {
                                if (step is string line)
                                    WriteLog(line);
                                else
                                    ((Action)step)();
                            }
                        }
                        Monitor.PulseAll(gate);
                    }
                }
            }
        }

        var threads = Enumerable.Range(0, slots).Select(i => new Thread(Worker) { IsBackground = true, Name = $"save {i + 1}" }).ToList();
        threads.ForEach(t => t.Start());
        threads.ForEach(t => t.Join());
    }

    /// <summary>Saves job k. True when the batch should stop (cancelled).</summary>
    private bool RunJob(int k, Job job, BatchCfg cfg, CancellationToken cancel)
    {
        var uid = job.Uid;
        lock (Lock)
        {
            var it0 = Item(uid);
            if (it0 != null)
            {
                it0.State = "saving";
                it0.Message = "Starting…";
                it0.Progress = -1;
            }
            Overall(k, 0.0, it0?.Name ?? "");
        }
        var prog = Throttled((f, m) =>
        {
            lock (Lock)
            {
                var x = Item(uid);
                if (x == null || !_batchRunning.ContainsKey(k))
                    return;
                var pct = f >= 0 ? $" {(int)(f * 100)}%" : "";
                x.Message = $"{m}{pct}";
                x.Progress = f;
                Overall(k, f >= 0 ? f : 0.0, $"{x.Name}, {m.ToLowerInvariant()}{pct}");
            }
        });
        try
        {
            SaveOne(job, cfg, cancel, prog);
            return false;
        }
        catch (Cancelled)
        {
            JobFailed(uid, CancelledMark);
            return true;
        }
        catch (OperationCanceledException)
        {
            JobFailed(uid, CancelledMark);
            return true;
        }
        catch (Exception e)
        {
            JobFailed(uid, e.Message.Length > 0 ? e.Message : e.GetType().Name);
            return false;
        }
        finally
        {
            lock (Lock)
            {
                _batchRunning.Remove(k);
                _batchFinished++;
            }
        }
    }

    private void SaveOne(Job job, BatchCfg cfg, CancellationToken cancel, Action<double, string> prog)
    {
        var uid = job.Uid;
        var it = new Item(job.Path, job.Kind)
        {
            Uid = uid, Info = job.Info, Docs = job.Docs, SdhCounts = job.SdhCounts, Sel = job.Sel, Manual = job.Manual,
            Opts = job.Opts, Overrides = new(job.Overrides), Sidecars = [.. job.Sidecars], Dropped = [.. job.Dropped],
        };
        var fresh = it.Info == null || (it.Kind == "video" && it.Docs.Count == 0
                                        && (it.Info.Subs.Any(t => t.TextBased) || Media.FindSidecars(it.Path).Count > 0));
        if (fresh)
            BatchLoad(it, uid, prog, cancel);
        if (!it.Manual || it.Sel is not { } s || !it.Docs.ContainsKey(s))
            it.Sel = Batch.PickTrack(it, cfg.Pref);
        var track = it.Track();
        var tidy = TidyOpts(it, job, cfg);
        if (track == null || it.Sel is not { } sel || !it.Docs.ContainsKey(sel))
        {
            TidyOrSkip(it, uid, tidy, "no text subtitles", prog, cfg, cancel);
            return;
        }
        if (!job.Force && !Batch.AutoCleaned(it, track))
        {
            TidyOrSkip(it, uid, tidy, "no SDH subtitles (regular subtitles only)", prog, cfg, cancel);
            return;
        }
        var res = Batch.CleanItem(it, cfg.Opts)!;
        var unresolved = res.Unresolved();
        if (unresolved.Count > 0)
        {
            if (job.Policy == "hold" || (fresh && job.Policy == "hold_new"))
            {
                JobHeld(uid, $"{unresolved.Count} lines to check");
                return;
            }
            if (job.Policy == "orig")
            {
                foreach (var i in unresolved)
                    res.Overrides[i] = "orig";
            }
        }
        var n = FinalChanged(res);
        if (!Enumerable.Range(0, res.Doc.Cues.Count).Any(i => res.Final(i) != null))
        {
            TidyOrSkip(it, uid, tidy, res.Doc.Cues.Count == 0 ? "this subtitle track is empty"
                           : "every line would be removed, so the subtitles were left alone", prog, cfg, cancel);
            return;
        }
        if (n == 0 && !job.Force && track.External.Length == 0 && !Batch.PlainToDefault(it, track))
        {
            TidyOrSkip(it, uid, tidy, "already clean", prog, cfg, cancel);
            return;
        }
        var mode = it.Kind == "video" ? cfg.VideoMode : cfg.SubMode;
        var note = "";
        if (it.Kind == "subtitle" && mode == "embed" && string.IsNullOrEmpty(it.Info!.Companion))
            (mode, note) = ("new", " (no matching video, so saved as a subtitle file)");
        var keep = cfg.Keep && !Batch.PlainPick(it, track);
        var opts = new SaveOptions
        {
            Mode = mode, KeepOriginal = keep, MakeDefault = cfg.Default, TrackName = cfg.Name, Language = track.Language,
            OverwriteSidecar = cfg.Overwrite, RecycleSourceSub = cfg.Recycle,
            RecyclePaths = cfg.Recycle && it.Kind == "video" ? Batch.RemovableSidecars(it, cfg.Keep, mode) : [],
        };
        if (tidy != null)
        {
            opts.DropOtherLanguages = tidy.DropOtherLanguages;
            opts.KeepLanguage = tidy.KeepLanguage;
            opts.RenameTo = tidy.RenameTo;
            opts.RenameSidecars = tidy.RenameSidecars;
        }
        var outp = Media.Save(Tools, it.Info!, track, res.Render(), res.Doc.Fmt, opts, prog, cancel, Log);
        RecordRename(it, outp.Path, cfg);
        JobDone(uid, outp.Path, outp.Note + note, n, it.Sel);
    }

    private static SaveOptions? TidyOpts(Item it, Job job, BatchCfg cfg)
    {
        if (it.Kind != "video" || cfg.VideoMode != "replace")
            return null;
        var nw = job.NewName ?? "";
        var drop = cfg.DropLangs && Batch.OtherLanguages(it, cfg.KeepLang).Count > 0;
        if (nw.Length == 0 && !drop)
            return null;
        var side = it.Sidecars.Where(t => t.External.Length > 0).Select(t => t.External).ToList();
        if (side.Count == 0)
            side = Media.FindSidecars(it.Path);
        return new SaveOptions
        {
            Mode = "replace", DropOtherLanguages = drop, KeepLanguage = cfg.KeepLang, RenameTo = nw, RenameSidecars = side,
            MakeDefault = cfg.Default, TrackName = cfg.Name,
        };
    }

    private void TidyOrSkip(Item it, int uid, SaveOptions? tidy, string why, Action<double, string> prog, BatchCfg cfg,
                            CancellationToken cancel)
    {
        if (tidy == null)
        {
            lock (Lock)
            {
                _batchCounts["skipped"]++;
                var x = Item(uid);
                if (x != null)
                {
                    x.State = "skipped";
                    x.Message = why;
                    Log($"Skipped {x.Path}: {why}");
                }
                Changed(false);
            }
            return;
        }
        var outp = Media.TidyOrRename(Tools, it.Info!, tidy, prog, cancel, Log);
        RecordRename(it, outp.Path, cfg);
        JobDone(uid, outp.Path, outp.Note, 0, it.Sel, "tidy");
    }

    private void RecordRename(Item it, string newPath, BatchCfg cfg)
    {
        if (PyPath.NormCase(newPath) != PyPath.NormCase(it.Path))
        {
            var old = it.Path;
            InOrder(() => RenameHistory.Record(cfg.BatchId, old, newPath));
        }
    }

    private void BatchLoad(Item it, int uid, Action<double, string> prog, CancellationToken cancel)
    {
        Dictionary<int, string>? errors = null;
        string? tmpdir = null;
        if (it.Kind == "subtitle")
        {
            var (info, doc) = Media.ProbeSubtitleFile(it.Path);
            it.Info = info;
            it.Docs = new Dictionary<int, SubtitleDoc> { [0] = doc };
        }
        else
        {
            prog(-1, "Opening");
            var reader = ReadCache.Reader(Tools);
            var cached = ReadCache.Get(it.Path, reader);
            var probe = cached?.Probe ?? Media.ProbeText(Tools, it.Path, Log);
            it.Info = Media.ParseProbe(it.Path, probe);
            tmpdir = Directory.CreateTempSubdirectory("subscrub_").FullName;
            var paths = cached != null ? Restore(cached, it.Info, tmpdir)
                : Media.ExtractSubs(Tools, it.Info, it.Info.Subs, tmpdir, (f, _) => prog(f, "Reading subtitles"), cancel, Log);
            if (cached == null)
                ReadCache.Put(it.Path, reader, probe, paths);
            (it.Docs, errors) = LoadDocs(it.Info, paths);
            Batch.AttachSidecars(it, Log);
        }
        Batch.Measure(it);
        lock (Lock)
        {
            var x = Item(uid);
            if (x == null)
                return;
            x.Info = it.Info;
            x.Docs = it.Docs;
            x.Sidecars = it.Sidecars;
            x.SdhCounts = it.SdhCounts;
            x.DocErrors = errors ?? [];
            if (tmpdir != null)
                x.Tmpdir = tmpdir;
            if (!x.Manual || x.Sel is not { } s || !x.Docs.ContainsKey(s))
                x.Sel = Batch.PickTrack(x, Pref);
            ComputeResult(x);
        }
    }

    private void JobDone(int uid, string path, string note, int changed, int? sel, string text = "")
    {
        lock (Lock)
        {
            _batchCounts["done"]++;
            _batchKept += note.Split(Media.Kept).Length - 1;
            var it = Item(uid);
            if (it == null)
                return;
            it.State = "done";
            it.Output = path;
            it.Message = note;
            it.DoneText = DoneText(it, path, note, changed);
            it.Sel = sel;
            it.Cleaned = changed;
            it.Progress = -1;
            InOrder(() =>
            {
                lock (Lock)
                    LastOutput = path;
            });
            if (it.Kind == "video" && PyPath.NormCase(path) != PyPath.NormCase(it.Path) && Cfg.VideoMode == "replace")
                it.Path = path;
            if (uid != ReviewUid)
                Release(it);                             // saved and not on screen: its tracks are packed away
            Log($"Saved {path}: {changed} lines cleaned. {note}");
            Changed(false);
        }
    }

    private static string DoneText(Item it, string path, string note, int changed)
    {
        var bits = new List<string>();
        if (changed != 0)
            bits.Add("Cleaned track saved into the video");
        if (PyPath.NormCase(path) != PyPath.NormCase(it.Path))
            bits.Add($"renamed from {PyPath.Name(it.Path)}");
        if (note.Contains("Removed") && note.Contains("other languages"))
            bits.Add("other languages removed");
        return Cap(string.Join(", ", bits));
    }

    private void JobHeld(int uid, string why)
    {
        lock (Lock)
        {
            _batchCounts["held"]++;
            var it = Item(uid);
            if (it != null)
            {
                it.State = "ready";
                it.Message = "";
                Log($"Held for review {it.Path}: {why}");
            }
            Changed(false);
        }
    }

    private void JobFailed(int uid, string msg)
    {
        lock (Lock)
        {
            var it = Item(uid);
            if (msg == CancelledMark)
            {
                if (it != null)
                {
                    it.State = it.Info != null ? "ready" : "queued";
                    it.Message = "";
                }
                Changed(false);
                return;
            }
            _batchCounts["failed"]++;
            if (it != null)
            {
                it.State = "failed";
                it.Message = msg;
                Log($"Failed {it.Path}: {msg}");
            }
            Changed(false);
        }
    }

    private void AllDone(bool cancelled)
    {
        JsonObject done;
        lock (Lock)
        {
            _saving = false;
            _saver = null;
            var c = _batchCounts;
            var parts = new List<string>();
            if (c["done"] != 0)
                parts.Add($"{c["done"]} saved");
            if (c["held"] != 0)
                parts.Add($"{c["held"]} waiting for you to check");
            if (c["skipped"] != 0)
                parts.Add($"{c["skipped"]} skipped");
            if (c["failed"] != 0)
                parts.Add($"{c["failed"]} failed");
            var summary = parts.Count > 0 ? string.Join(", ", parts) : "nothing saved";
            var kept = _batchKept != 0
                ? $" Kept {PyFmt.Plural(_batchKept, "old subtitle file")} the Recycle Bin couldn't take: hover a file to see where." : "";
            JsonObject? tell = null;
            if (cancelled)
                StatusMsg = ($"Stopped: {summary}.{kept}", c["failed"] != 0 ? "bad" : "");
            else if (c["failed"] != 0)
                StatusMsg = ($"Finished with problems: {summary}. Hover a file to see why.{kept}", "bad");
            else
            {
                var single = _batchTotal == 1 && c["done"] == 1;
                var it = single ? Items.FirstOrDefault(i => i.State == "done" && i.Output == LastOutput) : null;
                StatusMsg = (it != null ? Py.Strip($"Saved “{PyPath.Name(it.Output!)}”. {it.Message}") : $"Done: {summary}.{kept}",
                             c["done"] != 0 ? "good" : "");
            }
            if (c["failed"] != 0 && _batchTotal == 1)
            {
                var it = Items.FirstOrDefault(i => i.State == "failed");
                if (it != null)
                    tell = new JsonObject { ["title"] = "Couldn't save", ["text"] = PyFmt.Head(it.Message, 1200), ["log"] = true };
            }
            PumpLoader();
            PumpNames();
            Changed(true);
            done = new JsonObject
            {
                ["cancelled"] = cancelled,
                ["counts"] = new JsonObject(c.Select(kv => KeyValuePair.Create(kv.Key, (JsonNode?)kv.Value))),
                ["status"] = StatusMsg.Text, ["tell"] = tell,
                ["outputs"] = new JsonArray(Items.Where(i => i.State == "done" && !string.IsNullOrEmpty(i.Output))
                    .Select(i => (JsonNode)i.Output!).ToArray()),
            };
        }
        Emit("save_done", done);
        // the batch's working memory (tool output, the saved files' tracks before they were packed) goes back to
        // Windows now rather than whenever the runtime gets round to it
        GC.Collect(GC.MaxGeneration, GCCollectionMode.Aggressive, blocking: true, compacting: true);
    }

    public void OpenFolder(string? path = null)
    {
        var p = path ?? LastOutput;
        if (!string.IsNullOrEmpty(p))
            Media.OpenInExplorer(p);
    }

    // ================================================================ undoing renames, unfinished copies

    public JsonObject? UndoRenamesQuestion()
    {
        var rows = RenameHistory.LastBatch();
        if (rows.Count == 0)
            return null;
        return new JsonObject
        {
            ["title"] = "Undo the last renames?",
            ["text"] = $"Put the original names back on {PyFmt.Plural(rows.Count, "file")} from {rows[0].Time}?",
            ["detail"] = "Only the names change back. Removed subtitle tracks and originals in the Recycle Bin stay as they are.",
            ["buttons"] = new JsonArray(Button("cancel", "Cancel"), Button("go", "Undo Renames", "primary")),
        };
    }

    public JsonObject UndoRenames()
    {
        lock (Lock)
        {
            if (Busy())
                throw new InvalidOperationException("Wait for the save to finish.");
            var rows = RenameHistory.LastBatch();
            var done = 0;
            var problems = new List<string>();
            foreach (var r in rows)
            {
                try
                {
                    if (!File.Exists(r.New))
                        problems.Add($"“{PyPath.Name(r.New)}” isn't there any more");
                    else if (Path.Exists(r.Old))
                        problems.Add($"“{PyPath.Name(r.Old)}” already exists");
                    else
                    {
                        var sides = Media.FindSidecars(r.New);
                        File.Move(r.New, r.Old);
                        Media.RenameSidecars(r.New, r.Old, sides, Log);
                        done++;
                        foreach (var it in Items)
                        {
                            if (PyPath.NormCase(it.Path) == PyPath.NormCase(r.New))
                                it.Path = r.Old;
                        }
                    }
                }
                catch (Exception e) when (e is IOException or UnauthorizedAccessException)
                {
                    problems.Add($"“{PyPath.Name(r.New)}”: {e.Message}");
                }
            }
            if (rows.Count > 0 && problems.Count == 0)
                RenameHistory.MarkUndone(rows[0].Batch);
            Log($"Undid {done} rename(s)");
            Changed(false);
            if (problems.Count > 0)
            {
                return new JsonObject
                {
                    ["title"] = "Some names couldn't be put back", ["text"] = $"{PyFmt.Plural(done, "file")} renamed back. Not changed:",
                    ["rows"] = Rows(problems.Take(8).Select(p => (p, ""))),
                };
            }
            return new JsonObject { ["title"] = "Renames undone", ["text"] = $"{PyFmt.Plural(done, "file")} renamed back." };
        }
    }

    /// <summary>"remove": unfinished copies (with their original) go to the Recycle Bin. "restore": a copy whose
    /// original is missing gets the original name back.</summary>
    public JsonObject LeftoversAction(List<string> paths, string action, List<string>? which = null)
    {
        var failed = new JsonArray();
        foreach (var lo in Media.FindLeftovers(paths))
        {
            if (which != null && !which.Contains(lo.Path))
                continue;
            try
            {
                if (action == "remove" && !string.IsNullOrEmpty(lo.Original))
                {
                    if (Media.Trash(lo.Path, Log) is { } why)
                    {
                        failed.Add(new JsonArray(lo.Name, PyFmt.Head($"Kept: {why}", 60)));
                        continue;
                    }
                    Log($"Moved the unfinished copy {lo.Path} to the Recycle Bin");
                }
                else if (action == "restore" && string.IsNullOrEmpty(lo.Original))
                {
                    var nw = Media.RestoreLeftover(lo);
                    Log($"Renamed {lo.Path} back to {nw}");
                }
            }
            catch (Exception e)
            {
                failed.Add(new JsonArray(lo.Name, PyFmt.Head(e.Message, 60)));
            }
        }
        return new JsonObject { ["failed"] = failed };
    }
}
