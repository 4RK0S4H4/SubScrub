using System.Globalization;
using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace SubScrub.Engine;

public static partial class Naming
{
    // ================================================================ parsing

    // words that mark the end of the title in a release name
    private const string Junk = @"
        \d{3,4}p | 4k | uhd | 2160 | 1080 | 720 | 480 | hdr\d*\+? | hdr10plus | dv | dovi | dolby | vision | sdr | hlg |
        [xh]\.?26[45] | hevc | avc | av1 | xvid | divx | vc-?1 | mpeg-?2 | 10-?bit | 8-?bit | hi10p? |
        web(?:-?dl|-?rip)? | blu-?ray | bd(?:rip|25|50|66|100)? | br-?rip | remux | dvd(?:rip|r|5|9)? | hd-?tv | hdrip |
        pdtv | uhd-?bd | hddvd | cam | ts | telesync | screener | scr |
        ddp?(?:\d(?:\.\d)?)? | dd\+ | e-?ac-?3 | ac-?3 | aac\d*(?:\.\d)? | dts(?:-?hd|-?x|-?ma)? | truehd | atmos | flac |
        opus | mp3 | lpcm | pcm | \d\.\d | 2ch | 6ch | 8ch |
        proper | repack | rerip | internal | limited | dubbed | subbed | multi | dual | dual-?audio | multisub |
        amzn | nf | dsnp | hmax | atvp | pcok | pmtp | hulu | stan | crav |
        extended | unrated | uncut | remastered | imax | criterion | directors? | theatrical | hybrid | complete
    ";
    private const RegexOptions I = RegexOptions.IgnoreCase | RegexOptions.CultureInvariant;
    private static readonly Regex JunkRe = new(@"\A(?:" + Junk + @")\z", I | RegexOptions.IgnorePatternWhitespace);
    // "S01E02", "Show_S01E02" (an underscore is a word character, so no \b), "S2024E01" (a year as the season)
    private static readonly Regex Sxe = new(@"(?<![A-Za-z0-9])S(\d{1,4})[ ._-]*E(\d{1,3})((?:[ ._-]*-?[ ._-]*E\d{1,3})*)", I);
    private static readonly Regex Nxn = new(@"(?<![\d])(\d{1,2})x(\d{2,3})(?![\d])", I);
    private static readonly Regex Long = new(@"(?<![A-Za-z0-9])(?:Season|Series)[ ._-]*(\d{1,4})[ ._,-]*Episode[ ._-]*(\d{1,3})", I);
    private static readonly Regex YearRe = new(@"\A\(?(19\d\d|20\d\d)\)?\z");
    private static readonly Regex YearMatch = new(@"\A\(?(19\d\d|20\d\d)\)?");

    private static Match? PyMatchYear(string w)
    {
        // _YEAR.match: anchored at the start, and "$" may sit before a final newline
        var m = YearMatch.Match(w);
        if (!m.Success)
            return null;
        var rest = w[m.Length..];
        return rest.Length == 0 || rest == "\n" ? m : null;
    }

    private static int Int(string s) => int.Parse(s, NumberStyles.None, CultureInfo.InvariantCulture);

    private static List<string> Words(string text)
    {
        text = Regex.Replace(text, @"\[\s*((?:19|20)\d\d)\s*\]", "($1)");           // "Movie [2020]" is a year, not a tag
        text = Regex.Replace(text, @"\[[^\]]*\]|\{[^}]*\}", " ");                  // [release group], {tags}
        text = Regex.Replace(text, @"[._]+(?=[-–—][._\s])", " ");                   // Mission.Impossible.-.Fallout
        text = Regex.Replace(text, @"(?<=[-–—])[._]+(?=\w)", " ");
        text = Regex.Replace(text, @"(?<=[\w)\]])[._]+(?=[\w(\[])|(?<=\w)[._]+(?=[)\]])", " ");   // Green.Lantern.(2020).S01
        text = text.Replace("_", " ");
        return Regex.Split(text, @"\s+").Where(w => w.Length > 0).ToList();
    }

    private const string Bracketish = "()[]{}-–—,.";

