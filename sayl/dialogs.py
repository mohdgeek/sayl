"""نوافذ الإضافة والإعدادات."""
import os
import re
import threading
import urllib.parse

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtCore import QTime
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox, QDialog,
                               QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QSpinBox, QTabWidget, 
                               QVBoxLayout, QWidget)

from . import __version__, media, selfupdate, setup_tools, theme, updater
from .categories import CATEGORIES, COLORS, LABELS, category_of
from .media import find_ffmpeg, is_media_url
from .widgets import ltr

QUALITIES = [(0, "أفضل جودة متاحة"), (1080, "1080p"), (720, "720p"), (480, "480p"), (360, "360p")]


class _Probe(QObject):
    done = Signal(object, str)      # (معلومات أو None، رسالة خطأ)

    def run(self, url, headers=None):
        def work():
            try:
                self.done.emit(media.probe(url, headers), "")
            except Exception as e:  # noqa: BLE001
                self.done.emit(None, str(e))
        threading.Thread(target=work, daemon=True).start()


class _FileProbe(QObject):
    done = Signal(str, object)      # (اسم الملف، الحجم بالبايت أو None)

    def run(self, url, headers=None):
        def work():
            try:
                import requests
                from .engine import filename_from_response
                h = {"User-Agent": "Mozilla/5.0 Sayl/1.0", **(headers or {}), "Range": "bytes=0-0"}
                with requests.get(url, headers=h, stream=True, timeout=(10, 20)) as r:
                    size = None
                    if r.status_code == 206:
                        m = re.search(r"/(\d+)$", r.headers.get("Content-Range", ""))
                        size = int(m.group(1)) if m else None
                    else:
                        cl = r.headers.get("Content-Length", "")
                        size = int(cl) if cl.isdigit() else None
                    self.done.emit(filename_from_response(r, url), size)
            except Exception:  # noqa: BLE001
                self.done.emit("", None)
        threading.Thread(target=work, daemon=True).start()


def _fmt_size(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.2f} {u}"
        n /= 1024


class FileConfirmDialog(QDialog):
    """نافذة «معلومات الملف» قبل التحميل (على طريقة IDM): الرابط، الفئة، مكان الحفظ، الوصف."""

    def __init__(self, config, url, headers=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.start = True
        self.setWindowTitle("معلومات الملف قبل التحميل")
        self.setMinimumWidth(620)
        self._edited_path = False
        try:
            guess = os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(url).path)) or "file"
        except ValueError:
            guess = "file"
        self._name = guess

        self.url = QLineEdit(url)
        self.url.setReadOnly(True)
        self.url.setLayoutDirection(Qt.LeftToRight)

        self.cat = QComboBox()
        for key, label, _ in CATEGORIES:
            self.cat.addItem(label, key)
        self.cat.setCurrentIndex(max(0, self.cat.findData(category_of(guess))))

        self.path = QLineEdit()
        self.path.setLayoutDirection(Qt.LeftToRight)
        self.path.textEdited.connect(lambda *_: setattr(self, "_edited_path", True))
        browse = QPushButton("…")
        browse.setObjectName("Ghost")
        browse.setFixedSize(52, 40)
        browse.setStyleSheet("padding: 0;")
        browse.clicked.connect(self._browse)

        self.remember = QCheckBox("")
        self.remember_path = QLineEdit()
        self.remember_path.setReadOnly(True)
        self.remember_path.setLayoutDirection(Qt.LeftToRight)

        self.note = QLineEdit()
        self.note.setPlaceholderText("اختياري")

        self.badge = QLabel()
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setFixedSize(64, 64)
        self.size_lbl = QLabel("جارٍ قراءة معلومات الملف…")
        self.size_lbl.setObjectName("Sub")
        self.size_lbl.setAlignment(Qt.AlignCenter)
        self.size_lbl.setWordWrap(True)
        side = QVBoxLayout()
        side.addWidget(self.badge, 0, Qt.AlignHCenter)
        side.addWidget(self.size_lbl)
        side.addStretch(1)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(10)
        form.addRow("الرابط", self.url)
        form.addRow("الفئة", self.cat)
        prow = QHBoxLayout()
        prow.addWidget(self.path, 1)
        prow.addWidget(browse)
        form.addRow("حفظ باسم", prow)
        form.addRow(self.remember)
        form.addRow("", self.remember_path)
        form.addRow("الوصف", self.note)

        self.btn_later = QPushButton("تحميل لاحقًا")
        self.btn_later.setObjectName("Ghost")
        self.btn_start = QPushButton("بدء التحميل")
        self.btn_start.setObjectName("Primary")
        self.btn_start.setDefault(True)
        cancel = QPushButton("إلغاء")
        cancel.setObjectName("Ghost")
        self.btn_later.clicked.connect(lambda: self._finish(False))
        self.btn_start.clicked.connect(lambda: self._finish(True))
        cancel.clicked.connect(self.reject)
        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(self.btn_later)
        btns.addWidget(self.btn_start)
        btns.addWidget(cancel)

        top = QHBoxLayout()
        top.addLayout(form, 1)
        top.addLayout(side)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(14)
        t = QLabel("معلومات الملف")
        t.setObjectName("PageTitle")
        lay.addWidget(t)
        lay.addLayout(top)
        lay.addLayout(btns)

        self.cat.currentIndexChanged.connect(self._cat_changed)
        self.url.setCursorPosition(0)
        self._fprobe = _FileProbe()
        self._fprobe.done.connect(self._on_probe)
        self._cat_changed()
        self._fprobe.run(url, headers)

    # ---- المنطق
    def _key(self):
        return self.cat.currentData()

    def _folder_for(self, key):
        saved = (self.config["category_dirs"] or {}).get(key)
        if saved:
            return saved
        base = self.config["download_dir"]
        return os.path.join(base, LABELS[key]) if self.config["subfolders"] else base

    def _cat_changed(self):
        key = self._key()
        col = COLORS.get(key, "#8A91A8")
        self.badge.setText(LABELS[key])
        self.badge.setStyleSheet(f"background:{col}; color:#101010; border-radius:14px; font-weight:700;")
        self.remember.setText(f"تذكّر هذا المسار لفئة «{LABELS[key]}»")
        folder = self._folder_for(key)
        self.remember_path.setText(folder)
        if not self._edited_path:
            self.path.setText(os.path.join(folder, self._name))
            self.path.setCursorPosition(0)

    def _on_probe(self, name, size):
        if name:
            self._name = name
            self.cat.blockSignals(True)
            self.cat.setCurrentIndex(max(0, self.cat.findData(category_of(name))))
            self.cat.blockSignals(False)
            self._cat_changed()
            if self._edited_path:      # غيّر المستخدم المسار بيده: نبدّل الاسم فقط
                self.path.setText(os.path.join(os.path.dirname(self.path.text()), name))
        self.size_lbl.setText(_fmt_size(size) if size else "الحجم غير معروف")

    def _browse(self):
        f, _ = QFileDialog.getSaveFileName(self, "حفظ باسم", self.path.text())
        if f:
            self._edited_path = True
            self.path.setText(f)
            self.remember_path.setText(os.path.dirname(f))

    def _finish(self, start):
        self.start = start
        if not self.path.text().strip():
            return
        if self.remember.isChecked():
            dirs = dict(self.config["category_dirs"] or {})
            dirs[self._key()] = os.path.dirname(self.path.text().strip())
            self.config["category_dirs"] = dirs
            self.config.save()
        self.accept()

    def values(self):
        full = self.path.text().strip()
        return {"folder": os.path.dirname(full) or self.config["download_dir"],
                "filename": os.path.basename(full), "start": self.start,
                "note": self.note.text().strip(), "category": self._key()}


