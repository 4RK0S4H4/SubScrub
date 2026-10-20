using System.Globalization;
using Microsoft.UI;
using Microsoft.UI.Composition.SystemBackdrops;
using Microsoft.UI.Input;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;
using SubScrub.Controls;
using SubScrub.Models;
using SubScrub.Services;
using SubScrub.Views;
using Windows.ApplicationModel.DataTransfer;
using Windows.Foundation;
using Windows.Graphics;
using Windows.Storage.Pickers;
using Windows.System;
using Windows.UI.Core;

namespace SubScrub;

public sealed partial class MainWindow : Window
{
    public SettingsView Settings { get; private set; } = new();
    public Core? Core { get; private set; }
    public FilesView Files { get; private set; } = new();
    public ReviewView? Review { get; private set; }
    public PreviewPanel PreviewList => Preview;

    /// <summary>The scripted QA run answers dialogs through this (Services/Shots.cs); null for people.</summary>
    public Func<IChooser, Task>? AutoAnswer { get; set; }

    private static readonly string[] VideoExts = [".mkv", ".mp4", ".m4v", ".mov", ".webm", ".avi", ".ts", ".m2ts", ".mts", ".wmv", ".ogm", ".mk3d"];
    private static readonly string[] SubExts = [".srt", ".ass", ".ssa", ".vtt"];

    private readonly bool _mica;
    private readonly Dictionary<int, FileRow> _rows = [];
    private readonly Dictionary<string, RuleRow> _ruleRows = [];
    private SettingsDialog? _settingsDialog;           // made once, reused: later opens skip building it
    private Segmented? _filter;
    private bool _filterHasCheck;
    private readonly Segmented _lyrics = new([("italic", "Italic, No ♪"), ("keep", "Keep ♪"), ("remove", "Remove")], wide: true);
    private int? _reviewUid;
    private string _show = "changes";
    private bool _dialogOpen;
    private bool _closing;

    /// <summary>When the window first drew (for the QA run's timings).</summary>
    public DateTime? FirstFrame { get; private set; }
    private DispatcherQueueTimerWrapper? _ruleTimer;
    private TaskCompletionSource<SaveDone>? _saveWait;

    public MainWindow()
    {
        InitializeComponent();

        // the top bar is the title bar; Windows still draws the caption buttons, tall to match the bar
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(TopBar);
        AppWindow.TitleBar.PreferredHeightOption = TitleBarHeightOption.Tall;
        AppWindow.Title = "SubScrub";
        var icon = System.IO.Path.Combine(AppContext.BaseDirectory, "Assets", "subscrub.ico");
        if (File.Exists(icon))
            AppWindow.SetIcon(icon);

        // Mica on Windows 11; the S4 page is drawn over it, partly see-through, so the wallpaper tint shows
        _mica = MicaController.IsSupported() && !Shots.NoMica;
        if (_mica)
            SystemBackdrop = new MicaBackdrop();

        Settings.Theme = Shots.Theme ?? StoredTheme();
        ApplyTheme();

        Root.ActualThemeChanged += (_, _) => OnThemeChanged();
        Shell.SizeChanged += (_, _) => PlaceBlooms();
        TopBar.SizeChanged += (_, _) => UpdateTitleBarRegions();
        Root.Loaded += (_, _) =>
        {
            FirstFrame ??= DateTime.Now;
            OnThemeChanged();
            UpdateTitleBarRegions();
            WelcomeScreen.PlayDemo();
        };
        Root.SizeChanged += (_, _) => FitReview();

        BuildReview();
        WireInput();
        if (!RestorePlacement())
            SizeWindow(Shots.Size ?? (1340, 900));
        else
            SizeWindow((0, 0), minimumOnly: true);
        TrackPlacement();
        ShowPage("welcome");
        StartCore();

        AppWindow.Closing += OnClosing;
        Activated += (_, e) =>
        {
            if (e.WindowActivationState != WindowActivationState.Deactivated && Files.Count > 0 && !Files.Busy && !_dialogOpen)
                _ = Safe(() => Core!.Do("locks"));
        };
        if (Shots.Dir != null)
            Root.Loaded += async (_, _) => await Shots.RunAsync(this);
    }

    /// <summary>The theme the Qt app and this one share (HKCU\Software\SubScrub\SubScrub\theme), read before the
    /// engine is up so the window opens in the right colours.</summary>
    private static string StoredTheme()
    {
        try
        {
            using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(@"Software\SubScrub\SubScrub");
            var stored = key?.GetValue("theme") as string;
            return stored is "light" or "dark" or "system" ? stored : "system";
        }
        catch (Exception)
        {
            return "system";
        }
    }

    // ================================================================ the engine

    private void StartCore()
    {
        var args = new List<string>();
        if (Shots.Dir != null)
        {
            // a throwaway settings file (the user's real settings and TMDB key are never touched) and a stand-in TMDB
            args.AddRange(["--settings", System.IO.Path.Combine(Shots.Dir, "_settings.json"), "--demo-tmdb",
                           "--log", System.IO.Path.Combine(Shots.Dir, $"engine-{Shots.Theme ?? "x"}.log")]);
        }
        else if (Environment.GetCommandLineArgs() is var cl && Array.IndexOf(cl, "--log") is var at and > 0 && at + 1 < cl.Length)
            args.AddRange(["--log", cl[at + 1]]);          // the engine's log to a file too (support, CI's checks)
        try
        {
            Core = Services.Core.Start(DispatcherQueue, args);
        }
        catch (Exception e)
        {
            StatusText.Text = "SubScrub's engine didn't start: " + e.Message;
            Footer.Visibility = Visibility.Visible;
            return;
        }
        Core.Files += ApplyFiles;
        Core.ReviewChanged += uid =>
        {
            if (uid == _reviewUid && ReviewPage.Visibility == Visibility.Visible)
                _ = RefreshReviewAsync(keep: true);
        };
        Core.SaveDone += OnSaveDone;
        Core.UpdateProgress += OnUpdateProgress;
        Core.Died += why =>
        {
            if (_closing)
                return;
            StatusText.Text = "SubScrub's engine stopped. Restart SubScrub.";
            Log.Write(why);
        };
        _ = OnReadyAsync();
    }

