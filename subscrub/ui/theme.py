"""SubScrub's look: the S4 Glass design system, ported to Qt.

  * Every colour comes from one accent trio (crimson). Greys, borders and shadows carry a faint tint of it
    (S4 Glass "tinted neutrals"); `NEUTRALS = "mono"` makes them plain grey.
  * Light: an off-white page with frosted white cards. Dark: a near-black gradient with a faint bloom of the two
    accents, and translucent white cards.
  * The accent fills the primary button, switches, the selected tab or option and things that need checking.
    In dark mode it is never used for running text. Status badges: ok green, warn amber, bad red.
  * Inter (bundled) with ss01 + cv09 everywhere; tabular figures only on counts and timecodes (num_font).

The token formulas mirror the S4 Glass stylesheet (s4.css), so the app matches the approved mockup. Colours are
plain strings: "#rrggbb" or "rgba(r,g,b,a)" with a in 0..1; both work in style sheets and in qc().
"""
from __future__ import annotations

import math
import sys

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette

ACCENT, ACCENT_2, ACCENT_LIGHT = "#c81e3a", "#9f1239", "#fda4af"
NEUTRALS = "tint"                                   # "tint" (S4 Glass default) or "mono"

FONT_FAMILIES = ["Inter", "Segoe UI Variable Text", "Segoe UI", "DejaVu Sans"]
BASE_PX = 14
_fonts_loaded = False


# ============================================================================ colour maths (CSS color-mix, OKLCH)

def _rgba(c) -> tuple[float, float, float, float]:
    """'#rrggbb' | 'rgba(r,g,b,a)' | (r, g, b, a) -> floats: rgb 0..255, a 0..1."""
    if isinstance(c, tuple):
        return c
    c = c.strip()
    if c == "transparent":
        return (0.0, 0.0, 0.0, 0.0)
    if c.startswith("rgba"):
        r, g, b, a = [float(x) for x in c[c.index("(") + 1:c.rindex(")")].split(",")]
        return (r, g, b, a)
    h = c.lstrip("#")
    return (float(int(h[0:2], 16)), float(int(h[2:4], 16)), float(int(h[4:6], 16)), 1.0)


def css(c) -> str:
    r, g, b, a = _rgba(c)
    r, g, b = (max(0, min(255, round(x))) for x in (r, g, b))
    if a >= 0.999:
        return f"#{r:02x}{g:02x}{b:02x}"
    return f"rgba({r},{g},{b},{round(max(0.0, a), 3)})"


def mix(c1, p: float, c2) -> str:
    """CSS `color-mix(in srgb, c1 p, c2)`: premultiplied, so mixing with a translucent colour keeps its alpha."""
    r1, g1, b1, a1 = _rgba(c1)
    r2, g2, b2, a2 = _rgba(c2)
    a = a1 * p + a2 * (1 - p)
    if a <= 0:
        return "rgba(0,0,0,0)"
    return css(((r1 * a1 * p + r2 * a2 * (1 - p)) / a, (g1 * a1 * p + g2 * a2 * (1 - p)) / a,
                (b1 * a1 * p + b2 * a2 * (1 - p)) / a, a))


def alpha(c, p: float) -> str:
    """`color-mix(in srgb, c p, transparent)`."""
    r, g, b, a = _rgba(c)
    return css((r, g, b, a * p))


def over(top, bottom) -> str:
    """A translucent colour painted over another (the solid result)."""
    r1, g1, b1, a1 = _rgba(top)
    r2, g2, b2, a2 = _rgba(bottom)
    a = a1 + a2 * (1 - a1)
    if a <= 0:
        return "rgba(0,0,0,0)"
    return css(((r1 * a1 + r2 * a2 * (1 - a1)) / a, (g1 * a1 + g2 * a2 * (1 - a1)) / a,
                (b1 * a1 + b2 * a2 * (1 - a1)) / a, a))


def _lin(x: float) -> float:
    x /= 255
    return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4


