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


def test_default_sorting_and_migration(tmp_path):
    import json as _j
    from sayl.manager import DownloadManager
    p = tmp_path / "old.json"
    p.write_text(_j.dumps({"subfolders": False, "download_dir": str(tmp_path / "dl")}), encoding="utf-8")
    cfg = Config(str(p))
    assert cfg["subfolders"] is True                      # ترحيل لمرة واحدة
    cfg["subfolders"] = False                             # المستخدم عطّله بنفسه بعد ذلك
    cfg.save()
    assert Config(str(p))["subfolders"] is False          # لا يُفرض عليه ثانية
    fresh = Config(str(tmp_path / "new.json"))
    assert fresh["subfolders"] is True
    fresh["download_dir"] = str(tmp_path / "dl2")
    m = DownloadManager(fresh, state_path=str(tmp_path / "s.json"), autostart=False)
    t = m.add("magnet:?xt=urn:btih:" + "cd" * 20, start=False)
    assert t.dest_dir == os.path.join(str(tmp_path / "dl2"), "تورنت")
    v = m.add("https://x.example/clip.mp4", start=False)
    assert v.subfolder is True and v.dest_dir == str(tmp_path / "dl2")    # يُرتَّب عند الاكتمال إلى «فيديو»


def test_keep_awake(monkeypatch):
    from sayl import power
    calls = []
    monkeypatch.setattr(power.sys, "platform", "win32")
    monkeypatch.setattr(power, "_set_execution_state", lambda f: calls.append(f) or 1)
    monkeypatch.setattr(power, "_awake", False)
    assert power.keep_awake(True) is True
    assert power.keep_awake(True) is False                  # إعادة تأكيد دون تغيّر
    assert power.keep_awake(False) is True
    assert calls == [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED] * 2 + [power.ES_CONTINUOUS]
    monkeypatch.setattr(power.sys, "platform", "linux")
    assert power.keep_awake(True) is False                  # لا أثر خارج ويندوز


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


def test_resolvers_pure():
    from sayl import resolvers as r
    assert r.kind_of("https://www.dropbox.com/s/abc/f.zip?dl=0") == "dropbox"
    assert r.dropbox_direct("https://www.dropbox.com/scl/fi/x/f.zip?rlkey=K&dl=0") == \
        "https://www.dropbox.com/scl/fi/x/f.zip?rlkey=K&dl=1"
    assert r.gdrive_id("https://drive.google.com/file/d/1AbCdEfGhIjKlMn/view?usp=sharing") == "1AbCdEfGhIjKlMn"
    assert r.gdrive_id("https://drive.google.com/open?id=1AbCdEfGhIjKlMn") == "1AbCdEfGhIjKlMn"
    assert r.gdrive_id("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMn") is None
    assert not r.handles("https://drive.usercontent.google.com/download?id=1&export=download")
    form = ('<form id="download-form" action="https://drive.usercontent.google.com/download" method="get">'
            '<input type="hidden" name="id" value="1Abc"><input type="hidden" name="export" value="download">'
            '<input type="hidden" name="confirm" value="t"><input type="hidden" name="uuid" value="u-1"></form>'
            '<span class="uc-name-size"><a href="x">big file.zip</a></span>')
    nxt = r.parse_gdrive_form(form)
    assert nxt.startswith("https://drive.usercontent.google.com/download?") and "uuid=u-1" in nxt and "confirm=t" in nxt
    assert r.parse_gdrive_name(form) == "big file.zip"
    assert r.parse_mediafire('<a class="input" id="downloadButton" href="https://download1.mediafire.com/x/y/file.zip">') \
        == "https://download1.mediafire.com/x/y/file.zip"
    import base64
    scr = base64.b64encode(b"https://download2.mediafire.com/a/b/c.rar").decode()
    assert r.parse_mediafire(f'<a data-scrambled-url="{scr}" id="downloadButton" href="#">') == \
        "https://download2.mediafire.com/a/b/c.rar"
    assert r.parse_mediafire("<html>nothing</html>") is None
    assert r.telegram_parts("https://t.me/somechan/123") == ("somechan", "123")
    assert r.telegram_parts("https://t.me/s/somechan/45?embed=1") == ("somechan", "45")
    assert r.telegram_parts("https://t.me/somechan") is None
    assert r.parse_telegram('<video src="https://cdn4.telesco.pe/file/v.mp4?x=1&amp;y=2" class="x">') == \
        ("https://cdn4.telesco.pe/file/v.mp4?x=1&y=2", "mp4")
    assert r.parse_telegram("<a class=\"tgme_widget_message_photo_wrap\" style=\"width:1px;background-image:url('https://cdn.telesco.pe/p.jpg')\">") == \
        ("https://cdn.telesco.pe/p.jpg", "jpg")
    assert r.parse_telegram("<div>text only</div>") is None


