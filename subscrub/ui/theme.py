"""SubScrub's look: the Saha app style.

  * Light mode: calm, faintly red-tinted off-white with white cards, hairline borders and soft shadows.
  * Dark mode: glass. Translucent white cards over a near-black gradient with a faint neutral bloom. Everything but the crimson accent is monochrome.
  * One accent, picked for the subject (media: red). It is the primary button, the selected item and anything that
    needs you; success, caution and error use their own green, amber and red badges.
  * Inter at 14px, tabular numbers, 9-12px corners, Solar icons.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette

ACCENT, ACCENT_2, ACCENT_LIGHT = "#c81e3a", "#9f1239", "#fda4af"

DARK = dict(
    # surfaces are translucent whites over the backdrop; the *_s keys are solid stand-ins for places that can't be
    # see-through (menus, tooltips, the Qt palette)
    bg="#0D0D0E", cap="#151516", header="#151516", side="#151516",
    bar="rgba(255,255,255,0.03)", bar_edge="rgba(255,255,255,0.10)",
    surface="rgba(255,255,255,0.045)", surface2="rgba(255,255,255,0.045)", surface3="rgba(255,255,255,0.08)",
    surface_s="#1B1B1C", surface2_s="#202021", surface3_s="#2A2A2B", menu_bg="#1E1E1F",
    border="rgba(255,255,255,0.10)", border_strong="rgba(255,255,255,0.16)", seg_on="#C81E3A",
    dlg_a="#1C1C1D", dlg_b="#141415", dlg_c="#121213",
    bloom="#FFFFFF", bloom_a=0.045, bloom2="#FFFFFF", bloom2_a=0.0,
    text="#F2F2F2", dim="#B9B9B9", faint="#8C8C8C",
    accent="#C81E3A", accent_ink="#FDA4AF", accent2=ACCENT_2, accent_light=ACCENT_LIGHT,
    accent_hover="#D04058", accent_press="#A5182F", accent_text="#FFFFFF", accent_edge="#D86275",
    accent_soft="rgba(255,255,255,0.06)", accent_line="rgba(253,164,175,0.50)",
    glow="rgba(200,30,58,0.45)",
    hover="rgba(255,255,255,0.08)", press="rgba(255,255,255,0.12)",
    ok="#6CCB6C", ok_bg="#1C2B1C", warn="#F2B84B", warn_bg="#2F2814", bad="#FF8A80", bad_bg="#3A2120",
    trim="#8E8E97", removed="#DADADF", removed_soft="rgba(218,218,223,0.10)",
    screen="#09090A", screen_line="#26262B", screen_text="#FFFFFF",
    selection="rgba(200,30,58,0.45)", scroll="rgba(255,255,255,0.20)", scroll_hover="rgba(255,255,255,0.32)",
    sw_off="#4A4A53", sw_knob_off="#D4D4DA", radio_off="#6A6A74",
    # legacy names
    green="#6CCB6C", green_soft="#1C2B1C", amber="#F2B84B", amber_soft="#2F2814",
    red="#FF8A80", red_soft="#3A2120", teal="#B9B9B9", teal_soft="rgba(255,255,255,0.08)",
    gray_soft="rgba(255,255,255,0.08)", sel_row="rgba(255,255,255,0.07)", shadow="rgba(0,0,0,0.5)",
)

LIGHT = dict(
    bg="#F7F7F7", cap="#F7F7F7", header="#F7F7F7", side="#FFFFFF",
    bar="#FBFBFB", bar_edge="#E7E7E7",
    surface="#FFFFFF", surface2="#FFFFFF", surface3="#F3F3F3",
    surface_s="#FFFFFF", surface2_s="#FFFFFF", surface3_s="#F3F3F3", menu_bg="#FFFFFF",
    border="#E7E7E7", border_strong="#D5D5D5", seg_on="#C81E3A",
    dlg_a="#F7F7F7", dlg_b="#F7F7F7", dlg_c="#F7F7F7",
    text="#1C1C1C", dim="#5C5C5C", faint="#808080",
    accent="#C81E3A", accent_ink="#C81E3A", accent2=ACCENT_2, accent_light=ACCENT_LIGHT,
    accent_hover="#AC1A32", accent_press="#90162A", accent_text="#FFFFFF", accent_edge="#A0182E",
    accent_soft="#F1F1F1", accent_line="rgba(200,30,58,0.45)",
    glow="rgba(200,30,58,0.30)",
    hover="#F3F3F3", press="#E9E9E9",
    ok="#107C10", ok_bg="#E7F4E7", warn="#9A5B00", warn_bg="#FFF3D6", bad="#B3261E", bad_bg="#FDE8E7",
    trim="#A3A3AB", removed="#3D3D44", removed_soft="rgba(30,30,40,0.08)",
    screen="#0E0E10", screen_line="#26262B", screen_text="#FFFFFF",
    selection="rgba(200,30,58,0.25)", scroll="rgba(0,0,0,0.22)", scroll_hover="rgba(0,0,0,0.38)",
    sw_off="#CFCFCF", sw_knob_off="#FFFFFF", radio_off="#B5B5B5",
    green="#107C10", green_soft="#E7F4E7", amber="#9A5B00", amber_soft="#FFF3D6",
    red="#B3261E", red_soft="#FDE8E7", teal="#5C5C5C", teal_soft="#F1F1F1",
    gray_soft="#F1F1F1", sel_row="#EDEDED", shadow="rgba(20,20,26,0.12)",
)

FONT_FAMILIES = ["Inter", "Segoe UI Variable Text", "Segoe UI", "DejaVu Sans"]
BASE_PT = 10.5                                   # 14px
_fonts_loaded = False


def qc(value: str) -> QColor:
    """A QColor from '#rrggbb' or the 'rgba(r,g,b,a)' strings the style sheet uses (a is 0..1)."""
    if value.startswith("rgba"):
        r, g, b, a = [x.strip() for x in value[value.index("(") + 1:value.rindex(")")].split(",")]
        return QColor(int(float(r)), int(float(g)), int(float(b)), round(float(a) * 255))
    return QColor(value)


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


def app_font(size: float = BASE_PT) -> QFont:
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPointSizeF(size)
    f.setHintingPreference(QFont.PreferNoHinting)
    for tag in ("ss01", "cv09"):                  # Inter's open digits and single-storey a
        try:
            f.setFeature(QFont.Tag(tag), 1)
        except Exception:
            pass
    return f


def num_font(base: QFont) -> QFont:
    """The same font with tabular numbers, for counts and timecodes only. (Not app-wide: Inter's tnum also makes
    the hyphen digit-wide, which spaced out words like "Spider-Man".)"""
    f = QFont(base)
    try:
        f.setFeature(QFont.Tag("tnum"), 1)
    except Exception:
        pass
    return f


def system_is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False


def palette_for(mode: str) -> dict:
    """mode: 'system' | 'light' | 'dark'"""
    dark = system_is_dark() if mode == "system" else mode == "dark"
    return dict(DARK if dark else LIGHT, is_dark=dark)


def qpalette(t: dict) -> QPalette:
    p = QPalette()
    c = lambda k: qc(t[k])
    p.setColor(QPalette.Window, c("bg"))
    p.setColor(QPalette.WindowText, c("text"))
    p.setColor(QPalette.Base, c("surface2_s"))
    p.setColor(QPalette.AlternateBase, c("surface3_s"))
    p.setColor(QPalette.Text, c("text"))
    p.setColor(QPalette.Button, c("surface2_s"))
    p.setColor(QPalette.ButtonText, c("text"))
    p.setColor(QPalette.ToolTipBase, c("surface3_s"))
    p.setColor(QPalette.ToolTipText, c("text"))
    p.setColor(QPalette.PlaceholderText, c("faint"))
    p.setColor(QPalette.Highlight, c("accent"))
    p.setColor(QPalette.HighlightedText, c("accent_text"))
    p.setColor(QPalette.Link, c("accent_ink"))
    p.setColor(QPalette.Disabled, QPalette.Text, c("faint"))
    p.setColor(QPalette.Disabled, QPalette.WindowText, c("faint"))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, c("faint"))
    return p


def stylesheet(t: dict) -> str:
    return """
