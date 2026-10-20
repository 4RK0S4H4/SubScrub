using System.Text.Json;
using System.Text.Json.Nodes;
using SubScrub.Engine;

// session <cases.json> <out.json>
//   cases: {"files": {...}, "sessions": [{"name", "settings", "state", "script"}]} from tools/engine_parity.py
//   out:   {"files": {...}, "sessions": [[one result per action]]}, the shape the Python side writes
internal static class SessionParity
{
    private static Dictionary<string, List<string>> Locks = [];

    public static void Run(string casesPath, string outPath)
    {
        var cases = JsonNode.Parse(File.ReadAllText(casesPath))!;
        var output = new JsonObject { ["files"] = Files(cases["files"]!) };
        var sessions = new JsonArray();
        foreach (var c in cases["sessions"]!.AsArray())
        {
            var s = Load(c!);
            var results = new JsonArray();
            foreach (var op in c["script"]!.AsArray())
                results.Add(RunOp(s, op!.AsArray()));
            sessions.Add(results);
        }
        output["sessions"] = sessions;
        if (cases["reading"] is JsonObject reading)
            output["reading"] = Reading(reading);
        File.WriteAllText(outPath, output.ToJsonString(Session.JsonOut));
    }

    // ================================================================ the state Python wrote

    private static string? Str(JsonNode? n) => n is JsonValue v && v.TryGetValue<string>(out var s) ? s : null;

    private static int? Int(JsonNode? n) => n is JsonValue v && v.TryGetValue<int>(out var i) ? i : null;

    private static bool Bool(JsonNode? n) => n is JsonValue v && v.TryGetValue<bool>(out var b) && b;

    private static SubTrack Track(JsonNode n) => new()
    {
        Index = Int(n["index"])!.Value, SubPos = Int(n["sub_pos"])!.Value, Codec = Str(n["codec"]) ?? "",
        Language = Str(n["language"]) ?? "", Title = Str(n["title"]) ?? "", Default = Bool(n["default"]),
        Forced = Bool(n["forced"]), HearingImpaired = Bool(n["hearing_impaired"]),
        Disposition = n["disposition"]!.AsObject().ToDictionary(kv => kv.Key, kv => kv.Value is JsonValue v && v.TryGetValue<bool>(out var b) ? (b ? 1 : 0) : Int(kv.Value) ?? 0),
        External = Str(n["external"]) ?? "", Cleaned = Bool(n["cleaned"]),
    };

    private static MediaInfo? Info(JsonNode? n)
    {
        if (n == null)
            return null;
        return new MediaInfo
        {
            Path = Str(n["path"])!, Kind = Str(n["kind"])!, FormatName = Str(n["format_name"]) ?? "",
            Duration = n["duration"]!.GetValue<double>(), Size = n["size"]!.GetValue<long>(),
            Streams = n["streams"]!.AsArray().Select(x => x!.DeepClone().AsObject()).ToList(),
            Subs = n["subs"]!.AsArray().Select(x => Track(x!)).ToList(), Companion = Str(n["companion"]),
            Chapters = Int(n["chapters"]) ?? 0,
        };
    }

    private static List<int> Ints(JsonNode? n) => n?.AsArray().Select(x => x!.GetValue<int>()).ToList() ?? [];

    private static Choice Choice(JsonNode n) => new()
    {
        Kind = Str(n["kind"])!, TmdbId = Int(n["tmdb_id"])!.Value, Title = Str(n["title"]) ?? "", Year = Int(n["year"]),
        Season = Int(n["season"]), Episodes = Ints(n["episodes"]),
        EpisodeTitles = n["episode_titles"]!.AsArray().Select(x => Str(x) ?? "").ToList(), Runtime = Int(n["runtime"]),
        Score = n["score"]!.GetValue<double>(), Why = n["why"]!.AsArray().Select(x => Str(x) ?? "").ToList(),
    };