def _gam(x: float) -> float:
    x = 12.92 * x if x <= 0.0031308 else 1.055 * (max(x, 0) ** (1 / 2.4)) - 0.055
    return x * 255


def to_oklch(c) -> tuple[float, float, float]:
    r, g, b, _ = _rgba(c)
    r, g, b = _lin(r), _lin(g), _lin(b)
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return L, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360


def _oklch_rgb(L: float, C: float, h: float) -> tuple[float, float, float]:
    a, b = C * math.cos(math.radians(h)), C * math.sin(math.radians(h))
    l_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
            -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
            -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_)


def from_oklch(L: float, C: float, h: float) -> str:
    """Back to sRGB, reducing chroma until it fits (what browsers do with an out-of-gamut colour)."""
    L = max(0.0, min(1.0, L))
    lo, hi = 0.0, C
    rgb = _oklch_rgb(L, C, h)
    if not all(-1e-4 <= x <= 1.0001 for x in rgb):
        for _ in range(24):
            mid = (lo + hi) / 2
            if all(-1e-4 <= x <= 1.0001 for x in _oklch_rgb(L, mid, h)):
                lo = mid
            else:
                hi = mid
        rgb = _oklch_rgb(L, lo, h)
    return css(tuple(_gam(min(1.0, max(0.0, x))) for x in rgb) + (1.0,))


def oklch_shift(c, dl: float, dc: float) -> str:
    """`oklch(from c calc(l + dl) calc(c + dc) h)`."""
    L, C, h = to_oklch(c)
    return from_oklch(L + dl, C + dc, h)


def mix_oklch(c1, p: float, c2) -> str:
    """`color-mix(in oklch, c1 p, c2)` (shorter hue arc)."""
    L1, C1, h1 = to_oklch(c1)
    L2, C2, h2 = to_oklch(c2)
    dh = ((h2 - h1 + 180) % 360) - 180
    return from_oklch(L1 * p + L2 * (1 - p), C1 * p + C2 * (1 - p), (h1 + dh * (1 - p)) % 360)


# ============================================================================ tokens

