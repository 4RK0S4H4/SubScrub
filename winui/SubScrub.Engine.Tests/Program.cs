using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using SubScrub.Engine;

// The C# engine's tests on real videos (the swap, the pipeline, awkward files, saving, memory, two saves at once).
//   SubScrub.Engine.Tests <repository folder> [name filter]
//   SubScrub.Engine.Tests --qa-check <qa-dark.json> <pristine sample library>   what the WinUI QA run saved
if (args.Length == 3 && args[0] == "--qa-check")
    return QaCheck.Run(args[1], args[2]);
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
        Session.ReleaseSaved = true;
        Media.DriveOf = WinApi.DriveOf;
        Media.MaxParallelSaves = 2;
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

    // files SubScrub no longer needs (a .srt now inside the video): never deleted, kept when the Recycle Bin can't

    private static void Refuse(string p) => throw new IOException("no Recycle Bin here");

    public static void TestRecycleFailureKeepsTheFile()
    {
        var (src, _) = SwapSetup(Refuse);
        var srt = Path.Combine(Path.GetDirectoryName(src)!, "Show.en.sdh.srt");
        File.WriteAllText(srt, "x");
        var logged = new List<string>();
        var what = Media.Recycle(srt, logged.Add);
        Check(File.Exists(srt) && what.StartsWith(Media.Kept, StringComparison.Ordinal), what);
        Check(logged.Any(l => l.Contains(srt)), "the log says exactly where it is");
    }

    public static void TestNetworkDriveKeepsTheFile()
    {
        var (src, _) = SwapSetup(ToBin);
        Media.IsNetworkPath = _ => true;
        var srt = Path.Combine(Path.GetDirectoryName(src)!, "Show.en.sdh.srt");
        File.WriteAllText(srt, "x");
        Check(Media.Trash(srt) == "network drives have no Recycle Bin" && File.Exists(srt));
    }

    public static void TestBinReportsErrorButTheFileWent()
    {
        var (src, _) = SwapSetup(p =>
        {
            ToBin(p);
            throw new IOException("operation aborted");
        });
        var srt = Path.Combine(Path.GetDirectoryName(src)!, "Show.en.sdh.srt");
        File.WriteAllText(srt, "x");
        Check(Media.Recycle(srt) == "moved to the Recycle Bin");
    }

    public static void TestSubtitleReplacedInPlaceGoesToTheBin()
    {
        var (src, _) = SwapSetup(ToBin);
        var d = Path.GetDirectoryName(src)!;
        var srt = Path.Combine(d, "Show.srt");
        File.WriteAllText(srt, "old");
        var note = Media.ReplaceTextFile(srt, srt, "new", null);
        Check(File.ReadAllText(srt) == "new" && Bin[^1] == "old" && note.Contains("Recycle Bin"), note);
        Check(!File.Exists(Path.Combine(d, "Show" + Media.TmpMark + ".srt")), "no temporary copy left");
    }

    public static void TestSubtitleReplaceBinRefusesAndNothingChanges()
    {
        var (src, _) = SwapSetup(Refuse);
        var d = Path.GetDirectoryName(src)!;
        var srt = Path.Combine(d, "Show.srt");
        File.WriteAllText(srt, "old");
        var e = Throws<ToolError>(() => Media.ReplaceTextFile(srt, srt, "new", null));
        Check(e.Message.Contains("Nothing was changed"), e.Message);
        Check(File.ReadAllText(srt) == "old" && !File.Exists(Path.Combine(d, "Show" + Media.TmpMark + ".srt")));
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

    // ================================================================ memory: a saved file's tracks are packed away

    public static void TestPackedTracksComeBackExactly()
    {
        var vtt = Path.Combine(NewDir("pack"), "cues.vtt");
        File.WriteAllText(vtt, "WEBVTT\n\nNOTE a comment\n\nintro\n00:00:01.000 --> 00:00:02.000 align:start\n[door creaks]\nHi.\n\n"
                               + "00:00:03.000 --> 00:00:04.000\n- JOHN: Yo \ud83c\udfb5\n- Hey.\n");
        var files = Directory.EnumerateFiles(Path.Combine(Root, "tests", "fixtures"))
            .Concat(Directory.Exists(Path.Combine(Root, "samples")) ? Directory.EnumerateFiles(Path.Combine(Root, "samples"), "*", SearchOption.AllDirectories) : [])
            .Where(f => Path.GetExtension(f) is ".srt" or ".ass" or ".vtt").Append(vtt).ToList();
        Check(files.Count >= 4, "fixtures found");
        foreach (var f in files)
        {
            var doc = SubFormats.Load(f);
            if (f == vtt)
                doc.Cues[0].Text += "\ud800";              // a lone surrogate survives too
            var back = SubFormats.Unpack(SubFormats.Pack(doc));
            Check(back.Fmt == doc.Fmt && back.Newline == doc.Newline && back.Cues.Count == doc.Cues.Count
                  && back.Items.SequenceEqual(doc.Items), f);
            for (var i = 0; i < doc.Cues.Count; i++)
            {
                var (a, b) = (doc.Cues[i], back.Cues[i]);
                Check(a.Start == b.Start && a.End == b.End && a.Text == b.Text && a.Extra.Count == b.Extra.Count, $"{f} cue {i}");
            }
            var texts = doc.Cues.Select(c => (string?)c.Text).ToList();
            Check(back.Render(texts) == doc.Render(texts), f);
            var some = doc.Cues.Select((c, i) => i % 3 == 0 ? null : (string?)(c.Text + " x")).ToList();
            Check(back.Render(some) == doc.Render(some), f + " edited");
        }
    }

    /// <summary>Saves the library and returns every view of every file afterwards (folder names made the same).</summary>
    private static (List<string> Views, Session S) SaveAndLook(string lib, string name, bool release) =>
        SaveAndLook(lib, name, release, out _);

    private static (List<string> Views, Session S) SaveAndLook(string lib, string name, bool release, out List<string> log)
    {
        var dir = NewDir(name);
        CopyDir(lib, dir);
        var settings = Path.Combine(Tmp, name + ".json");
        new FileStore(settings).Set("tmdb_key", "stand-in");
        Session.ReleaseSaved = release;
        var s = new Session(new FileStore(settings)) { TmdbFactory = _ => FakeTmdb.Demo() };
        var done = new ManualResetEventSlim();
        s.Emit = (n, _) =>
        {
            if (n == "save_done")
                done.Set();
        };
        s.Start("tests");
        s.Add([dir]);
        Check(s.WaitIdle(300));
        foreach (var it in s.Items.ToList())
            s.Review(it.Uid, "changes");                 // as if each had been looked at
        var a = new JsonObject();
        for (var k = 0; k < 12; k++)
        {
            var q = s.Save(null, (JsonObject)a.DeepClone());
            if (q["ask"] is not JsonObject ask)
                break;
            var key = ask["key"]!.GetValue<string>();
            a[key] = key switch { "decision" => "guess", "names" => "keep", "overwrite" => "yes", "locks" => "skip", _ => "go" };
        }
        Check(done.Wait(TimeSpan.FromMinutes(10)), "the save finished");
        Check(s.WaitIdle(300));
        lock (s.LogLines)
            log = s.LogLines.Select(l => System.Text.RegularExpressions.Regex.Replace(l[10..].Replace(dir, "<dir>"), @"subscrub_(mux_|verify_)?\w+|took \d+ seconds?", "<varies>")).Where(l => !l.StartsWith("Saving up to", StringComparison.Ordinal)).ToList();
        var views = new List<string> { s.FilesView().ToJsonString() };
        if (release)
        {
            var shown = s.ReviewUid;
            Check(s.Items.Where(i => i.State == "done" && i.Uid != shown).All(i => i.Released), "saved files are released");
            Check(s.Items.Count(i => i.Released) >= 3, "most files were saved and released");
        }
        foreach (var it in s.Items.ToList())
            foreach (var show in new[] { "check", "changes", "all" })
                views.Add(s.Review(it.Uid, show).ToJsonString());
        views.Add(s.FilesView().ToJsonString());
        Session.ReleaseSaved = true;
        return (views.Select(v => NoUids(JsonNode.Parse(v.Replace(dir.Replace("\\", "\\\\"), "<dir>")))!.ToJsonString()).ToList(), s);
    }

    /// <summary>Files are numbered across sessions, so two sessions' views are compared without the numbers.</summary>
    private static JsonNode? NoUids(JsonNode? n)
    {
        if (n is JsonObject o)
        {
            foreach (var k in o.Select(kv => kv.Key).ToList())
            {
                if (k is "uid" or "review_uid")
                    o.Remove(k);
                else
                    NoUids(o[k]);
            }
        }
        else if (n is JsonArray a)
            foreach (var x in a)
                NoUids(x);
        return n;
    }

    public static void TestSavedFilesAreReleasedAndLookTheSame()
    {
        var lib = Path.Combine(Root, "samples", "library");
        NeedTools();
        if (!Directory.Exists(lib))
            throw new Skip("run \"python -m tests.make_samples --library\" first");
        var (kept, s1) = SaveAndLook(lib, "keep-all", false);
        var (freed, s2) = SaveAndLook(lib, "release", true);
        try
        {
            Check(kept.Count == freed.Count, "same number of views");
            for (var i = 0; i < kept.Count; i++)
                Check(kept[i] == freed[i], $"view {i} differs:\n{kept[i][..Math.Min(400, kept[i].Length)]}\n{freed[i][..Math.Min(400, freed[i].Length)]}");
            // looking at a saved file brings its data back; leaving it packs it away again
            var shown = s2.Items.First(i => i.State == "done");
            s2.Review(shown.Uid, "changes");
            Check(!shown.Released, "the file on screen is unpacked");
            s2.Review(s2.Items.First(i => i.Uid != shown.Uid).Uid, "changes");
            Check(shown.Released, "and packed again when it leaves the screen");
        }
        finally
        {
            s1.Close();
            s2.Close();
        }
    }

    // ================================================================ two files saving at once

    private static List<(string, string)> History() => RenameHistory.LastBatch().Select(r => (PyPath.Name(r.Old), PyPath.Name(r.New))).ToList();

    public static void TestTwoAtOnceGivesTheSameResultsInTheSameOrder()
    {
        var lib = Path.Combine(Root, "samples", "library");
        NeedTools();
        if (!Directory.Exists(lib))
            throw new Skip("run \"python -m tests.make_samples --library\" first");
        Media.DriveOf = _ => ("one disk", false);                 // a hard drive: one at a time
        var (one, s1) = SaveAndLook(lib, "one-by-one", true, out var log1);
        var history1 = History();
        Media.DriveOf = _ => ("ssd", true);                       // an SSD: two at once
        var (two, s2) = SaveAndLook(lib, "two-at-once", true, out var log2);
        var history2 = History();
        try
        {
            lock (s2.LogLines)
                Check(s2.LogLines.Count(l => l.Contains("Saving up to 2 files at a time")) == 1, "the second run saved two at a time");
            lock (s1.LogLines)
                Check(!s1.LogLines.Any(l => l.Contains("Saving up to")), "the first run saved one at a time");
            Check(one.Count == two.Count, "same number of views");
            for (var i = 0; i < one.Count; i++)
                Check(one[i] == two[i], $"view {i} differs");
            var (a, b) = (log1.SkipWhile(l => !l.StartsWith("Saving ", StringComparison.Ordinal)).ToList(),
                          log2.SkipWhile(l => !l.StartsWith("Saving ", StringComparison.Ordinal)).ToList());
            Check(a.Count > 3 && a.SequenceEqual(b), "the log is the same, in the same order:\n" + string.Join("\n", b.Except(a).Take(5)));
            Check(history1.SequenceEqual(history2), "the rename history is in the same order");
        }
        finally
        {
            s1.Close();
            s2.Close();
        }
    }

    public static void TestTwoAtOnceCancelLeavesNothingHalfDone()
    {
        var lib = Path.Combine(Root, "samples", "library");
        NeedTools();
        if (!Directory.Exists(lib))
            throw new Skip("run \"python -m tests.make_samples --library\" first");
        Media.DriveOf = _ => ("ssd", true);
        var dir = NewDir("cancel-two");
        CopyDir(lib, dir);
        var before = Digest(dir);
        var s = new Session(new FileStore(Path.Combine(Tmp, "cancel-two.json")));
        var done = new ManualResetEventSlim();
        s.Emit = (n, _) =>
        {
            if (n == "save_done")
                done.Set();
        };
        s.Start("tests");
        s.Add([dir]);
        Check(s.WaitIdle(300));
        var a = new JsonObject();
        for (var k = 0; k < 12; k++)
        {
            var q = s.Save(null, (JsonObject)a.DeepClone());
            if (q["ask"] is not JsonObject ask)
                break;
            var key = ask["key"]!.GetValue<string>();
            a[key] = key switch { "decision" => "guess", "names" => "keep", "overwrite" => "yes", "locks" => "skip", _ => "go" };
        }
        var watch = System.Diagnostics.Stopwatch.StartNew();
        while (s.Items.Count(i => i.State == "saving") < 2 && watch.Elapsed.TotalSeconds < 60 && !done.IsSet)
            Thread.Sleep(5);
        s.Cancel();
        Check(done.Wait(TimeSpan.FromMinutes(5)), "the save stopped");
        try
        {
            Check(Leftovers(dir).Count == 0, "no temporary copies left");
            var after = Digest(dir);
            foreach (var it in s.Items.Where(i => i.State != "done"))
            {
                var rel = Path.GetRelativePath(dir, it.Path);
                Check(after.TryGetValue(rel, out var h) && h == before[rel], $"{rel} is untouched");
                Check(it.State is "ready" or "queued" or "skipped", $"{rel}: {it.State}");
            }
            Check(s.Items.Any(i => i.State != "done"), "the cancel stopped the batch before the end");
        }
        finally
        {
            s.Close();
        }
    }
}