    /// <summary>Title words up to the first junk word, and the release year if one comes right after the title.</summary>
    private static (List<string> Words, int? Year) UntilJunk(List<string> words, bool allowYear = true)
    {
        var outList = new List<string>();
        int? year = null;
        var thisYear = DateTime.Now.Year;
        foreach (var w in words)
        {
            var bare = w.Trim(Bracketish.ToCharArray());
            if (allowYear && outList.Count > 0 && PyMatchYear(w.Trim('.', ',', '-')) is { } ym)
            {
                var y = Int(ym.Groups[1].Value);
                if (y >= 1900 && y <= thisYear + 1)
                {
                    year = y;                            // keep going: "Blade Runner 2049 (2017)" -> 2017 wins
                    outList.Add(w);
                    continue;
                }
            }
            if (bare.Length > 0 && JunkRe.IsMatch(bare))
                break;
            if (Regex.IsMatch(w, @"\A\(.*\)\z") && Regex.Split(w.Trim('(', ')'), "[ ,]+").Any(x => JunkRe.IsMatch(x)))
                break;                                   // "(1080p x265 EDGE2020)"
            if (w.StartsWith('(') && outList.Count > 0 && JunkRe.IsMatch(w.Trim('(', ')')))
                break;
            if (Regex.IsMatch(w, @"\A(?:-[A-Za-z0-9]+)\z") && outList.Count > 0)    // -GROUP at the end
                break;
            outList.Add(w);
        }
        if (year != null)
        {
            // the year is the last year-like word; drop it (and anything after it) from the title
            for (var k = outList.Count - 1; k >= 0; k--)
            {
                var m = PyMatchYear(outList[k].Trim('.', ',', '-'));
                if (m != null && Int(m.Groups[1].Value) == year)
                {
                    outList = outList[..k];
                    break;
                }
            }
        }
        return (outList, year);
    }

    private static string Tidy(List<string> words)
    {
        var t = string.Join(" ", words);
        t = Regex.Replace(t, @"\s*\(\s*\)\s*", " ");
        t = Regex.Replace(t, @"\s+-\s*$|^\s*-\s+", "");
        t = Regex.Replace(t, @"\s{2,}", " ").Trim(" -–—_.,".ToCharArray());
        return t;
    }

    public static Parsed Parse(string filename)
    {
        var stem = PyPath.Stem(filename);
        var m = Sxe.Match(stem);
        var which = Sxe;
        if (!m.Success)
        {
            m = Nxn.Match(stem);
            which = Nxn;
        }
        if (!m.Success)
        {
            m = Long.Match(stem);
            which = Long;
        }
        if (m.Success)
        {
            int season = Int(m.Groups[1].Value), first = Int(m.Groups[2].Value);
            var eps = new List<int> { first };
            if (which == Sxe && m.Groups[3].Value.Length > 0)
            {
                var g3 = m.Groups[3].Value;
                var more = Regex.Matches(g3, @"E(\d{1,3})", I).Select(x => Int(x.Groups[1].Value)).ToList();
                if (more.Count > 0 && g3.Contains('-') && more.Count == 1 && more[0] > first)
                    eps = Enumerable.Range(first, more[0] - first + 1).ToList();      // S01E01-E03
                else
                    eps.AddRange(more.Where(e => !eps.Contains(e)).ToList());     // checked against [first] only, as in Python
            }
            string before = stem[..m.Index], after = stem[(m.Index + m.Length)..];
            var (words, year) = UntilJunk(Words(before));
            var title = Tidy(words);
            var (epWords, _) = UntilJunk(Words(after.TrimStart(' ', '.', '_', '-')), false);
            return new Parsed { Title = title, Year = year, Season = season, Episodes = eps, EpisodeTitle = Tidy(epWords) };
        }
        var (w2, y2) = UntilJunk(Words(stem));
        return new Parsed { Title = Tidy(w2), Year = y2 };
    }

    // ================================================================ matching

    public static List<string> NormTokens(string text)
    {
        var sb = new StringBuilder();
        foreach (var c in text.Normalize(NormalizationForm.FormKD))
        {
            if (c < 0x80)
                sb.Append(c);
        }
        var t = sb.ToString().ToLowerInvariant().Replace("&", " and ").Replace("'", "");
        var outList = new List<string>();
        foreach (Match m in Regex.Matches(t, "[a-z0-9]+"))
        {
            var w = m.Value;
            if (w == "vs")
                w = "v";
            if (w is "the" or "a" or "an" && outList.Count == 0)
                continue;                                 // leading article
            outList.Add(w);
        }
        return outList;
    }

    /// <summary>0..1. Generous with truncated words ('Super' ~ 'Superman') and missing leading articles.</summary>
    public static double TitleSimilarity(string query, string candidate)
    {
        List<string> q = NormTokens(query), c = NormTokens(candidate);
        if (q.Count == 0 || c.Count == 0)
            return 0.0;
        if (q.SequenceEqual(c))
            return 1.0;
        var hits = 0.0;
        var used = new HashSet<int>();
        foreach (var w in q)
        {
            var best = 0.0;
            int? bi = null;
            for (var i = 0; i < c.Count; i++)
            {
                if (used.Contains(i))
                    continue;
                var x = c[i];
                double s;
                if (w == x)
                    s = 1.0;
                else if (w.Length >= 3 && x.StartsWith(w, StringComparison.Ordinal))
                    s = 0.85;
                else if (w.Length >= 4 && SequenceMatcher.Ratio(PyFmt.CodePoints(w), PyFmt.CodePoints(x)) >= 0.8)
                    s = 0.7;
                else
                    s = 0.0;
                if (s > best)
                {
                    best = s;
                    bi = i;
                }
            }
            if (bi != null)
                used.Add(bi.Value);
            hits += best;
        }
        var precision = hits / q.Count;
        var recall = hits / c.Count;
        var seq = SequenceMatcher.Ratio(PyFmt.CodePoints(string.Join(" ", q)), PyFmt.CodePoints(string.Join(" ", c)));
        return PyFmt.Round(0.45 * precision + 0.35 * recall + 0.2 * seq, 4);
    }

