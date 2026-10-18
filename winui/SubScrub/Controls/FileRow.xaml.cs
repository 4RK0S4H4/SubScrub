using System.Globalization;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using SubScrub.Models;

namespace SubScrub.Controls;

public sealed partial class FileRow : UserControl
{
    public event Action<FileItem>? ReviewClicked;
    public event Action<FileItem>? RemoveClicked;

    public FileItem Item { get; private set; } = null!;

    public FileRow()
    {
        InitializeComponent();
    }

    public FileRow(FileItem item, bool last) : this()
    {
        Item = item;
        NameText.Text = item.Name;
        if (item.Warning is { } warning)
        {
            WarnText.Text = warning;
            WarnLine.Visibility = Visibility.Visible;
            RenameLine.Visibility = Visibility.Collapsed;
        }
        else if (item.NewName is { } newName)
        {
            RenameText.Text = newName;
        }
        else
        {
            RenameLine.Visibility = Visibility.Collapsed;
        }
        TrackText.Text = item.Tracks;
        if (item.ToClean > 0)
            CountText.Text = item.ToClean.ToString("N0", CultureInfo.InvariantCulture);
        else
            CountPanel.Visibility = Visibility.Collapsed;
        BadgeText.Text = item.Badge;
        BadgeIcon.Glyph = item.State switch
        {
            FileState.Bad => "danger-triangle",
            FileState.Warn => "danger-circle",
            FileState.Mute => "info-circle",
            _ => "check-circle",
        };
        if (last)
            RowRoot.BorderThickness = new Thickness(0);
        ReviewButton.Click += (_, _) => ReviewClicked?.Invoke(Item);
        RemoveButton.Click += (_, _) => RemoveClicked?.Invoke(Item);
        Loaded += (_, _) => VisualStateManager.GoToState(this, item.State.ToString(), false);
        // re-apply the state after a theme switch, so the bar and badge pick up the other theme's colours
        ActualThemeChanged += (_, _) =>
        {
            var other = item.State == FileState.Mute ? FileState.Ok : FileState.Mute;
            VisualStateManager.GoToState(this, other.ToString(), false);
            VisualStateManager.GoToState(this, item.State.ToString(), false);
        };
    }
}