    private static TitleMatch? Match(JsonNode? n)
    {
        if (n == null)
            return null;
        var p = n["parsed"]!;
        return new TitleMatch
        {
            Parsed = new Parsed
            {
                Title = Str(p["title"]) ?? "", Year = Int(p["year"]), Season = Int(p["season"]), Episodes = Ints(p["episodes"]),
                EpisodeTitle = Str(p["episode_title"]) ?? "",
            },
            Choices = n["choices"]!.AsArray().Select(x => Choice(x!)).ToList(), Confident = Bool(n["confident"]),
            Note = Str(n["note"]) ?? "",
        };
    }

    private static Session Load(JsonNode c)
    {
        var s = new Session(new FileStore(Str(c["settings"])!));
        var st = c["state"]!;
        var t = st["tools"]!;
        s.Tools = new Tools { Ffmpeg = Str(t["ffmpeg"]), Ffprobe = Str(t["ffprobe"]), Mkvmerge = Str(t["mkvmerge"]) };
        if (st["pref"] is JsonObject pref)
            s.Pref = new TrackPref { Language = Str(pref["language"]) ?? "", Title = Str(pref["title"]) ?? "", Sdh = Bool(pref["sdh"]) };
        s.ReviewUid = Int(st["review_uid"]);
        s.AddNote = Str(st["add_note"]) ?? "";
        s.StatusMsg = (Str(st["status_msg"]![0]) ?? "", Str(st["status_msg"]![1]) ?? "");
        s.LastOutput = Str(st["last_output"]);
        s.LocksBanner = Str(st["locks_banner"]) ?? "";
        s.WhoHasOpen = paths => Locks.Where(kv => paths.Contains(kv.Key)).ToDictionary(kv => kv.Key, kv => kv.Value.ToList());
        foreach (var n in st["items"]!.AsArray())
        {
            var it = new Item(Str(n!["path"])!, Str(n["kind"])!)
            {
                Uid = Int(n["uid"])!.Value, State = Str(n["state"])!, Info = Info(n["info"]),
                DocErrors = n["doc_errors"]!.AsObject().ToDictionary(kv => int.Parse(kv.Key), kv => Str(kv.Value) ?? ""),
                SdhCounts = n["sdh_counts"]!.AsObject().ToDictionary(kv => int.Parse(kv.Key), kv => kv.Value!.GetValue<int>()),
                Sel = Int(n["sel"]), Manual = Bool(n["manual"]),
                Opts = n["opts"] is JsonObject o ? CleanOptions.FromDict(o.ToDictionary(kv => kv.Key, kv => kv.Value!.GetValue<bool>())) : null,
                Overrides = n["overrides"]!.AsObject().ToDictionary(kv => int.Parse(kv.Key), kv => Str(kv.Value)!),
                Reviewed = Bool(n["reviewed"]), Force = Bool(n["force"]),
                Sidecars = n["sidecars"]!.AsArray().Select(x => Track(x!)).ToList(),
                Dropped = n["dropped"]!.AsArray().Select(x => Str(x)!).ToHashSet(),
                Locked = n["locked"]!.AsObject().ToDictionary(kv => kv.Key, kv => kv.Value!.AsArray().Select(x => Str(x)!).ToList()),
                Tmpdir = Str(n["tmpdir"]), Progress = n["progress"]!.GetValue<double>(), Message = Str(n["message"]) ?? "",
                Output = Str(n["output"]), Cleaned = Int(n["cleaned"]) ?? 0, Match = Match(n["match"]),
                NameState = Str(n["name_state"]) ?? "", NameNote = Str(n["name_note"]) ?? "", DoneText = Str(n["done_text"]) ?? "",
            };
            Item.SeenUid(it.Uid);
            foreach (var (k, d) in n["docs"]!.AsObject())
                it.Docs[int.Parse(k)] = SubFormats.Parse(Str(d!["text"])!, Str(d["fmt"])!);
            it.NamePick = n["name_pick"] switch
            {
                JsonObject p => Choice(p["choice"]!),
                JsonValue v when Str(v) == "keep" => "keep",
                _ => null,
            };
            if (Bool(n["has_result"]))
                s.ComputeResult(it);
            s.Items.Add(it);
        }
        return s;
    }

