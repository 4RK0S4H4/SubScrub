using System.Collections.ObjectModel;
using Microsoft.UI.Input;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Markup;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;
using SubScrub.Models;

namespace SubScrub.Controls;

/// <summary>An always-dark strip that shows a subtitle line the way it will look on the TV: white caption text, the
/// timecode in one corner and what happened to the line in the other.</summary>
public sealed partial class CaptionScreen : UserControl
{
    private readonly Border _frame = new() { CornerRadius = new CornerRadius(10), BorderThickness = new Thickness(1) };
    private readonly TextBlock _left = Corner(HorizontalAlignment.Left);
    private readonly TextBlock _right = Corner(HorizontalAlignment.Right);
    private readonly TextBlock _caption = new()
    {
        FontFamily = Theme.Font("S4FontBold"), FontSize = 21, TextAlignment = TextAlignment.Center,
        HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Bottom,
        TextWrapping = TextWrapping.Wrap, MaxLines = 3, TextTrimming = TextTrimming.CharacterEllipsis,
        Margin = new Thickness(24, 0, 24, 18), Foreground = new SolidColorBrush(Microsoft.UI.Colors.White),
    };
    private readonly TextBlock _shadow = new()
    {
        FontFamily = Theme.Font("S4FontBold"), FontSize = 21, TextAlignment = TextAlignment.Center,
        HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Bottom,
        TextWrapping = TextWrapping.Wrap, MaxLines = 3, TextTrimming = TextTrimming.CharacterEllipsis,
        Margin = new Thickness(25, 0, 23, 16), Foreground = new SolidColorBrush(Windows.UI.Color.FromArgb(200, 0, 0, 0)),
    };
    private readonly TextBlock _placeholder = new()
    {
        FontFamily = Theme.Font("S4FontMedium"), FontSize = 13, HorizontalAlignment = HorizontalAlignment.Center,
        VerticalAlignment = VerticalAlignment.Center, Foreground = new SolidColorBrush(Windows.UI.Color.FromArgb(110, 255, 255, 255)),
    };
    private bool _alert;
    private DispatcherQueueTimerWrapper? _demo;

    public CaptionScreen()
    {
        var grid = new Grid();
        grid.Children.Add(_left);
        grid.Children.Add(_right);
        grid.Children.Add(_shadow);
        grid.Children.Add(_caption);
        grid.Children.Add(_placeholder);
        _frame.Child = grid;
        Content = _frame;
        IsTabStop = false;
        Height = 150;
        ActualThemeChanged += (_, _) => Paint();
        Loaded += (_, _) => Paint();
    }

    private static TextBlock Corner(HorizontalAlignment side) => new()
    {
        FontFamily = Theme.Font("S4FontSemiBold"), FontSize = 12, HorizontalAlignment = side,
        VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(16, 10, 16, 0),
        Foreground = new SolidColorBrush(Windows.UI.Color.FromArgb(128, 255, 255, 255)),
        TextTrimming = TextTrimming.CharacterEllipsis, MaxWidth = 520,
    };

    private void Paint()
    {
        _frame.Background = Theme.Brush("S4Screen", this);
        _frame.BorderBrush = Theme.Brush("S4ScreenLine", this);
        _right.Foreground = _alert ? Theme.Brush("S4AccentLight", this)
                                   : new SolidColorBrush(Windows.UI.Color.FromArgb(128, 255, 255, 255));
    }

    public void Show(string caption, string left = "", string right = "", bool alert = false, bool italic = false,
                     string placeholder = "")
    {
        _demo?.Stop();
        _left.Text = left;
        _right.Text = right;
        _alert = alert;
        var font = Theme.Font(italic ? "S4FontItalic" : "S4FontBold");
        _caption.FontFamily = font;
        _shadow.FontFamily = font;
        _caption.Inlines.Clear();
        _caption.Text = caption;
        _shadow.Text = caption;
        _placeholder.Text = caption.Length == 0 ? placeholder : "";
        Paint();
    }

