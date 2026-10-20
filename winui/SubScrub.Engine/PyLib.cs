using System.Globalization;
using System.Numerics;
using System.Text;

namespace SubScrub.Engine;

/// <summary>Python's formatting, sorting and path rules where the engine's results depend on them ("{n:,}",
/// round(), sorted(), os.path, pathlib), so the C# engine shows exactly what the Python one shows.</summary>
public static class PyFmt
{
    private static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

    /// <summary>f"{n:,}"</summary>
    public static string Thousands(long n) => n.ToString("#,0", Inv);

    /// <summary>f"{n:,} word(s)"</summary>
    public static string Plural(long n, string word, string? many = null) =>
        $"{Thousands(n)} {(n == 1 ? word : many ?? word + "s")}";

    /// <summary>round(x, digits) on a float: the decimal value of x rounded half to even, back as the nearest double.</summary>
    public static double Round(double x, int digits)
    {
        if (double.IsNaN(x) || double.IsInfinity(x))
            return x;
        var q = RoundedUnits(x, digits);
        return digits >= 0 ? (double)q / Math.Pow(10, digits) : (double)q * Math.Pow(10, -digits);
    }

    /// <summary>f"{x:.{digits}f}": the exact value of x rounded half to even.</summary>
    public static string Fixed(double x, int digits)
    {
        var q = RoundedUnits(x, digits);
        var neg = q.Sign < 0 || (q.IsZero && (x < 0 || double.IsNegative(x)));
        var s = BigInteger.Abs(q).ToString(Inv).PadLeft(digits + 1, '0');
        var body = digits > 0 ? s[..^digits] + "." + s[^digits..] : s;
        return (neg ? "-" : "") + body;
    }

    /// <summary>x * 10^digits rounded half to even, computed exactly.</summary>
    private static BigInteger RoundedUnits(double x, int digits)
    {
        long bits = BitConverter.DoubleToInt64Bits(x);
        var neg = bits < 0;
        var exp = (int)((bits >> 52) & 0x7FF);
        var man = bits & 0xFFFFFFFFFFFFFL;
        if (exp == 0)
            exp++;
        else
            man |= 1L << 52;
        exp -= 1075;
        // |x| = man * 2^exp; scaled = |x| * 10^digits = num / den
        BigInteger num = man, den = BigInteger.One;
        if (exp > 0)
            num <<= exp;
        else
            den <<= -exp;
        if (digits >= 0)
            num *= BigInteger.Pow(10, digits);
        else
            den *= BigInteger.Pow(10, -digits);
        var q = BigInteger.DivRem(num, den, out var rem);
        var twice = rem * 2;
        if (twice > den || (twice == den && !q.IsEven))
            q += 1;
        return neg ? -q : q;
    }

    /// <summary>str.upper() of the first character: text[:1].upper() + text[1:]</summary>
    public static string Cap(string text)
    {
        if (string.IsNullOrEmpty(text))
            return text;
        var first = char.IsHighSurrogate(text[0]) && text.Length > 1 ? text[..2] : text[..1];
        return first.ToUpperInvariant() + text[first.Length..];
    }

    public static string Lower(string s) => s.ToLowerInvariant();

    /// <summary>Python's string order (by code point, not UTF-16 unit).</summary>
    public static readonly IComparer<string> Order = Comparer<string>.Create(CompareCodePoints);

    public static int CompareCodePoints(string? a, string? b)
    {
        if (ReferenceEquals(a, b))
            return 0;
        if (a == null)
            return -1;
        if (b == null)
            return 1;
        var n = Math.Min(a.Length, b.Length);
        for (var i = 0; i < n; i++)
        {
            char x = a[i], y = b[i];
            if (x == y)
                continue;
            // surrogates (U+D800..U+DFFF) stand for code points above U+FFFF, so they sort after U+E000..U+FFFF
            int fx = x >= 0xD800 && x <= 0xDFFF ? x + 0x2000 : (x >= 0xE000 ? x - 0x800 : x);
            int fy = y >= 0xD800 && y <= 0xDFFF ? y + 0x2000 : (y >= 0xE000 ? y - 0x800 : y);
            return fx.CompareTo(fy);
        }
        return a.Length.CompareTo(b.Length);
    }

