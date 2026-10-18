"""السمات (داكنة/فاتحة) وأوراق الأنماط. لون الإبراز الكهرماني للتقدّم، والباقي هادئ."""
import os
import tempfile

from . import icons

FONT = '"Segoe UI Variable Text","Segoe UI","Tahoma","Noto Sans Arabic","DejaVu Sans",sans-serif'

DARK = {
    "bg": "#0E1016", "side": "#0A0C11", "surface": "#161A23", "surface2": "#1D2230",
    "line": "#262C3B", "line2": "#343C50", "text": "#E9ECF5", "sub": "#8D95AB",
    "accent": "#FFB020", "accent_hover": "#FFC24D", "on_accent": "#1B1300",
    "accent_soft": "rgba(255,176,32,0.13)", "ok": "#4FD1A1", "err": "#FF6B6B",
    "track": "#262C3B",
}
LIGHT = {
    "bg": "#F4F6FA", "side": "#EAEEF6", "surface": "#FFFFFF", "surface2": "#EEF1F8",
    "line": "#DCE1EC", "line2": "#C5CCDC", "text": "#161A26", "sub": "#5F677D",
    "accent": "#F5A300", "accent_hover": "#FFB52E", "on_accent": "#1B1300",
    "accent_soft": "rgba(245,163,0,0.16)", "ok": "#0F9D74", "err": "#D43D3D",
    "track": "#DCE1EC",
}

MIDNIGHT = dict(DARK, bg="#0B1020", side="#080C18", surface="#121A30", surface2="#1A2440",
                line="#222E4D", line2="#31406A", sub="#8E9AC0", track="#222E4D")
BLACK = dict(DARK, bg="#000000", side="#000000", surface="#0D0D10", surface2="#17171C",
             line="#1F1F26", line2="#2E2E38", sub="#8A8A99", track="#1F1F26")
BASES = {"dark": DARK, "midnight": MIDNIGHT, "black": BLACK, "light": LIGHT}
BASE_LABELS = {"dark": "داكن", "midnight": "منتصف الليل", "black": "أسود (OLED)", "light": "فاتح"}
ACCENTS = ["#FFB020", "#2DD4BF", "#8B7CFF", "#FF6FA5", "#4DB6FF", "#5FD39A", "#FF7A45"]

C = dict(DARK)          # السمة الحالية، تقرؤها الأدوات المرسومة يدويًا
NAME = "dark"           # "dark" أو "light" (لتبديل الشمس/القمر)
BASE = "dark"
_asset_dir = None


def _assets(c):
    """يولّد صور السهم وعلامة الصح بلون السمة (QSS يحتاج مسار ملف)."""
    global _asset_dir
    if _asset_dir is None:
        _asset_dir = tempfile.mkdtemp(prefix="sayl_assets_")
    out = {}
    for key, name, color in (("chev", "chevron", c["sub"]), ("tick", "check", c["on_accent"])):
        path = os.path.join(_asset_dir, f"{key}_{c['bg'][1:]}.png")
        if not os.path.exists(path):
            icons.pixmap(name, color, 16, 2.0).save(path, "PNG")
        out[key] = path.replace("\\", "/")
    return out


def _rgb(h):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def valid_color(h):
    return isinstance(h, str) and len(h) == 7 and h[0] == "#" and \
        all(ch in "0123456789abcdefABCDEF" for ch in h[1:])


def lum(h):
    r, g, b = (v / 255 for v in _rgb(h))
    f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def on_color(h):
    """لون نص مقروء فوق اللون h."""
    return "#1B1300" if lum(h) > 0.32 else "#FFFFFF"


def mix(h, other, t):
    a, b = _rgb(h), _rgb(other)
    return "#%02X%02X%02X" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rgba(h, a):
    r, g, b = _rgb(h)
    return f"rgba({r},{g},{b},{a})"


def set_theme(base="dark", accent=None, ok=None, err=None):
    """يبني السمة من أساس + ألوان اختيارية؛ الألوان غير الصالحة تُتجاهل."""
    global NAME, BASE
    if base not in BASES:
        base = "dark"
    BASE = base
    NAME = "light" if base == "light" else "dark"
    C.clear()
    C.update(BASES[base])
    if valid_color(accent):
        light = NAME == "light"
        C["accent"] = accent
        C["accent_hover"] = mix(accent, "#000000" if light else "#FFFFFF", 0.18)
        C["accent_soft"] = rgba(accent, 0.16 if light else 0.13)
        C["on_accent"] = on_color(accent)
    if valid_color(ok):
        C["ok"] = ok
    if valid_color(err):
        C["err"] = err
    return build_qss()


def apply_config(config):
    base = "light" if config["theme"] == "light" else config["dark_base"]
    return set_theme(base, config["accent"], config["ok_color"], config["err_color"])