    /// <summary>The welcome screen's moment: a caption with its sound tag struck out, then scrubbed away.</summary>
    public void PlayDemo()
    {
        Show("");
        _caption.Inlines.Clear();
        var tag = new Microsoft.UI.Xaml.Documents.Run
        {
            Text = "[door creaks] ",
            TextDecorations = Windows.UI.Text.TextDecorations.Strikethrough,
            Foreground = new SolidColorBrush(Windows.UI.Color.FromArgb(150, 0xFD, 0xA4, 0xAF)),
        };
        _caption.Inlines.Add(tag);
        _caption.Inlines.Add(new Microsoft.UI.Xaml.Documents.Run { Text = "Hello? Is anyone there?" });
        _shadow.Text = "[door creaks] Hello? Is anyone there?";
        _right.Text = "Before";
        _demo = new DispatcherQueueTimerWrapper(DispatcherQueue, TimeSpan.FromMilliseconds(1600), () =>
        {
            _caption.Inlines.Clear();
            _caption.Text = "Hello? Is anyone there?";
            _shadow.Text = _caption.Text;
            _right.Text = "After";
        });
    }
}

/// <summary>A one-shot timer on the UI thread.</summary>
internal sealed class DispatcherQueueTimerWrapper
{
    private readonly Microsoft.UI.Dispatching.DispatcherQueueTimer _timer;

    public DispatcherQueueTimerWrapper(Microsoft.UI.Dispatching.DispatcherQueue queue, TimeSpan after, Action action)
    {
        _timer = queue.CreateTimer();
        _timer.Interval = after;
        _timer.IsRepeating = false;
        _timer.Tick += (_, _) => action();
        _timer.Start();
    }

    public void Stop() => _timer.Stop();
}

/// <summary>The whole track as a strip: a mark for every line that changes (crimson for the ones to check), and the
/// playhead at the selected line. Click or drag to jump.</summary>
public sealed partial class Timeline : UserControl
{
    private readonly Canvas _canvas = new();
    private readonly Rectangle _track = new() { RadiusX = 4, RadiusY = 4, Height = 10 };
    private readonly Rectangle _head = new() { Width = 2, Height = 22, RadiusX = 1, RadiusY = 1 };
    private List<MarkView> _marks = [];
    private double _duration = 1;
    private double _position;
    private bool _dragging;

    public event Action<double>? Scrubbed;

