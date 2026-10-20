using System.IO.Compression;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using SubScrub.Engine;

// golden <tests/golden> <repository> [--update]
//   Checks the C# engine against the answers the Python engine gave before it was retired (tools/freeze_golden.py,
//   issue #44): the cleaner and subtitle formats (cleaner.json.br), and the file list, Review, the save questions,
//   reading, saving, title matching and the commands end to end (engine.json.br). Needs the generated sample videos
//   (python -m tests.make_samples, --library and --edge) under <repository>/samples.
//   --update writes what the C# engine gives as the new answers: only for a rule change made on purpose, and the
//   summary of what changed belongs in the pull request.
internal static class GoldenParity
{
    private static readonly string[] Disposition =
        ["default", "forced", "hearing_impaired", "visual_impaired", "original", "comment", "dub", "lyrics", "karaoke"];

    public static int Run(string goldenDir, string repo, bool update)
    {
        var tmp = Directory.CreateTempSubdirectory("subscrub_golden_").FullName;
        try
        {
            var bad = Cleaner(Path.Combine(goldenDir, "cleaner.json.br"), tmp, update);
            bad += Engine(Path.Combine(goldenDir, "engine.json.br"), repo, Path.Combine(tmp, "engine"), update);
            return bad == 0 ? 0 : 1;
        }
        finally
        {
            try
            {
                Directory.Delete(tmp, true);
            }
            catch (Exception)
            {
            }
        }
    }

    // ================================================================ the two files

    private static int Cleaner(string file, string tmp, bool update)
    {
        var golden = Load(file);
        var cases = Path.Combine(tmp, "cleaner-cases.json");
        var outp = Path.Combine(tmp, "cleaner-out.json");
        File.WriteAllText(cases, golden["cases"]!.ToJsonString(Session.JsonOut));
        CleanerParity.Run(cases, outp);
        var got = JsonNode.Parse(File.ReadAllText(outp))!;
        var exp = golden["expected"]!;
        var docs = exp["docs"]!.AsArray();
        var lines = docs.Sum(d => d!["results"]!.AsArray().Count) + exp["cues"]!.AsArray().Count;
        Console.WriteLine($"Cleaner: {golden["cases"]!["cues"]!.AsArray().Count} recorded cue calls, {docs.Count} documents "
                          + $"({lines:N0} lines in all), {exp["decode"]!.AsArray().Count} decodings");
        return Compare("cleaner", golden, exp, got, file, update, "The C# cleaner gives exactly the frozen answers.");
    }

    private static int Engine(string file, string repo, string root, bool update)
    {
        var golden = Load(file);
        Directory.CreateDirectory(root);
        Materialize(golden["layout"]!, Path.Combine(repo, "samples"), root);
        var casesText = golden["cases"]!.ToJsonString(Session.JsonOut).Replace("<root>", Escaped(root));
        var cases = Path.Combine(root, "golden-cases.json");
        var outp = Path.Combine(root, "golden-out.json");
        File.WriteAllText(cases, casesText);
        Environment.SetEnvironmentVariable("LOCALAPPDATA", Path.Combine(root, "cmd-cs", "appdata"));
        Environment.SetEnvironmentVariable("XDG_DATA_HOME", Path.Combine(root, "xdg"));
        SessionParity.Run(cases, outp);
        var got = JsonNode.Parse(File.ReadAllText(outp))!.AsObject();
        got["commands"] = NormalizeCommands(got["commands"]!, Path.Combine(root, "cmd-cs"));
        var text = got.ToJsonString(Session.JsonOut).Replace(Escaped(root), "<root>");
        var result = Project(JsonNode.Parse(text));
        var exp = golden["expected"]!;
        var parsed = JsonNode.Parse(casesText)!;
        Console.WriteLine($"Engine: {parsed["sessions"]!.AsArray().Count} sessions, "
                          + $"{exp["sessions"]!.AsArray().Sum(s => s!.AsArray().Count):N0} views and actions, "
                          + $"{parsed["reading"]!["adds"]!.AsArray().Count} fresh reads, {parsed["reading"]!["probe"]!.AsArray().Count} videos probed, "
                          + $"{parsed["saving"]!.AsArray().Count} saves, {exp["naming"]!["match"]!.AsArray().Count:N0} title matches, "
                          + $"{exp["commands"]!.AsArray().Count} commands end to end");
        return Compare("engine", golden, exp, result, file, update,
                       "The C# engine reads, lists, reviews, asks and saves exactly as the frozen answers say.");
    }

