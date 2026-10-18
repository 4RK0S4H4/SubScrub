using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.WindowsRuntime;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Foundation;
using SubScrub.Models;
using Windows.Graphics.Imaging;

namespace SubScrub.Services;

/// <summary>The scripted QA run for CI and the visual check. Drives the real app end to end on a copy of the sample
/// library (tests/make_samples.py --library), with a throwaway settings file and a stand-in for TMDB:
///
///     SubScrub.exe --shots &lt;folder&gt; --library &lt;videos&gt; --theme dark|light [--size 1920x1080] [--no-mica]
///
/// Adds the videos, opens Review (to check, changes, a kept line, look closer, regular subtitles only), picks a
/// title, opens Settings, then saves everything, answering the questions the way a person would. Every screen is
/// saved twice: "02-files-dark.png" is the window's XAML drawn by WinUI itself (exact pixels, the S4 page where
/// Mica would be) and "screen-02-files-dark.png" is a capture of the real screen. It also writes qa-dark.json (what
/// was saved, the final rows, timings) for tools/winui_qa_check.py to verify with ffprobe and mkvmerge, then quits.
/// Without --library it only shows the welcome screen and Settings.</summary>
internal static partial class Shots
{
    public static string? Dir { get; }
    public static string? Theme { get; }
    public static string? Library { get; }
    public static (int W, int H)? Size { get; }
    public static bool NoMica { get; }

    static Shots()
    {
        var args = Environment.GetCommandLineArgs();
        for (var i = 1; i < args.Length; i++)
        {
            var next = i + 1 < args.Length ? args[i + 1] : "";
            switch (args[i])
            {
                case "--shots": Dir = Path.GetFullPath(next); i++; break;
                case "--theme": Theme = next; i++; break;
                case "--library": Library = Path.GetFullPath(next); i++; break;
                case "--no-mica": NoMica = true; break;
                case "--size":
                    var wh = next.Split('x');
                    if (wh.Length == 2 && int.TryParse(wh[0], out var w) && int.TryParse(wh[1], out var h))
                        Size = (w, h);
                    i++;
                    break;
            }
        }
    }

    private static string Tag => Size is { } s && s != (1920, 1080) ? $"-{s.W}x{s.H}" : "";

    public static void Crash(Exception e)
    {
        var text = $"{DateTime.Now:s}\n{e}\n";
        try
        {
            var dir = Dir ?? Path.GetTempPath();
            Directory.CreateDirectory(dir);
            File.AppendAllText(Path.Combine(dir, "crash.txt"), text);
        }
        catch
        {
            // nothing more to do
        }
        if (Dir != null)
            Environment.Exit(1);
    }

    private static readonly List<string> Timings = [];
    private static readonly List<string> Problems = [];

    private static void Note(string line) => Timings.Add(line);

    public static async Task RunAsync(MainWindow win)
    {
        var t = (Theme ?? (win.IsDark ? "dark" : "light")) + Tag;
        try
        {
            Directory.CreateDirectory(Dir!);
            await win.Core!.WhenReady;
            await Task.Delay(2400);                                  // fonts, Mica, first layout and the welcome demo
            await Save(win, $"01-welcome-{t}");
            if (Library != null)
                await RunLibrary(win, t);
            await RunSettings(win, t);
            if (Library != null)
                await RunSave(win, t);
        }
        catch (Exception e)
        {
            Problems.Add("QA run stopped: " + e);
        }
        finally
        {
            File.WriteAllLines(Path.Combine(Dir!, $"timings-{t}.txt"), Timings);
            if (Problems.Count > 0)
                File.WriteAllLines(Path.Combine(Dir!, $"problems-{t}.txt"), Problems);
        }
        Application.Current.Exit();
    }

    private static async Task<bool> WaitFor(Func<bool> condition, double seconds)
    {
        var watch = Stopwatch.StartNew();
        while (watch.Elapsed.TotalSeconds < seconds)
        {
            if (condition())
                return true;
            await Task.Delay(100);
        }
        return false;
    }

    private static bool Settled(MainWindow win) => win.Files.Count > 0 && win.Files.Rows.All(r =>
        r.State is not ("queued" or "probing" or "reading") && !r.Rename.StartsWith("Looking up", StringComparison.Ordinal));

    private static RowView? Row(MainWindow win, string start) =>
        win.Files.Rows.FirstOrDefault(r => r.Name.StartsWith(start, StringComparison.OrdinalIgnoreCase));

