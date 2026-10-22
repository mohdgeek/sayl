"""فحص تشغيل الواجهة بدون شاشة: QT_QPA_PLATFORM=offscreen python tests/gui_smoke.py"""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from sayl import theme  # noqa: E402
from sayl.config import Config  # noqa: E402
from sayl.dialogs import AddDialog, SettingsDialog, WelcomeDialog  # noqa: E402
from sayl.gui import Bridge, MainWindow, looks_downloadable  # noqa: E402
from sayl.manager import DownloadManager  # noqa: E402
from test_engine import SHA, make_server, sha_of  # noqa: E402

tmp = tempfile.mkdtemp()
cfg = Config(os.path.join(tmp, "cfg.json"))
cfg["download_dir"] = os.path.join(tmp, "dl")
mgr = DownloadManager(cfg, state_path=os.path.join(tmp, "s.json"))
srv = make_server(delay=0.002)

app = QApplication([])
app.setLayoutDirection(Qt.RightToLeft)
app.setStyleSheet(theme.apply_config(cfg))
win = MainWindow(mgr, cfg, Bridge())
win.show()

# حوارات تُبنى وتتفاعل بلا أخطاء
dlg = AddDialog(cfg, win, urls="https://www.youtube.com/watch?v=x")
assert dlg.values()["kind"] == "media", "كشف تلقائي لرابط فيديو"
dlg2 = AddDialog(cfg, win, urls="https://example.com/a.zip")
assert dlg2.values()["kind"] == "http"
wd = WelcomeDialog(cfg, win)
wd.show()
assert wd.btn_ext.text()
wd.close()
sd = SettingsDialog(cfg, win)
assert sd.tabs.count() == 6
sd._pick("accent", "#FF6FA5")
assert theme.C["accent"] == "#FF6FA5" and theme.C["on_accent"] == "#1B1300"
sd._pick("accent", "#222222")
assert theme.C["on_accent"] == "#FFFFFF"             # نص مقروء على لون داكن
sd.base_combo.setCurrentIndex(sd.base_combo.findData("midnight"))
assert theme.C["bg"] == "#0B1020"
sd.base_combo.setCurrentIndex(sd.base_combo.findData("light"))
assert theme.NAME == "light"
sd._pick("err_color", "#123456")
sd.reset_btn.click()                                  # استعادة الافتراضي: السمة والألوان كلها
assert theme.NAME == "dark" and theme.C["bg"] == theme.DARK["bg"]
assert theme.C["accent"] == "#FFB020" and theme.C["err"] == theme.DARK["err"]
assert sd.base_combo.currentData() == "dark"
sd.reject()                                           # الإلغاء يعيد السمة
assert theme.C["accent"] == "#FFB020" and theme.C["bg"] == theme.DARK["bg"]
sd2 = SettingsDialog(cfg, win)
sd2._pick("accent", "#2DD4BF")
sd2._pick("err_color", "#E5484D")
sd2.s_from.setTime(sd2.s_from.time().addSecs(3600))
sd2.sched.setChecked(True)
sd2.apply()
assert cfg["accent"] == "#2DD4BF" and cfg["err_color"] == "#E5484D" and cfg["schedule_enabled"]
assert cfg["sched_start"] == "03:00"
cfg["schedule_enabled"] = False
cfg["accent"], cfg["err_color"] = "#FFB020", ""
app.setStyleSheet(theme.apply_config(cfg))

# اكتشاف الروابط في الحافظة
assert looks_downloadable("https://example.com/a.zip")
assert looks_downloadable("https://youtu.be/abc")
assert not looks_downloadable("https://example.com/page")
assert not looks_downloadable("hello world")

d = mgr.add(f"http://127.0.0.1:{srv.server_address[1]}/file.bin")
deadline = time.time() + 60
while time.time() < deadline and d.state not in ("completed", "error"):
    app.processEvents()
    time.sleep(0.05)
app.processEvents()
win.refresh(force=True)
assert d.state == "completed", d.error
assert sha_of(os.path.join(cfg["download_dir"], d.filename)) == SHA
row = win.rows[d.id]
assert row.chip.text() == "مكتمل", row.chip.text()
assert win.navs["done"].cnt.text() == "1"

