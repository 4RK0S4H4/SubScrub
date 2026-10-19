using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Dispatching;
using SubScrub.Models;

namespace SubScrub.Services;

/// <summary>The engine: subscrub-core.exe (the Python cleaner, muxing, TMDB and safe swap, built with PyInstaller
/// and shipped in the "core" folder next to SubScrub.exe), driven with one JSON object per line over stdin/stdout.
/// See subscrub/helper.py for the protocol. Replies come back as tasks; events ("files", "review_changed",
/// "save_done") are raised on the UI thread.</summary>
public sealed class Core : IDisposable
{
    public static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        PropertyNameCaseInsensitive = true,
        NumberHandling = System.Text.Json.Serialization.JsonNumberHandling.AllowReadingFromString,
    };

    private readonly Process _process;
    private readonly DispatcherQueue _ui;
    private readonly ConcurrentDictionary<int, TaskCompletionSource<JsonNode?>> _pending = new();
    private readonly SemaphoreSlim _write = new(1, 1);
    private readonly StringBuilder _stderr = new();
    private int _nextId;

    public event Action<FilesView>? Files;
    public event Action<int>? ReviewChanged;
    public event Action<SaveDone>? SaveDone;
    public event Action<string>? Died;

    public Hello? Ready { get; private set; }
    private readonly TaskCompletionSource<Hello> _ready = new();
    public Task<Hello> WhenReady => _ready.Task;

    private Core(Process process, DispatcherQueue ui)
    {
        _process = process;
        _ui = ui;
    }

    /// <summary>Starts the engine. Looks for core\subscrub-core.exe next to the app; from a source checkout it
    /// runs "python -m subscrub.helper" in the repository instead (or SUBSCRUB_CORE, a full command line).</summary>
    public static Core Start(DispatcherQueue ui, IEnumerable<string> extraArgs)
    {
        var (file, args, dir) = Locate();
        var info = new ProcessStartInfo(file)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = new UTF8Encoding(false),
            StandardErrorEncoding = new UTF8Encoding(false),
            WorkingDirectory = dir,
        };
        foreach (var a in args.Concat(extraArgs))
            info.ArgumentList.Add(a);
        info.Environment["PYTHONIOENCODING"] = "utf-8";
        info.Environment["PYTHONUTF8"] = "1";
        var process = Process.Start(info) ?? throw new InvalidOperationException("The SubScrub engine didn't start.");
        // the engine stops with the app, even if the app is killed
        Job.Attach(process);
        var core = new Core(process, ui);
        process.StandardInput.AutoFlush = false;
        _ = Task.Run(core.ReadLoop);
        _ = Task.Run(core.ReadErrors);
        return core;
    }

    public static string Where() => Locate().File;

    private static (string File, string[] Args, string Dir) Locate()
    {
        var env = Environment.GetEnvironmentVariable("SUBSCRUB_CORE");
        if (!string.IsNullOrWhiteSpace(env))
        {
            var parts = env.Split(' ', StringSplitOptions.RemoveEmptyEntries);
            return (parts[0], parts.Skip(1).ToArray(), AppContext.BaseDirectory);
        }
        var exe = Path.Combine(AppContext.BaseDirectory, "core", "subscrub-core.exe");
        if (File.Exists(exe))
            return (exe, Array.Empty<string>(), Path.GetDirectoryName(exe)!);
        // a source checkout: walk up to the folder that has subscrub/helper.py
        for (var d = new DirectoryInfo(AppContext.BaseDirectory); d != null; d = d.Parent)
        {
            if (File.Exists(Path.Combine(d.FullName, "subscrub", "helper.py")))
                return ("python", new[] { "-m", "subscrub.helper" }, d.FullName);
        }
        return (exe, Array.Empty<string>(), AppContext.BaseDirectory);
    }

    // ================================================================ requests

    public async Task<JsonNode?> CallRaw(string cmd, object? args = null)
    {
        var id = Interlocked.Increment(ref _nextId);
        var tcs = new TaskCompletionSource<JsonNode?>(TaskCreationOptions.RunContinuationsAsynchronously);
        _pending[id] = tcs;
        var line = JsonSerializer.Serialize(new { id, cmd, args = args ?? new { } }, Json);
        await _write.WaitAsync();
        try
        {
            if (_process.HasExited)
                throw new CoreException("The SubScrub engine stopped. Restart SubScrub.");
            await _process.StandardInput.WriteLineAsync(line);
            await _process.StandardInput.FlushAsync();
        }
        catch (Exception e) when (e is IOException or ObjectDisposedException)
        {
            _pending.TryRemove(id, out _);
            throw new CoreException("The SubScrub engine stopped. Restart SubScrub.");
        }
        finally
        {
            _write.Release();
        }
        return await tcs.Task;
    }

    public async Task<T> Call<T>(string cmd, object? args = null)
    {
        var node = await CallRaw(cmd, args);
        return node is null ? default! : node.Deserialize<T>(Json)!;
    }

    public Task Do(string cmd, object? args = null) => CallRaw(cmd, args);

    // ================================================================ reading

    private async Task ReadLoop()
    {
        var reader = _process.StandardOutput;
        try
        {
            while (await reader.ReadLineAsync() is { } line)
            {
                if (line.Length == 0)
                    continue;
                JsonNode? msg;
                try
                {
                    msg = JsonNode.Parse(line);
                }
                catch (JsonException)
                {
                    continue;
                }
                if (msg is not JsonObject obj)
                    continue;
                if (obj.TryGetPropertyValue("event", out var ev))
                {
                    Dispatch(ev!.GetValue<string>(), obj["data"]);
                    continue;
                }
                var id = obj["id"]?.GetValue<int>() ?? 0;
                if (!_pending.TryRemove(id, out var tcs))
                    continue;
                if (obj["ok"]?.GetValue<bool>() == true)
                    tcs.TrySetResult(obj["result"]?.DeepClone());
                else
                    tcs.TrySetException(new CoreException(obj["error"]?.GetValue<string>() ?? "Something went wrong."));
            }
        }
        catch (Exception)
        {
            // the pipe broke: handled below
        }
        var why = "The SubScrub engine stopped." + (_stderr.Length > 0 ? "\n\n" + Tail(_stderr.ToString()) : "");
        foreach (var (_, tcs) in _pending)
            tcs.TrySetException(new CoreException(why));
        _pending.Clear();
        _ready.TrySetException(new CoreException(why));
        if (!_disposed)
            _ui.TryEnqueue(() => Died?.Invoke(why));
    }

    private async Task ReadErrors()
    {
        try
        {
            while (await _process.StandardError.ReadLineAsync() is { } line)
            {
                lock (_stderr)
                {
                    _stderr.AppendLine(line);
                    if (_stderr.Length > 20000)
                        _stderr.Remove(0, _stderr.Length - 20000);
                }
            }
        }
        catch (Exception)
        {
            // nothing more to read
        }
    }

    private static string Tail(string text) => text.Length > 1500 ? "…" + text[^1500..] : text;

    private void Dispatch(string name, JsonNode? data)
    {
        try
        {
            switch (name)
            {
                case "ready":
                    var hello = data.Deserialize<Hello>(Json)!;
                    Ready = hello;
                    _ready.TrySetResult(hello);
                    break;
                case "files":
                    var files = data.Deserialize<FilesView>(Json)!;
                    _ui.TryEnqueue(() => Files?.Invoke(files));
                    break;
                case "review_changed":
                    var uid = data?["uid"]?.GetValue<int>() ?? 0;
                    _ui.TryEnqueue(() => ReviewChanged?.Invoke(uid));
                    break;
                case "save_done":
                    var done = data.Deserialize<SaveDone>(Json)!;
                    _ui.TryEnqueue(() => SaveDone?.Invoke(done));
                    break;
            }
        }
        catch (Exception e)
        {
            Log.Write($"event {name}: {e.Message}");
        }
    }

    private bool _disposed;

    public void Dispose()
    {
        if (_disposed)
            return;
        _disposed = true;
        try
        {
            _process.StandardInput.WriteLine("{\"id\":0,\"cmd\":\"shutdown\",\"args\":{}}");
            _process.StandardInput.Flush();
            _process.StandardInput.Close();
            if (!_process.WaitForExit(8000))
                _process.Kill(true);
        }
        catch (Exception)
        {
            // already gone
        }
    }
}

public sealed class CoreException(string message) : Exception(message);

/// <summary>A small app log for problems the window itself runs into (next to the engine's own log).</summary>
internal static class Log
{
    public static readonly List<string> Lines = [];

    public static void Write(string line)
    {
        lock (Lines)
        {
            Lines.Add($"{DateTime.Now:HH:mm:ss}  {line}");
            if (Lines.Count > 500)
                Lines.RemoveAt(0);
        }
    }
}
