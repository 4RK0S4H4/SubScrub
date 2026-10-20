using System.Net;
using System.Text;
using System.Text.Json.Nodes;

namespace SubScrub.Engine;

/// <summary>The TMDB client (naming.TMDB): a v3 API key or a v4 read access token, English results, answers cached
/// for the session, three tries with a pause when rate limited.</summary>
public sealed class Tmdb : ITmdb
{
    public const string Base = "https://api.themoviedb.org/3";
    private readonly string _key;
    private readonly TimeSpan _timeout;
    private readonly Dictionary<string, JsonNode?> _cache = [];
    private static readonly HttpClient Http = new(new SocketsHttpHandler { AutomaticDecompression = DecompressionMethods.All });

    /// <summary>For the tests: (url, Authorization header) -> (status, body). Null = the real network.</summary>
    public Func<string, string?, (int Status, string Body)>? Opener { get; set; }

    /// <summary>For the tests: waits between tries.</summary>
    public Action<double> Sleep { get; set; } = s => Thread.Sleep(TimeSpan.FromSeconds(s));

    public Tmdb(string key, double timeout = 12.0)
    {
        _key = Py.Strip(key ?? "");
        _timeout = TimeSpan.FromSeconds(timeout);
    }

    /// <summary>urllib.parse.quote_plus</summary>
    public static string QuotePlus(string s)
    {
        var sb = new StringBuilder();
        foreach (var b in Encoding.UTF8.GetBytes(s))
        {
            var c = (char)b;
            if (c is >= 'A' and <= 'Z' or >= 'a' and <= 'z' or >= '0' and <= '9' or '_' or '.' or '-' or '~')
                sb.Append(c);
            else if (c == ' ')
                sb.Append('+');
            else
                sb.Append('%').Append(b.ToString("X2"));
        }
        return sb.ToString();
    }

    private static string Encode(IEnumerable<(string K, string V)> ps) => string.Join("&", ps.Select(p => QuotePlus(p.K) + "=" + QuotePlus(p.V)));

    private JsonNode? Get(string path, params (string K, object? V)[] parameters)
    {
        var ps = parameters.Where(p => p.V is not null && !(p.V is string s && s.Length == 0))
            .Select(p => (p.K, Convert.ToString(p.V, System.Globalization.CultureInfo.InvariantCulture)!)).ToList();
        if (!ps.Any(p => p.K == "language"))
            ps.Add(("language", "en-US"));
        string? auth = null;
        if (_key.Length > 40)                        // v4 "read access token"
            auth = $"Bearer {_key}";
        else                                         // v3 API key
            ps.Add(("api_key", _key));
        var url = $"{Base}{path}?{Encode(ps)}";
        var cacheKey = path + "?" + Encode(ps.Where(p => p.K != "api_key").OrderBy(p => p.K, PyFmt.Order).ThenBy(p => p.Item2, PyFmt.Order));
        lock (_cache)
        {
            if (_cache.TryGetValue(cacheKey, out var hit))
                return hit;
        }
        JsonNode? data = null;
        string? last = null;
        var ok = false;
        for (var attempt = 0; attempt < 3; attempt++)
        {
            int status;
            string body;
            try
            {
                (status, body) = Opener != null ? Opener(url, auth) : Fetch(url, auth);
            }
            catch (Exception e) when (e is HttpRequestException or TaskCanceledException or IOException or TimeoutException)
            {
                last = e.InnerException?.Message ?? e.Message;
                Sleep(0.5);
                continue;
            }
            if (status == 401)
                throw new TmdbError("TMDB didn't accept the API key (check it in Settings).");
            if (status == 404)
            {
                data = null;
                ok = true;
                break;
            }
            if (status == 429)                       // rate limited: wait and retry
            {
                last = "HTTP Error 429: Too Many Requests";
                Sleep(1.0 + attempt);
                continue;
            }
            if (status < 200 || status >= 300)
                throw new TmdbError($"TMDB answered with an error ({status}).");
            data = JsonNode.Parse(body);
            ok = true;
            break;
        }
        if (!ok)
            throw new TmdbError($"Couldn't reach TMDB ({last}).");
        lock (_cache)
            _cache[cacheKey] = data;
        return data;
    }

    private (int, string) Fetch(string url, string? auth)
    {
        using var req = new HttpRequestMessage(HttpMethod.Get, url);
        req.Headers.TryAddWithoutValidation("Accept", "application/json");
        req.Headers.TryAddWithoutValidation("User-Agent", "SubScrub");
        if (auth != null)
            req.Headers.TryAddWithoutValidation("Authorization", auth);
        using var cts = new CancellationTokenSource(_timeout);
        using var resp = Http.Send(req, cts.Token);
        using var reader = new StreamReader(resp.Content.ReadAsStream(cts.Token), Encoding.UTF8);
        return ((int)resp.StatusCode, reader.ReadToEnd());
    }

