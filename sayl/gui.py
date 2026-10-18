"""النافذة الرئيسية."""
import hashlib
import os
import sys
import threading

from PySide6.QtCore import QLockFile, QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QActionGroup, QDesktopServices, QIcon
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QStackedWidget, QSystemTrayIcon, QToolButton,
                               QVBoxLayout, QWidget)

from . import icons, power, selfupdate, setup_tools, theme
from .categories import CATEGORIES, EXT_TO_CAT, category_of, ext_of
from .config import Config
from .dialogs import AddDialog, SettingsDialog, WelcomeDialog
from .engine import COMPLETED, DOWNLOADING, ERROR, PAUSED, PROBING, QUEUED
from .manager import DownloadManager, DuplicateError
from .media import is_media_url
from .server import ApiServer, open_pairing
from .widgets import (CAT_ICON, DownloadRow, ElidedLabel, FileTile, IconButton, NavButton,
                      SpeedGraph, fmt_size)

CAT_NAV = [(k, CAT_ICON[k], l) for k, l, _ in CATEGORIES if k != "other"] + \
          [("other", "doc", "أخرى")]
TITLES = {"all": "كل التحميلات", "active": "جارٍ التحميل", "done": "المكتملة"}
TITLES.update({k: l for k, _, l in CAT_NAV})


class Bridge(QObject):
    added = Signal(str)          # من خيط الخادم إلى خيط الواجهة
    hashed = Signal(str, str)    # (اسم الملف، SHA-256)


def looks_downloadable(text):
    t = text.strip()
    if "\n" in t or " " in t or not t.lower().startswith(("http://", "https://")):
        return False
    if is_media_url(t):
        return True
    path = t.split("?")[0].split("#")[0]
    return ext_of(path) in EXT_TO_CAT


