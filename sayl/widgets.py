"""مكوّنات الواجهة المخصّصة."""
import os
import urllib.parse

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QColor, QLinearGradient, QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import icons, theme
from .categories import COLORS, LABELS, category_of
from .engine import (CANCELED, COMPLETED, DOWNLOADING, ERROR, PAUSED, PROBING, QUEUED)

CAT_ICON = {"video": "video", "audio": "music", "docs": "doc", "archive": "zip",
            "apps": "app", "images": "image", "other": "doc"}


def fmt_size(n):
    if n is None:
        return "—"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def fmt_speed(bps):
    return f"{fmt_size(bps)}/s"


def fmt_eta(s):
    if s is None:
        return ""
    s = int(s)
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def ltr(s):
    """يعزل نصًا لاتينيًا (أرقام ووحدات) داخل سطر عربي كي لا ينعكس ترتيبه."""
    return f"\u2066{s}\u2069" if s else s


def host_of(url):
    try:
        return urllib.parse.urlparse(url).hostname or ""
    except ValueError:
        return ""


class ElidedLabel(QLabel):
    """تسمية تقتطع النص الطويل بحسب عرضها الفعلي وتعرض النص كاملًا في التلميح."""

    def __init__(self, mode=Qt.ElideRight):
        super().__init__()
        self._full, self._mode = "", mode
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def set_full_text(self, text):
        if text != self._full:
            self._full = text
            self.setToolTip(text)
            self._elide()

    def _elide(self):
        QLabel.setText(self, self.fontMetrics().elidedText(self._full, self._mode,
                                                           max(20, self.width())))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()


class IconButton(QPushButton):
    def __init__(self, name, tip="", size=34, icon_px=18):
        super().__init__()
        self.setObjectName("Icon")
        self._name, self._px = name, icon_px
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tip)
        self.retint()

    def set_name(self, name):
        self._name = name
        self.retint()

    def retint(self, color=None):
        self.setIcon(icons.icon(self._name, color or theme.C["sub"], self._px))
        self.setIconSize(QSize(self._px, self._px))


class FileTile(QWidget):
    """مربع ملوّن بأيقونة نوع الملف."""

    def __init__(self, size=46, icon_name=None):
        super().__init__()
        self.setFixedSize(size, size)
        self.cat = "other"
        self.icon_name = icon_name

    def set_category(self, cat):
        if cat != self.cat:
            self.cat = cat
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        col = QColor(COLORS.get(self.cat, "#8A91A8"))
        bg = QColor(col)
        bg.setAlpha(38)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(self.rect(), 13, 13)
        s = max(24, int(self.width() * 0.52))
        px = icons.pixmap(self.icon_name or CAT_ICON.get(self.cat, "doc"), col.name(), s, 2.0)
        p.drawPixmap((self.width() - s) // 2, (self.height() - s) // 2, px)


class ProgressLine(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(6)
        self.value, self.mode = 0.0, "run"

    def set_value(self, v, mode):
        v = max(0.0, min(1.0, v))
        if abs(v - self.value) > 0.0005 or mode != self.mode:
            self.value, self.mode = v, mode
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.C["track"]))
        p.drawRoundedRect(r, 3, 3)
        if self.value <= 0:
            return
        col = {"run": theme.C["accent"], "ok": theme.C["ok"], "err": theme.C["err"],
               "idle": theme.C["sub"]}.get(self.mode, theme.C["accent"])
        w = max(6.0, r.width() * self.value)
        fill = QRectF(r.right() - w, r.top(), w, r.height()) \
            if self.layoutDirection() == Qt.RightToLeft else QRectF(r.left(), r.top(), w, r.height())
        p.setBrush(QColor(col))
        p.drawRoundedRect(fill, 3, 3)


