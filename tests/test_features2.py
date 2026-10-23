"""اختبارات الاختصار العام وإضافة Firefox."""
import json
import os
import zipfile

from sayl import hotkey, setup_tools
from sayl.config import Config


def test_hotkey_parse():
    assert hotkey.parse("Ctrl+Alt+S") == (0x3, ord("S"))
    assert hotkey.parse("ctrl+shift+f5") == (0x6, 0x74)
    assert hotkey.parse("Win+1") == (0x8, ord("1"))
    assert hotkey.parse("S") is None            # بلا معدِّل
    assert hotkey.parse("Ctrl+") is None
    assert hotkey.parse("Ctrl+Alt+Enter") is None
    assert hotkey.parse("") is None
    g = hotkey.GlobalHotkey(lambda: None)
    if not os.name == "nt":
        assert g.start("Ctrl+Alt+S") is False and g.error


def test_firefox_export(tmp_path):
    cfg = Config(str(tmp_path / "c.json"))
    folder, xpi = setup_tools.export_firefox(cfg, str(tmp_path / "ff"))
    m = json.load(open(os.path.join(folder, "manifest.json"), encoding="utf-8"))
    assert "key" not in m and m["background"]["scripts"][0] == "shim.js"
    assert m["browser_specific_settings"]["gecko"]["id"] == setup_tools.FIREFOX_ID
    assert all(cs["js"][0] == "shim.js" for cs in m["content_scripts"])
    with zipfile.ZipFile(xpi) as z:
        names = z.namelist()
        assert "manifest.json" in names and "background.js" in names and "shim.js" in names
        assert json.loads(z.read("pair.json"))["token"] == cfg["token"]
    # الإضافة الأصلية (Chrome) لم تتغيّر
    orig = json.load(open(os.path.join(setup_tools.resource_path("extension"), "manifest.json"), encoding="utf-8"))
    assert "key" in orig and "service_worker" in orig["background"]


def test_native_host_has_firefox_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_tools, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(setup_tools, "host_command", lambda: "/bin/true")
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path) if p == "~" else p)
    setup_tools.register_native_host(registry=False)
    ff = json.load(open(tmp_path / "com.sayl.host.firefox.json", encoding="utf-8"))
    assert ff["allowed_extensions"] == [setup_tools.FIREFOX_ID] and "allowed_origins" not in ff
    ch = json.load(open(tmp_path / "com.sayl.host.json", encoding="utf-8"))
    assert ch["allowed_origins"]


def test_rules_match():
    from sayl import rules
    cfg = {"rules": [{"type": "ext", "value": "pdf, .DOCX", "dir": "D:/docs"},
                     {"type": "host", "value": "example.com", "dir": "D:/ex"},
                     {"type": "cat", "value": "video", "dir": "D:/vid"}]}
    assert rules.folder_for(cfg, "https://a.org/x/Report.PDF?z=1") == "D:/docs"
    assert rules.folder_for(cfg, "https://cdn.example.com/f.bin") == "D:/ex"
    assert rules.folder_for(cfg, "https://www.example.com/f.bin") == "D:/ex"
    assert rules.folder_for(cfg, "https://notexample.com/f.bin") is None
    assert rules.folder_for(cfg, "https://a.org/f", "movie.mkv", "video") == "D:/vid"
    assert rules.folder_for({"rules": []}, "https://a.org/f.pdf") is None


def test_manager_rules_hash_rename(tmp_path):
    import hashlib
    import time
    from sayl.manager import DownloadManager, normalize_hash
    cfg = Config(str(tmp_path / "c.json"))
    cfg["download_dir"] = str(tmp_path / "dl")
    cfg["rules"] = [{"type": "ext", "value": "pdf", "dir": str(tmp_path / "pdfs")}]
    m = DownloadManager(cfg, state_path=str(tmp_path / "s.json"), autostart=False)
    d = m.add("https://x.example/a/file.pdf", start=False)
    assert d.dest_dir == str(tmp_path / "pdfs")
    d2 = m.add("https://x.example/a/file.zip", start=False)
    assert d2.dest_dir == str(tmp_path / "dl")
    assert normalize_hash("SHA256: " + "AB" * 32) == "ab" * 32
    assert normalize_hash("zz") == "" and normalize_hash("a" * 31) == ""
    # تحقق من بصمة ملف مكتمل
    os.makedirs(d2.dest_dir)
    p = os.path.join(d2.dest_dir, "file.zip")
    open(p, "wb").write(b"hello sayl")
    d2.state, d2.filename = "completed", "file.zip"
    good = hashlib.sha256(b"hello sayl").hexdigest()
    assert m.set_expected_hash(d2.id, good)
    m._verify_pending()
    for _ in range(50):
        if d2.hash_state in ("ok", "bad"):
            break
        time.sleep(0.05)
    assert d2.hash_state == "ok"
    assert m.set_expected_hash(d2.id, "0" * 64)
    m._verify_pending()
    for _ in range(50):
        if d2.hash_state == "bad":
            break
        time.sleep(0.05)
    assert d2.hash_state == "bad"
    assert any("لا تطابق" in e for e in m.pop_events())
    assert not m.set_expected_hash(d2.id, "not a hash")
    m.save()
    assert DownloadManager(cfg, state_path=str(tmp_path / "s.json"), autostart=False).items[d2.id].hash_state == "bad"
    # إعادة التسمية
    m.rename(d2.id, "new:name?.zip")
    assert d2.filename == "new_name_.zip" and os.path.exists(os.path.join(d2.dest_dir, "new_name_.zip"))
    open(os.path.join(d2.dest_dir, "other.zip"), "wb").write(b"x")
    import pytest
    with pytest.raises(ValueError):
        m.rename(d2.id, "other.zip")