    private static List<JsonObject> Results(JsonNode? n) =>
        (n as JsonObject)?["results"] is JsonArray a ? a.OfType<JsonObject>().ToList() : [];

    public List<JsonObject> SearchMovie(string query, int? year = null) =>
        Results(Get("/search/movie", ("query", query), ("year", year), ("include_adult", "false")));

    public JsonObject Movie(int tmdbId) => Get($"/movie/{tmdbId}") as JsonObject ?? [];

    public List<JsonObject> SearchTv(string query, int? year = null) =>
        Results(Get("/search/tv", ("query", query), ("first_air_date_year", year)));

    public JsonObject Season(int tmdbId, int season) => Get($"/tv/{tmdbId}/season/{season}") as JsonObject ?? [];
}

/// <summary>A stand-in for TMDB from a few titles (subscrub/demo_tmdb.py and the tests' FakeTMDB): every word of the
/// query must start some word of a title, as TMDB's search does.</summary>
public sealed class FakeTmdb : ITmdb
{
    private readonly Dictionary<int, JsonObject> _movies = [], _shows = [];
    private readonly Dictionary<(int, int), JsonArray> _seasons = [];
    public int Calls { get; private set; }

    /// <summary>{"movies": [...], "shows": [...], "seasons": [[id, season, [episodes]]]}</summary>
    public FakeTmdb(JsonObject data)
    {
        foreach (var m in data["movies"]!.AsArray())
            _movies[m!["id"]!.GetValue<int>()] = m.AsObject();
        foreach (var m in data["shows"]!.AsArray())
            _shows[m!["id"]!.GetValue<int>()] = m.AsObject();
        foreach (var s in data["seasons"]!.AsArray())
            _seasons[(s![0]!.GetValue<int>(), s[1]!.GetValue<int>())] = s[2]!.AsArray();
    }

    private static string S(JsonObject r, string k) => r[k] is JsonValue v && v.TryGetValue<string>(out var s) ? s : "";

    private List<JsonObject> Find(Dictionary<int, JsonObject> pool, string field, string query, int? year)
    {
        Calls++;
        var q = Naming.NormTokens(query);
        return pool.Values.Where(r =>
        {
            var t = Naming.NormTokens(S(r, field));
            var date = S(r, "release_date");
            if (date.Length == 0)
                date = S(r, "first_air_date");
            return q.All(w => t.Any(x => x.StartsWith(w, StringComparison.Ordinal)))
                   && (year == null || date.StartsWith(year.Value.ToString(System.Globalization.CultureInfo.InvariantCulture), StringComparison.Ordinal));
        }).Select(r => (JsonObject)r.DeepClone()).ToList();
    }

    public List<JsonObject> SearchMovie(string query, int? year = null) => Find(_movies, "title", query, year);

    public JsonObject Movie(int tmdbId) => _movies.TryGetValue(tmdbId, out var m) ? (JsonObject)m.DeepClone() : [];

    public List<JsonObject> SearchTv(string query, int? year = null) => Find(_shows, "name", query, year);

    public JsonObject Season(int tmdbId, int season) =>
        new() { ["episodes"] = _seasons.TryGetValue((tmdbId, season), out var e) ? e.DeepClone() : new JsonArray() };

    /// <summary>The sample library's titles (subscrub/demo_tmdb.py), for screenshots and the scripted QA run.</summary>
    public static FakeTmdb Demo() => new(JsonNode.Parse(DemoJson)!.AsObject());

    public const string DemoJson = """
    {"movies": [
      {"id": 693134, "title": "Dune: Part Two", "original_title": "Dune: Part Two", "release_date": "2024-02-27", "popularity": 95, "runtime": 167},
      {"id": 78, "title": "Blade Runner", "original_title": "Blade Runner", "release_date": "1982-06-25", "popularity": 60, "runtime": 117}],
     "shows": [
      {"id": 63639, "name": "The Expanse", "original_name": "The Expanse", "first_air_date": "2015-12-14", "popularity": 60},
      {"id": 95396, "name": "Severance", "original_name": "Severance", "first_air_date": "2022-02-17", "popularity": 80},
      {"id": 96074, "name": "Slow Horses", "original_name": "Slow Horses", "first_air_date": "2022-04-01", "popularity": 50},
      {"id": 96075, "name": "Slow Horses: Inside", "original_name": "Slow Horses: Inside", "first_air_date": "2022-04-01", "popularity": 49},
      {"id": 231003, "name": "Planet Earth III", "original_name": "Planet Earth III", "first_air_date": "2023-10-22", "popularity": 30}],
     "seasons": [
      [63639, 1, [{"episode_number": 3, "name": "Remember the Cant", "runtime": 44}]],
      [95396, 2, [{"episode_number": 4, "name": "Woe's Hollow", "runtime": 50}]],
      [96074, 4, []], [96075, 4, []],
      [231003, 1, [{"episode_number": 2, "name": "Ocean", "runtime": 58}]]]}
    """;
}
