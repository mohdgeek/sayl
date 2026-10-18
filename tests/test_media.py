import functools
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sayl import media  # noqa: E402
from sayl.categories import category_of  # noqa: E402
from sayl.engine import COMPLETED, PAUSED  # noqa: E402
from sayl.media import MediaDownload, build_format, is_media_url  # noqa: E402

needs_media = pytest.mark.skipif(not (media.available() and shutil.which("ffmpeg")),
                                 reason="يحتاج yt-dlp وffmpeg")


def wait_for(cond, timeout=90):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_is_media_url():
    assert is_media_url("https://www.youtube.com/watch?v=abc")
    assert is_media_url("https://youtu.be/abc")
    assert is_media_url("https://cdn.example.com/a/b/master.m3u8?token=1")
    assert is_media_url("https://x.com/user/status/1")
    assert not is_media_url("https://example.com/file.zip")
    assert not is_media_url("https://notyoutube.com/watch")      # لا يطابق جزءًا من الاسم
    assert not is_media_url("not a url")


def test_category_of():
    assert category_of("a.PDF") == "docs"
    assert category_of("a.zip") == "archive"
    assert category_of("a.exe") == "apps"
    assert category_of("a.jpg") == "images"
    assert category_of("noext") == "other"
    assert category_of("clip.mp4", "media") == "video"
    assert category_of("song.mp3", "media") == "audio"


def test_build_format():
    assert build_format(None, False, True) == "bv*+ba/b"
    assert build_format(720, False, True).startswith("bv*[height<=720]")
    assert build_format(720, False, False) == "b[height<=720]/b"
    assert build_format(None, True, True) == "bestaudio/best"


@pytest.fixture(scope="module")
def hls_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("hls")
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=duration=6:size=320x240:rate=15",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-hls_time", "2", "-hls_playlist_type", "vod",
         "-hls_segment_filename", str(root / "seg%d.ts"), str(root / "stream.m3u8")],
        check=True)
    h = functools.partial(SimpleHTTPRequestHandler, directory=str(root))
    h.log_message = lambda *a: None
    SimpleHTTPRequestHandler.log_message = lambda *a: None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@needs_media
def test_probe_hls(hls_server):
    info = media.probe(hls_server + "/stream.m3u8")
    assert info["duration"] and 5 < info["duration"] < 8


@needs_media
def test_download_hls_to_mp4(hls_server, tmp_path):
    d = MediaDownload(hls_server + "/stream.m3u8", str(tmp_path))
    d.start()
    assert wait_for(lambda: d.state in (COMPLETED, "error")), d.state
    assert d.state == COMPLETED, d.error
    out = tmp_path / d.filename
    assert out.exists() and out.stat().st_size > 5000
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True)
    assert 5 < float(probe.stdout.strip()) < 8        # ملف فيديو سليم قابل للتشغيل
    assert d.progress == 1.0


@needs_media
def test_media_bad_url_reports_error(tmp_path):
    d = MediaDownload("http://127.0.0.1:9/none.m3u8", str(tmp_path))
    d.start()
    assert wait_for(lambda: d.state in (COMPLETED, "error"), 60)
    assert d.state == "error" and d.error


@needs_media
def test_manager_routes_media_and_persists(hls_server, tmp_path):
    from sayl.config import Config
    from sayl.manager import DownloadManager
    cfg = Config(str(tmp_path / "c.json"))
    cfg["download_dir"] = str(tmp_path / "dl")
    state = str(tmp_path / "s.json")
    m = DownloadManager(cfg, state_path=state)
    d = m.add(hls_server + "/stream.m3u8")           # kind=auto → media
    assert d.kind == "media"
    assert wait_for(lambda: d.state in (COMPLETED, "error")), d.error
    assert d.state == COMPLETED, d.error
    m.shutdown()
    m2 = DownloadManager(cfg, state_path=state, autostart=False)
    d2 = m2.snapshot()[0]
    assert d2.kind == "media" and d2.state == COMPLETED and d2.filename == d.filename
