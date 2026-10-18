using System.Globalization;
using Microsoft.UI;
using Microsoft.UI.Composition.SystemBackdrops;
using Microsoft.UI.Input;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using SubScrub.Controls;
using SubScrub.Models;
using SubScrub.Services;
using SubScrub.Views;
using Windows.Foundation;
using Windows.Graphics;

namespace SubScrub;

public sealed partial class MainWindow : Window
{
    public AppSettings Settings { get; } = new();

    private readonly bool _mica;
    private readonly List<FileItem> _files = [.. SampleData.Files];
    private SettingsDialog? _settingsDialog;           // made once, reused: later opens skip building it

    public MainWindow()
    {
        InitializeComponent();

        // the top bar is the title bar; Windows still draws the caption buttons, tall to match the bar
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(TopBar);
        AppWindow.TitleBar.PreferredHeightOption = TitleBarHeightOption.Tall;
        AppWindow.Title = "SubScrub";
        var icon = Path.Combine(AppContext.BaseDirectory, "Assets", "subscrub.ico");
        if (File.Exists(icon))
            AppWindow.SetIcon(icon);

        // Mica on Windows 11; the S4 page is drawn over it, partly see-through, so the wallpaper tint shows
        _mica = MicaController.IsSupported() && !Shots.NoMica;
        if (_mica)
            SystemBackdrop = new MicaBackdrop();

        if (Shots.Theme is { } theme)
            Settings.Theme = theme;
        ApplyTheme();

        Root.ActualThemeChanged += (_, _) => OnThemeChanged();
        Shell.SizeChanged += (_, _) => PlaceBlooms();
        TopBar.SizeChanged += (_, _) => UpdateTitleBarRegions();
        Root.Loaded += (_, _) =>
        {
            OnThemeChanged();
            UpdateTitleBarRegions();
        };

        SizeWindow(Shots.Size ?? (1280, 800));
        FillFiles();

        if (Shots.Dir != null)
            Root.Loaded += async (_, _) => await Shots.RunAsync(this);
    }

    // ================================================================ theme

    /// <summary>Light, dark or Windows' choice (live).</summary>
    public void ApplyTheme()
    {
        Root.RequestedTheme = Settings.Theme switch
        {
            "light" => ElementTheme.Light,
            "dark" => ElementTheme.Dark,
            _ => ElementTheme.Default,
        };
        if (_settingsDialog != null)
            _settingsDialog.RequestedTheme = Root.RequestedTheme;
    }

    public bool IsDark => Root.ActualTheme == ElementTheme.Dark;

    private void OnThemeChanged()
    {
        var dark = IsDark;
        PageLayer.Opacity = _mica ? (dark ? 0.84 : 0.55) : 1.0;
        ThemeIcon.Glyph = dark ? "sun" : "moon";
        var tip = dark ? "Switch to light mode" : "Switch to dark mode";
        ToolTipService.SetToolTip(ThemeButton, tip);
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(ThemeButton, tip);

        var bar = AppWindow.TitleBar;
        bar.ButtonBackgroundColor = Colors.Transparent;
        bar.ButtonInactiveBackgroundColor = Colors.Transparent;
        bar.ButtonForegroundColor = Theme.Color("S4TextColor", dark);
        bar.ButtonInactiveForegroundColor = Theme.Color("S4Text3Color", dark);
        bar.ButtonHoverBackgroundColor = Theme.Color("S4HovColor", dark);
        bar.ButtonHoverForegroundColor = Theme.Color("S4TextColor", dark);
        bar.ButtonPressedBackgroundColor = Theme.Color("S4PressColor", dark);
        bar.ButtonPressedForegroundColor = Theme.Color("S4TextColor", dark);
        PlaceBlooms();
    }

    private void OnThemeClick(object sender, RoutedEventArgs e)
    {
        Settings.Theme = IsDark ? "light" : "dark";
        ApplyTheme();
    }

    /// <summary>The two dark-mode blooms: radial-gradient(rx ry at x y, colour, transparent 60%).</summary>
    private void PlaceBlooms()
    {
        double w = Shell.ActualWidth, h = Shell.ActualHeight;
        if (w <= 0 || h <= 0)
            return;
        Place(Bloom0, "S4Bloom0Geometry", w, h);
        Place(Bloom1, "S4Bloom1Geometry", w, h);
    }

