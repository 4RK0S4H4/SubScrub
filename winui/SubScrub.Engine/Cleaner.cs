using System.Text;
using System.Text.RegularExpressions;

namespace SubScrub.Engine;

/// <summary>Which kinds of SDH text to take out (the defaults are the user's decided rules).</summary>
public sealed record CleanOptions
{
    public bool Brackets { get; init; } = true;           // [gunshot], [Grace]
    public bool Parentheses { get; init; } = true;        // (sighs), (ON PHONE)
    public bool SpeakerLabels { get; init; } = true;      // JOHN:  (and Name: when the file uses that style)
    public bool MusicCues { get; init; } = true;          // ♪ tense music ♪, ♪♪
    public bool Lyrics { get; init; }                     // true = remove sung lyrics entirely
    public bool LyricSymbols { get; init; } = true;       // kept lyrics: drop the ♪ marks, show them in italics
    public bool DialogueDashes { get; init; } = true;     // "- Hi." -> "Hi."
    public bool OnscreenText { get; init; } = true;       // [sign reads: EXIT] -> EXIT
    public bool OcrFixes { get; init; } = true;           // repair OCR damage (j" -> ♪, "[help" -> "I help")
    public bool SoundWords { get; init; } = true;         // "whoosh", "pew pew pew" on their own line
    public bool QuoteItalics { get; init; } = true;       // 'quoted' off-screen voices -> italics
    public bool MixedCaseLabels { get; init; }            // every "Name:", even in files that don't use them throughout

    /// <summary>From the Python option names (brackets, speaker_labels…); unknown names are ignored.</summary>
    public static CleanOptions FromDict(IReadOnlyDictionary<string, bool>? d)
    {
        var o = new CleanOptions();
        if (d == null)
            return o;
        bool G(string k, bool v) => d.TryGetValue(k, out var x) ? x : v;
        return new CleanOptions
        {
            Brackets = G("brackets", o.Brackets), Parentheses = G("parentheses", o.Parentheses),
            SpeakerLabels = G("speaker_labels", o.SpeakerLabels), MusicCues = G("music_cues", o.MusicCues),
            Lyrics = G("lyrics", o.Lyrics), LyricSymbols = G("lyric_symbols", o.LyricSymbols),
            DialogueDashes = G("dialogue_dashes", o.DialogueDashes), OnscreenText = G("onscreen_text", o.OnscreenText),
            OcrFixes = G("ocr_fixes", o.OcrFixes), SoundWords = G("sound_words", o.SoundWords),
            QuoteItalics = G("quote_italics", o.QuoteItalics), MixedCaseLabels = G("mixed_case_labels", o.MixedCaseLabels),
        };
    }
}

/// <summary>What happened to one cue. Text null = the cue is removed. Risk 0 routine, 1 worth a glance,
/// 2 a judgement call ("to check").</summary>
public sealed class CueResult
{
    public string? Text { get; set; }
    public bool Changed { get; set; }
    public HashSet<string> Rules { get; set; } = [];
    public int Risk { get; set; }
    public List<string> Why { get; set; } = [];

    public CueResult(string? text, bool changed)
    {
        Text = text;
        Changed = changed;
    }
}

/// <summary>What the rest of the document says about one cue.</summary>
public sealed class CueCtx
{
    public bool? ContinuesNext { get; set; }          // an unclosed [ or ( here is closed in the next cue (null = unknown)
    public bool? ContinuedFromPrev { get; set; }      // a stray ] or ) here closes the previous cue's
    public Dictionary<string, int>? MixedNames { get; set; }   // lower-case Name -> count, when the file uses "Name:" labels
    public string? OcrNotes { get; set; }             // "j" (♪ read as j") or "I" (♪ read as I)
    public bool OcrSuspect { get; set; }
    public bool QuoteVoices { get; set; }             // the file wraps off-screen voices in 'single quotes'
}

public sealed class DocNotes
{
    public Dictionary<string, int> MixedNames { get; set; } = [];
    public bool QuoteVoices { get; set; }
    public string? OcrNotes { get; set; }
    public bool OcrSuspect { get; set; }
    public List<string> Notes { get; set; } = [];
}

public sealed class CleanStats
{
    public int Total { get; set; }
    public int Removed { get; set; }
    public int Edited { get; set; }
    public Dictionary<string, int> ByRule { get; } = [];
    public int Risky { get; set; }
    public int Glance { get; set; }
    public int Changed => Removed + Edited;
}

public sealed class CleanResult
{
    public SubtitleDoc Doc { get; }
    public List<CueResult> Results { get; }
    public CleanStats Stats { get; }
    public DocNotes Notes { get; }
    public Dictionary<int, string> Overrides { get; set; } = [];   // cue index -> "orig" | "clean"

    public CleanResult(SubtitleDoc doc, List<CueResult> results, CleanStats stats, DocNotes notes)
    {
        Doc = doc;
        Results = results;
        Stats = stats;
        Notes = notes;
    }

    public string? Final(int i) => Overrides.GetValueOrDefault(i) == "orig" ? Doc.Cues[i].Text : Results[i].Text;

    public List<string?> Texts() => Enumerable.Range(0, Results.Count).Select(Final).ToList();

    public string Render() => Doc.Render(Texts());

    public List<int> Flagged() => Enumerable.Range(0, Results.Count).Where(i => Results[i].Changed && Results[i].Risk >= 2).ToList();

    public List<int> Unresolved() => Flagged().Where(i => !Overrides.ContainsKey(i)).ToList();

    /// <summary>"high": fine to save without looking; "check": a few judgement calls; "low": needs a look.</summary>
    public string Confidence
    {
        get
        {
            var n = Unresolved().Count;
            if (n == 0)
                return "high";
            if (n > Math.Max(12, Stats.Total * 0.03) || Notes.OcrSuspect && n > 4)
                return "low";
            return "check";
        }
    }
}

/// <summary>Strips hearing-impaired (SDH/CC) annotations from subtitle cues, keeping only dialogue. A port of
/// subscrub/cleaner.py, kept identical to it: tools/cleaner_parity.py fails on any difference in text, rules, risk
/// or reasons. Change the Python first, then this, and run the parity check.</summary>
public static class Cleaner
{
    private const RegexOptions I = RegexOptions.IgnoreCase | RegexOptions.CultureInvariant;
    private const int PhBase = 0xE000;                  // private-use chars stand in for formatting tags
    private const string PH = "[\uE000-\uF8FF]";
    private const string DASHC = @"\-\u2010\u2011\u2012\u2013\u2014\u2015\u2212";
    private const string DSH = $"(?:--(?=[ \t])|[{DASHC}](?![{DASHC}]))";
    private const string DSH_OPT = $@"(?:{DSH}\s*)?";
    private static readonly Regex StartsDash = Py.Anchored($@"\s*{DSH}");
    private static readonly Regex TagReText = new(@"<[^<>\n]*>|\{\\[^{}\n]*\}");
    private static readonly Regex TagReAss = new(@"\{[^{}]*\}");
    private static readonly Regex AssDrawing = new(@"\{[^{}]*\\p[1-9]");

