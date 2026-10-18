using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace SubScrub.Controls;

/// <summary>An S4 segmented control: a pill track with one crimson pill for the chosen option.
/// Wide: the options share the full width equally (the Settings choices).</summary>
public sealed partial class Segmented : UserControl
{
    private readonly List<RadioButton> _items = [];
    private string _value = "";

    public event Action<string>? Changed;

    public Segmented((string Key, string Label)[] options, bool wide = false)
    {
        var grid = new Grid();
        var group = "seg" + Guid.NewGuid().ToString("N");
        for (var i = 0; i < options.Length; i++)
        {
            var (key, label) = options[i];
            grid.ColumnDefinitions.Add(new ColumnDefinition
            {
                Width = wide ? new GridLength(1, GridUnitType.Star) : GridLength.Auto,
            });
            var item = new RadioButton
            {
                Content = label,
                Tag = key,
                GroupName = group,
                Style = Theme.Style("S4SegItem"),
            };
            Grid.SetColumn(item, i);
            item.Checked += (_, _) =>
            {
                if (_value == key) return;
                _value = key;
                Changed?.Invoke(key);
            };
            _items.Add(item);
            grid.Children.Add(item);
        }
        var track = new Border { Style = Theme.Style("S4SegTrack"), Child = grid };
        if (wide)
            track.HorizontalAlignment = HorizontalAlignment.Stretch;
        HorizontalAlignment = wide ? HorizontalAlignment.Stretch : HorizontalAlignment.Left;
        Content = track;
    }

    public string Value
    {
        get => _value;
        set
        {
            _value = value;
            foreach (var item in _items)
                item.IsChecked = (string)item.Tag == value;
        }
    }
}
