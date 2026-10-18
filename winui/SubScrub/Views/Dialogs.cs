using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using SubScrub.Controls;
using SubScrub.Models;
using SubScrub.Services;
using Windows.System;

namespace SubScrub.Views;

/// <summary>A dialog the scripted QA run (Services/Shots.cs) can answer: what is being asked, and how to answer.</summary>
public interface IChooser
{
    string Key { get; }
    IReadOnlyList<string> Keys { get; }
    void Choose(string key);
}

/// <summary>The S4 glass dialog every question uses: a title, a body and buttons on the right (one primary), over
/// the dimmed window. Native ContentDialog, so Windows handles the dimming, focus and Escape.</summary>
public partial class GlassDialog : ContentDialog, IChooser
{
    protected readonly StackPanel Body = new() { Spacing = 14, Padding = new Thickness(24, 12, 24, 8) };
    protected readonly Grid Footer = new() { Padding = new Thickness(20, 12, 20, 18), ColumnSpacing = 8 };
    protected readonly StackPanel Buttons = new() { Orientation = Orientation.Horizontal, Spacing = 8, HorizontalAlignment = HorizontalAlignment.Right };
    private readonly Grid _frame = new();
    private readonly List<string> _keys = [];
    private string _default = "";

    public string? Choice { get; protected set; }
    public string Key { get; }
    public IReadOnlyList<string> Keys => _keys;

    protected GlassDialog(MainWindow win, string key, string title, double width = 560)
    {
        Key = key;
        Style = Theme.Style("S4Dialog");
        XamlRoot = win.Content.XamlRoot;
        RequestedTheme = ((FrameworkElement)win.Content).RequestedTheme;
        var max = (XamlRoot?.Size.Width ?? 1200) - 48;
        _frame.Width = Math.Max(360, Math.Min(width, max));
        _frame.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        _frame.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        _frame.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var head = new TextBlock
        {
            Text = title, Style = Theme.Style("S4CardTitle"), FontSize = 18, TextWrapping = TextWrapping.Wrap,
            TextTrimming = TextTrimming.None, Margin = new Thickness(24, 20, 24, 0),
        };
        _frame.Children.Add(head);
        var scroll = new ScrollViewer
        {
            Content = Body, VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            MaxHeight = Math.Max(240, (XamlRoot?.Size.Height ?? 800) - 220),
        };
        Grid.SetRow(scroll, 1);
        _frame.Children.Add(scroll);
        Footer.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        Footer.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        Grid.SetColumn(Buttons, 1);
        Footer.Children.Add(Buttons);
        Grid.SetRow(Footer, 2);
        _frame.Children.Add(Footer);
        Content = _frame;
        AutomationProperties.SetName(this, title);
        KeyDown += OnKeyDown;
    }

    private void OnKeyDown(object sender, KeyRoutedEventArgs e)
    {
        if (e.Key == VirtualKey.Enter && _default.Length > 0 && FocusManager.GetFocusedElement(XamlRoot) is not TextBox)
        {
            Choose(_default);
            e.Handled = true;
        }
    }

    public void Choose(string key)
    {
        Choice = key == "cancel" ? null : key;
        Hide();
    }

    protected Button AddButton(string key, string label, string kind = "", bool left = false)
    {
        var b = new Button
        {
            Content = label,
            Style = Theme.Style(kind == "primary" ? "S4PrimaryButton" : left ? "S4SubtleButton" : "S4Button"),
            MinWidth = kind == "primary" ? 96 : 0,
        };
        b.Click += (_, _) => Choose(key);
        _keys.Add(key);
        if (kind == "primary")
            _default = key;
        if (left)
            Footer.Children.Add(b);
        else
            Buttons.Children.Add(b);
        return b;
    }

    protected void Register(string key) => _keys.Add(key);

    protected static TextBlock Text(string text, string style = "S4Text") => new()
    {
        Text = text, Style = Theme.Style(style), TextWrapping = TextWrapping.Wrap, TextTrimming = TextTrimming.None,
        IsTextSelectionEnabled = style == "S4Text",
    };

