using System.Globalization;
using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace SubScrub.Engine;

/// <summary>The Recycle Bin refused (Code: the Windows error, 0 when unknown).</summary>
public sealed class RecycleError(string message, int code = 0) : IOException(message)
{
    public int Code { get; } = code;

    /// <summary>Another program has the file open (sharing or lock violation, access denied).</summary>
    public bool InUse => Code is 5 or 32 or 33;
}

public static partial class Media
{
    public static int PathLimit { get; set; } = PyPath.IsWin ? 259 : 0;   // file paths tools can open (no long-path support assumed)

    // ---------------------------------------------------------------- seams the tests replace (tests/test_swap.py)

    /// <summary>send2trash: the file goes to the Recycle Bin, or this throws.</summary>
    public static Action<string> SendToTrash { get; set; } = WinApi.SendToTrash;

    /// <summary>Which other programs have each file open ({path: ["qBittorrent"]}).</summary>
    public static Func<List<string>, Dictionary<string, List<string>>> WhoHasOpen { get; set; } = WinApi.WhoHasOpen;

    public static Func<string, bool> IsNetworkPath { get; set; } = WinApi.IsNetworkPath;

    /// <summary>The physical disk a file is on and whether it's an SSD (("", false) when unknown).</summary>
    public static Func<string, (string Key, bool Ssd)> DriveOf { get; set; } = WinApi.DriveOf;

    /// <summary>At most this many saves run at once, and only where the drives allow it (see Session.RunBatch).</summary>
    public static int MaxParallelSaves { get; set; } = 2;

    /// <summary>os.replace</summary>
    public static Action<string, string> ReplaceFile { get; set; } = (a, b) => File.Move(a, b, true);

    public static Action<double> Sleep { get; set; } = s => Thread.Sleep(TimeSpan.FromSeconds(s));

    public static Func<Tools, string, Action<string>?, MediaInfo> ProbeFile { get; set; } = Probe;

    public static Func<Tools, MediaInfo, List<SubTrack>, string, Dictionary<int, string>> ExtractFor { get; set; } =
        (tools, info, tracks, dir) => ExtractSubs(tools, info, tracks, dir);

    public static Func<string, string, int> CountCues { get; set; } = (path, fmt) => SubFormats.Load(path, fmt).Cues.Count;

    public const string Kept = "kept in its folder";

    /// <summary>Send a file SubScrub no longer needs to the Recycle Bin. It is never deleted: if the Recycle Bin can't
    /// take it (network drives have none), it stays where it is. Returns null when it went, otherwise why it was kept.</summary>
    public static string? Trash(string path, Action<string>? log = null)
    {
        string why;
        if (IsNetworkPath(path))
            why = "network drives have no Recycle Bin";
        else
        {
            try
            {
                SendToTrash(path);
                return null;
            }
            catch (Exception e)
            {
                if (!Path.Exists(path))                  // Windows reported a problem but the file did go
                {
                    log?.Invoke($"Recycle Bin reported {e.GetType().Name}({e.Message}) for {PyPath.Name(path)}, but the file was moved.");
                    return null;
                }
                why = "the Recycle Bin couldn't take it";
                log?.Invoke($"Recycle Bin unavailable for {PyPath.Name(path)}: {e.Message}");
            }
        }
        log?.Invoke($"Kept {path} ({why})");
        return why;
    }

    /// <summary>Trash() as words for a save note: "moved to the Recycle Bin" or "kept in its folder (why)".</summary>
    public static string Recycle(string path, Action<string>? log = null) =>
        Trash(path, log) is { } why ? $"{Kept} ({why})" : "moved to the Recycle Bin";

    /// <summary>Replace a subtitle file with its cleaned version the safe way videos are replaced (SwapIn): the new
    /// text is written first, and if the old file can't go to the Recycle Bin nothing is changed.</summary>
    public static string ReplaceTextFile(string src, string final, string text, Action<string>? log)
    {
        var work = PyPath.WithName(src, $"{PyPath.Stem(src)}{TmpMark}{PyPath.Suffix(final)}");
        WriteText(work, text, true);
        return SwapIn(src, work, final, log);
    }

    private static void WriteText(string path, string text, bool bom) =>
        File.WriteAllText(path, text, new UTF8Encoding(bom));

    // ================================================================ saving

