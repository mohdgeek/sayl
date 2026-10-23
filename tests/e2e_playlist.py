"""اختبار في Chromium حقيقي: زر «تحميل القائمة» في يوتيوب، والتقاط روابط magnet."""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from playwright.sync_api import sync_playwright  # noqa: E402

from sayl.config import Config  # noqa: E402
from sayl.manager import DownloadManager  # noqa: E402
from sayl.server import ApiServer  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXT = os.path.join(ROOT, "extension")
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
tmp = tempfile.mkdtemp()
cfg = Config(os.path.join(tmp, "c.json"))
cfg["download_dir"] = os.path.join(tmp, "dl")
cfg["port"] = 0
mgr = DownloadManager(cfg, state_path=os.path.join(tmp, "s.json"), autostart=False)
api = ApiServer(mgr, cfg)
api.start()
fails = []
HASH = "ab" * 20


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


def wait_count(n, secs=8):
    t0 = time.time()
    while time.time() - t0 < secs and len(mgr.snapshot()) < n:
        time.sleep(0.2)
    return len(mgr.snapshot()) >= n


with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        os.path.join(tmp, "profile"), executable_path=CHROME, headless=False,
        args=["--headless=new", "--no-sandbox", f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"],
        viewport={"width": 900, "height": 500})
    sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
    sw.evaluate("([port, token]) => chrome.storage.local.set({port, token, confirmVideo: false, confirmFiles: false})",
                [api.port, cfg["token"]])
    ctx.route("**/*", lambda route: route.fulfill(
        status=200, content_type="text/html",
        body=f'<!doctype html><meta charset="utf-8"><body style="margin:0;background:#111;height:2000px">'
             f'<a id="m" href="magnet:?xt=urn:btih:{HASH}&dn=Test" style="position:absolute;left:20px;top:20px;color:#fff">magnet</a></body>'))
    page = ctx.new_page()
    # 1) صفحة قائمة تشغيل يوتيوب: يظهر الزر ويرسل رابط القائمة
    page.goto("https://www.youtube.com/playlist?list=PLabcdefghijklmn")
    page.wait_for_timeout(2500)
    page.screenshot(path=os.path.join(tmp, "pl.png"))
    page.mouse.click(18 + 60, 500 - 18 - 18)
    check(wait_count(1), "زر القائمة أرسل عنصرًا")
    snap = mgr.snapshot()
    if snap:
        check(snap[0].url == "https://www.youtube.com/playlist?list=PLabcdefghijklmn", f"رابط القائمة ({snap[0].url})")
        check(snap[0].kind == "media", "النوع media")
    # 2) ميكس يوتيوب (RD) لا يُظهر الزر
    page2 = ctx.new_page()
    page2.goto("https://www.youtube.com/watch?v=AAAAAAAAAAA&list=RDAAAAAAAAAAA")
    page2.wait_for_timeout(2500)
    n = len(mgr.snapshot())
    page2.mouse.click(18 + 60, 500 - 18 - 18)
    page2.wait_for_timeout(800)
    check(len(mgr.snapshot()) == n, "قوائم الميكس لا تُرسل")
    # 3) رابط magnet
    page3 = ctx.new_page()
    page3.goto("https://example.com/")
    page3.wait_for_timeout(500)
    page3.click("#m")
    check(wait_count(n + 1), "رابط magnet أُرسل")
    tor = [d for d in mgr.snapshot() if d.kind == "torrent"]
    check(bool(tor) and HASH in tor[0].url, "العنصر من نوع torrent")
    ctx.close()
api.stop()
print("E2E PLAYLIST/MAGNET " + ("FAILED: " + ", ".join(fails) if fails else "ALL PASSED"))
sys.exit(1 if fails else 0)
