using System.Text;
using System.Text.RegularExpressions;

namespace SubScrub.Engine;

/// <summary>One subtitle cue: its times as written in the file, and its text with lines joined by "\n".</summary>
public sealed class Cue
{
    public string Start { get; set; } = "";
    public string End { get; set; } = "";
    public string Text { get; set; } = "";
    /// <summary>Format-specific bits needed to write the cue back (settings, id, prefix, style, breaks).</summary>
    public Dictionary<string, object?> Extra { get; } = [];
}

/// <summary>A subtitle file: the cues plus whatever surrounds them (headers, styles, comments), so rendering puts
/// everything back byte for byte except the cues that changed. A port of subscrub/subformats.py.</summary>
public sealed class SubtitleDoc
{
    public string Fmt { get; }                                  // srt | ass | vtt
    public List<Cue> Cues { get; } = [];
    /// <summary>For ass/vtt: the file in order, each item raw text or a cue index.</summary>
    public List<(bool IsCue, string Raw, int Index)> Items { get; } = [];
    public string Newline { get; set; } = "\n";

    public SubtitleDoc(string fmt) => Fmt = fmt;

    /// <summary>The document with replacement cue texts (null = drop the cue).</summary>
    public string Render(IReadOnlyList<string?> texts)
    {
        if (texts.Count != Cues.Count)
            throw new ArgumentException("texts must match cues");
        return Fmt switch
        {
            "srt" => SubFormats.RenderSrt(this, texts),
            "vtt" => SubFormats.RenderVtt(this, texts),
            "ass" => SubFormats.RenderAss(this, texts),
            _ => throw new ArgumentException(Fmt),
        };
    }
}

