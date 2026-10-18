using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Documents;
using Microsoft.UI.Xaml.Media;
using SubScrub.Models;

namespace SubScrub.Controls;

/// <summary>A file's status badge (Ready, 3 to Check, Saved…): hugs its text, coloured by the engine's verdict kind.
/// ok green for ready and saved, crimson for things to check, red for failures, amber while saving, grey otherwise.</summary>
public sealed partial class StateBadge : UserControl
{
    private readonly Border _chrome = new() { Style = Theme.Style("S4Badge") };
    private readonly SolarIcon _icon = new() { Size = 12, VerticalAlignment = VerticalAlignment.Center };
    private readonly TextBlock _text = new() { Style = Theme.Style("S4BadgeText"), VerticalAlignment = VerticalAlignment.Center };
    private string _kind = "";

    public StateBadge()
    {
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 4 };
        row.Children.Add(_icon);
        row.Children.Add(_text);
        _chrome.Child = row;
        Content = _chrome;
        IsTabStop = false;
        Visibility = Visibility.Collapsed;
        ActualThemeChanged += (_, _) => Paint();
    }

    public static string Tone(string kind) => kind switch
    {
        "ready" or "done" => "ok",
        "check" or "low" or "flag" => "accent",
        "failed" => "bad",
        "busy" => "warn",
        _ => "mute",
    };

    public void Set(string text, string kind)
    {
        _text.Text = text;
        _kind = kind;
        Visibility = string.IsNullOrEmpty(text) ? Visibility.Collapsed : Visibility.Visible;
        AutomationProperties.SetName(this, text);
        Paint();
    }

    private void Paint()
    {
        var (bg, fg, glyph) = Tone(_kind) switch
        {
            "ok" => ("S4OkBg", "S4Ok", "check-circle"),
            "accent" => ("S4AccentSoft", "S4BadgeAccent", "danger-circle"),
            "bad" => ("S4BadBg", "S4Bad", "danger-triangle"),
            "warn" => ("S4WarnBg", "S4Warn", "restart"),
            _ => ("S4MuteBg", "S4Text2", "info-circle"),
        };
        if (Theme.IsDark(this) && Tone(_kind) == "accent")
            fg = "S4AccentLight";
        _chrome.Background = Theme.Brush(bg, this);
        _text.Foreground = Theme.Brush(fg, this);
        _icon.Foreground = Theme.Brush(fg, this);
        _icon.Glyph = glyph;
    }
}

/// <summary>A one-line notice with an action: files in use (warn), regular subtitles only (info), FFmpeg missing.</summary>
public sealed partial class Banner : UserControl
{
    public static readonly DependencyProperty TextProperty = DependencyProperty.Register(
        nameof(Text), typeof(string), typeof(Banner), new PropertyMetadata("", (d, e) => ((Banner)d)._text.Text = (string)e.NewValue));
    public static readonly DependencyProperty ActionTextProperty = DependencyProperty.Register(
        nameof(ActionText), typeof(string), typeof(Banner), new PropertyMetadata("", (d, e) => ((Banner)d).SetAction((string)e.NewValue)));
    public static readonly DependencyProperty ToneProperty = DependencyProperty.Register(
        nameof(Tone), typeof(string), typeof(Banner), new PropertyMetadata("info", (d, _) => ((Banner)d).Paint()));

    public string Text { get => (string)GetValue(TextProperty); set => SetValue(TextProperty, value); }
    public string ActionText { get => (string)GetValue(ActionTextProperty); set => SetValue(ActionTextProperty, value); }
    public string Tone { get => (string)GetValue(ToneProperty); set => SetValue(ToneProperty, value); }

    public event Action? ActionClicked;

    private readonly Border _chrome = new() { CornerRadius = new CornerRadius(10), BorderThickness = new Thickness(1), Padding = new Thickness(14, 10, 10, 10) };
    private readonly SolarIcon _icon = new() { Size = 18, VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(0, 1, 0, 0) };
    private readonly TextBlock _text = new() { Style = Theme.Style("S4Text"), FontSize = 13.5, TextWrapping = TextWrapping.Wrap, TextTrimming = TextTrimming.None, VerticalAlignment = VerticalAlignment.Center };
    private readonly Button _action = new() { Style = Theme.Style("S4SmallButton"), VerticalAlignment = VerticalAlignment.Center, Visibility = Visibility.Collapsed };

