using System.Text.RegularExpressions;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Markup;

namespace SubScrub.Services;

/// <summary>Screenshot mode only: loads every XAML file in the app on its own with XamlReader, and looks up every
/// resource, so a parse error is reported with its file, line and reason (a failing window only says "XAML parsing
/// failed"). Writes diagnose.txt next to the screenshots.</summary>
internal static partial class Diagnose
{
    private const string Namespaces =
        " xmlns=\"http://schemas.microsoft.com/winfx/2006/xaml/presentation\"" +
        " xmlns:x=\"http://schemas.microsoft.com/winfx/2006/xaml\"" +
        " xmlns:c=\"using:SubScrub.Controls\"";

    public static void Run(string dir)
    {
        var log = new List<string>();
        var app = Application.Current;
        app.DebugSettings.XamlResourceReferenceFailed += (_, e) => Append(dir, "resource: " + e.Message);
        app.DebugSettings.BindingFailed += (_, e) => Append(dir, "binding: " + e.Message);

        var assembly = typeof(Diagnose).Assembly;
        foreach (var name in assembly.GetManifestResourceNames().Where(n => n.EndsWith(".xaml", StringComparison.Ordinal)).Order())
        {
            string xaml;
            using (var stream = assembly.GetManifestResourceStream(name)!)
            using (var reader = new StreamReader(stream))
                xaml = reader.ReadToEnd();
            try
            {
                var loaded = XamlReader.Load(Prepare(xaml));
                if (loaded is ResourceDictionary dict)
                {
                    foreach (var key in dict.Keys.ToList())
                    {
                        try
                        {
                            _ = dict[key];
                        }
                        catch (Exception e)
                        {
                            log.Add($"{name}: resource {key}: {e.Message}");
                        }
                    }
                }
                log.Add($"{name}: ok");
            }
            catch (Exception e)
            {
                log.Add($"{name}: {e.Message}");
            }
        }
        foreach (var key in new[] { "S4Text", "S4Button", "S4PrimaryButton", "S4SegItem", "S4NavItem", "S4Toggle",
                                    "S4TextBox", "S4PasswordBox", "S4Dialog", "S4Card", "S4FontRegular" })
        {
            try
            {
                _ = app.Resources[key];
            }
            catch (Exception e)
            {
                log.Add($"app resource {key}: {e.Message}");
            }
        }
        File.AppendAllLines(Path.Combine(dir, "diagnose.txt"), log);
    }

    private static void Append(string dir, string line) =>
        File.AppendAllText(Path.Combine(dir, "diagnose.txt"), line + "\n");

    /// <summary>Make a page loadable on its own: no code-behind class or handlers, and a Window becomes its content.</summary>
    private static string Prepare(string xaml)
    {
        xaml = ClassAttr().Replace(xaml, "");
        xaml = HandlerAttr().Replace(xaml, "");
        var start = xaml.IndexOf("<UserControl x:Name=\"Root\"", StringComparison.Ordinal);
        if (xaml.Contains("<Window") && start >= 0)
        {
            var end = xaml.LastIndexOf("</UserControl>", StringComparison.Ordinal) + "</UserControl>".Length;
            xaml = "<UserControl" + Namespaces + xaml[(start + "<UserControl".Length)..end];
        }
        return xaml;
    }

    [GeneratedRegex("\\s+x:Class=\"[^\"]*\"")]
    private static partial Regex ClassAttr();

    [GeneratedRegex("\\s+(Click|Checked|Toggled)=\"[^\"]*\"")]
    private static partial Regex HandlerAttr();
}