    // ================================================================ the script

    private static readonly HashSet<string> StopAnswers = ["cancel", "review", "pick", "again"];

    private static JsonArray Walk(Session s, int? only, JsonObject answers, JsonObject locks)
    {
        Locks = LocksOf(locks);
        var got = new JsonArray();
        var a = new JsonObject();
        for (var k = 0; k < 12; k++)
        {
            var q = s.SaveQuestion(only, (JsonObject)a.DeepClone());
            got.Add(q);
            if (q["ask"] is not JsonObject ask)
                break;
            var key = Str(ask["key"])!;
            var ans = Str(answers[key]);
            if (ans == null || StopAnswers.Contains(ans))
                break;
            a[key] = ans;
        }
        Locks = [];
        return got;
    }

    private static Dictionary<string, List<string>> LocksOf(JsonNode? n) =>
        n?.AsObject().ToDictionary(kv => kv.Key, kv => kv.Value!.AsArray().Select(x => Str(x)!).ToList()) ?? [];

    private static JsonNode? RunOp(Session s, JsonArray op)
    {
        var kind = Str(op[0])!;
        try
        {
            switch (kind)
            {
                case "files":
                    return s.FilesView();
                case "batch":
                    return BatchProbe(s, s.Item(Int(op[1]))!);
                case "review":
                    return s.Review(Int(op[1])!.Value, Str(op[2]), Bool(op[3]));
                case "toggle":
                    return s.ToggleLine(Int(op[1])!.Value, Int(op[2])!.Value);
                case "select":
                    return s.SelectTrack(Int(op[1])!.Value, Int(op[2])!.Value, Str(op[3]));
                case "force":
                    return s.ForceClean(Int(op[1])!.Value);
                case "set_rules":
                    return s.SetRules(Int(op[1])!.Value, op[2]!.AsObject(), Str(op[3]) ?? "", Str(op[4]));
                case "reset_rules":
                    return s.ResetRules(Int(op[1])!.Value, Str(op[2]));
                case "promote":
                    return s.PromoteRules(Int(op[1])!.Value, Str(op[2]));
                case "settings":
                    return s.SetSettings(op[1]!.AsObject());
                case "save_walk":
                    return Walk(s, Int(op[1]), op[2]!.AsObject(), op[3]!.AsObject());
                case "locks":
                    Locks = LocksOf(op[1]);
                    try
                    {
                        return s.CheckLocks();
                    }
                    finally
                    {
                        Locks = [];
                    }
                case "name_choices":
                    return s.NameChoices(Int(op[1])!.Value);
                case "name_pick":
                    return s.NamePick(Int(op[1])!.Value, Str(op[2])!);
                case "poke":
                    Poke(s.Item(Int(op[1]))!, op[2]!.AsObject());
                    return new JsonObject();
                case "busy":
                    s.SetSaving(Bool(op[1]));
                    s.SaveStatus = (Str(op[2]) ?? "", op[3]!.GetValue<double>());
                    return new JsonObject();
            }
        }
        catch (InvalidOperationException e)
        {
            return new JsonObject { ["error"] = e.Message };
        }
        throw new ArgumentException(kind);
    }

    private static void Poke(Item it, JsonObject fields)
    {
        foreach (var (k, v) in fields)
        {
            switch (k)
            {
                case "state": it.State = Str(v)!; break;
                case "progress": it.Progress = v!.GetValue<double>(); break;
                case "message": it.Message = Str(v) ?? ""; break;
                case "output": it.Output = Str(v); break;
                case "cleaned": it.Cleaned = Int(v) ?? 0; break;
                case "done_text": it.DoneText = Str(v) ?? ""; break;
                case "name_state": it.NameState = Str(v) ?? ""; break;
                case "name_note": it.NameNote = Str(v) ?? ""; break;
                default: throw new ArgumentException(k);
            }
        }
    }

