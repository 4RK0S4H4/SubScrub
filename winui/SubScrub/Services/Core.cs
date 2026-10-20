using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Dispatching;
using SubScrub.Engine;
using SubScrub.Models;

namespace SubScrub.Services;

/// <summary>The engine: the C# port of SubScrub's Python engine (winui/SubScrub.Engine), run in-process. Commands
/// and replies are the JSON of subscrub/helper.py's protocol (see SubScrub.Engine.Commands), so the window's view
/// objects are the same as when the engine was a separate process. Replies come back as tasks; events ("files",
/// "review_changed", "save_done") are raised on the UI thread.</summary>
public sealed class Core : IDisposable
{
    public static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        PropertyNameCaseInsensitive = true,
        NumberHandling = System.Text.Json.Serialization.JsonNumberHandling.AllowReadingFromString,
    };

    private readonly DispatcherQueue _ui;
    private Commands? _engine;
    // commands run one at a time, in order, as the helper ran them; the slow ones (network, Windows) run beside them
    private readonly SemaphoreSlim _order = new(1, 1);

    public event Action<FilesView>? Files;
    public event Action<int>? ReviewChanged;
    public event Action<SaveDone>? SaveDone;
#pragma warning disable CS0067 // kept for the window: an in-process engine doesn't stop on its own
    public event Action<string>? Died;
#pragma warning restore CS0067

    public Hello? Ready { get; private set; }
    private readonly TaskCompletionSource<Hello> _ready = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public Task<Hello> WhenReady => _ready.Task;

    private Core(DispatcherQueue ui) => _ui = ui;

    /// <summary>Starts the engine. Arguments as the helper took them: --settings &lt;file.json&gt; (instead of the
    /// registry), --demo-tmdb (the stand-in for TMDB), --log &lt;file&gt;.</summary>
    public static Core Start(DispatcherQueue ui, IEnumerable<string> extraArgs)
    {
        var args = extraArgs.ToList();
        string? Opt(string name)
        {
            var i = args.IndexOf(name);
            return i >= 0 && i + 1 < args.Count ? args[i + 1] : null;
        }
        var core = new Core(ui);
        var version = typeof(Core).Assembly
            .GetCustomAttributes(typeof(System.Reflection.AssemblyInformationalVersionAttribute), false)
            .OfType<System.Reflection.AssemblyInformationalVersionAttribute>().FirstOrDefault()?.InformationalVersion ?? "";
        version = version.Split('+')[0];
        _ = Task.Run(() =>
        {
            try
            {
                core._engine = new Commands(version, Opt("--settings"), args.Contains("--demo-tmdb"), Opt("--log"), core.Dispatch);
                var hello = core._engine.Hello().Deserialize<Hello>(Json)!;
                core.Ready = hello;
                core._ready.TrySetResult(hello);
            }
            catch (Exception e)
            {
                Log.Write($"engine: {e}");
                core._ready.TrySetException(new CoreException(e.Message));
            }
        });
        return core;
    }

    /// <summary>Where the engine runs (for the About screen and the log).</summary>
    public static string Where() => "built in (C#)";

    // ================================================================ requests

    public async Task<JsonNode?> CallRaw(string cmd, object? args = null)
    {
        await WhenReady;
        var engine = _engine ?? throw new CoreException("The SubScrub engine isn't running. Restart SubScrub.");
        var json = args is JsonObject jo ? (JsonObject)jo.DeepClone()
            : JsonSerializer.SerializeToNode(args ?? new { }, Json) as JsonObject ?? [];
        var slow = Commands.Async.Contains(cmd);
        if (!slow)
            await _order.WaitAsync();
        try
        {
            return await Task.Run(() =>
            {
                try
                {
                    return engine.Run(cmd, json)?.DeepClone();
                }
                catch (Exception e)
                {
                    engine.Session.Log($"{cmd} failed: {e}");
                    throw new CoreException(e.Message.Length > 0 ? e.Message : e.GetType().Name);
                }
            });
        }
        finally
        {
            if (!slow)
                _order.Release();
        }
    }

    public async Task<T> Call<T>(string cmd, object? args = null)
    {
        var node = await CallRaw(cmd, args);
        return node is null ? default! : node.Deserialize<T>(Json)!;
    }

    public Task Do(string cmd, object? args = null) => CallRaw(cmd, args);

    // ================================================================ events

    private void Dispatch(string name, JsonNode? data)
    {
        try
        {
            switch (name)
            {
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
            _engine?.Close();
        }
        catch (Exception e)
        {
            Log.Write($"engine close: {e.Message}");
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
