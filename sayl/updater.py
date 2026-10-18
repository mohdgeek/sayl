"""تحديث yt-dlp من داخل البرنامج (يتغير موقع التحميل كثيرًا فتتعطل النسخ القديمة)."""
import hashlib
import importlib.machinery
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

from .config import APP_DIR

WHEEL = os.path.join(APP_DIR, "ytdlp", "yt_dlp.whl")


class _WheelFinder:
    """يُحمّل yt_dlp من النسخة المحدَّثة (wheel) بدل النسخة المضمَّنة في الـ exe."""

    def find_spec(self, name, path=None, target=None):
        if name == "yt_dlp" or name.startswith("yt_dlp."):
            return importlib.machinery.PathFinder.find_spec(name, [WHEEL] if path is None else path)
        return None


def use_wheel():
    """يفعّل النسخة المحدَّثة إن وُجدت. يجب استدعاؤه قبل أول import لـ yt_dlp."""
    if os.path.isfile(WHEEL) and not any(isinstance(f, _WheelFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _WheelFinder())


use_wheel()

try:
    import yt_dlp
    from yt_dlp.version import __version__ as _V
except Exception:  # noqa: BLE001
    yt_dlp, _V = None, None


def current_version():
    return _V


def latest_version(timeout=10):
    req = urllib.request.Request("https://pypi.org/pypi/yt-dlp/json",
                                 headers={"User-Agent": "Sayl"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)["info"]["version"]


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def update(timeout=300):
    """يحدّث yt-dlp عبر pip. في النسخة المُجمَّعة (exe) لا يمكن ذلك: أعد بناء البرنامج."""
    if is_frozen():
        return _update_wheel(timeout)
    try:
        p = subprocess.run([sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    if p.returncode != 0:
        return False, (p.stderr or p.stdout)[-300:]
    return True, "تم التحديث، أعد تشغيل البرنامج لتفعيله"


def _update_wheel(timeout=300):
    """النسخة المُجمَّعة: نزّل wheel الأحدث من PyPI إلى مجلد البرنامج (يُفعَّل عند إعادة التشغيل)."""
    try:
        req = urllib.request.Request("https://pypi.org/pypi/yt-dlp/json", headers={"User-Agent": "Sayl"})
        with urllib.request.urlopen(req, timeout=20) as r:
            files = json.load(r)["urls"]
        w = next(f for f in files if f["filename"].endswith("-py3-none-any.whl"))
        os.makedirs(os.path.dirname(WHEEL), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(WHEEL), suffix=".part")
        h = hashlib.sha256()
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(
                urllib.request.Request(w["url"], headers={"User-Agent": "Sayl"}), timeout=timeout) as r:
            for chunk in iter(lambda: r.read(65536), b""):
                h.update(chunk)
                out.write(chunk)
        if h.hexdigest() != w["digests"]["sha256"]:
            os.remove(tmp)
            return False, "فشل التحقق من الملف المنزَّل"
        os.replace(tmp, WHEEL)
    except Exception as e:  # noqa: BLE001
        return False, f"تعذّر التحديث: {e}"
    return True, "تم التحديث، أعد تشغيل البرنامج لتفعيله"


def auto_update_bg(config):
    """عند تشغيل البرنامج: حدّث yt-dlp في الخلفية مرة كل يوم (يوتيوب يغيّر موقعه باستمرار)."""
    import threading
    import time

    def work():
        try:
            ensure_js_runtime()
        except Exception:  # noqa: BLE001
            pass
        try:
            if time.time() - config.data.get("ytdlp_checked", 0) < 86400:
                return
            config.data["ytdlp_checked"] = time.time()
            config.save()
            if latest_version() != current_version():
                update()
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=work, daemon=True).start()


def ensure_js_runtime():
    """يوتيوب يحتاج JavaScript لفك التوقيع. إن لم يوجد deno/node نُنزّل deno الصغير مرة واحدة (ويندوز)."""
    import shutil
    import zipfile
    if os.name != "nt":
        return
    d = os.path.join(APP_DIR, "bin")
    exe = os.path.join(d, "deno.exe")
    if os.path.isfile(exe) or any(shutil.which(n) for n in ("deno", "node", "bun")):
        return
    try:
        os.makedirs(d, exist_ok=True)
        url = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"
        zp = os.path.join(d, "deno.zip.part")
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Sayl"}), timeout=60) as r, \
                open(zp, "wb") as out:
            shutil.copyfileobj(r, out)
        with zipfile.ZipFile(zp) as z:
            z.extract("deno.exe", d)
        os.remove(zp)
    except Exception:  # noqa: BLE001
        pass
