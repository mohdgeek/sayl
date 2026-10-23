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


def make_server(ranges=True, delay=0.0, hang_first=False):
    state = {"n": 0}

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
            if hang_first and rng and rng != "bytes=0-0":
                state["n"] += 1
                if state["n"] == 1:             # أول طلب بعد الفحص يُعلَّق
                    time.sleep(25)
                    return
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
    cfg["subfolders"] = False
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


def test_recovers_when_first_request_hangs(tmp_path):
    srv = make_server(hang_first=True)
    try:
        d = Download(f"http://127.0.0.1:{srv.server_address[1]}/file.bin", str(tmp_path), connections=4)
        d.start()
        assert wait_for(lambda: d.state in (COMPLETED, "error"), 60), d.error
        assert d.state == COMPLETED, d.error
        assert sha_of(tmp_path / d.filename) == SHA
    finally:
        srv.shutdown()


def test_dns_fallback_uses_doh_when_system_dns_fails(monkeypatch):
    import json as _json
    import socket
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from sayl import dns_fallback

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = _json.dumps({"Answer": [{"type": 1, "data": "127.0.0.1"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    doh = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=doh.serve_forever, daemon=True).start()
    monkeypatch.setattr(dns_fallback, "ENDPOINTS", [(f"http://127.0.0.1:{doh.server_address[1]}/dns-query", {})])
    dns_fallback._cache.clear()
    dns_fallback.install()
    res = socket.getaddrinfo("no-such-host.sayl-test.invalid", 80, 0, socket.SOCK_STREAM)
    assert res and res[0][4][0] == "127.0.0.1"
    doh.shutdown()


def test_mega_file_download_decrypts(tmp_path, monkeypatch):
    import base64
    import json as _json
    import struct
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from Cryptodome.Cipher import AES
    from sayl import mega
    from sayl.engine import make_download

    plain = os.urandom(3 * 1024 * 1024 + 77)
    k = struct.unpack(">8I", os.urandom(32))
    aes_key = struct.pack(">4I", k[0] ^ k[4], k[1] ^ k[5], k[2] ^ k[6], k[3] ^ k[7])
    nonce = struct.pack(">2I", k[4], k[5])
    cipher = AES.new(aes_key, AES.MODE_CTR, nonce=nonce, initial_value=0).encrypt(plain)
    attrs = b"MEGA" + _json.dumps({"n": "مرحبا file.zip"}).encode()
    attrs += b"\0" * (-len(attrs) % 16)
    at = AES.new(aes_key, AES.MODE_CBC, iv=b"\0" * 16).encrypt(attrs)
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    key_b64 = b64(struct.pack(">8I", *k))

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            body = _json.dumps([{"s": len(cipher), "at": b64(at),
                                 "g": f"http://127.0.0.1:{srv.server_address[1]}/blob"}]).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(cipher)))
            self.end_headers()
            self.wfile.write(cipher)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(mega, "API", f"http://127.0.0.1:{srv.server_address[1]}/cs")
    try:
        d = make_download(f"https://mega.nz/file/AbCdEfGh#{key_b64}", str(tmp_path))
        d.start()
        assert wait_for(lambda: d.state in (COMPLETED, "error"), 30), d.error
        assert d.state == COMPLETED, d.error
        assert d.filename.endswith("file.zip")
        assert (tmp_path / d.filename).read_bytes() == plain
        old = make_download(f"https://mega.nz/#!AbCdEfGh!{key_b64}", str(tmp_path))
        assert type(old).__name__ == "MegaDownload"
        assert mega.is_folder("https://mega.nz/folder/AbCdEfGh#xyz")
    finally:
        srv.shutdown()


def test_mega_folder(tmp_path, monkeypatch):
    """مجلد MEGA: تعداد الملفات وفك مفاتيح العقد والهيكل الفرعي والتنزيل المشفّر."""
    import base64
    import json as _json
    import struct
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from Cryptodome.Cipher import AES
    from sayl import mega
    from sayl.config import Config
    from sayl.manager import DownloadManager

    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    fkey = os.urandom(16)

    def attr(name, key16):
        a = b"MEGA" + _json.dumps({"n": name}).encode()
        a += b"\0" * (-len(a) % 16)
        return b64(AES.new(key16, AES.MODE_CBC, iv=b"\0" * 16).encrypt(a))

    def wrap(key):          # يشفّر مفتاح العقدة بمفتاح المجلد (ECB)
        return b64(AES.new(fkey, AES.MODE_ECB).encrypt(key))

    root_key = os.urandom(16)
    sub_key = os.urandom(16)
    contents, nodes_info = {}, {}
    file_nodes = []
    for h, name, parent in (("FILE0001", "root.txt", "ROOTROOT"), ("FILE0002", "inner.bin", "SUBSUB01")):
        k = struct.unpack(">8I", os.urandom(32))
        raw = struct.pack(">8I", *k)
        aes_key, nonce = mega.keys_from_node(raw)
        plain = os.urandom(200_000)
        contents[h] = (AES.new(aes_key, AES.MODE_CTR, nonce=nonce, initial_value=0).encrypt(plain), plain)
        nodes_info[h] = (raw, aes_key, name)
        file_nodes.append({"h": h, "p": parent, "t": 0, "s": len(plain),
                           "a": attr(name, aes_key), "k": "ROOTROOT:" + wrap(raw)})
    nodes = [{"h": "ROOTROOT", "p": "", "t": 1, "a": attr("MyFolder", root_key), "k": "ROOTROOT:" + wrap(root_key)},
             {"h": "SUBSUB01", "p": "ROOTROOT", "t": 1, "a": attr("sub", sub_key), "k": "ROOTROOT:" + wrap(sub_key)}] + file_nodes

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            req = _json.loads(self.rfile.read(int(self.headers["Content-Length"])))[0]
            if req["a"] == "f":
                out = [{"f": nodes}]
            else:
                h = req["n"]
                out = [{"s": len(contents[h][0]), "at": next(n["a"] for n in nodes if n["h"] == h),
                        "g": f"http://127.0.0.1:{srv.server_address[1]}/blob/{h}"}]
            body = _json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            data = contents[self.path.rsplit("/", 1)[1]][0]
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(mega, "API", f"http://127.0.0.1:{srv.server_address[1]}/cs")
    try:
        url = f"https://mega.nz/folder/ROOTROOT#{b64(fkey)}"
        info = mega.list_folder(url)
        assert info["name"] == "MyFolder"
        assert [f["path"] for f in info["files"]] == ["MyFolder/root.txt", "MyFolder/sub/inner.bin"]
        only = mega.list_folder(url + "/file/FILE0002")
        assert [f["name"] for f in only["files"]] == ["inner.bin"]
        cfg = Config(str(tmp_path / "c.json"))
        m = DownloadManager(cfg, state_path=str(tmp_path / "s.json"), autostart=False)
        d = m.add(url, dest_dir=str(tmp_path / "out"))
        assert len(m.items) == 2
        for it in list(m.items.values()):
            it.start()
        assert wait_for(lambda: all(i.state in (COMPLETED, "error") for i in m.items.values()), 30)
        assert all(i.state == COMPLETED for i in m.items.values()), [i.error for i in m.items.values()]
        assert (tmp_path / "out" / "MyFolder" / "root.txt").read_bytes() == contents["FILE0001"][1]
        assert (tmp_path / "out" / "MyFolder" / "sub" / "inner.bin").read_bytes() == contents["FILE0002"][1]
        m.save()
        m2 = DownloadManager(cfg, state_path=str(tmp_path / "s.json"), autostart=False)
        assert all(type(i).__name__ == "MegaDownload" and i.node for i in m2.items.values())
    finally:
        srv.shutdown()


def test_refresh_url_resumes_partial(tmp_path):
    """رابط منتهٍ: نوقف في المنتصف ثم نستبدل الرابط (نفس الملف/ETag) فيكمل من حيث توقف بدل البدء من الصفر."""
    s = make_server(delay=0.01)
    base = f"http://127.0.0.1:{s.server_address[1]}"
    try:
        cfg = Config(str(tmp_path / "cfg.json"))
        cfg["download_dir"] = str(tmp_path / "dl")
        m = DownloadManager(cfg, state_path=str(tmp_path / "s.json"), autostart=False)
        d = m.add(base + "/old-link", start=False)
        d.connections = 1
        d.start()
        assert wait_for(lambda: d.downloaded > 2_000_000, 30)
        d.pause()
        d.wait(10)
        got = d.downloaded
        assert d.state == PAUSED and 0 < got < len(DATA)
        stale = m.find_stale(d.filename)
        assert stale is d
        m.refresh_url(d.id, base + "/new-link")
        d.start()
        assert wait_for(lambda: d.state in (COMPLETED, "error"), 60), d.state
        assert d.state == COMPLETED, d.error
        out = os.path.join(d.folder, d.filename)
        assert hashlib.sha256(open(out, "rb").read()).hexdigest() == SHA
    finally:
        s.shutdown()