    private static readonly Regex BracketsRe = new(@"[\[［【][^\[\]［］【】]*[\]］】]");
    private static readonly Regex ParensRe = new(@"[(（][^()（）]*[)）]");
    private const string MUSIC_CHARS = "♪♫♬♩";
    private const string MUSIC_ONE = @"(?:[♪♫♬♩]|\uD83C[\uDFB5\uDFB6])";   // ♪♫♬♩🎵🎶, one code point each
    private const string MUSIC_RUN = MUSIC_ONE + "+";
    private const string NOT_MUSIC = $@"(?:(?!{MUSIC_ONE})[\s\S])";
    private static readonly Regex MusicSeg = new($"{MUSIC_RUN}({NOT_MUSIC}*?){MUSIC_RUN}");
    private static readonly Regex MusicOneRe = new(MUSIC_ONE);
    private static readonly Regex MusicRunRe = new(MUSIC_RUN);
    private static readonly Regex HashLyric = new($@"^({PH}*\s*{DSH_OPT}{PH}*)#\s+(.*?)\s*#({PH}*)\s*$");

    private const string U = "A-ZÀ-ÖØ-ÞЀ-ЯΑ-Ω";      // upper-case letters
    private const string L = "a-zß-öø-ÿа-яα-ω";      // lower-case letters
    private const string LWORD = $@"[""“]?(?:Mc|Mac)?[{U}0-9#][{U}0-9.'’""“”\-#/]*";
    private const string LABEL_BODY = $@"{LWORD}(?:\s+(?:&|and|AND|{LWORD})){{0,4}}";
    private static readonly Regex LabelUpper = new(
        $@"^(?<pre>{PH}*\s*{DSH_OPT}{PH}*)[""“]?(?<label>{LABEL_BODY})\s*(?<sep>[:;])" +
        $@"(?<post>\s+|$|(?={PH})|(?=[{U}""“'‘¿¡]))\s*");
    private static readonly Regex LabelMidline = new(
        $@"(?:(?<=[.!?…])|(?<=[.!?…][""”])|(?<=[.!?…] )|(?<=[.!?…][""”] ))[ \t]*(?:{DSH}[ \t]*)?(?<label>{LABEL_BODY})[ \t]*:(?=\s|[{U}""“'‘])[ \t]*");
    private const string MIXED_NAME = $@"[{U}][{L}'’]+(?:[ -](?:[{U}][{U}{L}'’]*|\d+|on|of|the|over)){{0,3}}";
    private static readonly Regex LabelMixed = new($@"^(?<pre>{PH}*\s*{DSH_OPT}{PH}*)(?<label>{MIXED_NAME})\s*:\s+");
    private static readonly Regex MixedMidline = new($@"(?<=[.!?…])[ \t]+(?:{DSH}[ \t]*)?(?<label>{MIXED_NAME})[ \t]*:[ \t]+");
    private static readonly Regex MixedCandidate = new($@"(?:^|(?<=[.!?…]\s)|(?<=[{DASHC}]))\s*{DSH_OPT}({MIXED_NAME})\s*:\s+\S");
    private static readonly HashSet<string> LabelStop =
    [
        "NO", "OK", "OKAY", "YES", "OH", "HEY", "PS", "P.S.", "NOTE", "AM", "PM", "A.M.", "P.M.", "Q", "A",
        "WARNING", "ATTENTION", "CAUTION", "ALERT", "BREAKING", "UPDATE", "RE", "FYI",
    ];
    private static readonly HashSet<string> MixedStop =
    [
        "warning", "note", "attention", "caution", "size", "speed", "homework", "answer", "question",
        "location", "subject", "re", "ps", "step", "rule", "lesson", "chapter", "part", "day", "level",
        "yes", "no", "okay", "ok", "well", "so", "look", "listen", "remember", "but", "and", "or", "plus",
        "first", "second", "third", "next", "then", "now", "here", "there", "mission", "goal", "rule one",
        "number one rule", "travel tip", "translation", "meaning", "reason", "problem", "point", "fact",
        "news flash", "spoiler", "bottom line", "lesson one", "exhibit a", "dear diary", "the answer is",
        "attention all", "ladies", "folks", "guys", "dude", "ma'am",
    ];
    private static readonly Regex GenericSpeakers = Py.Anchored(
        @"^(?:man|woman|men|women|girl|boy|kid|child|all|both|crowd|everyone|group|" +
        @"announcer|narrator|reporter|anchor|voice|computer|guard|officer|doctor|nurse|" +
        @"soldier|pilot|driver|waiter|waitress|host|operator|dispatcher|teacher|student)" +
        @"(?:\s+\d+|\s+#\d+)?$", I);
    private static readonly Regex CcArrows = new($@"^({PH}*\s*)(?:>>+|&gt;&gt;)\s*");

    private static readonly Regex DescWords = new(
        @"\b(music|musical|instrumental|playing|plays|continues|fades|fading|swells|intensifies|builds|resumes|" +
        @"concludes|stops|humming|hums|whistling|whistles|vocalizing|singing|sings|theme|jingle|chiming|tune|" +
        @"melody|song|fanfare|score)\b", I);
    private static readonly Regex MusicPhrase = new(
        @"\b(music|song|melody|instrumental|tune|theme|fanfare|score|jingle)\s+(?:\w+\s+)?" +
        @"(playing|plays|continues|fades|fading|ends|stops|swells|builds|intensifies|resumes|concludes|rises|falls|" +
        @"up|out|in)\b|\b(playing|plays)\s*$", I);
    private static readonly Regex Onscreen = new(
        @"[\[(]\s*(?:on-?screen\s+)?(?:the\s+)?(?:sign|screen|text|note|card|caption|title|banner|graffiti|headline|" +
        @"letter|message|label|plaque|poster|display|newspaper|monitor|phone|computer|subtitle|writing)s?" +
        @"(?:\s+on\s+screen)?\s+(?:reads?|says?|shows?|displays?|reading)\s*:?\s*(?<t>[^\[\]()]+?)\s*[\])]" +
        @"|[\[(]\s*(?:on-?screen(?:\s+text)?|text\s+on\s+screen|reads|text)\s*:\s*(?<t2>[^\[\]()]+?)\s*[\])]", I);
    private static readonly Regex AnnotationHint = new(
        @"\b\w+(?:ing|s|ed)\b|\b(softly|quietly|loudly|whisper\w*|in [A-Z]\w+|on (?:phone|tv|radio|screen|speaker)|" +
        @"over (?:phone|radio|pa|intercom|speaker|comm)|tv|radio|music|indistinct|inaudible|laugh\w*|sigh\w*|" +
        @"continu\w*|both|all|crowd|man|woman|voice)\b", I);
    private static readonly Regex VocalSounds = new(
        @"^(humming|hums|whistling|whistles|vocalizing|vocalizes|singing|sings|chanting|scatting|" +
        @"yodeling|beatboxing|crooning|warbling|harmonizing|strumming|drumming|playing|continues|" +
        @"fades|ends|stops)$", I);
    private static readonly Regex Pronouns = new(@"\b(?:I|I'm|I'll|I've|I'd|me|my|mine|you|you're|your|we|we're|us|our)\b", I);
    private static readonly Regex RealIEnd = new(
        @"\b(?:\w+n['’]t|do|am|did|was|have|had|can|will|could|would|should|if|so|and|than|as|me|" +
        @"say|said|think|thought|guess|bet|swear|hope|know|mean|wish|not|where|who|what|" +
        @"how|why|when|shall|may|might|must|nor|neither|too)\s+I[.!?,…]*$", I);
    private static readonly HashSet<string> Onomatopoeia =
    [
        "boom", "kaboom", "ka-boom", "booom", "whoosh", "woosh", "swoosh", "fwoosh", "whoom", "vwoom", "vroom", "zoom",
        "thud", "thunk", "thump", "whump", "zoop", "pew", "zap", "zzap", "beep", "boop", "bleep", "bloop", "growl",
        "crash", "clang", "clank", "clink", "pow", "kapow", "ka-pow", "blam", "bam", "wham", "whack", "thwack", "smack",
        "bang", "click", "clack", "whirr", "whir", "whizz", "whiz", "swish", "smash", "crunch", "crack", "snap",
        "splash", "splat", "sploosh", "buzz", "bzzt", "kzzt", "zzt", "hiss", "sizzle", "fizz", "pop", "ping", "ding",
        "dong", "tick", "tock", "rumble", "rumbling", "screech", "shing", "schwing", "zing", "boing", "bonk", "honk",
        "toot", "vrrr", "brrr", "thwip", "thwip-thwip", "rat-ta", "rat-a-tat", "ratatat", "tat", "ka-chunk",
        "chunk", "clunk", "klunk", "creak", "squeak", "squelch", "slosh", "drip", "plop", "plink", "twang", "whoo",
        "vroosh", "shoom", "fwip", "zip", "zwip", "slap", "slam", "clap", "whap", "thwomp", "kerplunk", "kersplat", "kssh",
        "ksshh", "pssh", "psst", "whomp", "womp", "chomp", "crackle",
    ];
    private static readonly Regex SoundLine = new(@"^[a-z][a-z'\-]*(?:[ ,]+[a-z][a-z'\-]*){0,7}!?$");
    private static readonly Regex ContractionStart = new(
        @"^(?:cause|cos|coz|em|til|till|tis|twas|round|bout|kay|course|fore|n|nuff|sup|cept|" +
        @"bout|scuse|member|ello|allo|ow|uns|ere|nother|ve|re|ll|d|s|m|t|n')\b", I);
    private static readonly Regex Word = new(@"\w");
    private static readonly Regex DashLine = new($@"^({PH}*)\s*{DSH}\s*");
    private static readonly Regex PhRe = new(PH);
    private static readonly Regex NotPh = new("[^\uE000-\uF8FF]");