def tokens(dark: bool, accent: str = ACCENT, accent2: str = ACCENT_2, light: str = ACCENT_LIGHT,
           neutrals: str = NEUTRALS) -> dict:
    """The S4 Glass tokens for one theme. Names follow s4.css with '-' written as '_'."""
    mono = neutrals == "mono"
    tint, tint2 = ("#808080", "#808080") if mono else (accent, accent2)
    t: dict = {"is_dark": dark, "accent_base": accent, "accent2": accent2, "accent_light": light}
    t["btn"] = accent
    t["btn_fg"] = "#ffffff"
    t["ok"], t["warn"], t["bad"] = ("#6ccb6c", "#f2b84b", "#ff8a80") if dark else ("#107c10", "#9a5b00", "#b3261e")
    if not dark:
        t.update(
            bg=mix(tint, .015, "#f5f5f5"),
            glass="rgba(255,255,255,0.62)", glass2="rgba(255,255,255,0.82)", stroke="rgba(255,255,255,0.85)",
            bg3="rgba(255,255,255,0.5)", scrim="rgba(0,0,0,0.35)", pop_bg="#ffffff",
            text=mix(tint, .08, "#161616"), text2=mix(tint, .085, "#5c5c5c"), text3=mix(tint, .085, "#666666"),
            mute_bg=mix(tint, .04, "#f2f2f2"),
            hov=alpha(accent, .07), press=alpha(accent, .13),
            ok_bg="#e7f4e7", warn_bg="#fff3d6", bad_bg="#fde8e7", danger_fill="#b3261e",
            btn_hover=mix(accent, .87, "#000000"), btn_edge=mix(accent, .815, "#080808"),
            vivid=accent, nav1=oklch_shift(accent, .02, .01), nav3=accent2,
            seg_line="rgba(0,0,0,0)", screen="#0b0b0c", screen_line="#26262b",
            removed="#3d3d44", trim="#a3a3ab",
        )
        line = mix(tint2, .435, "#656565")
        t["border"], t["border2"] = alpha(line, .13), alpha(line, .22)
        t["bg2"], t["well"], t["bar_bg"], t["field_bg"] = t["glass"], t["bg3"], t["glass"], t["glass2"]
        t["modal_bg"], t["modal_line"] = t["glass2"], t["stroke"]
        t["acc"], t["accent_text"] = accent, accent
        t["accent_ink"] = mix(accent, .75, "#000000")
        t["badge_accent"] = t["accent_ink"]
        t["accent_soft"] = mix(accent, .095, "#ffffff")
        t["nav2"] = mix_oklch(accent, .48, accent2)
        t["tile_on"] = alpha(accent, .18)
        t["seg_track"] = alpha(t["text"], .07)
        shade = mix(tint2, .285, "#191919")
        t["card_shadow"] = alpha(shade, .09)
        t["shadow_deep"] = alpha(mix(tint2, .21, "#0c0c0c"), .22)
        t["pri_shadow"] = alpha(accent, .45)
        t["backlight"] = "rgba(0,0,0,0)"
        t["seg_glow"] = "rgba(0,0,0,0)"
        t["nav_glow"] = alpha(accent, .45)
        t["tip_bg"] = alpha(t["text"], .95)
        t["tip_line"] = "rgba(0,0,0,0)"
        t["page"] = [(0.0, t["bg"]), (1.0, t["bg"])]
        t["blooms"] = []
    else:
        t.update(
            bg=mix(tint2, .015, "#0a0a0a"),
            glass="rgba(255,255,255,0.045)", glass2="rgba(255,255,255,0.075)", stroke="rgba(255,255,255,0.12)",
            bg2="rgba(255,255,255,0.05)", bg3="rgba(255,255,255,0.03)", well="rgba(255,255,255,0.05)",
            bar_bg="rgba(255,255,255,0.03)", field_bg="rgba(255,255,255,0.05)",
            modal_line="rgba(255,255,255,0.1)", scrim="rgba(0,0,0,0.45)",
            pop_bg=mix(tint2, .035, "#191919"),
            text="#f2f2f2", text2="#b9b9b9", text3="#999999",
            border="rgba(255,255,255,0.1)", border2="rgba(255,255,255,0.17)", mute_bg="rgba(255,255,255,0.07)",
            hov=alpha(light, .11), press=alpha(light, .18),
            ok_bg="rgba(108,203,108,0.13)", warn_bg="rgba(242,184,75,0.13)", bad_bg="rgba(255,138,128,0.14)",
            danger_fill="#c62828",
            acc=light, accent_text="#f2f2f2", accent_ink="#f2f2f2", badge_accent=light,
            accent_soft=alpha(light, .14),
            seg_track="rgba(255,255,255,0.04)", seg_line="rgba(255,255,255,0.14)",
            screen="#0b0b0c", screen_line="#26262b", removed="#dadadf", trim="#8e8e97",
            tip_bg="rgba(44,44,48,0.97)", tip_line="rgba(255,255,255,0.14)",
        )
        t["modal_bg"] = alpha(mix(tint2, .025, "#171717"), .78)
        t["btn_hover"] = oklch_shift(accent, .04, .02)
        t["btn_edge"] = oklch_shift(light, -.04, .03)
        vivid = oklch_shift(accent, .11, .07)
        t["vivid"] = vivid
        t["nav1"] = oklch_shift(accent, .04, .02)
        t["nav3"] = oklch_shift(accent2, .02, .01)
        t["nav2"] = mix_oklch(t["nav1"], .45, t["nav3"])
        t["tile_on"] = alpha(vivid, .20)
        t["card_shadow"] = "rgba(0,0,0,0.28)"
        t["shadow_deep"] = "rgba(0,0,0,0.35)"
        t["pri_shadow"] = "rgba(0,0,0,0)"
        t["backlight"] = alpha(vivid, .15)
        t["seg_glow"] = alpha(vivid, .20)
        t["nav_glow"] = alpha(vivid, .13)
        page1, page3 = mix(tint2, .02, "#141414"), mix(tint2, .015, "#0c0c0c")
        t["page"] = [(0.0, page1), (0.55, "#0b0b0b"), (1.0, page3)]
        # (x, y, rx, ry, colour) of the two blooms, as fractions of the window and pixels
        t["blooms"] = [] if mono else [(1.0, -0.10, 1400, 900, alpha(accent, .11)),
                                       (-0.10, 1.15, 1200, 800, alpha(accent2, .09))]
    t["fill"], t["fill2"] = alpha(accent, .12), alpha(accent, .18)
    t["soft_ring"] = alpha(accent, .34)
    t["bg_hover"], t["bg_sel"] = alpha(t["text"], .05 if not dark else .07), alpha(t["text"], .08)
    t["focus"] = alpha(t["text"], .42)
    t["field_line"] = t["border2"] if not dark else "rgba(255,255,255,0.14)"
    t["field_focus_line"] = alpha(t["text"], .40)
    t["field_focus_bg"] = mix(t["text"], .06, t["field_bg"])
    t["btn_press"] = mix(accent, .78, "#000000") if not dark else accent
    t["btn_disabled"] = mix(accent, .5, t["bg"])
    t["selection"] = alpha(accent, .45 if dark else .25)
    t["scroll"] = alpha(t["text3"], .45)
    t["scroll_hover"] = alpha(t["text3"], .75)
    t["removed_bg"] = alpha(t["removed"], .12)
    t["mute_line"] = alpha(t["text3"], .45)
    # solid stand-ins for places that can't be see-through (the title bar, the Qt palette)
    base = t["page"][0][1]
    t["bar_solid"] = over(t["bar_bg"], base)
    t["card_solid"] = over(t["glass"], t["bg"])
    t["field_solid"] = over(t["field_bg"], t["bg"])
    # older names some code still reads
    t.update(accent=accent, dim=t["text2"], faint=t["text3"], surface=t["glass"], surface2=t["bg2"],
             surface3=t["mute_bg"], hover=t["hov"], border_strong=t["border2"], green=t["ok"], amber=t["warn"],
             red=t["bad"], ok_bg=t["ok_bg"], warn_bg=t["warn_bg"], bad_bg=t["bad_bg"])
    return t


