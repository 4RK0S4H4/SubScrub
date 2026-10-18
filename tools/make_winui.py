"""Regenerate the WinUI app's generated files from the Python app's sources, so both look the same:

  * winui/SubScrub/Themes/Tokens.xaml   the S4 Glass colours for both themes, from subscrub.ui.theme.tokens()
  * winui/SubScrub/Controls/SolarData.g.cs   the Solar icons in subscrub.ui.solar, as XAML path data
  * winui/SubScrub/Assets/Fonts/*.ttf    the bundled Inter (subscrub.ui.fonts) with Inter's open digits (ss01) and
                                          flat-top 3 (cv09) made the default glyphs. XAML can't switch stylistic
                                          sets on, so they are baked in. Each weight is its own family
                                          ("SubScrub Inter SemiBold"), so XAML never fakes a bold.

    python tools/make_winui.py

Commit the results. Edit the colours in theme.py and the icon lists in tools/make_solar.py, not the outputs.
"""
from __future__ import annotations

import base64
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
APP = ROOT / "winui" / "SubScrub"

from subscrub.ui import fonts, solar, theme  # noqa: E402

# ============================================================================ colours

# token name in theme.tokens() -> XAML key (S4 names in PascalCase)
TOKENS = ["bg", "glass", "glass2", "stroke", "bg2", "bg3", "well", "bar_bg", "field_bg", "modal_bg", "modal_line",
          "scrim", "pop_bg", "text", "text2", "text3", "border", "border2", "mute_bg", "hov", "press", "ok", "warn",
          "bad", "ok_bg", "warn_bg", "bad_bg", "danger_fill", "btn", "btn_fg", "btn_hover", "btn_edge", "btn_press",
          "btn_disabled", "acc", "accent_text", "accent_ink", "badge_accent", "accent_soft", "vivid", "nav1", "nav2",
          "nav3", "tile_on", "seg_track", "seg_line", "card_shadow", "shadow_deep", "backlight", "seg_glow",
          "nav_glow", "tip_bg", "tip_line", "fill", "fill2", "soft_ring", "bg_hover", "bg_sel", "focus",
          "field_line", "field_focus_line", "field_focus_bg", "selection", "scroll", "scroll_hover", "mute_line",
          "bar_solid", "card_solid", "screen", "screen_line", "removed", "removed_bg", "trim", "accent_light"]


def pascal(name: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in name.split("_"))


def argb(c) -> str:
    r, g, b, a = theme._rgba(c)
    return "#{:02X}{:02X}{:02X}{:02X}".format(round(a * 255), *(max(0, min(255, round(x))) for x in (r, g, b)))