# التبديل بين السمات والتصفية والبحث
win.toggle_theme()
assert theme.NAME == "light" and cfg["theme"] == "light"
win.toggle_theme()
win.set_filter("video")
assert win.stack.currentIndex() == 1              # لا فيديو → حالة فارغة
win.set_filter("all")
win.search.setText("my file")
app.processEvents()
assert d.id in win.rows
win.search.setText("zzz")
app.processEvents()
assert win.stack.currentIndex() == 1
print("GUI OK")
mgr.shutdown()


def _file_dialog_smoke():
    import tempfile
    from sayl.config import Config
    from sayl.dialogs import FileConfirmDialog
    cfg = Config(os.path.join(tempfile.mkdtemp(), "c.json"))
    d = FileConfirmDialog(cfg, "http://127.0.0.1:9/files/idm_al.zip", {}, None)
    v = d.values()
    assert v["filename"] == "idm_al.zip" and v["category"] == "archive", v
    d.remember.setChecked(True)
    d._finish(False)
    assert cfg["category_dirs"]["archive"] and d.values()["start"] is False
    print("FILE DIALOG OK")


_file_dialog_smoke()


def _standalone_confirm_smoke():
    """نافذة التأكيد تظهر وحدها: الواجهة الرئيسية تبقى مخفية ويُضاف التحميل."""
    from sayl import gui
    from sayl.dialogs import FileConfirmDialog
    win.hide()
    seen = {}

    class Auto(FileConfirmDialog):
        def exec(self):
            seen["parent"] = self.parent()
            seen["visible"] = self.isVisible()
            self._finish(False)               # «تحميل لاحقًا»
            return 1

    gui.FileConfirmDialog = Auto
    cfg["warn_duplicates"] = False           # لا نريد حوار «موجود سابقًا» هنا
    before = len(mgr.snapshot())
    win.open_confirm("http://127.0.0.1:%d/file.bin" % srv.server_address[1], {}, "http", {})
    assert seen["parent"] is None and not win.isVisible(), "يجب ألا تظهر الواجهة الرئيسية"
    assert seen["visible"], "النافذة تُعرض وحدها"
    assert len(mgr.snapshot()) == before + 1
    print("STANDALONE CONFIRM OK")


_standalone_confirm_smoke()


def _progress_window_smoke():
    """بعد «بدء التحميل» تبقى نافذة التقدّم ظاهرة (دون الواجهة الرئيسية) وتتابع التحميل حتى الاكتمال."""
    from sayl import gui
    from sayl.dialogs import FileConfirmDialog
    win.hide()

    class Auto(FileConfirmDialog):
        def exec(self):
            self.path.setText(os.path.join(cfg["download_dir"], "prog_test.bin"))
            self._finish(True)
            return 1

    gui.FileConfirmDialog = Auto
    win.open_confirm("http://127.0.0.1:%d/file.bin?p=1" % srv.server_address[1], {}, "http", {})
    w = win._progress_wins[-1]
    assert w.isVisible() and not win.isVisible()
    t = time.time()
    while time.time() - t < 40 and w.d.state != "completed":
        app.processEvents()
        mgr._tick()
        time.sleep(0.05)
    w.refresh()
    assert w.d.state == "completed", w.d.state
    assert w.chip_state.text() == "مكتمل" and w.b_open.isVisibleTo(w)
    print("PROGRESS WINDOW OK")


_progress_window_smoke()


def _update_bar_and_buttons_smoke():
    # لافتة التحديث داخل البرنامج
    win.bridge.upd_found.emit({"latest": "99.0.0", "asset_url": "", "page": "https://example.com", "notes": ""})
    app.processEvents()
    assert win.update_bar.isVisibleTo(win) and "99.0.0" in win.update_text.text()
    assert win.update_go.text() in ("حدّث الآن", "افتح صفحة الإصدار")
    win.update_later.click()
    assert not win.update_bar.isVisibleTo(win)
    # أي زر في أسفل نافذة التقدّم يُخفيها، وليست دائمة الظهور فوق النوافذ
    w = win._progress_wins[-1]
    w.show()
    assert not (w.windowFlags() & Qt.WindowStaysOnTopHint)
    w.b_main.click()
    assert not w.isVisible()
    win.hide()
    print("UPDATE BAR + BUTTONS OK")


_update_bar_and_buttons_smoke()