    private const string NUMBER_WORDS = "zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|" +
        "fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|" +
        "hundred|thousand|million";
    private static readonly Regex SpeechHint = new(
        $@"\b(?:he|she|it|they|them|him|his|her|hers|its|their|we|us|our|you|your|I|me|my|mine|{NUMBER_WORDS})\b|\w['’](?:s|t|re|ve|ll|d|m)\b|\d", I);

    // the many small one-off patterns of clean_cue, compiled once
    private static readonly Regex OcrJStart = new($@"^({PH}*\s*{DSH_OPT}{PH}*)j\s?""\s*");
    private static readonly Regex OcrJEnd = new(@"\s*j\s?""\s*$");
    private static readonly Regex OcrJParen = new(@"\(J'\s*");
    private static readonly Regex OcrIStart = new($@"^{PH}*\s*{DSH_OPT}{PH}*I\s");
    private static readonly Regex OcrIStartSub = new($@"^({PH}*\s*{DSH_OPT}{PH}*)I\s");
    private static readonly Regex OcrIEnd = new($@"\sI{PH}*$");
    private static readonly Regex OcrIEndSub = new($@"\sI({PH}*)$");
    private static readonly Regex OcrBrace = new(@"\{(?=[A-Za-z])");
    private static readonly Regex Italic = new(@"<i>|\\i1", I);
    private static readonly Regex OpenBracketTail = new(@"[\[［【]([^\]］】]*)$");
    private static readonly Regex LowerWord = Py.Anchored($@"[{L}]+\b");
    private static readonly Regex AnyCloseBracket = new(@"[\]］】]");
    private static readonly Regex BeforeStrayBracket = new(@"^[^\[［【]*?[\]］】]");
    private static readonly Regex DashCharRe = new($"[{DASHC}]");
    private static readonly Regex LowerRe = new($"[{L}]");
    private static readonly Regex UnclosedParenTail = new(@"[(（](?=[^()（）]*$)");
    private static readonly Regex OpenParenTail = new(@"[(（][^)）]*$");
    private static readonly Regex BeforeStrayParen = new(@"^[^(（]*?[)）]");
    private static readonly Regex AssMotion = new(@"\\(?:pos|move|k[fo]?\d|t\()");
    private static readonly Regex QuoteOpen = new($@"^({PH}*\s*{DSH_OPT}{PH}*)['‘](?=\S)");
    private static readonly Regex QuoteClose = new($@"[^\s'’] ?['’]+({PH}*)\s*$");
    private static readonly Regex QuoteInEnd = new($@"\b\w+in['’]{PH}*\s*$");
    private static readonly Regex QuoteCloseSub = new($@" ?['’]+({PH}*)\s*$");
    private static readonly Regex LeadingPh = Py.Anchored($"^{PH}*");
    private static readonly Regex MidDash = new($@"(?<=[.!?…,])({PH}*)[ \t]+{DSH}[ \t]*(?=[\uE000-\uF8FF{U}""“'‘¿¡.])");
    private static readonly Regex Spaces2 = new(@"[ \t]{2,}");
    private static readonly Regex SpaceBeforePunct = new(@"(?<!\.)\s+([,!?;:…]|\.(?!\.))");
    private static readonly Regex DoublePunct = new(@"([,;])\s*[,;]+");
    private static readonly Regex LeadingPunct = new($@"^({PH}*\s*{DSH_OPT}{PH}*)\s*[,;:]+\s*");
    private static readonly Regex LeadingSpaceAll = new($@"^((?:{PH}|\s)*?)\s+(?=\S)");
    private static readonly Regex LeadingSpace = new($@"^({PH}*)\s+");
    private static readonly Regex DashPrefix = Py.Anchored($@"^{PH}*{DSH}\s?");
    private static readonly Regex TrailingSpace = new($@"({PH}*)\s+({PH}*)$");
    private static readonly Regex EmptyTag = new(@"<(\w+)[^<>]*>(\s*)</\1>");
    private static readonly Regex StripDashRe = new($@"^\s*{DSH_OPT}");
    private static readonly Regex Sentence = new(@"[.?!]");
    private static readonly Regex WordStart = Py.Anchored(@"[\w""“'‘]");
    private static readonly Regex DigitsDots = new(@"\A[\d\s.:]+\z");

    // ================================================================ small helpers

    private static bool StartsDashed(string vis) => StartsDash.IsMatch(vis);

    /// <summary>The visible text of a line without its dialogue dash.</summary>
    private static string StripDash(string vis) => Py.Strip(StripDashRe.Replace(vis, "", 1));

    private static bool HasWords(string s) => Word.IsMatch(s);

    private static string Visible(string s) => PhRe.Replace(s, "");

    private static string TagsOnly(string s) => NotPh.Replace(s, "");

    private static bool StartsMusic(string s) => s.Length > 0 && (MUSIC_CHARS.Contains(s[0]) || s.StartsWith("🎵", StringComparison.Ordinal) || s.StartsWith("🎶", StringComparison.Ordinal));