def theme_block(key: str, t: dict) -> list[str]:
    out = [f'        <ResourceDictionary x:Key="{key}">']
    for name in TOKENS:
        out.append(f'            <Color x:Key="S4{pascal(name)}Color">{argb(t[name])}</Color>')
    out.append(f'            <Color x:Key="S4AccentColor">{argb(t["accent_base"])}</Color>')
    for name, c in extra_colours(t).items():
        out.append(f'            <Color x:Key="S4{name}Color">{argb(c)}</Color>')
    stops = t["page"]
    for i, (_, c) in enumerate(stops):
        out.append(f'            <Color x:Key="S4Page{i}Color">{argb(c)}</Color>')
    blooms = t["blooms"] or [(1.0, -0.1, 1400, 900, "rgba(0,0,0,0)"), (-0.1, 1.15, 1200, 800, "rgba(0,0,0,0)")]
    for i, (_, _, _, _, c) in enumerate(blooms):
        out.append(f'            <Color x:Key="S4Bloom{i}Color">{argb(c)}</Color>')
        out.append(f'            <Color x:Key="S4Bloom{i}EdgeColor">{argb(theme.alpha(c, 0))}</Color>')
    out.append("")
    for name in TOKENS + ["accent", "TipText", "Clear", "KnobOff", "White"]:
        p = pascal(name)
        out.append(f'            <SolidColorBrush x:Key="S4{p}Brush" Color="{{StaticResource S4{p}Color}}"/>')
    # the page: CSS linear-gradient(165deg, ...): mostly top to bottom, leaning right
    out.append('            <LinearGradientBrush x:Key="S4PageBrush" StartPoint="0.37,0" EndPoint="0.63,1">')
    for i, (pos, _) in enumerate(stops):
        out.append(f'                <GradientStop Offset="{pos}" Color="{{StaticResource S4Page{i}Color}}"/>')
    out.append("            </LinearGradientBrush>")
    # the lit top edge of a card or dialog: the white highlight fading into the hairline
    out.append('            <LinearGradientBrush x:Key="S4CardEdgeBrush" StartPoint="0,0" EndPoint="0,1">')
    out.append('                <GradientStop Offset="0" Color="{StaticResource S4StrokeColor}"/>')
    out.append('                <GradientStop Offset="0.12" Color="{StaticResource S4BorderColor}"/>')
    out.append("            </LinearGradientBrush>")
    # the selected nav item: accent to accent-2, left to right
    out.append('            <LinearGradientBrush x:Key="S4NavBrush" StartPoint="0,0.5" EndPoint="1,0.5">')
    for i, n in enumerate(("nav1", "nav2", "nav3")):
        out.append(f'                <GradientStop Offset="{i / 2}" Color="{{StaticResource S4{pascal(n)}Color}}"/>')
    out.append("            </LinearGradientBrush>")
    # dialogs: in-app acrylic (a real GPU blur of the window behind), tinted like the S4 modal glass
    r, g, b, a = theme._rgba(t["modal_bg"])
    tint = theme.css((r, g, b, 1.0))
    out.append(f'            <AcrylicBrush x:Key="S4ModalBrush" TintColor="{argb(tint)}" TintOpacity="{round(a, 2)}"'
               f' TintLuminosityOpacity="{0.85 if t["is_dark"] else 0.9}"'
               f' FallbackColor="{argb(theme.over(t["modal_bg"], t["bg"]))}"/>')
    out.append("")
    out.append("            <!-- the Fluent controls SubScrub doesn't template itself: tooltips, menus, scroll bars -->")
    for key, token in ALIASES.items():
        out.append(f'            <SolidColorBrush x:Key="{key}" Color="{argb(t[token] if token in t else extra_colours(t)[token])}"/>')
    out.append("        </ResourceDictionary>")
    return out


# Fluent theme keys (lightweight styling) -> S4 token
ALIASES = {
    "ToolTipBackground": "tip_bg", "ToolTipForeground": "TipText", "ToolTipBorderBrush": "tip_line",
    "MenuFlyoutPresenterBackground": "pop_bg", "MenuFlyoutPresenterBorderBrush": "border2",
    "MenuFlyoutItemForeground": "text", "MenuFlyoutItemForegroundPointerOver": "text",
    "MenuFlyoutItemForegroundPressed": "text", "MenuFlyoutItemBackgroundPointerOver": "hov",
    "MenuFlyoutItemBackgroundPressed": "press", "MenuFlyoutSeparatorBackground": "border",
    "FlyoutPresenterBackground": "pop_bg", "FlyoutBorderThemeBrush": "border2",
    "ScrollBarThumbFill": "scroll", "ScrollBarThumbFillPointerOver": "scroll_hover",
    "ScrollBarThumbFillPressed": "scroll_hover", "ScrollBarPanningThumbBackground": "scroll",
    "FocusVisualPrimaryBrush": "focus", "FocusVisualSecondaryBrush": "Clear",
    "TextControlSelectionHighlightColor": "selection",
}


def extra_colours(t: dict) -> dict:
    """Colours the WinUI app needs on top of theme.tokens()."""
    glow = t["backlight"] if t["is_dark"] else theme.alpha(t["accent_base"], .30)
    return {
        "PrimaryGlow": glow, "PrimaryGlowEdge": theme.alpha(glow, 0),
        "TipText": t["text"] if t["is_dark"] else t["bg"],
        "Clear": "rgba(0,0,0,0)",
        "KnobOff": t["text3"],
        "White": "#ffffff",
    }


