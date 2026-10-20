using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using SubScrub.Engine;

// The C# engine's tests: tests/test_swap.py, test_pipeline.py and test_edge.py, on real videos.
//   SubScrub.Engine.Tests <repository folder> [name filter]
var root = args.Length > 0 ? Path.GetFullPath(args[0]) : Directory.GetCurrentDirectory();
var filter = args.Length > 1 ? args[1] : "";
Tests.Root = root;
Tests.Tmp = Directory.CreateTempSubdirectory("subscrub_cs_tests_").FullName;
Environment.SetEnvironmentVariable("XDG_DATA_HOME", Path.Combine(Tests.Tmp, "xdg"));      // a Linux Recycle Bin of our own
Environment.SetEnvironmentVariable("LOCALAPPDATA", Path.Combine(Tests.Tmp, "appdata"));  // the rename history too
Tests.ToolsFound = Media.FindTools();
var methods = typeof(Tests).GetMethods(BindingFlags.Public | BindingFlags.Static)
    .Where(m => m.Name.StartsWith("Test", StringComparison.Ordinal) && m.Name.Contains(filter, StringComparison.OrdinalIgnoreCase))
    .OrderBy(m => m.MetadataToken).ToList();
var failed = 0;
foreach (var m in methods)
{
    Tests.Reset();
    try
    {
        m.Invoke(null, null);
        Console.WriteLine($"ok   {m.Name}");
    }
    catch (TargetInvocationException e) when (e.InnerException is Tests.Skip s)
    {
        Console.WriteLine($"skip {m.Name}: {s.Message}");
    }
    catch (TargetInvocationException e)
    {
        failed++;
        Console.WriteLine($"FAIL {m.Name}: {e.InnerException}");
    }
}
Tests.Reset();
try
{
    Directory.Delete(Tests.Tmp, true);
}
catch (Exception)
{
}
Console.WriteLine(failed == 0 ? $"{methods.Count} tests passed" : $"{failed} of {methods.Count} tests failed");
return failed == 0 ? 0 : 1;

internal static class Tests
{
    public static string Root = "";
    public static string Tmp = "";
    public static Tools ToolsFound = new();
    private static readonly List<string> Bin = [];

    public sealed class Skip(string why) : Exception(why);

    public static void Reset()
    {
        Media.SendToTrash = WinApi.SendToTrash;
        Media.WhoHasOpen = WinApi.WhoHasOpen;
        Media.IsNetworkPath = WinApi.IsNetworkPath;
        Media.ReplaceFile = (a, b) => File.Move(a, b, true);
        Media.Sleep = s => Thread.Sleep(TimeSpan.FromSeconds(s));
        Media.ProbeFile = Media.Probe;
        Media.ExtractFor = (tools, info, tracks, dir) => Media.ExtractSubs(tools, info, tracks, dir);
        Media.CountCues = (path, fmt) => SubFormats.Load(path, fmt).Cues.Count;
        Media.PathLimit = PyPath.IsWin ? 259 : 0;
    }

    private static void Check(bool ok, string what = "")
    {
        if (!ok)
            throw new Exception("check failed" + (what.Length > 0 ? ": " + what : ""));
    }

    private static T Throws<T>(Action a) where T : Exception
    {
        try
        {
            a();
        }
        catch (T e)
        {
            return e;
        }
        throw new Exception($"expected {typeof(T).Name}");
    }

    private static string NewDir(string name)
    {
        var d = Path.Combine(Tmp, name);
        if (Directory.Exists(d))
            Directory.Delete(d, true);
        Directory.CreateDirectory(d);
        return d;
    }

