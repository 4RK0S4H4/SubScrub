using System.Globalization;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using SubScrub.Models;

namespace SubScrub.Controls;

/// <summary>A row of the Files list, drawn from the engine's row view. Update() redraws it only when its content
/// changed, so a progress tick on one file doesn't touch the others.</summary>
public sealed partial class FileRow : UserControl
{
    public event Action<int>? ReviewClicked;
    public event Action<int>? RemoveClicked;

    public RowView Row { get; private set; } = new();
    private bool _first;

    public FileRow()
    {
        InitializeComponent();
        ReviewButton.Click += (_, _) => ReviewClicked?.Invoke(Row.Uid);
        RemoveButton.Click += (_, _) => RemoveClicked?.Invoke(Row.Uid);
        PointerEntered += (_, _) => Hover.Opacity = 1;
        PointerExited += (_, _) => Hover.Opacity = 0;
        Tapped += (_, e) =>
        {
            if (Row.Review && !InButton(e.OriginalSource as DependencyObject))
                ReviewClicked?.Invoke(Row.Uid);
        };
        ActualThemeChanged += (_, _) => Paint();
    }

    private bool InButton(DependencyObject? d)
    {
        for (; d != null && !ReferenceEquals(d, this); d = Microsoft.UI.Xaml.Media.VisualTreeHelper.GetParent(d))
        {
            if (d is Button)
                return true;
        }
        return false;
    }

    public FileRow(RowView row, bool first) : this()
    {
        Update(row, first, force: true);
    }

    public void Update(RowView row, bool first, bool force = false)
    {
        if (!force && row.SameAs(Row) && first == _first)
            return;
        Row = row;
        _first = first;
        Separator.Visibility = first ? Visibility.Collapsed : Visibility.Visible;
        NameText.Text = row.Name;
        NameText.Opacity = row.State == "done" ? 0.75 : 1;
        ToolTipService.SetToolTip(NameText, row.Tip);
        KindIcon.Glyph = row.Kind == "subtitle" ? "subtitles" : "clapperboard-play";
        RenameLine.Visibility = row.Rename.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        RenameText.Text = row.Rename;
        DetailText.Text = row.Detail;
        ToolTipService.SetToolTip(DetailText, row.Detail.Length > 60 ? row.Detail : null);
        if (row.Progress is double p)
        {
            RowProgress.Visibility = Visibility.Visible;
            CountPanel.Visibility = Visibility.Collapsed;
            RowProgress.IsIndeterminate = p < 0;
            if (p >= 0)
                RowProgress.Value = p;
        }
        else
        {
            RowProgress.Visibility = Visibility.Collapsed;
            CountPanel.Visibility = row.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
            CountText.Text = row.Count.ToString("N0", CultureInfo.InvariantCulture);
            CountLabel.Text = row.CountLabel;
        }
        Badge.Set(row.Badge, row.BadgeKind);
        ReviewButton.Visibility = row.Review ? Visibility.Visible : Visibility.Collapsed;
        RemoveButton.IsEnabled = row.Removable;
        Paint();
    }

    private void Paint()
    {
        var tone = StateBadge.Tone(Row.BadgeKind);
        Bar.Fill = Theme.Brush(tone switch
        {
            "ok" => "S4Ok",
            "accent" => "S4Btn",
            "bad" => "S4Bad",
            "warn" => "S4Warn",
            _ => "S4MuteLine",
        }, this);
        var (glyph, ink) = Row.RenameTone switch
        {
            "alert" => ("danger-triangle", "S4Warn"),
            "faint" => ("info-circle", "S4Text3"),
            _ => ("arrow-right", "S4Text2"),
        };
        RenameIcon.Glyph = glyph;
        RenameIcon.Foreground = Theme.Brush(ink, this);
        RenameText.Foreground = Theme.Brush(Row.RenameTone switch
        {
            "alert" => "S4Warn",
            "faint" => "S4Text3",
            _ => "S4Text",
        }, this);
    }
}