    private static JsonArray Arr<T>(IEnumerable<T> xs) => new(xs.Select(x => JsonValue.Create(x) as JsonNode).ToArray());

    private static JsonObject BatchProbe(Session s, Item it)
    {
        var tracks = it.Tracks();
        Batch.Measure(it);
        var nc = Batch.NameChoice(it);
        var outp = new JsonObject
        {
            ["kind_for"] = Batch.KindFor(it.Path), ["sdh_counts"] = new JsonObject(it.SdhCounts.Select(kv => KeyValuePair.Create(kv.Key.ToString(), (JsonNode?)kv.Value))),
            ["pick"] = Batch.PickTrack(it, null), ["pick_pref"] = Arr(tracks.Select(t => Batch.PickTrack(it, Batch.PrefFrom(t)))),
            ["plain_partner"] = Batch.PlainPartner(it), ["other_eng"] = Arr(Batch.OtherLanguages(it).Select(t => t.Index)),
            ["other_spa"] = Arr(Batch.OtherLanguages(it, "spa").Select(t => t.Index)),
            ["new_name"] = Batch.NewName(it), ["new_name_year"] = Batch.NewName(it, true),
            ["name_choice"] = nc?.Label(), ["name_unsure"] = Batch.NameUnsure(it), ["files_touched"] = Arr(s.FilesTouched(it)),
        };
        var tr = new JsonArray();
        foreach (var t in tracks)
        {
            tr.Add(new JsonObject
            {
                ["index"] = t.Index, ["is_sdh"] = Batch.IsSdh(it, t), ["plain_pick"] = Batch.PlainPick(it, t),
                ["auto"] = Batch.AutoCleaned(it, t), ["to_default"] = Batch.PlainToDefault(it, t),
                ["label"] = Session.TrackLabel(it, t), ["sdh_flagged"] = t.SdhFlagged, ["text_based"] = t.TextBased,
                ["fmt"] = t.Fmt, ["codec_label"] = t.CodecLabel, ["lang_label"] = t.LangLabel, ["from_subscrub"] = t.FromSubscrub,
                ["dispo"] = Arr([t.DispoFlags(false), t.DispoFlags(true)]), ["default_name"] = Media.DefaultTrackName(t),
            });
        }
        outp["tracks"] = tr;
        var rem = new JsonObject();
        foreach (var keep in new[] { false, true })
        {
            foreach (var mode in new[] { "replace", "new", "sidecar" })
                rem[$"{(keep ? "True" : "False")}/{mode}"] = Arr(Batch.RemovableSidecars(it, keep, mode));
        }
        outp["removable"] = rem;
        var sel = it.Track();
        if (it.Info != null)
        {
            var paths = new JsonObject();
            var modes = it.Kind == "subtitle" ? new[] { "embed", "replace", "new" } : ["replace", "new", "sidecar"];
            foreach (var mode in modes)
            {
                foreach (var lang in new[] { "eng", "hin", "und", "" })
                {
                    var o = new SaveOptions { Mode = mode, Language = lang, RenameTo = Batch.NewName(it) ?? "" };
                    paths[$"{mode}/{lang}"] = Media.OutputPath(it.Info, o, sel != null ? sel.Fmt : "srt");
                }
            }
            outp["output_path"] = paths;
        }
        return outp;
    }

    // ================================================================ reading files

    private static JsonObject TrackJson(SubTrack t) => new()
    {
        ["index"] = t.Index, ["sub_pos"] = t.SubPos, ["codec"] = t.Codec, ["language"] = t.Language, ["title"] = t.Title,
        ["default"] = t.Default, ["forced"] = t.Forced, ["hearing_impaired"] = t.HearingImpaired,
        ["disposition"] = new JsonObject(t.Disposition.Select(kv => KeyValuePair.Create(kv.Key, (JsonNode?)kv.Value))),
        ["external"] = t.External, ["cleaned"] = t.Cleaned,
    };