def make_tokens() -> str:
    lines = ['<!-- Generated by tools/make_winui.py from subscrub/ui/theme.py tokens(). Don\'t edit by hand. -->',
             '<ResourceDictionary',
             '    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"',
             '    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">',
             "    <ResourceDictionary.ThemeDictionaries>"]
    lines += theme_block("Light", theme.LIGHT)
    lines += theme_block("Dark", theme.DARK)
    lines += ["    </ResourceDictionary.ThemeDictionaries>", "",
              "    <!-- the bloom sizes and places (fractions of the window, radii in pixels) -->"]
    for i, (x, y, rx, ry, _) in enumerate(theme.DARK["blooms"]):
        lines.append(f'    <x:String x:Key="S4Bloom{i}Geometry">{x},{y},{rx},{ry}</x:String>')
    lines += ["</ResourceDictionary>", ""]
    return "\n".join(lines)


# ============================================================================ icons

_ARGS = {"m": 2, "l": 2, "h": 1, "v": 1, "c": 6, "s": 4, "q": 4, "t": 2, "a": 7, "z": 0}
_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def normalise_path(d: str) -> str:
    """SVG path data -> XAML path data with every number separated (SVG allows '1.5.5' and packed arc flags)."""
    out, i, cmd = [], 0, None
    while i < len(d):
        ch = d[i]
        if ch.isspace() or ch == ",":
            i += 1
            continue
        if ch.isalpha():
            cmd = ch
            out.append(ch)
            i += 1
            if ch.lower() == "z":
                cmd = None
            continue
        if cmd is None:
            raise ValueError(f"number without a command in {d[:40]}")
        n = _ARGS[cmd.lower()]
        args = []
        for k in range(n):
            while i < len(d) and (d[i].isspace() or d[i] == ","):
                i += 1
            if cmd.lower() == "a" and k in (3, 4):          # arc flags: one digit each, maybe packed
                args.append(d[i])
                i += 1
                continue
            m = _NUM.match(d, i)
            if not m:
                raise ValueError(f"bad number at {d[i:i + 20]!r}")
            args.append(m.group(0))
            i = m.end()
        out.append(" ".join(args))
        if cmd == "m":                                    # extra pairs after a move are line-tos
            cmd = "l"
        elif cmd == "M":
            cmd = "L"
    return " ".join(out)


def circle_path(cx: float, cy: float, r: float) -> str:
    return f"M {cx - r} {cy} A {r} {r} 0 1 1 {cx + r} {cy} A {r} {r} 0 1 1 {cx - r} {cy} Z"


def _attrs(tag: str) -> dict:
    return dict(re.findall(r'([\w-]+)="([^"]*)"', tag))


def icon_parts(body: str) -> list[tuple]:
    """(data, filled, stroke width, opacity) for every shape, with the <g> attributes applied."""
    parts, stack = [], [{}]
    for m in re.finditer(r"<(/?)(\w+)([^>]*?)(/?)>", body):
        closing, tag, rest, selfclose = m.groups()
        if closing:
            stack.pop()
            continue
        a = {**stack[-1], **_attrs(rest)}
        if tag == "g":
            stack.append(a)
            continue
        if tag == "path":
            data = normalise_path(a["d"])
        elif tag == "circle":
            data = circle_path(float(a["cx"]), float(a["cy"]), float(a["r"]))
        else:
            raise ValueError(tag)
        evenodd = a.get("fill-rule") == "evenodd"
        filled = a.get("fill", "currentColor") != "none"
        stroked = a.get("stroke", "none") != "none"
        sw = float(a.get("stroke-width", "1")) if stroked else 0.0
        op = float(a.get("opacity", "1"))
        parts.append((("F0 " if evenodd else "F1 ") + data, filled, sw, op))
        if not selfclose:
            stack.append(a)
    return parts