    public Banner()
    {
        var grid = new Grid { ColumnSpacing = 12 };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.Children.Add(_icon);
        Grid.SetColumn(_text, 1);
        grid.Children.Add(_text);
        Grid.SetColumn(_action, 2);
        grid.Children.Add(_action);
        _action.Click += (_, _) => ActionClicked?.Invoke();
        _chrome.Child = grid;
        Content = _chrome;
        IsTabStop = false;
        ActualThemeChanged += (_, _) => Paint();
        Loaded += (_, _) => Paint();
    }

    private void SetAction(string text)
    {
        _action.Content = text;
        _action.Visibility = string.IsNullOrEmpty(text) ? Visibility.Collapsed : Visibility.Visible;
    }

    private void Paint()
    {
        var warn = Tone == "warn";
        _chrome.Background = Theme.Brush(warn ? "S4WarnBg" : "S4Bg2", this);
        _chrome.BorderBrush = Theme.Brush(warn ? "S4WarnBg" : "S4Border", this);
        _icon.Foreground = Theme.Brush(warn ? "S4Warn" : "S4Text2", this);
        _icon.Glyph = warn ? "danger-triangle" : "info-circle";
    }
}

/// <summary>One subtitle track in Review's sidebar: its name, language and format, what is in it, and whether it is
/// the one being cleaned (a soft crimson tile). Clicking picks it.</summary>
public sealed partial class TrackTile : Button
{
    public TrackView Track { get; }
    private readonly TextBlock _title, _sub, _stat;
    private readonly StackPanel _chips = new() { Orientation = Orientation.Horizontal, Spacing = 4, VerticalAlignment = VerticalAlignment.Center };

    public TrackTile(TrackView track)
    {
        Track = track;
        Style = Theme.Style("S4Button");
        Height = double.NaN;
        HorizontalAlignment = HorizontalAlignment.Stretch;
        HorizontalContentAlignment = HorizontalAlignment.Stretch;
        Padding = new Thickness(12, 10, 12, 10);
        CornerRadius = new CornerRadius(10);
        IsEnabled = track.Enabled;
        AutomationProperties.SetName(this, $"{track.Title}, {track.Language}");

        var grid = new Grid { RowSpacing = 2, ColumnSpacing = 8 };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        _title = new TextBlock { Text = track.Title, Style = Theme.Style("S4Medium"), FontSize = 14 };
        grid.Children.Add(_title);
        foreach (var flag in track.Flags.Append(track.Codec))
            _chips.Children.Add(Chip(flag));
        Grid.SetColumn(_chips, 1);
        grid.Children.Add(_chips);
        _sub = new TextBlock { Text = track.Language + (track.External ? ", a file next to the video" : ""), Style = Theme.Style("S4Quiet") };
        Grid.SetRow(_sub, 1);
        Grid.SetColumnSpan(_sub, 2);
        grid.Children.Add(_sub);
        _stat = new TextBlock { Text = track.Stat, Style = Theme.Style("S4Quiet"), Visibility = track.Stat.Length > 0 ? Visibility.Visible : Visibility.Collapsed };
        Grid.SetRow(_stat, 2);
        Grid.SetColumnSpan(_stat, 2);
        grid.Children.Add(_stat);
        Content = grid;
        ActualThemeChanged += (_, _) => Paint();
        Paint();
    }

    private static Border Chip(string text) => new()
    {
        CornerRadius = new CornerRadius(5),
        Padding = new Thickness(5, 1, 5, 2),
        Tag = "chip",
        Child = new TextBlock { Text = text, FontFamily = Theme.Font("S4FontSemiBold"), FontSize = 10.5 },
    };

    private void Paint()
    {
        var on = Track.Selected;
        Background = Theme.Brush(on ? "S4TileOn" : "S4Glass2", this);
        BorderBrush = Theme.Brush(on ? "S4SoftRing" : "S4Border2", this);
        _title.Foreground = Theme.Brush("S4Text", this);
        _sub.Foreground = Theme.Brush("S4Text2", this);
        _stat.Foreground = Theme.Brush(Track.Tone switch
        {
            "accent" => Theme.IsDark(this) ? "S4AccentLight" : "S4AccentText",
            "warn" => "S4Warn",
            "dim" => "S4Text3",
            _ => "S4Text2",
        }, this);
        _stat.FontFamily = Theme.Font(Track.Tone == "accent" ? "S4FontMedium" : "S4FontRegular");
        foreach (var chip in _chips.Children.OfType<Border>())
        {
            chip.Background = Theme.Brush(on ? "S4Glass2" : "S4MuteBg", this);
            ((TextBlock)chip.Child).Foreground = Theme.Brush("S4Text2", this);
        }
    }
}

