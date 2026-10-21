namespace SubScrub.Models;

// What the engine (subscrub/core.py) sends back, in its own words: the window only draws these. Names map from the
// engine's snake_case JSON (Core.Json).

public sealed class Hello
{
    public string Version { get; set; } = "";
    public int Protocol { get; set; }
    public ToolsView Tools { get; set; } = new();
    public SettingsView Settings { get; set; } = new();
}

public sealed class ToolsView
{
    public string Ffmpeg { get; set; } = "";
    public string Ffprobe { get; set; } = "";
    public string Mkvmerge { get; set; } = "";
    public string Mkvextract { get; set; } = "";
    public string Mkvpropedit { get; set; } = "";
    public bool Ready { get; set; }
}

/// <summary>Every setting, with the Qt app's defaults and keys (kept in the same place, so both apps share them).</summary>
public sealed class SettingsView
{
    public string VideoMode { get; set; } = "replace";      // replace | new | sidecar
    public string SubMode { get; set; } = "embed";          // embed | replace | new
    public bool KeepSdh { get; set; }
    public bool MakeDefault { get; set; } = true;
    public bool RecycleSub { get; set; } = true;
    public bool ConfirmReplace { get; set; } = true;
    public string TrackName { get; set; } = "";
    public bool RenameOn { get; set; } = true;
    public bool NameYear { get; set; }
    public bool DropLangs { get; set; } = true;
    public string TmdbKey { get; set; } = "";
    public bool UpdateCheck { get; set; } = true;
    public string Theme { get; set; } = "system";           // system | light | dark
    public Dictionary<string, bool> Rules { get; set; } = DefaultRules();
    public string Lyrics { get; set; } = "italic";          // italic | keep | remove

    public static Dictionary<string, bool> DefaultRules() => new()
    {
        ["brackets"] = true, ["parentheses"] = true, ["speaker_labels"] = true, ["music_cues"] = true,
        ["dialogue_dashes"] = true, ["onscreen_text"] = true, ["sound_words"] = true, ["quote_italics"] = true,
        ["ocr_fixes"] = true, ["mixed_case_labels"] = false,
    };

    public SettingsView Copy()
    {
        var c = (SettingsView)MemberwiseClone();
        c.Rules = new Dictionary<string, bool>(Rules);
        return c;
    }
}

// ================================================================ Files

public sealed class FilesView
{
    public List<RowView> Rows { get; set; } = [];
    public string Summary { get; set; } = "";
    public string Note { get; set; } = "";
    public string How { get; set; } = "";
    public string Locks { get; set; } = "";
    public StatusView Status { get; set; } = new();
    public ButtonsView Buttons { get; set; } = new();
    public bool Busy { get; set; }
    public int Count { get; set; }
    public ToolsView Tools { get; set; } = new();
    public int? ReviewUid { get; set; }
}

public sealed class RowView
{
    public int Uid { get; set; }
    public string Name { get; set; } = "";
    public string Path { get; set; } = "";
    public string Kind { get; set; } = "video";
    public string State { get; set; } = "";
    public string Detail { get; set; } = "";
    public string Badge { get; set; } = "";
    public string BadgeKind { get; set; } = "";
    public double? Progress { get; set; }
    public bool Review { get; set; }
    public int Count { get; set; }
    public string CountLabel { get; set; } = "";
    public string Rename { get; set; } = "";
    public string RenameTone { get; set; } = "";
    public string Tip { get; set; } = "";
    public bool Removable { get; set; } = true;

    /// <summary>Same content: the row doesn't need redrawing.</summary>
    public bool SameAs(RowView? o) => o != null && o.Name == Name && o.Path == Path && o.State == State &&
        o.Detail == Detail && o.Badge == Badge && o.BadgeKind == BadgeKind && o.Progress == Progress &&
        o.Review == Review && o.Count == Count && o.CountLabel == CountLabel && o.Rename == Rename &&
        o.RenameTone == RenameTone && o.Tip == Tip && o.Removable == Removable;
}

public sealed class StatusView
{
    public string Text { get; set; } = "";
    public string Tone { get; set; } = "";
    public double? Progress { get; set; }
}

public sealed class ButtonsView
{
    public string Primary { get; set; } = "Clean and Save";
    public bool PrimaryVisible { get; set; }
    public bool PrimaryEnabled { get; set; }
    public bool StopVisible { get; set; }
    public bool OpenVisible { get; set; }
    public bool ClearVisible { get; set; }
}

public sealed class AddReply
{
    public int Added { get; set; }
    public List<Message> Messages { get; set; } = [];
    public string LastDir { get; set; } = "";
}

public sealed class Leftover
{
    public int Id { get; set; }
    public string Path { get; set; } = "";
    public string Name { get; set; } = "";
    public string Size { get; set; } = "";
    public bool Original { get; set; }
}

// ================================================================ questions and messages

public sealed class Message
{
    public string Title { get; set; } = "";
    public string Text { get; set; } = "";
    public string Detail { get; set; } = "";
    public List<List<string>> Rows { get; set; } = [];
    public bool Tools { get; set; }
    public bool Log { get; set; }
}

public sealed class ButtonDef
{
    public string Key { get; set; } = "";
    public string Label { get; set; } = "";
    public string Kind { get; set; } = "";
}