def test_resolve_with_fake_session():
    import pytest
    from sayl import resolvers as r

    class Resp:
        def __init__(self, text="", status=200, ctype="text/html"):
            self.text, self.status_code, self.headers = text, status, {"Content-Type": ctype}

        def close(self):
            pass

    class S:
        def __init__(self, routes):
            self.routes = routes

        def get(self, url, **kw):
            for k, v in self.routes.items():
                if k in url:
                    return v
            return Resp(status=404)

    assert r.resolve("https://example.org/x", S({})) is None
    # Drive: صفحة التأكيد ثم الرابط المباشر
    page = ('<form id="download-form" action="https://drive.usercontent.google.com/download"><input type="hidden" name="id" value="F1">'
            '<input type="hidden" name="confirm" value="t"></form>')
    out = r.resolve("https://drive.google.com/file/d/F1AAAAAAAAAA/view", S({"drive.usercontent.google.com/download?id": Resp(page)}))
    assert "confirm=t" in out["url"]
    ok = r.resolve("https://drive.google.com/file/d/F1AAAAAAAAAA/view",
                   S({"drive.usercontent.google.com/download?id": Resp("", ctype="application/zip")}))
    assert ok["url"].endswith("confirm=t")
    with pytest.raises(IOError, match="خاص"):
        r.resolve("https://drive.google.com/file/d/F1AAAAAAAAAA/view", S({"download?id": Resp(status=403)}))
    with pytest.raises(IOError, match="مجلدات"):
        r.resolve("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMn", S({}))
    mf = r.resolve("https://www.mediafire.com/file/abc123/my%20file.zip/file",
                   S({"mediafire.com/file": Resp('<a id="downloadButton" href="https://download9.mediafire.com/k/my%20file.zip">')}))
    assert mf["filename"] == "my file.zip"
    with pytest.raises(IOError, match="MediaFire"):
        r.resolve("https://www.mediafire.com/file/abc123/x/file", S({"mediafire.com/file": Resp("<html></html>")}))
    tg = r.resolve("https://t.me/chan/77", S({"t.me/chan/77?embed": Resp('<video src="https://cdn.telesco.pe/v.mp4">')}))
    assert tg == {"url": "https://cdn.telesco.pe/v.mp4", "filename": "chan_77.mp4"}
    with pytest.raises(IOError, match="تطبيق تيليجرام"):
        r.resolve("https://t.me/chan/78", S({"t.me/chan/78?embed": Resp("<div>doc</div>")}))
    assert r.resolve("https://www.dropbox.com/s/a/b.zip?dl=0", S({}))["url"].endswith("dl=1")


def _mk_zip(path, files):
    import zipfile as zf
    with zf.ZipFile(path, "w") as z:
        for n, data in files.items():
            z.writestr(n, data)


