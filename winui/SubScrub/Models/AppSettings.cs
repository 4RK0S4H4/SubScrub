namespace SubScrub.Models;

/// <summary>What the Settings dialog edits. Stage 1 keeps it in memory only: nothing is saved to disk yet, and the
/// defaults match the Python app's.</summary>
public sealed class AppSettings
{
    public string VideoMode { get; set; } = "replace";      // replace | new | sidecar
    public bool MakeDefault { get; set; } = true;
    public bool KeepSdh { get; set; }
    public string TrackName { get; set; } = "";
    public bool OneFilePerVideo { get; set; } = true;
    public string SubMode { get; set; } = "embed";          // embed | replace | new
    public bool Rename { get; set; } = true;
    public bool NameYear { get; set; }
    public string TmdbKey { get; set; } = "";
    public bool DropLanguages { get; set; } = true;
    public Dictionary<string, bool> Rules { get; set; } = DefaultRules();
    public string Lyrics { get; set; } = "italic";          // italic | keep | remove
    public string Theme { get; set; } = "system";           // system | light | dark
    public bool ConfirmReplace { get; set; } = true;
    public bool UpdateCheck { get; set; } = true;

    public static Dictionary<string, bool> DefaultRules() => new()
    {
        ["brackets"] = true, ["parentheses"] = true, ["speaker_labels"] = true, ["music_cues"] = true,
        ["dialogue_dashes"] = true, ["onscreen_text"] = true, ["sound_words"] = true, ["quote_italics"] = true,
        ["ocr_fixes"] = true, ["mixed_case_labels"] = false,
    };

    public AppSettings Copy()
    {
        var c = (AppSettings)MemberwiseClone();
        c.Rules = new Dictionary<string, bool>(Rules);
        return c;
    }
}