    public static SaveResult Save(Tools tools, MediaInfo info, SubTrack track, string cleanedText, string fmt, SaveOptions opts,
                                  Action<double, string>? onProgress = null, CancellationToken cancel = default,
                                  Action<string>? log = null)
    {
        var lang = Py.Strip(!string.IsNullOrEmpty(opts.Language) ? opts.Language : (!string.IsNullOrEmpty(track.Language) ? track.Language : "und"));
        if (lang.Length == 0)
            lang = "und";
        var name = Py.Strip(opts.TrackName);
        if (name.Length == 0)
            name = DefaultTrackName(track);
        opts.Language = lang;

        if (info.Kind == "subtitle")
            return SaveSubtitleSource(tools, info, cleanedText, fmt, opts, lang, name, onProgress, cancel, log);

        if (opts.Mode == "sidecar")
        {
            var final = OutputPath(info, opts, fmt);
            var srcFile = track.External.Length > 0 ? track.External : null;
            var same = srcFile != null && PyPath.NormCase(final) == PyPath.NormCase(srcFile);
            if (Path.Exists(final) && !opts.OverwriteSidecar && !same)
                final = UniquePath(final);
            var note = "Subtitle file saved next to the video.";
            if (same)
                note = $"“{PyPath.Name(srcFile!)}” replaced with the cleaned version. {ReplaceTextFile(srcFile!, final, cleanedText, log)}";
            else
                WriteText(final, cleanedText, true);
            log?.Invoke($"Wrote {final}");
            if (!same)
                note += RecycleExtras(opts.RecyclePaths.Where(p => srcFile != null && p == srcFile).ToList(), log);
            return new SaveResult(final, note);
        }
        // a subtitle file next to the video is added as a new track. Nothing inside the video is dropped, except a
        // cleaned track from an earlier SubScrub run in the same language, which the new one replaces.
        SubTrack? inside = track;
        if (track.External.Length > 0)
        {
            inside = info.Subs.FirstOrDefault(t => t.TextBased && t.FromSubscrub && LangShort(t.Language) == LangShort(lang));
            if (inside != null)
            {
                opts = opts.Copy();
                opts.KeepOriginal = false;
                log?.Invoke($"Replacing the earlier cleaned track “{inside.Title}” in {info.Path}");
            }
        }
        CheckRenameTarget(info, opts, fmt);
        var res = Remux(tools, info, inside, cleanedText, fmt, opts, lang, name, onProgress, cancel, log);
        return AfterVideoSave(info, opts, res, log);
    }

    private static void CheckRenameTarget(MediaInfo info, SaveOptions opts, string fmt)
    {
        if (opts.Mode == "replace" && opts.RenameTo.Length > 0)
        {
            var final = OutputPath(info, opts, fmt);
            if (Path.Exists(final) && PyPath.NormCase(final) != PyPath.NormCase(info.Path))
                throw new ToolError($"Can't rename to “{PyPath.Name(final)}”: a file with that name is already in the folder. "
                                    + "Nothing was changed.");
        }
    }

    private static SaveResult AfterVideoSave(MediaInfo info, SaveOptions opts, SaveResult res, Action<string>? log)
    {
        var note = res.Note;
        if (opts.Mode == "replace")
        {
            note += RecycleExtras(opts.RecyclePaths, log, [res.Path, info.Path]);
            if (opts.RenameTo.Length > 0 && PyPath.NormCase(res.Path) != PyPath.NormCase(info.Path))
                note += RenameSidecars(info.Path, res.Path, opts.RenameSidecars, log);
        }
        if (!File.Exists(res.Path))
            throw new ToolError($"“{PyPath.Name(res.Path)}” is missing right after saving.");
        return res with { Note = note };
    }

