using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace SubScrub.Engine;

/// <summary>A half-written "Name.subscrub-tmp.mkv" left by an interrupted save.</summary>
public sealed record Leftover(string Path, string? Original, long Size)
{
    public string Name => PyPath.Name(Path);
}

public static partial class Media
{
    // ================================================================ tools

    private static string Exe(string name) => PyPath.IsWin ? name + ".exe" : name;

    /// <summary>The app's own folder (tools may sit in "tools" next to it).</summary>
    public static string AppDir { get; set; } = AppContext.BaseDirectory.TrimEnd('\\', '/');

    /// <summary>PATH as it is now in the registry (winget installs update it, but a running app only sees the PATH
    /// it was started with).</summary>
    private static List<string> RegistryPathDirs()
    {
        var dirs = new List<string>();
        if (!OperatingSystem.IsWindows())
            return dirs;
        foreach (var (root, key) in new[]
                 {
                     (Microsoft.Win32.Registry.CurrentUser, @"Environment"),
                     (Microsoft.Win32.Registry.LocalMachine, @"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                 })
        {
            try
            {
                using var k = root.OpenSubKey(key);
                if (k?.GetValue("Path", null, Microsoft.Win32.RegistryValueOptions.DoNotExpandEnvironmentNames) is string val)
                    dirs.AddRange(val.Split(';').Where(p => Py.Strip(p).Length > 0).Select(Environment.ExpandEnvironmentVariables));
            }
            catch (Exception e) when (e is System.Security.SecurityException or IOException or UnauthorizedAccessException)
            {
            }
        }
        return dirs;
    }

    private static string Env(string name, string def) => Environment.GetEnvironmentVariable(name) is { Length: > 0 } v ? v : def;

    private static IEnumerable<string> Glob(string root, string pattern1, string? pattern2, string tail)
    {
        // glob(root\pattern1\pattern2\tail) for the two-level winget package folders
        var outList = new List<string>();
        try
        {
            if (!Directory.Exists(root))
                return outList;
            foreach (var a in Directory.EnumerateDirectories(root, pattern1).Order(PyFmt.Order))
            {
                if (pattern2 == null)
                {
                    var p = Path.Combine(a, tail);
                    if (Directory.Exists(p))
                        outList.Add(p);
                    continue;
                }
                foreach (var b in Directory.EnumerateDirectories(a, pattern2).Order(PyFmt.Order))
                {
                    var p = Path.Combine(b, tail);
                    if (Directory.Exists(p))
                        outList.Add(p);
                }
            }
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
        }
        return outList;
    }

    private static List<string> CandidateDirs()
    {
        var dirs = new List<string> { PyPath.Join(AppDir, "tools"), AppDir };
        dirs.AddRange((Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator));
        dirs.AddRange(RegistryPathDirs());
        if (PyPath.IsWin)
        {
            var la = Env("LOCALAPPDATA", "");
            var pf = Env("ProgramFiles", @"C:\Program Files");
            var pf86 = Env("ProgramFiles(x86)", @"C:\Program Files (x86)");
            var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            dirs.AddRange(
            [
                Path.Combine(la, "Microsoft", "WinGet", "Links"),
                Path.Combine(pf, "MKVToolNix"), Path.Combine(pf86, "MKVToolNix"),
                Path.Combine(la, "Programs", "MKVToolNix"),
                @"C:\ffmpeg\bin", Path.Combine(pf, "ffmpeg", "bin"),
                Path.Combine(Env("ProgramData", @"C:\ProgramData"), "chocolatey", "bin"),
                Path.Combine(home, "scoop", "shims"),
            ]);
            // winget "portable" packages (Gyan.FFmpeg, BtbN.FFmpeg...) unpack here
            var packages = Path.Combine(la, "Microsoft", "WinGet", "Packages");
            dirs.AddRange(Glob(packages, "*FFmpeg*", "*", "bin"));
            dirs.AddRange(Glob(packages, "*FFmpeg*", null, "bin"));
        }
        var seen = new HashSet<string>();
        var outList = new List<string>();
        foreach (var d in dirs)
        {
            if (string.IsNullOrEmpty(d))
                continue;
            string key;
            try
            {
                key = PyPath.Key(d);
            }
            catch (Exception e) when (e is ArgumentException or NotSupportedException or PathTooLongException)
            {
                continue;
            }
            if (seen.Add(key))
                outList.Add(d);
        }
        return outList;
    }

    private static bool IsFile(string p)
    {
        try
        {
            return File.Exists(p);
        }
        catch (Exception e) when (e is ArgumentException or IOException)
        {
            return false;
        }
    }

    /// <summary>shutil.which: the first executable on PATH.</summary>
    private static string? Which(string name)
    {
        var exts = PyPath.IsWin ? (Env("PATHEXT", ".COM;.EXE;.BAT;.CMD")).Split(';').Where(e => e.Length > 0).ToArray() : [""];
        foreach (var d in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator))
        {
            if (d.Length == 0)
                continue;
            foreach (var e in exts)
            {
                var p = Path.Combine(d, PyPath.IsWin && !name.EndsWith(e, StringComparison.OrdinalIgnoreCase) ? name + e : name);
                if (IsFile(p))
                    return p;
            }
        }
        return null;
    }

    public static string? FindTool(string name, string? overridePath = null, string? near = null)
    {
        if (!string.IsNullOrEmpty(overridePath) && IsFile(overridePath))
            return overridePath;
        if (!string.IsNullOrEmpty(near))
        {
            var p = PyPath.Join(PyPath.Dir(near), Exe(name));
            if (IsFile(p))
                return p;
        }
        foreach (var d in CandidateDirs())
        {
            var p = PyPath.Join(d, Exe(name));
            if (IsFile(p))
                return p;
        }
        return Which(name);
    }

    public static Tools FindTools(IReadOnlyDictionary<string, string>? overrides = null)
    {
        string? O(string k) => overrides != null && overrides.TryGetValue(k, out var v) && v.Length > 0 ? v : null;
        var ffmpeg = FindTool("ffmpeg", O("ffmpeg"));
        var ffprobe = FindTool("ffprobe", O("ffprobe"), ffmpeg);
        var mkvmerge = FindTool("mkvmerge", O("mkvmerge"));
        return new Tools { Ffmpeg = ffmpeg, Ffprobe = ffprobe, Mkvmerge = mkvmerge };
    }

    private static readonly Regex VersionRe = new(@"version (\S+)|v(\d+\.\d+(?:\.\d+)?)");

    public static string ToolVersion(string? path)
    {
        if (string.IsNullOrEmpty(path))
            return "";
        try
        {
            var flag = PyPath.Name(path).ToLowerInvariant().Contains("ff") ? "-version" : "--version";
            var (_, stdout, _) = RunRaw([path, flag], TimeSpan.FromSeconds(10));
            var first = stdout.Length > 0 ? stdout.Split('\n')[0].TrimEnd('\r') : "";
            var m = VersionRe.Match(first);
            return m.Success ? (m.Groups[1].Success ? m.Groups[1].Value : m.Groups[2].Value) : PyFmt.Head(first, 40);
        }
        catch (Exception)
        {
            return "";
        }
    }

    // ================================================================ processes

    private static ProcessStartInfo StartInfo(IReadOnlyList<string> cmd)
    {
        var info = new ProcessStartInfo(cmd[0])
        {
            UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true,
            RedirectStandardOutput = true, RedirectStandardError = true,
            StandardOutputEncoding = new UTF8Encoding(false), StandardErrorEncoding = new UTF8Encoding(false),
        };
        foreach (var a in cmd.Skip(1))
            info.ArgumentList.Add(a);
        // like Python (PEP 538): in the plain C locale, tools get a UTF-8 one, or they mangle accented file names
        if (!PyPath.IsWin)
        {
            var lc = Environment.GetEnvironmentVariable("LC_ALL") is { Length: > 0 } all ? all
                : Environment.GetEnvironmentVariable("LC_CTYPE") is { Length: > 0 } ct ? ct
                : Environment.GetEnvironmentVariable("LANG") ?? "";
            if (lc is "" or "C" or "POSIX")
                info.Environment["LC_CTYPE"] = "C.UTF-8";
        }
        return info;
    }

    public static string FmtCmd(IEnumerable<string> cmd) =>
        string.Join(" ", cmd.Select(c => c.Contains(' ') || c.Length == 0 ? $"\"{c}\"" : c));

    private static (int Code, string Stdout, string Stderr) RunRaw(IReadOnlyList<string> cmd, TimeSpan? timeout)
    {
        using var p = Process.Start(StartInfo(cmd)) ?? throw new ToolError($"{PyPath.Name(cmd[0])} didn't start");
        p.StandardInput.Close();
        var so = p.StandardOutput.ReadToEndAsync();
        var se = p.StandardError.ReadToEndAsync();
        if (!p.WaitForExit(timeout ?? Timeout.InfiniteTimeSpan))
        {
            try
            {
                p.Kill(true);
            }
            catch (InvalidOperationException)
            {
            }
            throw new TimeoutException($"{PyPath.Name(cmd[0])} timed out after {timeout!.Value.TotalSeconds:0} seconds");
        }
        p.WaitForExit();
        return (p.ExitCode, so.Result, se.Result);
    }

    public static string RunCapture(IReadOnlyList<string> cmd, Action<string>? log = null, double? timeoutSec = null)
    {
        log?.Invoke("$ " + FmtCmd(cmd));
        var (code, stdout, stderr) = RunRaw(cmd, timeoutSec is { } t ? TimeSpan.FromSeconds(t) : null);
        if (code != 0)
        {
            var msg = Py.Strip(stderr.Length > 0 ? stderr : stdout);
            throw new ToolError($"{PyPath.Name(cmd[0])} failed (exit {code}): {Tail(msg, 800)}");
        }
        return stdout;
    }

    private static string Tail(string s, int n)
    {
        var cps = PyFmt.CodePoints(s);
        return cps.Length <= n ? s : PyFmt.FromCodePoints(cps, cps.Length - n, cps.Length);
    }

    private static readonly string[] Noise =
    [
        "out_time", "frame=", "fps=", "stream_", "bitrate=", "total_size=", "dup_frames", "drop_frames", "speed=",
        "progress=", "#GUI#progress",
    ];

    /// <summary>Run a tool, feeding each output line (stdout and stderr) to onLine. Kills it when cancelled.</summary>
    public static List<string> RunStream(IReadOnlyList<string> cmd, Action<string> onLine, Action<string>? log = null,
                                         CancellationToken cancel = default, int[]? okCodes = null)
    {
        log?.Invoke("$ " + FmtCmd(cmd));
        using var p = Process.Start(StartInfo(cmd)) ?? throw new ToolError($"{PyPath.Name(cmd[0])} didn't start");
        p.StandardInput.Close();
        var tail = new List<string>();
        var gate = new object();

        void Feed(StreamReader r)
        {
            while (r.ReadLine() is { } line)
            {
                lock (gate)
                {
                    onLine(line);
                    if (line.Length > 0 && !Noise.Any(n => line.StartsWith(n, StringComparison.Ordinal)))
                    {
                        tail.Add(line);
                        if (tail.Count > 60)
                            tail.RemoveAt(0);
                    }
                }
            }
        }
        using var reg = cancel.Register(() =>
        {
            try
            {
                p.Kill(true);
            }
            catch (InvalidOperationException)
            {
            }
        });
        var a = Task.Run(() => Feed(p.StandardOutput));
        var b = Task.Run(() => Feed(p.StandardError));
        Task.WaitAll(a, b);
        p.WaitForExit();
        if (cancel.IsCancellationRequested)
            throw new Cancelled();
        if (!(okCodes ?? [0]).Contains(p.ExitCode))
        {
            var detail = Py.Strip(string.Join("\n", tail.TakeLast(15)));
            throw new ToolError($"{PyPath.Name(cmd[0])} failed (exit {p.ExitCode}).\n{detail}");
        }
        if (log != null && tail.Count > 0)
            log(string.Join("\n", tail.TakeLast(15)));
        return tail;
    }

    // ================================================================ probing

    private static readonly Regex PlainHandler = new(@"\A(?:(Subtitle|Text|Sub)Handler|.*Media Handler|)\z",
                                                     RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

    private static string JStr(JsonNode? n) => n switch
    {
        JsonValue v when v.TryGetValue<string>(out var s) => s,
        null => "",
        _ => n.ToJsonString(),
    };

    /// <summary>float(x) of a JSON value, as Python reads ffprobe's numbers ("12.5", 12.5).</summary>
    private static double? PyFloat(JsonNode? n)
    {
        if (n is not JsonValue v)
            return null;
        if (v.TryGetValue<double>(out var d))
            return d;
        if (v.TryGetValue<string>(out var s))
        {
            var t = Py.Strip(s).Replace("_", "");
            if (double.TryParse(t, NumberStyles.Float, CultureInfo.InvariantCulture, out var x))
                return x;
            var low = t.ToLowerInvariant().TrimStart('+', '-');
            if (low is "inf" or "infinity" or "nan")
                return t.StartsWith('-') ? (low == "nan" ? double.NaN : double.NegativeInfinity) : (low == "nan" ? double.NaN : double.PositiveInfinity);
        }
        return null;
    }

    public static MediaInfo Probe(Tools tools, string path, Action<string>? log = null)
    {
        if (string.IsNullOrEmpty(tools.Ffprobe))
            throw new ToolError("FFprobe was not found. Install FFmpeg first.");
        var outText = RunCapture([tools.Ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                                  "-show_chapters", path], log, 120);
        var data = JsonNode.Parse(outText.Length > 0 ? outText : "{}")?.AsObject() ?? [];
        var streams = (data["streams"] as JsonArray ?? []).Select(s => s!.DeepClone().AsObject()).ToList();
        var fmt = data["format"] as JsonObject ?? [];
        var subs = new List<SubTrack>();
        foreach (var s in streams)
        {
            if (JStr(s["codec_type"]) != "subtitle")
                continue;
            var tags = new Dictionary<string, string>();
            foreach (var (k, v) in s["tags"] as JsonObject ?? [])
                tags[k.ToLowerInvariant()] = JStr(v);
            var disp = new Dictionary<string, int>();
            foreach (var (k, v) in s["disposition"] as JsonObject ?? [])
            {
                if (v is JsonValue jv && jv.GetValueKind() == JsonValueKind.Number && jv.TryGetValue<int>(out var i))
                    disp[k] = i;
                else if (v is JsonValue bv && bv.GetValueKind() is JsonValueKind.True or JsonValueKind.False)
                    disp[k] = bv.GetValue<bool>() ? 1 : 0;
            }
            var title = tags.GetValueOrDefault("title") ?? "";
            if (title.Length == 0)
            {
                var h = tags.GetValueOrDefault("handler_name") ?? "";
                if (h.Length > 0 && !PlainHandler.IsMatch(h))
                    title = h;
            }
            var lang = tags.GetValueOrDefault("language");
            subs.Add(new SubTrack
            {
                Index = s["index"] is JsonValue iv && iv.TryGetValue<int>(out var idx) ? idx : 0, SubPos = subs.Count,
                Codec = JStr(s["codec_name"]), Language = (string.IsNullOrEmpty(lang) ? "und" : lang).ToLowerInvariant(),
                Title = title, Default = disp.GetValueOrDefault("default") != 0, Forced = disp.GetValueOrDefault("forced") != 0,
                HearingImpaired = disp.GetValueOrDefault("hearing_impaired") != 0, Disposition = disp,
                Cleaned = (tags.GetValueOrDefault("subscrub") ?? "").ToLowerInvariant() == "cleaned",
            });
        }
        var duration = PyFloat(fmt["duration"]) ?? 0.0;
        long size = 0;
        if (fmt["size"] is JsonValue sv)
            size = sv.TryGetValue<long>(out var l) ? l : long.Parse(Py.Strip(JStr(sv)), CultureInfo.InvariantCulture);
        if (size == 0)
            size = new FileInfo(path).Length;
        return new MediaInfo
        {
            Path = path, Kind = ContainerKind(path), FormatName = fmt.ContainsKey("format_name") ? JStr(fmt["format_name"]) : "",
            Duration = duration, Size = size, Streams = streams, Subs = subs,
            Chapters = (data["chapters"] as JsonArray)?.Count ?? 0,
        };
    }

    // ================================================================ extraction

    private static Action<string> FfmpegProgress(double duration, Action<double, string>? onProgress, string label) => line =>
    {
        if (onProgress == null)
            return;
        if (line.StartsWith("out_time_us=", StringComparison.Ordinal) || line.StartsWith("out_time_ms=", StringComparison.Ordinal))
        {
            if (!long.TryParse(line[(line.IndexOf('=') + 1)..], NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out var us))
                return;
            if (duration > 0 && us > 0)
                onProgress(Math.Min(0.999, us / 1e6 / duration), label);
        }
    };

    /// <summary>Pull all given text tracks out in a single read of the file. Returns {stream index: path}.</summary>
    public static Dictionary<int, string> ExtractSubs(Tools tools, MediaInfo info, IEnumerable<SubTrack> trackList, string outDir,
                                                      Action<double, string>? onProgress = null, CancellationToken cancel = default,
                                                      Action<string>? log = null)
    {
        var tracks = trackList.Where(t => t.TextBased).ToList();
        if (tracks.Count == 0)
            return [];
        Directory.CreateDirectory(outDir);
        var paths = tracks.ToDictionary(t => t.Index, t => PyPath.Join(outDir, $"track{t.Index}.{t.Fmt}"));
        var cmd = new List<string>
        {
            tools.Ffmpeg ?? "ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats",
            "-i", info.Path,
        };
        foreach (var t in tracks)
            cmd.AddRange(["-map", $"0:{t.Index}", "-c:s", TextCodecs[t.Codec].Codec, paths[t.Index]]);
        try
        {
            RunStream(cmd, FfmpegProgress(info.Duration, onProgress, "Reading subtitles"), log, cancel);
        }
        catch (ToolError)
        {
            if (tracks.Count == 1)
                throw;
            // one bad track shouldn't sink the rest: retry them one by one
            log?.Invoke("Combined extraction failed; retrying tracks one at a time.");
            var good = new Dictionary<int, string>();
            foreach (var t in tracks)
            {
                try
                {
                    foreach (var (k, v) in ExtractSubs(tools, info, [t], outDir, onProgress, cancel, log))
                        good[k] = v;
                }
                catch (ToolError e)
                {
                    log?.Invoke($"Track {t.Index}: {e.Message}");
                }
            }
            return good;
        }
        return paths.Where(kv => Path.Exists(kv.Value)).ToDictionary(kv => kv.Key, kv => kv.Value);
    }

    // ================================================================ leftovers of an interrupted save

    public static readonly string[] StaleTempPrefixes = ["subscrub_mux_", "subscrub_verify_", "subscrub_"];

    private static Leftover? LeftoverOf(string path)
    {
        var name = PyPath.Name(path);
        var at = name.IndexOf(TmpMark, StringComparison.Ordinal);
        var stem = at >= 0 ? name[..at] : name;
        var suffix = PyPath.Suffix(path);
        if (at < 0 || !VideoExts.Contains(suffix.ToLowerInvariant()) || stem.Length == 0)
            return null;
        long size;
        try
        {
            size = new FileInfo(path).Length;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return null;
        }
        if (!File.Exists(path))
            return null;
        var exts = new HashSet<string>(VideoExts) { suffix }.Order(PyFmt.Order);
        var orig = exts.Select(e => PyPath.WithName(path, stem + e)).FirstOrDefault(File.Exists);
        return new Leftover(path, orig, size);
    }

    /// <summary>Unfinished "Name.subscrub-tmp.ext" files in the folders given, or next to the files given.</summary>
    public static List<Leftover> FindLeftovers(IEnumerable<string> paths, int limit = 2000)
    {
        var seen = new Dictionary<string, Leftover>();

        void Look(string folder, HashSet<string>? onlyStems = null)
        {
            List<string> names;
            try
            {
                names = Directory.EnumerateFileSystemEntries(folder.Length > 0 ? folder : ".").Select(PyPath.Name).ToList();
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException or ArgumentException)
            {
                return;
            }
            foreach (var n in names)
            {
                var at = n.IndexOf(TmpMark, StringComparison.Ordinal);
                if (at >= 0 && (onlyStems == null || onlyStems.Contains(n[..at].ToLowerInvariant())))
                {
                    var lo = LeftoverOf(PyPath.Join(folder, n));
                    if (lo != null)
                        seen[PyPath.Key(lo.Path)] = lo;
                }
            }
        }

        foreach (var p in paths)
        {
            if (Directory.Exists(p))
            {
                var count = 0;
                foreach (var root in WalkDirs(p))
                {
                    Look(root);
                    if (++count >= limit)
                        break;
                }
            }
            else if (File.Exists(p))
            {
                if (PyPath.Name(p).Contains(TmpMark))
                {
                    var lo = LeftoverOf(p);
                    if (lo != null)
                        seen[PyPath.Key(lo.Path)] = lo;
                }
                else
                    Look(PyPath.Dir(PyPath.Abs(p)), [PyPath.Stem(p).ToLowerInvariant()]);
            }
        }
        return seen.Values.OrderBy(x => x.Path.ToLowerInvariant(), PyFmt.Order).ToList();
    }

    /// <summary>os.walk's folders, top-down, sorted, not following links.</summary>
    public static IEnumerable<string> WalkDirs(string root)
    {
        yield return root;
        List<string> dirs;
        try
        {
            dirs = Directory.EnumerateDirectories(root).Select(PyPath.Name)
                .Where(n => new DirectoryInfo(PyPath.Join(root, n)).LinkTarget == null).Order(PyFmt.Order).ToList();
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            yield break;
        }
        foreach (var d in dirs)
        {
            foreach (var sub in WalkDirs(PyPath.Join(root, d)))
                yield return sub;
        }
    }

    /// <summary>The original is gone, so the half-written copy may be all that is left: give it its real name back.
    /// Never overwrites anything.</summary>
    public static string RestoreLeftover(Leftover lo)
    {
        var name = PyPath.Name(lo.Path);
        var target = PyPath.WithName(lo.Path, name[..name.IndexOf(TmpMark, StringComparison.Ordinal)] + PyPath.Suffix(lo.Path));
        if (Path.Exists(target))
            throw new ToolError($"“{PyPath.Name(target)}” already exists, so “{name}” was left as it is.");
        File.Move(lo.Path, target);
        return target;
    }

    /// <summary>Delete SubScrub's own working folders in %TEMP% that a crash left behind (older than a day, so a
    /// second running copy is never touched).</summary>
    public static int RemoveStaleTempDirs(double maxAge = 86400.0, string? root = null)
    {
        root ??= Path.GetTempPath();
        var removed = 0;
        var cutoff = DateTime.UtcNow.AddSeconds(-maxAge);
        List<string> names;
        try
        {
            names = Directory.EnumerateFileSystemEntries(root).Select(PyPath.Name).ToList();
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return 0;
        }
        foreach (var n in names)
        {
            var full = Path.Combine(root, n);
            if (!StaleTempPrefixes.Any(n.StartsWith) || !Directory.Exists(full) || new DirectoryInfo(full).LinkTarget != null)
                continue;
            try
            {
                if (Directory.GetLastWriteTimeUtc(full) < cutoff)
                {
                    try
                    {
                        Directory.Delete(full, true);
                    }
                    catch (Exception e) when (e is IOException or UnauthorizedAccessException)
                    {
                    }
                    removed += Directory.Exists(full) ? 0 : 1;
                }
            }
            catch (IOException)
            {
            }
        }
        return removed;
    }

    /// <summary>Show a file in Explorer (or its folder elsewhere).</summary>
    public static void OpenInExplorer(string path)
    {
        if (PyPath.IsWin)
            Process.Start(new ProcessStartInfo("explorer.exe", $"/select,\"{Path.GetFullPath(path)}\"") { UseShellExecute = false });
        else
            Process.Start(new ProcessStartInfo("xdg-open", PyPath.Dir(path)) { UseShellExecute = false });
    }
}
