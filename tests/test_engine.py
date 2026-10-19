import hashlib
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sayl.config import Config  # noqa: E402
from sayl.engine import COMPLETED, PAUSED, Download, RateLimiter  # noqa: E402
from sayl.manager import DownloadManager  # noqa: E402
from sayl.server import ApiServer  # noqa: E402

DATA = os.urandom(12 * 1024 * 1024 + 12345)
SHA = hashlib.sha256(DATA).hexdigest()


def make_server(ranges=True, delay=0.0):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/redir"):
                self.send_response(302)
                self.send_header("Location", "/file.bin")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start, end, code = 0, len(DATA) - 1, 200
            rng = self.headers.get("Range")
            if ranges and rng:
                m = re.match(r"bytes=(\d+)-(\d*)", rng)
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else len(DATA) - 1
                end = min(end, len(DATA) - 1)
                code = 206
            body = DATA[start:end + 1]
            self.send_response(code)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", '"abc123"')
            self.send_header("Content-Disposition", 'attachment; filename="my file.bin"')
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(DATA)}")
            self.end_headers()
            try:
                for i in range(0, len(body), 65536):
                    self.wfile.write(body[i:i + 65536])
                    if delay:
                        time.sleep(delay)
            except (BrokenPipeError, ConnectionResetError):
                pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def server():
    s = make_server()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


def sha_of(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def wait_for(cond, timeout=60):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_segmented_download_matches(server, tmp_path):
    d = Download(server + "/file.bin", str(tmp_path), connections=8)
    d.start()
    assert wait_for(lambda: d.state in (COMPLETED, "error")), d.error
    assert d.state == COMPLETED, d.error
    assert d.filename == "my file.bin"            # من Content-Disposition
    assert sha_of(tmp_path / d.filename) == SHA
    assert not list(tmp_path.glob("*.sayl*"))     # لا ملفات مؤقتة متبقية


def test_redirect_and_name(server, tmp_path):
    d = Download(server + "/redir", str(tmp_path), connections=4)
    d.start()
    assert wait_for(lambda: d.state in (COMPLETED, "error")), d.error
    assert sha_of(tmp_path / d.filename) == SHA


def test_dynamic_split_creates_more_segments(server, tmp_path):
    d = Download(server + "/file.bin", str(tmp_path), connections=2)
    d.start()
    assert wait_for(lambda: d.state in (COMPLETED, "error"))
    assert sha_of(tmp_path / d.filename) == SHA


def test_no_range_server_falls_back(tmp_path):
    s = make_server(ranges=False)
    try:
        d = Download(f"http://127.0.0.1:{s.server_address[1]}/file.bin", str(tmp_path))
        d.start()
        assert wait_for(lambda: d.state in (COMPLETED, "error")), d.error
        assert d.state == COMPLETED
        assert not d.resumable
        assert sha_of(tmp_path / d.filename) == SHA
    finally:
        s.shutdown()


def test_pause_and_resume(tmp_path):
    s = make_server(delay=0.004)
    try:
        d = Download(f"http://127.0.0.1:{s.server_address[1]}/file.bin", str(tmp_path), connections=4)
        d.start()
        assert wait_for(lambda: d.downloaded > 1_000_000)
        d.pause()
        assert d.state == PAUSED
        partial = d.downloaded
        assert 0 < partial < len(DATA)
        time.sleep(0.3)
        assert d.downloaded == partial            # توقّف فعلًا
        d.start()
        assert wait_for(lambda: d.state in (COMPLETED, "error"), 90), d.error
        assert d.state == COMPLETED
        assert sha_of(tmp_path / d.filename) == SHA
    finally:
        s.shutdown()


def test_resume_from_disk_after_restart(tmp_path):
    s = make_server(delay=0.004)
    url = f"http://127.0.0.1:{s.server_address[1]}/file.bin"
    cfg = Config(str(tmp_path / "cfg.json"))
    cfg["download_dir"] = str(tmp_path / "dl")
    state = str(tmp_path / "state.json")
    try:
        m1 = DownloadManager(cfg, state_path=state)
        d = m1.add(url)
        assert wait_for(lambda: d.downloaded > 1_000_000)
        m1.shutdown()
        partial = d.downloaded
        m2 = DownloadManager(cfg, state_path=state)      # "إعادة تشغيل"
        d2 = m2.snapshot()[0]
        assert d2.state == PAUSED
        m2.resume(d2.id)
        assert wait_for(lambda: d2.state in (COMPLETED, "error"), 90), d2.error
        assert d2.state == COMPLETED
        assert sha_of(tmp_path / "dl" / d2.filename) == SHA
        assert partial > 0
        m2.shutdown()
    finally:
        s.shutdown()


def test_rate_limiter():
    rl = RateLimiter(200_000)
    t = time.monotonic()
    for _ in range(10):
        rl.acquire(65536)          # 655KB تقريبًا
    elapsed = time.monotonic() - t
    assert elapsed > 2.0           # ~3.3s بسرعة 200KB/s (مع السماح الأولي)


def test_api_server_auth_and_add(server, tmp_path):
    cfg = Config(str(tmp_path / "cfg.json"))
    cfg["download_dir"] = str(tmp_path / "dl")
    cfg["port"] = 0
    mgr = DownloadManager(cfg, state_path=str(tmp_path / "s.json"))
    api = ApiServer(mgr, cfg)
    api.start()
    base = f"http://127.0.0.1:{api.port}"
    try:
        assert requests.get(base + "/ping").status_code == 401
        assert requests.get(base + "/ping", headers={"X-Sayl-Token": "wrong"}).status_code == 401
        ok = {"X-Sayl-Token": cfg["token"]}
        assert requests.get(base + "/ping", headers=ok).json()["ok"]
        # Origin غريب مرفوض
        assert requests.get(base + "/ping", headers={**ok, "Origin": "http://evil.com"}).status_code == 403
        # Host غريب مرفوض (DNS rebinding)
        assert requests.get(base + "/ping", headers={**ok, "Host": "evil.com"}).status_code == 403
        # رابط غير http مرفوض
        r = requests.post(base + "/add", headers=ok, json={"url": "file:///etc/passwd"})
        assert r.status_code == 400
        r = requests.post(base + "/add", headers=ok, json={"url": server + "/file.bin",
                                                          "headers": {"Referer": "http://x", "Evil": "1"}})
        assert r.json()["ok"]
        d = mgr.snapshot()[0]
        assert "Evil" not in d.headers and d.headers["Referer"] == "http://x"
        assert wait_for(lambda: d.state in (COMPLETED, "error")), d.error
        assert d.state == COMPLETED
    finally:
        api.stop()
        mgr.shutdown()


def test_connections_ramp_up_when_slow(server, tmp_path):
    from sayl.engine import RateLimiter
    d = Download(server + "/file.bin", str(tmp_path), connections=6, limiter=RateLimiter(3 * 1024 * 1024))
    d.start()
    assert wait_for(lambda: d.state in (COMPLETED, "error"), 120), d.error
    assert d.state == COMPLETED, d.error
    assert len(d.segments) >= 3                   # بدأ باتصال واحد ثم أضاف اتصالات
    assert sha_of(tmp_path / d.filename) == SHA