def make_icons() -> str:
    out = ["// Generated by tools/make_winui.py from subscrub/ui/solar.py (Solar icons, 480 Design, CC BY 4.0).",
           "// Don't edit by hand: add names to tools/make_solar.py and run both scripts.",
           "namespace SubScrub.Controls;", "",
           "internal static class SolarData", "{",
           "    // (path data, filled, stroke width (0 = no stroke), opacity) on a 24x24 canvas",
           "    public static readonly Dictionary<string, (string Data, bool Fill, double Stroke, double Opacity)[]>"
           " Linear = new()", "    {"]

    def emit(d):
        for name, body in d.items():
            items = ", ".join(f'("{p}", {str(f).lower()}, {sw}, {op})' for p, f, sw, op in icon_parts(body))
            out.append(f'        ["{name}"] = [{items}],')

    emit(solar.LINEAR)
    out += ["    };", "",
            "    public static readonly Dictionary<string, (string Data, bool Fill, double Stroke, double Opacity)[]>"
            " Duotone = new()", "    {"]
    emit(solar.DUOTONE)
    out += ["    };", "}", ""]
    return "\n".join(out)


# ============================================================================ fonts

WEIGHTS = {"regular": "Regular", "medium": "Medium", "semibold": "SemiBold", "bold": "Bold", "italic": "Italic"}


def _single_map(gsub, tag: str) -> dict:
    m: dict = {}
    for fr in gsub.FeatureList.FeatureRecord:
        if fr.FeatureTag == tag:
            for li in fr.Feature.LookupListIndex:
                for st in gsub.LookupList.Lookup[li].SubTable:
                    m.update(st.mapping)
    return m


def make_font(key: str, label: str) -> bytes:
    from fontTools.ttLib import TTFont
    f = TTFont(io.BytesIO(base64.b64decode("".join(fonts.DATA[key]))))
    gsub = f["GSUB"].table
    ss01, cv09 = _single_map(gsub, "ss01"), _single_map(gsub, "cv09")

    def frozen(g):
        return ss01.get(cv09.get(g, g), cv09.get(g, g))

    for table in f["cmap"].tables:
        table.cmap = {u: frozen(g) for u, g in table.cmap.items()}
    # tabular figures still have to reach the open shapes: teach tnum the new defaults (four.ss01 -> four.ss01.tf)
    for fr in gsub.FeatureList.FeatureRecord:
        if fr.FeatureTag != "tnum":
            continue
        for li in fr.Feature.LookupListIndex:
            for st in gsub.LookupList.Lookup[li].SubTable:
                for g, t in list(st.mapping.items()):
                    if frozen(g) != g and frozen(g) not in st.mapping:
                        st.mapping[frozen(g)] = frozen(t)
    family = f"SubScrub Inter {label}"
    style = "Italic" if key == "italic" else "Regular"
    name = f["name"]
    name.names = [n for n in name.names if n.nameID in (0, 3, 5) or n.nameID >= 256]
    for nid, val in ((1, family), (2, style), (4, f"{family} {style}" if style != "Regular" else family),
                     (6, family.replace(" ", "") + "-" + style)):
        name.setName(val, nid, 3, 1, 0x409)
    if "STAT" in f:
        del f["STAT"]
    buf = io.BytesIO()
    f.save(buf)
    return buf.getvalue()


def main():
    (APP / "Themes").mkdir(parents=True, exist_ok=True)
    (APP / "Themes" / "Tokens.xaml").write_text(make_tokens(), encoding="utf-8")
    (APP / "Controls").mkdir(parents=True, exist_ok=True)
    (APP / "Controls" / "SolarData.g.cs").write_text(make_icons(), encoding="utf-8")
    fdir = APP / "Assets" / "Fonts"
    fdir.mkdir(parents=True, exist_ok=True)
    for key, label in WEIGHTS.items():
        (fdir / f"Inter-{label}.ttf").write_bytes(make_font(key, label))
    print(f"Wrote Tokens.xaml, SolarData.g.cs and {len(WEIGHTS)} fonts under {APP}")


if __name__ == "__main__":
    main()
