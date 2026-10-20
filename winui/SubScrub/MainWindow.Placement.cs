using Microsoft.UI.Windowing;
using SubScrub.Services;
using Windows.Graphics;

namespace SubScrub;

/// <summary>The window opens where it was last closed, at the same size (or maximized), as long as that still fits
/// on a screen. Kept in HKCU\Software\SubScrub\SubScrub\winui\window ("x,y,width,height,maximized"), next to the
/// shared settings.</summary>
public sealed partial class MainWindow
{
    private const string PlacementKey = @"Software\SubScrub\SubScrub\winui";

    /// <summary>Puts the window back where it was. False when nothing was saved or it no longer fits.</summary>
    private bool RestorePlacement()
    {
        if (Shots.Dir != null)
            return false;
        try
        {
            using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(PlacementKey);
            if (key?.GetValue("window") is not string text)
                return false;
            var p = text.Split(',').Select(x => int.Parse(x, System.Globalization.CultureInfo.InvariantCulture)).ToArray();
            if (p.Length != 5 || p[2] < 400 || p[3] < 300)
                return false;
            var rect = new RectInt32(p[0], p[1], p[2], p[3]);
            var area = DisplayArea.GetFromRect(rect, DisplayAreaFallback.None);
            if (area == null)
                return false;
            var work = area.WorkArea;
            // at least the top bar has to be on that screen, so the window can be grabbed
            if (rect.X + 120 > work.X + work.Width || rect.X + rect.Width - 120 < work.X || rect.Y < work.Y - 8
                || rect.Y + 48 > work.Y + work.Height)
                return false;
            AppWindow.MoveAndResize(rect);
            if (p[4] == 1 && AppWindow.Presenter is OverlappedPresenter op)
                op.Maximize();
            return true;
        }
        catch (Exception e)
        {
            Log.Write($"window placement: {e.Message}");
            return false;
        }
    }

    private void SavePlacement()
    {
        if (Shots.Dir != null)
            return;
        try
        {
            var maximized = AppWindow.Presenter is OverlappedPresenter { State: OverlappedPresenterState.Maximized };
            if (AppWindow.Presenter is OverlappedPresenter { State: OverlappedPresenterState.Minimized })
                return;
            var pos = AppWindow.Position;
            var size = AppWindow.Size;
            if (maximized && _restoredBounds is { } rb)
                (pos, size) = (new PointInt32(rb.X, rb.Y), new SizeInt32(rb.Width, rb.Height));
            using var key = Microsoft.Win32.Registry.CurrentUser.CreateSubKey(PlacementKey);
            key.SetValue("window", $"{pos.X},{pos.Y},{size.Width},{size.Height},{(maximized ? 1 : 0)}");
        }
        catch (Exception e)
        {
            Log.Write($"window placement: {e.Message}");
        }
    }

    // the size before maximizing, so a maximized window comes back to the right size when restored
    private RectInt32? _restoredBounds;

    private void TrackPlacement()
    {
        AppWindow.Changed += (_, args) =>
        {
            if ((args.DidPositionChange || args.DidSizeChange)
                && AppWindow.Presenter is OverlappedPresenter { State: OverlappedPresenterState.Restored })
                _restoredBounds = new RectInt32(AppWindow.Position.X, AppWindow.Position.Y, AppWindow.Size.Width, AppWindow.Size.Height);
        };
    }
}
