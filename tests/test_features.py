import datetime as dt
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_engine import server, wait_for  # noqa: E402,F401

from sayl import media, power  # noqa: E402
from sayl.config import Config  # noqa: E402
from sayl.engine import COMPLETED, QUEUED  # noqa: E402
from sayl.manager import DownloadManager, DuplicateError  # noqa: E402
from sayl.schedule import in_window, next_boundary  # noqa: E402
from sayl.server import ApiServer  # noqa: E402


def T(h, m=0):
    return dt.datetime(2026, 1, 1, h, m)


def mk(tmp_path, **cfg_over):
    cfg = Config(str(tmp_path / "cfg.json"))
    cfg["download_dir"] = str(tmp_path / "dl")
    cfg["port"] = 0
    for k, v in cfg_over.items():
        cfg[k] = v
    clock = {"t": T(12)}
    mgr = DownloadManager(cfg, state_path=str(tmp_path / "s.json"), autostart=False,
                          now_fn=lambda: clock["t"])
    return cfg, mgr, clock


def test_windows():
    assert in_window(T(3), "02:00", "07:00") and not in_window(T(8), "02:00", "07:00")
    assert in_window(T(23), "22:00", "06:00") and in_window(T(5), "22:00", "06:00")
    assert not in_window(T(12), "22:00", "06:00")
    assert in_window(T(12), "05:00", "05:00")
    assert next_boundary(T(12), "22:00", "06:00") == dt.datetime(2026, 1, 1, 22, 0)
    assert next_boundary(T(23), "22:00", "06:00") == dt.datetime(2026, 1, 2, 6, 0)


def test_schedule_gate_and_force(server, tmp_path):
    cfg, mgr, clock = mk(tmp_path, schedule_enabled=True, sched_start="02:00", sched_stop="07:00")
    d = mgr.add(server + "/file.bin", force=False)
    mgr._tick()
    assert d.state == QUEUED and not d.active          # خارج النافذة
    clock["t"] = T(3)
    mgr._tick()
    wait_for(lambda: d.state == COMPLETED)
    # الإضافة اليدوية (force) تتجاوز النافذة
    d2 = mgr.add(server + "/file.bin?x=1")
    clock["t"] = T(12)
    mgr._tick()
    wait_for(lambda: d2.state == COMPLETED)


def test_peak_limit(tmp_path):
    cfg, mgr, clock = mk(tmp_path, speed_limit_kb=0, peak_enabled=True, peak_start="09:00",
                         peak_stop="17:00", peak_kb=300)
    assert mgr.effective_limit_kb() == 300
    mgr._tick()
    assert mgr.limiter.rate == 300 * 1024
    clock["t"] = T(20)
    mgr._tick()
    assert mgr.limiter.rate == 0


def test_subfolders(server, tmp_path):
    cfg, mgr, _ = mk(tmp_path, subfolders=True)
    d = mgr.add(server + "/file.bin")
    mgr._tick()
    wait_for(lambda: d.state == COMPLETED)
    assert os.path.dirname(os.path.join(d.folder, d.filename)) == d.folder
    assert d.folder != cfg["download_dir"] and os.path.exists(os.path.join(d.folder, d.filename))
    # الحفظ والتحميل يحفظان المجلد النهائي
    mgr.save()
    m2 = DownloadManager(cfg, state_path=mgr.state_path, autostart=False)
    assert m2.items[d.id].folder == d.folder


def test_duplicates(server, tmp_path):
    cfg, mgr, _ = mk(tmp_path)
    d = mgr.add(server + "/file.bin", start=False)
    with pytest.raises(DuplicateError) as e:
        mgr.add(server + "/file.bin#frag", start=False)
    assert e.value.existing is d
    mgr.add(server + "/file.bin", start=False, allow_duplicate=True)
    cfg["warn_duplicates"] = False
    mgr.add(server + "/file.bin", start=False)