def test_extract_variants(tmp_path):
    import tarfile
    import pytest
    from sayl import extract
    assert extract.is_archive("A.TAR.GZ") and extract.is_archive("x.7z") and not extract.is_archive("x.iso")
    assert extract.stem_of("backup.tar.gz") == "backup" and extract.stem_of("a.b.zip") == "a.b"
    # مجلد جذر وحيد: لا مجلد مكرر
    z1 = tmp_path / "pack.zip"
    _mk_zip(z1, {"pack/a.txt": "A", "pack/sub/b.txt": "B"})
    out = extract.extract(str(z1))
    assert out == str(tmp_path / "pack") and (tmp_path / "pack" / "sub" / "b.txt").read_text() == "B"
    assert z1.exists()
    # عدة ملفات في الجذر: مجلد باسم الأرشيف، والتكرار يأخذ رقمًا
    z2 = tmp_path / "multi.zip"
    _mk_zip(z2, {"x.txt": "1", "y.txt": "2"})
    out2 = extract.extract(str(z2), delete=True)
    assert out2 == str(tmp_path / "multi") and not z2.exists()
    z3 = tmp_path / "multi.zip"
    _mk_zip(z3, {"x.txt": "1", "y.txt": "2"})
    assert extract.extract(str(z3)) == str(tmp_path / "multi (1)")
    # tar.gz
    src = tmp_path / "src"
    src.mkdir()
    (src / "f.txt").write_text("T")
    tg = tmp_path / "data.tar.gz"
    with tarfile.open(tg, "w:gz") as t:
        t.add(src / "f.txt", arcname="f.txt")
        t.add(src / "f.txt", arcname="g.txt")
    assert (tmp_path / "data").is_dir() is False
    assert os.path.isfile(os.path.join(extract.extract(str(tg)), "g.txt"))
    # مسار خارج المجلد مرفوض
    evil = tmp_path / "evil.zip"
    _mk_zip(evil, {"../outside.txt": "x"})
    with pytest.raises(IOError, match="خارج"):
        extract.extract(str(evil))
    assert not (tmp_path / "outside.txt").exists()
    assert not [p for p in os.listdir(tmp_path) if p.startswith(".sayl-extract")]    # لا مخلّفات
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(IOError, match="تالف"):
        extract.extract(str(bad))
    sev = tmp_path / "x.7z"
    sev.write_bytes(b"7z")
    if not extract.find_7zip():
        with pytest.raises(IOError, match="7-Zip"):
            extract.extract(str(sev))


def test_manager_auto_extract(tmp_path):
    import time
    from sayl.manager import DownloadManager
    cfg = Config(str(tmp_path / "c.json"))
    cfg["download_dir"] = str(tmp_path / "dl")
    m = DownloadManager(cfg, state_path=str(tmp_path / "s.json"), autostart=False)
    d = m.add("https://x.example/pack.zip", start=False)
    os.makedirs(d.dest_dir, exist_ok=True)
    _mk_zip(os.path.join(d.dest_dir, "pack.zip"), {"a.txt": "1", "b.txt": "2"})
    d.state, d.filename = "completed", "pack.zip"
    m._extract_pending()
    assert d.extract_state is None                    # الخيار مطفأ افتراضيًا
    cfg["auto_extract"] = True
    m._extract_pending()
    for _ in range(60):
        if d.extract_state in ("done", "error"):
            break
        time.sleep(0.05)
    assert d.extract_state == "done" and os.path.isfile(os.path.join(d.extract_dir, "a.txt"))
    assert any("تم فك ضغط" in e for e in m.pop_events())
    m.save()
    d2 = DownloadManager(cfg, state_path=str(tmp_path / "s.json"), autostart=False).items[d.id]
    assert d2.extract_state == "done" and d2.extract_dir == d.extract_dir     # لا يُفك ثانية بعد إعادة التشغيل
    d.extract_state = None
    m.extract_now(d.id)
    import pytest
    d3 = m.add("https://x.example/n.bin", start=False)
    with pytest.raises(ValueError):
        m.extract_now(d3.id)