def test_clean_name():
    from sayl.engine import clean_name
    assert clean_name("[www.site.com] My_Movie_2024.mkv") == "My Movie 2024.mkv"
    assert clean_name("www.site.com - Song.mp3") == "Song.mp3"
    assert clean_name("Win.11.Aio.26200.Multi.iso") == "Win 11 Aio 26200 Multi.iso"
    assert clean_name("report v1.2.pdf") == "report v1.2.pdf"
    assert clean_name("%D9%85%D9%84%D9%81.zip") == "ملف.zip"


def test_auto_retry_and_refresh_url(tmp_path, monkeypatch):
    """فشل بسبب الشبكة => إعادة تلقائية بتأخير، ولا استهلاك للمحاولات وقت انقطاع الشبكة؛ وتحديث الرابط."""
    import socket
    import time
    from sayl import manager as mgrmod
    from sayl.engine import ERROR, QUEUED, is_network_error
    from sayl.manager import DownloadManager
    assert is_network_error("ConnectionResetError(10054)") and is_network_error("Read timed out")
    assert not is_network_error("HTTP 403") and not is_network_error("certificate verify failed")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                     # منفذ مغلق => رفض الاتصال
    cfg = Config(str(tmp_path / "c.json"))
    cfg["download_dir"] = str(tmp_path / "dl")
    m = DownloadManager(cfg, state_path=str(tmp_path / "s.json"), autostart=False)
    d = m.add(f"http://127.0.0.1:{port}/f.bin", start=True)
    d.start()
    d.wait(15)
    assert d.state == ERROR and d.net_error, (d.state, d.error)
    online = {"v": False}
    monkeypatch.setattr(mgrmod, "is_online", lambda timeout=2.0: online["v"])
    m._online_at = 0
    m._auto_retry()
    assert d.retry_at is not None and d.state == ERROR
    d.retry_at = time.time() - 1
    m._auto_retry()
    assert d.state == ERROR and d.retries == 0          # الشبكة غير متاحة: لم تُستهلك محاولة
    online["v"], m._online_at = True, 0
    d.retry_at = time.time() - 1
    m._auto_retry()
    assert d.state == QUEUED and d.retries == 1
    cfg["auto_retry"] = False
    d.state, d.retry_at = ERROR, time.time() - 1
    m._auto_retry()
    assert d.state == ERROR
    # تحديث الرابط
    d2 = m.refresh_url(d.id, "http://127.0.0.1:1/new.bin")
    assert d2.url.endswith("new.bin") and d2.final_url.endswith("new.bin") and d2.state == QUEUED and d2.size is None
    import pytest
    with pytest.raises(ValueError):
        m.refresh_url(d.id, "ftp://x")


def test_stats(tmp_path):
    import datetime as dt
    from sayl.manager import Stats
    s = Stats(str(tmp_path / "stats.json"))
    today = dt.date(2026, 10, 23)
    s.add(1000, "a.com", "2026-10-23")
    s.add(500, "b.com", "2026-10-20")
    s.add(2000, "a.com", "2026-09-01")
    sm = Stats(str(tmp_path / "stats.json")).summary(today)         # يُحمَّل من القرص
    assert sm["today"] == 1000 and sm["week"] == 1500 and sm["month"] == 1500
    assert sm["total"] == 3500 and sm["files"] == 3
    assert sm["hosts"][0] == ("a.com", 3000) and len(sm["series"]) == 14 and sm["series"][-1] == ("2026-10-23", 1000)


def test_completed_counted_once(tmp_path):
    from sayl.manager import DownloadManager
    cfg = Config(str(tmp_path / "c.json"))
    m = DownloadManager(cfg, state_path=str(tmp_path / "s.json"), autostart=False)
    d = m.add("https://x.example/a.bin", start=False)
    d.state, d.size = "completed", 4096
    m._count_completed()
    m._count_completed()
    assert m.stats.data["total"] == 4096 and m.stats.data["files"] == 1
    m.save()
    m2 = DownloadManager(cfg, state_path=str(tmp_path / "s.json"), autostart=False)
    m2._count_completed()
    assert m2.stats.data["total"] == 4096          # لم يُعَد عدّه بعد إعادة التشغيل
