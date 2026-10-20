using System.Runtime.CompilerServices;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace SubScrub.Engine;

public sealed partial class Session
{
    // ================================================================ Review

    public JsonObject Review(int uid, string? show = null, bool first = false)
    {
        lock (Lock)
        {
            var it = Item(uid) ?? throw new InvalidOperationException("That file isn't in the list any more.");
            if (ReviewUid is { } was && was != uid && Item(was) is { } left)
                Release(left);                           // a saved file leaves the screen
            ReviewUid = uid;
            it.Reviewed = true;
            var res = it.Result;
            if (show == null || first)
                show = res != null && res.Unresolved().Count > 0 ? "check"
                    : (Cfg.PreviewFilter != "check" ? Cfg.PreviewFilter : "changes");
            else if (show != "check" && show != Cfg.PreviewFilter)
            {
                Cfg.PreviewFilter = show;
                Cfg.Store.Set("preview_filter", show);
            }
            return ReviewView(it, show);
        }
    }

    private JsonObject Head(Item it)
    {
        var (detail, chip, kind, _) = Verdict(it);
        var n = RowCount(it);
        var text = detail.TrimEnd('.') + (n != 0 && it.State != "done" ? $", {PyFmt.Thousands(n)} lines to clean" : "");
        var notes = it.Result != null ? it.Result.Notes.Notes.ToList() : [];
        if (notes.Count > 0)
            text += ". " + string.Join(" ", notes);
        var tr = it.Track();
        var nosdh = tr != null && it.Loaded && !it.Force && !Batch.AutoCleaned(it, tr);
        var nosdhText = "";
        if (nosdh)
            nosdhText = "This file only has regular subtitles, with no sound descriptions or speaker names to strip. "
                        + (HasTidy(it) ? "Saving only renames it and tidies its tracks; the subtitles stay as they are."
                            : "It's skipped when saving.");
        var (line, tone) = NameLine(it);
        var can = it.Kind == "video" && Renaming() && Cfg.TmdbKey.Length > 0 && it.NameState == "done"
                  && it.Pending && it.Match != null && (it.Match.Choices.Count > 0 || it.Match.Parsed.Title.Length > 0);
        var multi = Items.Count > 1;
        return new JsonObject
        {
            ["uid"] = it.Uid, ["name"] = it.Name, ["path"] = it.Path, ["detail"] = text, ["badge"] = chip,
            ["badge_kind"] = kind, ["nosdh"] = nosdhText, ["name_row"] = can,
            ["name_text"] = can ? (line.Length > 0 ? line : "The name is already right") : "", ["name_tone"] = tone,
            ["multi"] = multi, ["save_one"] = multi && it.Pending && !Busy(),
        };
    }