* { outline: 0; }
QWidget { color: %(text)s; font-size: 10.5pt; }
QMainWindow { background: transparent; }
#Root { background: transparent; }
QDialog { background: qradialgradient(cx: 1, cy: 0, radius: 1.25, fx: 1, fy: 0,
                                      stop: 0 %(dlg_a)s, stop: 0.55 %(dlg_b)s, stop: 1 %(dlg_c)s); }
QToolTip { background: %(menu_bg)s; color: %(text)s; border: 1px solid %(border_strong)s;
           padding: 7px 9px; border-radius: 8px; font-size: 10pt; }

/* ---------- type */
#AppTitle { font-size: 12.5pt; font-weight: 700; }
#AppSub { color: %(faint)s; font-size: 9.5pt; }
#PageTitle { font-size: 15pt; font-weight: 700; }
#Count { color: %(faint)s; font-size: 12pt; font-weight: 600; }
#CardTitle, #SideTitle { font-size: 11pt; font-weight: 600; }
#CardHint, #Hint { color: %(dim)s; font-size: 10pt; }
#Faint { color: %(faint)s; font-size: 10pt; }
#Summary { color: %(dim)s; font-size: 10.5pt; }
#Section { color: %(faint)s; font-size: 8.5pt; font-weight: 600; }
#SectionNote { color: %(faint)s; font-size: 9.5pt; }
#Hero { font-size: 20pt; font-weight: 700; }
#HeroSub { color: %(dim)s; font-size: 11.5pt; }
#Empty { color: %(dim)s; font-size: 11pt; }
#Divider { background: %(border)s; max-height: 1px; min-height: 1px; border: none; }

