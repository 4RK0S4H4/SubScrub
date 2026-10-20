using System.IO.Compression;
using System.Text;
using System.Text.Json.Nodes;

namespace SubScrub.Engine;

/// <summary>Files already read: what ffprobe said and the subtitle tracks as extracted, byte for byte, so adding an
/// unchanged file again skips FFmpeg and gives exactly what reading it fresh would (the same parsing runs on the same
/// bytes). Kept in %LOCALAPPDATA%\SubScrub\cache, one file per video, keyed by the full path, size, modified time,
/// the reader (mkvextract or FFmpeg) and this build of the engine; any change misses and the entry ages out. Capped
/// at 200 MB, least recently used first.</summary>
public static class ReadCache
{
    public static long Cap { get; set; } = 200L * 1024 * 1024;

    public static bool Enabled { get; set; } = true;

    private static readonly object Gate = new();

    private static readonly string Build = typeof(ReadCache).Assembly.ManifestModule.ModuleVersionId.ToString("N");

    public sealed record Entry(string Probe, Dictionary<int, byte[]> Tracks);

    public static string Folder
    {
        get
        {
            var b = Environment.GetEnvironmentVariable("LOCALAPPDATA");
            if (string.IsNullOrEmpty(b))
                b = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".local", "share");
            return Path.Combine(b, "SubScrub", "cache");
        }
    }

    /// <summary>Which programs read the file: a different ffprobe or reader is a different entry.</summary>
    public static string Reader(Tools tools) => $"{tools.Ffprobe}|{(string.IsNullOrEmpty(tools.Mkvextract) ? "ffmpeg" : "mkvextract")}";

    private static string? Key(string path, string reader)
    {
        try
        {
            var f = new FileInfo(Path.GetFullPath(path));
            return f.Exists ? $"{f.FullName}|{f.Length}|{f.LastWriteTimeUtc.Ticks}|{reader}|{Build}" : null;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or ArgumentException)
        {
            return null;
        }
    }

    private static string FileFor(string key) =>
        Path.Combine(Folder, Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(Encoding.UTF8.GetBytes(key)))[..32] + ".bin");

    public static Entry? Get(string path, string reader)
    {
        if (!Enabled || Key(path, reader) is not { } key)
            return null;
        var file = FileFor(key);
        try
        {
            lock (Gate)
            {
                if (!File.Exists(file))
                    return null;
                using var z = new DeflateStream(File.OpenRead(file), CompressionMode.Decompress);
                var data = JsonNode.Parse(z)!;
                if (data["key"]?.GetValue<string>() != key)
                    return null;
                File.SetLastWriteTimeUtc(file, DateTime.UtcNow);          // recently used
                return new Entry(data["probe"]!.GetValue<string>(),
                                 data["tracks"]!.AsObject().ToDictionary(kv => int.Parse(kv.Key), kv => Convert.FromBase64String(kv.Value!.GetValue<string>())));
            }
        }
        catch (Exception)
        {
            return null;                                                   // a damaged entry is a miss
        }
    }

    public static void Put(string path, string reader, string probe, Dictionary<int, string> tracks)
    {
        if (!Enabled || Key(path, reader) is not { } key)
            return;
        try
        {
            var data = new JsonObject
            {
                ["key"] = key, ["probe"] = probe,
                ["tracks"] = new JsonObject(tracks.Select(kv => KeyValuePair.Create(kv.Key.ToString(), (JsonNode?)Convert.ToBase64String(File.ReadAllBytes(kv.Value))))),
            };
            lock (Gate)
            {
                Directory.CreateDirectory(Folder);
                var file = FileFor(key);
                var tmp = file + ".part";
                using (var z = new DeflateStream(File.Create(tmp), CompressionLevel.Fastest))
                    z.Write(Encoding.UTF8.GetBytes(data.ToJsonString()));
                File.Move(tmp, file, true);
                Trim();
            }
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
        }
    }

    /// <summary>Drops the least recently used entries until the cache is under its cap.</summary>
    private static void Trim()
    {
        var files = new DirectoryInfo(Folder).GetFiles("*.bin").OrderBy(f => f.LastWriteTimeUtc).ToList();
        var total = files.Sum(f => f.Length);
        foreach (var f in files)
        {
            if (total <= Cap)
                break;
            total -= f.Length;
            f.Delete();
        }
    }

    /// <summary>Empties the cache. Returns how many bytes it held.</summary>
    public static long Clear()
    {
        lock (Gate)
        {
            if (!Directory.Exists(Folder))
                return 0;
            long n = 0;
            foreach (var f in new DirectoryInfo(Folder).GetFiles())
            {
                n += f.Length;
                try
                {
                    f.Delete();
                }
                catch (Exception e) when (e is IOException or UnauthorizedAccessException)
                {
                }
            }
            return n;
        }
    }
}