    /// <summary>'Old.eng.srt' -> 'New.eng.srt' for subtitle files that stay next to a renamed video.</summary>
    public static string RenameSidecars(string oldVideo, string newVideo, IEnumerable<string> sidecars, Action<string>? log)
    {
        string oldStem = PyPath.Stem(oldVideo), newStem = PyPath.Stem(newVideo);
        var done = new List<string>();
        foreach (var p in sidecars)
        {
            if (string.IsNullOrEmpty(p) || !File.Exists(p))
                continue;
            var name = PyPath.Name(p);
            if (!name.ToLowerInvariant().StartsWith(oldStem.ToLowerInvariant(), StringComparison.Ordinal))
                continue;
            var target = PyPath.WithName(p, newStem + name[oldStem.Length..]);
            if (Path.Exists(target))
            {
                done.Add($"kept “{name}” (“{PyPath.Name(target)}” already exists)");
                continue;
            }
            try
            {
                File.Move(p, target);
                log?.Invoke($"Renamed {name} -> {PyPath.Name(target)}");
                done.Add($"“{name}” renamed to match");
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
                done.Add($"couldn't rename “{name}” ({e.Message})");
            }
        }
        return done.Count > 0 ? " " + string.Join("; ", done) + "." : "";
    }

    /// <summary>For a video whose subtitles need no cleaning: remove other-language subtitle tracks and/or rename it.</summary>
    public static SaveResult TidyOrRename(Tools tools, MediaInfo info, SaveOptions opts, Action<double, string>? onProgress = null,
                                          CancellationToken cancel = default, Action<string>? log = null)
    {
        var drop = info.Subs.Where(t => opts.DropOtherLanguages && IsOtherLanguage(t, opts.KeepLanguage)).Select(t => t.Index).ToHashSet();
        if (drop.Count > 0)
        {
            CheckRenameTarget(info, opts, "srt");
            var res = Remux(tools, info, null, null, "srt", opts, opts.KeepLanguage, "", onProgress, cancel, log, drop);
            res = res with
            {
                Note = $"Removed {drop.Count} subtitle track{(drop.Count != 1 ? "s" : "")} in other languages. " + res.Note,
            };
            return AfterVideoSave(info, opts, res, log);
        }
        if (opts.RenameTo.Length > 0)
        {
            var src = info.Path;
            var final = PyPath.WithName(src, PyPath.Stem(opts.RenameTo) + PyPath.Suffix(src));
            if (PyPath.NormCase(final) == PyPath.NormCase(src))
                return new SaveResult(src, "Name already right.");
            if (Path.Exists(final))
                throw new ToolError($"Can't rename to “{PyPath.Name(final)}”: a file with that name is already in the folder.");
            var who = WhoHasOpen([src]).GetValueOrDefault(src);
            if (who is { Count: > 0 })
                throw new ToolError($"“{PyPath.Name(src)}” is open in {string.Join(" and ", who)}, so it couldn't be renamed. "
                                    + "Nothing was changed.");
            File.Move(src, final);
            log?.Invoke($"Renamed {PyPath.Name(src)} -> {PyPath.Name(final)}");
            return new SaveResult(final, "Renamed." + RenameSidecars(src, final, opts.RenameSidecars, log));
        }
        return new SaveResult(info.Path, "Nothing to change.");
    }

    /// <summary>Remove subtitle files that are now inside the video. Only subtitle files, never the video itself.</summary>
    private static string RecycleExtras(IEnumerable<string> paths, Action<string>? log, IEnumerable<string>? protect = null)
    {
        var done = new List<string>();
        var keep = (protect ?? []).Where(x => !string.IsNullOrEmpty(x)).Select(PyPath.Key).ToHashSet();
        foreach (var p in paths)
        {
            if (string.IsNullOrEmpty(p) || !TextSubExts.Contains(PyPath.Suffix(p).ToLowerInvariant()) || keep.Contains(PyPath.Key(p)))
                continue;
            if (File.Exists(p))
            {
                try
                {
                    done.Add($"“{PyPath.Name(p)}” {Recycle(p, log)}");
                }
                catch (Exception e) when (e is IOException or UnauthorizedAccessException)
                {
                    done.Add($"couldn't remove “{PyPath.Name(p)}” ({e.Message})");
                }
            }
        }
        return done.Count > 0 ? " " + string.Join("; ", done) + "." : "";
    }