    private async Task OnReadyAsync()
    {
        try
        {
            var hello = await Core!.WhenReady;
            Settings = hello.Settings;
            if (Shots.Theme is { } t)
                Settings.Theme = t;
            ApplyTheme();
            ApplyTools(hello.Tools);
            StartUpdateCheck();
            var paths = Program.PathsOn(Environment.GetCommandLineArgs().Skip(1));
            if (paths.Count > 0 && Shots.Dir == null)
                await AddPathsAsync(paths);
            if (Shots.Dir == null)
                TakeForwardedPaths();
        }
        catch (Exception e)
        {
            StatusText.Text = "SubScrub's engine didn't start. " + e.Message;
            Footer.Visibility = Visibility.Visible;
            Log.Write(e.ToString());
        }
    }

    /// <summary>Run an engine call, showing its error instead of crashing.</summary>
    private async Task Safe(Func<Task> work)
    {
        try
        {
            await work();
        }
        catch (CoreException e)
        {
            if (!_closing)
                await TellAsync(new Message { Title = "Something went wrong", Text = e.Message });
        }
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
        ApplyStatusTone();
        ApplyTools(Files.Tools.Ffmpeg.Length > 0 || Core?.Ready == null ? Files.Tools : Core.Ready.Tools);
        if (Review != null)
            ApplyLegend(Review.Legend);
        BuildKeyHints();
    }

