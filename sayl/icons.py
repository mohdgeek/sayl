"""أيقونات مرسومة بالكود (خطوط نظيفة بلا ملفات صور)، تتلوّن حسب السمة."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QIcon, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF)


def _pts(*xy):
    return QPolygonF([QPointF(x, y) for x, y in xy])


def _line(p, *xy):
    p.drawPolyline(_pts(*xy))


def _draw(name, p, color):
    p.setPen(QPen(color, 1.9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    fill = QBrush(color)
    if name == "plus":
        _line(p, (12, 5), (12, 19))
        _line(p, (5, 12), (19, 12))
    elif name == "close":
        _line(p, (6, 6), (18, 18))
        _line(p, (18, 6), (6, 18))
    elif name == "pause":
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(QRectF(6.5, 5, 3.8, 14), 1.4, 1.4)
        p.drawRoundedRect(QRectF(13.7, 5, 3.8, 14), 1.4, 1.4)
    elif name == "play":
        p.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(fill)
        p.drawPolygon(_pts((8, 5.5), (18.5, 12), (8, 18.5)))
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(3.5, 7)
        path.lineTo(3.5, 17.5)
        path.quadTo(3.5, 19, 5, 19)
        path.lineTo(19, 19)
        path.quadTo(20.5, 19, 20.5, 17.5)
        path.lineTo(20.5, 9.5)
        path.quadTo(20.5, 8, 19, 8)
        path.lineTo(11.5, 8)
        path.lineTo(9.5, 5.8)
        path.lineTo(5, 5.8)
        path.quadTo(3.5, 5.8, 3.5, 7.3)
        p.drawPath(path)
    elif name == "trash":
        _line(p, (5, 7), (19, 7))
        _line(p, (7, 7), (8, 19), (16, 19), (17, 7))
        _line(p, (10, 7), (10, 4.5), (14, 4.5), (14, 7))
    elif name == "search":
        p.drawEllipse(QPointF(10.5, 10.5), 5.5, 5.5)
        _line(p, (14.6, 14.6), (19.5, 19.5))
    elif name == "gear":
        p.drawEllipse(QPointF(12, 12), 6.2, 6.2)
        p.drawEllipse(QPointF(12, 12), 2.4, 2.4)
        import math
        for i in range(8):
            a = math.radians(i * 45)
            p.drawLine(QPointF(12 + 6.2 * math.cos(a), 12 + 6.2 * math.sin(a)),
                       QPointF(12 + 9 * math.cos(a), 12 + 9 * math.sin(a)))
    elif name == "link":
        for dx in (-4.5, 4.5):
            p.save()
            p.translate(12 + dx * 0.7, 12 + dx * -0.7)
            p.rotate(-45)
            p.drawRoundedRect(QRectF(-5.5, -2.8, 11, 5.6), 2.8, 2.8)
            p.restore()
    elif name == "all":
        for x, y in ((4, 4), (13.5, 4), (4, 13.5), (13.5, 13.5)):
            p.drawRoundedRect(QRectF(x, y, 6.5, 6.5), 1.8, 1.8)
    elif name == "down":
        _line(p, (12, 4.5), (12, 15.5))
        _line(p, (6.5, 10.5), (12, 16), (17.5, 10.5))
        _line(p, (5, 20), (19, 20))
    elif name == "check":
        _line(p, (5, 12.5), (10, 17.5), (19, 7))
    elif name == "video":
        p.drawRoundedRect(QRectF(3, 6, 13, 12), 2.5, 2.5)
        p.drawPolygon(_pts((16, 10.5), (21, 7.5), (21, 16.5), (16, 13.5)))
    elif name == "music":
        p.drawEllipse(QPointF(8, 17.5), 2.8, 2.8)
        p.drawEllipse(QPointF(17, 15.5), 2.8, 2.8)
        _line(p, (10.8, 17.5), (10.8, 6.5), (19.8, 4.5), (19.8, 15.5))
    elif name == "doc":
        _line(p, (6.5, 3.5), (13.5, 3.5), (18.5, 8.5), (18.5, 20.5), (6.5, 20.5), (6.5, 3.5))
        _line(p, (13.5, 3.5), (13.5, 8.5), (18.5, 8.5))
        _line(p, (9.5, 13), (15.5, 13))
        _line(p, (9.5, 16.5), (15.5, 16.5))
    elif name == "zip":
        p.drawRoundedRect(QRectF(5.5, 3.5, 13, 17), 2.2, 2.2)
        _line(p, (12, 3.5), (12, 8.5))
        for y in (8.5, 12, 15.5):
            p.drawLine(QPointF(10.2, y), QPointF(13.8, y))
        p.drawRoundedRect(QRectF(10, 15.5, 4, 3.2), 1, 1)
    elif name == "app":
        p.drawRoundedRect(QRectF(3, 5, 18, 14), 2.5, 2.5)
        _line(p, (3, 9.2), (21, 9.2))
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        for x in (5.8, 8.4):
            p.drawEllipse(QPointF(x, 7.1), 0.75, 0.75)
    elif name == "image":
        p.drawRoundedRect(QRectF(3, 5, 18, 14), 2.5, 2.5)
        p.drawEllipse(QPointF(9, 10), 1.7, 1.7)
        _line(p, (4, 17.5), (10, 12.8), (14, 16), (17, 13.5), (20.5, 16.5))
    elif name == "sun":
        p.drawEllipse(QPointF(12, 12), 4, 4)
        import math
        for i in range(8):
            a = math.radians(i * 45)
            p.drawLine(QPointF(12 + 6.8 * math.cos(a), 12 + 6.8 * math.sin(a)),
                       QPointF(12 + 9 * math.cos(a), 12 + 9 * math.sin(a)))
    elif name == "moon":
        path = QPainterPath()
        path.addEllipse(QPointF(12, 12), 8, 8)
        cut = QPainterPath()
        cut.addEllipse(QPointF(16.5, 9), 7, 7)
        p.drawPath(path.subtracted(cut))
    elif name == "clip":
        p.drawRoundedRect(QRectF(5.5, 5, 13, 16), 2.4, 2.4)
        p.drawRoundedRect(QRectF(9, 3, 6, 4), 1.6, 1.6)
        _line(p, (9, 12), (15, 12))
        _line(p, (9, 15.5), (13, 15.5))
    elif name == "more":
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        for y in (6, 12, 18):
            p.drawEllipse(QPointF(12, y), 1.7, 1.7)
    elif name == "chevron":
        _line(p, (7, 10), (12, 15), (17, 10))
    elif name == "bolt":
        p.setBrush(fill)
        p.drawPolygon(_pts((13.5, 3), (6, 13.5), (11.2, 13.5), (10, 21), (18, 10), (12.6, 10)))
    elif name == "retry":
        path = QPainterPath()
        path.arcMoveTo(QRectF(5, 5, 14, 14), 40)
        path.arcTo(QRectF(5, 5, 14, 14), 40, 280)
        p.drawPath(path)
        _line(p, (17.5, 3.8), (17.8, 8.2), (13.4, 8.4))


def pixmap(name, color="#ffffff", size=20, dpr=2.0):
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 24.0, size / 24.0)
    _draw(name, p, QColor(color))
    p.end()
    return px


def icon(name, color="#ffffff", size=20):
    return QIcon(pixmap(name, color, size))


def logo_pixmap(size=128):
    """شعار «سيل»: مربع كهرماني متدرّج وموجتان متدفقتان بنفس الطول الموجي (تقصر الثانية)."""
    from PySide6.QtGui import QLinearGradient
    px = QPixmap(size, size)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, 0, size)
    g.setColorAt(0, QColor("#FFCB52"))
    g.setColorAt(1, QColor("#FF9500"))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.27, size * 0.27)
    p.setPen(QPen(QColor("#1B1300"), size * 0.105, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    h = size * 0.20                     # نصف الطول الموجي ثابت، فتتطابق أطوار الموجات ولا تتصادم
    c = size * 0.13
    for halves, y in ((3, 0.39), (2, 0.63)):    # تقصر الموجة كلما نزلنا
        x, Y = size * 0.20, y * size
        path = QPainterPath(QPointF(x, Y))
        for k in range(halves):
            sgn = -1 if k % 2 == 0 else 1
            path.cubicTo(QPointF(x + h * 0.27, Y + sgn * c), QPointF(x + h * 0.73, Y + sgn * c),
                         QPointF(x + h, Y))
            x += h
        p.drawPath(path)
    p.end()
    return px