def build_qss():
    c = C
    a = _assets(c)
    return f"""
* {{ font-family: {FONT}; font-size: 13px; color: {c['text']}; outline: 0; }}
QMainWindow, QDialog {{ background: {c['bg']}; }}
#Content {{ background: {c['bg']}; }}
QLabel {{ background: transparent; }}
QToolTip {{ background: {c['surface2']}; color: {c['text']}; border: 1px solid {c['line2']};
  border-radius: 8px; padding: 6px 9px; }}

#Sidebar {{ background: {c['side']}; border-left: 1px solid {c['line']}; }}
#Wordmark {{ font-size: 25px; font-weight: 800; }}
#PageTitle {{ font-size: 25px; font-weight: 700; }}
#Sub {{ color: {c['sub']}; font-size: 12px; }}
#NavHead {{ color: {c['sub']}; font-size: 11.5px; font-weight: 600; padding: 0 14px; }}

QPushButton#Nav {{ background: transparent; border: none; border-radius: 10px; }}
QPushButton#Nav:hover {{ background: {c['surface']}; }}
QPushButton#Nav:checked {{ background: {c['accent_soft']}; }}
QLabel#NavText {{ font-size: 13.5px; }}
QLabel#NavCount {{ color: {c['sub']}; font-size: 12px; }}
QLabel#NavText[on="true"] {{ color: {c['accent']}; font-weight: 700; }}
QLabel#NavCount[on="true"] {{ color: {c['accent']}; }}

QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QTimeEdit {{ background: {c['surface']};
  border: 1px solid {c['line']}; border-radius: 10px; padding: 8px 12px;
  selection-background-color: {c['accent']}; selection-color: {c['on_accent']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QTimeEdit:focus {{ border-color: {c['accent']}; }}
QLineEdit#Search {{ padding: 9px 14px; min-width: 260px; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url({a['chev']}); width: 16px; height: 16px; }}
QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['line']};
  border-radius: 8px; padding: 4px; selection-background-color: {c['surface2']};
  selection-color: {c['text']}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}

QPushButton#Primary {{ background: {c['accent']}; color: {c['on_accent']}; border: none;
  border-radius: 10px; padding: 10px 20px; font-weight: 700; }}
QPushButton#Primary:hover {{ background: {c['accent_hover']}; }}
QPushButton#Primary:disabled {{ background: {c['line2']}; color: {c['sub']}; }}
QPushButton#Ghost {{ background: {c['surface']}; border: 1px solid {c['line']};
  border-radius: 10px; padding: 9px 16px; min-width: 64px; }}
QPushButton#Ghost:hover {{ background: {c['surface2']}; border-color: {c['line2']}; }}
QPushButton#Ghost:checked {{ background: {c['accent_soft']}; border-color: {c['accent']};
  color: {c['accent']}; font-weight: 700; }}
QPushButton#Icon, QToolButton#Icon {{ background: transparent; border: none; border-radius: 9px; }}
QPushButton#Icon:hover, QToolButton#Icon:hover {{ background: {c['surface2']}; }}
QToolButton#Icon::menu-indicator {{ image: none; width: 0; }}

QFrame#Row {{ background: {c['surface']}; border: 1px solid transparent; border-radius: 14px; }}
QFrame#Row:hover {{ border-color: {c['line2']}; }}
QFrame#Row[selected="true"] {{ border-color: {c['accent']}; }}
QFrame#Card {{ background: {c['surface']}; border: 1px solid {c['line']}; border-radius: 14px; }}
QFrame#Banner {{ background: {c['surface2']}; border: 1px solid {c['accent']}; border-radius: 12px; }}
QLabel#Chip {{ border-radius: 9px; padding: 2px 9px; font-size: 11.5px; font-weight: 600;
  background: {c['surface2']}; color: {c['sub']}; }}
QLabel#Chip[kind="ok"] {{ background: {rgba(c['ok'], 0.15)}; color: {c['ok']}; }}
QLabel#Chip[kind="err"] {{ background: {rgba(c['err'], 0.15)}; color: {c['err']}; }}
QLabel#Chip[kind="run"] {{ background: {c['accent_soft']}; color: {c['accent']}; }}
QLabel#Meta {{ color: {c['sub']}; font-size: 12px; }}
QLabel#MetaErr {{ color: {c['err']}; font-size: 12px; }}
QLabel#RowName {{ font-size: 14px; font-weight: 600; }}
QLabel#Speed {{ font-size: 15px; font-weight: 700; }}
QLabel#BigSpeed {{ font-size: 22px; font-weight: 800; }}

QListWidget {{ background: transparent; border: none; }}
QListWidget::item, QListWidget::item:selected, QListWidget::item:hover {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['line']}; border-radius: 4px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: {c['line2']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QMenu {{ background: {c['surface']}; border: 1px solid {c['line']}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 8px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['surface2']}; }}
QMenu::separator {{ height: 1px; background: {c['line']}; margin: 5px 8px; }}

QTabWidget::pane {{ border: none; border-top: 1px solid {c['line']}; top: -1px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {c['sub']}; padding: 9px 18px; margin-left: 2px;
  border: none; border-bottom: 2px solid transparent; font-weight: 600; }}
QTabBar::tab:hover {{ color: {c['text']}; }}
QTabBar::tab:selected {{ color: {c['accent']}; border-bottom: 2px solid {c['accent']}; }}

QCheckBox {{ spacing: 9px; background: transparent; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 6px;
  border: 1.5px solid {c['line2']}; background: {c['surface']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']};
  image: url({a['tick']}); }}
QMessageBox {{ background: {c['bg']}; }}
"""