    private static JsonObject? InfoJson(MediaInfo? i) => i == null ? null : new JsonObject
    {
        ["path"] = i.Path, ["kind"] = i.Kind, ["format_name"] = i.FormatName, ["duration"] = i.Duration, ["size"] = i.Size,
        ["streams"] = new JsonArray(i.Streams.Select(x => (JsonNode)x.DeepClone()).ToArray()),
        ["subs"] = new JsonArray(i.Subs.Select(t => (JsonNode)TrackJson(t)).ToArray()), ["companion"] = i.Companion,
        ["chapters"] = i.Chapters,
    };

    private static JsonObject DocView(SubtitleDoc d) => new()
    {
        ["fmt"] = d.Fmt,
        ["cues"] = new JsonArray(d.Cues.Select(c => (JsonNode)new JsonArray(c.Start, c.End, c.Text)).ToArray()),
        ["render"] = d.Render(d.Cues.Select(c => (string?)c.Text).ToList()),
    };

    private static JsonObject LoadedView(Session s, JsonArray added)
    {
        var pos = s.Items.Select((it, k) => (it.Uid, k)).ToDictionary(x => x.Uid, x => x.k);
        var items = new JsonArray();
        foreach (var it in s.Items)
        {
            items.Add(new JsonObject
            {
                ["path"] = it.Path, ["kind"] = it.Kind, ["state"] = it.State, ["info"] = InfoJson(it.Info),
                ["docs"] = new JsonObject(it.Docs.Select(kv => KeyValuePair.Create(kv.Key.ToString(), (JsonNode?)DocView(kv.Value)))),
                ["doc_errors"] = new JsonObject(it.DocErrors.Select(kv => KeyValuePair.Create(kv.Key.ToString(),
                    (JsonNode?)(kv.Value == "Couldn't read this track." ? kv.Value : "error")))),
                ["sdh_counts"] = new JsonObject(it.SdhCounts.Select(kv => KeyValuePair.Create(kv.Key.ToString(), (JsonNode?)kv.Value))),
                ["sel"] = it.Sel, ["sidecars"] = new JsonArray(it.Sidecars.Select(t => (JsonNode)TrackJson(t)).ToArray()),
                ["dropped"] = Arr(it.Dropped.Order(PyFmt.Order)),
                ["message"] = it.Message.StartsWith("couldn't read it (", StringComparison.Ordinal) ? "couldn't read it" : it.Message,
                ["progress"] = it.Progress, ["has_result"] = it.Result != null, ["tmpdir"] = it.Tmpdir != null,
            });
        }
        var fv = s.FilesView();
        foreach (var r in fv["rows"]!.AsArray())
        {
            r!["uid"] = pos[r["uid"]!.GetValue<int>()];
            if (Str(r["detail"])!.StartsWith("Couldn't read it (", StringComparison.Ordinal))
                r["detail"] = "Couldn't read it";
        }
        fv["review_uid"] = null;
        return new JsonObject { ["added"] = added, ["items"] = items, ["files"] = fv };
    }

