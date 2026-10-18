"""أدوات الجدولة: نوافذ الوقت اليومية (تدعم الامتداد عبر منتصف الليل)."""
import datetime as dt


def parse_hm(text, default=0):
    try:
        h, m = str(text).split(":")
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h * 60 + m
    except (ValueError, AttributeError):
        pass
    return default


def in_window(now, start, end):
    """هل الوقت الحالي داخل النافذة [start, end)؟ الصيغة HH:MM.

    إذا تساوى الطرفان تُعدّ النافذة اليوم كله. وإذا كان end أصغر من start فالنافذة تعبر منتصف الليل
    (مثل 22:00 إلى 06:00).
    """
    n = now.hour * 60 + now.minute
    a, b = parse_hm(start), parse_hm(end)
    if a == b:
        return True
    return a <= n < b if a < b else (n >= a or n < b)


def next_boundary(now, start, end):
    """أقرب لحظة قادمة تتغير فيها حالة النافذة (للعرض في الواجهة)."""
    inside = in_window(now, start, end)
    target = parse_hm(end if inside else start)
    cand = now.replace(hour=target // 60, minute=target % 60, second=0, microsecond=0)
    if cand <= now:
        cand += dt.timedelta(days=1)
    return cand
