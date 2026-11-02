"""اختبارات لمشاكل ظهرت في الاستخدام الفعلي: ضياع التقدّم عند الاستئناف، وتحميلات «مكتملة» بلا ملف صحيح."""
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sayl import media  # noqa: E402
from sayl.config import Config  # noqa: E402
from sayl.engine import COMPLETED, ERROR, Download  # noqa: E402
from sayl.manager import DownloadManager  # noqa: E402
from sayl.server import ApiServer  # noqa: E402

DATA = os.urandom(6 * 1024 * 1024 + 321)


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def range_server(ctrl):
    """خادم يدعم Range؛ ctrl["no_range"] يجعله يتجاهله، وctrl["hang"] يعلّق الطلبات."""
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            rng = self.headers.get("Range")
            if ctrl.get("hang") and rng != "bytes=0-0":
                time.sleep(ctrl["hang"])
                self.close_connection = True
                return
            start, end, code = 0, len(DATA) - 1, 200
            if rng and not ctrl.get("no_range"):
                m = re.match(r"bytes=(\d+)-(\d*)", rng)
                start = int(m.group(1))
                end = min(int(m.group(2)) if m.group(2) else end, end)
                code = 206
            body = DATA[start:end + 1]
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", '"v1"')
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(DATA)}")
            self.end_headers()
            try:
                for i in range(0, len(body), 65536):
                    self.wfile.write(body[i:i + 65536])
                    time.sleep(0.004)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    return serve(H)


def body_server(body, ctype, length=None):
    """خادم بلا Range يرسل نصًّا ثابتًا، مع Content-Length مختلف عند الحاجة لمحاكاة الانقطاع."""
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            if ctype:
                self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body) if length is None else length))
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

    return serve(H)


def wait_for(cond, timeout=60):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        time.sleep(0.05)
    return False


def partial_download(url, folder):
    d = Download(url + "/file.bin", str(folder), connections=4)
    d.start()
    assert wait_for(lambda: d.downloaded > 500_000), d.error
    d.pause()
    d.wait(15)
    assert 0 < d.downloaded < len(DATA)
    return d