DARK = tokens(True)
LIGHT = tokens(False)
CURRENT: dict = dict(LIGHT)                         # what the painted widgets read; set by set_current()


def set_current(t: dict):
    CURRENT.clear()
    CURRENT.update(t)


def qc(value) -> QColor:
    """A QColor from '#rrggbb' or 'rgba(r,g,b,a)' (a is 0..1)."""
    if isinstance(value, QColor):
        return QColor(value)
    r, g, b, a = _rgba(value)
    return QColor(round(r), round(g), round(b), round(a * 255))


# ============================================================================ fonts

def load_fonts() -> bool:
    """Register the bundled typeface (once). Falls back to Segoe UI if anything goes wrong."""
    global _fonts_loaded
    if _fonts_loaded:
        return True
    try:
        import base64
        from . import fonts
        ok = False
        for chunks in fonts.DATA.values():
            data = base64.b64decode("".join(chunks))
            ok = QFontDatabase.addApplicationFontFromData(QByteArray(data)) >= 0 or ok
        _fonts_loaded = ok
    except Exception:
        _fonts_loaded = False
    return _fonts_loaded


def _features(f: QFont, tags=("ss01", "cv09")) -> QFont:
    for tag in tags:                              # Inter's open digits and flat-top 3
        try:
            f.setFeature(QFont.Tag(tag), 1)
        except Exception:
            pass
    return f


_FONTS: dict = {}


def app_font(px: float = BASE_PX) -> QFont:
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPixelSize(round(px))
    f.setHintingPreference(QFont.PreferNoHinting)
    return _features(f)


