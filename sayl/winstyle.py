"""مظهر شريط العنوان في ويندوز 10/11 عبر DWM: يتبع لون السمة، أو Mica، أو Acrylic.
خارج ويندوز (أو في نسخ قديمة) لا يفعل شيئًا ولا يسبب أخطاء."""
import sys

STYLES = {"default": "افتراضي (ويندوز)", "theme": "بلون السمة", "mica": "شفاف (Mica)",
          "acrylic": "زجاجي (Acrylic)"}
STYLE = "default"

DWMWA_DARK = 20
DWMWA_BORDER, DWMWA_CAPTION, DWMWA_TEXT, DWMWA_BACKDROP = 34, 35, 36, 38
BACKDROP = {"default": 1, "theme": 1, "mica": 2, "acrylic": 3}      # 1 = بلا
COLOR_DEFAULT = 0xFFFFFFFF


def colorref(hexstr):
    h = hexstr.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b << 16) | (g << 8) | r


def plan(style, colors, dark):
    """قائمة (خاصية DWM، قيمة) المطلوب ضبطها — دالة نقية لسهولة الاختبار."""
    if style not in STYLES:
        style = "default"
    if style == "default":
        return [(DWMWA_BACKDROP, 1), (DWMWA_CAPTION, COLOR_DEFAULT), (DWMWA_TEXT, COLOR_DEFAULT),
                (DWMWA_BORDER, COLOR_DEFAULT)]
    out = [(DWMWA_DARK, 1 if dark else 0), (DWMWA_BACKDROP, BACKDROP[style])]
    if style == "theme":
        out += [(DWMWA_CAPTION, colorref(colors["side"])), (DWMWA_TEXT, colorref(colors["text"])),
                (DWMWA_BORDER, colorref(colors["line"]))]
    else:
        out += [(DWMWA_CAPTION, COLOR_DEFAULT), (DWMWA_TEXT, COLOR_DEFAULT), (DWMWA_BORDER, COLOR_DEFAULT)]
    return out


def _apply_native(hwnd, attrs, extend):
    import ctypes
    from ctypes import byref, c_int, wintypes
    dwm = ctypes.windll.dwmapi
    for attr, val in attrs:
        v = c_int(val if val < 0x80000000 else val - 0x100000000)
        dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), c_int(attr), byref(v), 4)
    if extend:               # يمدّ إطار DWM كي يظهر تأثير Mica/Acrylic خلف شريط العنوان
        class MARGINS(ctypes.Structure):
            _fields_ = [("l", c_int), ("r", c_int), ("t", c_int), ("b", c_int)]
        m = MARGINS(-1, -1, -1, -1) if extend == "full" else MARGINS(0, 0, 1, 0)   # بالكامل أو شريط العنوان فقط
        dwm.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), byref(m))


def apply(win, style=None):
    if sys.platform != "win32":
        return False
    try:
        from . import theme
        style = style or STYLE
        full = theme.glass_active()              # سمة زجاجية: Acrylic على كامل النافذة
        if full:
            style = "acrylic"
        attrs = plan(style, theme.C, theme.NAME == "dark")
        _apply_native(int(win.winId()), attrs, extend=("full" if full else
                                                       "top" if style in ("mica", "acrylic") else None))
        return True
    except Exception:  # noqa: BLE001
        return False


def set_style(style):
    """يضبط النمط ويطبّقه على كل النوافذ المفتوحة."""
    global STYLE
    STYLE = style if style in STYLES else "default"
    if sys.platform != "win32":
        return
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is not None:
        for w in app.topLevelWidgets():
            if w.isWindow() and w.isVisible():
                apply(w)