def test_duplicate_api(server, tmp_path):
    cfg, mgr, _ = mk(tmp_path)
    api = ApiServer(mgr, cfg)
    api.start()
    base, h = f"http://127.0.0.1:{api.port}", {"X-Sayl-Token": cfg["token"]}
    try:
        a = requests.post(base + "/add", headers=h, json={"url": server + "/file.bin"}).json()
        b = requests.post(base + "/add", headers=h, json={"url": server + "/file.bin"}).json()
        assert a["ok"] and not a.get("duplicate")
        assert b["ok"] and b["duplicate"] and b["id"] == a["id"]
    finally:
        api.stop()
        mgr.shutdown()


def test_watch_folder(tmp_path):
    w = tmp_path / "watch"
    w.mkdir()
    (w / "links.txt").write_text("a\nhttps://x.test/a.zip\nhttps://x.test/b.zip junk\nftp://no\n")
    (w / "other.png").write_text("https://x.test/c.zip")
    cfg, mgr, _ = mk(tmp_path, watch_enabled=True, watch_folder=str(w))
    for d in list(mgr.items.values()):
        d.state = "paused"
    assert mgr._watch_tick() == 2
    assert (w / "links.txt.done").exists() and (w / "other.png").exists()
    assert len(mgr.items) == 2 and mgr.pop_events()
    assert mgr._watch_tick() == 0
    mgr.shutdown()


def test_subtitle_opts(tmp_path):
    d = media.MediaDownload("https://x.test/v", str(tmp_path), subs=["ar", "en"], subfolder=True)
    o = d._build_opts(media._Log())
    assert o["writesubtitles"] and o["subtitleslangs"] == ["ar", "en"]
    assert o["outtmpl"].startswith(str(tmp_path)) and os.path.dirname(o["outtmpl"]) != str(tmp_path)
    d2 = media.MediaDownload("https://x.test/v", str(tmp_path), subs=["ar"], audio_only=True)
    assert "writesubtitles" not in d2._build_opts(media._Log())


def test_playlist_detection_and_probe(monkeypatch):
    assert media.looks_like_playlist("https://www.youtube.com/playlist?list=PL1")
    assert media.looks_like_playlist("https://www.youtube.com/watch?list=PL1")
    assert not media.looks_like_playlist("https://www.youtube.com/watch?v=abc&list=PL1")

    class FakeYDL:
        def __init__(self, o): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def extract_info(self, url, download=False):
            return {"_type": "playlist", "title": "P", "entries": [
                {"url": "https://v.test/1", "title": "one"}, None, {"id": "zz", "ie_key": "Youtube", "title": "two"}]}

    monkeypatch.setattr(media.yt_dlp, "YoutubeDL", FakeYDL)
    r = media.probe("https://www.youtube.com/playlist?list=PL1")
    assert r["playlist"] and [e["title"] for e in r["entries"]] == ["one", "two"]
    assert r["entries"][1]["url"].endswith("watch?v=zz")


def test_power_calls_subprocess(monkeypatch):
    calls = []
    monkeypatch.setattr(power.subprocess, "Popen", lambda c: calls.append(c))
    power.shutdown(5)
    assert calls and calls[0]


def test_selfupdate_helpers(monkeypatch):
    from sayl import selfupdate as su
    assert su.is_newer("v2.2.0", "2.1.0") and not su.is_newer("2.1.0", "2.1.0")
    assert su.clean_repo("https://github.com/me/sayl.git") == "me/sayl" and su.clean_repo("x y") == ""
    assert not su._safe_url("https://evil.com/Sayl.exe") and not su._safe_url("http://github.com/a")

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return b""
    import io, json as _j
    payload = {"tag_name": "v9.0.0", "html_url": "https://github.com/me/sayl/releases/v9", "body": "x",
               "assets": [{"name": "Sayl.exe", "browser_download_url": "https://github.com/a/Sayl.exe",
                           "size": 5, "digest": "sha256:abc"}]}
    monkeypatch.setattr(su, "_get", lambda url, timeout=15: io.StringIO(_j.dumps(payload)))
    info = su.check("me/sayl")
    assert info["newer"] and info["latest"] == "9.0.0" and info["sha256"] == "abc"
    with pytest.raises(RuntimeError):
        su.check("bad repo")
    info["asset_url"] = "https://evil.com/x"
    with pytest.raises(RuntimeError):
        su.download(info)
