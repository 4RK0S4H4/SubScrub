using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace SubScrub.Engine;

/// <summary>The update check didn't work; the message is for the user.</summary>
public sealed class UpdateError(string message) : Exception(message);

public sealed record Update(string Version, string Page, string InstallerUrl = "", long InstallerSize = 0, string Sha256 = "")
{
    public bool CanInstall => InstallerUrl.Length > 0;
}

/// <summary>Finding out whether a newer SubScrub exists, and installing it (subscrub/updates.py). The newest GitHub
/// Release is the source of truth. If it has a "SubScrub-Setup.exe", an installed copy downloads it, checks its size
/// and SHA-256, runs it quietly and quits (the installer starts the new version). Anything else opens the Releases
/// page. Nothing is downloaded without a click.</summary>
public static class Updates
{
    public const string Repo = "4RK0S4H4/SubScrub";
    public const string ApiUrl = $"https://api.github.com/repos/{Repo}/releases/latest";
    public const string ReleasesUrl = $"https://github.com/{Repo}/releases/latest";
    public const string InstallerName = "SubScrub-Setup.exe";
    public static readonly string[] SilentArgs = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"];
    private static readonly HttpClient Http = new() { Timeout = Timeout.InfiniteTimeSpan };

    /// <summary>For the tests: url -> (status, body bytes). Null = the network.</summary>
    public static Func<string, (int Status, byte[] Body)>? Opener { get; set; }

    /// <summary>'v2.9.0' / '2.9' -> [2, 9, 0] / [2, 9]. Null when it isn't a version.</summary>
    public static int[]? ParseVersion(string? text)
    {
        var m = Regex.Match(text ?? "", @"\A\s*v?(\d+(?:\.\d+){0,3})\s*\z");
        return m.Success ? m.Groups[1].Value.Split('.').Select(x => int.Parse(x, System.Globalization.CultureInfo.InvariantCulture)).ToArray() : null;
    }

    public static bool IsNewer(string latest, string current)
    {
        var a = ParseVersion(latest);
        var b = ParseVersion(current);
        if (a == null || b == null)
            return false;
        var n = Math.Max(a.Length, b.Length);
        for (var i = 0; i < n; i++)
        {
            int x = i < a.Length ? a[i] : 0, y = i < b.Length ? b[i] : 0;
            if (x != y)
                return x > y;
        }
        return false;
    }

    private static (int, byte[]) Get(string url, string accept, TimeSpan timeout)
    {
        if (Opener != null)
            return Opener(url);
        using var req = new HttpRequestMessage(HttpMethod.Get, url);
        req.Headers.TryAddWithoutValidation("Accept", accept);
        req.Headers.TryAddWithoutValidation("User-Agent", "SubScrub");
        using var cts = new CancellationTokenSource(timeout);
        using var resp = Http.Send(req, cts.Token);
        using var ms = new MemoryStream();
        resp.Content.ReadAsStream(cts.Token).CopyTo(ms);
        return ((int)resp.StatusCode, ms.ToArray());
    }