    private static void Place(RadialGradientBrush brush, string key, double w, double h)
    {
        var p = ((string)Application.Current.Resources[key]).Split(',')
            .Select(x => double.Parse(x, CultureInfo.InvariantCulture)).ToArray();
        var centre = new Point(p[0] * w, p[1] * h);
        brush.Center = centre;
        brush.GradientOrigin = centre;
        brush.RadiusX = p[2];
        brush.RadiusY = p[3];
    }

    // ================================================================ window and title bar

    private void SizeWindow((int W, int H) size)
    {
        var scale = Shots.Dir != null ? 1.0 : Dpi.Scale(this);
        var w = (int)(size.W * scale);
        var h = (int)(size.H * scale);
        var area = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        AppWindow.MoveAndResize(new RectInt32(area.X + Math.Max(0, (area.Width - w) / 2),
                                              area.Y + Math.Max(0, (area.Height - h) / 2), w, h));
    }

    /// <summary>Keep the right-hand actions clear of the caption buttons, and let clicks through to the tabs and
    /// buttons (the rest of the top bar drags the window).</summary>
    private void UpdateTitleBarRegions()
    {
        if (Root.XamlRoot == null)
            return;
        var scale = Root.XamlRoot.RasterizationScale;
        TopBar.Padding = new Thickness(16, 0, AppWindow.TitleBar.RightInset / scale + 8, 0);
        DispatcherQueue.TryEnqueue(() =>
        {
            if (Root.XamlRoot == null)
                return;
            var rects = new FrameworkElement[] { Tabs, RightActions }.Select(e => Bounds(e, scale)).ToArray();
            InputNonClientPointerSource.GetForWindowId(AppWindow.Id)
                .SetRegionRects(NonClientRegionKind.Passthrough, rects);
        });
    }

    private static RectInt32 Bounds(FrameworkElement e, double scale)
    {
        var b = e.TransformToVisual(null).TransformBounds(new Rect(0, 0, e.ActualWidth, e.ActualHeight));
        return new RectInt32((int)Math.Round(b.X * scale), (int)Math.Round(b.Y * scale),
                             (int)Math.Round(b.Width * scale), (int)Math.Round(b.Height * scale));
    }

    // ================================================================ Files

    private void FillFiles()
    {
        FileList.Children.Clear();
        for (var i = 0; i < _files.Count; i++)
        {
            var row = new FileRow(_files[i], i == _files.Count - 1);
            row.ReviewClicked += _ => ReviewTab.IsChecked = true;
            row.RemoveClicked += item =>
            {
                _files.Remove(item);
                FillFiles();
            };
            FileList.Children.Add(row);
        }
        var ready = _files.Count(f => f.State == FileState.Ok);
        var check = _files.Count - ready;
        FilesCount.Text = _files.Count.ToString(CultureInfo.InvariantCulture);
        FilesSummary.Text = check > 0 ? $"{ready} ready, {check} to check" : $"{ready} ready";
        FilesSubSummary.Text = "1 subtitle file matched to 1 video.";
        var lines = _files.Sum(f => f.ToClean);
        StatusText.Text = $"{lines.ToString("N0", CultureInfo.InvariantCulture)} lines to clean";
        PrimaryButton.Content = _files.Count == 1 ? "Clean and Save 1 File" : $"Clean and Save {_files.Count} Files";
        // nothing left to do: hide the primary button rather than grey it out
        PrimaryButton.Visibility = _files.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
    }

    // ================================================================ tabs

    private void OnTabChecked(object sender, RoutedEventArgs e)
    {
        if (FilesPage == null || ReviewPage == null)
            return;                                     // during InitializeComponent
        var review = ReviewTab.IsChecked == true;
        FilesPage.Visibility = review ? Visibility.Collapsed : Visibility.Visible;
        ReviewPage.Visibility = review ? Visibility.Visible : Visibility.Collapsed;
    }

    private void OnGoToFiles(object sender, RoutedEventArgs e) => FilesTab.IsChecked = true;

    public void ShowTab(bool review) => (review ? ReviewTab : FilesTab).IsChecked = true;

    // ================================================================ Settings

    private async void OnSettingsClick(object sender, RoutedEventArgs e) => await ShowSettingsAsync();

    public SettingsDialog Dialog => _settingsDialog ??= new SettingsDialog(this);

    public async Task ShowSettingsAsync(string section = "videos")
    {
        var dialog = Dialog;
        dialog.Prepare(section);
        await dialog.ShowAsync();
    }
}