public static class SubFormats
{
    static SubFormats() => Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);

    // ================================================================ reading

    /// <summary>UTF-8 (with or without a BOM), UTF-16 with a BOM, else Windows-1252, else Latin-1.</summary>
    public static string ReadTextFile(string path) => Decode(File.ReadAllBytes(path));

    public static string Decode(byte[] data)
    {
        try
        {
            var start = data.Length >= 3 && data[0] == 0xEF && data[1] == 0xBB && data[2] == 0xBF ? 3 : 0;
            return new UTF8Encoding(false, true).GetString(data, start, data.Length - start);
        }
        catch (DecoderFallbackException)
        {
        }
        if (data.Length >= 2 && (data[0] == 0xFF && data[1] == 0xFE || data[0] == 0xFE && data[1] == 0xFF))
        {
            try
            {
                var big = data[0] == 0xFE;
                return new UnicodeEncoding(big, false, true).GetString(data, 2, data.Length - 2);
            }
            catch (DecoderFallbackException)
            {
            }
        }
        try
        {
            return Encoding.GetEncoding(1252, EncoderFallback.ExceptionFallback, DecoderFallback.ExceptionFallback)
                .GetString(data);
        }
        catch (DecoderFallbackException)
        {
            return Encoding.Latin1.GetString(data);
        }
    }

    public static SubtitleDoc Load(string path, string? fmt = null)
    {
        var text = ReadTextFile(path);
        return Parse(text, fmt ?? DetectFormat(text, path));
    }

    private static readonly Regex V4Styles = new(@"^\[V4\+? Styles\]", RegexOptions.Multiline);

    public static string DetectFormat(string text, string name = "")
    {
        var lead = Py.LStrip(text);
        var head = lead.Length > 400 ? lead[..400] : lead;
        if (head.StartsWith("WEBVTT", StringComparison.Ordinal))
            return "vtt";
        if (head.Contains("[Script Info]", StringComparison.Ordinal) || V4Styles.IsMatch(text))
            return "ass";
        var low = name.ToLowerInvariant();
        if (low.EndsWith(".ass", StringComparison.Ordinal) || low.EndsWith(".ssa", StringComparison.Ordinal))
            return "ass";
        return low.EndsWith(".vtt", StringComparison.Ordinal) ? "vtt" : "srt";
    }

    public static SubtitleDoc Parse(string text, string fmt)
    {
        var newline = (text.Length > 5000 ? text[..5000] : text).Contains("\r\n", StringComparison.Ordinal) ? "\r\n" : "\n";
        text = text.Replace("\r\n", "\n").Replace("\r", "\n");
        var doc = fmt switch
        {
            "srt" => ParseSrt(text),
            "vtt" => ParseVtt(text),
            "ass" => ParseAss(text),
            _ => throw new ArgumentException($"Unsupported subtitle format: {fmt}"),
        };
        doc.Newline = newline;
        return doc;
    }

    // ================================================================ SRT

    private static readonly Regex SrtTime = new(
        @"\A\s*(\d{1,3}:\d{1,2}:\d{1,2}[,.]\d{1,3})\s*-->\s*(\d{1,3}:\d{1,2}:\d{1,2}[,.]\d{1,3})(.*)$");

    private static SubtitleDoc ParseSrt(string text)
    {
        var doc = new SubtitleDoc("srt");
        var acc = new List<List<string>>();
        foreach (var line in text.Split('\n'))
        {
            var m = SrtTime.Match(line);
            if (m.Success)
            {
                // the line just before a timing line is the cue number: drop it from the previous cue
                if (acc.Count > 0)
                {
                    var prev = acc[^1];
                    if (prev.Count > 0 && Py.IsDigits(Py.Strip(prev[^1])) && (prev.Count == 1 || Py.Strip(prev[^2]).Length == 0))
                        prev.RemoveAt(prev.Count - 1);
                }
                var cue = new Cue { Start = m.Groups[1].Value, End = m.Groups[2].Value };
                cue.Extra["settings"] = m.Groups[3].Value;
                doc.Cues.Add(cue);
                acc.Add([]);
                continue;
            }
            if (acc.Count > 0)
                acc[^1].Add(line);
        }
        for (var i = 0; i < doc.Cues.Count; i++)
            doc.Cues[i].Text = string.Join("\n", acc[i].Where(l => Py.Strip(l).Length > 0).Select(Py.RStrip));
        return doc;
    }

    internal static string RenderSrt(SubtitleDoc doc, IReadOnlyList<string?> texts)
    {
        var parts = new List<string>();
        var n = 0;
        for (var i = 0; i < doc.Cues.Count; i++)
        {
            var t = texts[i];
            if (t == null || Py.Strip(t).Length == 0)
                continue;
            n++;
            var c = doc.Cues[i];
            parts.Add($"{n}\n{c.Start} --> {c.End}{c.Extra.GetValueOrDefault("settings") as string ?? ""}\n{t}\n");
        }
        return string.Join("\n", parts).Replace("\n", doc.Newline);
    }

    // ================================================================ WebVTT

    private static readonly Regex VttTime = new(
        @"\A\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*((?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})(.*)$");
    private static readonly Regex BlankLine = new(@"\n[ \t]*\n");

    private static SubtitleDoc ParseVtt(string text)
    {
        var doc = new SubtitleDoc("vtt");
        var blocks = BlankLine.Split(text);
        for (var i = 0; i < blocks.Length; i++)
        {
            var block = blocks[i];
            if (Py.Strip(block).Length == 0)
                continue;
            var lines = block.Split('\n').ToList();
            while (lines.Count > 0 && Py.Strip(lines[0]).Length == 0)
                lines.RemoveAt(0);
            if (i == 0 || new[] { "NOTE", "STYLE", "REGION", "WEBVTT" }.Any(p => lines[0].StartsWith(p, StringComparison.Ordinal)))
            {
                doc.Items.Add((false, string.Join("\n", lines), -1));
                continue;
            }
            var tline = 0;
            if (!VttTime.IsMatch(lines[0]) && lines.Count > 1 && VttTime.IsMatch(lines[1]))
                tline = 1;
            var m = VttTime.Match(lines[tline]);
            if (!m.Success)
            {
                doc.Items.Add((false, string.Join("\n", lines), -1));
                continue;
            }
            var cue = new Cue { Start = m.Groups[1].Value, End = m.Groups[2].Value, Text = string.Join("\n", lines.Skip(tline + 1)) };
            cue.Extra["settings"] = m.Groups[3].Value;
            cue.Extra["id"] = tline == 1 ? lines[0] : null;
            doc.Items.Add((true, "", doc.Cues.Count));
            doc.Cues.Add(cue);
        }
        return doc;
    }

    internal static string RenderVtt(SubtitleDoc doc, IReadOnlyList<string?> texts)
    {
        var parts = new List<string>();
        foreach (var (isCue, raw, index) in doc.Items)
        {
            if (!isCue)
            {
                parts.Add(raw);
                continue;
            }
            var cue = doc.Cues[index];
            var t = texts[index];
            if (t == null || Py.Strip(t).Length == 0)
                continue;
            var id = cue.Extra.GetValueOrDefault("id") as string;
            var head = string.IsNullOrEmpty(id) ? "" : id + "\n";
            parts.Add($"{head}{cue.Start} --> {cue.End}{cue.Extra.GetValueOrDefault("settings") as string ?? ""}\n{t}");
        }
        return (string.Join("\n\n", parts) + "\n").Replace("\n", doc.Newline);
    }

    // ================================================================ ASS / SSA

    private static readonly Regex AssBlocks = new(@"(\{[^}]*\})");
    private static readonly Regex AssBreak = new(@"\\[Nn]");

    /// <summary>Dialogue text with \N and \n turned into newlines, plus which kind each was (breaks inside {...}
    /// override blocks are not breaks).</summary>
    private static (string Text, List<string> Kinds) AssBreaks(string raw)
    {
        var kinds = new List<string>();
        var segs = AssBlocks.Split(raw);
        var sb = new StringBuilder();
        for (var i = 0; i < segs.Length; i++)
        {
            if (i % 2 == 1)
                sb.Append(segs[i]);
            else
                sb.Append(AssBreak.Replace(segs[i], m =>
                {
                    kinds.Add(m.Value);
                    return "\n";
                }));
        }
        return (sb.ToString(), kinds);
    }

    /// <summary>The reverse: newlines back to \N / \n. If lines were removed, the remaining breaks are hard ones.</summary>
    private static string AssJoin(string text, List<string> kinds)
    {
        var parts = text.Split('\n');
        if (parts.Length - 1 != kinds.Count)
            kinds = Enumerable.Repeat("\\N", parts.Length - 1).ToList();
        var sb = new StringBuilder();
        for (var i = 0; i < parts.Length; i++)
            sb.Append(parts[i]).Append(i < kinds.Count ? kinds[i] : "");
        return sb.ToString();
    }

    private static SubtitleDoc ParseAss(string text)
    {
        var doc = new SubtitleDoc("ass");
        var section = "";
        var fields = new List<string>();
        foreach (var line in text.Split('\n'))
        {
            var stripped = Py.Strip(line);
            if (stripped.StartsWith('[') && stripped.EndsWith(']'))
            {
                section = stripped.ToLowerInvariant();
                doc.Items.Add((false, line, -1));
                continue;
            }
            if (section == "[events]")
            {
                if (stripped.ToLowerInvariant().StartsWith("format:", StringComparison.Ordinal))
                {
                    fields = stripped.Split(':', 2)[1].Split(',').Select(f => Py.Strip(f).ToLowerInvariant()).ToList();
                    doc.Items.Add((false, line, -1));
                    continue;
                }
                if (stripped.StartsWith("Dialogue:", StringComparison.Ordinal) && fields.Count > 0)
                {
                    var body = line.Split(':', 2)[1];
                    var rest = Py.LStrip(body);
                    var lead = body[..(body.Length - rest.Length)];
                    var parts = rest.Split(',', fields.Count).ToList();
                    if (parts.Count == fields.Count)
                    {
                        var rec = fields.Select((f, k) => (f, parts[k])).GroupBy(x => x.f).ToDictionary(g => g.Key, g => g.Last().Item2);
                        var prefix = "Dialogue:" + lead + string.Join(",", parts.Take(parts.Count - 1)) + ",";
                        var (bodyText, breaks) = AssBreaks(parts[^1]);
                        var cue = new Cue { Start = rec.GetValueOrDefault("start", ""), End = rec.GetValueOrDefault("end", ""), Text = bodyText };
                        cue.Extra["prefix"] = prefix;
                        cue.Extra["style"] = rec.GetValueOrDefault("style", "");
                        cue.Extra["breaks"] = breaks;
                        doc.Items.Add((true, "", doc.Cues.Count));
                        doc.Cues.Add(cue);
                        continue;
                    }
                }
            }
            doc.Items.Add((false, line, -1));
        }
        return doc;
    }

    internal static string RenderAss(SubtitleDoc doc, IReadOnlyList<string?> texts)
    {
        var parts = new List<string>();
        foreach (var (isCue, raw, index) in doc.Items)
        {
            if (!isCue)
            {
                parts.Add(raw);
                continue;
            }
            var t = texts[index];
            if (t == null)
                continue;
            var cue = doc.Cues[index];
            parts.Add((string)cue.Extra["prefix"]! + AssJoin(t, cue.Extra.GetValueOrDefault("breaks") as List<string> ?? []));
        }
        return string.Join("\n", parts).Replace("\n", doc.Newline);
    }

    // ================================================================ times

    private static readonly Regex Time = new(@"\A\s*(?:(\d+):)?(\d+):(\d+)(?:[.,](\d+))?");

    /// <summary>'01:02:03,450' / '1:02:03.45' / '02:03.450' → seconds.</summary>
    public static double ToSeconds(string? t)
    {
        var m = Time.Match(t ?? "");
        if (!m.Success)
            return 0.0;
        var h = m.Groups[1].Success ? long.Parse(m.Groups[1].Value, System.Globalization.CultureInfo.InvariantCulture) : 0;
        var mi = long.Parse(m.Groups[2].Value, System.Globalization.CultureInfo.InvariantCulture);
        var s = long.Parse(m.Groups[3].Value, System.Globalization.CultureInfo.InvariantCulture);
        var frac = m.Groups[4].Success ? m.Groups[4].Value : "0";
        return h * 3600 + mi * 60 + s + double.Parse(frac, System.Globalization.CultureInfo.InvariantCulture) / Math.Pow(10, frac.Length);
    }
}