    private static int? YearOf(string? date)
    {
        var m = Regex.Match(date ?? "", @"\A(\d{4})");
        return m.Success ? Int(m.Groups[1].Value) : null;
    }

    /// <summary>The title, then shorter versions of it (release names often add junk TMDB doesn't know).</summary>
    private static List<string> Queries(string title)
    {
        var words = Py.Words(title);
        var outList = new List<string> { title };
        for (var n = words.Count - 1; n > 1; n--)
            outList.Add(string.Join(" ", words.Take(n)));
        if (title.Contains(" - "))
            outList.Insert(1, title.Replace(" - ", " "));
        return outList.Where(q => Py.Strip(q).Length > 0).Distinct().ToList();
    }

    public const double High = 0.82;     // score a match needs to be applied without asking
    public const double Margin = 0.08;   // ...and how far ahead of the runner-up it must be

    private static string S(JsonObject r, string k) => r[k] is JsonValue v && v.TryGetValue<string>(out var s) ? s : "";

    private static int Id(JsonObject r) => r["id"]!.GetValue<int>();

    private static double Num(JsonNode? n) => n is JsonValue v && v.TryGetValue<double>(out var d) ? d : 0.0;

    private static string Pct(double x) => PyFmt.Fixed(x * 100, 0) + "%";

    public static TitleMatch MatchTitle(ITmdb api, string filename, double durationSec = 0.0)
    {
        var p = Parse(filename);
        if (p.Title.Length == 0)
            return new TitleMatch { Parsed = p, Note = "No title could be read from the file name." };
        return p.IsEpisode ? MatchEpisode(api, p) : MatchMovie(api, p, durationSec);
    }

    private static void SortByScore(List<Choice> cands)
    {
        var sorted = cands.Select((c, k) => (c, k)).OrderBy(x => -x.c.Score).ThenBy(x => x.k).Select(x => x.c).ToList();
        cands.Clear();
        cands.AddRange(sorted);
    }

    private static TitleMatch MatchMovie(ITmdb api, Parsed p, double durationSec)
    {
        var seen = new List<(int Id, JsonObject R)>();
        void Add(IEnumerable<JsonObject> rs)
        {
            foreach (var r in rs)
            {
                if (!seen.Any(x => x.Id == Id(r)))
                    seen.Add((Id(r), r));
            }
        }
        foreach (var q in Queries(p.Title).Take(5))
        {
            Add(api.SearchMovie(q, p.Year));
            if (p.Year != null)
                Add(api.SearchMovie(q));
            var good = seen.Where(x => Math.Max(TitleSimilarity(p.Title, S(x.R, "title")), TitleSimilarity(p.Title, S(x.R, "original_title"))) >= 0.75);
            if (good.Any())
                break;
        }
        var cands = new List<Choice>();
        foreach (var (_, r) in seen)
        {
            var sim = Math.Max(TitleSimilarity(p.Title, S(r, "title")), TitleSimilarity(p.Title, S(r, "original_title")));
            var y = YearOf(S(r, "release_date"));
            var title = S(r, "title");
            var c = new Choice { Kind = "movie", TmdbId = Id(r), Title = title.Length > 0 ? title : S(r, "original_title"), Year = y, Score = sim };
            c.Why.Add($"title {Pct(sim)}");
            if (p.Year is { } py && py != 0 && y is { } yy && yy != 0)
            {
                if (yy == py)
                {
                    c.Score += 0.12;
                    c.Why.Add("year matches");
                }
                else if (Math.Abs(yy - py) == 1)
                    c.Score += 0.04;
                else
                {
                    c.Score -= 0.25;
                    c.Why.Add($"year {yy} vs {py}");
                }
            }
            c.Score += Math.Min(0.03, Num(r["popularity"]) / 2000);           // tie-breaker only
            cands.Add(c);
        }
        SortByScore(cands);
        // runtime: the strongest evidence there is, so check it for the leading few
        var minutes = durationSec != 0 ? durationSec / 60 : 0;
        if (minutes != 0)
        {
            foreach (var c in cands.Take(4))
            {
                var info = api.Movie(c.TmdbId);
                var rt = Num(info["runtime"]);
                c.Runtime = rt != 0 ? (int)rt : null;
                if (c.Runtime is { } run)
                {
                    var diff = Math.Abs(run - minutes);
                    if (diff <= 4)
                    {
                        c.Score += 0.12;
                        c.Why.Add("length matches");
                    }
                    else if (diff >= 15)
                    {
                        c.Score -= 0.15;
                        c.Why.Add($"length {run} min vs {PyFmt.Fixed(minutes, 0)}");
                    }
                }
            }
            SortByScore(cands);
        }
        if (cands.Count > 6)
            cands = cands[..6];
        if (cands.Count == 0)
            return new TitleMatch { Parsed = p, Note = $"TMDB has nothing called “{p.Title}”." };
        var lead = cands[0].Score - (cands.Count > 1 ? cands[1].Score : 0);
        return new TitleMatch { Parsed = p, Choices = cands, Confident = cands[0].Score >= High && lead >= Margin };
    }