def font(px: float, weight: int = 400, italic: bool = False, base: QFont | None = None) -> QFont:
    """Inter at a pixel size and weight (cached: painted widgets ask for fonts on every paint). Fractional sizes
    such as 12.5px come out as point sizes, so they aren't rounded."""
    key = (px, weight, italic) if base is None else None
    if key is not None and key in _FONTS:
        return QFont(_FONTS[key])
    f = QFont(base) if base is not None else app_font()
    if float(px).is_integer():
        f.setPixelSize(int(px))
    else:
        f.setPointSizeF(px * 0.75)
    f.setWeight(QFont.Weight(weight))
    f.setItalic(italic)
    if key is not None:
        _FONTS[key] = QFont(f)
    return f


_NUM: dict = {}


def num_font(base: QFont) -> QFont:
    """The same font with tabular numbers, for counts and timecodes only. (Not app-wide: Inter's tnum also makes
    the hyphen digit-wide, which spaced out words like "Spider-Man".)"""
    k = base.key()
    f = _NUM.get(k)
    if f is None:
        f = _NUM[k] = _features(QFont(base), ("ss01", "cv09", "tnum"))
    return QFont(f)


# ============================================================================ theme selection

def system_is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False


def palette_for(mode: str) -> dict:
    """mode: 'system' | 'light' | 'dark'"""
    dark = system_is_dark() if mode == "system" else mode == "dark"
    return dict(DARK if dark else LIGHT)


def qpalette(t: dict) -> QPalette:
    p = QPalette()
    c = lambda k: qc(t[k])
    p.setColor(QPalette.Window, c("bg"))
    p.setColor(QPalette.WindowText, c("text"))
    p.setColor(QPalette.Base, c("field_solid"))
    p.setColor(QPalette.AlternateBase, c("card_solid"))
    p.setColor(QPalette.Text, c("text"))
    p.setColor(QPalette.Button, c("card_solid"))
    p.setColor(QPalette.ButtonText, c("text"))
    p.setColor(QPalette.ToolTipBase, qc(over(t["tip_bg"], t["bg"])))
    p.setColor(QPalette.ToolTipText, QColor("#ffffff"))
    p.setColor(QPalette.PlaceholderText, c("text3"))
    p.setColor(QPalette.Highlight, c("btn"))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.Link, c("accent_text"))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, c("text3"))
    return p


# ============================================================================ style sheet

def _pt(px: float) -> str:
    """Fractional pixel sizes as points (Qt's px sizes are whole numbers)."""
    return f"{px * 0.75:.3f}pt"


