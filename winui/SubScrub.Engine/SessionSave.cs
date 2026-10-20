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
}