    private static bool EndsMusic(string s) => s.Length > 0 && (MUSIC_CHARS.Contains(s[^1]) || s.EndsWith("🎵", StringComparison.Ordinal) || s.EndsWith("🎶", StringComparison.Ordinal));

    private static bool HasMusic(string s) => MusicOneRe.IsMatch(s);

    private static List<char> Letters(string s) => s.Where(char.IsLetter).ToList();

    /// <summary>True for '♪ tense music playing ♪' style descriptions, false for sung lyrics.</summary>
    private static bool IsMusicDescription(string content)
    {
        var c = Py.Strip(Py.Strip(Py.Strip(Visible(content)), "[]()"));
        if (c.Length == 0)
            return true;
        var words = Py.Words(c);
        var letters = Letters(c);
        var upper = letters.Count > 0 && letters.All(char.IsUpper);
        if (MusicPhrase.IsMatch(c) && words.Count <= 8)
            return true;
        if ((char.IsLower(c[0]) || upper) && DescWords.IsMatch(c) && words.Count <= 8)
            return true;
        return words.Count <= 3 && char.IsLower(c[0]) && VocalSounds.IsMatch(words[^1]);
    }

    /// <summary>Parenthetical content that is clearly a sound or manner note (vs. dialogue in parentheses).</summary>
    private static bool LooksLikeAnnotation(string content)
    {
        var c = Py.Strip(Visible(content));
        var letters = Letters(c);
        if (letters.Count == 0)
            return true;
        if (letters.All(char.IsUpper))
            return true;
        if (StartsMusic(c))
            return true;
        var words = Py.Words(c);
        if (words.Count <= 4 && !Sentence.IsMatch(c) && !Pronouns.IsMatch(c))
            return true;
        return AnnotationHint.IsMatch(c) && words.Count <= 6 && !Pronouns.IsMatch(c);
    }

    private static bool SoundsLikeSpeech(string content) => SpeechHint.IsMatch(Visible(content));

    /// <summary>The span has words on both sides within the same sentence, so removing it could take part of a
    /// spoken line with it.</summary>
    private static bool InsideSentence(string whole, int start, int end)
    {
        var before = Py.RStrip(Visible(whole[..start]).Split('\n')[^1]);
        var rest = Visible(whole[end..]).Split('\n');
        var after = Py.LStrip(rest[0]);
        if (after.Length == 0)
        {
            var nxt = rest.Skip(1).Where(r => Py.Strip(r).Length > 0).Select(Py.LStrip).FirstOrDefault() ?? "";
            after = nxt.Length > 0 && char.IsLower(nxt[0]) ? nxt : "";
        }
        return HasWords(before) && !".!?…".Contains(before[^1]) && WordStart.IsMatch(after);
    }

    private static bool LabelOk(string label)
    {
        var lab = Py.Strip(Py.Strip(label), "\"“”");
        if (lab.Count(char.IsLetter) < 2 || LabelStop.Contains(UpperPy(lab)))
            return false;
        if (DigitsDots.IsMatch(lab) || lab.Contains(" - ", StringComparison.Ordinal) || Py.Words(lab).Count > 5)
            return false;
        return lab.Count(char.IsUpper) >= 2;
    }

    /// <summary>str.upper(): ß becomes SS like Python's.</summary>
    private static string UpperPy(string s) => s.ToUpperInvariant().Replace("ß", "SS");

    private static bool Unclosed(string vis, char o, char c)
    {
        var depth = 0;
        foreach (var ch in vis)
        {
            if (ch == o)
                depth++;
            else if (ch == c && depth > 0)
                depth--;
        }
        return depth > 0;
    }

    private static bool StrayClose(string vis, char o, char c)
    {
        int fc = vis.IndexOf(c), fo = vis.IndexOf(o);
        return fc != -1 && (fo == -1 || fc < fo);
    }

    private sealed class Rec
    {
        public readonly HashSet<string> Rules = [];
        public int Risk;
        public readonly List<string> Why = [];

        public void Hit(string rule, int risk = 0, string? why = null)
        {
            Rules.Add(rule);
            Risk = Math.Max(Risk, risk);
            if (why != null && !Why.Contains(why))
                Why.Add(why);
        }
    }

    private static (string Text, List<string> Tags) Protect(string text, Regex tagRe)
    {
        var tags = new List<string>();
        var work = tagRe.Replace(text, m =>
        {
            tags.Add(m.Value);
            return ((char)(PhBase + tags.Count - 1)).ToString();
        });
        return (work, tags);
    }

    private static string Restore(string text, List<string> tags) => PhRe.Replace(text, m => tags[m.Value[0] - PhBase]);

    private static (string Text, bool Hit) SubLoop(Regex pattern, string text, MatchEvaluator repl)
    {
        var hit = false;
        while (true)
        {
            var current = text;
            var next = pattern.Replace(current, repl);
            if (next == current)
                return (current, hit);
            hit = true;
            text = next;
        }
    }

    private static string Sub(Regex re, string s, string repl, int count = -1) => re.Replace(s, repl, count);

    private static string Join(IEnumerable<string> lines) => string.Join("\n", lines);

    private static string? G(Match m, string name) => m.Groups[name].Success ? m.Groups[name].Value : null;

    // ================================================================ the cleaner