    private static async Task RunLibrary(MainWindow win, string t)
    {
        var watch = Stopwatch.StartNew();
        await win.AddPathsAsync([Library!]);
        if (!await WaitFor(() => Settled(win), 300))
            Problems.Add("the library didn't finish reading within 5 minutes");
        Note($"Add the sample library and read it all: {watch.Elapsed.TotalSeconds:F1} s ({win.Files.Count} files)");
        await Task.Delay(900);
        await Save(win, $"02-files-{t}");

        // Review: lines to check, then the changes, then keep one line
        var sev = Row(win, "Severance") ?? win.Files.Rows.FirstOrDefault(r => r.Review);
        if (sev != null)
        {
            watch.Restart();
            await win.OpenReviewAsync(sev.Uid);
            Note($"Open Review ({sev.Name}): {watch.ElapsedMilliseconds} ms");
            await Task.Delay(900);
            await Save(win, $"03-review-{t}");
            watch.Restart();
            await win.OpenReviewAsync(sev.Uid, "changes");
            Note($"Switch to Changes: {watch.ElapsedMilliseconds} ms ({win.PreviewList.Rows.Count} lines)");
            await Task.Delay(700);
            win.PreviewList.Step(1);
            await Task.Delay(300);
            watch.Restart();
            await win.ToggleCurrentAsync();
            Note($"Keep Original on one line: {watch.ElapsedMilliseconds} ms");
            await Task.Delay(700);
            await Save(win, $"04-review-kept-{t}");
            if (win.PreviewList.Current?.Override != "orig")
                Problems.Add("Keep Original didn't mark the line as kept");
        }
        if (Row(win, "Blade") is { } br)
        {
            await win.OpenReviewAsync(br.Uid);
            await Task.Delay(900);
            await Save(win, $"05-review-look-closer-{t}");
        }
        if (Row(win, "Planet") is { } pe)
        {
            await win.OpenReviewAsync(pe.Uid);
            await Task.Delay(900);
            await Save(win, $"06-review-no-sdh-{t}");
        }

        // the title picker, for the video TMDB isn't sure about
        if (Row(win, "Slow") is { } sh)
        {
            await win.OpenReviewAsync(sh.Uid);
            await Task.Delay(600);
            win.AutoAnswer = async dialog =>
            {
                await Task.Delay(800);
                await Save(win, $"07-title-dialog-{t}");
                dialog.Choose(dialog.Keys.FirstOrDefault(k => k is not ("keep" or "cancel")) ?? "keep");
            };
            await win.ChooseTitleAsync(sh.Uid);
            win.AutoAnswer = null;
            await Task.Delay(800);
        }
        win.ShowTab(review: false);
        await Task.Delay(700);
        await Save(win, $"08-files-ready-{t}");
    }

    private static async Task RunSettings(MainWindow win, string t)
    {
        // Settings: the first open builds the dialog; later opens reuse it
        var first = await OpenSettings(win);
        Note($"Settings, first open (builds the dialog): {first} ms");
        await Task.Delay(700);
        await Save(win, $"09-settings-{t}");
        win.Dialog.ShowSection("names");
        await Task.Delay(500);
        await Save(win, $"10-settings-names-{t}");
        win.Dialog.ShowSection("look");
        await Task.Delay(500);
        await Save(win, $"11-settings-look-{t}");
        win.Dialog.Hide();
        await Task.Delay(800);
        for (var i = 0; i < 2; i++)
        {
            var again = await OpenSettings(win);
            Note($"Settings, open again: {again} ms");
            await Task.Delay(400);
            win.Dialog.Hide();
            await Task.Delay(600);
        }
    }