def test_refused_resume_keeps_partial(tmp_path):
    """الخادم صار يتجاهل Range عند الاستئناف: لا يُكتب فوق ما حُمِّل."""
    ctrl = {}
    srv, url = range_server(ctrl)
    try:
        d = partial_download(url, tmp_path)
        saved, tmp = d.downloaded, d._tmp
        with open(tmp, "rb") as f:
            before = f.read(saved // 8)
        ctrl["no_range"] = True
        d.start()
        assert wait_for(lambda: d.state == ERROR, 30), d.state
        assert d.downloaded == saved
        assert "محفوظ" in d.error
        with open(tmp, "rb") as f:
            assert f.read(len(before)) == before
        assert os.path.exists(tmp + ".json")
        ctrl["no_range"] = False                  # عاد الخادم: يكتمل الملف ويطابق الأصل
        d.start()
        assert wait_for(lambda: d.state in (COMPLETED, ERROR), 90), d.error
        assert d.state == COMPLETED, d.error
        with open(tmp_path / d.filename, "rb") as f:
            assert f.read() == DATA
    finally:
        srv.shutdown()


def test_stalled_resume_keeps_partial(tmp_path):
    """استئناف بلا بيانات: لا يُحذف الملف الجزئي، ويُعدّ خطأ شبكة يستحق إعادة المحاولة."""
    ctrl = {}
    srv, url = range_server(ctrl)
    try:
        d = partial_download(url, tmp_path)
        saved, tmp = d.downloaded, d._tmp
        d.STALL_SECS = 1
        ctrl["hang"] = 3
        d.start()
        assert wait_for(lambda: d.state == ERROR, 40), d.state
        assert d.net_error
        assert d.downloaded == saved
        assert os.path.exists(tmp) and os.path.exists(tmp + ".json")
        assert not os.path.exists(tmp + ".single")
    finally:
        srv.shutdown()


def test_fresh_no_range_still_falls_back(tmp_path):
    """بدون أي تقدّم سابق يبقى التحويل إلى اتصال واحد كما هو."""
    ctrl = {"no_range": True}
    srv, url = range_server(ctrl)
    try:
        d = Download(url + "/file.bin", str(tmp_path))
        d.size, d.filename, d.resumable, d.etag = len(DATA), "f.bin", True, '"v1"'
        d.start()
        assert wait_for(lambda: d.state in (COMPLETED, ERROR), 60), d.error
        assert d.state == COMPLETED, d.error
        with open(tmp_path / "f.bin", "rb") as f:
            assert f.read() == DATA
    finally:
        srv.shutdown()


@pytest.mark.parametrize("path,body,ctype", [
    ("/videoplayback", b",\nsabr.malformed_config ", "text/plain"),      # حالة حقيقية من googlevideo
    ("/download", b"short", "application/octet-stream"),
    ("/clip.mp4", b"denied", "video/mp4"),
    ("/file.zip", b"<!DOCTYPE html><html><body>login</body></html>" * 100, "text/html; charset=utf-8"),
    ("/download", b"<html><body>expired</body></html>", "text/html"),
])
def test_error_body_is_not_completed(tmp_path, path, body, ctype):
    srv, url = body_server(body, ctype)
    try:
        d = Download(url + path, str(tmp_path))
        d.start()
        assert wait_for(lambda: d.state in (COMPLETED, ERROR), 30), d.error
        assert d.state == ERROR
        assert d.error
        assert not d.net_error
        assert not [p for p in os.listdir(tmp_path)]        # لا يبقى ملف مضلل ولا مؤقت
    finally:
        srv.shutdown()


@pytest.mark.parametrize("path,body,ctype", [
    ("/LICENSE", b"MIT License", "text/plain"),              # ملف نصي صغير حقيقي
    ("/note.txt", b"hi", "text/plain"),
    ("/page.html", b"<html><body>ok</body></html>", "text/html"),
    ("/data.bin", b"\x00\x01\x02", "application/octet-stream"),
])
def test_small_real_files_still_complete(tmp_path, path, body, ctype):
    srv, url = body_server(body, ctype)
    try:
        d = Download(url + path, str(tmp_path))
        d.start()
        assert wait_for(lambda: d.state in (COMPLETED, ERROR), 30), d.error
        assert d.state == COMPLETED, d.error
        with open(tmp_path / d.filename, "rb") as f:
            assert f.read() == body
    finally:
        srv.shutdown()


def test_truncated_body_is_not_completed(tmp_path):
    srv, url = body_server(b"x" * 5000, "application/octet-stream", length=9000)
    try:
        d = Download(url + "/file.bin", str(tmp_path))
        d.start()
        assert wait_for(lambda: d.state in (COMPLETED, ERROR), 30), d.error
        assert d.state == ERROR
        assert d.net_error                       # انقطاع: يستحق إعادة المحاولة التلقائية
        assert not (tmp_path / "file.bin").exists()
    finally:
        srv.shutdown()


@pytest.mark.parametrize("dest,audio,expected", [
    (["Downloads"], False, ["Downloads", "فيديو"]),
    (["Downloads", "فيديو"], False, ["Downloads", "فيديو"]),
    (["Downloads", "موسيقى"], True, ["Downloads", "موسيقى"]),
    (["Downloads", "فيديو", ""], False, ["Downloads", "فيديو", ""]),
])
def test_media_folder_is_not_nested(dest, audio, expected):
    d = media.MediaDownload("https://example.com/v", os.path.join(*dest), audio_only=audio, subfolder=True)
    assert os.path.normpath(d._out_dir()) == os.path.normpath(os.path.join(*expected))


def test_media_without_file_is_not_completed(tmp_path, monkeypatch):
    """صفحة بلا فيديو (مثل الصفحة الرئيسية ليوتيوب): لا تُسجَّل «مكتملة» بلا ملف."""
    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {"title": "recommended"}

        def process_ie_result(self, info, download=False):
            return {"title": "recommended"}

    class Fake:
        YoutubeDL = FakeYDL

    monkeypatch.setattr(media, "yt_dlp", Fake, raising=False)
    d = media.MediaDownload("https://example.com/feed", str(tmp_path))
    with pytest.raises(RuntimeError):
        d._attempt({})
    assert d.state != COMPLETED


def test_show_endpoint(tmp_path):
    calls = []
    cfg = Config(str(tmp_path / "cfg.json"))
    cfg["port"] = 0
    mgr = DownloadManager(cfg, state_path=str(tmp_path / "state.json"))
    srv = ApiServer(mgr, cfg, on_show=lambda: calls.append(1))
    srv.start()
    base = f"http://127.0.0.1:{srv.port}"
    try:
        assert requests.post(base + "/show", json={}).status_code == 401      # بلا رمز: مرفوض
        assert not calls
        cfg["port"] = srv.port
        from sayl import setup_tools
        assert setup_tools.show_running(cfg) is True
        assert calls == [1]
    finally:
        srv.stop()
        mgr.shutdown()


def test_show_running_false_when_nothing_listens(tmp_path):
    from sayl import setup_tools
    cfg = Config(str(tmp_path / "cfg.json"))
    cfg["port"] = 1
    assert setup_tools.show_running(cfg) is False