    public static CueResult CleanCue(string text, CleanOptions opts, string fmt = "srt", CueCtx? ctx = null)
    {
        ctx ??= new CueCtx();
        if (fmt == "ass" && AssDrawing.IsMatch(text))
            return new CueResult(text, false);                 // vector drawing / typesetting: leave it
        var tagRe = fmt == "ass" ? TagReAss : TagReText;
        var (work, tags) = Protect(text, tagRe);
        if (tags.Count > 6000 || PhRe.IsMatch(text))
            return new CueResult(text, false);
        var rec = new Rec();
        var alreadyItalic = Italic.IsMatch(string.Concat(tags));
        var (italOn, italOff) = fmt == "ass" ? ("{\\i1}", "{\\i0}") : ("<i>", "</i>");

        string Italicise(string t)
        {
            t = Py.Strip(t);
            return alreadyItalic || !HasWords(Visible(t)) ? t : $"{italOn}{t}{italOff}";
        }

        // --- OCR damage (only where the document showed the pattern)
        if (opts.OcrFixes)
        {
            if (ctx.OcrNotes == "j")
            {
                var fixedText = Join(work.Split('\n').Select(l => OcrJEnd.Replace(OcrJStart.Replace(l, "${1}♪ "), " ♪")));
                fixedText = OcrJParen.Replace(fixedText, "(♪ ");
                if (fixedText != work)
                {
                    work = fixedText;
                    rec.Hit("ocr", 1, "♪ had been misread as j\" by OCR");
                }
            }
            else if (ctx.OcrNotes == "I")
            {
                var ls = work.Split('\n');
                if (OcrIStart.IsMatch(ls[0]) && OcrIEnd.IsMatch(ls[^1]) && !RealIEnd.IsMatch(Visible(ls[^1])))
                {
                    ls[0] = OcrIStartSub.Replace(ls[0], "${1}♪ ", 1);
                    ls[^1] = OcrIEndSub.Replace(ls[^1], " ♪${1}");
                    work = Join(ls);
                    rec.Hit("ocr", 1, "♪ had been misread as I by OCR");
                }
            }
            if (fmt != "ass" && OcrBrace.IsMatch(work))
            {
                work = OcrBrace.Replace(work, "[");
                rec.Hit("ocr", 1, "[ had been misread as { by OCR");
            }
        }

        var origLines = work.Split('\n');

        // --- "# lyric #" style (old subtitle files) -> ♪ lyric ♪
        work = Join(work.Split('\n').Select(l => HashLyric.Replace(l, "${1}♪ ${2} ♪${3}")));

        // --- on-screen text: [sign reads: EXIT] -> EXIT
        if (opts.OnscreenText)
        {
            work = Onscreen.Replace(work, m =>
            {
                var t = Py.Strip(Py.Strip(Py.Strip(G(m, "t") ?? G(m, "t2") ?? ""), "\"“”'‘’"));
                rec.Hit("onscreen", 1, "an on-screen text note was turned into the text itself");
                return t;
            });
        }

        // --- sound descriptions and bracket speaker IDs
        if (opts.Brackets)
        {
            bool hit;
            (work, hit) = SubLoop(BracketsRe, work, _ => " ");
            if (hit)
                rec.Hit("brackets");
            var lines = new List<string>();
            foreach (var line in work.Split('\n'))
            {
                var l = line;
                var v = Visible(l);
                var m = OpenBracketTail.Match(l);
                var open = m.Success ? m.Value[0] : ' ';
                var close = open switch { '[' => ']', '［' => '］', _ => '】' };
                if (m.Success && Unclosed(v, open, close))
                {
                    var follow = Py.Strip(Visible(m.Groups[1].Value));
                    var before = l[..m.Index];
                    if (ctx.ContinuesNext != false)                 // closes in the next cue (or we can't tell)
                    {
                        l = before + " ";
                        rec.Hit("brackets");
                    }
                    else if (opts.OcrFixes && ctx.OcrSuspect && LowerWord.IsMatch(follow)
                             && (Py.Strip(Visible(before)).Length == 0 || before.EndsWith(' ')))
                    {
                        l = before + "I " + Py.LStrip(m.Groups[1].Value);
                        rec.Hit("ocr", 2, "a “[” looked like an OCR'd “I” and was turned back into one");
                    }
                    else
                    {
                        l = before + " ";
                        var small = Py.Words(follow).Count <= 1;
                        rec.Hit("brackets", small ? 1 : 2,
                                "removed text after a “[” that never closes" + (small ? "" : " — check it"));
                    }
                }
                else if (AnyCloseBracket.IsMatch(v) && StrayClose(v, '[', ']'))
                {
                    var l2 = BeforeStrayBracket.Replace(l, " ");
                    if (l2 != l)
                    {
                        var gone = Visible(l[..(l.Length - l2.Length + 1)]);
                        var risky = ctx.ContinuedFromPrev == false && !LooksLikeAnnotation(Py.Strip(gone, "] "));
                        l = l2;
                        rec.Hit("brackets", risky ? 2 : 0, risky ? "removed text before a stray “]”" : null);
                    }
                }
                lines.Add(l);
            }
            work = Join(lines);
        }

        // --- a second speaker's name in the middle of a line ("Nice.HOLDEN: Shut up.", "nice? -IAN: No.")
        if (opts.SpeakerLabels)
        {
            string MidRepl(Match m)
            {
                var lead = m.Value[..(m.Groups["label"].Index - m.Index)];
                if (lead.Length == 0)                              // glued straight on: a line break got lost
                    return "\n";
                return DashCharRe.IsMatch(lead) && !opts.DialogueDashes ? " -" : " ";
            }
            var source = work;
            work = LabelMidline.Replace(source, m =>
            {
                var lineBefore = Visible(source[..m.Index].Split('\n')[^1]);
                if (!LabelOk(m.Groups["label"].Value) || Py.Strip(lineBefore).Length == 0 || !LowerRe.IsMatch(lineBefore))
                    return m.Value;                                // "DR. SMITH:" is one label
                rec.Hit("labels", 1, "removed a second speaker's name from the middle of a line");
                return MidRepl(m);
            });
            if (ctx.MixedNames is { Count: > 0 } names)
            {
                work = MixedMidline.Replace(work, m =>
                {
                    var name = m.Groups["label"].Value.ToLowerInvariant();
                    if (names.ContainsKey(name) || GenericSpeakers.IsMatch(name))
                    {
                        rec.Hit("labels", 1, "removed a second speaker's name from the middle of a line");
                        return MidRepl(m);
                    }
                    return m.Value;
                });
            }
        }

        if (opts.Parentheses)
        {
            var risky = new List<string>();
            var current = work;
            var hit = false;
            while (true)
            {
                var input = current;
                var next = ParensRe.Replace(input, m =>
                {
                    var inner = m.Value[1..^1];
                    if (!LooksLikeAnnotation(inner) || SoundsLikeSpeech(inner) && InsideSentence(input, m.Index, m.Index + m.Length))
                        risky.Add(inner);
                    return " ";
                });
                if (next == input)
                    break;
                hit = true;
                current = next;
            }
            work = current;
            if (hit)
                rec.Hit("parentheses", risky.Count > 0 ? 2 : 0,
                        risky.Count > 0 ? $"removed “({Py.Strip(risky[0])})” — it could be dialogue" : null);
            // an unclosed "(" whose note runs over several lines: (MUSIC FADING UP,\n"BRANDY
            var um = UnclosedParenTail.Match(work);
            if (um.Success && work[um.Index..].Contains('\n') && Unclosed(Visible(work), '(', ')'))
            {
                var follow = Visible(work[(um.Index + 1)..]);
                var letters = Letters(follow);
                if (letters.Count > 0 && letters.All(char.IsUpper))
                {
                    work = work[..um.Index] + " " + TagsOnly(work[um.Index..]);
                    rec.Hit("parentheses", ctx.ContinuesNext != false ? 0 : 1, ctx.ContinuesNext != false ? null : "a “(” never closes");
                }
            }
            var lines = new List<string>();
            foreach (var line in work.Split('\n'))
            {
                var l = line;
                var v = Visible(l);
                if (Unclosed(v, '(', ')'))
                {
                    var m = OpenParenTail.Match(l);
                    if (m.Success && (ctx.ContinuesNext != false || LooksLikeAnnotation(m.Value[1..])))
                    {
                        l = l[..m.Index] + " ";
                        rec.Hit("parentheses", ctx.ContinuesNext != false ? 0 : 1, ctx.ContinuesNext != false ? null : "a “(” never closes");
                    }
                }
                else if (StrayClose(v, '(', ')') && ctx.ContinuedFromPrev != false)
                {
                    var l2 = BeforeStrayParen.Replace(l, " ");
                    if (l2 != l && LooksLikeAnnotation(Visible(l[..(l.Length - l2.Length)])))
                    {
                        l = l2;
                        rec.Hit("parentheses");
                    }
                }
                lines.Add(l);
            }
            work = Join(lines);
        }

        // --- music
        var restyle = !opts.Lyrics && opts.LyricSymbols;
        if (opts.MusicCues || opts.Lyrics || restyle)
        {
            work = MusicSeg.Replace(work, m =>
            {
                var isDesc = IsMusicDescription(m.Groups[1].Value);
                if (isDesc && opts.MusicCues || !isDesc && opts.Lyrics)
                {
                    rec.Hit(isDesc ? "music" : "lyrics");
                    return " ";
                }
                if (restyle && !isDesc)
                {
                    rec.Hit("lyric_style");
                    return Italicise(m.Groups[1].Value);
                }
                return m.Value;
            });
            var lines = new List<string>();
            var inLyric = false;
            foreach (var l in work.Split('\n'))
            {
                var v = StripDash(Visible(l));
                var hasNote = HasMusic(v);
                var starts = StartsMusic(v);
                var ends = EndsMusic(v);
                if (hasNote && !HasWords(v))                                 // "♪♪", "♪"
                {
                    if (opts.MusicCues)
                    {
                        rec.Hit("music");
                        lines.Add(TagsOnly(l));
                    }
                    else
                        lines.Add(l);
                    continue;
                }
                if (inLyric && !hasNote && StartsDashed(Visible(l)))
                    inLyric = false;                                         // "- OK, take off…": a new speaker
                if ((inLyric || starts || ends) && HasWords(v))
                {
                    if (starts && !ends)
                        inLyric = true;
                    else if (ends)
                        inLyric = false;
                    var desc = IsMusicDescription(MusicOneRe.Replace(v, ""));
                    if (desc && opts.MusicCues || !desc && opts.Lyrics)
                    {
                        rec.Hit(desc ? "music" : "lyrics");
                        lines.Add(TagsOnly(l));
                        continue;
                    }
                    if (restyle && !desc)
                    {
                        rec.Hit("lyric_style");
                        var dm = DashLine.Match(l);
                        var prefix = dm.Success ? dm.Value : "";
                        lines.Add(prefix + Italicise(MusicRunRe.Replace(l[prefix.Length..], "")));
                        continue;
                    }
                }
                lines.Add(l);
            }
            work = Join(lines);
        }

        // --- speaker labels at the start of a line
        if (opts.SpeakerLabels || opts.MixedCaseLabels)
        {
            var ls = work.Split('\n');
            var lines = new List<string>();
            for (var idx = 0; idx < ls.Length; idx++)
            {
                var l = ls[idx];
                if (opts.SpeakerLabels)
                {
                    var l2 = CcArrows.Replace(l, "${1}");
                    if (l2 != l)
                    {
                        rec.Hit("labels");
                        l = l2;
                    }
                    var m = LabelUpper.Match(l);
                    if (m.Success)
                    {
                        var rest = l[(m.Index + m.Length)..];
                        var label = m.Groups["label"].Value;
                        var ok = LabelOk(label);
                        if (ok && m.Groups["sep"].Value == ";" && (Py.Strip(Visible(rest)).Length > 0 || idx == ls.Length - 1))
                            ok = false;                                      // "COMPUTER;" only when alone on its line
                        var tail = Py.Strip(Visible(rest)).Length > 0 ? rest : Join(ls.Skip(idx + 1));
                        if (ok && Py.Words(label).Count >= 2 && HasWords(Visible(tail)) && !LowerRe.IsMatch(Visible(tail)))
                            ok = false;                                      // "PASSIVE SCAN: MAXIMUM RANGE": on-screen text
                        if (ok)
                        {
                            l = m.Groups["pre"].Value + rest;
                            var semi = m.Groups["sep"].Value == ";";
                            rec.Hit("labels", semi ? 1 : 0, semi ? "removed a speaker name written with “;”" : null);
                        }
                    }
                }
                var mm = LabelMixed.Match(l);
                if (mm.Success)
                {
                    var name = mm.Groups["label"].Value.ToLowerInvariant();
                    var known = ctx.MixedNames ?? [];
                    if (opts.SpeakerLabels && (known.ContainsKey(name) || known.Count > 0 && GenericSpeakers.IsMatch(name)))
                    {
                        l = mm.Groups["pre"].Value + l[(mm.Index + mm.Length)..];
                        var once = known.GetValueOrDefault(name, 0) < 2 && !GenericSpeakers.IsMatch(name);
                        rec.Hit("labels", once ? 1 : 0,
                                once ? $"removed “{mm.Groups["label"].Value}:” (only used once in this file)" : null);
                    }
                    else if (opts.MixedCaseLabels && !MixedStop.Contains(name))
                    {
                        l = mm.Groups["pre"].Value + l[(mm.Index + mm.Length)..];
                        rec.Hit("labels", 2, $"removed “{mm.Groups["label"].Value}:” — it could be dialogue");
                    }
                }
                lines.Add(l);
            }
            work = Join(lines);
        }

        // --- bare sound words: a line that is nothing but "whoosh" / "pew pew pew" / "ka-boom"
        if (opts.SoundWords && !(fmt == "ass" && AssMotion.IsMatch(string.Concat(tags))))
        {
            var lines = new List<string>();
            foreach (var l in work.Split('\n'))
            {
                var v = StripDash(Visible(l));
                if (v.Length > 0 && SoundLine.IsMatch(v) &&
                    Py.Words(v.Replace(",", " ")).All(w => Onomatopoeia.Contains(Py.Strip(w, ",!"))))
                {
                    rec.Hit("sound_words");
                    lines.Add(TagsOnly(l));
                    continue;
                }
                lines.Add(l);
            }
            work = Join(lines);
        }

        // --- 'quoted' off-screen voices (files that use quotes instead of italics) -> italics
        if (opts.QuoteItalics && ctx.QuoteVoices)
        {
            var ls = work.Split('\n');
            int? openAt = null, closeAt = null;
            for (var i = 0; i < ls.Length; i++)
            {
                var l = ls[i];
                if (Py.Strip(Visible(l)).Length == 0)
                    continue;
                var m = QuoteOpen.Match(l);
                if (openAt == null && m.Success && !ContractionStart.IsMatch(Visible(l[(m.Index + m.Length)..])))
                {
                    openAt = i;
                    ls[i] = m.Groups[1].Value + l[(m.Index + m.Length)..];
                }
                if (QuoteClose.IsMatch(ls[i]) && !QuoteInEnd.IsMatch(ls[i]))
                {
                    closeAt = i;
                    ls[i] = QuoteCloseSub.Replace(ls[i], "${1}");
                }
            }
            if (openAt != null || closeAt != null)
            {
                var first = openAt ?? 0;
                var last = closeAt ?? ls.Length - 1;
                if (first <= last)
                {
                    for (var i = first; i <= last; i++)
                    {
                        var dm = DashLine.Match(ls[i]);
                        var prefix = dm.Success ? dm.Value : LeadingPh.Match(ls[i]).Value;
                        ls[i] = prefix + Italicise(ls[i][prefix.Length..]);
                    }
                    rec.Hit("quotes");
                }
                work = Join(ls);
            }
        }

        // --- dialogue dashes
        if (opts.DialogueDashes)
        {
            var n = MidDash.Matches(work).Count;
            work = MidDash.Replace(work, "${1} ");                          // "do. -Oh, God."
            if (n > 0)
                rec.Hit("dashes");
            var ls = work.Split('\n');
            var updated = ls.Select(l => StartsDashed(Visible(l)) ? DashLine.Replace(l, "${1}", 1) : l).ToArray();
            if (ls.Zip(updated).Any(p => p.First != p.Second && HasWords(Visible(p.Second))))
                rec.Hit("dashes");
            work = Join(updated);
        }

        if (rec.Rules.Count == 0)
            return new CueResult(text, false);

        // --- tidy up what's left
        var kept = new List<string>();
        var carry = "";                                    // tags from dropped lines, e.g. an opening <i>
        foreach (var line in work.Split('\n'))
        {
            var l = carry + line;
            carry = "";
            l = Spaces2.Replace(l, " ");
            l = SpaceBeforePunct.Replace(l, "${1}");
            l = DoublePunct.Replace(l, "${1}");
            l = LeadingPunct.Replace(l, "${1}");
            if (opts.DialogueDashes)
                l = LeadingSpaceAll.Replace(l, "${1}");
            else
            {
                l = LeadingSpace.Replace(l, "${1}");
                var dm = DashPrefix.Match(l);
                if (dm.Success)
                    l = dm.Value + LeadingSpace.Replace(l[dm.Length..], "${1}");
            }
            l = Py.RStrip(TrailingSpace.Replace(l, "${1}${2}"));
            if (!HasWords(Visible(l)))
            {
                carry = TagsOnly(l);
                continue;
            }
            kept.Add(l);
        }
        if (carry.Length > 0 && kept.Count > 0)
            kept[^1] += carry;

        if (kept.Count == 0)
            return Result(null, rec);

        // a two-speaker cue that lost one speaker: drop the now-pointless dash (when dashes are kept)
        if (!opts.DialogueDashes)
        {
            var origDashes = origLines.Count(l => StartsDashed(Visible(l)));
            var dashIdx = kept.Select((l, i) => (l, i)).Where(x => StartsDashed(Visible(x.l))).Select(x => x.i).ToList();
            if (origDashes >= 2 && dashIdx.Count == 1 && dashIdx[0] == 0)
                kept[0] = DashLine.Replace(kept[0], "${1}", 1);
        }

        var output = Restore(Join(kept), tags);
        output = output.Replace($"{italOff}\n{italOn}", "\n");           // lyric lines italicised one by one -> one block
        if (fmt != "ass")
        {
            (output, _) = SubLoop(EmptyTag, output, m => m.Groups[2].Value);
            output = Py.Strip(output);
        }
        if (!HasWords(VisibleText(output, fmt)))
            return Result(null, rec);
        if (output == text)
            return new CueResult(text, false);
        return Result(output, rec);
    }

