using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Data;
using Microsoft.UI.Xaml.Markup;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;
using Path = Microsoft.UI.Xaml.Shapes.Path;

namespace SubScrub.Controls;

/// <summary>A Solar icon (linear, or bold duotone with Duo="True") drawn in the inherited Foreground.</summary>
public sealed partial class SolarIcon : UserControl
{
    public static readonly DependencyProperty GlyphProperty = DependencyProperty.Register(
        nameof(Glyph), typeof(string), typeof(SolarIcon), new PropertyMetadata("", (d, _) => ((SolarIcon)d).Rebuild()));

    public static readonly DependencyProperty DuoProperty = DependencyProperty.Register(
        nameof(Duo), typeof(bool), typeof(SolarIcon), new PropertyMetadata(false, (d, _) => ((SolarIcon)d).Rebuild()));

    public static readonly DependencyProperty SizeProperty = DependencyProperty.Register(
        nameof(Size), typeof(double), typeof(SolarIcon), new PropertyMetadata(18.0, (d, _) => ((SolarIcon)d).Resize()));

    public string Glyph { get => (string)GetValue(GlyphProperty); set => SetValue(GlyphProperty, value); }
    public bool Duo { get => (bool)GetValue(DuoProperty); set => SetValue(DuoProperty, value); }
    public double Size { get => (double)GetValue(SizeProperty); set => SetValue(SizeProperty, value); }

    public SolarIcon()
    {
        IsTabStop = false;
        Resize();
    }

    private void Resize()
    {
        Width = Size;
        Height = Size;
    }

    private void Rebuild()
    {
        var set = Duo ? SolarData.Duotone : SolarData.Linear;
        if (string.IsNullOrEmpty(Glyph) || !set.TryGetValue(Glyph, out var parts))
        {
            Content = null;
            return;
        }
        var canvas = new Canvas { Width = 24, Height = 24 };
        foreach (var part in parts)
        {
            var path = new Path
            {
                Data = (Geometry)XamlBindingHelper.ConvertValue(typeof(Geometry), part.Data),
                Opacity = part.Opacity,
            };
            var ink = new Binding { Source = this, Path = new PropertyPath(nameof(Foreground)) };
            if (part.Fill)
                path.SetBinding(Shape.FillProperty, ink);
            if (part.Stroke > 0)
            {
                path.SetBinding(Shape.StrokeProperty, ink);
                path.StrokeThickness = part.Stroke;
                path.StrokeStartLineCap = PenLineCap.Round;
                path.StrokeEndLineCap = PenLineCap.Round;
                path.StrokeLineJoin = PenLineJoin.Round;
            }
            canvas.Children.Add(path);
        }
        Content = new Viewbox { Child = canvas, Stretch = Stretch.Uniform };
    }
}