    private static async Task RunSave(MainWindow win, string t)
    {
        var asked = new List<string>();
        var shot = 12;
        win.AutoAnswer = async dialog =>
        {
            asked.Add(dialog.Key);
            await Task.Delay(900);
            await Save(win, $"{shot++}-ask-{dialog.Key}-{t}");
            dialog.Choose(dialog.Key switch
            {
                "decision" => "guess",
                "rewrite" => "go",
                "names" => "keep",
                "locks" => "skip",
                _ => dialog.Keys.LastOrDefault() ?? "ok",
            });
        };
        var done = new TaskCompletionSource<SaveDone>();
        win.Saved += d => done.TrySetResult(d);
        var watch = Stopwatch.StartNew();
        await win.StartSaveAsync(null);
        if (!await WaitFor(() => win.Files.Busy, 20))
        {
            Problems.Add("the save didn't start (asked: " + string.Join(", ", asked) + ")");
            return;
        }
        await Task.Delay(1500);
        await Save(win, $"{shot++}-saving-{t}");
        var finished = await Task.WhenAny(done.Task, Task.Delay(TimeSpan.FromMinutes(10)));
        Note($"Save everything: {watch.Elapsed.TotalSeconds:F1} s (questions: {string.Join(", ", asked)})");
        if (finished != done.Task)
        {
            Problems.Add("the save didn't finish within 10 minutes");
            return;
        }
        var result = await done.Task;
        await Task.Delay(1200);
        win.AutoAnswer = null;
        await Save(win, $"{shot++}-saved-{t}");
        var report = new
        {
            theme = t, asked, counts = result.Counts, status = result.Status, outputs = result.Outputs,
            rows = win.Files.Rows.Select(r => new { r.Name, r.State, r.Detail, r.Badge }).ToList(),
            library = Library,
        };
        File.WriteAllText(Path.Combine(Dir!, $"qa-{t}.json"),
                          System.Text.Json.JsonSerializer.Serialize(report, new System.Text.Json.JsonSerializerOptions { WriteIndented = true }));
    }

    private static async Task<long> OpenSettings(MainWindow win)
    {
        var watch = Stopwatch.StartNew();
        var dialog = win.Dialog;
        var opened = new TaskCompletionSource();
        void OnOpened(ContentDialog d, ContentDialogOpenedEventArgs a) => opened.TrySetResult();
        dialog.Opened += OnOpened;
        _ = win.ShowSettingsAsync();
        await opened.Task;
        watch.Stop();
        dialog.Opened -= OnOpened;
        return watch.ElapsedMilliseconds;
    }

    // ================================================================ the XAML render

    private static async Task Save(MainWindow win, string name)
    {
        var root = (UIElement)win.Content;
        var xamlRoot = root.XamlRoot;
        var scale = xamlRoot.RasterizationScale;

        var bitmap = new RenderTargetBitmap();
        await bitmap.RenderAsync(root);
        int w = bitmap.PixelWidth, h = bitmap.PixelHeight;
        var pixels = (await bitmap.GetPixelsAsync()).ToArray();
        OverSolid(pixels, global::SubScrub.Theme.Color("S4BgColor", win.IsDark));

        // dialogs, menus and tooltips live in popups, which a render of the window leaves out
        foreach (var popup in VisualTreeHelper.GetOpenPopupsForXamlRoot(xamlRoot))
        {
            if (popup.Child is not FrameworkElement child || child.ActualWidth < 1 || child.ActualHeight < 1)
                continue;
            var layer = new RenderTargetBitmap();
            await layer.RenderAsync(child);
            var top = (await layer.GetPixelsAsync()).ToArray();
            var at = child.TransformToVisual(null).TransformPoint(new Point(0, 0));
            Blend(pixels, w, h, top, layer.PixelWidth, layer.PixelHeight,
                  (int)Math.Round(at.X * scale), (int)Math.Round(at.Y * scale));
        }
        await WritePng(Path.Combine(Dir!, name + ".png"), pixels, w, h);

        try
        {
            var (screen, sw, sh) = CaptureScreen(Dpi.Handle(win));
            await WritePng(Path.Combine(Dir!, "screen-" + name + ".png"), screen, sw, sh);
        }
        catch (Exception e)
        {
            File.AppendAllText(Path.Combine(Dir!, "screen-capture-errors.txt"), $"{name}: {e.Message}\n");
        }
    }

    /// <summary>Premultiplied BGRA over an opaque colour.</summary>
    private static void OverSolid(byte[] px, Windows.UI.Color c)
    {
        for (var i = 0; i < px.Length; i += 4)
        {
            var rest = 255 - px[i + 3];
            px[i] = (byte)(px[i] + c.B * rest / 255);
            px[i + 1] = (byte)(px[i + 1] + c.G * rest / 255);
            px[i + 2] = (byte)(px[i + 2] + c.R * rest / 255);
            px[i + 3] = 255;
        }
    }