    private (JsonArray Tiles, string Meta, string Count, string Note) TrackTiles(Item it)
    {
        var info = it.Info;
        if (info == null)
            return ([], it.State == "failed" ? "Couldn't read this file." : "Opening…", "", "");
        List<string> bits;
        if (it.Kind == "subtitle")
        {
            var n = it.Docs.TryGetValue(0, out var d0) ? d0.Cues.Count : 0;
            bits =
            [
                $"{info.FormatName.ToUpperInvariant()} subtitle file", $"{PyFmt.Thousands(n)} lines",
                !string.IsNullOrEmpty(info.Companion) ? $"belongs to “{PyPath.Name(info.Companion)}”" : "no matching video in its folder",
            ];
        }
        else
        {
            bits = [info.Kind != "other" ? info.Kind.ToUpperInvariant() : PyPath.Suffix(info.Path).ToUpperInvariant().TrimStart('.')];
            if (info.Duration != 0)
                bits.Add(FmtDuration(info.Duration));
            bits.Add(FmtSize(info.Size));
        }
        var alltr = it.Tracks();
        var plain = Batch.PlainPartner(it);
        var tiles = new JsonArray();
        foreach (var tr in alltr)
        {
            string stat = "", tone = "";
            var sdh = false;
            if (tr.TextBased && it.State == "reading")
                stat = "Reading…";
            else if (tr.TextBased && it.Docs.ContainsKey(tr.Index))
            {
                var n = it.SdhCounts.GetValueOrDefault(tr.Index);
                sdh = Batch.IsSdh(it, tr);
                if (it.DocErrors.ContainsKey(tr.Index))
                    (stat, tone) = ("Couldn't read", "warn");
                else if (sdh)
                    (stat, tone) = (PyFmt.Plural(n, "sound or name note"), tr.Index == it.Sel ? "accent" : "");
                else if (tr.Index == plain)
                    (stat, tone) = ("Regular, used as a cross-check", "dim");
                else if (tr.Index == it.Sel && Batch.PlainPick(it, tr))
                    (stat, tone) = ("Regular, becomes the default", "accent");
                else
                    (stat, tone) = ("Regular subtitles", "dim");
            }
            else if (!tr.TextBased)
                (stat, tone) = ("Picture-based, can't be edited", "dim");
            var flags = new JsonArray();
            if (tr.Default)
                flags.Add("Default");
            if (tr.Forced)
                flags.Add("Forced");
            if (sdh || tr.SdhFlagged)
                flags.Add("SDH");
            var title = tr.External.Length > 0 ? PyPath.Name(tr.External) : tr.Title;
            if (string.IsNullOrEmpty(title))
                title = tr.LangLabel;
            tiles.Add(new JsonObject
            {
                ["index"] = tr.Index, ["title"] = title, ["language"] = tr.LangLabel, ["codec"] = tr.CodecLabel,
                ["flags"] = flags, ["stat"] = stat, ["tone"] = tone, ["selected"] = tr.Index == it.Sel,
                ["enabled"] = tr.TextBased && !Busy(), ["external"] = tr.External.Length > 0,
            });
        }
        var textTracks = alltr.Where(t => t.TextBased).ToList();
        var note = "";
        if (it.Sidecars.Count > 0)
            note = "Subtitle files next to the video are listed too. Whichever track gets cleaned ends up inside the video"
                   + (Cfg.RecycleSub && Cfg.VideoMode == "replace"
                       ? "; afterwards, files in the same language next to it go to the Recycle Bin so the video is a single file."
                       : ".");
        if (alltr.Count == 0)
            note = "This file has no subtitle tracks, and there are no subtitle files next to it.";
        else if (textTracks.Count == 0)
            note = "All of this file's subtitles are picture-based (Blu-ray PGS or DVD VobSub). They'd need OCR to "
                   + "become editable text, which SubScrub doesn't do yet.";
        else if (Items.Count(i => i.Kind == "video") > 1 && it.Kind == "video" && textTracks.Count > 1 && it.Sidecars.Count == 0)
            note = "Picking a different track here picks the matching track in the other files too.";
        return (tiles, string.Join(", ", bits), alltr.Count > 0 ? PyFmt.Plural(alltr.Count, "track") : "", note);
    }

    private JsonObject RulesView(Item it)
    {
        var o = OptsFor(it);
        var res = it.Result;
        var rows = new JsonArray();
        foreach (var (key, title, example, rules) in Rules)
        {
            int? count = res != null && o.Get(key) ? rules.Sum(r => res.Stats.ByRule.GetValueOrDefault(r)) : null;
            var visible = true;
            if (key == "ocr_fixes")
                visible = res != null && res.Notes.OcrSuspect;
            else if (key == "quote_italics")
                visible = res != null && res.Notes.QuoteVoices;
            else if (key == "mixed_case_labels")
                visible = !(res != null && res.Notes.MixedNames.Count > 0);
            rows.Add(new JsonObject
            {
                ["key"] = key, ["title"] = title, ["example"] = example, ["on"] = o.Get(key), ["count"] = count,
                ["visible"] = visible,
            });
        }
        var nLyr = res != null ? res.Stats.ByRule.GetValueOrDefault("lyrics") + res.Stats.ByRule.GetValueOrDefault("lyric_style") : 0;
        return new JsonObject
        {
            ["rows"] = rows, ["lyrics"] = LyricMode(o), ["lyric_count"] = nLyr, ["custom"] = it.Opts != null,
            ["state"] = it.Opts != null ? "Changed for this file" : "Your defaults",
        };
    }

