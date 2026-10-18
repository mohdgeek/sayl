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


def test_export_extension_pairs(tmp_path, monkeypatch):
    import json
    from sayl import setup_tools as st
    cfg = Config(str(tmp_path / "c.json"))
    dest = st.export_extension(cfg, str(tmp_path / "ext"))
    assert os.path.isfile(os.path.join(dest, "manifest.json"))
    p = json.load(open(os.path.join(dest, "pair.json")))
    assert p["token"] == cfg["token"] and p["port"] == cfg["port"]
    st.export_extension(cfg, dest)                      # إعادة التصدير فوق القديم تعمل
    # أثناء التغليف تُقرأ الإضافة من _MEIPASS
    fake = tmp_path / "meipass"
    (fake / "extension").mkdir(parents=True)
    (fake / "extension" / "manifest.json").write_text("{}")
    monkeypatch.setattr(st.sys, "_MEIPASS", str(fake), raising=False)
    assert st.resource_path("extension") == str(fake / "extension")


def test_pairing_window(tmp_path):
    from sayl import server as sv
    cfg, mgr, _ = mk(tmp_path)
    api = ApiServer(mgr, cfg)
    api.start()
    base = f"http://127.0.0.1:{api.port}"
    ext = {"Origin": "chrome-extension://abc"}
    try:
        sv._PAIR["until"] = 0
        assert requests.get(base + "/pair", headers=ext).status_code == 403    # مغلق
        sv.open_pairing(60)
        assert requests.get(base + "/pair", headers={"Origin": "http://evil.com"}).status_code == 403
        assert requests.get(base + "/pair").status_code == 403                  # بلا Origin
        assert requests.get(base + "/pair", headers={**ext, "Host": "evil.com"}).status_code == 403
        r = requests.get(base + "/pair", headers=ext)
        assert r.status_code == 200 and r.json()["token"] == cfg["token"]
    finally:
        sv._PAIR["until"] = 0
        api.stop()
        mgr.shutdown()


def test_update_env_is_clean():
    from sayl import selfupdate as su
    env = su.clean_env({"PATH": "x", "_MEIPASS2": "C:/old", "_PYI_APPLICATION_HOME_DIR": "C:/old",
                        "_PYI_ARCHIVE_FILE": "a", "KEEP": "1"})
    assert not any(k.startswith("_PYI") or k == "_MEIPASS2" for k in env)
    assert env["KEEP"] == "1" and env["PATH"] == "x" and env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_extension_stats_and_browsers(tmp_path, monkeypatch):
    from sayl import server as sv, setup_tools as st
    cfg, mgr, _ = mk(tmp_path)
    api = ApiServer(mgr, cfg)
    api.start()
    base = f"http://127.0.0.1:{api.port}"
    sv.STATS.update(seen=0.0, bad=0.0, browser="")
    try:
        h = {"Origin": "chrome-extension://abc", "User-Agent": "Mozilla Chrome/130 Edg/130"}
        requests.get(base + "/ping", headers={**h, "X-Sayl-Token": "wrong"})
        assert sv.STATS["bad"] > 0 and sv.STATS["seen"] == 0
        requests.get(base + "/ping", headers={**h, "X-Sayl-Token": cfg["token"]})
        assert sv.STATS["seen"] > 0 and sv.STATS["browser"] == "Edge"
    finally:
        api.stop()
        mgr.shutdown()
    fake = tmp_path / "msedge.exe"
    fake.write_text("x")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setattr(st, "BROWSERS", [("edge", "Microsoft Edge", "edge", ("msedge.exe",), "nope")])
    b = st.find_browsers()[0]
    assert b["exe"] == str(fake) and b["scheme"] == "edge"