    private static CueResult Result(string? text, Rec rec) =>
        new(text, true) { Rules = [.. rec.Rules], Risk = rec.Risk, Why = [.. rec.Why] };

    // ================================================================ document analysis

    private static readonly Regex OcrJLineStart = Py.Anchored($@"^\s*{DSH_OPT}j\s?""");
    private static readonly Regex OcrJLineEnd = new(@"j\s?""\s*$");
    private static readonly Regex OcrILineStart = Py.Anchored($@"^\s*{DSH_OPT}I\s");
    private static readonly Regex OcrILineEnd = new(@"\sI$");
    private static readonly Regex OcrWeird = new(@"\[M1\b|\{[a-z]|\[[A-Z][a-z]+fl\b");
    private static readonly Regex QuoteOpens = Py.Anchored($@"^\s*(?:\([^)]*\)\s*)?{DSH_OPT}['‘](?=\w)");
    private static readonly Regex QuoteCloses = new(@"[^\s'’] ?['’]\s*$");
    private static readonly Regex QuoteClosesIn = new(@"\b\w+in['’]\s*$");

    public static (DocNotes Notes, List<CueCtx> Ctxs) AnalyseDoc(SubtitleDoc doc)
    {
        var vis = doc.Cues.Select(c => VisibleText(c.Text, doc.Fmt)).ToList();
        var dn = new DocNotes();

        // "Name:" style labels used consistently through the file
        var names = new OrderedCounter();
        foreach (var v in vis)
        {
            foreach (var line in v.Split('\n'))
            {
                foreach (Match m in MixedCandidate.Matches(line))
                {
                    var n = Py.Strip(m.Groups[1].Value);
                    if (!MixedStop.Contains(n.ToLowerInvariant()))
                        names.Add(n.ToLowerInvariant());
                }
            }
        }
        var repeated = names.Items.Where(x => x.Count >= 2).Select(x => x.Key).ToList();
        if (names.Total >= 8 && repeated.Count >= 3)
        {
            dn.MixedNames = names.Items.Where(x => x.Count >= 2 || GenericSpeakers.IsMatch(x.Key) || !x.Key.Contains(' '))
                .ToDictionary(x => x.Key, x => x.Count);
            dn.Notes.Add("This file writes speaker names like “Peter:” — those are removed too.");
        }

        // OCR'd tracks: ♪ read as j" / (J' or as a leading + trailing I
        var j = vis.Sum(v => v.Split('\n').Count(l => OcrJLineStart.IsMatch(l) || OcrJLineEnd.IsMatch(l) || l.Contains("(J'", StringComparison.Ordinal)));
        var inote = vis.Count(v => OcrILineStart.IsMatch(v) && OcrILineEnd.IsMatch(v) && !RealIEnd.IsMatch(v));
        if (j >= 3)
            dn.OcrNotes = "j";
        else if (inote >= 3)
            dn.OcrNotes = "I";
        var weird = vis.Count(v => OcrWeird.IsMatch(v));
        var lone = vis.Count(v => Unclosed(v, '[', ']'));
        dn.OcrSuspect = dn.OcrNotes != null || weird >= 2 || lone >= 4;
        if (dn.OcrSuspect)
            dn.Notes.Add("This track looks machine-converted from disc images (OCR), so some lines may be garbled.");

        // 'Quoted' voices: off-screen/radio/narration wrapped in single quotes instead of italics
        bool Opens(string v)
        {
            var m = QuoteOpens.Match(v);
            return m.Success && !ContractionStart.IsMatch(v[(m.Index + m.Length)..]);
        }
        bool Closes(string v) => QuoteCloses.IsMatch(v) && !QuoteClosesIn.IsMatch(v);
        var wrapped = vis.Count(v => Opens(v) && Closes(v));
        if (wrapped >= 5 && wrapped >= 0.02 * vis.Count)
        {
            dn.QuoteVoices = true;
            dn.Notes.Add("This file marks off-screen voices with 'quotes' — they're shown in italics instead.");
        }

        var ctxs = vis.Select(_ => new CueCtx
        {
            MixedNames = dn.MixedNames.Count > 0 ? dn.MixedNames : null, OcrNotes = dn.OcrNotes,
            OcrSuspect = dn.OcrSuspect, QuoteVoices = dn.QuoteVoices,
        }).ToList();
        for (var i = 0; i < vis.Count; i++)
        {
            var v = vis[i];
            var nxt = i + 1 < vis.Count ? vis[i + 1] : "";
            foreach (var (o, c) in new[] { ('[', ']'), ('(', ')') })
            {
                if (Unclosed(v, o, c))
                {
                    var closes = StrayClose(nxt, o, c);
                    ctxs[i].ContinuesNext = closes || ctxs[i].ContinuesNext == true;
                    if (closes)
                        ctxs[i + 1].ContinuedFromPrev = true;
                }
            }
        }
        for (var i = 0; i < vis.Count; i++)
        {
            if (ctxs[i].ContinuedFromPrev == null && (StrayClose(vis[i], '[', ']') || StrayClose(vis[i], '(', ')')))
                ctxs[i].ContinuedFromPrev = false;
        }
        return (dn, ctxs);
    }

