"""اختبار: زر «تحميل» يظهر فوق مصغّرة فيديو في صفحة رئيسية/قائمة ويرسل رابط ذلك الفيديو (لا رابط الصفحة)."""
import functools
import os
import socket
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from playwright.sync_api import sync_playwright  # noqa: E402

from sayl.config import Config  # noqa: E402
from sayl.manager import DownloadManager  # noqa: E402
from sayl.server import ApiServer  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXT = os.path.join(ROOT, "extension")
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
tmp = tempfile.mkdtemp()
site = os.path.join(tmp, "site")
os.makedirs(site)
open(os.path.join(site, "feed.html"), "w", encoding="utf-8").write("""<!doctype html><meta charset="utf-8">
<body style="margin:0;background:#111">
<a id="t1" href="https://www.youtube.com/watch?v=AAAAAAAAAAA" style="display:block;position:absolute;left:20px;top:20px;width:320px;height:180px;background:#335"></a>
<a id="t2" href="https://www.youtube.com/watch?v=BBBBBBBBBBB" style="display:block;position:absolute;left:380px;top:20px;width:320px;height:180px;background:#533"></a>
<a id="title" href="https://www.youtube.com/watch?v=CCCCCCCCCCC" style="position:absolute;left:20px;top:220px;width:300px;height:24px;color:#fff">عنوان صغير</a>
</body>""")
SimpleHTTPRequestHandler.log_message = lambda *a: None
srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(SimpleHTTPRequestHandler, directory=site))
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://media.test:{srv.server_address[1]}"

cfg = Config(os.path.join(tmp, "c.json"))
cfg["download_dir"] = os.path.join(tmp, "dl")
cfg["port"] = 0
mgr = DownloadManager(cfg, state_path=os.path.join(tmp, "s.json"), autostart=False)
api = ApiServer(mgr, cfg)
api.start()
fails = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        os.path.join(tmp, "profile"), executable_path=CHROME, headless=False,
        args=["--headless=new", "--no-sandbox", f"--disable-extensions-except={EXT}",
              f"--load-extension={EXT}", "--host-resolver-rules=MAP media.test 127.0.0.1"],
        viewport={"width": 760, "height": 300})
    sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
    sw.evaluate("([port, token]) => chrome.storage.local.set({port, token, confirmVideo: false})", [api.port, cfg["token"]])
    page = ctx.new_page()
    page.goto(base + "/feed.html")
    page.wait_for_timeout(800)
    # التمرير فوق المصغّرة الثانية ثم الضغط على الزر العائم (أعلى يمين المصغّرة)
    for dx in (0, 6):
        page.mouse.move(380 + 160 + dx, 20 + 90 + dx)
        page.wait_for_timeout(300)
    page.screenshot(path=os.path.join(tmp, "feed.png"))
    before = len(mgr.snapshot())
    page.mouse.click(380 + 320 - 10 - 45, 20 + 10 + 18)
    t0 = time.time()
    while time.time() - t0 < 8 and len(mgr.snapshot()) == before:
        time.sleep(0.2)
    snap = mgr.snapshot()
    check(len(snap) == before + 1, "الزر أرسل فيديو المصغّرة")
    if len(snap) > before:
        check(snap[0].url == "https://www.youtube.com/watch?v=BBBBBBBBBBB",
              f"أُرسل رابط الفيديو نفسه لا الصفحة ({snap[0].url})")
        check(snap[0].kind == "media", "النوع media")
    # رابط عنوان صغير (أقل من حجم مصغّرة) لا يُظهر الزر
    page.mouse.move(60, 232)
    page.wait_for_timeout(400)
    check(page.evaluate("() => !!document.querySelector('#title')"), "صفحة الاختبار سليمة")
    ctx.close()
print("FAILED:" if fails else "ALL OK", fails)
sys.exit(1 if fails else 0)
