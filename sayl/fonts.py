"""خط «ثمانية»: لا يُضمَّن في البرنامج (ترخيصه يمنع إعادة التوزيع)، بل يلتقطه سيل من جهازك.

حمّل الخط من الصفحة الرسمية ثم ثبّته في ويندوز، أو ضع ملفاته (otf/ttf) في المجلد ~/.sayl/fonts.
"""
import os

from . import theme
from .config import APP_DIR

FONT_DIR = os.path.join(APP_DIR, "fonts")
OFFICIAL_PAGE = "https://ask.thmanyah.com/hc/en-001/articles/45993930027281-Thmanyah-Font-for-Everyone"


def load_user_fonts():
    """يسجّل ملفات الخطوط الموجودة في مجلد الخطوط. يرجع عددها."""
    from PySide6.QtGui import QFontDatabase
    n = 0
    try:
        for f in sorted(os.listdir(FONT_DIR)):
            if f.lower().endswith((".otf", ".ttf", ".ttc")):
                if QFontDatabase.addApplicationFont(os.path.join(FONT_DIR, f)) >= 0:
                    n += 1
    except OSError:
        pass
    return n


def thmanyah_families():
    from PySide6.QtGui import QFontDatabase
    return [f for f in QFontDatabase.families() if "thmanyah" in f.lower() or "ثمانية" in f]


def apply():
    """يضع خط ثمانية أولًا في قائمة الخطوط إن وُجد. يرجع أسماء العائلات الموجودة."""
    load_user_fonts()
    fams = thmanyah_families()
    if fams:
        # نفضّل Sans للواجهة ثم أي عائلة أخرى
        fams.sort(key=lambda f: ("sans" not in f.lower(), f))
        theme.FONT = ",".join(f'"{f}"' for f in fams[:2]) + "," + theme.BASE_FONT
    else:
        theme.FONT = theme.BASE_FONT
    return fams