    private static JsonArray Legend(Item it)
    {
        var res = it.Result!;
        var st = res.Stats;
        var unresolved = res.Unresolved().Count;
        var keptByUser = it.Overrides.Values.Count(v => v == "orig");
        var items = new JsonArray();
        if (unresolved != 0)
            items.Add(new JsonObject { ["kind"] = "check", ["text"] = $"{PyFmt.Thousands(unresolved)} to check" });
        items.Add(new JsonObject { ["kind"] = "removed", ["text"] = $"{PyFmt.Thousands(st.Removed)} removed" });
        items.Add(new JsonObject { ["kind"] = "trim", ["text"] = $"{PyFmt.Thousands(st.Edited)} trimmed" });
        var untouched = $"{PyFmt.Thousands(st.Total - st.Changed)} untouched";
        if (keptByUser != 0)
            untouched += $", {keptByUser} kept by you";
        items.Add(new JsonObject { ["kind"] = "kept", ["text"] = untouched });
        return items;
    }

    private sealed class Prep(List<(double Start, double End)> times, List<string> befores)
    {
        public readonly List<(double Start, double End)> Times = times;
        public readonly List<string> Befores = befores;
    }

    private static readonly ConditionalWeakTable<SubtitleDoc, Prep> PrepCache = new();

    private static Prep Prepare(CleanResult res)
    {
        var doc = res.Doc;
        if (PrepCache.TryGetValue(doc, out var cached))
            return cached;
        var times = new List<(double, double)>();
        var befores = new List<string>();
        foreach (var c in doc.Cues)
        {
            double s = SubFormats.ToSeconds(c.Start), e = SubFormats.ToSeconds(c.End);
            times.Add((s, Math.Max(e, s)));
            befores.Add(Cleaner.VisibleText(c.Text, doc.Fmt));
        }
        var prep = new Prep(times, befores);
        PrepCache.AddOrUpdate(doc, prep);
        return prep;
    }