class MainWindow(QMainWindow):
    def __init__(self, manager, config, bridge):
        super().__init__()
        self.manager, self.config, self.bridge = manager, config, bridge
        self.setWindowTitle("سيل")
        self.resize(1140, 700)
        self.setMinimumSize(940, 640)
        self.setAcceptDrops(True)
        self.setWindowIcon(QIcon(icons.logo_pixmap(128)))
        self.filter = "all"
        self.rows, self._ids = {}, None
        self.selected = None
        self._done_seen = {d.id for d in manager.snapshot() if d.state == COMPLETED}
        self._clip_last = ""
        self._ignore_clip = ""
        self._banner_url = None
        self._icon_buttons = []

        root = QWidget()
        root.setObjectName("Content")
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_sidebar())
        lay.addWidget(self._build_content(), 1)

        self.tray = QSystemTrayIcon(QIcon(icons.logo_pixmap(64)), self)
        tm = QMenu()
        tm.addAction("إظهار سيل", self.show_normal)
        tm.addSeparator()
        tm.addAction("خروج", self.quit)
        self.tray.setContextMenu(tm)
        self.tray.activated.connect(lambda r: self.show_normal() if r == QSystemTrayIcon.Trigger else None)
        self.tray.setToolTip("سيل")
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

        QApplication.clipboard().dataChanged.connect(self._clip_changed)
        bridge.hashed.connect(self._on_hash)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)
        self.retint()
        self.refresh()

    # ------------------------------------------------------------ البناء
    def _build_sidebar(self):
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(252)
        v = QVBoxLayout(side)
        v.setContentsMargins(14, 18, 14, 14)
        v.setSpacing(2)

        brand = QHBoxLayout()
        brand.setContentsMargins(8, 0, 8, 8)
        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(76).scaled(38, 38, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        word = QLabel("سيل")
        word.setObjectName("Wordmark")
        brand.addWidget(logo)
        brand.addSpacing(8)
        brand.addWidget(word)
        brand.addStretch(1)
        v.addLayout(brand)

        self.navs = {}
        for key, ic, text in (("all", "all", "الكل"), ("active", "down", "جارٍ التحميل"),
                              ("done", "check", "مكتمل")):
            v.addWidget(self._nav(key, ic, text))
        v.addSpacing(12)
        head = QLabel("الأنواع")
        head.setObjectName("NavHead")
        v.addWidget(head)
        v.addSpacing(2)
        for key, ic, text in CAT_NAV:
            v.addWidget(self._nav(key, ic, text))
        v.addStretch(1)

        card = QFrame()
        card.setObjectName("Card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(16, 12, 16, 0)
        cv.setSpacing(0)
        cap = QLabel("السرعة الآن")
        cap.setObjectName("Sub")
        self.big = QLabel("0 KB/s")
        self.big.setObjectName("BigSpeed")
        self.big.setLayoutDirection(Qt.LeftToRight)
        self.big.setAlignment(Qt.AlignRight)
        self.graph = SpeedGraph()
        cv.addWidget(cap)
        cv.addWidget(self.big)
        cv.addSpacing(4)
        cv.addWidget(self.graph)
        v.addWidget(card)
        v.addSpacing(8)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(4, 0, 4, 0)
        self.b_theme = self._ibtn("sun", "تبديل السمة", self.toggle_theme)
        self.b_set = self._ibtn("gear", "الإعدادات", self.settings)
        bottom.addWidget(self.b_set)
        bottom.addWidget(self.b_theme)
        bottom.addStretch(1)
        self.limit_lbl = QLabel("")
        self.limit_lbl.setObjectName("Sub")
        bottom.addWidget(self.limit_lbl)
        v.addLayout(bottom)
        self.navs["all"].setChecked(True)
        return side

    def _nav(self, key, ic, text):
        b = NavButton(key, ic, text)
        b.clicked.connect(lambda _=False, k=key: self.set_filter(k))
        self.navs[key] = b
        return b

    def _ibtn(self, name, tip, slot):
        b = IconButton(name, tip)
        b.clicked.connect(slot)
        self._icon_buttons.append(b)
        return b

    def _build_content(self):
        w = QWidget()
        w.setObjectName("Content")
        v = QVBoxLayout(w)
        v.setContentsMargins(30, 26, 30, 18)
        v.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(10)
        tcol = QVBoxLayout()
        tcol.setSpacing(2)
        self.title = QLabel(TITLES["all"])
        self.title.setObjectName("PageTitle")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("Sub")
        tcol.addWidget(self.title)
        tcol.addWidget(self.subtitle)
        head.addLayout(tcol)
        head.addStretch(1)
        self.search = QLineEdit()
        self.search.setObjectName("Search")
        self.search.setPlaceholderText("بحث في التحميلات")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.refresh(force=True))
        self.search_act = self.search.addAction(icons.icon("search", theme.C["sub"], 18),
                                                QLineEdit.LeadingPosition)
        head.addWidget(self.search)
        add = QPushButton("  إضافة رابط")
        add.setObjectName("Primary")
        add.setCursor(Qt.PointingHandCursor)
        add.setIcon(icons.icon("plus", theme.C["on_accent"], 18))
        add.setIconSize(QSize(18, 18))
        add.clicked.connect(lambda: self.open_add())
        self.b_add = add
        vid = QPushButton("  التقاط فيديو")
        vid.setObjectName("Ghost")
        vid.setCursor(Qt.PointingHandCursor)
        vid.setIconSize(QSize(18, 18))
        vid.clicked.connect(lambda: self.open_add(video=True))
        self.b_vid = vid
        more = QToolButton()
        more.setObjectName("Icon")
        more.setFixedSize(38, 38)
        more.setCursor(Qt.PointingHandCursor)
        more.setPopupMode(QToolButton.InstantPopup)
        more.setIconSize(QSize(18, 18))
        mm = QMenu(more)
        mm.addAction("إيقاف الكل", self.manager.pause_all)
        mm.addAction("استئناف الكل", self.manager.resume_all)
        mm.addSeparator()
        mm.addAction("حذف المكتملة من القائمة", self.manager.clear_completed)
        mm.addSeparator()
        mm.addAction("تثبيت إضافة Chrome…", self.show_welcome)
        mm.addSeparator()
        after = mm.addMenu("بعد انتهاء الطابور")
        self._after_acts = {}
        grp = QActionGroup(after)
        for key, text in (("none", "لا شيء"), ("quit", "إغلاق سيل"), ("shutdown", "إطفاء الجهاز")):
            a = after.addAction(text)
            a.setCheckable(True)
            a.setActionGroup(grp)
            a.setChecked(self.config["after_all"] == key)
            a.triggered.connect(lambda _=False, k=key: self._set_after(k))
            self._after_acts[key] = a
        self._had_work = False
        more.setMenu(mm)
        self.b_more = more
        head.addWidget(add)
        head.addWidget(vid)
        head.addWidget(more)
        v.addLayout(head)

        # لافتة الحافظة
        self.banner = QFrame()
        self.banner.setObjectName("Banner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(14, 8, 8, 8)
        bl.setSpacing(10)
        self.banner_ico = QLabel()
        self.banner_text = ElidedLabel(Qt.ElideMiddle)
        self.banner_go = QPushButton("تحميل")
        self.banner_go.setObjectName("Primary")
        self.banner_go.setCursor(Qt.PointingHandCursor)
        self.banner_go.clicked.connect(self._banner_download)
        self.banner_x = IconButton("close", "تجاهل", 32, 16)
        self.banner_x.clicked.connect(self.banner.hide)
        self._icon_buttons.append(self.banner_x)
        bl.addWidget(self.banner_ico)
        bl.addWidget(self.banner_text, 1)
        bl.addWidget(self.banner_go)
        bl.addWidget(self.banner_x)
        self.banner.hide()
        self.banner_timer = QTimer(self)
        self.banner_timer.setSingleShot(True)
        self.banner_timer.timeout.connect(self.banner.hide)
        v.addWidget(self.banner)

        self.stack = QStackedWidget()
        self.list = QListWidget()
        self.list.setSpacing(7)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.setSelectionMode(QListWidget.NoSelection)
        self.list.setFocusPolicy(Qt.NoFocus)
        self.stack.addWidget(self.list)
        self.stack.addWidget(self._build_empty())
        v.addWidget(self.stack, 1)
        return w

    def _build_empty(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignCenter)
        v.setSpacing(10)
        tile = FileTile(76, "down")
        tile.set_category("archive")
        self.empty_tile = tile
        t = QLabel("لا توجد تحميلات هنا")
        t.setObjectName("PageTitle")
        t.setAlignment(Qt.AlignCenter)
        self.empty_sub = QLabel("ألصق رابطًا، أو اسحبه إلى النافذة، أو انسخه وسيظهر لك اقتراح تلقائي.")
        self.empty_sub.setObjectName("Sub")
        self.empty_sub.setAlignment(Qt.AlignCenter)
        btn = QPushButton("إضافة رابط")
        btn.setObjectName("Primary")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self.open_add())
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn)
        row.addStretch(1)
        v.addWidget(tile, 0, Qt.AlignHCenter)
        v.addSpacing(6)
        v.addWidget(t)
        v.addWidget(self.empty_sub)
        v.addSpacing(8)
        v.addLayout(row)
        return w

    # ------------------------------------------------------------ السمة
    def retint(self):
        c = theme.C
        for b in self._icon_buttons:
            b.retint()
        self.b_theme.set_name("sun" if theme.NAME == "dark" else "moon")
        self.b_add.setIcon(icons.icon("plus", c["on_accent"], 18))
        self.b_vid.setIcon(icons.icon("video", c["text"], 18))
        self.b_more.setIcon(icons.icon("more", c["sub"], 18))
        self.banner_ico.setPixmap(icons.pixmap("clip", c["accent"], 20))
        self.search_act.setIcon(icons.icon("search", c["sub"], 18))
        for n in self.navs.values():
            n.retint()
        for r in self.rows.values():
            r.retint()
        self.graph.update()
        self.empty_tile.update()

    def toggle_theme(self):
        name = "light" if theme.NAME == "dark" else "dark"
        self.config["theme"] = name
        QApplication.instance().setStyleSheet(theme.apply_config(self.config))
        self.config.save()
        self.retint()

    # ------------------------------------------------------------ التصفية
    def set_filter(self, key):
        self.filter = key
        for k, b in self.navs.items():
            b.setChecked(k == key)
        self.title.setText(TITLES.get(key, ""))
        self.refresh(force=True)

    def _match(self, d):
        f = self.filter
        if f == "all":
            return True
        if f == "active":
            return d.state != COMPLETED
        if f == "done":
            return d.state == COMPLETED
        return category_of(d.filename or "", d.kind) == f

    def _visible(self):
        q = self.search.text().strip().lower()
        out = []
        for d in self.manager.snapshot():
            if not self._match(d):
                continue
            if q and q not in ((d.filename or "") + " " + d.url).lower():
                continue
            out.append(d)
        return out

    # ------------------------------------------------------------ التحديث
    def refresh(self, force=False):
        items = self._visible()
        ids = [d.id for d in items]
        if force or ids != self._ids:
            self._rebuild(items)
        else:
            for d in items:
                self.rows[d.id].update_from(d)
        self.stack.setCurrentIndex(0 if items else 1)
        if not items:
            filtered = bool(self.search.text().strip()) or self.filter != "all"
            self.empty_sub.setText("لا نتائج مطابقة. جرّب تغيير البحث أو التصنيف." if filtered else
                                   "ألصق رابطًا، أو اسحبه إلى النافذة، أو انسخه وسيظهر لك اقتراح تلقائي.")

        snap = self.manager.snapshot()
        counts = {"all": len(snap), "active": sum(1 for d in snap if d.state != COMPLETED),
                  "done": sum(1 for d in snap if d.state == COMPLETED)}
        for d in snap:
            k = category_of(d.filename or "", d.kind)
            counts[k] = counts.get(k, 0) + 1
        for k, b in self.navs.items():
            b.set_count(counts.get(k, 0))
        running = sum(1 for d in snap if d.state in (DOWNLOADING, PROBING))
        sub = f"{len(items)} عنصر" + (f" · {running} قيد التحميل" if running else "")
        if self.config["schedule_enabled"] and not self.manager.schedule_open():
            sub += f" · الطابور ينتظر الساعة {self.config['sched_start']}"
        self.subtitle.setText(sub)
        for ev in self.manager.pop_events():
            self.show_banner(ev, None, 8)
        self._after_queue()

        speed = self.manager.total_speed()
        self.big.setText(fmt_size(speed) + "/s")
        self.graph.set_values(list(self.manager.history)[-89:] + [speed])
        lim = self.manager.effective_limit_kb()
        self.limit_lbl.setText(f"الحدّ {fmt_size(lim * 1024)}/s" if lim else "بلا حدّ للسرعة")

        for d in snap:
            if d.state == COMPLETED and d.id not in self._done_seen:
                self._done_seen.add(d.id)
                if self.config["notify"] and self.tray.isVisible():
                    self.tray.showMessage("اكتمل التحميل", d.filename or d.url,
                                          QSystemTrayIcon.Information, 4000)

    def _rebuild(self, items):
        bar = self.list.verticalScrollBar().value()
        self.list.clear()
        self.rows = {}
        for d in items:
            row = DownloadRow(d)
            row.clicked.connect(self._select)
            row.toggled.connect(self._toggle)
            row.open_folder.connect(self._open_folder)
            row.open_file.connect(self._open_file)
            row.delete.connect(self._delete)
            row.menu_requested.connect(self._row_menu)
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 88))
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            row.set_selected(d.id == self.selected)
            self.rows[d.id] = row
        self._ids = [d.id for d in items]
        self.list.verticalScrollBar().setValue(bar)

    # ------------------------------------------------------------ الإجراءات
    def _select(self, id):
        self.selected = id
        for i, r in self.rows.items():
            r.set_selected(i == id)

    def _toggle(self, id):
        d = self.manager.items.get(id)
        if not d:
            return
        if d.state in (DOWNLOADING, PROBING, QUEUED):
            self.manager.pause(id)
        else:
            self.manager.resume(id)

    def _path(self, d):
        return os.path.join(d.folder, d.filename) if d.filename else d.folder

    def _open_folder(self, id):
        d = self.manager.items.get(id)
        if d:
            QDesktopServices.openUrl(QUrl.fromLocalFile(d.folder))

    def _open_file(self, id):
        d = self.manager.items.get(id)
        if d and d.state == COMPLETED and os.path.exists(self._path(d)):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._path(d)))

    def _delete(self, id):
        d = self.manager.items.get(id)
        if not d:
            return
        done = d.state == COMPLETED
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("حذف")
        box.setText(f"حذف «{d.filename or d.url}»؟")
        keep = box.addButton("حذف من القائمة", QMessageBox.AcceptRole)
        wipe = box.addButton("حذف مع الملف", QMessageBox.DestructiveRole) if done else None
        box.addButton("إلغاء", QMessageBox.RejectRole)
        box.exec()
        c = box.clickedButton()
        if c is keep or (wipe is not None and c is wipe):
            self.manager.remove(id, delete_files=(c is wipe))

    def _row_menu(self, id, pos):
        d = self.manager.items.get(id)
        if not d:
            return
        m = QMenu(self)
        if d.state == COMPLETED:
            m.addAction("فتح الملف", lambda: self._open_file(id))
        m.addAction("فتح المجلد", lambda: self._open_folder(id))
        m.addAction("نسخ الرابط", lambda: self._copy(d.url))
        if d.state == COMPLETED:
            m.addAction("نسخ SHA-256 للملف", lambda: self._hash(d))
        m.addSeparator()
        if d.state != COMPLETED:
            running = d.state in (DOWNLOADING, PROBING, QUEUED)
            m.addAction("إيقاف مؤقت" if running else "استئناف", lambda: self._toggle(id))
        m.addAction("حذف", lambda: self._delete(id))
        m.exec(pos)

    def _copy(self, text):
        self._ignore_clip = text
        QApplication.clipboard().setText(text)

    def _hash(self, d):
        path = self._path(d)

        def work():
            h = hashlib.sha256()
            try:
                with open(path, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                self.bridge.hashed.emit(d.filename, h.hexdigest())
            except OSError:
                self.bridge.hashed.emit(d.filename, "")
        self.show_banner(f"جارٍ حساب SHA-256 لـ {d.filename}…", None)
        threading.Thread(target=work, daemon=True).start()

    def _on_hash(self, name, digest):
        if digest:
            self._copy(digest)
            self.show_banner(f"تم نسخ SHA-256 لـ {name}", None)
        else:
            self.show_banner(f"تعذّر قراءة الملف {name}", None)

    def _add(self, url, quiet=False, **kw):
        """يضيف رابطًا ويسأل عند التكرار. يعيد: التحميل، أو None إن لم يُضَف."""
        try:
            return self.manager.add(url, **kw)
        except DuplicateError as e:
            if quiet:
                return None
            name = e.existing.filename or e.existing.url
            r = QMessageBox.question(self, "موجود سابقًا",
                                     f"هذا الرابط موجود في القائمة:\n{name[:90]}\n\nهل تريد إضافته مرة أخرى؟")
            if r == QMessageBox.Yes:
                return self.manager.add(url, allow_duplicate=True, **kw)
            return None

    def _set_after(self, key):
        self.config["after_all"] = key
        self.config.save()

    def _after_queue(self):
        """بعد انتهاء كل التحميلات: تنفيذ الإجراء المختار (مع مهلة إلغاء للإطفاء)."""
        key = self.config["after_all"]
        busy = any(d.state in (DOWNLOADING, PROBING, QUEUED) for d in self.manager.snapshot())
        if busy:
            self._had_work = True
            return
        if key == "none" or not self._had_work:
            return
        self._had_work = False
        self._set_after("none")
        self._after_acts["none"].setChecked(True)
        if key == "quit":
            self.quit()
        elif key == "shutdown":
            box = QMessageBox(QMessageBox.Warning, "إطفاء الجهاز",
                              "اكتملت التحميلات. سيُطفأ الجهاز بعد 60 ثانية.", QMessageBox.Cancel, self)
            box.button(QMessageBox.Cancel).setText("إلغاء الإطفاء")
            QTimer.singleShot(60000, box.accept)
            box.exec()
            if box.clickedButton() is not box.button(QMessageBox.Cancel):
                power.shutdown(0)

    def open_add(self, urls="", video=False):
        dlg = AddDialog(self.config, self, urls=urls, video=video)
        if dlg.exec():
            v = dlg.values()
            errors = []
            skipped = 0
            many = len(v["urls"]) > 1
            for u in v["urls"]:
                try:
                    d = self._add(u, quiet=many, dest_dir=v["folder"] or None, kind=v["kind"],
                                  height=v["height"], audio_only=v["audio_only"],
                                  force=not v["later"], subs=v["subs"] or None)
                    skipped += d is None
                except ValueError as e:
                    errors.append(f"{u[:70]}\n{e}")
            if skipped and many:
                self.show_banner(f"تم تخطي {skipped} رابط مكرر", None, 6)
            if errors:
                QMessageBox.warning(self, "تعذّرت إضافة بعض الروابط", "\n\n".join(errors))
            self.set_filter("all")

    def settings(self):
        dlg = SettingsDialog(self.config, self)
        if dlg.exec():
            dlg.apply()
            self.manager.apply_settings()
            QApplication.instance().setStyleSheet(theme.apply_config(self.config))
            self.retint()
            self.refresh(force=True)
            if dlg.ready_path:
                self.manager.shutdown()
                selfupdate.apply_and_restart(dlg.ready_path)
                self.quit()

    def pair_extension(self):
        open_pairing(300)
        self.show_banner("جاهز للربط: افتح خيارات إضافة سيل في Chrome واضغط «ربط تلقائي» (خلال 5 دقائق).",
                         None, 20)

    def show_welcome(self):
        WelcomeDialog(self.config, self).exec()
        self.config["welcomed"] = True
        self.config.save()

    def check_updates_bg(self):
        """فحص صامت عند التشغيل؛ يظهر إشعار فقط إن وُجد إصدار أحدث."""
        repo = selfupdate.clean_repo(self.config["update_repo"])
        if not repo or not self.config["auto_update_check"]:
            return

        def work():
            try:
                info = selfupdate.check(repo)
            except Exception:  # noqa: BLE001
                return
            if info["newer"]:
                self.manager.events.append(
                    f"يتوفر إصدار جديد من سيل ({info['latest']}). افتح الإعدادات ← التحديث.")
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ الحافظة والسحب
    def show_banner(self, text, url, seconds=14):
        self._banner_url = url
        self.banner_text.set_full_text(text)
        self.banner_go.setVisible(bool(url))
        self.banner.show()
        self.banner_timer.start(seconds * 1000)

    def _clip_changed(self):
        if not self.config["clipboard_watch"]:
            return
        text = (QApplication.clipboard().text() or "").strip()
        if not text or text == self._clip_last or text == self._ignore_clip:
            return
        self._clip_last = text
        if looks_downloadable(text):
            kind = "فيديو" if is_media_url(text) else "ملف"
            self.show_banner(f"رابط {kind} منسوخ: {text}", text)

    def _banner_download(self):
        if self._banner_url:
            try:
                self._add(self._banner_url)
                self.set_filter("all")
            except ValueError as e:
                QMessageBox.warning(self, "سيل", str(e))
        self.banner.hide()

    def on_remote_add(self, _id):
        self.show_banner("وصل تحميل جديد من المتصفح", None, 4)
        if self.config["notify"] and self.tray.isVisible():
            self.tray.showMessage("سيل", "تمت إضافة تحميل من المتصفح",
                                  QSystemTrayIcon.Information, 2500)

    def _urls_from(self, mime):
        urls = [u.toString() for u in mime.urls() if u.scheme() in ("http", "https")]
        if not urls and mime.hasText():
            urls = [t.strip() for t in mime.text().splitlines()
                    if t.strip().lower().startswith(("http://", "https://"))]
        return urls

    def dragEnterEvent(self, e):
        if self._urls_from(e.mimeData()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = self._urls_from(e.mimeData())
        if urls:
            e.acceptProposedAction()
            self.open_add(urls="\n".join(urls))

    # ------------------------------------------------------------ الإغلاق
    def show_normal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        if self.tray.isVisible():
            self.hide()
            e.ignore()
        else:
            self.quit()

    def quit(self):
        self.manager.shutdown()
        self.tray.hide()
        QApplication.quit()


def main():
    setup_tools.install_excepthook()
    app = QApplication(sys.argv)
    app.setLayoutDirection(Qt.RightToLeft)
    app.setQuitOnLastWindowClosed(False)
    config = Config()
    os.makedirs(os.path.dirname(setup_tools.LOG_PATH), exist_ok=True)
    lock = QLockFile(os.path.join(os.path.dirname(setup_tools.LOG_PATH), "sayl.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "سيل", "سيل يعمل بالفعل. ابحث عن أيقونته بجانب الساعة.")
        return
    app.setStyleSheet(theme.apply_config(config))
    try:
        setup_tools.register_native_host(config)      # يجعل الإضافة تعمل بلا رمز ولا منفذ
    except Exception as e:  # noqa: BLE001
        setup_tools.log(f"فشل تسجيل الجسر: {e!r}")
    manager = DownloadManager(config)
    bridge = Bridge()
    win = MainWindow(manager, config, bridge)
    bridge.added.connect(win.on_remote_add)
    try:
        server = ApiServer(manager, config, on_add=lambda d: bridge.added.emit(d.id))
        server.start()
    except OSError as e:
        QMessageBox.warning(win, "سيل", f"تعذّر فتح المنفذ {config['port']}: {e}\n"
                            "لن تعمل إضافة المتصفح حتى تغيّر المنفذ.")
    if "--minimized" not in sys.argv:
        win.show()
        if not config["welcomed"]:
            QTimer.singleShot(400, win.show_welcome)
    win.check_updates_bg()
    code = app.exec()
    lock.unlock()
    sys.exit(code)


if __name__ == "__main__":
    main()
