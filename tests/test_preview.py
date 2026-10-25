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


def test_titlebar_plan():
    from sayl import theme, winstyle
    theme.set_theme("dark")
    p = dict(winstyle.plan("theme", theme.C, True))
    assert p[winstyle.DWMWA_DARK] == 1 and p[winstyle.DWMWA_BACKDROP] == 1
    assert p[winstyle.DWMWA_CAPTION] == winstyle.colorref(theme.C["side"])
    assert dict(winstyle.plan("mica", theme.C, True))[winstyle.DWMWA_BACKDROP] == 2
    assert dict(winstyle.plan("acrylic", theme.C, False))[winstyle.DWMWA_BACKDROP] == 3
    d = dict(winstyle.plan("default", theme.C, True))
    assert d[winstyle.DWMWA_CAPTION] == winstyle.COLOR_DEFAULT
    assert dict(winstyle.plan("bogus", theme.C, True)) == d
    assert winstyle.colorref("#102030") == 0x302010
    assert winstyle.apply(object()) is False      # خارج ويندوز: لا يفعل شيئًا


def test_glass_themes_and_settings_fit(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from sayl import theme
    from sayl.dialogs import SettingsDialog
    for k in theme.GLASS_BASES:
        theme.GLASS_READY = True
        q = theme.set_theme(k, theme.THEME_ACCENT[k])
        assert "rgba(" in q and "QMenu { background: #" in q     # الأسطح شفافة والقوائم معتمة
        theme.GLASS_READY = False
        assert "QMainWindow, QDialog { background: #" in theme.set_theme(k)
    theme.set_theme("dark")
    cfg = Config(str(tmp_path / "c.json"))
    dlg = SettingsDialog(cfg)
    h = app.primaryScreen().availableGeometry().height()
    assert dlg.maximumHeight() <= h and dlg.height() <= h
    dlg.tabs.setCurrentIndex(4)


def test_share_server(tmp_path):
    import requests
    from sayl import share
    f = tmp_path / "ملف.bin"
    f.write_bytes(bytes(range(256)) * 400)
    s = share.ShareServer(str(f), host="127.0.0.1")
    try:
        r = requests.get(s.url, timeout=5)
        assert r.status_code == 200 and r.content == f.read_bytes()
        r = requests.get(s.url, headers={"Range": "bytes=100-199"}, timeout=5)
        assert r.status_code == 206 and r.content == f.read_bytes()[100:200]
        bad = s.url.replace(s.token, "wrong")
        assert requests.get(bad, timeout=5).status_code == 404
        assert requests.get(f"http://127.0.0.1:{s.port}/", timeout=5).status_code == 404
        png = share.qr_png(s.url)
        assert png[:4] == b"\x89PNG"
    finally:
        s.stop()


def test_share_dialog(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from sayl.dialogs import ShareDialog
    f = tmp_path / "a.zip"
    f.write_bytes(b"x" * 5000)
    d = ShareDialog(str(f))
    assert d.srv.url.endswith("/a.zip")
    d.accept()
    import requests
    with pytest.raises(requests.RequestException):
        requests.get(d.srv.url.replace(d.srv.host, "127.0.0.1"), timeout=2)


def test_share_completed_flag(tmp_path):
    import requests
    from sayl import share
    f = tmp_path / "a.bin"
    f.write_bytes(b"y" * 300000)
    s = share.ShareServer(str(f), host="127.0.0.1")
    try:
        requests.get(s.url, headers={"Range": "bytes=0-99"}, timeout=5)
        assert s.completed == 0                       # جزء فقط: لم يكتمل
        requests.get(s.url, timeout=5)
        assert s.completed == 1 and s.cur == s.cur_total == 300000
    finally:
        s.stop()


def test_lan_transfer_and_refusal(tmp_path):
    import os
    from sayl import lan
    src = tmp_path / "تقرير 1.bin"
    src.write_bytes(os.urandom(1_500_000))
    got = []
    r = lan.LanReceiver(str(tmp_path / "in"), "PC-B", lambda i: True, got.append, port=0, discover_port=47691)
    try:
        found = lan.scan(targets=["127.0.0.1"], port=47691, timeout=1)
        assert found and found[0]["name"] == "PC-B" and found[0]["port"] == r.port
        prog = []
        res = lan.send_file("127.0.0.1", r.port, str(src), lambda s, t: prog.append((s, t)))
        assert res["verified"] and prog[-1] == (1_500_000, 1_500_000)
        assert (tmp_path / "in" / "تقرير 1.bin").read_bytes() == src.read_bytes()
        res2 = lan.send_file("127.0.0.1", r.port, str(src))          # نفس الاسم: لا يُستبدل
        assert res2["name"] != "تقرير 1.bin" and len(os.listdir(tmp_path / "in")) == 2
        assert not [f for f in os.listdir(tmp_path / "in") if f.endswith(".sayl-part")]
        # اسم خبيث لا يخرج من المجلد
        evil = tmp_path / "x.bin"
        evil.write_bytes(b"1")
        import http.client, json
        c = http.client.HTTPConnection("127.0.0.1", r.port)
        c.request("POST", "/offer", json.dumps({"name": "../../evil.txt", "size": 1}))
        tok = json.loads(c.getresponse().read())["token"]
        c = http.client.HTTPConnection("127.0.0.1", r.port)
        c.request("PUT", "/send/" + tok, b"1", {"Content-Length": "1"})
        out = json.loads(c.getresponse().read())
        assert os.path.dirname(os.path.join(str(tmp_path / "in"), out["name"])) == str(tmp_path / "in")
        assert not (tmp_path / "evil.txt").exists()
        c = http.client.HTTPConnection("127.0.0.1", r.port)           # رمز مستعمل يُرفض
        c.request("PUT", "/send/" + tok, b"1", {"Content-Length": "1"})
        assert c.getresponse().status == 403
    finally:
        r.stop()
    r2 = lan.LanReceiver(str(tmp_path / "in2"), "PC-C", lambda i: False, port=0, discover_port=47692)
    try:
        with pytest.raises(lan.SendError, match="رفض"):
            lan.send_file("127.0.0.1", r2.port, str(src))
        assert not os.path.exists(tmp_path / "in2") or not os.listdir(tmp_path / "in2")
    finally:
        r2.stop()
    with pytest.raises(lan.SendError):
        lan.send_file("127.0.0.1", 9, str(src))                         # لا أحد يستمع


def test_lan_dialog_sends(tmp_path):
    import os, time
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from sayl import lan
    from sayl.dialogs import LanSendDialog
    src = tmp_path / "f.bin"
    src.write_bytes(b"z" * 200000)
    r = lan.LanReceiver(str(tmp_path / "in"), "PC-B", lambda i: True, port=0, discover_port=47693)
    try:
        d = LanSendDialog([str(src)])
        d.manual.setText(f"127.0.0.1:{r.port}")
        d.devs.clear()
        d._send()
        t0 = time.time()
        while d._busy and time.time() - t0 < 10:
            app.processEvents()
            time.sleep(0.02)
        app.processEvents()
        assert "✓" in d.status.text() and d.bar.value() == 100
        d.reject()
    finally:
        r.stop()