    private static JsonObject Reading(JsonObject c)
    {
        var outp = new JsonObject();
        var tools = Media.FindTools();
        outp["tools"] = new JsonObject { ["ffmpeg"] = tools.Ffmpeg, ["ffprobe"] = tools.Ffprobe, ["mkvmerge"] = tools.Mkvmerge };
        var adds = new JsonArray();
        foreach (var a in c["adds"]!.AsArray())
        {
            var s = new Session(new FileStore(Str(a!["settings_file"])!));
            s.Start("parity");
            var added = new JsonArray();
            foreach (var paths in a["adds"]!.AsArray())
            {
                added.Add(s.Add(paths!.AsArray().Select(x => Str(x)!).ToList()));
                if (!s.WaitIdle(600))
                    throw new TimeoutException("the C# session didn't finish reading");
            }
            adds.Add(LoadedView(s, added));
            s.Close();
        }
        outp["adds"] = adds;
        var probe = new JsonArray();
        var k = 0;
        foreach (var v in c["probe"]!.AsArray())
        {
            var info = Media.Probe(tools, Str(v)!);
            var got = Media.ExtractSubs(tools, info, info.Subs, Path.Combine(Str(c["tmp"])!, $"cs{k++}"));
            var files = new JsonObject();
            foreach (var (idx, path) in got)
                files[idx.ToString()] = new System.Text.UTF8Encoding(false).GetString(File.ReadAllBytes(path));
            probe.Add(new JsonObject { ["info"] = InfoJson(info), ["extracted"] = files });
        }
        outp["probe"] = probe;
        outp["lone"] = new JsonArray(c["lone"]!.AsArray().Select(f =>
        {
            var (info, doc) = Media.ProbeSubtitleFile(Str(f)!);
            return (JsonNode)new JsonObject { ["info"] = InfoJson(info), ["doc"] = DocView(doc) };
        }).ToArray());
        outp["leftovers"] = new JsonArray(c["leftovers"]!.AsArray().Select(p => (JsonNode)new JsonArray(
            Media.FindLeftovers(p!.AsArray().Select(x => Str(x)!)).Select(x => (JsonNode)new JsonArray(x.Path, x.Original, x.Size, x.Name)).ToArray())).ToArray());
        outp["restore"] = Media.RestoreLeftover(Media.FindLeftovers([Str(c["restore"])!])[0]);
        return outp;
    }

    // ================================================================ the files themselves

    private static JsonObject Files(JsonNode c)
    {
        var outp = new JsonObject();
        var names = new JsonArray();
        foreach (var n in c["names"]!.AsArray())
        {
            var name = Str(n)!;
            names.Add(new JsonObject
            {
                ["kind"] = Batch.KindFor(name), ["lang"] = Media.GuessLanguage(name),
                ["clean"] = Media.CleanSubtitleName(PyPath.Join(PyPath.IsWin ? "\\x" : "/x", name), "srt"),
                ["container"] = Media.ContainerKind(name),
            });
        }
        outp["names"] = names;
        outp["subs"] = new JsonArray(c["subs"]!.AsArray().Select(n => (JsonNode)new JsonObject
        {
            ["match"] = Media.FindMatchingVideo(Str(n)!), ["lang"] = Media.GuessLanguage(Str(n)!),
        }).ToArray());
        outp["videos"] = new JsonArray(c["videos"]!.AsArray().Select(n => (JsonNode)new JsonObject
        {
            ["sidecars"] = Arr(Media.FindSidecars(Str(n)!)),
        }).ToArray());
        outp["expand"] = new JsonArray(c["expand"]!.AsArray().Select(n =>
        {
            var (files, skipped) = Batch.ExpandPaths(n!.AsArray().Select(x => Str(x)!));
            return (JsonNode)new JsonObject { ["files"] = Arr(files), ["skipped"] = Arr(skipped) };
        }).ToArray());
        outp["pair"] = new JsonArray(c["pair"]!.AsArray().Select(n =>
        {
            var plan = Batch.PairUp(n!["files"]!.AsArray().Select(x => Str(x)!).ToList(),
                                    n["explicit"]!.AsArray().Select(x => Str(x)!), Bool(n["embed"]),
                                    n["known"]!.AsArray().Select(x => Str(x)!));
            return (JsonNode)new JsonObject
            {
                ["videos"] = Arr(plan.Videos), ["subtitles"] = Arr(plan.Subtitles),
                ["dropped_with"] = new JsonObject(plan.DroppedWith.Select(kv => KeyValuePair.Create(kv.Key, (JsonNode?)Arr(kv.Value)))),
                ["orphans"] = Arr(plan.Orphans),
            };
        }).ToArray());
        return outp;
    }
}
