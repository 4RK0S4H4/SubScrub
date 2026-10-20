using System.Runtime.InteropServices;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.Windows.AppLifecycle;

namespace SubScrub;

/// <summary>SubScrub runs as one window. Starting it again (Explorer's "Clean subtitles with SubScrub" starts it once
/// per selected file) hands the new paths to the window already open and quits, so several files land in one list.
/// The QA run (--shots) and --new-window always start a window of their own.</summary>
public static partial class Program
{
    private static readonly object Gate = new();
    private static readonly List<List<string>> Waiting = [];
    private static Action<List<string>>? _deliver;

    [STAThread]
    private static int Main(string[] args)
    {
        WinRT.ComWrappersSupport.InitializeComWrappers();
        if (!args.Contains("--shots") && !args.Contains("--new-window") && HandedOver())
            return 0;
        Application.Start(p =>
        {
            SynchronizationContext.SetSynchronizationContext(new DispatcherQueueSynchronizationContext(DispatcherQueue.GetForCurrentThread()));
            new App();
        });
        return 0;
    }

    /// <summary>True when another SubScrub is running and this start's paths went to it.</summary>
    private static bool HandedOver()
    {
        AppInstance main;
        try
        {
            main = AppInstance.FindOrRegisterForKey("SubScrub");
        }
        catch (Exception)
        {
            return false;                                // no single-window support here: just open a window
        }
        if (main.IsCurrent)
        {
            main.Activated += (_, e) => Arrived(PathsIn(e));
            return false;
        }
        try
        {
            AllowSetForegroundWindow(main.ProcessId);   // so the open window may come to the front
            var e = AppInstance.GetCurrent().GetActivatedEventArgs();
            return Task.Run(() => main.RedirectActivationToAsync(e).AsTask()).Wait(TimeSpan.FromSeconds(15));
        }
        catch (Exception)
        {
            return false;
        }
    }

    /// <summary>The files and folders on a forwarded command line.</summary>
    private static List<string> PathsIn(AppActivationArguments e)
    {
        if (e.Data is not Windows.ApplicationModel.Activation.ILaunchActivatedEventArgs launch || string.IsNullOrEmpty(launch.Arguments))
            return [];
        return PathsOn(SplitCommandLine(launch.Arguments));
    }

    /// <summary>The files and folders among command-line arguments (not SubScrub itself, nor an option's value).</summary>
    public static List<string> PathsOn(IEnumerable<string> args)
    {
        var self = Environment.ProcessPath ?? "";
        var paths = new List<string>();
        var skip = false;
        foreach (var a in args)
        {
            if (skip)
                skip = false;
            else if (a is "--log" or "--settings")
                skip = true;
            else if ((File.Exists(a) || Directory.Exists(a)) && !string.Equals(Path.GetFullPath(a), self, StringComparison.OrdinalIgnoreCase))
                paths.Add(a);
        }
        return paths;
    }

    private static void Arrived(List<string> paths)
    {
        Action<List<string>>? deliver;
        lock (Gate)
        {
            deliver = _deliver;
            if (deliver == null)
                Waiting.Add(paths);                      // the window isn't ready yet
        }
        deliver?.Invoke(paths);
    }

    /// <summary>The window takes forwarded paths from here on (on a background thread), including any that came early.</summary>
    public static void OnForwarded(Action<List<string>> deliver)
    {
        List<List<string>> early;
        lock (Gate)
        {
            _deliver = deliver;
            early = [.. Waiting];
            Waiting.Clear();
        }
        foreach (var paths in early)
            deliver(paths);
    }

    public static List<string> SplitCommandLine(string line)
    {
        var argv = CommandLineToArgvW(line, out var n);
        if (argv == 0)
            return [];
        try
        {
            return Enumerable.Range(0, n).Select(i => Marshal.PtrToStringUni(Marshal.ReadIntPtr(argv, i * IntPtr.Size)) ?? "").ToList();
        }
        finally
        {
            LocalFree(argv);
        }
    }

    [LibraryImport("shell32.dll", StringMarshalling = StringMarshalling.Utf16)]
    private static partial nint CommandLineToArgvW(string line, out int count);

    [LibraryImport("kernel32.dll")]
    private static partial nint LocalFree(nint mem);

    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool AllowSetForegroundWindow(uint processId);
}
