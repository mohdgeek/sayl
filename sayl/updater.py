"""تحديث yt-dlp من داخل البرنامج (يتغير موقع التحميل كثيرًا فتتعطل النسخ القديمة)."""
import json
import subprocess
import sys
import urllib.request

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
        return False, "النسخة المُجمَّعة (exe) لا تُحدَّث من الداخل؛ أعد بناءها بعد تحديث yt-dlp"
    try:
        p = subprocess.run([sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    if p.returncode != 0:
        return False, (p.stderr or p.stdout)[-300:]
    return True, "تم التحديث، أعد تشغيل البرنامج لتفعيله"
