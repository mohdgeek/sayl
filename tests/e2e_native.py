"""اختبار Native Messaging في Chromium حقيقي: الإضافة تتصل بسيل بلا رمز ولا منفذ.
python tests/e2e_native.py
"""
import functools
import os
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from playwright.sync_api import sync_playwright  # noqa: E402

from sayl import server as sv, setup_tools  # noqa: E402
from sayl.config import Config  # noqa: E402
from sayl.manager import DownloadManager  # noqa: E402
from sayl.server import ApiServer  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXT = os.path.join(ROOT, "extension")
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
tmp = tempfile.mkdtemp()
site = os.path.join(tmp, "site")
os.makedirs(site)
open(os.path.join(site, "file.bin"), "wb").write(os.urandom(300_000))
SimpleHTTPRequestHandler.log_message = lambda *a: None
fsrv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(SimpleHTTPRequestHandler, directory=site))
threading.Thread(target=fsrv.serve_forever, daemon=True).start()

cfg_path = os.path.join(tmp, "config.json")
cfg = Config(cfg_path)
cfg["download_dir"] = os.path.join(tmp, "dl")
cfg["port"] = 0
mgr = DownloadManager(cfg, state_path=os.path.join(tmp, "s.json"), autostart=False)
api = ApiServer(mgr, cfg)
api.start()
cfg["port"] = api.port
cfg.save()
os.environ["SAYL_CONFIG"] = cfg_path          # الجسر يقرأ هذا الملف (يرثه كروم ثم الجسر)

profile = os.path.join(tmp, "profile")
setup_tools.register_native_host(extra_dirs=[os.path.join(profile, "NativeMessagingHosts")], registry=False)
fails = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        profile, executable_path=CHROME, headless=False,
        args=["--headless=new", "--no-sandbox", f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"])
    sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
    ext_id = sw.url.split("/")[2]
    check(ext_id == setup_tools.extension_id(), f"معرّف الإضافة ثابت ({ext_id})")
    st = sw.evaluate("() => chrome.storage.local.get(['token'])")
    check(not st.get("token"), "لا رمز ولا منفذ مخزّنان في الإضافة")

    r = sw.evaluate("async () => await api('/ping')")
    check(r.get("ok") is True and r.get("app") == "Sayl", f"ping عبر Native Messaging ({r})")
    check(sv.STATS["via"] == "native" and sv.STATS["browser"] == "Chrome", f"البرنامج يعرف أنها عبر الجسر ({sv.STATS})")

    url = f"http://127.0.0.1:{fsrv.server_address[1]}/file.bin"
    r = sw.evaluate("async (u) => await sendToSayl(u, {kind: 'http'})", url)
    check(r.get("ok") and r.get("id"), "إرسال تحميل عبر الجسر")
    t0 = time.time()
    while time.time() - t0 < 20 and not any(d.state == "completed" for d in mgr.snapshot()):
        mgr._tick()
        time.sleep(0.3)
    check(any(d.state == "completed" for d in mgr.snapshot()), "اكتمل التحميل")

    # معرّف غريب لا يُقبل: نتأكد أن الجسر لا يستجيب إلا لمعرّفنا عبر allowed_origins
    import json
    mf = json.load(open(os.path.join(profile, "NativeMessagingHosts", "com.sayl.host.json")))
    check(mf["allowed_origins"] == [f"chrome-extension://{ext_id}/"], "allowed_origins تقتصر على إضافة سيل")
    ctx.close()

api.stop()
mgr.shutdown()
print("E2E NATIVE ALL PASSED" if not fails else f"FAILS: {fails}")
sys.exit(1 if fails else 0)
