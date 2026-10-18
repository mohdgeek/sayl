"""لقطات للواجهة بحالات متعددة: QT_QPA_PLATFORM=offscreen python tests/shots.py OUTDIR"""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from sayl import theme  # noqa: E402
from sayl.config import Config  # noqa: E402
from sayl.dialogs import AddDialog, SettingsDialog  # noqa: E402
from sayl.engine import Download, Segment  # noqa: E402
from sayl.gui import Bridge, MainWindow  # noqa: E402
from sayl.manager import DownloadManager  # noqa: E402
from sayl.media import MediaDownload  # noqa: E402

out = sys.argv[1] if len(sys.argv) > 1 else "."
os.makedirs(out, exist_ok=True)
tmp = tempfile.mkdtemp()
cfg = Config(os.path.join(tmp, "c.json"))
cfg["download_dir"] = os.path.join(tmp, "dl")
app = QApplication([])
app.setLayoutDirection(Qt.RightToLeft)
app.setStyleSheet(theme.set_theme("dark"))
mgr = DownloadManager(cfg, state_path=os.path.join(tmp, "s.json"), autostart=False)


def fake(cls, url, name, state, size, done=0, speed=0, eta=None, err="", **kw):
    d = cls(url, tmp, **kw) if cls is Download else cls(url, tmp)
    d.filename, d.size, d.state, d.error, d.speed = name, size, state, err, speed
    if cls is Download:
        if done:
            n = 8
            seg = size // n
            d.segments = [Segment(i * seg, (i + 1) * seg, i * seg + int(seg * done / size * 1.0))
                          for i in range(n)]
    else:
        d._frac = done / size if size else 0
        d._seen = {"v": [done, size]}
    d._eta = eta
    mgr.items[d.id] = d
    mgr._order.append(d.id)
    return d


MB = 1024 * 1024
a = fake(Download, "https://releases.ubuntu.com/24.04/ubuntu-24.04.1-desktop-amd64.iso",
         "ubuntu-24.04.1-desktop-amd64.iso", "downloading", 6200 * MB, 2480 * MB, 9.4 * MB)
b = fake(MediaDownload, "https://www.youtube.com/watch?v=abc123", "محاضرة الذكاء الاصطناعي - الجزء الأول.mp4",
         "downloading", 412 * MB, 301 * MB, 4.1 * MB)
c = fake(Download, "https://cdn.example.com/files/annual-report-2025.pdf", "annual-report-2025.pdf",
         "completed", 8 * MB)
d = fake(Download, "https://example.com/setup/MyApp-Setup-4.2.0.exe", "MyApp-Setup-4.2.0.exe",
         "paused", 180 * MB, 64 * MB)
e = fake(Download, "https://static.example.org/archive/photos-2024.zip", "photos-2024.zip",
         "error", 900 * MB, 120 * MB, err="انقطع الاتصال بالخادم بعد عدة محاولات")
f = fake(MediaDownload, "https://music.example.com/track.m3u8", "track.mp3", "completed", 9 * MB)
g = fake(Download, "https://img.example.com/wallpaper-4k.jpg", "wallpaper-4k.jpg", "queued", 14 * MB)
b._eta = 27
mgr.history.extend([2e6, 3e6, 5e6, 7e6, 6e6, 9e6, 12e6, 10e6, 8e6, 11e6, 13.5e6, 12e6, 14e6, 13.5e6] * 3)

win = MainWindow(mgr, cfg, Bridge())
win.resize(1140, 700)
win.show()
win._select(a.id)
win.show_banner("رابط فيديو منسوخ: https://www.youtube.com/watch?v=dQw4w9WgXcQ", "x")
for _ in range(10):
    app.processEvents()
    time.sleep(0.03)
win.refresh(force=True)
for _ in range(10):
    app.processEvents()
win.grab().save(os.path.join(out, "dark.png"))

win.toggle_theme()
win.set_filter("video")
for _ in range(10):
    app.processEvents()
win.grab().save(os.path.join(out, "light_video.png"))

win.toggle_theme()
win.set_filter("all")
win.search.setText("لا-يوجد")
for _ in range(10):
    app.processEvents()
win.grab().save(os.path.join(out, "empty.png"))
win.search.setText("")

dlg = AddDialog(cfg, win, urls="https://www.youtube.com/watch?v=abc123")
dlg.show()
for _ in range(10):
    app.processEvents()
dlg.grab().save(os.path.join(out, "add.png"))
dlg.close()
def pump(n=10):
    for _ in range(n):
        app.processEvents()


# ملف فيه قائمة تشغيل
dlg = AddDialog(cfg, win, urls="https://www.youtube.com/playlist?list=PL1", video=True)
dlg.show()
dlg._show_playlist({"title": "دورة بايثون الكاملة", "entries": [
    {"url": f"https://v.test/{i}", "title": t} for i, t in enumerate(
        ["١. المقدمة والتثبيت", "٢. المتغيرات وأنواع البيانات", "٣. الجمل الشرطية",
         "٤. الحلقات التكرارية", "٥. الدوال", "٦. الكلاسات والكائنات"])]})
pump()
dlg.grab().save(os.path.join(out, "playlist.png"))
dlg.close()

cfg["schedule_enabled"] = True
for name, tab in (("general", 0), ("schedule", 1), ("video", 2), ("look", 3)):
    sd = SettingsDialog(cfg, win)
    sd.tabs.setCurrentIndex(tab)
    sd.show()
    pump()
    sd.grab().save(os.path.join(out, f"settings_{name}.png"))
    sd.reject()
cfg["schedule_enabled"] = False

# لون وردي على «منتصف الليل»
app.setStyleSheet(theme.set_theme("midnight", "#FF6FA5"))
win.retint()
win.refresh(force=True)
pump()
win.grab().save(os.path.join(out, "rose_midnight.png"))
app.setStyleSheet(theme.set_theme("black", "#2DD4BF"))
win.retint()
win.refresh(force=True)
pump()
win.grab().save(os.path.join(out, "teal_black.png"))
print("shots ok")