    private static SaveResult SaveSubtitleSource(Tools tools, MediaInfo info, string text, string fmt, SaveOptions opts, string lang,
                                                 string name, Action<double, string>? onProgress, CancellationToken cancel,
                                                 Action<string>? log)
    {
        var src = info.Path;
        if (opts.Mode == "embed")
        {
            if (string.IsNullOrEmpty(info.Companion) || !File.Exists(info.Companion))
                throw new ToolError($"There's no video named like “{PyPath.Name(src)}” in its folder to embed it into.");
            onProgress?.Invoke(-1, "Opening the video");
            var vinfo = ProbeFile(tools, info.Companion, log);
            var vopts = new SaveOptions
            {
                Mode = "replace", KeepOriginal = true, MakeDefault = opts.MakeDefault, TrackName = name, Language = lang,
            };
            var res = Remux(tools, vinfo, null, text, fmt, vopts, lang, name, onProgress, cancel, log);
            if (opts.RecycleSourceSub && Path.Exists(src))
                res = res with { Note = res.Note + $" “{PyPath.Name(src)}” {Recycle(src, log)}." };
            return res;
        }
        var target = OutputPath(info, opts, fmt);
        if (opts.Mode == "replace")                      // the cleaned file takes the old one's place, like a video does
            return new SaveResult(target, ReplaceTextFile(src, target, text, log));
        WriteText(target, text, true);
        return new SaveResult(target, "Saved next to the original.");
    }

    private static long FreeSpace(string folder)
    {
        if (PyPath.IsWin)
            return WinApi.FreeSpace(folder);
        var full = Path.GetFullPath(folder);
        var drive = DriveInfo.GetDrives().Where(d => full.StartsWith(d.Name, StringComparison.Ordinal))
            .MaxBy(d => d.Name.Length);
        return drive?.AvailableFreeSpace ?? long.MaxValue;
    }

    /// <summary>Write the video again with the cleaned track added (and the old one dropped unless KeepOriginal),
    /// and other-language subtitle tracks removed when asked. `track` is the subtitle track the text came from, or
    /// null when it came from a separate file; `text` null means no new track (tidy-up only).</summary>
    public static SaveResult Remux(Tools tools, MediaInfo info, SubTrack? track, string? text, string fmt, SaveOptions opts,
                                   string lang, string name, Action<double, string>? onProgress, CancellationToken cancel,
                                   Action<string>? log, HashSet<int>? drop = null)
    {
        if (string.IsNullOrEmpty(tools.Ffmpeg))
            throw new ToolError("FFmpeg was not found.");
        var src = info.Path;
        // two saves at once on one drive each need their room: what the other one will write counts as used
        var root = Path.GetPathRoot(Path.GetFullPath(src)) ?? "";
        var need = (long)(info.Size * 1.02) + 64 * 1024 * 1024;
        lock (Reserved)
        {
            var free = FreeSpace(PyPath.Dir(PyPath.Abs(src))) - Reserved.GetValueOrDefault(root);
            if (free < info.Size * 1.02 + 64 * 1024 * 1024)
                throw new ToolError($"Not enough free space on that drive: need about {PyFmt.Fixed(info.Size * 1.02 / 1e9, 1)} GB, "
                                    + $"have {PyFmt.Fixed(free / 1e9, 1)} GB.");
            Reserved[root] = Reserved.GetValueOrDefault(root) + need;
        }
        try
        {
            return RemuxWithRoom(tools, info, track, text, fmt, opts, lang, name, onProgress, cancel, log, drop);
        }
        finally
        {
            lock (Reserved)
                Reserved[root] -= need;
        }
    }

    private static readonly Dictionary<string, long> Reserved = [];      // drive root -> bytes saves in progress will write