    private static TitleMatch MatchEpisode(ITmdb api, Parsed p)
    {
        var seen = new List<(int Id, JsonObject R)>();
        foreach (var q in Queries(p.Title).Take(5))
        {
            foreach (var r in api.SearchTv(q))
            {
                if (!seen.Any(x => x.Id == Id(r)))
                    seen.Add((Id(r), r));
            }
            if (seen.Any(x => TitleSimilarity(p.Title, S(x.R, "name")) >= 0.75))
                break;
        }
        var shows = new List<(double Score, double Sim, JsonObject R, int? Y, int K)>();
        foreach (var (_, r) in seen)
        {
            var sim = Math.Max(TitleSimilarity(p.Title, S(r, "name")), TitleSimilarity(p.Title, S(r, "original_name")));
            var y = YearOf(S(r, "first_air_date"));
            var score = sim + Math.Min(0.03, Num(r["popularity"]) / 2000);
            if (p.Year is { } py && py != 0 && y is { } yy && yy != 0)
                score += yy == py ? 0.12 : (Math.Abs(yy - py) > 1 ? -0.2 : 0);
            shows.Add((score, sim, r, y, shows.Count));
        }
        shows = shows.OrderBy(s => -s.Score).ThenBy(s => s.K).ToList();
        var cands = new List<Choice>();
        foreach (var (score, sim, r, y, _) in shows.Take(4))
        {
            var season = api.Season(Id(r), p.Season!.Value);
            var eps = new Dictionary<int, JsonObject>();
            foreach (var e in season["episodes"] as JsonArray ?? [])
            {
                if (e is JsonObject eo && eo["episode_number"] is JsonValue nv && nv.TryGetValue<int>(out var num))
                    eps[num] = eo;
            }
            var found = p.Episodes.Where(eps.ContainsKey).Select(n => eps[n]).ToList();
            var c = new Choice
            {
                Kind = "episode", TmdbId = Id(r), Title = S(r, "name"), Year = y, Season = p.Season, Episodes = [.. p.Episodes],
                EpisodeTitles = found.Select(e => S(e, "name")).ToList(), Score = score,
            };
            c.Why.Add($"title {Pct(sim)}");
            if (found.Count == p.Episodes.Count)
            {
                c.Score += 0.1;
                c.Why.Add($"has {c.EpisodeCode()}");
                var rt = found.Select(e => Num(e["runtime"])).Where(x => x != 0).ToList();
                c.Runtime = rt.Count > 0 ? (int)rt.Sum() : null;
            }
            else
            {
                c.Score -= 0.3;
                c.Why.Add($"no {c.EpisodeCode()} on TMDB");
            }
            cands.Add(c);
        }
        SortByScore(cands);
        if (cands.Count == 0)
            return new TitleMatch { Parsed = p, Note = $"TMDB has no show called “{p.Title}”." };
        var lead = cands[0].Score - (cands.Count > 1 ? cands[1].Score : 0);
        var confident = cands[0].Score >= High && lead >= Margin && cands[0].EpisodeTitles.Count > 0;
        return new TitleMatch { Parsed = p, Choices = cands, Confident = confident };
    }

    /// <summary>A title the user typed: list what TMDB has, keeping the season/episode read from the file name.</summary>
    public static List<Choice> Search(ITmdb api, string text, string kind, Parsed parsed)
    {
        text = Py.Strip(text);
        if (text.Length == 0)
            return [];
        var p = new Parsed { Title = text, Season = parsed.Season, Episodes = [.. parsed.Episodes] };
        if (kind == "episode" && p.IsEpisode)
            return MatchEpisode(api, p).Choices;
        return MatchMovie(api, p, 0).Choices;
    }
}
