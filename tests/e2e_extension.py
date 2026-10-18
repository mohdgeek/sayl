"""اختبار شامل لإضافة Chrome في Chromium حقيقي (بدون شاشة):
python tests/e2e_extension.py OUTDIR
يحمّل الإضافة، يفتح صفحة فيها فيديو وبث HLS، ويتحقق أن الإضافة ترصدهما وترسلهما إلى سيل.
"""
import functools
import os
import subprocess
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
out = sys.argv[1] if len(sys.argv) > 1 else "."
os.makedirs(out, exist_ok=True)

tmp = tempfile.mkdtemp()
site = os.path.join(tmp, "site")
os.makedirs(site)


def ff(*args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


ff("-f", "lavfi", "-i", "testsrc2=duration=6:size=640x360:rate=24", "-vf", "noise=alls=40:allf=t",
   "-c:v", "libvpx", "-b:v", "2M", os.path.join(site, "sample.webm"))
print("sample.webm bytes:", os.path.getsize(os.path.join(site, "sample.webm")))
ff("-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=15", "-c:v", "libx264",
   "-pix_fmt", "yuv420p", "-hls_time", "2", "-hls_playlist_type", "vod",
   "-hls_segment_filename", os.path.join(site, "seg%d.ts"), os.path.join(site, "stream.m3u8"))
open(os.path.join(site, "page.html"), "w", encoding="utf-8").write("""<!doctype html>
<meta charset="utf-8"><title>صفحة اختبار</title>
<body style="margin:0;background:#222">
<video id="v" src="sample.webm" width="640" height="360" muted loop autoplay></video>
<script>fetch('stream.m3u8').then(r => r.text());</script>
</body>""")

SimpleHTTPRequestHandler.log_message = lambda *a: None
srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(SimpleHTTPRequestHandler, directory=site))
threading.Thread(target=srv.serve_forever, daemon=True).start()
# localhost و127.0.0.1 مستثنيان عمدًا من الرصد، فنستخدم اسمًا وهميًا يحلّه كروم ومحمّلنا إلى الجهاز
import socket  # noqa: E402
_real_gai = socket.getaddrinfo
socket.getaddrinfo = lambda host, *a, **k: _real_gai("127.0.0.1" if host == "media.test" else host, *a, **k)
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
        viewport={"width": 900, "height": 520})
    sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
    ext_id = sw.url.split("/")[2]
    sw.evaluate("([port, token]) => chrome.storage.local.set({port, token})", [api.port, cfg["token"]])

    page = ctx.new_page()
    page.goto(base + "/page.html")
    page.wait_for_timeout(3500)

    tabs = sw.evaluate("async (u) => (await chrome.tabs.query({})).filter(t => t.url.includes('page.html')).map(t => t.id)", base)
    tab_id = tabs[0]
    det = sw.evaluate("async (k) => (await chrome.storage.session.get(k))[k] || []", f"m_{tab_id}")
    kinds = {(d["kind"], d["type"]) for d in det}
    print("detected:", [(d["type"], d["size"]) for d in det])
    check(("manifest", "HLS") in kinds, "الإضافة رصدت بث HLS (m3u8)")
    check(any(d["kind"] == "file" and d["type"] == "WEBM" for d in det), "الإضافة رصدت فيديو WEBM")
    check(not any(d["url"].endswith(".ts") for d in det), "لم تُرصد مقاطع .ts")
    badge = sw.evaluate("async (t) => await chrome.action.getBadgeText({tabId: t})", tab_id)
    check(badge == str(len(det)) and len(det) >= 2, f"شارة الأيقونة تعرض العدد ({badge})")

    # زر التحميل العائم فوق الفيديو
    box = page.locator("#v").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_timeout(400)
    page.mouse.move(box["x"] + box["width"] / 2 + 5, box["y"] + box["height"] / 2 + 5)
    page.wait_for_timeout(500)
    page.screenshot(path=os.path.join(out, "page_button.png"))
    before = len(mgr.snapshot())
    page.mouse.click(box["x"] + box["width"] - 10 - 45, box["y"] + 10 + 18)
    t0 = time.time()
    while time.time() - t0 < 8 and len(mgr.snapshot()) == before:
        time.sleep(0.2)
    snap = mgr.snapshot()
    check(len(snap) == before + 1, "الزر العائم أرسل الفيديو إلى سيل")
    if len(snap) > before:
        d = snap[0]
        check(d.url.endswith("sample.webm") and d.kind == "http", f"رابط مباشر وبنوع http ({d.kind})")
        check(d.headers.get("Referer", "").endswith("page.html"), "نُقل الـ Referer")
        t0 = time.time()
        while time.time() - t0 < 30 and d.state not in ("completed", "error"):
            if d.state == "queued" and not d.active:      # المجدول معطّل في الاختبار
                d.start()
            time.sleep(0.1)
        check(d.state == "completed" and os.path.getsize(os.path.join(d.dest_dir, d.filename)) > 100000,
              f"اكتمل تحميل الفيديو ({d.state} {d.error})")

    # إرسال البث عبر الرسالة (كما تفعل النافذة المنبثقة)
    m3u8 = next(d["url"] for d in det if d["kind"] == "manifest")
    r = sw.evaluate("async (u) => await sendToSayl(u, {referrer: '', kind: 'media'})", m3u8)
    check(r.get("ok"), "إرسال m3u8 بنوع media قُبل")
    check(any(x.kind == "media" for x in mgr.snapshot()), "أُنشئ تحميل فيديو (media)")

    # رفض بلا رمز صحيح
    sw.evaluate("() => chrome.storage.local.set({token: 'wrong'})")
    try:
        sw.evaluate("async (u) => await sendToSayl(u, {kind: 'auto'})", base + "/sample.webm")
        check(False, "كان يجب أن يُرفض الرمز الخاطئ")
    except Exception as e:  # noqa: BLE001
        check("401" in str(e) or "الرمز" in str(e), "رُفض الرمز الخاطئ")
    sw.evaluate("(t) => chrome.storage.local.set({token: t})", cfg["token"])

    # لقطة النافذة المنبثقة مع بيانات
    pop = ctx.new_page()
    pop.set_viewport_size({"width": 380, "height": 520})
    pop.goto(f"chrome-extension://{ext_id}/popup.html")
    pop.wait_for_timeout(500)
    ptab = sw.evaluate("async (u) => (await chrome.tabs.query({})).find(t => t.url.includes(u)).id", "popup.html")
    sw.evaluate("async ([k, v]) => chrome.storage.session.set({[k]: v})", [f"m_{ptab}", det])
    pop.reload()
    pop.wait_for_timeout(800)
    pop.screenshot(path=os.path.join(out, "popup.png"))
    items = pop.locator("li").count()
    check(items == len(det), f"النافذة المنبثقة تعرض {items} عنصرًا")
    check("متصل" in pop.locator("#st").inner_text(), "حالة الاتصال: متصل")
    ctx.close()

api.stop()
mgr.shutdown()
print("E2E", "FAILED: " + "; ".join(fails) if fails else "ALL PASSED")
sys.exit(1 if fails else 0)
