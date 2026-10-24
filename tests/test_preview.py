import io
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sayl import preview
from sayl.config import Config

ZIPB = io.BytesIO()
with zipfile.ZipFile(ZIPB, "w") as z:
    z.writestr("a/one.txt", "x" * 100)
    z.writestr("two.txt", "y" * 50)
ZIPDATA = ZIPB.getvalue()
PNG = None


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        data = ZIPDATA
        rng = self.headers.get("Range")
        if rng and self.path.endswith("zip"):
            a, b = rng.split("=")[1].split("-")
            a, b = int(a), min(int(b), len(data) - 1)
            self.send_response(206)
            self.send_header("Content-Length", str(b - a + 1))
            self.end_headers()
            self.wfile.write(data[a:b + 1])
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def srv():
    s = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_port}"
    s.shutdown()


def test_kind():
    assert preview.kind_for("a.PNG", 10) == "image"
    assert preview.kind_for("a.exe", 10) is None
    assert preview.kind_for("a.pdf", 10**9) is None


def test_zip_range(srv):
    r = preview.fetch(srv + "/f.zip", "f.zip", len(ZIPDATA))
    assert r["count"] == 2 and r["total"] == 150


def test_zip_full_fallback(srv):
    r = preview.fetch(srv + "/f.bin", "f.zip", None)
    assert r["count"] == 2


def test_pagelinks_extract():
    from sayl import pagelinks
    html_ = ('<a href="https://github.com/a/r/releases/download/v1/App-1.4.exe">x</a>'
             '<a href="/files/b.zip?x=1">z</a><a href="/about">no</a><a href="mailto:a@b.c">m</a>')
    r = pagelinks.extract(html_, "https://site.io/p/")
    assert r == ["https://github.com/a/r/releases/download/v1/App-1.4.exe", "https://site.io/files/b.zip?x=1"]


def test_schedule_item(tmp_path):
    import time
    from sayl.engine import PAUSED, QUEUED
    from sayl.manager import DownloadManager
    import pytest
    cfg = Config(str(tmp_path / "c.json"))
    m = DownloadManager(cfg, state_path=str(tmp_path / "s.json"), autostart=False)
    d = m.add("https://x.example/f.bin", start=True, start_at=time.time() + 3600)
    assert d.state == QUEUED and d.force is False
    started = []
    d.start = lambda: started.append(1)
    m._tick()
    assert not started                                   # لم يحن وقته
    with pytest.raises(ValueError):
        m.schedule(d.id, time.time() - 5)
    m.save()
    m2 = DownloadManager(cfg, state_path=str(tmp_path / "s.json"), autostart=False)
    assert m2.items[d.id].state == QUEUED and m2.items[d.id].start_at == d.start_at   # تبقى بعد إعادة التشغيل
    d.start_at = time.time() - 1                         # حان الوقت
    cfg["schedule_enabled"], cfg["sched_start"], cfg["sched_stop"] = True, "00:00", "00:01"  # نافذة الطابور مغلقة غالبًا
    m._tick()
    assert started == [1] and d.start_at is None and d.force is True   # يبدأ حتى خارج النافذة العامة
    m.schedule(d.id, None)
    assert d.state == PAUSED and d.start_at is None


def test_daily_cap(tmp_path):
    from sayl.manager import DownloadManager
    cfg = Config(str(tmp_path / "c.json"))
    m = DownloadManager(cfg, state_path=str(tmp_path / "s.json"), autostart=False)
    d = m.add("https://x.example/f.bin", start=True)
    started = []
    d.start = lambda: started.append(1)
    cfg["daily_cap_gb"] = 1
    m._tick()
    m.day_bytes = 2 * 1024 ** 3                    # تجاوز الحد
    assert m.capped()
    d.state = "queued"
    m._tick()
    assert started == [1] or started == []          # لا يبدأ جديد بعد الحد
    n = len(started)
    m._tick()
    assert len(started) == n
    cfg["daily_cap_gb"] = 0
    assert not m.capped()


def test_all_themes_build(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from sayl import theme
    for k in theme.BASES:
        q = theme.set_theme(k, theme.THEME_ACCENT[k])
        assert "QMainWindow" in q
        assert (theme.NAME == "light") == (k in theme.LIGHT_BASES)
    cfg = Config(str(tmp_path / "c.json"))
    cfg["theme"], cfg["light_base"] = "light", "paper"
    theme.apply_config(cfg)
    assert theme.C["bg"] == "#F7F1E5"
    cfg["theme"], cfg["dark_base"] = "dark", "nord"
    theme.apply_config(cfg)
    assert theme.C["bg"] == "#242933"
    theme.set_theme("dark")
    del app


def test_window_opacity(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialog
    app = QApplication.instance() or QApplication([])
    from sayl import theme
    f = theme.install_opacity(app)
    w1 = QDialog()
    w1.show()
    theme.apply_opacity(70)
    assert abs(w1.windowOpacity() - 0.7) < 0.01
    w2 = QDialog()
    w2.show()                                   # نافذة جديدة ترث الشفافية
    app.processEvents()
    assert abs(w2.windowOpacity() - 0.7) < 0.01
    theme.apply_opacity(10)                     # لا تنزل عن 50%
    assert theme.OPACITY == 0.5
    theme.apply_opacity(100)
    assert w1.windowOpacity() == 1.0
    app.removeEventFilter(f)
    w1.close(); w2.close()