    private static SaveResult RemuxWithRoom(Tools tools, MediaInfo info, SubTrack? track, string? text, string fmt, SaveOptions opts,
                                            string lang, string name, Action<double, string>? onProgress, CancellationToken cancel,
                                            Action<string>? log, HashSet<int>? drop)
    {
        var final = OutputPath(info, opts, fmt);
        var src = info.Path;

        var work = opts.Mode == "replace" ? PyPath.WithName(final, $"{PyPath.Stem(src)}.subscrub-tmp{PyPath.Suffix(final)}") : final;
        if (PathLimit != 0 && PyFmt.Len(work) > PathLimit)
            throw new ToolError($"The path to “{PyPath.Name(src)}” is too long once the temporary copy is added ({PyFmt.Len(work)} "
                                + $"characters; the limit is {PathLimit}). Nothing was changed. Move it to a shorter folder "
                                + "and save again.");
        var tmpdir = Directory.CreateTempSubdirectory("subscrub_mux_").FullName;
        string? subPath = null;
        if (text != null)
        {
            subPath = PyPath.Join(tmpdir, $"cleaned.{fmt}");
            WriteText(subPath, text, false);
        }
        var dropped = new HashSet<int>(drop ?? []);
        if (track != null && !opts.KeepOriginal)
            dropped.Add(track.Index);
        if (opts.DropOtherLanguages)
            dropped.UnionWith(info.Subs.Where(t => IsOtherLanguage(t, opts.KeepLanguage)).Select(t => t.Index));

        if (Path.Exists(work))
            File.Delete(work);
        var useMkvmerge = info.Kind == "mkv" && !string.IsNullOrEmpty(tools.Mkvmerge);
        try
        {
            if (useMkvmerge)
                MuxMkvmerge(tools, info, track, dropped, subPath, work, lang, name, opts, onProgress, cancel, log);
            else
                MuxFfmpeg(tools, info, track, dropped, subPath, work, lang, name, opts, onProgress, cancel, log);
            onProgress?.Invoke(-1, "Checking the new file");
            var expected = info.Subs.Count + (subPath != null ? 1 : 0) - dropped.Count;
            Verify(tools, info, work, expected, log, subPath != null ? text : null, fmt, (lang, name));
        }
        catch
        {
            try
            {
                if (Path.Exists(work))
                    File.Delete(work);
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
            }
            throw;
        }
        finally
        {
            Session.TryDeleteDir(tmpdir);
        }
        var note = $"Saved with {(useMkvmerge ? "MKVToolNix" : "FFmpeg")}.";
        if (opts.Mode == "replace")
            note = SwapIn(src, work, final, log);
        return new SaveResult(final, note);
    }

    /// <summary>Put the verified new file in the original's place, never ending up with neither:
    ///   1. nothing happens while another program has the original open;
    ///   2. the original goes to the Recycle Bin (if that fails and it's still there, nothing is changed);
    ///   3. the new file takes its name. From the moment the original has moved, the new file is never deleted: if it
    ///      can't be renamed it stays next to it under its temporary name.</summary>
    public static string SwapIn(string src, string work, string final, Action<string>? log)
    {
        lock (SwapLock)                                  // two saves at once never swap at the same moment
            return SwapInLocked(src, work, final, log);
    }

    private static readonly object SwapLock = new();

    private static string SwapInLocked(string src, string work, string final, Action<string>? log)
    {
        ToolError Abort(string msg)
        {
            try
            {
                File.Delete(work);                       // just the new copy; the original is untouched
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
            }
            return new ToolError(msg);
        }

        ToolError InUse()
        {
            var who = WhoHasOpen([src]).GetValueOrDefault(src);
            var by = who is { Count: > 0 } ? string.Join(" and ", who)
                : "another program (a torrent client, a player, Jellyfin, Explorer's preview…)";
            return Abort($"“{PyPath.Name(src)}” is open in {by}, so it couldn't be replaced. Nothing was changed — "
                         + "close or pause it there and save again.");
        }

        if (WhoHasOpen([src]).GetValueOrDefault(src) is { Count: > 0 })
            throw InUse();
        if (PyPath.NormCase(final) != PyPath.NormCase(src) && Path.Exists(final))   // taken since the save began
            throw Abort($"Can't rename to “{PyPath.Name(final)}”: a file with that name is already in the folder. "
                        + "Nothing was changed.");
        var network = IsNetworkPath(src);
        var note = "Original moved to the Recycle Bin.";
        if (network)
            note = "Original replaced (network drives have no Recycle Bin).";
        else
        {
            try
            {
                SendToTrash(src);
            }
            catch (Exception e)
            {
                if (Path.Exists(src))
                {
                    if (e is UnauthorizedAccessException || (e is RecycleError r && r.InUse))
                        throw InUse();
                    throw Abort($"“{PyPath.Name(src)}” couldn't be moved to the Recycle Bin ({e.Message}), so it wasn't replaced. "
                                + "Nothing was changed.");
                }
                // Windows reported a problem but the original is gone from the folder: carry on, keep the new file
                note = "Original moved out of the folder (Windows reported a Recycle Bin problem: look for it there).";
                log?.Invoke($"Recycle Bin reported {e.GetType().Name}({e.Message}) for {PyPath.Name(src)}, but the file was moved.");
            }
        }

        Exception? last = null;
        for (var k = 0; k < 12; k++)                     // an indexer or antivirus can hold the new file for a moment
        {
            try
            {
                ReplaceFile(work, final);
                last = null;
                break;
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
                last = e;
                if (network && Path.Exists(src))        // nothing has changed yet on a network drive
                    break;
                Sleep(0.5);
            }
        }
        if (last != null)
        {
            if (network && Path.Exists(src))
            {
                if (last is UnauthorizedAccessException || WinApi.IsSharingViolation(last))
                    throw InUse();
                throw Abort($"“{PyPath.Name(src)}” couldn't be replaced ({last.Message}). Nothing was changed.");
            }
            throw new ToolError($"The original “{PyPath.Name(src)}” is in the Recycle Bin, but the new version couldn't take its "
                                + $"name ({last.Message}). It's saved next to it as “{PyPath.Name(work)}”: rename that to “{PyPath.Name(final)}”.");
        }
        if (!File.Exists(final))
            throw new ToolError($"“{PyPath.Name(final)}” is missing right after saving. The original is in the Recycle Bin.");
        if (network && !PyPath.Same(final, src) && Path.Exists(src))
            File.Delete(src);                            // renamed on a network drive: the old name goes
        log?.Invoke(note);
        return note;
    }