    /// <summary>Counts in first-seen order (Python's Counter keeps insertion order, and some messages depend on it).</summary>
    private sealed class OrderedCounter
    {
        private readonly Dictionary<string, int> _counts = [];
        private readonly List<string> _order = [];

        public void Add(string key, int n = 1)
        {
            if (!_counts.ContainsKey(key))
            {
                _order.Add(key);
                _counts[key] = 0;
            }
            _counts[key] += n;
        }

        public int this[string key] => _counts.GetValueOrDefault(key);

        public int Total => _counts.Values.Sum();

        public IEnumerable<(string Key, int Count)> Items => _order.Select(k => (k, _counts[k]));

        public bool IsEmpty => _order.Count == 0;

        public static OrderedCounter Of(IEnumerable<string> keys)
        {
            var c = new OrderedCounter();
            foreach (var k in keys)
                c.Add(k);
            return c;
        }

        /// <summary>Counter subtraction: what is left in this one, positive counts only, in this one's order.</summary>
        public OrderedCounter Minus(OrderedCounter other)
        {
            var c = new OrderedCounter();
            foreach (var (k, n) in Items)
            {
                var left = n - other[k];
                if (left > 0)
                    c.Add(k, left);
            }
            return c;
        }

        public bool SameAs(OrderedCounter other) =>
            _counts.Count == other._counts.Count && _counts.All(kv => other._counts.TryGetValue(kv.Key, out var n) && n == kv.Value);
    }