    private static readonly Regex Italic = new(@"<i>|\\i1", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

    /// <summary>One line of the before/after list: the original with removed parts marked, and what is saved.</summary>
    public JsonObject CueView(Item it, int i)
    {
        var res = it.Result!;
        var prep = Prepare(res);
        var fmt = res.Doc.Fmt;
        var cue = res.Doc.Cues[i];
        var r = res.Results[i];
        var (start, end) = prep.Times[i];
        var before = prep.Befores[i];
        var status = !r.Changed ? "kept" : (r.Text == null ? "removed" : "edited");
        var ovr = res.Overrides.GetValueOrDefault(i);
        var flagged = r.Changed && r.Risk >= 2;
        string? after;
        string final;
        if (!r.Changed || ovr == "orig")
        {
            after = before;
            final = cue.Text;
        }
        else
        {
            after = r.Text == null ? null : Cleaner.VisibleText(r.Text, fmt);
            final = r.Text ?? "";
        }
        // the timeline: unchanged lines have no mark ("none"), lines the user kept are grey ("kept")
        var mark = flagged && ovr == null ? "flagged" : (ovr == "orig" ? "kept" : (status == "kept" ? "none" : status));
        // the original, split into kept and struck-out pieces (the Qt app's diff_html, as data)
        var segs = new List<(string Text, bool Gone)>();
        if (status != "kept" && ovr != "orig")
        {
            if (after == null)
                segs.Add((before, true));
            else
            {
                var a = PyFmt.CodePoints(before);
                var b = PyFmt.CodePoints(after);
                foreach (var (op, a0, a1, _, _) in SequenceMatcher.Opcodes(a, b))
                {
                    var seg = PyFmt.FromCodePoints(a, a0, a1);
                    if (seg.Length == 0)
                        continue;
                    var gone = op is "delete" or "replace" && Py.Strip(seg).Length > 0;
                    if (segs.Count > 0 && segs[^1].Gone == gone)
                        segs[^1] = (segs[^1].Text + seg, gone);
                    else
                        segs.Add((seg, gone));
                }
            }
        }
        else
            segs.Add((before, false));
        var clock = FmtClock(start);
        return new JsonObject
        {
            ["cue"] = i, ["start"] = start, ["end"] = end, ["time"] = clock.Length > 0 ? clock : "0:00", ["status"] = status,
            ["before"] = new JsonArray(segs.Select(s => (JsonNode)new JsonObject { ["text"] = s.Text, ["gone"] = s.Gone }).ToArray()),
            ["after"] = after, ["flagged"] = flagged, ["why"] = r.Changed ? string.Join("; ", r.Why.Take(2)) : "",
            ["override"] = ovr ?? "", ["italic"] = Italic.IsMatch(final), ["mark"] = mark,
        };
    }

    private JsonObject ReviewView(Item it, string show)
    {
        var (tiles, meta, count, note) = TrackTiles(it);
        var head = Head(it);
        var view = new JsonObject
        {
            ["uid"] = it.Uid, ["head"] = head, ["tracks"] = tiles, ["meta"] = meta, ["track_count"] = count,
            ["tracks_note"] = note, ["rules"] = RulesView(it), ["filter"] = show, ["empty"] = "", ["legend"] = new JsonArray(),
            ["rows"] = new JsonArray(), ["marks"] = new JsonArray(), ["duration"] = 0.0, ["check_option"] = false,
            ["nav"] = false, ["preview_only"] = false, ["busy"] = Busy(),
            ["index"] = Math.Max(0, Items.IndexOf(it)), ["total"] = Items.Count,
        };
        var res = it.Result;
        if (it.Info == null || it.State is "queued" or "probing" or "reading")
        {
            view["empty"] = it.State == "failed" ? "Couldn't read this file." : "Reading subtitles…";
            return view;
        }
        var tr = it.Track();
        if (tr == null)
        {
            view["empty"] = "No editable subtitles in this file.";
            return view;
        }
        if (it.DocErrors.TryGetValue(tr.Index, out var err))
        {
            view["empty"] = "SubScrub couldn't read this track.\n" + PyFmt.Head(err, 300);
            return view;
        }
        if (res == null)
        {
            view["empty"] = "Reading subtitles…";
            return view;
        }
        view["legend"] = Legend(it);
        var flagged = res.Flagged().Count;
        view["check_option"] = flagged > 0;
        if (show == "check" && flagged == 0)
        {
            show = "changes";
            view["filter"] = show;
        }
        view["preview_only"] = head["nosdh"]!.GetValue<string>().Length > 0;
        if (res.Stats.Changed == 0 && show != "all")
        {
            view["empty"] = "Nothing to remove in this track. It's already dialogue-only.\nSwitch to Everything to read through it.";
            return view;
        }
        var rows = new List<JsonObject>();
        var marks = new List<(double T, string K)>();
        for (var i = 0; i < res.Results.Count; i++)
        {
            var c = CueView(it, i);
            marks.Add((PyFmt.Round(c["start"]!.GetValue<double>(), 2), c["mark"]!.GetValue<string>()));
            if (show == "changes" && c["status"]!.GetValue<string>() == "kept")
                continue;
            if (show == "check" && !c["flagged"]!.GetValue<bool>())
                continue;
            rows.Add(c);
        }
        // sorted() is stable: equal times keep their order
        rows = rows.OrderBy(x => x["start"]!.GetValue<double>()).ToList();
        marks = marks.OrderBy(m => m.T).ToList();
        view["rows"] = new JsonArray(rows.Select(x => (JsonNode)x).ToArray());
        view["marks"] = new JsonArray(marks.Select(m => (JsonNode)new JsonObject { ["t"] = m.T, ["k"] = m.K }).ToArray());
        view["nav"] = res.Stats.Changed > 0;
        view["duration"] = it.Info.Duration != 0 ? it.Info.Duration : (marks.Count > 0 ? marks[^1].T : 0.0);
        return view;
    }

    public JsonObject SelectTrack(int uid, int index, string? show = null)
    {
        lock (Lock)
        {
            var it = Item(uid);
            if (it == null || Busy())
                throw new InvalidOperationException("Can't change the track right now.");
            if (it.Sel != index)
                it.Overrides = [];
            it.Sel = index;
            it.Manual = true;
            var tr = it.Track();
            if (tr != null && it.Kind == "video")
            {
                Pref = Batch.PrefFrom(tr);
                foreach (var other in Items)
                {
                    if (other == it || other.Manual || !other.Loaded || other.Kind != "video")
                        continue;
                    var nw = Batch.PickTrack(other, Pref);
                    if (nw != other.Sel)
                    {
                        other.Sel = nw;
                        other.Overrides = [];
                        ComputeResult(other);
                    }
                }
            }
            it.Force = tr != null && !Batch.AutoCleaned(it, tr);
            ComputeResult(it);
            Changed(false);
            return Review(uid, show);
        }
    }

    public JsonObject ForceClean(int uid, string? show = null)
    {
        lock (Lock)
        {
            var it = Item(uid);
            if (it == null || Busy())
                throw new InvalidOperationException("Can't change this file right now.");
            it.Force = true;
            if (it.State == "skipped")
            {
                it.State = "ready";
                it.Message = "";
            }
            ComputeResult(it);
            Changed(false);
            return Review(uid, show);
        }
    }

    public JsonObject SetRules(int uid, JsonObject rules, string lyrics, string? show = null)
    {
        lock (Lock)
        {
            var it = Item(uid);
            if (it == null || !it.Loaded || Busy())
                throw new InvalidOperationException("Can't change this file right now.");
            var o = OptsFor(it).With(rules.Where(kv => RuleKeys.Contains(kv.Key)).Select(kv => KeyValuePair.Create(kv.Key, Config.Truthy(kv.Value))));
            o = WithLyricMode(o, string.IsNullOrEmpty(lyrics) ? LyricMode(o) : lyrics);
            it.Opts = o == Cfg.Defaults ? null : o;
            ComputeResult(it);
            Changed(false);
            return Review(uid, show);
        }
    }

    public JsonObject ResetRules(int uid, string? show = null)
    {
        lock (Lock)
        {
            var it = Item(uid) ?? throw new InvalidOperationException("That file isn't in the list any more.");
            it.Opts = null;
            ComputeResult(it);
            Changed(false);
            return Review(uid, show);
        }
    }

    public JsonObject PromoteRules(int uid, string? show = null)
    {
        lock (Lock)
        {
            var it = Item(uid);
            if (it == null || it.Opts == null)
                throw new InvalidOperationException("Nothing to save as the default.");
            Cfg.Defaults = it.Opts;
            it.Opts = null;
            Cfg.Save();
            foreach (var other in Items)
            {
                if (other.Opts == null && other.Loaded)
                    ComputeResult(other);
            }
            StatusMsg = ("Saved as your default edits for every file.", "good");
            Changed(false);
            return Review(uid, show);
        }
    }

    /// <summary>Keep the original line, or use the cleaned one again. Returns just what changed: the row, its
    /// timeline mark, the legend and the header (the window updates that one row instead of rebuilding the list).</summary>
    public JsonObject ToggleLine(int uid, int cue)
    {
        lock (Lock)
        {
            var it = Item(uid);
            if (it == null || it.Result == null || Busy())
                throw new InvalidOperationException("Can't change this line right now.");
            var r = it.Result.Results[cue];
            if (r.Changed)
            {
                if (it.Overrides.GetValueOrDefault(cue) == "orig")
                {
                    if (r.Risk >= 2)
                        it.Overrides[cue] = "clean";          // an explicit "yes, clean it" resolves the flag
                    else
                        it.Overrides.Remove(cue);
                }
                else
                    it.Overrides[cue] = "orig";
                it.Result.Overrides = it.Overrides;
            }
            Changed(false);
            return new JsonObject
            {
                ["row"] = CueView(it, cue), ["legend"] = Legend(it), ["head"] = Head(it), ["rules"] = RulesView(it),
            };
        }
    }
}
