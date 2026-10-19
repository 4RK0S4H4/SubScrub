using System.Text.RegularExpressions;

namespace SubScrub.Engine;

/// <summary>Python's string semantics where the port depends on them (str.strip, str.split(), isdigit…), so the
/// C# engine gives exactly the Python engine's results.</summary>
internal static class Py
{
    /// <summary>What Python's str.isspace() calls whitespace.</summary>
    public static bool IsSpace(char c) => c is (>= (char)0x09 and <= (char)0x0D) or (>= (char)0x1C and <= (char)0x20)
        or (char)0x85 or (char)0xA0 or (char)0x1680 or (>= (char)0x2000 and <= (char)0x200A) or (char)0x2028
        or (char)0x2029 or (char)0x202F or (char)0x205F or (char)0x3000;

    public static string Strip(string s) => RStrip(LStrip(s));

    public static string LStrip(string s)
    {
        var i = 0;
        while (i < s.Length && IsSpace(s[i]))
            i++;
        return s[i..];
    }

    public static string RStrip(string s)
    {
        var j = s.Length;
        while (j > 0 && IsSpace(s[j - 1]))
            j--;
        return s[..j];
    }

    /// <summary>str.strip(chars)</summary>
    public static string Strip(string s, string chars) => s.Trim(chars.ToCharArray());

    /// <summary>str.split() with no arguments: runs of whitespace, no empty parts.</summary>
    public static List<string> Words(string s)
    {
        var words = new List<string>();
        var start = -1;
        for (var i = 0; i <= s.Length; i++)
        {
            var space = i == s.Length || IsSpace(s[i]);
            if (!space && start < 0)
                start = i;
            else if (space && start >= 0)
            {
                words.Add(s[start..i]);
                start = -1;
            }
        }
        return words;
    }

    public static bool IsDigits(string s) => s.Length > 0 && s.All(char.IsDigit);

    public static bool IsAlpha(char c) => char.IsLetter(c);

    /// <summary>Anchored like re.match.</summary>
    public static Regex Anchored(string pattern, RegexOptions options = RegexOptions.None) =>
        new(@"\G(?:" + pattern + ")", options);
}