def test_native_host_protocol(tmp_path, monkeypatch):
    import io, json as _j, struct
    from sayl import host
    cfg, mgr, _ = mk(tmp_path)
    api = ApiServer(mgr, cfg)
    api.start()
    cfg["port"] = api.port
    conf = {"port": api.port, "token": cfg["token"]}
    try:
        r = host.handle({"id": 7, "path": "/ping", "method": "GET", "ua": "Edg/130"}, conf, launch=False)
        assert r["id"] == 7 and r["status"] == 200 and r["json"]["app"] == "Sayl"
        r = host.handle({"id": 8, "path": "/add", "method": "POST", "body": {"url": "file:///x"}}, conf, launch=False)
        assert r["status"] == 400
        r = host.handle({"id": 9, "path": "/ping"}, {"port": api.port, "token": "bad"}, launch=False)
        assert r["status"] == 401
        assert host.handle({"id": 1, "path": "http://evil"}, conf)["error"] == "bad_request"
        # بروتوكول الإطار: طول 4 بايت + JSON
        msg = _j.dumps({"id": 3, "path": "/ping"}).encode()
        out = io.BytesIO()
        monkeypatch.setattr(host, "read_config", lambda path=None: conf)
        host.main(io.BytesIO(struct.pack("<I", len(msg)) + msg), out)
        data = out.getvalue()
        assert _j.loads(data[4:4 + struct.unpack("<I", data[:4])[0]])["status"] == 200
    finally:
        api.stop()
        mgr.shutdown()
    # سيل مغلق ولا مسار لتشغيله: خطأ واضح
    assert host.handle({"id": 5, "path": "/ping"}, {"port": 1, "token": "x"}, launch=True)["error"] == "app_not_running"


def test_register_native_host(tmp_path):
    import json as _j
    from sayl import setup_tools as st
    d = tmp_path / "nm"
    written = st.register_native_host(extra_dirs=[str(d)], registry=False)
    m = _j.load(open(d / "com.sayl.host.json"))
    assert m["name"] == "com.sayl.host" and m["type"] == "stdio" and os.path.isfile(m["path"])
    assert m["allowed_origins"] == [f"chrome-extension://{st.extension_id()}/"] and written


def test_self_test_host_and_status(tmp_path, monkeypatch):
    from sayl import setup_tools as st
    cfg, mgr, _ = mk(tmp_path)
    api = ApiServer(mgr, cfg)
    api.start()
    cfg["port"] = api.port
    cfg.save()
    monkeypatch.setenv("SAYL_CONFIG", cfg.path)
    try:
        ok, msg = st.self_test_host(timeout=20)
        assert ok, msg
    finally:
        api.stop()
        mgr.shutdown()
    ok, msg = st.self_test_host(timeout=20)      # بعد إيقاف سيل: الجسر يردّ خطأً واضحًا لا يعلّق
    assert not ok
    st.register_native_host(extra_dirs=[str(tmp_path / "nm")], registry=False)
    assert all(ok for _, ok in st.native_status())


def test_token_selfheal_from_disk(tmp_path):
    """عملية تحمل رمزًا قديمًا في الذاكرة تقبل الرمز الحالي في الملف."""
    from sayl import host
    cfg, mgr, _ = mk(tmp_path)
    api = ApiServer(mgr, cfg)
    api.start()
    try:
        cfg.data["token"] = "stale-in-memory"          # الذاكرة قديمة
        disk = {"port": api.port, "token": "fresh-on-disk"}
        import json as _j
        p = _j.load(open(cfg.path))
        p["token"] = "fresh-on-disk"
        _j.dump(p, open(cfg.path, "w"))                  # الملف أحدث
        r = host.handle({"id": 1, "path": "/ping"}, disk, launch=False)
        assert r["status"] == 200
        r = host.handle({"id": 2, "path": "/ping"}, {"port": api.port, "token": "wrong"}, launch=False)
        assert r["status"] == 401 and r["json"]["server_fp"] and r["json"]["host_fp"]
    finally:
        api.stop()
        mgr.shutdown()


def test_port_fallback_when_busy(tmp_path):
    import socket
    from sayl.server import start_server
    cfg, mgr, _ = mk(tmp_path)
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    busy = blocker.getsockname()[1]
    cfg["port"] = busy
    srv, moved = start_server(mgr, cfg)
    try:
        assert moved and srv.port != busy and cfg["port"] == srv.port
        r = requests.get(f"http://127.0.0.1:{srv.port}/ping", headers={"X-Sayl-Token": cfg["token"]})
        assert r.status_code == 200
    finally:
        srv.stop()
        blocker.close()
        mgr.shutdown()
    cfg["port"] = 0
    srv2, moved2 = start_server(mgr, cfg)
    assert not moved2
    srv2.stop()
