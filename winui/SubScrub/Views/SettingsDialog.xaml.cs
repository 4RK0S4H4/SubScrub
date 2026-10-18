using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using SubScrub.Controls;
using SubScrub.Models;

namespace SubScrub.Views;

/// <summary>Everything that's set once: how files are saved, names and tracks, the default edits, appearance.
/// The same sections, labels and hints as the Python app's Settings (subscrub/ui/dialogs.py). Stage 1: the values
/// live in memory only.</summary>
public sealed partial class SettingsDialog : ContentDialog
{
    private static readonly (string Key, string Label, string Icon)[] Sections =
    [
        ("videos", "Videos", "videocamera-record"),
        ("subs", "Subtitle Files", "subtitles"),
        ("names", "Names and Tracks", "tag-horizontal"),
        ("edits", "Default Edits", "magic-stick-3"),
        ("look", "Appearance and Safety", "pallete-2"),
    ];

    private static readonly (string Key, string Title, string Description)[] Rules =
    [
        ("brackets", "Sounds in [Brackets]", "[door slams], [Grace] Hi."),
        ("parentheses", "Sounds in (Parentheses)", "(sighs), (ON PHONE)"),
        ("speaker_labels", "Speaker Names", "JOHN: Get down!  →  Get down!"),
        ("music_cues", "Music Cues", "♪ tense music playing ♪, ♪♪"),
        ("dialogue_dashes", "Dialogue Dashes", "- Hi. / - Hey.  →  Hi. / Hey."),
        ("onscreen_text", "On-Screen Text Notes", "[sign reads: EXIT]  →  EXIT"),
        ("sound_words", "Sound Words", "whoosh, pew pew pew, ka-boom on their own line"),
        ("quote_italics", "Quoted Voices → Italics", "'Report to Oa.'  →  Report to Oa. (in italics)"),
        ("ocr_fixes", "OCR Repairs", "Fixes ♪ and “I” misread in disc-converted tracks"),
        ("mixed_case_labels", "Every “Name:” Label", "Also in files that don't use them throughout (risky)"),
    ];

    private readonly MainWindow _win;
    private readonly Dictionary<string, RadioButton> _navItems = [];
    private readonly Dictionary<string, FrameworkElement> _pages = [];
    private readonly Dictionary<string, OptionRow> _ruleRows = [];
    private string _themeOnOpen = "system";

    private readonly Segmented _videoMode = new([("replace", "Replace Original"), ("new", "New File"), ("sidecar", "Subtitle File")], wide: true);
    private readonly TextBlock _videoHint = Hint("");
    private readonly OptionRow _defaultRow = new("Make the Cleaned Track the Default", "Jellyfin and players pick it automatically.", true);
    private readonly OptionRow _keepRow = new("Keep the SDH Track Too", "Off: the SDH track is replaced by the clean one. Doesn't apply when the file has a regular English track.", false);
    private readonly TextBox _trackName = new() { PlaceholderText = "Automatic: the language, e.g. “English”" };
    private readonly OptionRow _recycleRow = new("One File per Video", "Subtitle files next to a video (Movie.eng.srt) are cleaned into the video and then moved to the Recycle Bin, along with other files in the same language.", true);
    private readonly Segmented _subMode = new([("embed", "Embed in Video"), ("replace", "Replace File"), ("new", "New File")], wide: true);
    private readonly OptionRow _renameRow = new("Rename Videos to Their TMDB Title", "“Batman V Super Dawn of Justice 2160 HDR.mkv” becomes “Batman v Superman - Dawn of Justice.mkv”; episodes become “Show S1 E1.mkv”. Files stay in their folder. Only when replacing originals.", true);
    private readonly OptionRow _yearRow = new("Add the Year to Movie Names", "“Dune (2021).mkv” instead of “Dune.mkv”.", false);
    private readonly PasswordBox _tmdbKey = new() { PlaceholderText = "Paste your API key or read access token" };
    private readonly OptionRow _langsRow = new("Remove Subtitles in Other Languages", "Keeps English, forced tracks (any language) and tracks with no language set.", true);
    private readonly Segmented _lyrics = new([("italic", "Italic, No ♪"), ("keep", "Keep ♪"), ("remove", "Remove")]);
    private readonly Segmented _theme = new([("system", "Automatic"), ("light", "Light"), ("dark", "Dark")]);
    private readonly OptionRow _confirmRow = new("Ask Before Rewriting Files in Place", "A last check before originals are replaced.", true);
    private readonly OptionRow _updateRow = new("Check for Updates Automatically", "Looks once a day when SubScrub starts. Nothing is downloaded until you click Update.", true);

