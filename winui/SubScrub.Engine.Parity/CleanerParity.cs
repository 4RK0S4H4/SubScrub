using System.Text.Json;
using System.Text.Json.Nodes;
using SubScrub.Engine;

// cleaner <cases.json> <out.json>
//   cases: {"cues": [{"text", "opts", "fmt", "ctx"}], "docs": [{"name", "content", "fmt", "opts", "plain"}]}
//   out:   the same shape as the Python side writes (the retired tools/cleaner_parity.py), one result per case
internal static class CleanerParity
{
    public static void Run(string casesPath, string outPath)
    {
        var cases = JsonNode.Parse(File.ReadAllText(casesPath))!;
        var output = new JsonObject();

        static Dictionary<string, bool> Opts(JsonNode? n) =>
            n?.AsObject().ToDictionary(kv => kv.Key, kv => kv.Value!.GetValue<bool>()) ?? [];

        static JsonObject Cue(CueResult r) => new()
        {
            ["text"] = r.Text, ["changed"] = r.Changed,
            ["rules"] = new JsonArray(r.Rules.Order(StringComparer.Ordinal).Select(x => (JsonNode)x!).ToArray()),
            ["risk"] = r.Risk, ["why"] = new JsonArray(r.Why.Select(x => (JsonNode)x!).ToArray()),
        };

        var cueOut = new JsonArray();
        foreach (var c in cases["cues"]!.AsArray())
        {
            var ctxNode = c!["ctx"];
            CueCtx? ctx = null;
            if (ctxNode != null)
            {
                ctx = new CueCtx
                {
                    ContinuesNext = ctxNode["continues_next"]?.GetValue<bool>(),
                    ContinuedFromPrev = ctxNode["continued_from_prev"]?.GetValue<bool>(),
                    MixedNames = ctxNode["mixed_names"]?.AsObject().ToDictionary(kv => kv.Key, kv => kv.Value!.GetValue<int>()),
                    OcrNotes = ctxNode["ocr_notes"]?.GetValue<string>(),
                    OcrSuspect = ctxNode["ocr_suspect"]?.GetValue<bool>() ?? false,
                    QuoteVoices = ctxNode["quote_voices"]?.GetValue<bool>() ?? false,
                };
            }
            cueOut.Add(Cue(Cleaner.CleanCue(c["text"]!.GetValue<string>(), CleanOptions.FromDict(Opts(c["opts"])),
                                            c["fmt"]?.GetValue<string>() ?? "srt", ctx)));
        }
        output["cues"] = cueOut;

        var docOut = new JsonArray();
        foreach (var d in cases["docs"]!.AsArray())
        {
            var fmt = d!["fmt"]!.GetValue<string>();
            var doc = SubFormats.Parse(d["content"]!.GetValue<string>(), fmt);
            var plain = d["plain"] is { } p ? SubFormats.Parse(p.GetValue<string>(), d["plain_fmt"]?.GetValue<string>() ?? "srt") : null;
            var res = Cleaner.CleanDoc(doc, CleanOptions.FromDict(Opts(d["opts"])), plain);
            var notes = new JsonObject
            {
                ["mixed_names"] = new JsonObject(res.Notes.MixedNames.OrderBy(kv => kv.Key, StringComparer.Ordinal)
                    .Select(kv => KeyValuePair.Create(kv.Key, (JsonNode?)kv.Value))),
                ["quote_voices"] = res.Notes.QuoteVoices, ["ocr_notes"] = res.Notes.OcrNotes, ["ocr_suspect"] = res.Notes.OcrSuspect,
                ["notes"] = new JsonArray(res.Notes.Notes.Select(x => (JsonNode)x!).ToArray()),
            };
            var st = res.Stats;
            docOut.Add(new JsonObject
            {
                ["name"] = d["name"]!.GetValue<string>(),
                ["cues"] = new JsonArray(doc.Cues.Select(c => (JsonNode)new JsonObject
                {
                    ["start"] = c.Start, ["end"] = c.End, ["text"] = c.Text, ["seconds"] = SubFormats.ToSeconds(c.Start),
                }).ToArray()),
                ["results"] = new JsonArray(res.Results.Select(r => (JsonNode)Cue(r)).ToArray()),
                ["notes"] = notes,
                ["stats"] = new JsonObject
                {
                    ["total"] = st.Total, ["removed"] = st.Removed, ["edited"] = st.Edited, ["risky"] = st.Risky, ["glance"] = st.Glance,
                    ["by_rule"] = new JsonObject(st.ByRule.OrderBy(kv => kv.Key, StringComparer.Ordinal)
                        .Select(kv => KeyValuePair.Create(kv.Key, (JsonNode?)kv.Value))),
                },
                ["confidence"] = res.Confidence,
                ["render"] = res.Render(),
                ["roundtrip"] = doc.Render(doc.Cues.Select(c => (string?)c.Text).ToList()),
            });
        }
        output["docs"] = docOut;
        output["decode"] = new JsonArray((cases["decode"]?.AsArray() ?? []).Select(c =>
            (JsonNode)SubFormats.Decode(Convert.FromBase64String(c!["b64"]!.GetValue<string>()))!).ToArray());
        File.WriteAllText(outPath, output.ToJsonString(new JsonSerializerOptions
        {
            Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        }));
    }
}