    public Timeline()
    {
        Height = 26;
        _canvas.Children.Add(_track);
        _canvas.Children.Add(_head);
        Content = _canvas;
        Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent);
        IsTabStop = false;
        AutomationProperties.SetName(this, "Timeline");
        SizeChanged += (_, _) => Draw();
        ActualThemeChanged += (_, _) => Draw();
        PointerPressed += (_, e) =>
        {
            _dragging = true;
            CapturePointer(e.Pointer);
            Emit(e);
        };
        PointerMoved += (_, e) =>
        {
            if (_dragging)
                Emit(e);
        };
        PointerReleased += (_, e) =>
        {
            _dragging = false;
            ReleasePointerCapture(e.Pointer);
        };
        ProtectedCursor = InputSystemCursor.Create(InputSystemCursorShape.Hand);
    }

    public bool Dragging => _dragging;

    private void Emit(PointerRoutedEventArgs e)
    {
        var x = e.GetCurrentPoint(this).Position.X;
        var w = Math.Max(1, ActualWidth - 8);
        Scrubbed?.Invoke(Math.Clamp((x - 4) / w, 0, 1) * _duration);
    }

    public void SetData(double duration, List<MarkView> marks)
    {
        _duration = Math.Max(1, duration);
        _marks = marks;
        Draw();
    }

    /// <summary>One line changed its state (kept or cleaned again): redraw its mark only when needed.</summary>
    public void UpdateMark(double start, string kind)
    {
        var m = _marks.FirstOrDefault(x => Math.Abs(x.T - start) < 0.01);
        if (m == null || m.K == kind)
            return;
        m.K = kind;
        Draw();
    }

    public void SetPosition(double sec)
    {
        _position = sec;
        PlaceHead();
    }

    private double X(double sec) => 4 + Math.Clamp(sec / _duration, 0, 1) * Math.Max(1, ActualWidth - 8);

    private void PlaceHead()
    {
        Canvas.SetLeft(_head, X(_position) - 1);
        Canvas.SetTop(_head, 2);
    }

    private void Draw()
    {
        var w = ActualWidth;
        if (w <= 0)
            return;
        // one rectangle per pixel column, in the most important state that falls in it
        var rank = new Dictionary<string, int> { ["kept"] = 1, ["edited"] = 2, ["removed"] = 3, ["flagged"] = 4 };
        var cols = new Dictionary<int, string>();
        foreach (var m in _marks)
        {
            if (!rank.ContainsKey(m.K))
                continue;
            var x = (int)Math.Round(X(m.T));
            if (!cols.TryGetValue(x, out var cur) || rank[m.K] > rank[cur])
                cols[x] = m.K;
        }
        while (_canvas.Children.Count > 2)
            _canvas.Children.RemoveAt(2);
        _track.Width = w;
        _track.Fill = Theme.Brush("S4SegTrack", this);
        Canvas.SetTop(_track, 8);
        foreach (var (x, kind) in cols)
        {
            var mark = new Rectangle
            {
                Width = 2, Height = kind == "flagged" ? 14 : 10, RadiusX = 1, RadiusY = 1,
                Fill = Theme.Brush(kind switch
                {
                    "flagged" => "S4Btn",
                    "removed" => "S4Removed",
                    "edited" => "S4Trim",
                    _ => "S4MuteLine",
                }, this),
                IsHitTestVisible = false,
            };
            Canvas.SetLeft(mark, x - 1);
            Canvas.SetTop(mark, kind == "flagged" ? 6 : 8);
            _canvas.Children.Insert(1, mark);
        }
        _head.Fill = Theme.Brush("S4Text", this);
        PlaceHead();
    }
}

/// <summary>One line of the before/after list: the time, the original with removed parts struck out, what is saved,
/// and for judgement calls the reason and a Keep Original / Use Cleaned button.</summary>
public sealed partial class CueRowView : UserControl
{
    private readonly PreviewPanel _owner;
    private readonly Grid _grid = new() { Padding = new Thickness(0, 10, 12, 10), ColumnSpacing = 18 };
    private readonly Rectangle _edge = new() { Width = 3, RadiusX = 1.5, RadiusY = 1.5, Margin = new Thickness(8, 0, 0, 0), HorizontalAlignment = HorizontalAlignment.Left };
    private readonly TextBlock _time = new() { Style = Theme.Style("S4Count"), FontSize = 12.5, FontFamily = Theme.Font("S4FontRegular") };
    private readonly TextBlock _before = new() { Style = Theme.Style("S4Text"), TextWrapping = TextWrapping.Wrap, TextTrimming = TextTrimming.None };
    private readonly TextBlock _after = new() { Style = Theme.Style("S4Text"), TextWrapping = TextWrapping.Wrap, TextTrimming = TextTrimming.None };
    private readonly Grid _foot = new() { ColumnSpacing = 12, Margin = new Thickness(0, 8, 0, 0) };
    private readonly TextBlock _why = new() { Style = Theme.Style("S4Quiet"), FontSize = 12.5, VerticalAlignment = VerticalAlignment.Center };
    private readonly Button _toggle = new() { Style = Theme.Style("S4SmallButton"), Height = 28, MinWidth = 116 };
    private readonly Rectangle _line = new() { Height = 1, VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(18, 0, 16, 0) };
    public CueView? Cue { get; private set; }