    public SettingsDialog(MainWindow win)
    {
        InitializeComponent();
        _win = win;
        _trackName.Style = Theme.Style("S4TextBox");
        _tmdbKey.Style = Theme.Style("S4PasswordBox");
        AutomationProperties.SetName(_trackName, "Track Name");
        AutomationProperties.SetName(_tmdbKey, "TMDB API Key");

        foreach (var (key, label, icon) in Sections)
        {
            var content = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12 };
            content.Children.Add(new SolarIcon { Glyph = icon, Duo = true, Size = 20, VerticalAlignment = VerticalAlignment.Center });
            content.Children.Add(new TextBlock { Text = label, VerticalAlignment = VerticalAlignment.Center });
            var item = new RadioButton { Content = content, GroupName = "SettingsNav", Style = Theme.Style("S4NavItem") };
            AutomationProperties.SetName(item, label);
            item.Checked += (_, _) => ShowSection(key);
            _navItems[key] = item;
            Nav.Children.Add(item);
        }

        _pages["videos"] = Page("Videos",
            Field("When Saving a Video", _videoMode), _videoHint, _defaultRow, _keepRow,
            Field("Track Name", _trackName));
        _pages["subs"] = Page("Subtitle Files",
            _recycleRow,
            Field("Subtitle Files on Their Own", _subMode, "Subtitle files with no matching video, or when you add just the .srt."));
        _pages["names"] = Page("Names and Tracks",
            _renameRow, _yearRow,
            Field("TMDB API Key", _tmdbKey, "Free: sign in at themoviedb.org, then Settings → API → Create. Titles are looked up there; nothing else is sent."),
            _langsRow);
        var edits = new List<UIElement>
        {
            Hint("What gets cleaned out of every file. You can still change these for one file in Review."),
        };
        foreach (var (key, title, description) in Rules)
        {
            var row = new OptionRow(title, description, true);
            _ruleRows[key] = row;
            edits.Add(row);
        }
        edits.Add(LabelledRow("Song Lyrics", _lyrics));
        _pages["edits"] = Page("Default Edits", [.. edits]);
        var tools = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, Margin = new Thickness(0, 10, 0, 0) };
        tools.Children.Add(new Button { Content = "Tools and Paths…", Style = Theme.Style("S4Button") });
        tools.Children.Add(new Button { Content = "Activity Log", Style = Theme.Style("S4Button") });
        _pages["look"] = Page("Appearance and Safety",
            LabelledRow("Theme", _theme), Hint("Automatic follows Windows' light or dark setting, live."),
            _confirmRow, _updateRow, tools);
        foreach (var page in _pages.Values)
        {
            page.Visibility = Visibility.Collapsed;
            PageHost.Children.Add(page);
        }

        _videoMode.Changed += _ => UpdateHints();
        _keepRow.Toggled += _ => UpdateHints();
        _defaultRow.Toggled += _ => UpdateHints();
        _renameRow.Toggled += _ => UpdateHints();
        _theme.Changed += mode =>
        {
            _win.Settings.Theme = mode;                // live, like the Python app; Cancel puts it back
            _win.ApplyTheme();
        };
    }

    // ================================================================ opening and closing

    /// <summary>Called before every ShowAsync: fit the window, load the current values, pick the section.</summary>
    public void Prepare(string section)
    {
        XamlRoot = _win.Content.XamlRoot;
        RequestedTheme = ((FrameworkElement)_win.Content).RequestedTheme;
        var height = XamlRoot?.Size.Height ?? 800;
        var width = XamlRoot?.Size.Width ?? 1200;
        DialogFrame.Height = Math.Min(640, Math.Max(420, height - 48));
        DialogFrame.Width = Math.Min(900, Math.Max(600, width - 48));
        Load(_win.Settings);
        _themeOnOpen = _win.Settings.Theme;
        ShowSection(section);
    }

    public void ShowSection(string key)
    {
        if (!_pages.ContainsKey(key))
            key = "videos";
        _navItems[key].IsChecked = true;
        foreach (var (k, page) in _pages)
            page.Visibility = k == key ? Visibility.Visible : Visibility.Collapsed;
    }

    private void OnSave(object sender, RoutedEventArgs e)
    {
        Store(_win.Settings);
        Hide();
    }

    private void OnCancel(object sender, RoutedEventArgs e)
    {
        if (_win.Settings.Theme != _themeOnOpen)
        {
            _win.Settings.Theme = _themeOnOpen;
            _win.ApplyTheme();
        }
        Hide();
    }

    private void OnRestore(object sender, RoutedEventArgs e)
    {
        var defaults = new AppSettings { Theme = _theme.Value, TmdbKey = _tmdbKey.Password, TrackName = "" };
        Load(defaults);
    }

    // ================================================================ values

    private void Load(AppSettings s)
    {
        _videoMode.Value = s.VideoMode;
        _defaultRow.IsOn = s.MakeDefault;
        _keepRow.IsOn = s.KeepSdh;
        _trackName.Text = s.TrackName;
        _recycleRow.IsOn = s.OneFilePerVideo;
        _subMode.Value = s.SubMode;
        _renameRow.IsOn = s.Rename;
        _yearRow.IsOn = s.NameYear;
        _tmdbKey.Password = s.TmdbKey;
        _langsRow.IsOn = s.DropLanguages;
        foreach (var (key, row) in _ruleRows)
            row.IsOn = s.Rules.TryGetValue(key, out var on) && on;
        _lyrics.Value = s.Lyrics;
        _theme.Value = s.Theme;
        _confirmRow.IsOn = s.ConfirmReplace;
        _updateRow.IsOn = s.UpdateCheck;
        UpdateHints();
    }

    private void Store(AppSettings s)
    {
        s.VideoMode = _videoMode.Value;
        s.MakeDefault = _defaultRow.IsOn;
        s.KeepSdh = _keepRow.IsOn;
        s.TrackName = _trackName.Text;
        s.OneFilePerVideo = _recycleRow.IsOn;
        s.SubMode = _subMode.Value;
        s.Rename = _renameRow.IsOn;
        s.NameYear = _yearRow.IsOn;
        s.TmdbKey = _tmdbKey.Password;
        s.DropLanguages = _langsRow.IsOn;
        foreach (var (key, row) in _ruleRows)
            s.Rules[key] = row.IsOn;
        s.Lyrics = _lyrics.Value;
        s.Theme = _theme.Value;
        s.ConfirmReplace = _confirmRow.IsOn;
        s.UpdateCheck = _updateRow.IsOn;
    }

    /// <summary>The same wording as the Python app's Settings hints.</summary>
    private void UpdateHints()
    {
        var mode = _videoMode.Value;
        bool keep = _keepRow.IsOn, makeDefault = _defaultRow.IsOn;
        _videoHint.Text = mode switch
        {
            "replace" => "Rewrites the video with the cleaned track" + (makeDefault ? " as the default" : "")
                         + (keep ? ", keeping the SDH track as a backup"
                                 : " in place of the SDH track (a regular English track is cleaned instead when there is one, and the SDH track stays)")
                         + ". The new file is checked before the old one goes to the Recycle Bin.",
            "new" => "Saves “Movie.dialogue.mkv” next to the original, which isn't touched.",
            _ => "Saves “Movie.en.srt” next to the video. Nothing is remuxed; players load it automatically.",
        };
        _keepRow.IsEnabled = mode != "sidecar";
        _defaultRow.IsEnabled = mode != "sidecar";
        _renameRow.IsEnabled = mode == "replace";
        _yearRow.IsEnabled = mode == "replace" && _renameRow.IsOn;
        _tmdbKey.IsEnabled = _renameRow.IsOn;
        _langsRow.IsEnabled = mode != "sidecar";
    }

    // ================================================================ layout helpers

    private static StackPanel Page(string title, params UIElement[] children)
    {
        var page = new StackPanel { Spacing = 6 };
        page.Children.Add(new TextBlock { Text = title, Style = Theme.Style("S4CardTitle"), Margin = new Thickness(0, 0, 0, 6) });
        foreach (var child in children)
            page.Children.Add(child);
        return page;
    }

    private static StackPanel Field(string label, FrameworkElement control, string hint = "")
    {
        var field = new StackPanel { Spacing = 8, Margin = new Thickness(0, 6, 0, 4) };
        field.Children.Add(new TextBlock { Text = label, Style = Theme.Style("S4FieldLabel") });
        field.Children.Add(control);
        if (hint.Length > 0)
            field.Children.Add(Hint(hint));
        return field;
    }

    private static Grid LabelledRow(string label, FrameworkElement control)
    {
        var row = new Grid { Margin = new Thickness(0, 8, 0, 2) };
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        row.Children.Add(new TextBlock { Text = label, Style = Theme.Style("S4Medium"), VerticalAlignment = VerticalAlignment.Center });
        Grid.SetColumn(control, 1);
        row.Children.Add(control);
        return row;
    }

    private static TextBlock Hint(string text) => new() { Text = text, Style = Theme.Style("S4Hint") };
}