    /// <summary>The newer release, or null when this is the newest. Throws UpdateError with a plain message.</summary>
    public static Update? Check(string current, double timeout = 10.0)
    {
        int status;
        byte[] body;
        try
        {
            (status, body) = Get(ApiUrl, "application/vnd.github+json", TimeSpan.FromSeconds(timeout));
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException or IOException)
        {
            throw new UpdateError($"Couldn't reach GitHub ({e.InnerException?.Message ?? e.Message}).");
        }
        if (status == 404)
            return null;                                   // no release published yet
        if (status is 403 or 429)
            throw new UpdateError("GitHub is limiting requests right now. Try again in a little while.");
        if (status < 200 || status >= 300)
            throw new UpdateError($"GitHub answered with an error ({status}).");
        JsonObject? data;
        try
        {
            data = JsonNode.Parse(body) as JsonObject;
        }
        catch (System.Text.Json.JsonException)
        {
            throw new UpdateError("GitHub's answer couldn't be read.");
        }
        if (data == null || Config.Truthy(data["draft"]) || Config.Truthy(data["prerelease"]))
            return null;
        var tag = data["tag_name"] is JsonValue tv && tv.TryGetValue<string>(out var t) ? t : "";
        if (!IsNewer(tag, current))
            return null;
        var version = string.Join(".", ParseVersion(tag)!);
        var page = data["html_url"] is JsonValue hv && hv.TryGetValue<string>(out var h) && h.Length > 0 ? h : ReleasesUrl;
        var upd = new Update(version, page);
        foreach (var a in data["assets"] as JsonArray ?? [])
        {
            if (a is not JsonObject o || o["name"]?.GetValue<string>() != InstallerName)
                continue;
            if (o["browser_download_url"] is not JsonValue uv || !uv.TryGetValue<string>(out var url) || url.Length == 0)
                continue;
            var digest = o["digest"] is JsonValue dv && dv.TryGetValue<string>(out var d) ? d : "";
            upd = upd with
            {
                InstallerUrl = url,
                InstallerSize = o["size"] is JsonValue sv && sv.TryGetValue<long>(out var sz) ? sz : 0,
                Sha256 = digest.ToLowerInvariant().StartsWith("sha256:", StringComparison.Ordinal) ? digest[7..].ToLowerInvariant() : "",
            };
        }
        return upd;
    }

    /// <summary>Running from an installed copy (the installer puts an uninstaller next to the exe).</summary>
    public static bool IsInstalled() => File.Exists(Path.Combine(AppContext.BaseDirectory, "unins000.exe"));

    /// <summary>Download the installer into a fresh folder and check it. Returns its path.</summary>
    public static string Download(Update upd, Action<double>? onProgress = null, CancellationToken cancel = default)
    {
        if (!upd.CanInstall)
            throw new UpdateError("This release has no installer attached.");
        var folder = Directory.CreateTempSubdirectory("subscrub_update_").FullName;
        var dest = Path.Combine(folder, InstallerName);
        byte[] data;
        try
        {
            if (Opener != null)
                data = Opener(upd.InstallerUrl).Body;
            else
            {
                using var req = new HttpRequestMessage(HttpMethod.Get, upd.InstallerUrl);
                req.Headers.TryAddWithoutValidation("Accept", "application/octet-stream");
                req.Headers.TryAddWithoutValidation("User-Agent", "SubScrub");
                using var resp = Http.Send(req, HttpCompletionOption.ResponseHeadersRead, cancel);
                if (!resp.IsSuccessStatusCode)
                    throw new UpdateError($"The download didn't finish (HTTP {(int)resp.StatusCode}).");
                var total = resp.Content.Headers.ContentLength ?? upd.InstallerSize;
                using var src = resp.Content.ReadAsStream(cancel);
                using var ms = new MemoryStream();
                var buf = new byte[1 << 16];
                int n;
                while ((n = src.Read(buf, 0, buf.Length)) > 0)
                {
                    if (cancel.IsCancellationRequested)
                        throw new UpdateError("The download was stopped.");
                    ms.Write(buf, 0, n);
                    if (onProgress != null && total > 0)
                        onProgress(Math.Min(1.0, ms.Length / (double)total));
                }
                data = ms.ToArray();
            }
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException or IOException)
        {
            throw new UpdateError($"The download didn't finish ({e.InnerException?.Message ?? e.Message}).");
        }
        if (upd.InstallerSize != 0 && data.Length != upd.InstallerSize)
            throw new UpdateError("The download is incomplete, so it wasn't installed.");
        if (upd.Sha256.Length > 0 && Convert.ToHexString(SHA256.HashData(data)).ToLowerInvariant() != upd.Sha256)
            throw new UpdateError("The download doesn't match its checksum, so it wasn't installed.");
        File.WriteAllBytes(dest, data);
        return dest;
    }

    /// <summary>Start the installer quietly and leave it running on its own; the caller quits the app right after.</summary>
    public static void RunInstaller(string path)
    {
        var info = new ProcessStartInfo(path) { UseShellExecute = false, CreateNoWindow = true };
        foreach (var a in SilentArgs)
            info.ArgumentList.Add(a);
        Process.Start(info);
    }

    /// <summary>Open a web page in the default browser.</summary>
    public static void OpenPage(string url) => Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
}