    public CueRowView(PreviewPanel owner)
    {
        _owner = owner;
        _grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(58) });
        _grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        _grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        _grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        _grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        _time.Margin = new Thickness(18, 1, 0, 0);
        _grid.Children.Add(_time);
        Grid.SetColumn(_before, 1);
        _grid.Children.Add(_before);
        Grid.SetColumn(_after, 2);
        _grid.Children.Add(_after);
        _foot.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        _foot.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        _foot.Children.Add(_why);
        Grid.SetColumn(_toggle, 1);
        _foot.Children.Add(_toggle);
        Grid.SetRow(_foot, 1);
        Grid.SetColumn(_foot, 1);
        Grid.SetColumnSpan(_foot, 2);
        _grid.Children.Add(_foot);
        _toggle.Click += (_, _) =>
        {
            if (Cue != null)
                _owner.RequestToggle(Cue.Cue);
        };
        var root = new Grid();
        root.Children.Add(_line);
        root.Children.Add(_edge);
        root.Children.Add(_grid);
        Content = root;
        IsTabStop = false;
        DoubleTapped += (_, e) =>
        {
            if (Cue != null && Cue.Status != "kept")
            {
                _owner.RequestToggle(Cue.Cue);
                e.Handled = true;
            }
        };
        ActualThemeChanged += (_, _) => Paint();
    }

    public void Bind(CueView cue, bool first)
    {
        Cue = cue;
        _line.Visibility = first ? Visibility.Collapsed : Visibility.Visible;
        Paint();
    }

    private void Paint()
    {
        var c = Cue;
        if (c == null)
            return;
        var kept = c.Status == "kept";
        var undone = c.Override == "orig";
        _time.Text = c.Time;
        _time.Foreground = Theme.Brush("S4Text3", this);
        _line.Fill = Theme.Brush("S4Border", this);
        RichText.Fill(_before, c.Before, Theme.Brush("S4Removed", this), Theme.Brush("S4RemovedBg", this));
        _before.Foreground = Theme.Brush(kept ? "S4Text3" : "S4Text2", this);
        if (c.After == null)
        {
            _after.Text = "Removed";
            _after.FontFamily = Theme.Font("S4FontItalic");
            _after.Foreground = Theme.Brush("S4Text3", this);
        }
        else
        {
            _after.Text = c.After;
            _after.FontFamily = Theme.Font(c.Italic ? "S4FontItalic" : "S4FontRegular");
            _after.Foreground = Theme.Brush(kept ? "S4Text2" : "S4Text", this);
        }
        string? edge = undone ? "S4MuteLine" : c.Flagged ? "S4Btn" : c.Status switch
        {
            "removed" => "S4Removed",
            "edited" => "S4Trim",
            _ => null,
        };
        _edge.Visibility = edge == null ? Visibility.Collapsed : Visibility.Visible;
        if (edge != null)
            _edge.Fill = Theme.Brush(edge, this);
        var foot = !kept && (c.Flagged || undone);
        _foot.Visibility = foot ? Visibility.Visible : Visibility.Collapsed;
        if (foot)
        {
            _why.Inlines.Clear();
            if (undone)
            {
                _why.Text = "You kept the original line.";
                _why.Foreground = Theme.Brush("S4Text2", this);
            }
            else
            {
                _why.Inlines.Add(new Microsoft.UI.Xaml.Documents.Run { Text = "Check: ", FontFamily = Theme.Font("S4FontSemiBold"), Foreground = Theme.Brush("S4Text", this) });
                _why.Inlines.Add(new Microsoft.UI.Xaml.Documents.Run { Text = c.Why.Length > 0 ? c.Why : "judgement call", Foreground = Theme.Brush("S4Text2", this) });
            }
            _toggle.Content = undone ? "Use Cleaned" : "Keep Original";
            AutomationProperties.SetName(_toggle, (undone ? "Use Cleaned: " : "Keep Original: ") + c.BeforeText);
        }
    }
}

/// <summary>Review's preview: the caption screen, the timeline, the column header and the before/after list. The list
/// is virtualised (ListView), and when one line changes only its row is replaced.</summary>
public sealed partial class PreviewPanel : UserControl
{
    public CaptionScreen Screen { get; } = new() { Height = 150 };
    public Timeline Timeline { get; } = new() { Margin = new Thickness(2, 8, 2, 0) };
    public ListView Lines { get; } = new()
    {
        SelectionMode = ListViewSelectionMode.Single,
        IsItemClickEnabled = false,
        Padding = new Thickness(0, 0, 0, 8),
    };
    private readonly ObservableCollection<CueView> _rows = [];
    private readonly TextBlock _beforeHead = Head("Before");
    private readonly TextBlock _afterHead = Head("After, as Saved");
    private bool _previewOnly;
    private bool _syncing;