public sealed class OptionDef
{
    public string Key { get; set; } = "";
    public string Title { get; set; } = "";
    public string Desc { get; set; } = "";
    public bool Recommended { get; set; }
}

public sealed class Question
{
    public string Key { get; set; } = "";
    public string Title { get; set; } = "";
    public string Text { get; set; } = "";
    public string Detail { get; set; } = "";
    public List<List<string>> Rows { get; set; } = [];
    public int More { get; set; }
    public List<ButtonDef> Buttons { get; set; } = [];
    public List<OptionDef> Options { get; set; } = [];
    public string Checkbox { get; set; } = "";
    public List<int> Uids { get; set; } = [];
    public int? ReviewUid { get; set; }
}

public sealed class SaveReply
{
    public Question? Ask { get; set; }
    public Message? Tell { get; set; }
    public int? Started { get; set; }
    public bool Stop { get; set; }
}

public sealed class SaveDone
{
    public bool Cancelled { get; set; }
    public Dictionary<string, int> Counts { get; set; } = [];
    public string Status { get; set; } = "";
    public Message? Tell { get; set; }
    public List<string> Outputs { get; set; } = [];
}

// ================================================================ titles

public sealed class NameChoices
{
    public string Name { get; set; } = "";
    public string Query { get; set; } = "";
    public List<ChoiceView> Choices { get; set; } = [];
    public bool CanSearch { get; set; }
    public string Note { get; set; } = "";
}

public sealed class ChoiceView
{
    public string Id { get; set; } = "";
    public string Label { get; set; } = "";
    public string NewName { get; set; } = "";
}

// ================================================================ Review

public sealed class ReviewView
{
    public int Uid { get; set; }
    public HeadView Head { get; set; } = new();
    public List<TrackView> Tracks { get; set; } = [];
    public string Meta { get; set; } = "";
    public string TrackCount { get; set; } = "";
    public string TracksNote { get; set; } = "";
    public RulesView Rules { get; set; } = new();
    public string Filter { get; set; } = "changes";
    public string Empty { get; set; } = "";
    public List<LegendItem> Legend { get; set; } = [];
    public List<CueView> Rows { get; set; } = [];
    public List<MarkView> Marks { get; set; } = [];
    public double Duration { get; set; }
    public bool CheckOption { get; set; }
    public bool Nav { get; set; }
    public bool PreviewOnly { get; set; }
    public bool Busy { get; set; }
    public int Index { get; set; }
    public int Total { get; set; }
}

public sealed class HeadView
{
    public int Uid { get; set; }
    public string Name { get; set; } = "";
    public string Path { get; set; } = "";
    public string Detail { get; set; } = "";
    public string Badge { get; set; } = "";
    public string BadgeKind { get; set; } = "";
    public string Nosdh { get; set; } = "";
    public bool NameRow { get; set; }
    public string NameText { get; set; } = "";
    public string NameTone { get; set; } = "";
    public bool Multi { get; set; }
    public bool SaveOne { get; set; }
}

public sealed class TrackView
{
    public int Index { get; set; }
    public string Title { get; set; } = "";
    public string Language { get; set; } = "";
    public string Codec { get; set; } = "";
    public List<string> Flags { get; set; } = [];
    public string Stat { get; set; } = "";
    public string Tone { get; set; } = "";
    public bool Selected { get; set; }
    public bool Enabled { get; set; }
    public bool External { get; set; }
}

public sealed class RulesView
{
    public List<RuleView> Rows { get; set; } = [];
    public string Lyrics { get; set; } = "italic";
    public int LyricCount { get; set; }
    public bool Custom { get; set; }
    public string State { get; set; } = "";
}

public sealed class RuleView
{
    public string Key { get; set; } = "";
    public string Title { get; set; } = "";
    public string Example { get; set; } = "";
    public bool On { get; set; }
    public int? Count { get; set; }
    public bool Visible { get; set; } = true;
}

public sealed class LegendItem
{
    public string Kind { get; set; } = "";
    public string Text { get; set; } = "";
}

public sealed class Segment
{
    public string Text { get; set; } = "";
    public bool Gone { get; set; }
}

public sealed class CueView
{
    public int Cue { get; set; }
    public double Start { get; set; }
    public double End { get; set; }
    public string Time { get; set; } = "";
    public string Status { get; set; } = "kept";          // removed | edited | kept
    public List<Segment> Before { get; set; } = [];
    public string? After { get; set; }
    public bool Flagged { get; set; }
    public string Why { get; set; } = "";
    public string Override { get; set; } = "";            // "" | orig | clean
    public bool Italic { get; set; }
    public string Mark { get; set; } = "kept";

    public string BeforeText => string.Concat(Before.Select(s => s.Text));
}

public sealed class MarkView
{
    public double T { get; set; }
    public string K { get; set; } = "";
}

public sealed class ToggleReply
{
    public CueView Row { get; set; } = new();
    public List<LegendItem> Legend { get; set; } = [];
    public HeadView Head { get; set; } = new();
    public RulesView Rules { get; set; } = new();
}

public sealed class LogReply
{
    public string Text { get; set; } = "";
}