    private void OnThemeClick(object sender, RoutedEventArgs e)
    {
        Settings.Theme = IsDark ? "light" : "dark";
        ApplyTheme();
        _ = Safe(() => Core!.Do("theme", new { mode = Settings.Theme }));
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

    private void SizeWindow((int W, int H) size, bool minimumOnly = false)
    {
        var scale = Shots.Dir != null ? 1.0 : Dpi.Scale(this);
        if (minimumOnly)
        {
            if (AppWindow.Presenter is OverlappedPresenter mp)
            {
                mp.PreferredMinimumWidth = (int)(1000 * scale);
                mp.PreferredMinimumHeight = (int)(680 * scale);
            }
            return;
        }
        var area = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        var w = (int)(size.W * scale);
        var h = (int)(size.H * scale);
        if (Shots.Dir == null)
        {
            w = Math.Min(w, (int)(area.Width * 0.92));
            h = Math.Min(h, (int)(area.Height * 0.92));
        }
        AppWindow.MoveAndResize(new RectInt32(area.X + Math.Max(0, (area.Width - w) / 2),
                                              area.Y + Math.Max(0, (area.Height - h) / 2), w, h));
        if (AppWindow.Presenter is OverlappedPresenter p && Shots.Dir == null)
        {
            p.PreferredMinimumWidth = (int)(1000 * scale);
            p.PreferredMinimumHeight = (int)(680 * scale);
        }
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

    private async void OnClosing(AppWindow sender, AppWindowClosingEventArgs e)
    {
        if (_closing)
            return;
        SavePlacement();
        if (Files.Busy && Core != null)
        {
            e.Cancel = true;
            var (choice, _) = await AskAsync(new Question
            {
                Key = "closing", Title = "Still saving",
                Text = "Files are still being written. Stop and discard the one in progress?",
                Buttons = [new() { Key = "cancel", Label = "Keep Saving" }, new() { Key = "stop", Label = "Stop and Close", Kind = "primary" }],
            });
            if (choice != "stop")
                return;
            _saveWait = new TaskCompletionSource<SaveDone>();
            await Core.Do("cancel");
            StatusText.Text = "Stopping…";
            await Task.WhenAny(_saveWait.Task, Task.Delay(TimeSpan.FromMinutes(5)));
            _closing = true;
            Core.Dispose();
            Close();
            return;
        }
        _closing = true;
        Core?.Dispose();
    }

    // ================================================================ pages and tabs

    private void ShowPage(string page)
    {
        WelcomePage.Visibility = page == "welcome" ? Visibility.Visible : Visibility.Collapsed;
        FilesPage.Visibility = page == "files" ? Visibility.Visible : Visibility.Collapsed;
        ReviewPage.Visibility = page == "review" ? Visibility.Visible : Visibility.Collapsed;
        Footer.Visibility = page == "welcome" ? Visibility.Collapsed : Visibility.Visible;
        AppWindow.Title = page == "review" && Review != null ? $"{Review.Head.Name} – SubScrub" : "SubScrub";
        ApplyButtons();
    }

    private bool _tabSync;

    private void OnTabChecked(object sender, RoutedEventArgs e)
    {
        if (FilesPage == null || ReviewPage == null || _tabSync)
            return;                                     // during InitializeComponent
        if (ReviewTab.IsChecked == true)
        {
            var uid = _reviewUid is int u && _rows.ContainsKey(u) ? u : Files.Rows.FirstOrDefault()?.Uid;
            if (uid is int target)
                _ = OpenReviewAsync(target);
            else
                ShowTab(review: false);
        }
        else
            ShowFiles();
    }

    public void ShowTab(bool review)
    {
        _tabSync = true;
        (review ? ReviewTab : FilesTab).IsChecked = true;
        _tabSync = false;
        if (!review)
            ShowFiles();
    }

    private void ShowFiles() => ShowPage(Files.Count > 0 ? "files" : "welcome");

    // ================================================================ Files

    private void ApplyFiles(FilesView v)
    {
        var hadNone = Files.Count == 0;
        Files = v;
        FilesCount.Text = v.Count > 0 ? v.Count.ToString(CultureInfo.InvariantCulture) : "";
        ReviewTab.IsEnabled = v.Count > 0 && !v.Busy;
        FilesSummary.Text = v.Summary;
        FilesNote.Text = v.Note;
        FilesNote.Visibility = v.Note.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        SaveHow.Text = v.How;
        LocksBanner.Text = v.Locks;
        LocksBanner.Visibility = v.Locks.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        ClearButton.Visibility = v.Buttons.ClearVisible ? Visibility.Visible : Visibility.Collapsed;
        AddFilesButton.IsEnabled = AddFolderButton.IsEnabled = !v.Busy;
        SettingsButton.IsEnabled = !v.Busy;
        ApplyTools(v.Tools);

        // rows: update in place; rebuild only when files came, went or moved
        var order = v.Rows.Select(r => r.Uid).ToList();
        var current = FileList.Children.OfType<FileRow>().Select(r => r.Row.Uid).ToList();
        if (!order.SequenceEqual(current))
        {
            foreach (var gone in _rows.Keys.Except(order).ToList())
                _rows.Remove(gone);
            FileList.Children.Clear();
            for (var i = 0; i < v.Rows.Count; i++)
            {
                var r = v.Rows[i];
                if (!_rows.TryGetValue(r.Uid, out var row))
                {
                    row = new FileRow(r, i == 0);
                    row.ReviewClicked += uid => _ = OpenReviewAsync(uid);
                    row.RemoveClicked += uid => _ = Safe(() => Core!.Do("remove", new { uid }));
                    _rows[r.Uid] = row;
                }
                else
                    row.Update(r, i == 0);
                FileList.Children.Add(row);
            }
        }
        else
        {
            for (var i = 0; i < v.Rows.Count; i++)
                _rows[v.Rows[i].Uid].Update(v.Rows[i], i == 0);
        }

        if (v.Count == 0)
        {
            _reviewUid = null;
            Review = null;
            if (WelcomePage.Visibility != Visibility.Visible)
            {
                ShowTab(review: false);
                WelcomeScreen.PlayDemo();
            }
        }
        else if (hadNone && WelcomePage.Visibility == Visibility.Visible)
            ShowTab(review: false);
        else if (_reviewUid is int u && !_rows.ContainsKey(u))
        {
            _reviewUid = null;
            if (ReviewPage.Visibility == Visibility.Visible)
                ShowTab(review: false);
        }
        StatusText.Text = v.Status.Text;
        ApplyStatusTone();
        if (v.Status.Progress is double p)
        {
            Progress.Visibility = Visibility.Visible;
            Progress.IsIndeterminate = p < 0;
            if (p >= 0)
                Progress.Value = p;
        }
        else
            Progress.Visibility = Visibility.Collapsed;
        ApplyButtons();
        if (_saveWait != null && !v.Busy)
            _saveWait.TrySetResult(new SaveDone());
    }

    private void ApplyStatusTone()
    {
        StatusText.Foreground = Theme.Brush(Files.Status.Tone switch
        {
            "good" => "S4Ok",
            "bad" => "S4Bad",
            _ => "S4Text2",
        }, Root);
    }

    private void ApplyButtons()
    {
        var b = Files.Buttons;
        PrimaryButton.Content = b.Primary;
        PrimaryButton.Visibility = b.PrimaryVisible ? Visibility.Visible : Visibility.Collapsed;
        PrimaryButton.IsEnabled = b.PrimaryEnabled;
        StopButton.Visibility = b.StopVisible ? Visibility.Visible : Visibility.Collapsed;
        OpenButton.Visibility = b.OpenVisible ? Visibility.Visible : Visibility.Collapsed;
        var one = ReviewPage.Visibility == Visibility.Visible && Review != null && Review.Head.SaveOne && !Files.Busy
                  && _rows.TryGetValue(Review.Uid, out var row) && row.Row.State is not ("done" or "saving");
        SaveOneButton.Visibility = one ? Visibility.Visible : Visibility.Collapsed;
    }

    private void ApplyTools(ToolsView t)
    {
        var ready = t.Ready || Core?.Ready == null && t.Ffmpeg.Length == 0;
        ToolsText.Text = ready ? "Tools Ready" : "FFmpeg Needed";
        ToolsIcon.Glyph = ready ? "check-read" : "danger-triangle";
        var ink = Theme.Brush(ready ? "S4Text2" : "S4Warn", Root);
        ToolsIcon.Foreground = ink;
        ToolsText.Foreground = ink;
        ToolTipService.SetToolTip(ToolsButton, ready ? "FFmpeg and MKVToolNix found" : "FFmpeg isn't installed yet");
        WelcomeToolsBanner.Visibility = ready ? Visibility.Collapsed : Visibility.Visible;
    }

    // ================================================================ adding files

    private async void OnAddFilesClick(object sender, RoutedEventArgs e)
    {
        if (Files.Busy)
            return;
        var picker = new FileOpenPicker { ViewMode = PickerViewMode.List, SuggestedStartLocation = PickerLocationId.VideosLibrary };
        foreach (var ext in VideoExts.Concat(SubExts))
            picker.FileTypeFilter.Add(ext);
        WinRT.Interop.InitializeWithWindow.Initialize(picker, Dpi.Handle(this));
        var files = await picker.PickMultipleFilesAsync();
        if (files is { Count: > 0 })
            await AddPathsAsync(files.Select(f => f.Path).ToList());
    }

    private async void OnAddFolderClick(object sender, RoutedEventArgs e)
    {
        if (Files.Busy)
            return;
        var picker = new FolderPicker { SuggestedStartLocation = PickerLocationId.VideosLibrary };
        picker.FileTypeFilter.Add("*");
        WinRT.Interop.InitializeWithWindow.Initialize(picker, Dpi.Handle(this));
        var folder = await picker.PickSingleFolderAsync();
        if (folder != null)
            await AddPathsAsync([folder.Path]);
    }

    /// <summary>Add files and folders, after offering to tidy up unfinished copies from an interrupted save.</summary>
    public async Task AddPathsAsync(List<string> paths)
    {
        if (Core == null || Files.Busy)
            return;
        try
        {
            var leftovers = await Core.Call<List<Leftover>>("leftovers", new { paths });
            await OfferLeftoversAsync(paths, leftovers);
            var reply = await Core.Call<AddReply>("add", new { paths });
            foreach (var m in reply.Messages)
            {
                await TellAsync(m);
                if (m.Tools)
                    await ShowToolsAsync();
            }
            if (reply.Added > 0 && ReviewPage.Visibility == Visibility.Visible)
                ShowTab(review: false);
        }
        catch (CoreException e)
        {
            await TellAsync(new Message { Title = "Couldn't add these", Text = e.Message });
        }
    }

    private async Task OfferLeftoversAsync(List<string> paths, List<Leftover> leftovers)
    {
        var have = leftovers.Where(l => l.Original).ToList();
        if (have.Count > 0)
        {
            var (choice, _) = await AskAsync(new Question
            {
                Key = "leftovers", Title = "Unfinished Copies Found",
                Text = "An earlier save was interrupted and left unfinished copies next to these videos. The videos themselves are fine. Move the leftovers to the Recycle Bin?",
                Rows = have.Take(8).Select(l => new List<string> { l.Name, l.Size }).ToList(), More = Math.Max(0, have.Count - 8),
                Buttons = [new() { Key = "keep", Label = "Keep Them" }, new() { Key = "remove", Label = "Move to Recycle Bin", Kind = "primary" }],
            });
            if (choice == "remove")
            {
                var res = await Core!.CallRaw("leftovers_action", new { paths, action = "remove" });
                var failed = res?["failed"]?.AsArray().Select(x => new List<string> { x![0]!.GetValue<string>(), x[1]!.GetValue<string>() }).ToList() ?? [];
                if (failed.Count > 0)
                    await TellAsync(new Message { Title = "Couldn't Remove Everything", Text = "These stay where they are:", Rows = failed });
            }
        }
        foreach (var lo in leftovers.Where(l => !l.Original))
        {
            var (choice, _) = await AskAsync(new Question
            {
                Key = "missing", Title = "The Original Is Missing",
                Text = $"“{lo.Name}” is all that is left of this video: the original isn't in the folder. It may be incomplete, so nothing was removed.",
                Detail = "Renaming gives it the original name back. Check that it plays before relying on it.",
                Buttons = [new() { Key = "leave", Label = "Leave It" }, new() { Key = "restore", Label = "Rename It Back", Kind = "primary" }],
            });
            if (choice == "restore")
                await Core!.Do("leftovers_action", new { paths, action = "restore", which = new[] { lo.Path } });
        }
    }

    private void OnClearClick(object sender, RoutedEventArgs e) => _ = Safe(() => Core!.Do("clear"));

    // ================================================================ input: keys and drag and drop

    private void WireInput()
    {
        Root.DragOver += (_, e) =>
        {
            if (!e.DataView.Contains(StandardDataFormats.StorageItems) || Files.Busy || _dialogOpen)
                return;
            e.AcceptedOperation = DataPackageOperation.Copy;
            e.DragUIOverride.IsCaptionVisible = false;
            e.DragUIOverride.IsGlyphVisible = false;
            DropText.Text = Files.Count > 0 ? "Drop to Add" : "Drop to Open";
            DropVeil.Visibility = Visibility.Visible;
        };
        Root.DragLeave += (_, _) => DropVeil.Visibility = Visibility.Collapsed;
        Root.Drop += async (_, e) =>
        {
            DropVeil.Visibility = Visibility.Collapsed;
            if (!e.DataView.Contains(StandardDataFormats.StorageItems))
                return;
            var items = await e.DataView.GetStorageItemsAsync();
            var paths = items.Select(i => i.Path).Where(p => !string.IsNullOrEmpty(p)).ToList();
            if (paths.Count > 0)
                await AddPathsAsync(paths);
        };
        Root.PreviewKeyDown += OnKey;
        LocksBanner.ActionClicked += () => _ = Safe(() => Core!.Do("locks"));
        WelcomeToolsBanner.ActionClicked += () => _ = ShowToolsAsync();
        NoSdhBanner.ActionClicked += () => _ = ReviewCall("force_clean", new { uid = _reviewUid, show = _show });
    }

    private static bool Down(VirtualKey key) =>
        InputKeyboardSource.GetKeyStateForCurrentThread(key).HasFlag(CoreVirtualKeyStates.Down);

    private void OnKey(object sender, KeyRoutedEventArgs e)
    {
        if (_dialogOpen)
            return;
        var ctrl = Down(VirtualKey.Control);
        var shift = Down(VirtualKey.Shift);
        var typing = FocusManager.GetFocusedElement(Root.XamlRoot) is TextBox or PasswordBox;
        var review = ReviewPage.Visibility == Visibility.Visible && !Files.Busy;
        switch (e.Key)
        {
            case VirtualKey.O when ctrl:
                OnAddFilesClick(this, new RoutedEventArgs());
                break;
            case VirtualKey.S when ctrl:
                if (Files.Buttons.PrimaryVisible && Files.Buttons.PrimaryEnabled)
                    _ = StartSaveAsync(null);
                break;
            case (VirtualKey)188 when ctrl:                  // Ctrl+,
                _ = ShowSettingsAsync();
                break;
            case VirtualKey.F3 when review:
                Preview.Step(shift ? -1 : 1);
                break;
            case VirtualKey.Z when ctrl && review:
                _ = ToggleCurrentAsync();
                break;
            case VirtualKey.J when review && !typing && !ctrl:
                Preview.Move(1);
                break;
            case VirtualKey.K when review && !typing && !ctrl:
                Preview.Move(-1);
                break;
            case VirtualKey.O when review && !typing:
                _ = ToggleCurrentAsync();
                break;
            case VirtualKey.Escape when review:
                ShowTab(review: false);
                break;
            case VirtualKey.Left when review && Down(VirtualKey.Menu):
                ShowTab(review: false);
                break;
            default:
                return;
        }
        e.Handled = true;
    }

    // ================================================================ Review

    private void BuildReview()
    {
        _lyrics.Changed += _ => QueueRules();
        LyricsHost.Content = _lyrics;
        Preview.ToggleRequested += cue => _ = ToggleAsync(cue);
        Preview.PositionChanged += UpdateNav;
        ReviewMain.SizeChanged += (_, _) => FitReview();
        BuildKeyHints();
    }

    private void BuildKeyHints()
    {
        KeyHints.Children.Clear();
        foreach (var (keys, label) in new[] { (new[] { "J", "K" }, "Next or previous line"), (new[] { "O" }, "Keep original"),
                                              (new[] { "F3" }, "Next change"), (new[] { "Ctrl", "Z" }, "Undo a line") })
        {
            var item = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 4 };
            foreach (var k in keys)
            {
                item.Children.Add(new Border
                {
                    CornerRadius = new CornerRadius(5), BorderThickness = new Thickness(1), Padding = new Thickness(6, 1, 6, 2),
                    BorderBrush = Theme.Brush("S4Border2", Root), Background = Theme.Brush("S4Glass2", Root),
                    Child = new TextBlock { Text = k, FontFamily = Theme.Font("S4FontSemiBold"), FontSize = 11, Foreground = Theme.Brush("S4Text2", Root) },
                });
            }
            item.Children.Add(new TextBlock { Text = label, Style = Theme.Style("S4Quiet"), FontSize = 12, Margin = new Thickness(4, 0, 0, 0), VerticalAlignment = VerticalAlignment.Center, Foreground = Theme.Brush("S4Text3", Root) });
            KeyHints.Children.Add(item);
        }
    }

    public async Task OpenReviewAsync(int uid, string? show = null)
    {
        if (Core == null || Files.Busy)
            return;
        try
        {
            var first = uid != _reviewUid || ReviewPage.Visibility != Visibility.Visible;
            var view = await Core.Call<ReviewView>("review", new { uid, show, first = first && show == null });
            _reviewUid = uid;
            _tabSync = true;
            ReviewTab.IsChecked = true;
            _tabSync = false;
            ApplyReview(view, keep: !first);
            ShowPage("review");
        }
        catch (CoreException e)
        {
            await TellAsync(new Message { Title = "Couldn't open this file", Text = e.Message });
        }
    }

    private async Task RefreshReviewAsync(bool keep)
    {
        if (Core == null || _reviewUid is not int uid)
            return;
        try
        {
            var view = await Core.Call<ReviewView>("review", new { uid, show = _show });
            ApplyReview(view, keep);
        }
        catch (CoreException)
        {
            // the file went away: the files view takes the window back to Files
        }
    }

    private async Task ReviewCall(string cmd, object args, int? selectCue = null)
    {
        try
        {
            var view = await Core!.Call<ReviewView>(cmd, args);
            ApplyReview(view, keep: true, selectCue);
        }
        catch (CoreException e)
        {
            await TellAsync(new Message { Title = "Something went wrong", Text = e.Message });
        }
    }

    private void ApplyReview(ReviewView v, bool keep, int? selectCue = null)
    {
        var same = Review?.Uid == v.Uid;
        Review = v;
        _show = v.Filter;
        ApplyHead(v.Head);
        AppWindow.Title = $"{v.Head.Name} – SubScrub";

        // sidebar: tracks
        TrackCount.Text = v.TrackCount;
        FileMeta.Text = v.Meta;
        TrackList.Children.Clear();
        foreach (var t in v.Tracks)
        {
            var tile = new TrackTile(t);
            tile.Click += (_, _) =>
            {
                if (!t.Selected)
                    _ = ReviewCall("select_track", new { uid = v.Uid, index = t.Index, show = _show });
            };
            TrackList.Children.Add(tile);
        }
        TracksNote.Text = v.TracksNote;
        TracksNote.Visibility = v.TracksNote.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        ApplyRules(v.Rules, v.Busy);

        // toolbar
        if (_filter == null || _filterHasCheck != v.CheckOption)
        {
            _filterHasCheck = v.CheckOption;
            var options = v.CheckOption
                ? new[] { ("check", "To Check"), ("changes", "Changes"), ("all", "Everything") }
                : new[] { ("changes", "Changes"), ("all", "Everything") };
            _filter = new Segmented(options);
            _filter.Changed += key => _ = ReviewCall("review", new { uid = _reviewUid, show = key });
            FilterHost.Content = _filter;
        }
        _filter.Value = v.Filter;
        ApplyLegend(v.Legend);

        // the preview
        Preview.SetPreviewOnly(v.PreviewOnly);
        if (v.Empty.Length > 0)
        {
            PreviewEmpty.Text = v.Empty;
            PreviewEmpty.Visibility = Visibility.Visible;
            Preview.Visibility = Visibility.Collapsed;
        }
        else
        {
            PreviewEmpty.Visibility = Visibility.Collapsed;
            Preview.Visibility = Visibility.Visible;
            Preview.SetData(v, keep && same, selectCue);
        }
        var nav = v.Nav && v.Empty.Length == 0;
        ChangeNav.Visibility = nav ? Visibility.Visible : Visibility.Collapsed;
        KeepButton.Visibility = nav ? Visibility.Visible : Visibility.Collapsed;
        UpdateNav();
        FitReview();
        ApplyButtons();
    }

    private void ApplyHead(HeadView h)
    {
        ReviewTitle.Text = h.Name;
        ToolTipService.SetToolTip(ReviewTitle, h.Path);
        ReviewBadge.Set(h.Badge, h.BadgeKind);
        ReviewDetail.Text = h.Detail;
        FileNav.Visibility = h.Multi ? Visibility.Visible : Visibility.Collapsed;
        NoSdhBanner.Text = h.Nosdh;
        NoSdhBanner.Visibility = h.Nosdh.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
        NameRow.Visibility = h.NameRow ? Visibility.Visible : Visibility.Collapsed;
        NameText.Text = h.NameText;
        NameIcon.Glyph = h.NameTone == "alert" ? "danger-triangle" : "arrow-right";
        NameIcon.Foreground = Theme.Brush(h.NameTone == "alert" ? "S4Warn" : "S4Text2", Root);
        NameText.Foreground = Theme.Brush(h.NameTone == "alert" ? "S4Warn" : "S4Text", Root);
    }

    private void ApplyRules(RulesView r, bool busy)
    {
        if (_ruleRows.Count == 0)
        {
            foreach (var rule in r.Rows)
            {
                var row = new RuleRow(rule.Key, rule.Title, rule.Example);
                row.Toggled += (_, _) => QueueRules();
                _ruleRows[rule.Key] = row;
                RuleList.Children.Add(row);
            }
        }
        foreach (var rule in r.Rows)
        {
            if (_ruleRows.TryGetValue(rule.Key, out var row))
                row.Set(rule, !busy);
        }
        _lyrics.Value = r.Lyrics;
        LyricCount.Text = r.LyricCount > 0 ? r.LyricCount.ToString("N0", CultureInfo.InvariantCulture) : "";
        EditsState.Text = r.State;
        PromoteButton.Visibility = r.Custom ? Visibility.Visible : Visibility.Collapsed;
        ResetRulesButton.Visibility = r.Custom ? Visibility.Visible : Visibility.Collapsed;
    }

    /// <summary>Switches are applied together, a moment after the last click (like the Qt app's 90 ms timer).</summary>
    private void QueueRules()
    {
        _ruleTimer?.Stop();
        _ruleTimer = new DispatcherQueueTimerWrapper(DispatcherQueue, TimeSpan.FromMilliseconds(90), () =>
        {
            if (_reviewUid is not int uid)
                return;
            var rules = _ruleRows.ToDictionary(kv => kv.Key, kv => kv.Value.Switch.IsOn);
            _ = ReviewCall("set_rules", new { uid, rules, lyrics = _lyrics.Value, show = _show });
        });
    }

    private void ApplyLegend(List<LegendItem> items)
    {
        Legend.Children.Clear();
        foreach (var item in items)
        {
            var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
            row.Children.Add(new Ellipse
            {
                Width = 8, Height = 8, VerticalAlignment = VerticalAlignment.Center,
                Fill = Theme.Brush(item.Kind switch
                {
                    "check" => "S4Btn",
                    "removed" => "S4Removed",
                    "trim" => "S4Trim",
                    _ => "S4MuteLine",
                }, Root),
            });
            row.Children.Add(new TextBlock
            {
                Text = item.Text, Style = Theme.Style(item.Kind == "check" ? "S4Medium" : "S4Quiet"), FontSize = 13,
                VerticalAlignment = VerticalAlignment.Center,
            });
            Legend.Children.Add(row);
        }
    }

    private void UpdateNav()
    {
        var (k, n) = Preview.NavState();
        NavCount.Text = k > 0 ? $"{k:N0} of {n:N0}" : $"{n:N0} changes";
        var row = Preview.Current;
        var ok = row != null && row.Status != "kept" && !Files.Busy;
        KeepButton.IsEnabled = ok;
        var label = ok && row!.Override == "orig" ? "Use Cleaned" : "Keep Original";
        KeepText.Text = label;
        ToolTipService.SetToolTip(KeepButton, (label == "Keep Original" ? "Put the selected line back the way it was"
                                                                       : "Use the cleaned text for the selected line again")
                                              + " (O, Ctrl+Z, or double-click a line)");
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(KeepButton, label);
    }

    /// <summary>A small window keeps the room for the list: the key hints go, and the line button shows its icon
    /// only when the toolbar would otherwise cut off the filter.</summary>
    private void FitReview()
    {
        KeyHints.Visibility = Root.ActualHeight >= 760 ? Visibility.Visible : Visibility.Collapsed;
        var compact = ReviewMain.ActualWidth > 0 && ReviewMain.ActualWidth < 860;
        KeepText.Visibility = compact ? Visibility.Collapsed : Visibility.Visible;
        KeepButton.MinWidth = compact ? 32 : 132;
        Legend.Visibility = ReviewMain.ActualWidth > 0 && ReviewMain.ActualWidth < 700 ? Visibility.Collapsed : Visibility.Visible;
        SideCard.MaxHeight = Math.Max(160, ReviewPage.ActualHeight - 26);
    }

    public async Task ToggleCurrentAsync()
    {
        if (Preview.Current is { } row && row.Status != "kept")
            await ToggleAsync(row.Cue);
    }

    /// <summary>Keep the original line or use the cleaned one again: only that row, its mark and the counts change.</summary>
    private async Task ToggleAsync(int cue)
    {
        if (Core == null || _reviewUid is not int uid || Files.Busy)
            return;
        try
        {
            var r = await Core.Call<ToggleReply>("toggle_line", new { uid, cue });
            if (Review != null)
                Review.Legend = r.Legend;
            ApplyLegend(r.Legend);
            ApplyHead(r.Head);
            ApplyRules(r.Rules, false);
            Preview.UpdateCue(r.Row);
            UpdateNav();
        }
        catch (CoreException e)
        {
            await TellAsync(new Message { Title = "Something went wrong", Text = e.Message });
        }
    }

    private void OnKeepClick(object sender, RoutedEventArgs e) => _ = ToggleCurrentAsync();

    private void OnPrevChange(object sender, RoutedEventArgs e) => Preview.Step(-1);

    private void OnNextChange(object sender, RoutedEventArgs e) => Preview.Step(1);

    private void OnPrevFile(object sender, RoutedEventArgs e) => StepFile(-1);

    private void OnNextFile(object sender, RoutedEventArgs e) => StepFile(1);

    private void StepFile(int d)
    {
        if (Files.Rows.Count == 0 || _reviewUid is not int uid)
            return;
        var k = Files.Rows.FindIndex(r => r.Uid == uid);
        var next = Files.Rows[((k < 0 ? 0 : k) + d + Files.Rows.Count) % Files.Rows.Count];
        _ = OpenReviewAsync(next.Uid);
    }

    private void OnPromoteRules(object sender, RoutedEventArgs e) => _ = ReviewCall("promote_rules", new { uid = _reviewUid, show = _show });

    private void OnResetRules(object sender, RoutedEventArgs e) => _ = ReviewCall("reset_rules", new { uid = _reviewUid, show = _show });

    private async void OnChangeTitle(object sender, RoutedEventArgs e)
    {
        if (_reviewUid is int uid && await ChooseTitleAsync(uid) != null)
            await RefreshReviewAsync(keep: true);
    }

    /// <summary>The title picker for one video. Returns what was picked ("keep" or a title), or null.</summary>
    public async Task<string?> ChooseTitleAsync(int uid)
    {
        if (Core == null)
            return null;
        NameChoices data;
        try
        {
            data = await Core.Call<NameChoices>("name_choices", new { uid });
        }
        catch (CoreException e)
        {
            await TellAsync(new Message { Title = "Couldn't look up titles", Text = e.Message });
            return null;
        }
        var dialog = new NameDialog(this, Core, uid, data);
        await ShowDialogAsync(dialog);
        if (dialog.Choice == null)
            return null;
        await Safe(() => Core.Do("name_pick", new { uid, pick = dialog.Choice }));
        return dialog.Choice;
    }

    // ================================================================ saving

    private void OnPrimaryClick(object sender, RoutedEventArgs e) => _ = StartSaveAsync(null);

    private void OnSaveOneClick(object sender, RoutedEventArgs e) => _ = StartSaveAsync(_reviewUid);

    private void OnStopClick(object sender, RoutedEventArgs e)
    {
        StatusText.Text = "Stopping…";
        _ = Safe(() => Core!.Do("cancel"));
    }

    private void OnOpenFolder(object sender, RoutedEventArgs e) => _ = Safe(() => Core!.Do("open_folder"));

    /// <summary>Save: the engine asks its questions one at a time (files in use, lines it isn't sure about, unsure
    /// titles, rewriting in place); each answer goes back with the next call until it starts.</summary>
    public async Task StartSaveAsync(int? only)
    {
        if (Core == null || Files.Busy || _dialogOpen)
            return;
        var answers = new Dictionary<string, object>();
        try
        {
            for (var round = 0; round < 20; round++)
            {
                var reply = await Core.Call<SaveReply>("save", new { only, answers });
                if (reply.Started != null)
                {
                    if (ReviewPage.Visibility == Visibility.Visible && Files.Count > 1)
                        ShowTab(review: false);
                    return;
                }
                if (reply.Stop)
                    return;
                if (reply.Tell != null)
                {
                    await TellAsync(reply.Tell);
                    return;
                }
                var q = reply.Ask!;
                var (choice, ticked) = await AskAsync(q);
                if (choice == null)
                    return;
                switch (q.Key)
                {
                    case "locks":
                        if (choice == "skip")
                            answers["locks"] = "skip";
                        break;                                      // "again": the engine checks again
                    case "lone":
                        answers["lone"] = "go";
                        break;
                    case "decision":
                        if (choice == "review")
                        {
                            if (q.ReviewUid is int target)
                                await OpenReviewAsync(target, "check");
                            return;
                        }
                        answers["decision"] = choice;
                        break;
                    case "names":
                        if (choice == "keep")
                            answers["names"] = "keep";
                        else
                        {
                            foreach (var uid in q.Uids)
                            {
                                if (await ChooseTitleAsync(uid) == null)
                                    return;
                            }
                        }
                        break;
                    case "rewrite":
                        answers["rewrite"] = "go";
                        if (ticked)
                            answers["dont_ask"] = true;
                        break;
                    case "overwrite":
                        answers["overwrite"] = choice;
                        break;
                    default:
                        return;
                }
            }
        }
        catch (CoreException e)
        {
            await TellAsync(new Message { Title = "Couldn't save", Text = e.Message });
        }
    }

    public event Action<SaveDone>? Saved;

    private async void OnSaveDone(SaveDone done)
    {
        _saveWait?.TrySetResult(done);
        Saved?.Invoke(done);
        _ = AddForwardedAfterSaveAsync();                // files handed over during the save
        if (ReviewPage.Visibility == Visibility.Visible)
            await RefreshReviewAsync(keep: true);
        if (done.Tell != null && !_closing)
        {
            var dialog = AskDialog.Tell(this, done.Tell, "log", "Show Log");
            await ShowDialogAsync(dialog);
            if (dialog.Choice == "log")
                await ShowLogAsync();
        }
    }

    // ================================================================ dialogs

    /// <summary>One dialog at a time (Windows allows only one ContentDialog open).</summary>
    public async Task ShowDialogAsync(GlassDialog dialog)
    {
        while (_dialogOpen)
            await Task.Delay(50);
        _dialogOpen = true;
        try
        {
            if (AutoAnswer is { } auto)
            {
                dialog.Opened += async (_, _) => await auto(dialog);
            }
            await dialog.ShowAsync();
        }
        finally
        {
            _dialogOpen = false;
        }
    }

    public async Task<(string? Choice, bool Ticked)> AskAsync(Question q)
    {
        var dialog = new AskDialog(this, q);
        await ShowDialogAsync(dialog);
        return (dialog.Choice, dialog.Checked);
    }

    public async Task TellAsync(Message m) => await ShowDialogAsync(AskDialog.Tell(this, m));

    private async void OnSettingsClick(object sender, RoutedEventArgs e) => await ShowSettingsAsync();

    public SettingsDialog Dialog => _settingsDialog ??= new SettingsDialog(this);

    public async Task ShowSettingsAsync(string section = "videos")
    {
        if (Files.Busy || _dialogOpen)
            return;
        _dialogOpen = true;
        try
        {
            var dialog = Dialog;
            dialog.Prepare(section);
            await dialog.ShowAsync();
        }
        finally
        {
            _dialogOpen = false;
        }
    }

    public async Task SaveSettingsAsync(SettingsView values)
    {
        if (Core == null)
            return;
        try
        {
            Settings = await Core.Call<SettingsView>("settings_set", new { values });
            ApplyTheme();
            if (Review != null && ReviewPage.Visibility == Visibility.Visible)
                await RefreshReviewAsync(keep: true);
        }
        catch (CoreException ex)
        {
            await TellAsync(new Message { Title = "Settings weren't saved", Text = ex.Message });
        }
    }

    private async void OnToolsClick(object sender, RoutedEventArgs e) => await ShowToolsAsync();

    public async Task ShowToolsAsync()
    {
        if (Core == null)
            return;
        while (true)
        {
            var t = Files.Tools.Ffmpeg.Length > 0 || Core.Ready == null ? Files.Tools : Core.Ready.Tools;
            Dictionary<string, string> versions = [];
            try
            {
                versions = await Core.Call<Dictionary<string, string>>("tools_versions") ?? [];
            }
            catch (CoreException)
            {
            }
            string Row(string path, string key, bool optional) =>
                path.Length > 0 ? $"✓  {(versions.GetValueOrDefault(key) is { Length: > 0 } v ? v : "Found")}   {path}"
                    : optional ? "Not Found (Optional)" : "Not Found";
            var buttons = new List<ButtonDef>();
            if (t.Ffmpeg.Length == 0 || t.Ffprobe.Length == 0)
                buttons.Add(new() { Key = "ffmpeg", Label = "Install FFmpeg" });
            if (t.Mkvmerge.Length == 0)
                buttons.Add(new() { Key = "mkvmerge", Label = "Install MKVToolNix" });
            buttons.Add(new() { Key = "again", Label = "Scan Again" });
            buttons.Add(new() { Key = "ok", Label = "Done", Kind = "primary" });
            var (choice, _) = await AskAsync(new Question
            {
                Key = "tools", Title = "Tools",
                Text = "FFmpeg is required. MKVToolNix is optional but recommended: it's the most faithful way to rewrite MKV "
                       + "files (it keeps every tag, chapter and attachment exactly as they were). Nothing is re-encoded.",
                Rows = [["FFmpeg", Row(t.Ffmpeg, "ffmpeg", false)], ["FFprobe", Row(t.Ffprobe, "ffprobe", false)],
                        ["MKVToolNix", Row(t.Mkvmerge, "mkvmerge", true)]],
                Detail = t.Ready && t.Mkvmerge.Length > 0 ? ""
                    : "The install buttons run Windows' package manager (winget) in its own window; SubScrub looks again when it finishes.",
                Buttons = buttons,
            });
            try
            {
                if (choice is "ffmpeg" or "mkvmerge")
                {
                    var id = choice == "ffmpeg" ? "Gyan.FFmpeg" : "MoritzBunkus.MKVToolNix";
                    StatusText.Text = $"Installing {(choice == "ffmpeg" ? "FFmpeg" : "MKVToolNix")} with winget…";
                    var r = await Core.CallRaw("winget_install", new { id }) as System.Text.Json.Nodes.JsonObject;
                    StatusText.Text = "";
                    if (r?["missing"] is not null)
                    {
                        await TellAsync(new Message
                        {
                            Title = "winget isn't available", Text = "Windows Package Manager (winget) isn't available on this PC.",
                            Detail = "Download FFmpeg from https://www.gyan.dev/ffmpeg/builds/ or MKVToolNix from "
                                     + "https://mkvtoolnix.download/, install it, then click Scan Again.",
                        });
                    }
                    var tools = await Core.Call<ToolsView>("tools_rescan");
                    Files.Tools = tools;
                    ApplyTools(tools);
                    continue;
                }
                if (choice != "again")
                    return;
                var found = await Core.Call<ToolsView>("tools_rescan");
                Files.Tools = found;
                ApplyTools(found);
            }
            catch (CoreException ex)
            {
                await TellAsync(new Message { Title = "Something went wrong", Text = ex.Message });
                return;
            }
        }
    }

    private async void OnLogClick(object sender, RoutedEventArgs e) => await ShowLogAsync();

    public async Task ShowLogAsync()
    {
        if (Core == null)
            return;
        var text = "";
        try
        {
            text = (await Core.Call<LogReply>("log")).Text;
        }
        catch (CoreException ex)
        {
            text = ex.Message;
        }
        lock (Log.Lines)
        {
            if (Log.Lines.Count > 0)
                text += "\n\nWindow:\n" + string.Join("\n", Log.Lines);
        }
        await ShowDialogAsync(new LogDialog(this, text));
    }

    private async void OnUndoRenamesClick(object sender, RoutedEventArgs e)
    {
        if (Core == null || Files.Busy)
            return;
        try
        {
            var q = await Core.Call<Question?>("undo_renames_question");
            if (q == null)
            {
                await TellAsync(new Message { Title = "Nothing to undo", Text = "There are no recent renames to undo." });
                return;
            }
            q.Key = "undo";
            var (choice, _) = await AskAsync(q);
            if (choice != "go")
                return;
            await TellAsync(await Core.Call<Message>("undo_renames"));
        }
        catch (CoreException ex)
        {
            await TellAsync(new Message { Title = "Couldn't undo the renames", Text = ex.Message });
        }
    }

    private async void OnAboutClick(object sender, RoutedEventArgs e)
    {
        var version = typeof(MainWindow).Assembly
            .GetCustomAttributes(typeof(System.Reflection.AssemblyInformationalVersionAttribute), false)
            .OfType<System.Reflection.AssemblyInformationalVersionAttribute>().FirstOrDefault()?.InformationalVersion ?? "";
        version = version.Split('+')[0];
        await TellAsync(new Message
        {
            Title = "About SubScrub",
            Text = $"SubScrub {version}, the native Windows app (a preview). It takes the sound descriptions, speaker names and music cues out of SDH subtitles and keeps the dialogue.",
            Detail = $"Engine {Core?.Ready?.Version ?? "not running"}. Inter (SIL Open Font License) and Solar icons (CC BY 4.0).",
        });
    }
}