    /// <summary>Code points of a string (Python indexes strings by code point).</summary>
    public static int[] CodePoints(string s)
    {
        var list = new List<int>(s.Length);
        for (var i = 0; i < s.Length; i++)
        {
            if (char.IsHighSurrogate(s[i]) && i + 1 < s.Length && char.IsLowSurrogate(s[i + 1]))
            {
                list.Add(char.ConvertToUtf32(s[i], s[i + 1]));
                i++;
            }
            else
                list.Add(s[i]);
        }
        return list.ToArray();
    }

    public static string FromCodePoints(int[] cps, int start, int end)
    {
        var sb = new StringBuilder(end - start);
        for (var i = start; i < end; i++)
        {
            if (cps[i] > 0xFFFF)
                sb.Append(char.ConvertFromUtf32(cps[i]));
            else
                sb.Append((char)cps[i]);
        }
        return sb.ToString();
    }

    /// <summary>s[:n], counting code points.</summary>
    public static string Head(string s, int n)
    {
        if (n <= 0)
            return "";
        var cps = CodePoints(s);
        return cps.Length <= n ? s : FromCodePoints(cps, 0, n);
    }

    /// <summary>len(s) in code points.</summary>
    public static int Len(string s) => CodePoints(s).Length;
}

/// <summary>os.path and pathlib, as Python on this system does them (Windows rules on Windows).</summary>
public static class PyPath
{
    public static readonly bool IsWin = OperatingSystem.IsWindows();

    /// <summary>os.path.normcase</summary>
    public static string NormCase(string p) => IsWin ? p.Replace('/', '\\').ToLowerInvariant() : p;

    /// <summary>os.path.abspath</summary>
    public static string Abs(string p) => Path.GetFullPath(p);

    public static string Key(string p) => NormCase(Abs(p));

    public static bool Same(string? a, string? b) => a != null && b != null && NormCase(a) == NormCase(b);

    /// <summary>os.path.basename / Path.name</summary>
    public static string Name(string p)
    {
        var i = p.LastIndexOfAny(IsWin ? ['\\', '/'] : ['/']);
        var name = i >= 0 ? p[(i + 1)..] : p;
        if (IsWin && i < 0 && name.Length >= 2 && name[1] == ':')
            name = name[2..];
        return name;
    }

    /// <summary>os.path.dirname</summary>
    public static string Dir(string p)
    {
        var i = p.LastIndexOfAny(IsWin ? ['\\', '/'] : ['/']);
        if (i < 0)
            return IsWin && p.Length >= 2 && p[1] == ':' ? p[..2] : "";
        var head = p[..(i + 1)];
        var trimmed = head.TrimEnd(IsWin ? ['\\', '/'] : ['/']);
        if (trimmed.Length == 0 || (IsWin && trimmed.Length == 2 && trimmed[1] == ':'))
            return head;
        return trimmed;
    }

    /// <summary>pathlib's suffix: the last ".ext" of the name ("" for ".hidden" or "name.").</summary>
    public static string Suffix(string p)
    {
        var name = Name(p);
        var i = name.LastIndexOf('.');
        return i > 0 && i < name.Length - 1 ? name[i..] : "";
    }

    /// <summary>pathlib's stem.</summary>
    public static string Stem(string p)
    {
        var name = Name(p);
        var i = name.LastIndexOf('.');
        return i > 0 && i < name.Length - 1 ? name[..i] : name;
    }

    /// <summary>Path.with_name</summary>
    public static string WithName(string p, string name) => Join(Dir(p), name);

    /// <summary>Path.with_suffix</summary>
    public static string WithSuffix(string p, string suffix) => WithName(p, Stem(p) + suffix);

    public static string Join(string dir, string name)
    {
        if (dir.Length == 0)
            return name;
        var sep = IsWin ? '\\' : '/';
        return dir.EndsWith(sep) || (IsWin && dir.EndsWith('/')) ? dir + name : dir + sep + name;
    }

