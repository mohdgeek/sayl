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
from sayl.dialogs import AddDialog, SettingsDialog  # noqa: E402
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
sd = SettingsDialog(cfg, win)
assert sd.tabs.count() == 5
sd._pick("accent", "#FF6FA5")
assert theme.C["accent"] == "#FF6FA5" and theme.C["on_accent"] == "#1B1300"
sd._pick("accent", "#222222")
assert theme.C["on_accent"] == "#FFFFFF"             # نص مقروء على لون داكن
sd.base_combo.setCurrentIndex(sd.base_combo.findData("midnight"))
assert theme.C["bg"] == "#0B1020"
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