class DownloadRow(QFrame):
    clicked = Signal(str)
    toggled = Signal(str)
    open_folder = Signal(str)
    open_file = Signal(str)
    delete = Signal(str)
    menu_requested = Signal(str, object)

    def __init__(self, d):
        super().__init__()
        self.id = d.id
        self.setObjectName("Row")
        self.setProperty("selected", False)
        self.setFixedHeight(88)
        self.setCursor(Qt.ArrowCursor)
        self._last = None

        self.tile = FileTile()
        self.name = ElidedLabel(Qt.ElideMiddle)
        self.name.setObjectName("RowName")
        self.chip = QLabel()
        self.chip.setObjectName("Chip")
        self.bar = ProgressLine()
        self.meta = ElidedLabel(Qt.ElideRight)
        self.meta.setObjectName("Meta")
        self.speed = QLabel()
        self.speed.setObjectName("Speed")
        self.speed.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.eta = QLabel()
        self.eta.setObjectName("Meta")
        self.eta.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.btn_toggle = IconButton("pause", "إيقاف مؤقت", 36, 18)
        self.btn_folder = IconButton("folder", "فتح المجلد", 36, 18)
        self.btn_del = IconButton("trash", "حذف", 36, 18)
        self.btn_toggle.clicked.connect(lambda: self.toggled.emit(self.id))
        self.btn_folder.clicked.connect(lambda: self.open_folder.emit(self.id))
        self.btn_del.clicked.connect(lambda: self.delete.emit(self.id))

        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self.name, 1)
        top.addWidget(self.chip)
        info = QVBoxLayout()
        info.setSpacing(7)
        info.addLayout(top)
        info.addWidget(self.bar)
        info.addWidget(self.meta)

        stats = QVBoxLayout()
        stats.setSpacing(2)
        stats.addStretch(1)
        stats.addWidget(self.speed)
        stats.addWidget(self.eta)
        stats.addStretch(1)
        stats_w = QWidget()
        stats_w.setFixedWidth(112)
        stats_w.setLayout(stats)
        stats.setContentsMargins(0, 0, 0, 0)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 12, 12)
        lay.setSpacing(14)
        lay.addWidget(self.tile)
        lay.addLayout(info, 1)
        lay.addWidget(stats_w)
        lay.addWidget(self.btn_toggle)
        lay.addWidget(self.btn_folder)
        lay.addWidget(self.btn_del)
        self.update_from(d, force=True)

    # ---- أحداث
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.id)
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        self.open_file.emit(self.id)

    def contextMenuEvent(self, e):
        self.clicked.emit(self.id)
        self.menu_requested.emit(self.id, e.globalPos())

    def set_selected(self, on):
        if self.property("selected") != on:
            self.setProperty("selected", on)
            self.style().unpolish(self)
            self.style().polish(self)

    def retint(self):
        for b in (self.btn_toggle, self.btn_folder, self.btn_del):
            b.retint()
        self.tile.update()
        self.bar.update()

    # ---- التحديث
    def _chip(self, text, kind):
        if self.chip.text() != text:
            self.chip.setText(text)
        if self.chip.property("kind") != kind:
            self.chip.setProperty("kind", kind)
            self.chip.style().unpolish(self.chip)
            self.chip.style().polish(self.chip)

    def update_from(self, d, force=False):
        st = d.state
        name = d.filename or d.url
        cat = category_of(d.filename or "", d.kind)
        if d.kind == "http" and not d.filename:
            cat = "other"
        key = (st, name, round(d.progress, 3), int(d.speed // 1024), d.size, d.error, cat)
        if key == self._last and not force:
            return
        self._last = key
        self.tile.set_category(cat)
        base, dot, ext = name.rpartition(".")
        # يعزل الامتداد كي لا ينقلب ترتيبه بعد اسم عربي
        self.name.set_full_text(f"{base}\u2066.{ext}\u2069" if dot and base and len(ext) <= 5 else name)

        running = st in (DOWNLOADING, PROBING)
        pct = f"{d.progress * 100:.0f}%"
        if st == COMPLETED:
            self._chip("مكتمل", "ok")
            self.bar.set_value(1.0, "ok")
            self.meta.setObjectName("Meta")
            parts = [ltr(fmt_size(d.size)) if d.size else "", LABELS[cat], ltr(host_of(d.url))]
            self._meta(" · ".join(p for p in parts if p), False)
            self.speed.setText("")
            self.eta.setText("")
        elif st == ERROR:
            self._chip("فشل", "err")
            self.bar.set_value(max(d.progress, 0.04), "err")
            self._meta(d.error or "حدث خطأ غير معروف", True)
            self.speed.setText("")
            self.eta.setText("")
        elif st == PAUSED or st == CANCELED:
            self._chip("متوقف", "idle")
            self.bar.set_value(d.progress, "idle")
            self._meta(f"{ltr(fmt_size(d.downloaded))} من {ltr(fmt_size(d.size))}"
                       if d.downloaded else "متوقف", False)
            self.speed.setText(pct if d.progress else "")
            self.eta.setText("")
        elif st == QUEUED:
            self._chip("في الانتظار", "idle")
            self.bar.set_value(d.progress, "idle")
            self._meta(ltr(host_of(d.url)), False)
            self.speed.setText("")
            self.eta.setText("")
        else:   # يُحمَّل / يُفحص
            self._chip("جارٍ الفحص" if st == PROBING else pct, "run")
            self.bar.set_value(d.progress, "run")
            extra = ""
            if d.kind == "http" and d.segments:
                live = sum(1 for s in d.segments if s.pos < s.end)
                extra = f" · {ltr(str(live))} اتصالات"
            elif d.kind == "media":
                extra = " · فيديو"
            self._meta(f"{ltr(fmt_size(d.downloaded))} من {ltr(fmt_size(d.size))}{extra}", False)
            self.speed.setText(fmt_speed(d.speed) if running and d.speed else "")
            e = fmt_eta(d.eta)
            self.eta.setText(f"متبقي {e}" if e else "")
        # زر التبديل
        if running or st == QUEUED:
            self.btn_toggle.set_name("pause")
            self.btn_toggle.setToolTip("إيقاف مؤقت")
        else:
            self.btn_toggle.set_name("retry" if st == ERROR else "play")
            self.btn_toggle.setToolTip("إعادة المحاولة" if st == ERROR else "استئناف")
        self.btn_toggle.setVisible(st != COMPLETED)

    def _meta(self, text, err):
        name = "MetaErr" if err else "Meta"
        if self.meta.objectName() != name:
            self.meta.setObjectName(name)
            self.meta.style().unpolish(self.meta)
            self.meta.style().polish(self.meta)
        self.meta.set_full_text(text)


class SpeedGraph(QWidget):
    """شريط السرعة الحيّ: الرقم الحالي ومنحنى ناعم لآخر دقيقة ونصف."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(62)
        self.values = []
        self._max = 1.0

    def set_values(self, vals):
        self.values = list(vals)
        self.update()

    def paintEvent(self, _):
        v = self.values
        if len(v) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        target = max(max(v) * 1.15, 256 * 1024)
        self._max += (target - self._max) * 0.35          # تكيّف ناعم مع القمم
        n = len(v)
        pts = [QPointF(i * w / (n - 1), h - 4 - (min(x, self._max) / self._max) * (h - 12))
               for i, x in enumerate(v)]
        path = QPainterPath(pts[0])
        for a, b in zip(pts, pts[1:]):
            mid = (a.x() + b.x()) / 2
            path.cubicTo(QPointF(mid, a.y()), QPointF(mid, b.y()), b)
        area = QPainterPath(path)
        area.lineTo(w, h)
        area.lineTo(0, h)
        area.closeSubpath()
        acc = QColor(theme.C["accent"])
        g = QLinearGradient(0, 0, 0, h)
        top = QColor(acc)
        top.setAlpha(110)
        bot = QColor(acc)
        bot.setAlpha(0)
        g.setColorAt(0, top)
        g.setColorAt(1, bot)
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawPath(area)
        p.setPen(QPen(acc, 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        if v[-1] > 0:
            p.setPen(Qt.NoPen)
            p.setBrush(acc)
            p.drawEllipse(pts[-1], 3.6, 3.6)


class NavButton(QPushButton):
    def __init__(self, key, icon_name, text):
        super().__init__()
        self.key, self.icon_name = key, icon_name
        self.setObjectName("Nav")
        self.setCheckable(True)
        self.setFixedHeight(35)
        self.setCursor(Qt.PointingHandCursor)
        self.ico = QLabel()
        self.ico.setFixedSize(20, 20)
        self.txt = QLabel(text)
        self.txt.setObjectName("NavText")
        self.cnt = QLabel("")
        self.cnt.setObjectName("NavCount")
        for w in (self.ico, self.txt, self.cnt):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(11)
        lay.addWidget(self.ico)
        lay.addWidget(self.txt, 1)
        lay.addWidget(self.cnt)
        self.toggled.connect(lambda _: self.retint())
        self.retint()

    def set_count(self, n):
        t = str(n) if n else ""
        if self.cnt.text() != t:
            self.cnt.setText(t)

    def retint(self):
        on = self.isChecked()
        color = theme.C["accent"] if on else theme.C["sub"]
        self.ico.setPixmap(icons.pixmap(self.icon_name, color, 20))
        for lbl in (self.txt, self.cnt):          # QSS لا يقرأ حالة الأب، فنمرّرها خاصية
            if lbl.property("on") != on:
                lbl.setProperty("on", on)
                lbl.style().unpolish(lbl)
                lbl.style().polish(lbl)