/* ---------- chrome */
#Header { background: transparent; border: none; }
#Footer { background: %(bar)s; border: none; border-top: 1px solid %(border)s; }
#Status { color: %(dim)s; }
#Status[tone="good"] { color: %(ok)s; }
#Status[tone="bad"] { color: %(bad)s; }
#Sidebar { background: transparent; border: none; }
#Card { background: transparent; border: none; }

/* ---------- welcome */
#Drop { background: %(surface)s; border: 1.5px dashed %(border_strong)s; border-radius: 16px; }
#Drop[hover="true"], #Root[dragging="true"] #Drop { border-color: %(accent)s; }
#DropTitle { font-size: 17pt; font-weight: 700; }
#DropSub { color: %(dim)s; font-size: 10.5pt; }
#DragVeil { background: %(accent_soft)s; border: 2px dashed %(accent)s; border-radius: 14px; }
#DragVeilText { color: %(accent_ink)s; font-size: 16pt; font-weight: 700; background: transparent; }

/* ---------- buttons */
QPushButton { background: %(surface2)s; border: 1px solid %(border_strong)s; border-radius: 9px;
              padding: 6px 14px; min-height: 20px; font-weight: 500; }
QPushButton:hover { background: %(surface3)s; }
QPushButton:pressed { background: %(press)s; }
QPushButton:disabled { color: %(faint)s; background: transparent; border-color: %(border)s; }
QPushButton#Primary { color: %(accent_text)s; background: %(accent)s; border: 1px solid %(accent_edge)s;
                      padding: 8px 20px; border-radius: 9px; font-size: 10.5pt; font-weight: 600; }
QPushButton#Primary:hover { background: %(accent_hover)s; }
QPushButton#Primary:pressed { background: %(accent_press)s; }
QPushButton#Primary:disabled { background: %(surface3)s; color: %(faint)s; border: 1px solid %(border)s; }
QPushButton#Danger { background: transparent; color: %(bad)s; border: 1px solid %(bad)s;
                     padding: 8px 20px; border-radius: 9px; font-weight: 600; }
QPushButton#Danger:hover { background: %(bad_bg)s; }
QPushButton#Ghost, QToolButton#Ghost { background: transparent; border: 1px solid transparent; padding: 6px 11px;
                                       border-radius: 9px; color: %(dim)s; font-weight: 500; }
QPushButton#Ghost:hover, QToolButton#Ghost:hover { background: %(hover)s; color: %(text)s; }
QPushButton#Ghost:disabled { color: %(faint)s; }
QToolButton#Ghost::menu-indicator { image: none; width: 0; }
QPushButton#Secondary { background: %(surface2)s; border: 1px solid %(border_strong)s; }
QPushButton#Secondary:hover { background: %(hover)s; }
QPushButton#Link { background: transparent; border: none; color: %(accent_ink)s; padding: 2px 2px; font-weight: 600; }
QPushButton#Link:hover { text-decoration: underline; }
QPushButton#LinkQuiet { background: transparent; border: none; color: %(dim)s; padding: 2px 2px; font-weight: 500; }
QPushButton#LinkQuiet:hover { color: %(text)s; text-decoration: underline; }
QPushButton#Pill { background: %(surface)s; border: 1px solid %(border)s; border-radius: 15px;
                   padding: 4px 12px; color: %(dim)s; font-size: 9.5pt; font-weight: 500; }
QPushButton#Pill:hover { background: %(hover)s; color: %(text)s; }
QPushButton#Pill[tone="bad"] { color: %(accent_ink)s; border-color: %(accent_line)s; }
QPushButton#Back { background: transparent; border: none; border-radius: 9px; padding: 6px 10px 6px 6px;
                   color: %(dim)s; font-weight: 500; }