    private const string TagsXml = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
                                   + "<Tags><Tag><Targets><TargetTypeValue>50</TargetTypeValue></Targets>\n"
                                   + "<Simple><Name>SUBSCRUB</Name><String>cleaned</String></Simple></Tag></Tags>\n";

    private static readonly Regex GuiProgress = new(@"\A#GUI#progress (\d+)%");

    private static void MuxMkvmerge(Tools tools, MediaInfo info, SubTrack? track, HashSet<int> drop, string? subPath, string outPath,
                                    string lang, string name, SaveOptions opts, Action<double, string>? onProgress,
                                    CancellationToken cancel, Action<string>? log)
    {
        var identText = RunCapture([tools.Mkvmerge!, "-J", info.Path], log, 120);
        var ident = JsonNode.Parse(identText.Length > 0 ? identText : "{}") as JsonObject ?? [];
        var mtracks = (ident["tracks"] as JsonArray ?? []).Select(t => t!.AsObject()).ToList();
        var subIds = mtracks.Where(t => JStr(t["type"]) == "subtitles").Select(t => t["id"]!.GetValue<int>()).ToList();
        if (subIds.Count != info.Subs.Count)
            throw new ToolError("MKVToolNix and FFprobe disagree about the subtitle tracks in this file.");
        var toId = info.Subs.Select((t, k) => (t.Index, k)).ToDictionary(x => x.Index, x => subIds[x.k]);
        var dropIds = drop.Where(toId.ContainsKey).Select(i => toId[i]).ToHashSet();
        var keepIds = subIds.Where(i => !dropIds.Contains(i)).ToList();
        int? target = track != null && toId.TryGetValue(track.Index, out var tid0) ? tid0 : null;
        // the cleaned track goes where the one it came from was (or after the last subtitle track kept)
        int? anchor = target ?? (keepIds.Count > 0 ? keepIds[^1] : null);

        var cmd = new List<string> { tools.Mkvmerge!, "--gui-mode", "--output-charset", "UTF-8", "-o", outPath };
        if (dropIds.Count > 0)
        {
            if (keepIds.Count > 0)
                cmd.AddRange(["--subtitle-tracks", string.Join(",", keepIds)]);
            else
                cmd.Add("--no-subtitles");
        }
        if (opts.MakeDefault && subPath != null)
        {
            foreach (var i in keepIds)
                cmd.AddRange(["--default-track-flag", $"{i}:0"]);
        }
        cmd.Add(info.Path);
        if (subPath != null)
        {
            var tags = PyPath.Join(PyPath.Dir(subPath), "tags.xml");
            WriteText(tags, TagsXml, false);
            cmd.AddRange(["--language", $"0:{lang}", "--track-name", $"0:{name}",
                          "--default-track-flag", $"0:{(opts.MakeDefault ? 1 : 0)}",
                          "--hearing-impaired-flag", "0:0", "--sub-charset", "0:UTF-8", "--tags", $"0:{tags}", subPath]);
            var order = new List<string>();
            var placed = false;
            foreach (var t in mtracks)
            {
                var id = t["id"]!.GetValue<int>();
                if (JStr(t["type"]) == "subtitles" && dropIds.Contains(id))
                {
                    if (id == target && !placed)
                    {
                        order.Add("1:0");
                        placed = true;
                    }
                    continue;
                }
                order.Add($"0:{id}");
                if (id == anchor && !placed)
                {
                    order.Add("1:0");
                    placed = true;
                }
            }
            if (!placed)
                order.Add("1:0");
            cmd.AddRange(["--track-order", string.Join(",", order)]);
        }
        RunStream(cmd, line =>
        {
            var m = GuiProgress.Match(line);
            if (m.Success && onProgress != null)
                onProgress(int.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture) / 100.0, "Writing new file");
        }, log, cancel, [0, 1]);                         // 1 = finished with warnings
    }

    private static int StreamIndex(JsonObject s) => s["index"] is JsonValue v && v.TryGetValue<int>(out var i) ? i : 0;

    private static void MuxFfmpeg(Tools tools, MediaInfo info, SubTrack? track, HashSet<int> drop, string? subPath, string outPath,
                                  string lang, string name, SaveOptions opts, Action<double, string>? onProgress,
                                  CancellationToken cancel, Action<string>? log)
    {
        var outKind = ContainerKind(outPath);
        var cmd = new List<string>
        {
            tools.Ffmpeg!, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats",
            "-i", info.Path,
        };
        if (subPath != null)
            cmd.AddRange(["-i", subPath]);
        var mapped = info.Streams.Where(s => !(JStr(s["codec_type"]) is "data" or "attachment" && outKind != "mkv")).ToList();
        var subsIdx = mapped.Where(s => JStr(s["codec_type"]) == "subtitle" && !drop.Contains(StreamIndex(s))).Select(StreamIndex).ToList();
        var mainIdx = mapped.Where(s => JStr(s["codec_type"]) is "video" or "audio").Select(StreamIndex).ToList();
        int? anchor = track?.Index ?? (subsIdx.Count > 0 ? subsIdx[^1] : (mainIdx.Count > 0 ? mainIdx[^1] : null));
        var subOut = new List<SubTrack?>();              // output subtitle order; null = the new track
        var placed = subPath == null;
        foreach (var s in mapped)
        {
            var idx = StreamIndex(s);
            var ctype = JStr(s["codec_type"]);
            if (!drop.Contains(idx))
            {
                cmd.AddRange(["-map", $"0:{idx}"]);
                if (ctype == "subtitle")
                    subOut.Add(info.Subs.First(t => t.Index == idx));
            }
            if (idx == anchor && !placed)
            {
                cmd.AddRange(["-map", "1:0"]);
                subOut.Add(null);
                placed = true;
            }
        }
        if (!placed)
        {
            cmd.AddRange(["-map", "1:0"]);
            subOut.Add(null);
        }
        cmd.AddRange(["-map_metadata", "0", "-map_chapters", "0", "-c", "copy"]);
        if (subPath != null)
        {
            var newPos = subOut.IndexOf(null);
            var srcCodec = PyPath.Suffix(subPath).TrimStart('.') switch { "srt" => "subrip", "ass" => "ass", "vtt" => "webvtt", _ => "subrip" };
            var newCodec = outKind switch { "mp4" => "mov_text", "webm" => "webvtt", _ => "copy" };
            if (outKind == "mkv" && srcCodec == "webvtt")
                newCodec = "srt";                        // WebVTT in MKV is poorly supported by players
            cmd.AddRange([$"-c:s:{newPos}", newCodec]);
        }
        for (var j = 0; j < subOut.Count; j++)
        {
            var t = subOut[j];
            if (t == null)
            {
                cmd.AddRange([$"-disposition:s:{j}", opts.MakeDefault ? "default" : "0",
                              $"-metadata:s:s:{j}", $"language={lang}", $"-metadata:s:s:{j}", $"title={name}",
                              $"-metadata:s:s:{j}", "SUBSCRUB=cleaned"]);
                if (outKind == "mp4")
                    cmd.AddRange([$"-metadata:s:s:{j}", $"handler_name={name}"]);
            }
            else
                cmd.AddRange([$"-disposition:s:{j}", t.DispoFlags(opts.MakeDefault && subPath != null)]);
        }
        if (outKind == "mkv")
            cmd.AddRange(["-default_mode", "passthrough"]);
        cmd.AddRange(["-max_interleave_delta", "0", outPath]);
        RunStream(cmd, FfmpegProgress(info.Duration, onProgress, "Writing new file"), log, cancel);
    }

    /// <summary>How many cues the new file's cleaned track has (null when that track can't be told apart from the
    /// others). The track is found by the tag SubScrub writes, or else by its language and name.</summary>
    private static int? NewTrackCues(Tools tools, MediaInfo after, (string Lang, string Name) hint, Action<string>? log)
    {
        var mine = after.Subs.Where(t => t.TextBased && t.Cleaned).ToList();
        if (mine.Count == 0)
            mine = after.Subs.Where(t => t.TextBased && LangShort(t.Language) == LangShort(hint.Lang)
                                         && Py.Strip(t.Title ?? "").ToLowerInvariant() == Py.Strip(hint.Name).ToLowerInvariant()).ToList();
        if (mine.Count == 0)
            return null;
        var tmp = Directory.CreateTempSubdirectory("subscrub_verify_").FullName;
        try
        {
            var paths = ExtractFor(tools, after, mine, tmp);
            var counts = mine.Where(t => paths.ContainsKey(t.Index)).Select(t => CountCues(paths[t.Index], t.Fmt)).ToList();
            // an earlier cleaned track kept next to this one is another candidate: the new one is the best match
            return counts.Count > 0 ? counts.Max() : 0;
        }
        finally
        {
            Session.TryDeleteDir(tmp);
        }
    }

    /// <summary>Decide whether the new file is good enough to replace the original. Checks the track counts, duration,
    /// size, and (for MKV) the attachments such as ASS fonts, the chapters, and that the new subtitle track really
    /// holds the cleaned lines.</summary>
    public static void Verify(Tools tools, MediaInfo before, string outPath, int expectedSubs, Action<string>? log,
                              string? newText = null, string fmt = "srt", (string, string)? newHint = null)
    {
        if (!Path.Exists(outPath) || new FileInfo(outPath).Length == 0)
            throw new ToolError("The new file wasn't written.");
        var after = ProbeFile(tools, outPath, log);
        var problems = new List<string>();
        if (after.Subs.Count != expectedSubs)
            problems.Add($"expected {expectedSubs} subtitle tracks, found {after.Subs.Count}");
        foreach (var kind in new[] { "video", "audio" })
        {
            if (after.Count(kind) != before.Count(kind))
                problems.Add($"{kind} track count changed ({before.Count(kind)} → {after.Count(kind)})");
        }
        if (before.Duration != 0 && after.Duration != 0 && Math.Abs(before.Duration - after.Duration) > Math.Max(2.0, before.Duration * 0.01))
            problems.Add($"duration changed ({PyFmt.Fixed(before.Duration, 1)}s → {PyFmt.Fixed(after.Duration, 1)}s)");
        if (before.Size != 0 && after.Size < before.Size * 0.5)
            problems.Add("the new file is much smaller than the original");
        if (after.Kind == "mkv" && after.Count("attachment") < before.Count("attachment"))
            problems.Add($"attached files (such as fonts) were lost ({before.Count("attachment")} → {after.Count("attachment")})");
        if (after.Chapters != before.Chapters)
            problems.Add($"chapters changed ({before.Chapters} → {after.Chapters})");
        int? cues = null;
        if (problems.Count == 0 && newText != null && newHint != null)
        {
            var want = SubFormats.Parse(newText, fmt).Cues.Count;
            cues = NewTrackCues(tools, after, newHint.Value, log);
            if (cues == null)
                log?.Invoke("Couldn't tell which subtitle track in the new file is the cleaned one; skipped the line count.");
            else if (cues < want * 0.98 - 1)
                problems.Add($"the cleaned subtitle track has {cues} lines instead of {want}");
        }
        if (problems.Count > 0)
            throw new ToolError("The new file didn't pass checks, so nothing was replaced: " + string.Join("; ", problems));
        log?.Invoke($"Verified {PyPath.Name(outPath)}: {after.Count("video")} video, {after.Count("audio")} audio, "
                    + $"{after.Subs.Count} subtitle tracks, {after.Count("attachment")} attachments, {after.Chapters} chapters, "
                    + $"{PyFmt.Fixed(after.Duration, 1)}s" + (cues != null ? $", {cues} cleaned lines." : "."));
    }
}
