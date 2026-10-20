using System.Runtime.InteropServices;
using SubScrub.Services;

namespace SubScrub;

/// <summary>Paths handed over by a second start (Explorer's right-click entry, one start per selected file): the
/// window comes to the front and they are added together, a moment after the last one arrives. During a save they
/// wait and are added once it's finished.</summary>
public sealed partial class MainWindow
{
    private readonly List<string> _forwarded = [];
    private CancellationTokenSource? _forwardWait;

    private void TakeForwardedPaths() =>
        Program.OnForwarded(paths => DispatcherQueue.TryEnqueue(() => _ = ForwardedAsync(paths)));

    private async Task ForwardedAsync(List<string> paths)
    {
        BringToFront();
        foreach (var p in paths)
        {
            if (!_forwarded.Contains(p, StringComparer.OrdinalIgnoreCase))
                _forwarded.Add(p);
        }
        _forwardWait?.Cancel();
        var wait = _forwardWait = new CancellationTokenSource();
        try
        {
            await Task.Delay(400, wait.Token);           // Explorer starts one SubScrub per file, a few ms apart
        }
        catch (TaskCanceledException)
        {
            return;
        }
        await AddForwardedAsync();
    }

    /// <summary>Adds what was handed over, unless a save is running (then OnSaveDone calls this again).</summary>
    private async Task AddForwardedAsync()
    {
        if (_forwarded.Count == 0 || Files.Busy || Core?.Ready == null)
            return;
        var paths = _forwarded.ToList();
        _forwarded.Clear();
        await AddPathsAsync(paths);
    }

    private async Task AddForwardedAfterSaveAsync()
    {
        for (var i = 0; i < 100 && Files.Busy; i++)     // the list says it's no longer saving a moment later
            await Task.Delay(50);
        await AddForwardedAsync();
    }

    private void BringToFront()
    {
        if (Shots.Dir != null)
            return;
        var hwnd = Dpi.Handle(this);
        if (IsIconic(hwnd))
            ShowWindow(hwnd, 9);                         // SW_RESTORE
        Activate();
        SetForegroundWindow(hwnd);
    }

    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool IsIconic(nint hwnd);

    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool ShowWindow(nint hwnd, int cmd);

    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool SetForegroundWindow(nint hwnd);
}
