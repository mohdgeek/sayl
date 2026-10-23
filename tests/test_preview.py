import io
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sayl import preview

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


def test_backup_roundtrip(tmp_path):
    from sayl import backup
    from sayl.config import Config
    from sayl.manager import DownloadManager
    cfg = Config(str(tmp_path / "c.json"))
    cfg.data["speed_limit"] = 123
    m = DownloadManager(cfg, state_path=str(tmp_path / "d.json"))
    m.stats.add(1000, "x.com")
    b = backup.make_backup(m, cfg, str(tmp_path / "b.zip"))
    cfg2 = Config(str(tmp_path / "c2.json"))
    m2 = DownloadManager(cfg2, state_path=str(tmp_path / "d2.json"))
    backup.restore_backup(m2, cfg2, b)
    assert cfg2.data["speed_limit"] == 123
    assert cfg2.data["token"] != cfg.data["token"]
    assert m2.stats.data["total"] == 1000
    m.shutdown()
    m2.shutdown()


def test_eta_speed():
    from sayl import speedtest
    assert "دقيقة" in speedtest.eta(1e9, 5e6)
    assert speedtest.eta(1, 0) == "—"