    private static int Compare(string what, JsonNode golden, JsonNode exp, JsonNode? got, string file, bool update, string ok)
    {
        var problems = new List<string>();
        Diff(exp, got, "", problems);
        if (problems.Count == 0)
        {
            Console.WriteLine(ok);
            return 0;
        }
        foreach (var p in problems.Take(40))
            Console.WriteLine("  DIFF " + p);
        if (Environment.GetEnvironmentVariable("GOLDEN_DIFFS") is { Length: > 0 } all)
            File.WriteAllLines($"{all}.{what}.txt", problems);       // every difference, for reading them all
        // where they are, with list positions folded together, so a large but uniform change reads at a glance
        Console.WriteLine("  by place:");
        foreach (var g in problems.GroupBy(p => Regex.Replace(p[..Math.Max(0, p.IndexOf(':'))], @"\[\d+\]", "[*]"))
                     .OrderByDescending(g => g.Count()).Take(25))
            Console.WriteLine($"    {g.Count(),6}  {g.Key}");
        if (update)
        {
            golden["expected"] = got!.DeepClone();
            Save(file, golden);
            Console.WriteLine($"{problems.Count} differences in the {what}: the golden file now has the C# engine's answers.");
            return 0;
        }
        Console.WriteLine($"{problems.Count} differences between the C# {what} and the frozen answers");
        return 1;
    }

    // ================================================================ files

    private static JsonNode Load(string file)
    {
        using var input = new BrotliStream(File.OpenRead(file), CompressionMode.Decompress);
        return JsonNode.Parse(input)!;
    }

    private static void Save(string file, JsonNode data)
    {
        var raw = Encoding.UTF8.GetBytes(data.ToJsonString(Session.JsonOut));
        using var enc = new BrotliEncoder(11, 24);
        var buf = new byte[BrotliEncoder.GetMaxCompressedLength(raw.Length)];
        enc.Compress(raw, buf, out _, out var written, isFinalBlock: true);
        File.WriteAllBytes(file, buf[..written]);
    }

    private static string Escaped(string s) => JsonValue.Create(s)!.ToJsonString(Session.JsonOut)[1..^1];

    /// <summary>The folders the Python run left, rebuilt: sample videos copied by name from samples/, small files
    /// from the golden file, and a file of the right size where only the name matters.</summary>
    private static void Materialize(JsonNode layout, string samples, string root)
    {
        Directory.CreateDirectory(Path.Combine(root, "extract"));
        foreach (var d in layout["dirs"]!.AsArray())
            Directory.CreateDirectory(Path.Combine(root, d!.GetValue<string>()));
        var missing = new List<string>();
        foreach (var (rel, e) in layout["files"]!.AsObject())
        {
            var dest = Path.Combine(root, rel);
            Directory.CreateDirectory(Path.GetDirectoryName(dest)!);
            if (e!["from"] is JsonValue from)
            {
                var src = Path.Combine(samples, from.GetValue<string>());
                if (!File.Exists(src))
                {
                    missing.Add(from.GetValue<string>());
                    continue;
                }
                File.Copy(src, dest, true);
            }
            else if (e["b64"] is JsonValue b64)
                File.WriteAllBytes(dest, Convert.FromBase64String(b64.GetValue<string>()));
            else
            {
                using var f = File.Create(dest);
                f.SetLength(e["size"]!.GetValue<long>());
            }
        }
        if (missing.Count > 0)
            throw new FileNotFoundException("make the sample videos first (python -m tests.make_samples, --library, --edge): missing "
                                            + string.Join(", ", missing.Distinct().Take(5)));
    }

    // ================================================================ made the same on both sides

    /// <summary>What doesn't depend on a video's exact bytes or the FFmpeg version (tools/freeze_golden.py's project).</summary>
    private static JsonNode? Project(JsonNode? x)
    {
        if (x is JsonObject o)
        {
            var info = o.ContainsKey("streams") && o.ContainsKey("subs") && o.ContainsKey("format_name");
            var outp = new JsonObject();
            foreach (var (k, v) in o)
            {
                if (info && k == "streams" && v is JsonArray streams)
                    outp[k] = new JsonArray(streams.Select(s => s is JsonObject so
                        ? new JsonArray(so["codec_type"]?.DeepClone(), so["codec_name"]?.DeepClone()) : s?.DeepClone()).ToArray());
                else if (info && k == "size")
                    continue;
                else if (info && k == "duration" && v is JsonValue dv && dv.TryGetValue<double>(out var dur))
                    outp[k] = PyFmt.Round(dur, 1);
                else if (k == "disposition" && v is JsonObject disp)
                    outp[k] = new JsonObject(Disposition.Where(disp.ContainsKey).Select(d => KeyValuePair.Create(d, disp[d]?.DeepClone())));
                else if (k == "tools" && v is JsonObject t && t.ContainsKey("ffprobe"))
                    continue;
                else
                    outp[k] = Project(v);
            }
            return outp;
        }
        if (x is JsonArray a)
            return new JsonArray(a.Select(Project).ToArray());
        return x?.DeepClone();
    }