    // ================================================================ documents

    public static CleanResult CleanDoc(SubtitleDoc doc, CleanOptions opts, SubtitleDoc? plain = null)
    {
        var (dn, ctxs) = AnalyseDoc(doc);
        var results = doc.Cues.Select((c, i) => CleanCue(c.Text, opts, doc.Fmt, ctxs[i])).ToList();
        if (plain != null && plain.Cues.Count > 0 && CrossCheck(doc, results, plain))
            dn.Notes.Add("Checked against the regular English subtitles in the same file.");
        var st = new CleanStats { Total = results.Count };
        foreach (var r in results)
        {
            if (!r.Changed)
                continue;
            if (r.Text == null)
                st.Removed++;
            else
                st.Edited++;
            if (r.Risk >= 2)
                st.Risky++;
            else if (r.Risk == 1)
                st.Glance++;
            foreach (var rule in r.Rules)
                st.ByRule[rule] = st.ByRule.GetValueOrDefault(rule) + 1;
        }
        return new CleanResult(doc, results, st, dn);
    }

    // ================================================================ answer-key check

    private static readonly Regex Tok = new(@"[^\W_]+(?:'[^\W_]+)*");
    private static readonly Regex PlainSdhLike = new(@"\[[^\]]+\]|\([A-Z ]{4,}\)");
    private static readonly Regex PlainNotes = new(@"\[[^\]]*\]|\([^a-z)]*\)");
    private static readonly Regex Capitalised = new(@"\b[A-Z][\w'’]*");

    private static List<string> Toks(string s) =>
        Tok.Matches(s.ToLowerInvariant().Replace("’", "'").Replace("‘", "'")).Select(m => m.Value).ToList();

    /// <summary>Flag removals the plain (non-SDH) track of the same video disagrees with. False when the plain
    /// track is useless as a reference (it has SDH content itself).</summary>
    public static bool CrossCheck(SubtitleDoc doc, List<CueResult> results, SubtitleDoc plain)
    {
        var sdhLike = plain.Cues.Count(c => PlainSdhLike.IsMatch(c.Text));
        if (sdhLike > 0.02 * plain.Cues.Count)
            return false;
        var pts = plain.Cues.Select(c => (S: SubFormats.ToSeconds(c.Start), E: SubFormats.ToSeconds(c.End),
                                         W: OrderedCounter.Of(Toks(PlainNotes.Replace(VisibleText(c.Text, plain.Fmt), " "))))).ToList();
        // Python sorts (start, end, Counter) tuples: two lines with the same times and different words can't be
        // ordered, and the check gives up (TypeError), so do the same
        var sorted = pts.OrderBy(p => p.S).ThenBy(p => p.E).ToList();
        for (var k = 1; k < sorted.Count; k++)
        {
            if (sorted[k].S == sorted[k - 1].S && sorted[k].E == sorted[k - 1].E && !sorted[k].W.SameAs(sorted[k - 1].W))
                return false;
        }
        pts = sorted;
        var starts = pts.Select(p => p.S).ToList();
        for (var ci = 0; ci < doc.Cues.Count; ci++)
        {
            var c = doc.Cues[ci];
            var r = results[ci];
            if (!r.Changed)
                continue;
            var rawVis = VisibleText(c.Text, doc.Fmt);
            var cleanedToks = r.Text == null ? [] : Toks(VisibleText(r.Text, doc.Fmt));
            var removed = OrderedCounter.Of(Toks(rawVis)).Minus(OrderedCounter.Of(cleanedToks));
            if (removed.IsEmpty)
                continue;
            var cln = OrderedCounter.Of(cleanedToks);
            double s = SubFormats.ToSeconds(c.Start), e = SubFormats.ToSeconds(c.End);
            var j = LowerBound(starts, s - 10);
            var win = new OrderedCounter();
            while (j < pts.Count && pts[j].S < e + 0.25)
            {
                if (pts[j].E > s - 0.25)
                {
                    foreach (var (k, n) in pts[j].W.Items)
                        win.Add(k, n);
                }
                j++;
            }
            if (win.IsEmpty)
                continue;
            var capitalised = Capitalised.Matches(rawVis).Select(m => m.Value.ToLowerInvariant()).ToHashSet();
            var hits = removed.Items.Where(x => Math.Min(x.Count, Math.Max(0, win[x.Key] - cln[x.Key])) > 0
                                                && !capitalised.Contains(x.Key) && !Py.IsDigits(x.Key))
                .Select(x => x.Key).ToList();
            if (hits.Count >= 2)
            {
                r.Risk = 2;
                var msg = "the regular English subtitles have “" + string.Join(" ", hits.Take(4)) + "” here";
                if (!r.Why.Contains(msg))
                    r.Why.Add(msg);
            }
        }
        return true;
    }

    private static int LowerBound(List<double> a, double x)
    {
        int lo = 0, hi = a.Count;
        while (lo < hi)
        {
            var mid = (lo + hi) / 2;
            if (a[mid] < x)
                lo = mid + 1;
            else
                hi = mid;
        }
        return lo;
    }

    // ================================================================ preview helpers

    private static readonly Regex AssHardSpace = new(@"\\h");

    /// <summary>What a viewer would actually read (tags removed).</summary>
    public static string VisibleText(string text, string fmt = "srt")
    {
        var t = (fmt == "ass" ? TagReAss : TagReText).Replace(text, "");
        if (fmt == "ass")
            t = t.Replace("\\h", " ").Replace("\\n", " ");
        return Py.Strip(t);
    }
}
