using Microsoft.UI.Xaml;

namespace SubScrub;

/// <summary>Reads the S4 Glass resources (Themes/Controls.xaml and the generated Themes/Tokens.xaml) from code.</summary>
internal static class Theme
{
    public static Style Style(string key) => (Style)Application.Current.Resources[key];

    /// <summary>A token colour for one theme, e.g. Color("S4TextColor", dark: true).</summary>
    public static Windows.UI.Color Color(string key, bool dark)
    {
        var found = Find(Application.Current.Resources, key, dark ? "Dark" : "Light");
        return found ?? Microsoft.UI.Colors.Transparent;
    }

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