    /// <summary>tools/engine_parity.py's normalize_commands: file numbers by their place, the folder as &lt;dir&gt;,
    /// times masked, no tool paths.</summary>
    private static JsonNode NormalizeCommands(JsonNode got, string folder)
    {
        var text = got.ToJsonString(Session.JsonOut).Replace(Escaped(folder), "<dir>");
        text = Regex.Replace(text, @"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", "<time>");
        var data = JsonNode.Parse(text)!;
        var uids = new Dictionary<long, int>();
        int Num(long u) => uids.TryGetValue(u, out var n) ? n : uids[u] = uids.Count;

        void Walk(JsonNode? x)
        {
            if (x is JsonObject o)
            {
                foreach (var k in o.Select(kv => kv.Key).ToList())
                {
                    var v = o[k];
                    if (k is "uid" or "review_uid" && v is JsonValue uv && uv.TryGetValue<long>(out var u))
                        o[k] = Num(u);
                    else if (k == "uids" && v is JsonArray ua)
                        o[k] = new JsonArray(ua.Select(n => (JsonNode)Num(n!.GetValue<long>())).ToArray());
                    else if (k == "tools" && v is JsonObject t && t.ContainsKey("ffmpeg"))
                        o[k] = new JsonObject { ["ready"] = t["ready"]?.DeepClone() };
                    else if (k == "version")
                        o[k] = "<version>";
                    else
                        Walk(v);
                }
            }
            else if (x is JsonArray a)
            {
                foreach (var v in a)
                    Walk(v);
            }
        }

        Walk(data);
        return data;
    }

    /// <summary>tools/engine_parity.py's diff: true is not 1, 3 is 3.0, keys in sorted order.</summary>
    private static void Diff(JsonNode? a, JsonNode? b, string path, List<string> outp)
    {
        bool IsBool(JsonNode? n) => n is JsonValue v && v.GetValueKind() is JsonValueKind.True or JsonValueKind.False;
        if (IsBool(a) || IsBool(b))
        {
            if (!(IsBool(a) && IsBool(b) && a!.GetValue<bool>() == b!.GetValue<bool>()))
                outp.Add($"{path}: frozen {Show(a)} / C# {Show(b)}");
            return;
        }
        if (a is JsonObject oa && b is JsonObject ob)
        {
            foreach (var k in oa.Select(kv => kv.Key).Union(ob.Select(kv => kv.Key)).Order(StringComparer.Ordinal))
            {
                if (!oa.ContainsKey(k) || !ob.ContainsKey(k))
                    outp.Add($"{path}.{k}: only in {(oa.ContainsKey(k) ? "the frozen answers" : "C#")}");
                else
                    Diff(oa[k], ob[k], $"{path}.{k}", outp);
            }
            return;
        }
        if (a is JsonArray la && b is JsonArray lb)
        {
            if (la.Count != lb.Count)
            {
                outp.Add($"{path}: {la.Count} items frozen, {lb.Count} in C#");
                return;
            }
            for (var i = 0; i < la.Count; i++)
                Diff(la[i], lb[i], $"{path}[{i}]", outp);
            return;
        }
        if (!Same(a, b))
            outp.Add($"{path}: frozen {Show(a)} / C# {Show(b)}");
    }

    private static bool Same(JsonNode? a, JsonNode? b)
    {
        if (a == null || b == null)
            return a == null && b == null;
        if (a is JsonValue va && b is JsonValue vb)
        {
            if (va.GetValueKind() == JsonValueKind.Number && vb.GetValueKind() == JsonValueKind.Number)
                return va.GetValue<double>() == vb.GetValue<double>();
            if (va.GetValueKind() == JsonValueKind.String && vb.GetValueKind() == JsonValueKind.String)
                return va.GetValue<string>() == vb.GetValue<string>();
        }
        return false;
    }

    private static string Show(JsonNode? n)
    {
        var s = n?.ToJsonString(Session.JsonOut) ?? "null";
        return s.Length > 160 ? s[..160] + "…" : s;
    }
}