    /// <summary>Premultiplied BGRA layer over the picture at (x, y).</summary>
    private static void Blend(byte[] dst, int dw, int dh, byte[] src, int sw, int sh, int x, int y)
    {
        for (var row = 0; row < sh; row++)
        {
            var dy = y + row;
            if (dy < 0 || dy >= dh)
                continue;
            for (var col = 0; col < sw; col++)
            {
                var dx = x + col;
                if (dx < 0 || dx >= dw)
                    continue;
                int s = (row * sw + col) * 4, d = (dy * dw + dx) * 4;
                var rest = 255 - src[s + 3];
                for (var k = 0; k < 3; k++)
                    dst[d + k] = (byte)Math.Min(255, src[s + k] + dst[d + k] * rest / 255);
            }
        }
    }

    private static async Task WritePng(string path, byte[] bgra, int w, int h)
    {
        using var file = File.Create(path);
        var encoder = await BitmapEncoder.CreateAsync(BitmapEncoder.PngEncoderId, file.AsRandomAccessStream());
        encoder.SetPixelData(BitmapPixelFormat.Bgra8, BitmapAlphaMode.Ignore, (uint)w, (uint)h, 96, 96, bgra);
        await encoder.FlushAsync();
    }

    // ================================================================ the screen capture

    [StructLayout(LayoutKind.Sequential)]
    private struct RECT { public int Left, Top, Right, Bottom; }

    [StructLayout(LayoutKind.Sequential)]
    private struct BITMAPINFOHEADER
    {
        public uint biSize;
        public int biWidth, biHeight;
        public ushort biPlanes, biBitCount;
        public uint biCompression, biSizeImage;
        public int biXPelsPerMeter, biYPelsPerMeter;
        public uint biClrUsed, biClrImportant;
    }

    [LibraryImport("dwmapi.dll")]
    private static partial int DwmGetWindowAttribute(nint hwnd, int attribute, out RECT value, int size);

    [LibraryImport("user32.dll")]
    private static partial nint GetDC(nint hwnd);

    [LibraryImport("user32.dll")]
    private static partial int ReleaseDC(nint hwnd, nint dc);

    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool SetForegroundWindow(nint hwnd);

    [LibraryImport("gdi32.dll")]
    private static partial nint CreateCompatibleDC(nint dc);

    [LibraryImport("gdi32.dll")]
    private static partial nint CreateCompatibleBitmap(nint dc, int w, int h);

    [LibraryImport("gdi32.dll")]
    private static partial nint SelectObject(nint dc, nint obj);

    [LibraryImport("gdi32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool BitBlt(nint dst, int x, int y, int w, int h, nint src, int sx, int sy, uint rop);

    [LibraryImport("gdi32.dll")]
    private static partial int GetDIBits(nint dc, nint bitmap, uint start, uint lines, byte[] bits,
                                         ref BITMAPINFOHEADER info, uint usage);

    [LibraryImport("gdi32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool DeleteObject(nint obj);

    [LibraryImport("gdi32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool DeleteDC(nint dc);

    private static (byte[] Pixels, int W, int H) CaptureScreen(nint hwnd)
    {
        SetForegroundWindow(hwnd);
        const int DWMWA_EXTENDED_FRAME_BOUNDS = 9;
        if (DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, out var r, Marshal.SizeOf<RECT>()) != 0)
            throw new InvalidOperationException("no window bounds");
        int w = r.Right - r.Left, h = r.Bottom - r.Top;
        var screen = GetDC(0);
        var mem = CreateCompatibleDC(screen);
        var bmp = CreateCompatibleBitmap(screen, w, h);
        var old = SelectObject(mem, bmp);
        try
        {
            const uint SRCCOPY = 0x00CC0020, CAPTUREBLT = 0x40000000;
            if (!BitBlt(mem, 0, 0, w, h, screen, r.Left, r.Top, SRCCOPY | CAPTUREBLT))
                throw new InvalidOperationException("BitBlt failed");
            SelectObject(mem, old);
            var info = new BITMAPINFOHEADER
            {
                biSize = (uint)Marshal.SizeOf<BITMAPINFOHEADER>(),
                biWidth = w,
                biHeight = -h,                                       // top-down rows
                biPlanes = 1,
                biBitCount = 32,
            };
            var pixels = new byte[w * h * 4];
            if (GetDIBits(mem, bmp, 0, (uint)h, pixels, ref info, 0) == 0)
                throw new InvalidOperationException("GetDIBits failed");
            for (var i = 3; i < pixels.Length; i += 4)
                pixels[i] = 255;
            return (pixels, w, h);
        }
        finally
        {
            DeleteObject(bmp);
            DeleteDC(mem);
            ReleaseDC(0, screen);
        }
    }
}