    private static Dictionary<string, string> Digest(string folder) =>
        Directory.EnumerateFiles(folder, "*", SearchOption.AllDirectories).Order(StringComparer.Ordinal)
            .ToDictionary(p => Path.GetRelativePath(folder, p), p => Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(p))));

    private static bool Same(Dictionary<string, string> a, Dictionary<string, string> b) =>
        a.Count == b.Count && a.All(kv => b.TryGetValue(kv.Key, out var v) && v == kv.Value);

    private static List<string> Leftovers(string folder) =>
        Directory.EnumerateFileSystemEntries(folder, "*", SearchOption.AllDirectories).Where(p => Path.GetFileName(p).Contains(Media.TmpMark)).ToList();

    private static void CopyDir(string src, string dst)
    {
        Directory.CreateDirectory(dst);
        foreach (var f in Directory.EnumerateFiles(src))
            File.Copy(f, Path.Combine(dst, Path.GetFileName(f)), true);
        foreach (var d in Directory.EnumerateDirectories(src))
            CopyDir(d, Path.Combine(dst, Path.GetFileName(d)));
    }

    private static void NeedTools()
    {
        if (!ToolsFound.Ready)
            throw new Skip("FFmpeg not found");
    }

    // ================================================================ the swap (tests/test_swap.py)

    private static (string Src, string Work) SwapSetup(Action<string> trash)
    {
        var d = NewDir("swap");
        string src = Path.Combine(d, "Show.mkv"), work = Path.Combine(d, "Show.subscrub-tmp.mkv");
        File.WriteAllText(src, "old");
        File.WriteAllText(work, "new");
        Media.SendToTrash = trash;
        Media.WhoHasOpen = _ => [];
        Media.IsNetworkPath = _ => false;
        return (src, work);
    }

    private static void ToBin(string p)
    {
        Bin.Add(File.ReadAllText(p));
        File.Delete(p);
    }

    public static void TestNormalSwap()
    {
        var (src, work) = SwapSetup(ToBin);
        var note = Media.SwapIn(src, work, src, null);
        Check(File.ReadAllText(src) == "new" && !File.Exists(work) && Bin[^1] == "old");
        Check(note.Contains("Recycle Bin"), note);
    }

    public static void TestBinReportsErrorAfterTakingTheFile()
    {
        var (src, work) = SwapSetup(p =>
        {
            ToBin(p);
            throw new IOException("operation aborted");
        });
        Media.SwapIn(src, work, src, null);
        Check(File.ReadAllText(src) == "new", "the new file survives");
    }

    public static void TestBinRefusesAndNothingChanges()
    {
        var (src, work) = SwapSetup(_ => throw new IOException("path too long"));
        var e = Throws<ToolError>(() => Media.SwapIn(src, work, src, null));
        Check(e.Message.Contains("Nothing was changed"), e.Message);
        Check(File.ReadAllText(src) == "old" && !File.Exists(work));
    }

    public static void TestBinSaysInUseAndNothingChanges()
    {
        var (src, work) = SwapSetup(_ => throw new RecycleError("in use", 32));
        var e = Throws<ToolError>(() => Media.SwapIn(src, work, src, null));
        Check(e.Message.Contains("couldn't be replaced") && e.Message.Contains("another program"), e.Message);
        Check(File.ReadAllText(src) == "old" && !File.Exists(work));
    }

    public static void TestOpenElsewhereAndNothingChanges()
    {
        var (src, work) = SwapSetup(ToBin);
        Media.WhoHasOpen = _ => new() { [src] = ["qBittorrent"] };
        var e = Throws<ToolError>(() => Media.SwapIn(src, work, src, null));
        Check(e.Message.Contains("qBittorrent"), e.Message);
        Check(File.ReadAllText(src) == "old" && !File.Exists(work));
    }

    public static void TestRenameFailsAfterOriginalMovedKeepsNewFile()
    {
        var (src, work) = SwapSetup(ToBin);
        Media.ReplaceFile = (_, _) => throw new UnauthorizedAccessException("in use");
        Media.Sleep = _ => { };
        var e = Throws<ToolError>(() => Media.SwapIn(src, work, src, null));
        Check(e.Message.Contains(Path.GetFileName(work)), e.Message);
        Check(File.ReadAllText(work) == "new", "never deleted once the original has moved");
    }

    public static void TestRealRecycleBin()
    {
        var d = NewDir("bin");
        var f = Path.Combine(d, "Old.en.sdh.srt");
        File.WriteAllText(f, "x");
        var what = Media.Recycle(f);
        Check(!File.Exists(f) && what == "moved to the Recycle Bin", what);
    }

    // ================================================================ the check before the swap

    private static MediaInfo Info(int subs = 1, int attach = 1, int chapters = 2, string kind = "mkv", bool cleaned = true)
    {
        var streams = new List<JsonObject> { new() { ["codec_type"] = "video" }, new() { ["codec_type"] = "audio" } };
        for (var i = 0; i < attach; i++)
            streams.Add(new JsonObject { ["codec_type"] = "attachment" });
        var tracks = Enumerable.Range(0, subs).Select(i => new SubTrack
        {
            Index = 2 + i, SubPos = i, Codec = "subrip", Language = "eng", Title = "English", Cleaned = cleaned && i == 0,
        }).ToList();
        return new MediaInfo { Path = "x.mkv", Kind = kind, FormatName = "matroska", Duration = 10.0, Size = 1000, Streams = streams, Subs = tracks, Chapters = chapters };
    }

    private static string VerifyWith(MediaInfo after, int cues = 2)
    {
        var text = "1\n00:00:01,000 --> 00:00:02,000\nHi.\n\n2\n00:00:03,000 --> 00:00:04,000\nYo.\n";
        var outPath = Path.Combine(NewDir("verify"), "new.mkv");
        File.WriteAllText(outPath, "data");
        var before = Info();
        Media.ProbeFile = (_, _, _) => after;
        Media.ExtractFor = (_, _, tracks, _) => tracks.ToDictionary(t => t.Index, _ => "x");
        Media.CountCues = (_, _) => cues;
        try
        {
            Media.Verify(new Tools(), before, outPath, after.Subs.Count, null, text, "srt", ("eng", "English"));
            return "";
        }
        catch (ToolError e)
        {
            return e.Message;
        }
    }

    public static void TestVerifyAcceptsAGoodCopy() => Check(VerifyWith(Info()) == "");

    public static void TestVerifyRefusesLostFonts() => Check(VerifyWith(Info(attach: 0)).Contains("attached files"));

    public static void TestVerifyRefusesLostChapters() => Check(VerifyWith(Info(chapters: 0)).Contains("chapters changed"));

    public static void TestVerifyRefusesATrackThatLostCues()
    {
        var err = VerifyWith(Info(), 0);
        Check(err.Contains("0 lines instead of 2") && err.Contains("nothing was replaced"), err);
    }

    public static void TestVerifyFindsTheNewTrackByNameWhenTheTagIsLost()
    {
        Check(VerifyWith(Info(cleaned: false), 0).EndsWith("0 lines instead of 2", StringComparison.Ordinal));
        Check(VerifyWith(Info(cleaned: false), 2) == "");
    }

    // ================================================================ the pipeline (tests/test_pipeline.py)

    private static (MediaInfo Before, MediaInfo After, SaveResult Out) RunCase(Tools tools, string video, Func<MediaInfo, SubTrack> pick,
                                                                             SaveOptions opts, string label)
    {
        var info = Media.Probe(tools, video);
        var track = pick(info);
        var tmp = NewDir("case");
        var paths = Media.ExtractSubs(tools, info, info.Subs, tmp);
        var doc = SubFormats.Load(paths[track.Index], track.Fmt);
        var res = Cleaner.CleanDoc(doc, new CleanOptions());
        var outp = Media.Save(tools, info, track, res.Render(), track.Fmt, opts);
        var after = Media.Probe(tools, outp.Path);
        Console.WriteLine($"     {label}: {Path.GetFileName(outp.Path)} ({outp.Note}) "
                          + string.Join(", ", after.Subs.Select(s => $"{s.Title}{(s.Default ? "*" : "")}")));
        return (info, after, outp);
    }

    private static (string Work, string Mkv, string Mp4) PipelineFiles()
    {
        NeedTools();
        string mkvSrc = Path.Combine(Root, "samples", "Sample (SDH test).mkv"), mp4Src = Path.Combine(Root, "samples", "Sample (SDH test).mp4");
        if (!File.Exists(mkvSrc) || !File.Exists(mp4Src))
            throw new Skip("run \"python -m tests.make_samples\" first");
        var work = NewDir("pipeline");
        string mkv = Path.Combine(work, "Test Movie (2024).mkv"), mp4 = Path.Combine(work, "Test Clip.mp4");
        File.Copy(mkvSrc, mkv);
        File.Copy(mp4Src, mp4);
        return (work, mkv, mp4);
    }

    private static SubTrack Sdh(MediaInfo i) => i.Subs.First(t => t.SdhFlagged && t.Language == "eng");

    public static void TestPipeline()
    {
        var (work, mkv, mp4) = PipelineFiles();
        var tools = ToolsFound;
        var src = Media.Probe(tools, mkv);
        Check(src.Count("attachment") == 1 && src.Chapters == 2, "the sample carries a font and chapters");

        // 1. MKV via mkvmerge, new file, keep original
        var (before, after, outp) = RunCase(tools, mkv, Sdh, new SaveOptions { Mode = "new", KeepOriginal = true }, "mkv/mkvmerge/new");
        Check(after.Subs.Count == before.Subs.Count + 1);
        var nw = after.Subs.Where(s => s.Default).ToList();
        Check(nw.Count == 1 && !nw[0].HearingImpaired, "the cleaned track is the default one");
        Check(after.Subs.Count(s => s.Default) == 1);
        Check(nw[0].SubPos == 2, "cleaned track should sit right after the original");
        Check(after.Count("attachment") == before.Count("attachment"), "attachments (fonts) must survive");

        // 2. MKV via ffmpeg (no mkvmerge), replace mode, drop original
        var t2 = new Tools { Ffmpeg = tools.Ffmpeg, Ffprobe = tools.Ffprobe };
        (before, after, outp) = RunCase(t2, mkv, Sdh, new SaveOptions { Mode = "replace", KeepOriginal = false }, "mkv/ffmpeg/replace");
        Check(outp.Path == mkv && after.Subs.Count == before.Subs.Count);
        Check(after.Chapters == 2, "chapters must survive");
        Check(!after.Subs.Any(s => s.Title == "English SDH"));
        Check(after.Count("attachment") == before.Count("attachment"), "attachments (fonts) must survive");
        Check(after.Subs.Count(s => s.Default) == 1);

        // 3. MP4 (mov_text) via ffmpeg, new file
        (before, after, outp) = RunCase(tools, mp4, Sdh, new SaveOptions { Mode = "new", KeepOriginal = true }, "mp4/ffmpeg/new");
        Check(after.Subs.Count == 3 && after.Subs[2].Codec == "mov_text" && after.Subs[2].Default);
        Check(after.Subs.Count(t => t.Default) == 1 && after.Chapters == before.Chapters);

        // 4. sidecar
        (_, _, outp) = RunCase(tools, mp4, i => i.Subs[0], new SaveOptions { Mode = "sidecar" }, "sidecar");
        Check(outp.Path.EndsWith("Test Clip.en.srt", StringComparison.Ordinal), outp.Path);

        // 5. cancel mid-way leaves nothing behind
        var info = Media.Probe(tools, mkv);
        using (var cts = new CancellationTokenSource())
        {
            cts.Cancel();
            Throws<Cancelled>(() => Media.Save(tools, info, info.Subs[0], "1\n00:00:01,000 --> 00:00:02,000\nHi\n", "srt",
                                               new SaveOptions { Mode = "new" }, cancel: cts.Token));
        }
        Check(Leftovers(work).Count == 0, "no unfinished copies");

        // 6. a subtitle file next to a video: embed it (and recycle the .srt)
        var vid = Path.Combine(work, "Episode 01.mkv");
        File.Copy(Path.Combine(Root, "samples", "Sample (SDH test).mkv"), vid);
        var srt = Path.Combine(work, "Episode 01.en.sdh.srt");
        File.Copy(Path.Combine(Root, "tests", "fixtures", "sdh_en.srt"), srt);
        var (sinfo, sdoc) = Media.ProbeSubtitleFile(srt);
        Check(sinfo.Companion == vid && sinfo.Subs[0].Language == "eng" && sinfo.Subs[0].SdhFlagged);
        var vbefore = Media.Probe(tools, vid);
        outp = Media.Save(tools, sinfo, sinfo.Subs[0], Cleaner.CleanDoc(sdoc, new CleanOptions()).Render(), "srt", new SaveOptions { Mode = "embed" });
        var vafter = Media.Probe(tools, vid);
        Check(outp.Path == vid && vafter.Subs.Count == vbefore.Subs.Count + 1);
        Check(vafter.Subs.Where(s => s.Default).Select(s => s.Title).SequenceEqual(["English"]));
        Check(!File.Exists(srt), "the SDH .srt should have been recycled");

        // 7. a subtitle file on its own, replace: Other.en.sdh.srt -> Other.en.srt
        var srt2 = Path.Combine(work, "Other.en.sdh.srt");
        File.Copy(Path.Combine(Root, "tests", "fixtures", "sdh_en.srt"), srt2);
        (sinfo, sdoc) = Media.ProbeSubtitleFile(srt2);
        Check(sinfo.Companion == null);
        outp = Media.Save(tools, sinfo, sinfo.Subs[0], Cleaner.CleanDoc(sdoc, new CleanOptions()).Render(), "srt", new SaveOptions { Mode = "replace" });
        Check(outp.Path.EndsWith("Other.en.srt", StringComparison.Ordinal) && !File.Exists(srt2));
        Check(File.ReadAllBytes(outp.Path).Take(3).SequenceEqual(new byte[] { 0xEF, 0xBB, 0xBF }), "saved as UTF-8 with a BOM");

        // 8. default options = replace + drop SDH + make default
        info = Media.Probe(tools, vid);
        var tr = info.Subs.First(t => t.Title == "English SDH");
        var paths = Media.ExtractSubs(tools, info, [tr], NewDir("p8"));
        Media.Save(tools, info, tr, Cleaner.CleanDoc(SubFormats.Load(paths[tr.Index]), new CleanOptions()).Render(), "srt", new SaveOptions());
        after = Media.Probe(tools, vid);
        Check(!after.Subs.Any(s => s.Title == "English SDH") && after.Subs.Count(s => s.Default) == 1);

        // 9. the regular English track is cleaned and becomes the default; the SDH track stays (#20), on both tools
        foreach (var (label, tl) in new[] { ("mkvmerge", tools), ("ffmpeg", t2) })
        {
            if (label == "mkvmerge" && string.IsNullOrEmpty(tools.Mkvmerge))
                continue;
            var v = Path.Combine(work, $"Policy {label}.mkv");
            File.Copy(Path.Combine(Root, "samples", "Sample (SDH test).mkv"), v);
            var it = new Item(v, "video") { Info = Media.Probe(tl, v) };
            var ps = Media.ExtractSubs(tl, it.Info, it.Info.Subs, NewDir("p9" + label));
            foreach (var t in it.Info.Subs.Where(t => ps.ContainsKey(t.Index)))
                it.Docs[t.Index] = SubFormats.Load(ps[t.Index], t.Fmt);
            Batch.Measure(it);
            it.Sel = Batch.PickTrack(it, null);
            var track = it.Track()!;
            Check(track.Title == "English" && Batch.PlainPick(it, track), label);
            var res = Batch.CleanItem(it, new CleanOptions())!;
            Media.Save(tl, it.Info, track, res.Render(), res.Doc.Fmt, new SaveOptions { Mode = "replace", KeepOriginal = false });
            after = Media.Probe(tl, v);
            var titles = after.Subs.Select(s => (s.Title, s.Default)).ToList();
            Check(titles.SequenceEqual([("English", true), ("English SDH", false), ("Español (SDH)", false)]), $"{label}: {string.Join(", ", titles)}");
            Check(after.Count("attachment") == 1 && after.Chapters == 2, label);
        }
    }

    public static void TestRenameAndOtherLanguagesTidy()
    {
        var (work, mkv, _) = PipelineFiles();
        var tools = ToolsFound;
        var info = Media.Probe(tools, mkv);
        var side = Path.Combine(work, "Test Movie (2024).eng.forced.srt");
        File.WriteAllText(side, "1\n00:00:01,000 --> 00:00:02,000\nHi.\n");
        var outp = Media.TidyOrRename(tools, info, new SaveOptions
        {
            Mode = "replace", DropOtherLanguages = true, RenameTo = "Test Movie.mkv", RenameSidecars = [side],
        });
        Check(outp.Path == Path.Combine(work, "Test Movie.mkv") && !File.Exists(mkv), outp.Path);
        Check(outp.Note.StartsWith("Removed 1 subtitle track in other languages.", StringComparison.Ordinal), outp.Note);
        Check(File.Exists(Path.Combine(work, "Test Movie.eng.forced.srt")), "the subtitle file next to it follows the new name");
        var after = Media.Probe(tools, outp.Path);
        Check(after.Subs.All(s => s.Language == "eng") && after.Subs.Count == 2);
        // a pure rename, then a clash that is refused with nothing changed
        outp = Media.TidyOrRename(tools, after, new SaveOptions { Mode = "replace", RenameTo = "Renamed.mkv" });
        Check(outp.Note.StartsWith("Renamed.", StringComparison.Ordinal) && File.Exists(Path.Combine(work, "Renamed.mkv")));
        File.WriteAllText(Path.Combine(work, "Taken.mkv"), "x");
        var before = Digest(work);
        var info2 = Media.Probe(tools, outp.Path);
        var e = Throws<ToolError>(() => Media.TidyOrRename(tools, info2, new SaveOptions { Mode = "replace", RenameTo = "Taken.mkv" }));
        Check(e.Message.Contains("already in the folder"), e.Message);
        Check(Same(before, Digest(work)), "nothing changed");
    }

    // ================================================================ awkward files (tests/test_edge.py), through the session

    private static Dictionary<string, string> EdgeCases()
    {
        NeedTools();
        var edge = Path.Combine(Root, "samples", "edge");
        if (!Directory.Exists(edge))
            throw new Skip("run \"python -m tests.make_samples --edge\" first");
        var dst = NewDir("edge");
        CopyDir(edge, dst);
        var cases = new Dictionary<string, string>();
        foreach (var d in Directory.EnumerateDirectories(dst))
        {
            var name = Path.GetFileName(d);
            cases[name] = Directory.EnumerateFiles(d, "*.mkv").First();
        }
        return cases;
    }

    /// <summary>Saves one video the way the Save button does (rewrite in place, best guess for uncertain lines).
    /// Returns the item's state and message ("told" when the save didn't start and only said why).</summary>
    private static (string State, string Message) SaveOne(string path, Tools? tools = null)
    {
        var settings = Path.Combine(Tmp, "settings-" + Guid.NewGuid().ToString("N") + ".json");
        var store = new FileStore(settings);
        foreach (var (k, v) in new[] { ("rename_on", "false"), ("drop_langs", "false"), ("confirm_replace", "false"), ("sub_mode", "replace") })
            store.Set(k, v);
        var s = new Session(new FileStore(settings));
        var done = new ManualResetEventSlim();
        s.Emit = (name, _) =>
        {
            if (name == "save_done")
                done.Set();
        };
        s.Start("tests");
        if (tools != null)
            s.Tools = tools;
        s.Add([path]);
        Check(s.WaitIdle(300), "reading finished");
        var answers = new JsonObject();
        for (var k = 0; k < 8; k++)
        {
            var q = s.Save(null, answers);
            if (q["tell"] is JsonObject tell)
            {
                s.Close();
                return ("told", tell["title"]!.GetValue<string>());
            }
            if (q["ask"] is not JsonObject ask)
                break;
            var key = ask["key"]!.GetValue<string>();
            if (key is "locks" or "lone")              // the worker in test_edge.py doesn't force regular subtitles either
            {
                s.Close();
                return ("asked", ask["title"]!.GetValue<string>());
            }
            answers[key] = key switch { "decision" => "guess", "lone" => "go", "rewrite" => "go", "overwrite" => "yes", _ => "keep" };
        }
        Check(done.Wait(TimeSpan.FromMinutes(5)), "the save finished");
        var it = s.Items[0];
        var result = (it.State, it.Message);
        s.Close();
        return result;
    }

    public static void TestNoSubtitles()
    {
        var p = EdgeCases()["no subtitles"];
        var before = Digest(Path.GetDirectoryName(p)!);
        var (state, _) = SaveOne(p);
        Check(state is "skipped" or "told", state);
        Check(Same(before, Digest(Path.GetDirectoryName(p)!)));
    }

    public static void TestPictureSubtitlesOnly()
    {
        var p = EdgeCases()["picture subtitles only"];
        var info = Media.Probe(ToolsFound, p);
        if (info.Subs.Count == 0 || info.Subs[0].TextBased)
            throw new Skip("the picture-subtitle sample could not be made on this machine");
        var before = Digest(Path.GetDirectoryName(p)!);
        var (state, msg) = SaveOne(p);
        Check(state == "skipped" && msg == "no text subtitles", $"{state}: {msg}");
        Check(Same(before, Digest(Path.GetDirectoryName(p)!)));
    }

    public static void TestTwoSdhTracksOneReplacesItsOriginal()
    {
        var p = EdgeCases()["two sdh"];
        var (state, msg) = SaveOne(p);
        Check(state == "done", $"{state}: {msg}");
        var after = Media.Probe(ToolsFound, p);
        Check(after.Subs.Count == 2 && after.Subs.Count(t => t.Default) == 1);
        Check(after.Subs.Where(t => t.Default).Select(t => t.Title).SequenceEqual(["English"]));
        Check(Leftovers(Path.GetDirectoryName(p)!).Count == 0);
    }

    public static void TestEmptySdhTrackChangesNothing()
    {
        var cases = EdgeCases();
        if (!cases.TryGetValue("empty sdh", out var p))
            throw new Skip("no mkvmerge");
        var before = Digest(Path.GetDirectoryName(p)!);
        var (state, msg) = SaveOne(p);
        Check(state is "skipped" or "told", $"{state}: {msg}");
        Check(Same(before, Digest(Path.GetDirectoryName(p)!)));
    }

    public static void TestTrackWhereEveryLineIsSdh()
    {
        var p = EdgeCases()["every line is sdh"];
        var before = Digest(Path.GetDirectoryName(p)!);
        var (state, msg) = SaveOne(p);
        Check(state == "skipped" && msg.Contains("every line"), $"{state}: {msg}");
        Check(Same(before, Digest(Path.GetDirectoryName(p)!)));
    }

    public static void TestAwkwardFileName()
    {
        var p = EdgeCases()["It's & 100% [Rock] Café 🎬"];
        var (state, msg) = SaveOne(p);
        Check(state == "done", $"{state}: {msg}");
        Check(File.Exists(p) && Leftovers(Path.GetDirectoryName(p)!).Count == 0);
        var after = Media.Probe(ToolsFound, p);
        Check(after.Subs.Select(t => t.Title).SequenceEqual(["English"]) && after.Subs[0].Default);
    }

    public static void TestAwkwardFileNameWithoutMkvmerge()
    {
        // as in test_edge.py: a copy of the file once saved, so FFmpeg's own path is used as well
        var p = EdgeCases()["It's & 100% [Rock] Café 🎬"];
        Check(SaveOne(p).State == "done");
        var copy = Path.Combine(NewDir("copy2"), Path.GetFileName(p));
        File.Copy(p, copy);
        p = copy;
        var (state, msg) = SaveOne(p, new Tools { Ffmpeg = ToolsFound.Ffmpeg, Ffprobe = ToolsFound.Ffprobe });
        Check(state is "done" or "skipped" or "asked", $"{state}: {msg}");
        Check(Leftovers(Path.GetDirectoryName(p)!).Count == 0);
    }

    public static void TestZeroByteSidecarIsIgnoredSafely()
    {
        var v = EdgeCases()["zero byte sidecar"];
        var before = Digest(Path.GetDirectoryName(v)!);
        var (state, msg) = SaveOne(v);
        Check(state is "skipped" or "told", $"{state}: {msg}");
        Check(Same(before, Digest(Path.GetDirectoryName(v)!)), "the video and the (empty) .srt are untouched");
    }

    public static void TestUtf16SidecarIsCleanedAndEmbedded()
    {
        var v = EdgeCases()["utf-16 sidecar"];
        var srt = Directory.EnumerateFiles(Path.GetDirectoryName(v)!, "*.srt").First();
        var (state, msg) = SaveOne(v);
        Check(state == "done", $"{state}: {msg}");
        Check(Media.Probe(ToolsFound, v).Subs.Select(t => t.Title).SequenceEqual(["English"]));
        Check(!File.Exists(srt), "the .srt goes to the Recycle Bin once it is inside the video");
    }

    public static void TestCp1252SidecarKeepsItsAccents()
    {
        var v = EdgeCases()["cp1252 sidecar"];
        var (state, msg) = SaveOne(v);
        Check(state == "done", $"{state}: {msg}");
        var info = Media.Probe(ToolsFound, v);
        var paths = Media.ExtractSubs(ToolsFound, info, info.Subs, NewDir("cp1252"));
        Check(File.ReadAllText(paths.Values.First(), Encoding.UTF8).Contains("Café is closed."));
    }

    public static void TestPathThatIsTooLongIsRefusedFirst()
    {
        if (PyPath.IsWin)
            throw new Skip("needs a path that Windows itself can't create here");
        var two = EdgeCases()["two sdh"];
        var d = Path.Combine(Tmp, "long", new string('x', 90), new string('x', 90), new string('x', 90));
        Directory.CreateDirectory(d);
        var v = Path.Combine(d, string.Concat(Enumerable.Repeat("Show with a rather long name ", 3)) + ".mkv");
        File.Copy(two, v);
        var before = Digest(d);
        Media.PathLimit = 259;
        var (state, msg) = SaveOne(v);
        Check(state == "failed" && msg.ToLowerInvariant().Contains("too long"), $"{state}: {msg}");
        Check(Same(before, Digest(d)));
    }

    public static void TestCancelLeavesNoTempFileAndTheOriginalAlone()
    {
        var p = EdgeCases()["two sdh"];
        var dir = Path.GetDirectoryName(p)!;
        var before = Digest(dir);
        // the new file exists at this point; the user presses Stop
        Media.ProbeFile = (tools, path, log) => path.Contains(Media.TmpMark) ? throw new Cancelled() : Media.Probe(tools, path, log);
        var (state, msg) = SaveOne(p);
        Check(state == "ready" && msg == "", $"{state}: {msg}");
        Check(Leftovers(dir).Count == 0 && Same(before, Digest(dir)));
    }

    public static void TestOpenInAnotherProgramIsRefusedFirst()
    {
        var p = EdgeCases()["two sdh"];
        var dir = Path.GetDirectoryName(p)!;
        var before = Digest(dir);
        Media.WhoHasOpen = paths => paths.Contains(p) ? new() { [p] = ["qBittorrent"] } : [];
        var (state, msg) = SaveOne(p);
        Check(state == "asked" && msg.Contains("qBittorrent"), $"{state}: {msg}");
        Check(Leftovers(dir).Count == 0 && Same(before, Digest(dir)));
    }

    public static void TestUndoRenames()
    {
        var d = NewDir("undo");
        string a = Path.Combine(d, "Old Name.mkv"), b = Path.Combine(d, "New Name.mkv");
        File.WriteAllText(b, "v");
        File.WriteAllText(Path.Combine(d, "New Name.eng.srt"), "s");
        RenameHistory.Record("b1", a, b);
        var s = new Session(new FileStore(Path.Combine(Tmp, "undo.json")));
        Check(s.UndoRenamesQuestion() != null);
        var r = s.UndoRenames();
        Check(r["title"]!.GetValue<string>() == "Renames undone", r.ToJsonString());
        Check(File.Exists(a) && File.Exists(Path.Combine(d, "Old Name.eng.srt")) && !File.Exists(b));
        Check(s.UndoRenamesQuestion() == null, "the batch is marked undone");
    }

    // ================================================================ TMDB and settings (tests/test_naming.py)

    public static void TestApiKeyAndErrors()
    {
        string? url = null, auth = null;
        var t = new Tmdb("0123456789abcdef0123456789abcdef") { Opener = (u, a) => { (url, auth) = (u, a); return (200, "{\"results\": []}"); } };
        t.SearchMovie("x");
        Check(url!.Contains("api_key=0123") && auth == null, url);
        t = new Tmdb("eyJ" + new string('a', 100)) { Opener = (u, a) => { (url, auth) = (u, a); return (200, "{\"results\": []}"); } };
        t.SearchMovie("y y");
        Check(!url!.Contains("api_key") && auth!.StartsWith("Bearer ", StringComparison.Ordinal) && url.Contains("query=y+y"), url);
        var e = Throws<TmdbError>(() => new Tmdb("bad") { Opener = (_, _) => (401, "") }.SearchMovie("x"));
        Check(e.Message.Contains("key"), e.Message);
        var calls = 0;
        e = Throws<TmdbError>(() => new Tmdb("k") { Opener = (_, _) => { calls++; return (429, ""); }, Sleep = _ => { } }.SearchMovie("x"));
        Check(calls == 3 && e.Message.StartsWith("Couldn't reach TMDB", StringComparison.Ordinal), e.Message);
        Check(new Tmdb("k") { Opener = (_, _) => (404, "") }.Movie(1).Count == 0, "404 is no result");
        calls = 0;
        var cached = new Tmdb("k") { Opener = (_, _) => { calls++; return (200, "{\"results\": [{\"id\": 1}]}"); } };
        cached.SearchMovie("Dune");
        cached.SearchMovie("Dune");
        Check(calls == 1, "answers are cached");
    }

    public static void TestDemoStandInFindsTheLibrary()
    {
        var m = Naming.MatchTitle(FakeTmdb.Demo(), "Severance.S02E04.Woes.Hollow.2160p.ATVP.WEB-DL.DDP5.1.mkv");
        Check(m.Confident && Naming.Render(m.Best!, ".mkv") == "Severance S2 E4.mkv", m.Best?.Label() ?? "none");
        m = Naming.MatchTitle(FakeTmdb.Demo(), "Dune.Part.Two.2024.2160p.UHD.BluRay.x265.mkv", 167 * 60);
        Check(m.Confident && Naming.Render(m.Best!, ".mkv") == "Dune - Part Two.mkv");
    }

    public static void TestSettingsInTheRegistry()
    {
        if (!OperatingSystem.IsWindows())
            throw new Skip("Windows only");
        var root = @"Software\SubScrub\EngineTests-" + Guid.NewGuid().ToString("N");
        try
        {
            var store = new RegistryStore(root);
            store.Set("theme", "dark");
            store.Set("track_name", "@home");
            store.Set("tools/ffmpeg", @"C:\ffmpeg\bin\ffmpeg.exe");
            Check(store.Get("theme") == "dark" && store.Get("track_name") == "@home" && store.Get("missing", "x") == "x");
            Check(store.Get("tools/ffmpeg") == @"C:\ffmpeg\bin\ffmpeg.exe");
            using (var k = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(root)!)
                Check((string)k.GetValue("track_name")! == "@@home", "QSettings' escaping of a leading @");
            var cfg = new Config(store) { TmdbKey = "abc", KeepSdh = true };
            cfg.Save();
            var again = new Config(new RegistryStore(root));
            Check(again.TmdbKey == "abc" && again.KeepSdh && again.Theme == "dark");
        }
        finally
        {
            Microsoft.Win32.Registry.CurrentUser.DeleteSubKeyTree(root, false);
        }
    }

    public static void TestTitleLookupsInTheSession()
    {
        var lib = Path.Combine(Root, "samples", "library");
        NeedTools();
        if (!Directory.Exists(lib))
            throw new Skip("run \"python -m tests.make_samples --library\" first");
        var dir = NewDir("names");
        foreach (var f in Directory.EnumerateFiles(lib, "Severance*"))
            File.Copy(f, Path.Combine(dir, Path.GetFileName(f)));
        var settings = Path.Combine(Tmp, "names.json");
        new FileStore(settings).Set("tmdb_key", "stand-in");
        var s = new Session(new FileStore(settings)) { TmdbFactory = _ => FakeTmdb.Demo() };
        s.Start("tests");
        s.Add([dir]);
        Check(s.WaitIdle(300));
        var row = s.FilesView()["rows"]![0]!;
        Check(row["rename"]!.GetValue<string>() == "Severance S2 E4.mkv", row.ToJsonString());
        var found = s.NameSearch(s.Items[0].Uid, "Slow Horses");
        Check(found["choices"]!.AsArray().Count == 2, found.ToJsonString());
        s.Close();
    }
}
