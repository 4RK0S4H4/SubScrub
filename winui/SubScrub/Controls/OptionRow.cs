using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;

namespace SubScrub.Controls;

/// <summary>A setting that is on or off: a Title Case label, a sentence-case description and a switch.</summary>
public sealed partial class OptionRow : UserControl
{
    public ToggleSwitch Switch { get; }

    public event Action<bool>? Toggled;

    public OptionRow(string title, string description, bool on)
    {
        var grid = new Grid { ColumnSpacing = 24, Padding = new Thickness(0, 8, 0, 8) };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var text = new StackPanel { Spacing = 2, VerticalAlignment = VerticalAlignment.Center };
        text.Children.Add(new TextBlock { Text = title, Style = Theme.Style("S4Medium") });
        if (description.Length > 0)
            text.Children.Add(new TextBlock { Text = description, Style = Theme.Style("S4Hint") });
        grid.Children.Add(text);
        Switch = new ToggleSwitch { IsOn = on, Style = Theme.Style("S4Toggle"), OnContent = "", OffContent = "" };
        AutomationProperties.SetName(Switch, title);
        Switch.Toggled += (_, _) => Toggled?.Invoke(Switch.IsOn);
        Grid.SetColumn(Switch, 1);
        grid.Children.Add(Switch);
        Content = grid;
    }

    public bool IsOn
    {
        get => Switch.IsOn;
        set => Switch.IsOn = value;
    }

    public new bool IsEnabled
    {
        get => Switch.IsEnabled;
        set
        {
            Switch.IsEnabled = value;
            Opacity = value ? 1 : 0.55;
        }
    }
}