def stylesheet(t: dict) -> str:
    """Widgets drawn by Qt. Everything with a shape of its own (cards, rows, badges, tabs, switches) is painted in
    widgets.py from the same tokens."""
    v = dict(t, sm=_pt(12.5), xs=_pt(11.5), sub=_pt(12.5))
    return """
* { outline: 0; }
QMainWindow, QDialog { background: transparent; }
QToolTip { background: %(tip_bg)s; color: #ffffff; border: 1px solid %(tip_line)s; padding: 6px 9px; border-radius: 10px; }

/* ---------- type */
QLabel { color: %(text)s; background: transparent; }
QLabel#H1 { font-size: 20px; font-weight: 600; }
QLabel#H2 { font-size: 18px; font-weight: 600; }
QLabel#H3 { font-size: 15px; font-weight: 600; }
QLabel#Hero { font-size: 26px; font-weight: 700; }
QLabel#HeroSub { color: %(text2)s; font-size: 15px; }
QLabel#Brand { font-size: 15px; font-weight: 600; }
QLabel#Sub { color: %(text2)s; font-size: 13px; }
QLabel#CardTitle { color: %(text2)s; font-size: 13px; font-weight: 600; }
QLabel#SecSub { color: %(text3)s; font-size: %(sub)s; }
QLabel#Muted { color: %(text2)s; font-size: 13px; }
QLabel#Hint { color: %(text3)s; font-size: %(sm)s; }
QLabel#Hint[on="true"] { color: %(text2)s; }
QLabel#Faint { color: %(text3)s; font-size: %(sm)s; }
QLabel#OptTitle { font-weight: 500; }
QLabel#OptDesc { color: %(text2)s; font-size: %(sm)s; }
QLabel#RuleDesc { color: %(text3)s; font-size: 12px; }
QLabel#FieldLabel { color: %(text2)s; font-size: 12px; font-weight: 500; }
QLabel#Status { color: %(text2)s; font-size: 13px; }
QLabel#Status[tone="good"] { color: %(ok)s; }
QLabel#Status[tone="bad"] { color: %(bad)s; }
QLabel#Chip { color: %(text2)s; background: %(well)s; border: 1px solid %(border)s; border-radius: 6px;
              padding: 0px 6px; font-size: %(xs)s; font-weight: 600; }
QLabel#Empty { color: %(text2)s; font-size: 14px; }
QLabel#DragVeilText { color: %(text)s; font-size: 22px; font-weight: 700; }
QLabel#FileName[done="true"] { color: %(text2)s; }
QLabel#FileRename { color: %(text)s; font-size: %(sm)s; }
QLabel#FileRename[tone="faint"] { color: %(text3)s; }
QLabel#FileRename[tone="alert"] { color: %(warn)s; font-weight: 500; }
QLabel#FileDetail { color: %(text2)s; font-size: %(sm)s; }
QLabel#FileCount { color: %(text2)s; }
QLabel#TrackTitle:disabled { color: %(text3)s; }
QLabel#TrackStat { color: %(text2)s; font-size: %(sm)s; }
QLabel#TrackStat[tone="accent"] { color: %(text)s; font-weight: 600; }
QLabel#TrackStat[tone="warn"] { color: %(warn)s; }
QLabel#TrackStat[tone="bad"] { color: %(bad)s; }
QLabel#TrackStat[tone="dim"] { color: %(text3)s; }
QLabel#RuleCount { color: %(text2)s; }
QLabel#RuleCount[zero="true"] { color: %(text3)s; font-weight: 400; }
QLabel#OptTitle:disabled, QLabel#OptDesc:disabled, QLabel#RuleDesc:disabled { color: %(text3)s; }
QLabel#BannerText { color: %(text)s; font-size: 13px; }
QLabel#BannerText[tone="warn"] { color: %(warn)s; }
QLabel#BannerText[tone="bad"] { color: %(bad)s; }
QLabel#ToolState[tone="good"] { color: %(ok)s; font-weight: 600; }
QLabel#ToolState[tone="warn"] { color: %(warn)s; font-weight: 600; }
QLabel#ToolState[tone="dim"] { color: %(text3)s; font-weight: 600; }

/* ---------- buttons: one rule, variants by property */
QPushButton { background: %(bg2)s; color: %(text)s; border: 1px solid %(border2)s; border-radius: 10px;
              padding: 0px 14px; min-height: 32px; font-weight: 500; }
QPushButton:hover { background: %(hov)s; }
QPushButton:pressed { background: %(press)s; }
QPushButton:focus { border-color: %(focus)s; }
QPushButton:disabled { color: %(text3)s; border-color: %(border)s; background: transparent; }
QPushButton[kind="primary"], QPushButton[kind="attn"] { background: %(btn)s; color: #ffffff;
              border: 1px solid %(btn_edge)s; font-weight: 600; }
QPushButton[kind="primary"]:hover, QPushButton[kind="attn"]:hover { background: %(btn_hover)s; }
QPushButton[kind="primary"]:pressed, QPushButton[kind="attn"]:pressed { background: %(btn_press)s; }
QPushButton[kind="primary"]:focus { border-color: %(text)s; }
QPushButton[kind="primary"]:disabled { background: %(btn_disabled)s; color: rgba(255,255,255,0.75); border-color: transparent; }
QPushButton[kind="subtle"] { background: transparent; border-color: transparent; color: %(text2)s; }
QPushButton[kind="subtle"]:hover { background: %(hov)s; color: %(text)s; }
QPushButton[kind="subtle"]:pressed { background: %(press)s; }
QPushButton[kind="subtle"]:focus { border-color: %(focus)s; }
QPushButton[kind="subtle"]:disabled { color: %(text3)s; background: transparent; border-color: transparent; }
QPushButton[kind="danger"] { color: %(bad)s; }
QPushButton[btnsize="sm"] { min-height: 26px; padding: 0px 11px; border-radius: 8px; font-size: %(sm)s; }
QPushButton[iconOnly="true"] { padding: 0px; }

/* ---------- fields */
QLineEdit { background: %(field_bg)s; color: %(text)s; border: 1px solid %(field_line)s; border-radius: 10px;
            padding: 0px 10px; min-height: 32px; selection-background-color: %(selection)s; selection-color: %(text)s; }
QLineEdit:focus { border-color: %(field_focus_line)s; background: %(field_focus_bg)s; }
QLineEdit:disabled { color: %(text3)s; border-color: %(border)s; }
QPlainTextEdit#Log { background: %(well)s; color: %(text)s; border: 1px solid %(border)s; border-radius: 10px; padding: 6px;
                     font-family: "Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace; font-size: 12px;
                     selection-background-color: %(selection)s; }

/* ---------- progress */
QProgressBar { background: %(mute_bg)s; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; }
QProgressBar::chunk { background: %(btn)s; border-radius: 3px; }

/* ---------- scrolling */
QScrollArea, QAbstractScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px 1px; }
QScrollBar::handle:vertical { background: %(scroll)s; border-radius: 3px; min-height: 36px; margin: 0px 2px; }
QScrollBar::handle:vertical:hover { background: %(scroll_hover)s; margin: 0px 1px; border-radius: 4px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 1px 2px; }
QScrollBar::handle:horizontal { background: %(scroll)s; border-radius: 3px; min-width: 36px; margin: 2px 0px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ---------- menus: one solid surface */
QMenu { background: %(pop_bg)s; color: %(text)s; border: 1px solid %(border2)s; border-radius: 12px; padding: 6px; }
QMenu::item { padding: 8px 28px 8px 10px; border-radius: 8px; margin: 1px 0px; }
QMenu::item:selected { background: %(hov)s; }
QMenu::item:disabled { color: %(text3)s; }
QMenu::icon { padding-left: 10px; }
QMenu::separator { height: 1px; background: %(border)s; margin: 4px 6px; }
""" % v