def _fmt_dur(sec):
    if not sec:
        return ""
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _title(text):
    l = QLabel(text)
    l.setObjectName("Sub")
    return l


class AddDialog(QDialog):
    def __init__(self, config, parent=None, urls="", video=False, preset=None):
        super().__init__(parent)
        self.config = config
        self._want_h = int((preset or {}).get("height") or 0)
        self.setWindowTitle("إضافة تحميل")
        self.setMinimumWidth(560)
        self._probe = _Probe()
        self._probe.done.connect(self._on_probe)
        self._fprobe = _FileProbe()
        self._fprobe.done.connect(self._on_file_probe)
        self._headers = (preset or {}).get("headers")
        self.finfo = QLabel("")
        self.finfo.setObjectName("Sub")
        self.finfo.setWordWrap(True)
        self.finfo.setVisible(False)

        self.urls = QPlainTextEdit(urls)
        self.urls.setPlaceholderText("ألصق رابطًا أو أكثر، رابط في كل سطر")
        self.urls.setFixedHeight(84)
        self.urls.setLayoutDirection(Qt.LeftToRight)
        self.urls.textChanged.connect(self._on_text)

        self.b_file = QPushButton("ملف")
        self.b_video = QPushButton("فيديو أو صوت")
        for b in (self.b_file, self.b_video):
            b.setObjectName("Ghost")
            b.setCheckable(True)
            b.setMinimumWidth(175)
            b.setCursor(Qt.PointingHandCursor)
        self.b_file.clicked.connect(lambda: self._set_mode(False))
        self.b_video.clicked.connect(lambda: self._set_mode(True))

        # لوحة الفيديو
        self.panel = QFrame()
        self.panel.setObjectName("Card")
        self.btn_probe = QPushButton("فحص الرابط")
        self.btn_probe.setObjectName("Ghost")
        self.btn_probe.clicked.connect(self._start_probe)
        self.info = QLabel("افحص الرابط لمعرفة العنوان والجودات المتاحة، أو اختر الجودة مباشرة.")
        self.info.setObjectName("Sub")
        self.info.setWordWrap(True)
        self.quality = QComboBox()
        for h, t in QUALITIES:
            self.quality.addItem(t, h)
        idx = self.quality.findData(int(config["video_quality"] or 0))
        self.quality.setCurrentIndex(max(0, idx))
        self.audio = QCheckBox("صوت فقط (MP3)")
        self.audio.toggled.connect(lambda on: self.quality.setEnabled(not on))
        self.subs = QCheckBox("الترجمة")
        self.subs.setChecked(bool(config["subtitles"]))
        self.plist = QListWidget()
        self.plist.setMaximumHeight(170)
        self.plist.setVisible(False)
        self.pl_all = QPushButton("تحديد الكل")
        self.pl_none = QPushButton("إلغاء التحديد")
        self.pl_count = QLabel("")
        self.pl_count.setObjectName("Sub")
        for b, v in ((self.pl_all, Qt.Checked), (self.pl_none, Qt.Unchecked)):
            b.setObjectName("Ghost")
            b.clicked.connect(lambda _=False, st=v: self._pl_set(st))
        self.pl_bar = QWidget()
        pb = QHBoxLayout(self.pl_bar)
        pb.setContentsMargins(0, 0, 0, 0)
        pb.addWidget(self.pl_all)
        pb.addWidget(self.pl_none)
        pb.addWidget(self.pl_count, 1)
        self.pl_bar.setVisible(False)
        self.plist.itemChanged.connect(lambda *_: self._pl_update())
        self.ff = QLabel()
        self.ff.setObjectName("Sub")
        self.ff.setWordWrap(True)
        pl = QVBoxLayout(self.panel)
        pl.setContentsMargins(14, 12, 14, 12)
        pl.setSpacing(10)
        row = QHBoxLayout()
        row.addWidget(self.btn_probe)
        row.addWidget(self.info, 1)
        pl.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(_title("الجودة"))
        row2.addWidget(self.quality, 1)
        row2.addWidget(self.audio)
        row2.addWidget(self.subs)
        pl.addLayout(row2)
        pl.addWidget(self.pl_bar)
        pl.addWidget(self.plist)
        pl.addWidget(self.ff)

        self.folder = QLineEdit(config["download_dir"])
        self.folder.setLayoutDirection(Qt.LeftToRight)
        browse = QPushButton("استعراض")
        browse.setObjectName("Ghost")
        browse.clicked.connect(self._browse)
        frow = QHBoxLayout()
        frow.addWidget(self.folder, 1)
        frow.addWidget(browse)

        self.later = QCheckBox("ابدأ لاحقًا حسب الجدولة")
        self.later.setVisible(bool(config["schedule_enabled"]))
        self.ok = QPushButton("بدء التحميل")
        self.ok.setObjectName("Primary")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self.accept)
        cancel = QPushButton("إلغاء")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        btns = QHBoxLayout()
        btns.addWidget(self.later)
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(self.ok)

        seg = QHBoxLayout()
        seg.setSpacing(8)
        seg.addWidget(self.b_file)
        seg.addWidget(self.b_video)
        seg.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(10)
        t = QLabel("إضافة تحميل")
        t.setObjectName("PageTitle")
        lay.addWidget(t)
        lay.addWidget(_title("الرابط"))
        lay.addWidget(self.urls)
        lay.addLayout(seg)
        lay.addWidget(self.finfo)
        lay.addWidget(self.panel)
        lay.addWidget(_title("حفظ في"))
        lay.addLayout(frow)
        lay.addSpacing(6)
        lay.addLayout(btns)

        self._entries = []
        self._video = False
        self._manual = video       # إن اختار المستخدم النوع بنفسه لا نغيّره تلقائيًا
        self._set_mode(video, manual=False)
        self._on_text()
        if preset:
            if preset.get("audio_only"):
                self.audio.setChecked(True)
            elif self._want_h:
                self._select_height(self._want_h)
        if (preset or {}).get("file") and urls.strip():   # ملف عادي من المتصفح: اعرض الاسم والحجم
            self.setWindowTitle("تأكيد التحميل")
            self.finfo.setText("جارٍ قراءة معلومات الملف…")
            self.finfo.setVisible(True)
            self._fprobe.run(self._first_url(), self._headers)
        if video and urls.strip():             # قادم من الإضافة: اعرض الجودات المتاحة فورًا
            from PySide6.QtCore import QTimer
            QTimer.singleShot(150, self._start_probe)

    # ---- المنطق
    def _on_file_probe(self, name, size):
        if not name and size is None:
            self.finfo.setText("تعذّر قراءة معلومات الملف، يمكنك البدء مع ذلك.")
            return
        parts = [f"الملف: {name}" if name else "", f"الحجم: {_fmt_size(size)}" if size else ""]
        self.finfo.setText("    •    ".join(x for x in parts if x))

    def _first_url(self):
        for line in self.urls.toPlainText().splitlines():
            if line.strip():
                return line.strip()
        return ""

    def _on_text(self):
        u = self._first_url()
        if not self._manual and u:
            self._set_mode(is_media_url(u), manual=False)
        if self._entries:
            self._hide_playlist()      # تغيّر الرابط: القائمة القديمة لم تعد صالحة
        self.ok.setEnabled(bool(u))

    def _set_mode(self, video, manual=True):
        if manual:
            self._manual = True
        self._video = video
        self.b_video.setChecked(video)
        self.b_file.setChecked(not video)
        self.panel.setVisible(video)
        ff = find_ffmpeg(self.config["ffmpeg_path"])
        if not media.available():
            self.ff.setText("ميزة الفيديو تحتاج الحزمة yt-dlp: pip install yt-dlp")
        elif ff:
            self.ff.setText("تم العثور على ffmpeg: يمكن دمج الصوت والصورة بأعلى جودة.")
        else:
            self.ff.setText("ffmpeg غير موجود: ستُحمَّل الصيغ الجاهزة فقط (غالبًا حتى 720p). "
                            "ثبّته بالأمر winget install ffmpeg لأعلى جودة وتحويل MP3.")
        self.adjustSize()

    def _show_playlist(self, info):
        self._entries = info["entries"]
        self.plist.blockSignals(True)
        self.plist.clear()
        for e in self._entries:
            it = QListWidgetItem(e["title"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            self.plist.addItem(it)
        self.plist.blockSignals(False)
        self.plist.setVisible(True)
        self.pl_bar.setVisible(True)
        self.info.setText(f"قائمة تشغيل: {info['title']}")
        self.quality.setEnabled(not self.audio.isChecked())
        self._pl_update()
        self.adjustSize()

    def _hide_playlist(self):
        self._entries = []
        self.plist.setVisible(False)
        self.pl_bar.setVisible(False)
        self.plist.clear()

    def _pl_set(self, state):
        self.plist.blockSignals(True)
        for i in range(self.plist.count()):
            self.plist.item(i).setCheckState(state)
        self.plist.blockSignals(False)
        self._pl_update()

    def _checked_entries(self):
        return [self._entries[i] for i in range(self.plist.count())
                if self.plist.item(i).checkState() == Qt.Checked]

    def _pl_update(self):
        n = len(self._checked_entries())
        self.pl_count.setText(f"{n} من {self.plist.count()} محدّد")
        self.ok.setEnabled(n > 0)

    def _start_probe(self):
        u = self._first_url()
        if not u:
            return
        self.btn_probe.setEnabled(False)
        self.btn_probe.setText("جارٍ الفحص…")
        self._probe.run(u)

    def _on_probe(self, info, err):
        self.btn_probe.setEnabled(True)
        self.btn_probe.setText("فحص الرابط")
        if info is None:
            self.info.setText(f"تعذّر الفحص: {err}")
            return
        if info.get("playlist"):
            self._show_playlist(info)
            return
        self._hide_playlist()
        extra = " · ".join(x for x in (_fmt_dur(info["duration"]), info["uploader"]) if x)
        self.info.setText(f"{info['title']}" + (f"\n{extra}" if extra else ""))
        if info["is_live"]:
            self.info.setText(self.info.text() + "\nبث مباشر: غير مدعوم")
        cur = self.quality.currentData()
        self.quality.clear()
        self.quality.addItem("أفضل جودة متاحة", 0)
        for h in info["heights"]:
            self.quality.addItem(f"{h}p", h)
        i = self.quality.findData(cur)
        self.quality.setCurrentIndex(max(0, i))
        if self._want_h:
            self._select_height(self._want_h)

    def _select_height(self, want):
        """يختار الجودة المطلوبة، وإن لم تتوفر فأقرب جودة أقل منها."""
        best = None
        for i in range(self.quality.count()):
            h = self.quality.itemData(i) or 0
            if h and h <= want and (best is None or h > (self.quality.itemData(best) or 0)):
                best = i
        if best is None and self.quality.findData(want) < 0:
            self.quality.addItem(f"{want}p", want)
            best = self.quality.count() - 1
        self.quality.setCurrentIndex(best if best is not None else 0)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "اختر مجلد الحفظ", self.folder.text())
        if d:
            self.folder.setText(d)

    def values(self):
        urls = [u.strip() for u in self.urls.toPlainText().splitlines() if u.strip()]
        if self._entries:
            urls = [e["url"] for e in self._checked_entries()]
        langs = [x.strip() for x in str(self.config["sub_langs"]).split(",") if x.strip()]
        return {"urls": urls, "folder": self.folder.text().strip(),
                "subs": langs if (self._video and self.subs.isChecked()) else [],
                "later": self.later.isChecked(), "playlist": bool(self._entries),
                "kind": "media" if self._video else "http",
                "height": self.quality.currentData() or None,
                "audio_only": self.audio.isChecked() and self._video}


class _Two(QSpinBox):
    def textFromValue(self, v):
        return f"{v:02d}"


class HMEdit(QWidget):
    """حقل وقت HH:mm بخانتين. QTimeEdit يعكس الأرقام (00:02) في واجهة RTL فنبنيه بأنفسنا."""

    def __init__(self, t=None, parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.LeftToRight)
        self.h, self.m = _Two(), _Two()
        self.h.setRange(0, 23)
        self.m.setRange(0, 59)
        self.m.setWrapping(True)
        self.h.setWrapping(True)
        for sp in (self.h, self.m):
            sp.setAlignment(Qt.AlignCenter)
            sp.setFixedWidth(72)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        colon = QLabel(":")
        lay.addWidget(self.h)
        lay.addWidget(colon)
        lay.addWidget(self.m)
        lay.addStretch(1)
        if t:
            self.setTime(t)

    def setTime(self, t):
        self.h.setValue(t.hour())
        self.m.setValue(t.minute())

    def time(self):
        return QTime(self.h.value(), self.m.value())


def _time(text):
    t = QTime.fromString(text, "HH:mm")
    return t if t.isValid() else QTime(0, 0)


def _form():
    f = QFormLayout()
    f.setSpacing(12)
    f.setLabelAlignment(Qt.AlignRight)
    return f


def _page(form):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(6, 16, 6, 6)
    lay.addLayout(form) if not isinstance(form, QWidget) else lay.addWidget(form)
    lay.addStretch(1)
    return w


class ColorSwatch(QPushButton):
    """زر دائري بلون معيّن."""

    def __init__(self, color, size=30, parent=None):
        super().__init__(parent)
        self.color = color
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.set_selected(False)

    def set_selected(self, on):
        c = theme.C
        ring = c["text"] if on else "transparent"
        self.setStyleSheet(f"QPushButton{{background:{self.color};border-radius:{self.width() // 2}px;"
                           f"border:2px solid {ring};}}")


class SettingsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.ready_path = None
        self.config = config
        self._orig = {k: config[k] for k in ("theme", "dark_base", "accent", "ok_color", "err_color")}
        self._sel = dict(self._orig)
        self.setWindowTitle("الإعدادات")
        self.setMinimumWidth(580)

        tabs = QTabWidget()
        tabs.addTab(self._general(), "عام")
        tabs.addTab(self._schedule(), "الجدولة")
        tabs.addTab(self._video(), "الفيديو")
        tabs.addTab(self._look(), "المظهر")
        tabs.addTab(self._browsers(), "المتصفحات")
        tabs.addTab(self._app_update(), "التحديث")
        self.tabs = tabs

        ok = QPushButton("حفظ")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("إلغاء")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(ok)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(12)
        t = QLabel("الإعدادات")
        t.setObjectName("PageTitle")
        lay.addWidget(t)
        lay.addWidget(tabs)
        lay.addLayout(btns)

    # ------------------------------------------------------------ عام
    def _general(self):
        c = self.config
        self.folder = QLineEdit(c["download_dir"])
        self.folder.setLayoutDirection(Qt.LeftToRight)
        browse = QPushButton("استعراض")
        browse.setObjectName("Ghost")
        browse.clicked.connect(lambda: self._browse(self.folder))
        frow = QHBoxLayout()
        frow.addWidget(self.folder, 1)
        frow.addWidget(browse)
        self.conns = QSpinBox()
        self.conns.setRange(1, 32)
        self.conns.setValue(c["connections"])
        self.conc = QSpinBox()
        self.conc.setRange(1, 10)
        self.conc.setValue(c["max_concurrent"])
        self.limit = QSpinBox()
        self.limit.setRange(0, 1_000_000)
        self.limit.setSuffix(" KB/s")
        self.limit.setSpecialValueText("بلا حد")
        self.limit.setValue(c["speed_limit_kb"])
        self.subfolders = QCheckBox("ترتيب الملفات تلقائيًا في مجلدات (فيديو، مستندات، …)")
        self.subfolders.setChecked(c["subfolders"])
        self.dups = QCheckBox("تنبيه عند تحميل رابط أو ملف موجود سابقًا")
        self.dups.setChecked(c["warn_duplicates"])
        self.clip = QCheckBox("التقاط الروابط المنسوخة تلقائيًا")
        self.clip.setChecked(c["clipboard_watch"])
        self.notify = QCheckBox("إشعار عند اكتمال التحميل")
        self.notify.setChecked(c["notify"])
        self.autostart = QCheckBox("تشغيل سيل تلقائيًا مع ويندوز (يعمل في الخلفية)")
        self.autostart.setChecked(setup_tools.autostart_enabled())
        self.autostart.setVisible(setup_tools.autostart_supported())
        self.watch = QCheckBox("مراقبة مجلد: أي ملف .txt أو .url فيه روابط يُحمَّل تلقائيًا")
        self.watch.setChecked(c["watch_enabled"])
        self.watch_dir = QLineEdit(c["watch_folder"])
        self.watch_dir.setLayoutDirection(Qt.LeftToRight)
        wb = QPushButton("استعراض")
        wb.setObjectName("Ghost")
        wb.clicked.connect(lambda: self._browse(self.watch_dir))
        wrow = QHBoxLayout()
        wrow.addWidget(self.watch_dir, 1)
        wrow.addWidget(wb)
        self.token = QLineEdit(c["token"])
        self.token.setReadOnly(True)
        self.token.setLayoutDirection(Qt.LeftToRight)
        cp = QPushButton("نسخ")
        cp.setObjectName("Ghost")
        cp.clicked.connect(lambda: QApplication.clipboard().setText(self.token.text()))
        trow = QHBoxLayout()
        trow.addWidget(self.token, 1)
        trow.addWidget(cp)
        f = _form()
        f.addRow("مجلد الحفظ", frow)
        f.addRow("الاتصالات لكل ملف", self.conns)
        f.addRow("التحميلات المتزامنة", self.conc)
        f.addRow("حدّ السرعة", self.limit)
        f.addRow("", self.subfolders)
        f.addRow("", self.dups)
        f.addRow("", self.clip)
        f.addRow("", self.notify)
        f.addRow("", self.autostart)
        f.addRow("", self.watch)
        f.addRow("المجلد المراقَب", wrow)
        f.addRow("رمز الإضافة", trow)
        return _page(f)

    # ------------------------------------------------------------ الجدولة
    def _schedule(self):
        c = self.config
        self.sched = QCheckBox("شغّل الطابور داخل هذه الفترة فقط")
        self.sched.setChecked(c["schedule_enabled"])
        self.s_from = HMEdit(_time(c["sched_start"]))
        self.s_to = HMEdit(_time(c["sched_stop"]))
        self.peak = QCheckBox("حدّ سرعة مختلف في ساعات معيّنة (مثل ساعات الدوام)")
        self.peak.setChecked(c["peak_enabled"])
        self.p_from = HMEdit(_time(c["peak_start"]))
        self.p_to = HMEdit(_time(c["peak_stop"]))
        self.p_kb = QSpinBox()
        self.p_kb.setRange(1, 1_000_000)
        self.p_kb.setSuffix(" KB/s")
        self.p_kb.setValue(c["peak_kb"])
        note = QLabel("إذا كانت نهاية الفترة قبل بدايتها فهي تعبر منتصف الليل (مثل 22:00 إلى 06:00). "
                      "ما تضيفه يدويًا يبدأ فورًا، وما تختار «ابدأ لاحقًا» له ينتظر الفترة.")
        note.setObjectName("Sub")
        note.setWordWrap(True)
        f = _form()
        f.addRow("", self.sched)
        f.addRow("من الساعة", self.s_from)
        f.addRow("إلى الساعة", self.s_to)
        f.addRow("", self.peak)
        f.addRow("من الساعة", self.p_from)
        f.addRow("إلى الساعة", self.p_to)
        f.addRow("الحدّ خلالها", self.p_kb)
        w = _page(f)
        w.layout().insertWidget(1, note)
        return w

    # ------------------------------------------------------------ الفيديو
    def _video(self):
        c = self.config
        self.vq = QComboBox()
        for h, t in QUALITIES:
            self.vq.addItem(t, h)
        self.vq.setCurrentIndex(max(0, self.vq.findData(int(c["video_quality"] or 0))))
        self.subs = QCheckBox("حمّل الترجمة مع الفيديو (SRT)")
        self.subs.setChecked(c["subtitles"])
        self.sub_langs = QLineEdit(c["sub_langs"])
        self.sub_langs.setPlaceholderText("ar,en")
        self.sub_langs.setLayoutDirection(Qt.LeftToRight)
        ff = find_ffmpeg(c["ffmpeg_path"])
        self.ffmpeg = QLineEdit(c["ffmpeg_path"])
        self.ffmpeg.setPlaceholderText(ff or "غير موجود")
        self.ffmpeg.setLayoutDirection(Qt.LeftToRight)
        ver = updater.current_version()
        self.ytver = QLabel(ltr(ver) if ver else "غير مثبّتة")
        self.upd = QPushButton("تحديث yt-dlp")
        self.upd.setObjectName("Ghost")
        self.upd.clicked.connect(self._update_ytdlp)
        self.upd_msg = QLabel("")
        self.upd_msg.setObjectName("Sub")
        self.upd_msg.setWordWrap(True)
        urow = QHBoxLayout()
        urow.addWidget(self.ytver)
        urow.addStretch(1)
        urow.addWidget(self.upd)
        f = _form()
        f.addRow("الجودة الافتراضية", self.vq)
        f.addRow("", self.subs)
        f.addRow("لغات الترجمة", self.sub_langs)
        f.addRow("مسار ffmpeg", self.ffmpeg)
        f.addRow("إصدار yt-dlp", urow)
        w = _page(f)
        w.layout().insertWidget(1, self.upd_msg)
        return w

    def _update_ytdlp(self):
        self.upd.setEnabled(False)
        self.upd_msg.setText("جارٍ التحقق…")

        def work():
            try:
                latest = updater.latest_version()
            except Exception as e:  # noqa: BLE001
                self._upd_res.emit(False, f"تعذّر الاتصال: {e}")
                return
            if latest == updater.current_version():
                self._upd_res.emit(True, "أنت على أحدث إصدار")
                return
            ok, msg = updater.update()
            self._upd_res.emit(ok, f"{msg} ({latest})")
        threading.Thread(target=work, daemon=True).start()

    _upd_res = Signal(bool, str)
    _app_res = Signal(object, str)
    _app_prog = Signal(float)

    # ------------------------------------------------------------ المتصفحات
    def _browsers(self):
        from PySide6.QtCore import QTimer
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(6, 16, 6, 6)
        lay.setSpacing(10)
        intro = QLabel("ثبّت إضافة سيل في أي متصفح مبني على Chromium. اضغط «تثبيت» بجانب المتصفح، "
                       "فنجهّز لك مجلد الإضافة ونفتح صفحة الإضافات فيه، ثم: فعّل «وضع المطوّر» ← "
                       "«تحميل إضافة غير مُحزّمة» ← اختر المجلد. هذا كل شيء: لا رمز ولا منفذ، وتتصل الإضافة بسيل مباشرة.")
        intro.setWordWrap(True)
        intro.setObjectName("Sub")
        lay.addWidget(intro)
        self._browser_rows = []
        for b in setup_tools.find_browsers():
            row = QHBoxLayout()
            name = QLabel(b["name"] + ("" if b["exe"] else "  (غير مثبّت)"))
            btn = QPushButton("تثبيت الإضافة")
            btn.setObjectName("Ghost")
            btn.setEnabled(bool(b["exe"]))
            btn.clicked.connect(lambda _=False, br=b: self._install_in(br))
            row.addWidget(name, 1)
            row.addWidget(btn)
            lay.addLayout(row)
            self._browser_rows.append((b, name, btn))
        self.ext_msg = QLabel("")
        self.ext_msg.setWordWrap(True)
        self.ext_msg.setObjectName("Sub")
        self.ext_state = QLabel("")
        self.ext_state.setWordWrap(True)
        btn_pair = QPushButton("إصلاح الاتصال")
        btn_pair.setObjectName("Primary")
        btn_pair.clicked.connect(self._pair_now)
        btn_diag = QPushButton("فحص الاتصال")
        btn_diag.setObjectName("Ghost")
        btn_diag.clicked.connect(self._diagnose)
        self.diag = QLabel("")
        self.diag.setWordWrap(True)
        self.diag.setTextInteractionFlags(Qt.TextSelectableByMouse)
        btn_folder = QPushButton("فتح مجلد الإضافة")
        btn_folder.setObjectName("Ghost")
        btn_folder.clicked.connect(self._open_ext_folder)
        r2 = QHBoxLayout()
        r2.addWidget(btn_folder)
        r2.addWidget(btn_diag)
        r2.addStretch(1)
        r2.addWidget(btn_pair)
        lay.addSpacing(6)
        lay.addWidget(self.ext_state)
        lay.addWidget(self.ext_msg)
        lay.addLayout(r2)
        lay.addWidget(self.diag)
        lay.addStretch(1)
        note = QLabel("متصفح Firefox غير مدعوم حاليًا.")
        note.setObjectName("Sub")
        lay.addWidget(note)
        self._diag_done.connect(self._on_diag)
        self._ext_timer = QTimer(self)
        self._ext_timer.timeout.connect(self._refresh_ext_state)
        self._ext_timer.start(1500)
        self._refresh_ext_state()
        return box

    def _refresh_ext_state(self):
        import time
        from . import server as sv
        now = time.time()
        seen, bad = sv.STATS["seen"], sv.STATS["bad"]
        if seen and now - seen < 75:
            who = f" ({sv.STATS['browser']})" if sv.STATS["browser"] else ""
            self.ext_state.setText(f"<b style='color:{theme.C['ok']}'>● الإضافة متصلة{who}</b>")
        elif bad and now - bad < 75:
            self.ext_state.setText(f"<b style='color:{theme.C['err']}'>● الإضافة تحاول الاتصال برمز خاطئ</b>"
                                   " — اضغط «إصلاح الاتصال».")
        else:
            self.ext_state.setText("<b>● لم تتصل أي إضافة بعد</b> — ثبّتها من الأعلى، وإن لم تتصل اضغط «إصلاح الاتصال».")

    def _diagnose(self):
        """يعرض حالة تسجيل الجسر ثم يختبر ملف الجسر مباشرة دون متصفح."""
        ok_c, bad_c = theme.C["ok"], theme.C["err"]
        lines = []
        try:
            setup_tools.register_native_host(self.config)
        except Exception as e:  # noqa: BLE001
            lines.append(f"<span style='color:{bad_c}'>✘ فشل التسجيل: {e}</span>")
        for text, ok in setup_tools.native_status():
            lines.append(f"<span style='color:{ok_c if ok else bad_c}'>{'✔' if ok else '✘'} {text}</span>")
        self.diag.setText("<br>".join(lines) + "<br>جارٍ اختبار ملف الجسر…")
        self._diag_lines = lines

        def work():
            self._diag_done.emit(*setup_tools.self_test_host())
        threading.Thread(target=work, daemon=True).start()

    _diag_done = Signal(bool, str)

    def _on_diag(self, ok, msg):
        c = theme.C["ok"] if ok else theme.C["err"]
        self.diag.setText("<br>".join(self._diag_lines) +
                          f"<br><span style='color:{c}'>{'✔' if ok else '✘'} {msg}</span>")

    def _pair_now(self):
        from .server import open_pairing
        open_pairing(300)
        try:
            setup_tools.register_native_host(self.config)      # يعيد تسجيل الجسر عند المتصفحات
        except Exception as e:  # noqa: BLE001
            self.ext_msg.setText(f"تعذّر تسجيل الجسر: {e}")
            return
        self.ext_msg.setText("أعدت تسجيل الاتصال عند المتصفحات. أعد تشغيل المتصفح مرة واحدة ثم افتح نافذة الإضافة. "
                             "ستظهر حالة «متصلة» هنا خلال ثوانٍ.")

    def _install_in(self, browser):
        from .server import open_pairing
        try:
            path = setup_tools.export_extension(self.config)
            open_pairing(600)
            setup_tools.register_native_host(self.config)
            setup_tools.open_extensions_page(browser)
        except Exception as e:  # noqa: BLE001
            self.ext_msg.setText(f"تعذّر التثبيت: {e}")
            return
        self._ext_path = path
        self.ext_msg.setText(
            f"تم تجهيز الإضافة وفتح {browser['name']}. في صفحة الإضافات: فعّل «وضع المطوّر» ثم "
            f"«تحميل إضافة غير مُحزّمة» واختر المجلد:\n{path}")

    def _open_ext_folder(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        try:
            path = setup_tools.export_extension(self.config)
        except Exception as e:  # noqa: BLE001
            self.ext_msg.setText(f"تعذّر تجهيز الإضافة: {e}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ------------------------------------------------------------ تحديث البرنامج
    def _app_update(self):
        c = self.config
        self.repo = QLineEdit(c["update_repo"])
        self.repo.setPlaceholderText("username/repo")
        self.repo.setLayoutDirection(Qt.LeftToRight)
        self.auto_chk = QCheckBox("افحص التحديثات عند تشغيل البرنامج")
        self.auto_chk.setChecked(c["auto_update_check"])
        self.app_msg = QLabel("")
        self.app_msg.setObjectName("Sub")
        self.app_msg.setWordWrap(True)
        self.btn_check = QPushButton("تحقق الآن")
        self.btn_check.setObjectName("Ghost")
        self.btn_check.clicked.connect(self._check_app)
        self.btn_apply = QPushButton("حدّث الآن")
        self.btn_apply.setObjectName("Primary")
        self.btn_apply.setVisible(False)
        self.btn_apply.clicked.connect(self._apply_app)
        self._app_info = None
        self._app_res.connect(self._on_app_res)
        self._app_prog.connect(lambda p: self.app_msg.setText(f"جارٍ التنزيل… {int(p * 100)}%"))
        row = QHBoxLayout()
        row.addWidget(self.btn_check)
        row.addWidget(self.btn_apply)
        row.addStretch(1)
        f = _form()
        f.addRow("الإصدار الحالي", QLabel(ltr(__version__)))
        f.addRow("مستودع GitHub", self.repo)
        f.addRow("", self.auto_chk)
        w = _page(f)
        w.layout().insertLayout(1, row)
        w.layout().insertWidget(2, self.app_msg)
        if not selfupdate.can_apply():
            self.app_msg.setText("التحديث التلقائي يعمل في Sayl.exe على ويندوز. "
                                 "من الشيفرة المصدرية سيفتح زر التحديث صفحة الإصدار.")
        return w

    def _check_app(self):
        repo = selfupdate.clean_repo(self.repo.text())
        self.btn_check.setEnabled(False)
        self.app_msg.setText("جارٍ التحقق…")

        def work():
            try:
                self._app_res.emit(selfupdate.check(repo), "")
            except Exception as e:  # noqa: BLE001
                self._app_res.emit(None, str(e))
        threading.Thread(target=work, daemon=True).start()

    def _on_app_res(self, info, err):
        self.btn_check.setEnabled(True)
        self.btn_apply.setEnabled(True)
        if info is None:
            self.app_msg.setText(err)
            return
        if "ready" in info:
            self.ready_path = info["ready"]
            self.app_msg.setText("تم التنزيل. سيُغلق البرنامج ويُعاد تشغيله بالإصدار الجديد.")
            self.apply()
            self.done(QDialog.Accepted)
            return
        if err:
            self.app_msg.setText(err)
            return
        self._app_info = info
        if not info["newer"]:
            self.app_msg.setText("أنت على أحدث إصدار.")
            self.btn_apply.setVisible(False)
            return
        self.app_msg.setText(f"يتوفر إصدار {info['latest']}.\n{info['notes']}")
        self.btn_apply.setVisible(True)
        self.btn_apply.setText("حدّث الآن" if selfupdate.can_apply() else "افتح صفحة الإصدار")

    def _apply_app(self):
        info = self._app_info
        if not info:
            return
        if not selfupdate.can_apply() or not info["asset_url"]:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(info["page"]))
            return
        self.btn_apply.setEnabled(False)
        self.btn_check.setEnabled(False)

        def work():
            try:
                path = selfupdate.download(info, self._app_prog.emit)
                self._app_res.emit({"ready": path}, "")
            except Exception as e:  # noqa: BLE001
                self._app_res.emit(None, str(e))
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ المظهر
    def _look(self):
        self.upd_sig_connected = False
        self._upd_res.connect(lambda ok, m: (self.upd_msg.setText(m), self.upd.setEnabled(True)))
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(6, 16, 6, 6)
        lay.setSpacing(10)

        lay.addWidget(self._cap("السمة"))
        self.base_combo = QComboBox()
        for k, t in theme.BASE_LABELS.items():
            self.base_combo.addItem(t, k)
        cur = "light" if self._sel["theme"] == "light" else self._sel["dark_base"]
        self.base_combo.setCurrentIndex(max(0, self.base_combo.findData(cur)))
        self.base_combo.currentIndexChanged.connect(self._base_changed)
        lay.addWidget(self.base_combo)

        lay.addWidget(self._cap("لون الإبراز"))
        self.accent_row, self.accent_sw = self._swatches(theme.ACCENTS, "accent")
        lay.addLayout(self.accent_row)
        lay.addLayout(self._custom_row("accent", "لون مخصص…"))

        lay.addWidget(self._cap("لون النجاح (مكتمل)"))
        self.ok_row, self.ok_sw = self._swatches(["#4FD1A1", "#5FD39A", "#7BD88F", "#4DB6FF"], "ok_color")
        lay.addLayout(self.ok_row)
        lay.addLayout(self._custom_row("ok_color", "لون مخصص…"))

        lay.addWidget(self._cap("لون الخطأ"))
        self.err_row, self.err_sw = self._swatches(["#FF6B6B", "#FF5C8A", "#FF8A4C", "#E5484D"], "err_color")
        lay.addLayout(self.err_row)
        lay.addLayout(self._custom_row("err_color", "لون مخصص…"))

        lay.addWidget(self._cap("الخط"))
        from . import fonts
        found = fonts.thmanyah_families()
        ftxt = ("يُستخدم الآن خط: " + "، ".join(found)) if found else (
            "خط «ثمانية» غير مثبّت. حمّله من صفحته الرسمية (ترخيصه يمنع تضمينه في البرنامج) ثم ثبّته "
            "في ويندوز أو ضع ملفاته في مجلد الخطوط، وأعد تشغيل سيل.")
        fl = QLabel(ftxt)
        fl.setObjectName("Sub")
        fl.setWordWrap(True)
        lay.addWidget(fl)
        frow = QHBoxLayout()
        b1 = QPushButton("فتح مجلد الخطوط")
        b1.setObjectName("Ghost")
        b1.clicked.connect(lambda: (os.makedirs(fonts.FONT_DIR, exist_ok=True),
                                    QDesktopServices.openUrl(QUrl.fromLocalFile(fonts.FONT_DIR))))
        b3 = QPushButton("اختيار مجلد الخط…")
        b3.setObjectName("Ghost")
        b3.clicked.connect(lambda: self._pick_font_dir(fl))
        b2 = QPushButton("صفحة الخط الرسمية")
        b2.setObjectName("Ghost")
        b2.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(fonts.OFFICIAL_PAGE)))
        frow.addWidget(b3)
        frow.addWidget(b1)
        frow.addWidget(b2)
        frow.addStretch(1)
        lay.addLayout(frow)

        reset = QPushButton("استعادة الألوان الافتراضية")
        reset.setObjectName("Ghost")
        reset.clicked.connect(self._reset_colors)
        self.reset_btn = reset
        lay.addWidget(reset, 0, Qt.AlignRight)
        lay.addStretch(1)
        return box

    def _pick_font_dir(self, label):
        d = QFileDialog.getExistingDirectory(self, "اختر المجلد الذي فيه ملفات otf أو ttf للخط",
                                             self.config["font_dir"] or os.path.expanduser("~"))
        if d:
            self.config["font_dir"] = d
            self.config.save()
            label.setText("تم حفظ المجلد. أعد تشغيل سيل ليُطبَّق الخط.")

    def _cap(self, text):
        l = QLabel(text)
        l.setObjectName("NavHead")
        return l

    def _swatches(self, colors, key):
        row = QHBoxLayout()
        row.setSpacing(8)
        sws = []
        for col in colors:
            b = ColorSwatch(col)
            b.clicked.connect(lambda _=False, c=col, k=key: self._pick(k, c))
            row.addWidget(b)
            sws.append(b)
        row.addStretch(1)
        return row, sws

    def _custom_row(self, key, label):
        b = QPushButton(label)
        b.setObjectName("Ghost")
        b.clicked.connect(lambda: self._custom(key))
        row = QHBoxLayout()
        row.addWidget(b)
        row.addStretch(1)
        return row

    def _custom(self, key):
        start = QColor(self._sel[key] or theme.C["accent" if key == "accent" else key.split("_")[0]])
        col = QColorDialog.getColor(start, self, "اختر لونًا")
        if col.isValid():
            self._pick(key, col.name().upper())

    def _pick(self, key, color):
        self._sel[key] = color
        self._preview()

    def _base_changed(self):
        b = self.base_combo.currentData()
        if b == "light":
            self._sel["theme"] = "light"
        else:
            self._sel["theme"], self._sel["dark_base"] = "dark", b
        self._preview()

    def _reset_colors(self, *_):
        """يعيد السمة والألوان كلها إلى الافتراضي (داكن + إبراز كهرماني)."""
        self._sel.update(theme="dark", dark_base="dark", accent="#FFB020", ok_color="", err_color="")
        self.base_combo.blockSignals(True)
        self.base_combo.setCurrentIndex(max(0, self.base_combo.findData("dark")))
        self.base_combo.blockSignals(False)
        self._preview()

    def _preview(self):
        """معاينة مباشرة؛ تُلغى عند الإلغاء."""
        s = self._sel
        base = "light" if s["theme"] == "light" else s["dark_base"]
        QApplication.instance().setStyleSheet(
            theme.set_theme(base, s["accent"], s["ok_color"], s["err_color"]))
        for sws, key in ((self.accent_sw, "accent"), (self.ok_sw, "ok_color"), (self.err_sw, "err_color")):
            cur = (s[key] or theme.C[{"accent": "accent", "ok_color": "ok", "err_color": "err"}[key]]).upper()
            for b in sws:
                b.set_selected(b.color.upper() == cur)
        p = self.parent()
        if p is not None and hasattr(p, "retint"):
            p.retint()

    def showEvent(self, e):
        super().showEvent(e)
        self._preview()

    def reject(self):
        self._sel = dict(self._orig)
        self._preview()
        super().reject()

    # ------------------------------------------------------------ حفظ
    def _browse(self, edit):
        d = QFileDialog.getExistingDirectory(self, "اختر مجلدًا", edit.text())
        if d:
            edit.setText(d)

    def apply(self):
        c = self.config
        c["download_dir"] = self.folder.text().strip() or c["download_dir"]
        c["connections"] = self.conns.value()
        c["max_concurrent"] = self.conc.value()
        c["speed_limit_kb"] = self.limit.value()
        c["subfolders"] = self.subfolders.isChecked()
        c["warn_duplicates"] = self.dups.isChecked()
        c["clipboard_watch"] = self.clip.isChecked()
        c["notify"] = self.notify.isChecked()
        c["watch_enabled"] = self.watch.isChecked()
        c["watch_folder"] = self.watch_dir.text().strip()
        c["schedule_enabled"] = self.sched.isChecked()
        c["sched_start"] = self.s_from.time().toString("HH:mm")
        c["sched_stop"] = self.s_to.time().toString("HH:mm")
        c["peak_enabled"] = self.peak.isChecked()
        c["peak_start"] = self.p_from.time().toString("HH:mm")
        c["peak_stop"] = self.p_to.time().toString("HH:mm")
        c["peak_kb"] = self.p_kb.value()
        c["video_quality"] = self.vq.currentData() or 0
        c["subtitles"] = self.subs.isChecked()
        c["sub_langs"] = self.sub_langs.text().strip() or "ar,en"
        c["ffmpeg_path"] = self.ffmpeg.text().strip()
        if setup_tools.autostart_supported():
            try:
                setup_tools.set_autostart(self.autostart.isChecked())
            except OSError:
                pass
        c["update_repo"] = selfupdate.clean_repo(self.repo.text())
        c["auto_update_check"] = self.auto_chk.isChecked()
        for k, v in self._sel.items():
            c[k] = v