/// <summary>One kind of edit in Review: a Title Case name, an example, how many lines it touches here, a switch.</summary>
public sealed partial class RuleRow : UserControl
{
    public string Key { get; }
    public ToggleSwitch Switch { get; }
    private readonly TextBlock _count;
    private bool _quiet;

    public event Action<string, bool>? Toggled;

    public RuleRow(string key, string title, string example)
    {
        Key = key;
        var grid = new Grid { ColumnSpacing = 10, Padding = new Thickness(0, 6, 0, 6) };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var text = new StackPanel { Spacing = 1, VerticalAlignment = VerticalAlignment.Center };
        text.Children.Add(new TextBlock { Text = title, Style = Theme.Style("S4Medium"), FontSize = 13.5 });
        text.Children.Add(new TextBlock { Text = example, Style = Theme.Style("S4Hint"), FontSize = 12, TextWrapping = TextWrapping.NoWrap, TextTrimming = TextTrimming.CharacterEllipsis });
        grid.Children.Add(text);
        _count = new TextBlock { Style = Theme.Style("S4Count"), FontSize = 13, VerticalAlignment = VerticalAlignment.Center };
        Grid.SetColumn(_count, 1);
        grid.Children.Add(_count);
        Switch = new ToggleSwitch { Style = Theme.Style("S4Toggle"), OnContent = "", OffContent = "" };
        AutomationProperties.SetName(Switch, title);
        Switch.Toggled += (_, _) =>
        {
            if (!_quiet)
                Toggled?.Invoke(Key, Switch.IsOn);
        };
        Grid.SetColumn(Switch, 2);
        grid.Children.Add(Switch);
        Content = grid;
        ActualThemeChanged += (_, _) => _count.Foreground = Theme.Brush("S4Text2", this);
        Loaded += (_, _) => _count.Foreground = Theme.Brush("S4Text2", this);
    }

    public void Set(RuleView r, bool enabled)
    {
        _quiet = true;
        Switch.IsOn = r.On;
        _quiet = false;
        Switch.IsEnabled = enabled;
        _count.Text = r.Count is int n && n > 0 ? n.ToString("N0", System.Globalization.CultureInfo.InvariantCulture) : "";
        Visibility = r.Visible ? Visibility.Visible : Visibility.Collapsed;
    }
}

/// <summary>A choice in a question (Review Them Now, a title from TMDB): a title, a line under it, and
/// "Recommended" on the one SubScrub suggests.</summary>
public sealed partial class ChoiceButton : Button
{
    public string Key { get; }

    public ChoiceButton(string key, string title, string description, bool recommended = false)
    {
        Key = key;
        Style = Theme.Style("S4Button");
        Height = double.NaN;
        MinHeight = 52;
        HorizontalAlignment = HorizontalAlignment.Stretch;
        HorizontalContentAlignment = HorizontalAlignment.Stretch;
        Padding = new Thickness(14, 9, 14, 10);
        AutomationProperties.SetName(this, title);
        var grid = new Grid { ColumnSpacing = 12 };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var text = new StackPanel { Spacing = 2, VerticalAlignment = VerticalAlignment.Center };
        text.Children.Add(new TextBlock { Text = title, Style = Theme.Style("S4Medium"), TextWrapping = TextWrapping.Wrap, TextTrimming = TextTrimming.None });
        if (description.Length > 0)
            text.Children.Add(new TextBlock { Text = description, Style = Theme.Style("S4Hint") });
        grid.Children.Add(text);
        if (recommended)
        {
            var badge = new StateBadge { VerticalAlignment = VerticalAlignment.Center };
            badge.Set("Recommended", "flag");
            Grid.SetColumn(badge, 1);
            grid.Children.Add(badge);
        }
        Content = grid;
    }
}

/// <summary>Builds the before column of a preview row: kept text as is, removed parts struck through.</summary>
internal static class RichText
{
    public static void Fill(TextBlock block, IEnumerable<Segment> segments, Brush gone)
    {
        block.Inlines.Clear();
        foreach (var s in segments)
        {
            var run = new Run { Text = s.Text };
            if (s.Gone)
            {
                run.TextDecorations = Windows.UI.Text.TextDecorations.Strikethrough;
                run.Foreground = gone;
            }
            block.Inlines.Add(run);
        }
    }
}