def apply_titlebar(widget, t: dict, glass: bool = False):
    """Windows 10/11: a dark or light title bar in the top bar's own colour, so the two read as one surface."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes
        hwnd = wintypes.HWND(int(widget.winId()))
        dwm = ctypes.windll.dwmapi

        def setattr_(attr, value):
            v = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, ctypes.c_uint(attr), ctypes.byref(v), ctypes.sizeof(v))

        setattr_(20, 1 if t["is_dark"] else 0)         # DWMWA_USE_IMMERSIVE_DARK_MODE
        setattr_(19, 1 if t["is_dark"] else 0)         # same attribute on older Windows 10 builds

        def colorref(hexstr):
            r, g, b, _ = _rgba(hexstr)
            return int(r) | (int(g) << 8) | (int(b) << 16)

        if glass:                                      # Mica shows through the title bar: use Windows' own colours
            setattr_(35, 0xFFFFFFFF)                   # DWMWA_COLOR_DEFAULT
            setattr_(36, 0xFFFFFFFF)
            setattr_(34, 0xFFFFFFFF)
            return
        setattr_(35, colorref(t["bar_solid"]))         # DWMWA_CAPTION_COLOR (Windows 11)
        setattr_(36, colorref(t["text"]))              # DWMWA_TEXT_COLOR
        setattr_(34, colorref(t["bar_solid"]))         # DWMWA_BORDER_COLOR
    except Exception:
        pass