    /// <summary>A short list of files in a recessed box: the name on the left, a quieter count on the right.</summary>
    protected Border Well(IList<List<string>> rows, int more)
    {
        var list = new StackPanel();
        var all = rows.Select(r => (r.Count > 0 ? r[0] : "", r.Count > 1 ? r[1] : "", false)).ToList();
        if (more > 0)
            all.Add(($"…and {more:N0} more", "", true));
        for (var i = 0; i < all.Count; i++)
        {
            var (name, count, quiet) = all[i];
            var row = new Grid { Padding = new Thickness(0, 7, 0, 7), ColumnSpacing = 16 };
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            var n = new TextBlock { Text = name, Style = Theme.Style(quiet ? "S4Quiet" : "S4Text"), FontSize = 13 };
            ToolTipService.SetToolTip(n, name);
            row.Children.Add(n);
            if (count.Length > 0)
            {
                var c = new TextBlock { Text = count, Style = Theme.Style("S4Count"), FontSize = 13,
                                        FontFamily = Theme.Font("S4FontRegular"), Foreground = Theme.Brush("S4Text2", IsDarkDialog) };
                Grid.SetColumn(c, 1);
                row.Children.Add(c);
            }
            if (i > 0)
                row.BorderThickness = new Thickness(0, 1, 0, 0);
            row.BorderBrush = Theme.Brush("S4Border", IsDarkDialog);
            list.Children.Add(row);
        }
        return new Border
        {
            Child = list, CornerRadius = new CornerRadius(10), BorderThickness = new Thickness(1),
            Padding = new Thickness(12, 2, 12, 2),
            Background = Theme.Brush("S4Well", IsDarkDialog), BorderBrush = Theme.Brush("S4Border", IsDarkDialog),
        };
    }

    protected bool IsDarkDialog => RequestedTheme == ElementTheme.Dark ||
        RequestedTheme == ElementTheme.Default && Application.Current.RequestedTheme == ApplicationTheme.Dark;
}

/// <summary>A question or a message from the engine: the text, a list of files, choices (Review Them Now…),
/// a note, an optional "Don't ask again" box, and buttons.</summary>
public sealed partial class AskDialog : GlassDialog
{
    private readonly CheckBox? _check;

    public bool Checked => _check?.IsChecked == true;

    public AskDialog(MainWindow win, Question q, UIElement? extra = null, double width = 560)
        : base(win, q.Key, q.Title, q.Options.Count > 0 ? 600 : width)
    {
        // "These already exist:" introduces the list; anything else explains it, after it
        var lead = q.Text.TrimEnd().EndsWith(':');
        if (q.Text.Length > 0 && lead)
            Body.Children.Add(Text(q.Text));
        if (q.Rows.Count > 0 || q.More > 0)
            Body.Children.Add(Well(q.Rows, q.More));
        if (q.Text.Length > 0 && !lead)
            Body.Children.Add(Text(q.Text, q.Options.Count > 0 ? "S4Hint" : "S4Text"));
        if (extra != null)
            Body.Children.Add(extra);
        if (q.Options.Count > 0)
        {
            var choices = new StackPanel { Spacing = 8 };
            foreach (var o in q.Options)
            {
                var b = new ChoiceButton(o.Key, o.Title, o.Desc, o.Recommended);
                b.Click += (_, _) => Choose(o.Key);
                Register(o.Key);
                choices.Children.Add(b);
            }
            Body.Children.Add(choices);
        }
        if (q.Detail.Length > 0)
            Body.Children.Add(Text(q.Detail, "S4Hint"));
        if (q.Checkbox.Length > 0)
        {
            _check = new CheckBox { Content = q.Checkbox, FontSize = 13.5 };
            Tint(_check);
            Body.Children.Add(_check);
        }
        foreach (var b in q.Buttons)
            AddButton(b.Key, b.Label, b.Kind);
    }

    /// <summary>A message with OK (and optionally one more button, e.g. Show Log).</summary>
    public static AskDialog Tell(MainWindow win, Message m, string extraKey = "", string extraLabel = "")
    {
        var q = new Question
        {
            Key = "message", Title = m.Title, Text = m.Text, Detail = m.Detail, Rows = m.Rows,
        };
        if (extraKey.Length > 0)
            q.Buttons.Add(new ButtonDef { Key = extraKey, Label = extraLabel });
        q.Buttons.Add(new ButtonDef { Key = "ok", Label = "OK", Kind = "primary" });
        return new AskDialog(win, q);
    }

    /// <summary>The check box in the accent colour instead of the system's.</summary>
    private void Tint(CheckBox box)
    {
        var dark = IsDarkDialog;
        foreach (var k in new[] { "CheckBoxCheckBackgroundFillChecked", "CheckBoxCheckBackgroundFillCheckedPointerOver",
                                  "CheckBoxCheckBackgroundFillCheckedPressed", "CheckBoxCheckBackgroundStrokeChecked",
                                  "CheckBoxCheckBackgroundStrokeCheckedPointerOver", "CheckBoxCheckBackgroundStrokeCheckedPressed" })
            box.Resources[k] = Theme.Brush("S4Btn", dark);
        box.Resources["CheckBoxCheckGlyphForegroundChecked"] = Theme.Brush("S4BtnFg", dark);
        box.Resources["CheckBoxCheckGlyphForegroundCheckedPointerOver"] = Theme.Brush("S4BtnFg", dark);
        box.Resources["CheckBoxCheckGlyphForegroundCheckedPressed"] = Theme.Brush("S4BtnFg", dark);
        box.Resources["CheckBoxForegroundUnchecked"] = Theme.Brush("S4Text", dark);
        box.Resources["CheckBoxForegroundChecked"] = Theme.Brush("S4Text", dark);
        box.FontFamily = Theme.Font("S4FontRegular");
    }
}