    /// <summary>Entries of a folder, as os.scandir / Path.iterdir list them (unsorted).</summary>
    public static List<string> List(string dir)
    {
        var d = dir.Length == 0 ? "." : dir;
        var prefix = dir.Length == 0 ? "" : dir;
        return Directory.EnumerateFileSystemEntries(d).Select(e => prefix.Length == 0 ? Name(e) : Join(prefix, Name(e))).ToList();
    }

    /// <summary>sorted() of paths in one folder (pathlib compares the names; Windows ignores case).</summary>
    public static List<string> Sorted(IEnumerable<string> paths) =>
        paths.OrderBy(p => IsWin ? p.ToLowerInvariant() : p, PyFmt.Order).ToList();
}

/// <summary>difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() on code points.</summary>
public static class SequenceMatcher
{
    public static List<(string Tag, int I1, int I2, int J1, int J2)> Opcodes(int[] a, int[] b)
    {
        var b2j = new Dictionary<int, List<int>>();
        for (var j = 0; j < b.Length; j++)
        {
            if (!b2j.TryGetValue(b[j], out var list))
                b2j[b[j]] = list = [];
            list.Add(j);
        }

        (int, int, int) Longest(int alo, int ahi, int blo, int bhi)
        {
            int besti = alo, bestj = blo, bestsize = 0;
            var j2len = new Dictionary<int, int>();
            for (var i = alo; i < ahi; i++)
            {
                var newj2len = new Dictionary<int, int>();
                if (b2j.TryGetValue(a[i], out var js))
                {
                    foreach (var j in js)
                    {
                        if (j < blo)
                            continue;
                        if (j >= bhi)
                            break;
                        var k = newj2len[j] = j2len.GetValueOrDefault(j - 1) + 1;
                        if (k > bestsize)
                        {
                            besti = i - k + 1;
                            bestj = j - k + 1;
                            bestsize = k;
                        }
                    }
                }
                j2len = newj2len;
            }
            while (besti > alo && bestj > blo && a[besti - 1] == b[bestj - 1])
            {
                besti--;
                bestj--;
                bestsize++;
            }
            while (besti + bestsize < ahi && bestj + bestsize < bhi && a[besti + bestsize] == b[bestj + bestsize])
                bestsize++;
            return (besti, bestj, bestsize);
        }

        var queue = new List<(int, int, int, int)> { (0, a.Length, 0, b.Length) };
        var blocks = new List<(int I, int J, int K)>();
        while (queue.Count > 0)
        {
            var (alo, ahi, blo, bhi) = queue[^1];
            queue.RemoveAt(queue.Count - 1);
            var (i, j, k) = Longest(alo, ahi, blo, bhi);
            if (k > 0)
            {
                blocks.Add((i, j, k));
                if (alo < i && blo < j)
                    queue.Add((alo, i, blo, j));
                if (i + k < ahi && j + k < bhi)
                    queue.Add((i + k, ahi, j + k, bhi));
            }
        }
        blocks.Sort();
        int i1 = 0, j1 = 0, k1 = 0;
        var nonAdjacent = new List<(int I, int J, int K)>();
        foreach (var (i2, j2, k2) in blocks)
        {
            if (i1 + k1 == i2 && j1 + k1 == j2)
                k1 += k2;
            else
            {
                if (k1 > 0)
                    nonAdjacent.Add((i1, j1, k1));
                (i1, j1, k1) = (i2, j2, k2);
            }
        }
        if (k1 > 0)
            nonAdjacent.Add((i1, j1, k1));
        nonAdjacent.Add((a.Length, b.Length, 0));

        var ops = new List<(string, int, int, int, int)>();
        int ai = 0, bj = 0;
        foreach (var (ai2, bj2, size) in nonAdjacent)
        {
            var tag = "";
            if (ai < ai2 && bj < bj2)
                tag = "replace";
            else if (ai < ai2)
                tag = "delete";
            else if (bj < bj2)
                tag = "insert";
            if (tag.Length > 0)
                ops.Add((tag, ai, ai2, bj, bj2));
            ai = ai2 + size;
            bj = bj2 + size;
            if (size > 0)
                ops.Add(("equal", ai2, ai, bj2, bj));
        }
        return ops;
    }
}