QPushButton#Back:hover { background: %(hover)s; color: %(text)s; }
QToolButton#IconButton { background: transparent; border: none; border-radius: 9px; padding: 6px; }
QToolButton#IconButton::menu-indicator { image: none; width: 0; height: 0; }
QToolButton#IconButton:hover { background: %(hover)s; }
QPushButton#RowButton { background: %(surface2)s; border: 1px solid %(border_strong)s; border-radius: 8px;
                        padding: 4px 12px; font-weight: 500; color: %(text)s; min-height: 18px; }
QPushButton#RowButton:hover { background: %(hover)s; }
QToolButton#RowX { background: transparent; border: none; padding: 0; border-radius: 7px; }
QToolButton#RowX:hover { background: %(hover)s; }
QPushButton#Choice, QPushButton#ChoicePrimary { text-align: left; padding: 12px 16px; border-radius: 12px;
                     background: %(surface2)s; border: 1px solid %(border)s; font-weight: 500; }
QPushButton#Choice:hover { background: %(hover)s; border-color: %(border_strong)s; }
QPushButton#ChoicePrimary { background: %(accent_soft)s; border: 1px solid %(accent_line)s; }
QPushButton#ChoicePrimary:hover { border-color: %(accent)s; }
QLabel#ChoiceTitle { font-weight: 600; color: %(text)s; background: transparent; }
QLabel#ChoiceDesc { color: %(dim)s; font-size: 9.5pt; background: transparent; }

/* ---------- segmented pills */
#Segmented { background: %(surface2)s; border: 1px solid %(border)s; border-radius: 17px; }
#Segmented QPushButton { background: transparent; border: none; border-radius: 12px; min-height: 18px;
                         padding: 4px 12px; color: %(dim)s; font-weight: 500; font-size: 10pt; }
#Segmented QPushButton:hover { color: %(text)s; }
#Segmented QPushButton:checked { background: %(accent)s; color: %(accent_text)s; font-weight: 600; }

/* ---------- file list */
#FileRow { background: transparent; border: 1px solid transparent; border-radius: 12px; }
#FileRow:hover { background: %(hover)s; border-color: %(border)s; }
#FileName { font-weight: 600; font-size: 11pt; }
#FileDetail { color: %(dim)s; font-size: 10pt; }
#FileRename { color: %(text)s; font-size: 10.5pt; }
#FileRename[tone="faint"] { color: %(faint)s; }
#FileRename[tone="alert"] { color: %(warn)s; font-weight: 600; }
#NewName { font-size: 11pt; font-weight: 600; }
#OldName { color: %(faint)s; font-size: 9.5pt; }
QPushButton#NameChoice { text-align: left; background: %(surface2)s; border: 1px solid %(border)s; border-radius: 10px;
                         padding: 8px 12px; font-weight: 400; }
QPushButton#NameChoice:hover { border-color: %(border_strong)s; background: %(hover)s; }
QPushButton#NameChoice:checked { background: %(accent_soft)s; border-color: %(accent)s; font-weight: 600; }
#FileCount { color: %(dim)s; font-size: 10pt; }
QProgressBar#RowBar { max-height: 4px; min-height: 4px; border-radius: 2px; background: %(surface3)s; }

/* ---------- tracks (Review sidebar) */
#Track { background: %(surface)s; border: 1px solid %(border)s; border-radius: 12px; }
#Track:hover { border-color: %(border_strong)s; background: %(hover)s; }
#Track[selected="true"] { background: %(accent_soft)s; border: 1px solid %(accent)s; }
#Track[enabledTrack="false"] { background: transparent; }
#TrackTitle { font-weight: 600; }
#TrackTitle[muted="true"] { color: %(faint)s; }
#TrackMeta { color: %(dim)s; font-size: 9.5pt; }
#TrackStat { color: %(dim)s; font-size: 9.5pt; }
#TrackStat[tone="accent"] { color: %(text)s; font-weight: 600; }
#TrackStat[tone="good"] { color: %(ok)s; }
#TrackStat[tone="warn"] { color: %(warn)s; }
#TrackStat[tone="bad"] { color: %(bad)s; }
#TrackStat[tone="dim"] { color: %(faint)s; }
#Badge { border-radius: 6px; padding: 1px 7px; font-size: 8.5pt; font-weight: 600;
         color: %(dim)s; border: 1px solid %(border_strong)s; }
#Badge[kind="sdh"] { color: %(accent_ink)s; border-color: %(accent_line)s; background: %(accent_soft)s; }
#Badge[kind="default"] { color: %(text)s; }