/// <summary>"Which title is this?": TMDB's likely matches (each with the name the file will get), a search box, and
/// Keep Current Name.</summary>
public sealed partial class NameDialog : GlassDialog
{
    private readonly Core _core;
    private readonly int _uid;
    private readonly StackPanel _choices = new() { Spacing = 8 };
    private readonly TextBlock _note;
    private readonly TextBox _query = new() { PlaceholderText = "Search TMDB by title" };

    public NameDialog(MainWindow win, Core core, int uid, NameChoices data) : base(win, "title", "Which title is this?", 620)
    {
        _core = core;
        _uid = uid;
        var name = new TextBlock { Text = data.Name, Style = Theme.Style("S4Text"), FontFamily = Theme.Font("S4FontSemiBold") };
        ToolTipService.SetToolTip(name, data.Name);
        Body.Children.Add(name);
        Body.Children.Add(Text("Pick the closest match. The file is renamed to the name shown under it.", "S4Hint"));
        Body.Children.Add(_choices);
        _note = Text("", "S4Hint");
        Body.Children.Add(_note);
        var search = new Grid { ColumnSpacing = 8 };
        search.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        search.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        _query.Style = Theme.Style("S4TextBox");
        _query.Text = data.Query;
        AutomationProperties.SetName(_query, "Search TMDB by title");
        _query.KeyDown += async (_, e) =>
        {
            if (e.Key == VirtualKey.Enter)
            {
                e.Handled = true;
                await Search();
            }
        };
        search.Children.Add(_query);
        var go = new Button { Style = Theme.Style("S4Button") };
        var label = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        label.Children.Add(new SolarIcon { Glyph = "magnifier", Size = 16, VerticalAlignment = VerticalAlignment.Center });
        label.Children.Add(new TextBlock { Text = "Search", VerticalAlignment = VerticalAlignment.Center });
        go.Content = label;
        go.Click += async (_, _) => await Search();
        Grid.SetColumn(go, 1);
        search.Children.Add(go);
        search.Visibility = data.CanSearch ? Visibility.Visible : Visibility.Collapsed;
        Body.Children.Add(search);
        AddButton("keep", "Keep Current Name", left: true);
        AddButton("cancel", "Cancel");
        Show(data.Choices, data.Note);
    }

    private void Show(List<ChoiceView> choices, string note)
    {
        _choices.Children.Clear();
        foreach (var c in choices)
        {
            var b = new ChoiceButton(c.Id, c.Label, c.NewName);
            b.Click += (_, _) => Choose(c.Id);
            Register(c.Id);
            _choices.Children.Add(b);
        }
        _note.Text = choices.Count == 0 && note.Length == 0 ? "No matches. Try a different title below." : note;
        _note.Visibility = _note.Text.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    private async Task Search()
    {
        var text = _query.Text.Trim();
        if (text.Length == 0)
            return;
        _note.Text = "Searching…";
        _note.Visibility = Visibility.Visible;
        try
        {
            var found = await _core.Call<NameChoices>("name_search", new { uid = _uid, text });
            Show(found.Choices, found.Note);
        }
        catch (CoreException e)
        {
            _note.Text = e.Message;
        }
    }
}

/// <summary>The activity log: what SubScrub did, for when something goes wrong.</summary>
public sealed partial class LogDialog : GlassDialog
{
    public LogDialog(MainWindow win, string text) : base(win, "log", "Activity Log", 760)
    {
        var block = new TextBlock
        {
            Text = text.Length > 0 ? text : "Nothing yet.",
            FontFamily = new Microsoft.UI.Xaml.Media.FontFamily("Cascadia Mono, Consolas"),
            FontSize = 12, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true,
            Foreground = Theme.Brush("S4Text2", IsDarkDialog),
        };
        Body.Children.Add(new Border
        {
            Child = block, Padding = new Thickness(12, 10, 12, 10), CornerRadius = new CornerRadius(10),
            Background = Theme.Brush("S4Well", IsDarkDialog), BorderBrush = Theme.Brush("S4Border", IsDarkDialog),
            BorderThickness = new Thickness(1),
        });
        var copy = AddButton("copy", "Copy", left: true);
        copy.Click += (_, _) =>
        {
            var data = new Windows.ApplicationModel.DataTransfer.DataPackage();
            data.SetText(text);
            Windows.ApplicationModel.DataTransfer.Clipboard.SetContent(data);
        };
        AddButton("ok", "Close", "primary");
    }
}