class WelcomeDialog(QDialog):
    """نافذة الترحيب عند أول تشغيل: ثلاث خطوات واضحة لمن لا يعرف شيئًا."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("مرحبًا بك في سيل")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 22)
        lay.setSpacing(12)
        t = QLabel("أهلًا بك في سيل 👋")
        t.setObjectName("PageTitle")
        lay.addWidget(t)
        intro = QLabel("مدير تحميل سريع يقسّم الملف إلى أجزاء ويلتقط الفيديوهات من المتصفح. "
                       "ابدأ بثلاث خطوات:")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        for n, txt in (("1", "ألصق أي رابط في النافذة الرئيسية، أو انسخه وسيقترح عليك سيل تحميله."),
                       ("2", "لالتقاط الفيديوهات من Chrome ثبّت الإضافة بالزر أدناه (دقيقة واحدة)."),
                       ("3", "في الإعدادات تجد الجدولة والألوان والتحديث التلقائي.")):
            row = QLabel(f"<b>{n}.</b>  {txt}")
            row.setWordWrap(True)
            lay.addWidget(row)
        self.msg = QLabel("")
        self.msg.setObjectName("Sub")
        self.msg.setWordWrap(True)
        self.btn_ext = QPushButton("تثبيت إضافة Chrome")
        self.btn_ext.setObjectName("Primary")
        self.btn_ext.clicked.connect(self.install_extension)
        done = QPushButton("ابدأ الاستخدام")
        done.setObjectName("Ghost")
        done.clicked.connect(self.accept)
        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(done)
        btns.addWidget(self.btn_ext)
        lay.addSpacing(6)
        lay.addWidget(self.msg)
        lay.addLayout(btns)

    def install_extension(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        try:
            path = setup_tools.export_extension(self.config)
            setup_tools.register_native_host(self.config)
        except Exception as e:  # noqa: BLE001
            self.msg.setText(f"تعذّر تجهيز الإضافة: {e}")
            return
        from .server import open_pairing
        open_pairing(600)
        QApplication.clipboard().setText("chrome://extensions")
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        self.msg.setText(
            "جهّزت الإضافة وفتحت مجلدها. الآن:\n"
            "• افتح Chrome والصق في شريط العنوان: chrome://extensions (نسخته لك)\n"
            "• فعّل «وضع المطوّر» من الأعلى\n"
            "• اضغط «تحميل إضافة غير مُحزّمة» واختر المجلد الذي فُتح\n"
            "لا حاجة لإدخال أي رمز: الإضافة مرتبطة تلقائيًا.")
