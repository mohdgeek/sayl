"""خط «ثمانية»: لا يُضمَّن في البرنامج (ترخيصه يمنع إعادة التوزيع)، بل يلتقطه سيل من جهازك.

حمّل الخط من الصفحة الرسمية ثم ثبّته في ويندوز، أو ضع ملفاته (otf/ttf) في المجلد ~/.sayl/fonts.
"""
import os

from . import theme
from .config import APP_DIR

FONT_DIR = os.path.join(APP_DIR, "fonts")
OFFICIAL_PAGE = "https://ask.thmanyah.com/hc/en-001/articles/45993930027281-Thmanyah-Font-for-Everyone"


def _auto_dirs():
    """يبحث في مجلد التنزيلات عن ملفات thmanyahsans (بعمق محدود) دون لمس غيرها."""
    root = os.path.join(os.path.expanduser("~"), "Downloads")
    out = []
    base_depth = root.count(os.sep)
    try:
        for dp, dn, fn in os.walk(root):
            if dp.count(os.sep) - base_depth >= 5:
                dn[:] = []
                continue
            if any(f.lower().startswith("thmanyahsans") and f.lower().endswith((".otf", ".ttf")) for f in fn):
                out.append(dp)
                dn[:] = []
            if len(out) >= 2:
                break
    except OSError:
        pass
    return out


def font_dirs(config=None):
    dirs = []
    if config is not None and config["font_dir"]:
        dirs.append(config["font_dir"])
    dirs.append(FONT_DIR)
    if not any(d for d in dirs if os.path.isdir(d) and _has_fonts(d)):
        dirs.extend(_auto_dirs())
    return dirs


def _has_fonts(d):
    try:
        return any(f.lower().endswith((".otf", ".ttf")) for f in os.listdir(d))
    except OSError:
        return False


def load_user_fonts(config=None):
    """يسجّل ملفات الخطوط (otf/ttf) من المجلدات المعروفة. يرجع عددها."""
    from PySide6.QtGui import QFontDatabase
    n = 0
    for d in font_dirs(config):
        try:
            for f in sorted(os.listdir(d)):
                if f.lower().endswith((".otf", ".ttf", ".ttc")) and "thmanyah" in f.lower():
                    if QFontDatabase.addApplicationFont(os.path.join(d, f)) >= 0:
                        n += 1
        except OSError:
            continue
    return n


def thmanyah_families():
    from PySide6.QtGui import QFontDatabase
    return [f for f in QFontDatabase.families() if "thmanyah" in f.lower() or "ثمانية" in f]


def apply(config=None):
    """يضع خط ثمانية أولًا في قائمة الخطوط إن وُجد. يرجع أسماء العائلات الموجودة."""
    load_user_fonts(config)
    fams = thmanyah_families()
    if fams:
        # نفضّل Sans للواجهة ثم أي عائلة أخرى
        fams.sort(key=lambda f: ("sans" not in f.lower(), f))
        theme.FONT = ",".join(f'"{f}"' for f in fams[:2]) + "," + theme.BASE_FONT
    else:
        theme.FONT = theme.BASE_FONT
    return fams