    public event Action? PositionChanged;
    public event Action<int>? ToggleRequested;

    public PreviewPanel()
    {
        var grid = new Grid();
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        grid.Children.Add(Screen);
        Grid.SetRow(Timeline, 1);
        grid.Children.Add(Timeline);
        var head = new Grid { ColumnSpacing = 18, Padding = new Thickness(0, 10, 12, 6) };
        head.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(58) });
        head.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        head.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        Grid.SetColumn(_beforeHead, 1);
        head.Children.Add(_beforeHead);
        Grid.SetColumn(_afterHead, 2);
        head.Children.Add(_afterHead);
        Grid.SetRow(head, 2);
        grid.Children.Add(head);
        Lines.ItemsSource = _rows;
        Lines.ItemContainerStyle = Theme.Style("S4CueItem");
        Lines.ItemTemplate = (DataTemplate)XamlReader.Load(
            "<DataTemplate xmlns=\"http://schemas.microsoft.com/winfx/2006/xaml/presentation\"><Border /></DataTemplate>");
        Lines.ContainerContentChanging += OnContainerContentChanging;
        Lines.SelectionChanged += (_, _) =>
        {
            if (Lines.SelectedItem is CueView c && !_syncing && !Timeline.Dragging)
                Timeline.SetPosition(c.Start);
            UpdateScreen();
            PositionChanged?.Invoke();
        };
        AutomationProperties.SetName(Lines, "Lines");
        Grid.SetRow(Lines, 3);
        grid.Children.Add(Lines);
        Content = grid;
        Timeline.Scrubbed += OnScrub;
        ActualThemeChanged += (_, _) =>
        {
            _beforeHead.Foreground = Theme.Brush("S4Text3", this);
            _afterHead.Foreground = Theme.Brush("S4Text3", this);
        };
        Loaded += (_, _) =>
        {
            _beforeHead.Foreground = Theme.Brush("S4Text3", this);
            _afterHead.Foreground = Theme.Brush("S4Text3", this);
        };
    }

    private static TextBlock Head(string text) => new()
    {
        Text = text, FontFamily = Theme.Font("S4FontSemiBold"), FontSize = 11.5, CharacterSpacing = 40,
    };

    private void OnContainerContentChanging(ListViewBase sender, ContainerContentChangingEventArgs args)
    {
        if (args.ItemContainer.ContentTemplateRoot is not Border box || args.Item is not CueView cue)
            return;
        if (box.Child is not CueRowView row)
        {
            row = new CueRowView(this);
            box.Child = row;
        }
        row.Bind(cue, args.ItemIndex == 0);
        args.Handled = true;
    }

    internal void RequestToggle(int cue) => ToggleRequested?.Invoke(cue);

    public IReadOnlyList<CueView> Rows => _rows;

    public void SetPreviewOnly(bool on)
    {
        _previewOnly = on;
        _afterHead.Text = on ? "After, if Cleaned" : "After, as Saved";
    }

    /// <summary>Show a new list. keepPosition: stay at the same moment (a filter or an edit changed).</summary>
    public void SetData(ReviewView view, bool keepPosition, int? selectCue = null)
    {
        double? at = keepPosition && Lines.SelectedItem is CueView cur ? cur.Start : null;
        _syncing = true;
        _rows.Clear();
        foreach (var r in view.Rows)
            _rows.Add(r);
        Timeline.SetData(view.Duration, view.Marks);
        _syncing = false;
        if (_rows.Count == 0)
        {
            Timeline.SetPosition(0);
            UpdateScreen();
            return;
        }
        var i = selectCue is int cue ? IndexOfCue(cue) : at is double t ? RowAt(t) : 0;
        Select(Math.Max(0, i), top: at == null && selectCue == null);
    }

    /// <summary>One line was kept or cleaned again: replace just its row and its timeline mark.</summary>
    public void UpdateCue(CueView cue)
    {
        Timeline.UpdateMark(cue.Start, cue.Mark);
        var k = IndexOfCue(cue.Cue);
        if (k < 0)
            return;
        var selected = Lines.SelectedIndex == k;
        _syncing = true;
        _rows[k] = cue;
        if (selected)
            Lines.SelectedIndex = k;
        _syncing = false;
        UpdateScreen();
        PositionChanged?.Invoke();
    }

    public CueView? Current => Lines.SelectedItem as CueView;

    public int IndexOfCue(int cue)
    {
        for (var i = 0; i < _rows.Count; i++)
        {
            if (_rows[i].Cue == cue)
                return i;
        }
        return -1;
    }

    private int RowAt(double sec)
    {
        var best = 0;
        for (var i = 0; i < _rows.Count; i++)
        {
            if (_rows[i].Start <= sec)
                best = i;
            else
                break;
        }
        return best;
    }

    public void Select(int i, bool top = false, bool center = false)
    {
        if (i < 0 || i >= _rows.Count)
            return;
        _syncing = true;
        Lines.SelectedIndex = i;
        _syncing = false;
        Timeline.SetPosition(_rows[i].Start);
        Lines.ScrollIntoView(_rows[i], top ? ScrollIntoViewAlignment.Leading : ScrollIntoViewAlignment.Default);
        UpdateScreen();
        PositionChanged?.Invoke();
    }

    private void OnScrub(double sec)
    {
        var i = RowAt(sec);
        if (_rows.Count == 0)
            return;
        Timeline.SetPosition(sec);
        _syncing = true;
        Lines.SelectedIndex = i;
        _syncing = false;
        Lines.ScrollIntoView(_rows[i], ScrollIntoViewAlignment.Default);
        UpdateScreen();
        PositionChanged?.Invoke();
    }

    /// <summary>The line above or below (J / K).</summary>
    public void Move(int delta)
    {
        if (_rows.Count == 0)
            return;
        var cur = Lines.SelectedIndex;
        var next = cur < 0 ? 0 : Math.Clamp(cur + delta, 0, _rows.Count - 1);
        if (next != cur)
            Select(next);
    }

    /// <summary>The next or previous change (F3 / Shift+F3), wrapping around.</summary>
    public void Step(int direction)
    {
        var changed = Enumerable.Range(0, _rows.Count).Where(i => _rows[i].Status != "kept").ToList();
        if (changed.Count == 0)
            return;
        var cur = Lines.SelectedIndex;
        var next = direction > 0 ? changed.FirstOrDefault(i => i > cur, changed[0])
                                 : changed.LastOrDefault(i => i < cur, changed[^1]);
        Select(next, center: true);
    }

    /// <summary>(1-based position among changes, number of changes)</summary>
    public (int K, int N) NavState()
    {
        var changed = Enumerable.Range(0, _rows.Count).Where(i => _rows[i].Status != "kept").ToList();
        var cur = Lines.SelectedIndex;
        var k = changed.IndexOf(cur);
        return (k >= 0 ? k + 1 : 0, changed.Count);
    }

    private void UpdateScreen()
    {
        var row = Current;
        if (row == null)
        {
            Screen.Show("", placeholder: "Pick a line below to see it as it will appear");
            return;
        }
        string right;
        var alert = false;
        if (row.Override == "orig")
            right = "You kept the original";
        else if (row.Flagged)
        {
            right = "Check: " + (row.Why.Length > 0 ? row.Why : "judgement call");
            alert = true;
        }
        else
            right = _previewOnly
                ? row.Status switch { "removed" => "Would Be Removed", "edited" => "Would Change", _ => "Unchanged" }
                : row.Status switch { "removed" => "Removed", "edited" => "Cleaned", _ => "Unchanged" };
        if (row.After == null)
            Screen.Show("", row.Time, right, alert, placeholder: "Nothing on screen: this whole line is removed");
        else
            Screen.Show(row.After, row.Time, right, alert, row.Italic);
    }
}
