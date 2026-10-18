using System.Runtime.InteropServices;
using Microsoft.UI.Xaml;

namespace SubScrub.Services;

internal static partial class Dpi
{
    [LibraryImport("user32.dll")]
    private static partial uint GetDpiForWindow(nint hwnd);

    public static nint Handle(Window window) => WinRT.Interop.WindowNative.GetWindowHandle(window);

    /// <summary>Physical pixels per view pixel for the window's monitor (1.5 at 150%).</summary>
    public static double Scale(Window window)
    {
        var dpi = GetDpiForWindow(Handle(window));
        return dpi > 0 ? dpi / 96.0 : 1.0;
    }
}