/* ---------- option rows */
#OptTitle { font-weight: 600; color: %(text)s; }
#OptDesc { color: %(dim)s; font-size: 9.5pt; }
#RuleCount { color: %(dim)s; font-size: 10pt; font-weight: 600; }
#RuleCount[zero="true"] { color: %(faint)s; font-weight: 400; }

/* ---------- inputs */
QLineEdit { background: %(surface2)s; border: 1px solid %(border_strong)s; border-radius: 9px; padding: 6px 11px;
            min-height: 20px; selection-background-color: %(selection)s; }
QLineEdit:focus { border: 1px solid %(accent)s; }
QLineEdit:disabled { color: %(faint)s; }

/* ---------- preview */
#PreviewList { background: transparent; border: none; }
#NavCount { color: %(dim)s; font-size: 10pt; font-weight: 600; }
#Chip { border-radius: 10px; padding: 2px 8px; font-size: 9pt; font-weight: 600;
        color: %(dim)s; background: %(gray_soft)s; }
#Chip[kind="flag"], #Chip[kind="low"], #Chip[kind="check"] { color: %(warn)s; background: %(warn_bg)s; }
#Chip[kind="failed"] { color: %(bad)s; background: %(bad_bg)s; }
#Chip[kind="ready"], #Chip[kind="done"] { color: %(ok)s; background: %(ok_bg)s; }

/* ---------- banners */
#Banner { border-radius: 12px; }
#Banner[tone="warn"], #Banner[tone="flag"] { background: %(warn_bg)s; border: 1px solid %(warn)s; }
#Banner[tone="info"] { background: %(surface)s; border: 1px solid %(border)s; }
#Banner[tone="good"] { background: %(ok_bg)s; border: 1px solid %(ok)s; }
#Banner QLabel { background: transparent; }

/* ---------- progress */
QProgressBar { background: %(surface3)s; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; }
QProgressBar::chunk { background: %(accent)s; border-radius: 3px; }

/* ---------- scrollbars */
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: %(scroll)s; border-radius: 3px; min-height: 36px; margin: 0 2px; }
QScrollBar::handle:vertical:hover { background: %(scroll_hover)s; margin: 0; border-radius: 4px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: %(scroll)s; border-radius: 3px; min-width: 36px; margin: 2px 0; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ---------- menus & dialogs */
QMenu { background: %(menu_bg)s; border: 1px solid %(border_strong)s; border-radius: 12px; padding: 6px; }
QMenu::item { padding: 8px 28px 8px 12px; border-radius: 8px; }
QMenu::item:selected { background: %(hover)s; }
QMenu::separator { height: 1px; background: %(border)s; margin: 5px 8px; }
QMessageBox QLabel { color: %(text)s; }
QPlainTextEdit#Log { background: %(surface)s; border: 1px solid %(border)s; border-radius: 12px;
                     font-family: "Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace; font-size: 9pt; padding: 6px; }
""" % t


def glow(root, t: dict):
    """Dark mode: soft accent light behind the primary buttons (and nowhere else). Light mode: none."""
    from PySide6.QtWidgets import QGraphicsDropShadowEffect, QPushButton
    for b in root.findChildren(QPushButton, "Primary"):
        if t["is_dark"]:
            e = QGraphicsDropShadowEffect(b)
            e.setBlurRadius(30)
            e.setOffset(0, 2)
            e.setColor(qc(t["glow"]))
            b.setGraphicsEffect(e)
        else:
            b.setGraphicsEffect(None)


def apply_titlebar(widget, t: dict, glass: bool = False):
    """Windows 10/11: dark/light title bar tinted to match the app; also the glow on primary buttons."""
    try:
        glow(widget, t)
    except Exception:
        pass
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
            h = hexstr.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return r | (g << 8) | (b << 16)

        if glass:                                      # Mica shows through the title bar: use Windows' own colours
            setattr_(35, 0xFFFFFFFF)                   # DWMWA_COLOR_DEFAULT
            setattr_(36, 0xFFFFFFFF)
            setattr_(34, 0xFFFFFFFF)
            return
        # the title bar takes the backdrop's top colour, so it and the toolbar below read as one surface
        setattr_(35, colorref(t["cap"]))               # DWMWA_CAPTION_COLOR (Windows 11)
        setattr_(36, colorref(t["text"]))              # DWMWA_TEXT_COLOR
        setattr_(34, colorref(t["cap"]))               # DWMWA_BORDER_COLOR
    except Exception:
        pass
