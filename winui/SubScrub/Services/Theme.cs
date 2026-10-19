using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;

namespace SubScrub;

/// <summary>Reads the S4 Glass resources (Themes/Controls.xaml and the generated Themes/Tokens.xaml) from code.</summary>
internal static class Theme
{
    private static readonly Dictionary<(string, bool), SolidColorBrush> Brushes = [];

    public static Style Style(string key) => (Style)Application.Current.Resources[key];

    /// <summary>A token colour for one theme, e.g. Color("S4TextColor", dark: true).</summary>
    public static Windows.UI.Color Color(string key, bool dark)
    {
        var found = Find(Application.Current.Resources, key, dark ? "Dark" : "Light");
        return found ?? Microsoft.UI.Colors.Transparent;
    }

    /// <summary>A token brush for the theme an element is drawn in ("S4Text" → S4TextColor). Code-built controls
    /// call this again from ActualThemeChanged; a ThemeResource lookup from code would follow the app's theme, not
    /// the window's.</summary>
    public static SolidColorBrush Brush(string token, FrameworkElement element) => Brush(token, IsDark(element));

    public static SolidColorBrush Brush(string token, bool dark)
    {
        if (!Brushes.TryGetValue((token, dark), out var brush))
        {
            brush = new SolidColorBrush(Color(token + "Color", dark));
            Brushes[(token, dark)] = brush;
        }
        return brush;
    }

    public static bool IsDark(FrameworkElement element) => element.ActualTheme == ElementTheme.Dark;

    public static FontFamily Font(string key) => (FontFamily)Application.Current.Resources[key];

    private static Windows.UI.Color? Find(ResourceDictionary dict, string key, string theme)
    {
        if (dict.ThemeDictionaries.TryGetValue(theme, out var t) && t is ResourceDictionary td
            && td.TryGetValue(key, out var value) && value is Windows.UI.Color c)
            return c;
        foreach (var merged in dict.MergedDictionaries)
        {
            var hit = Find(merged, key, theme);
            if (hit != null)
                return hit;
        }
        return null;
    }
}
