"""تحديث البرنامج نفسه من GitHub Releases.

يفحص آخر إصدار منشور في المستودع. في نسخة exe على ويندوز ينزّل Sayl.exe الجديد (مع التحقق من
SHA-256 إن وفّره GitHub) ثم يستبدل الملف بعد إغلاق البرنامج ويعيد تشغيله.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

from . import __version__

API = "https://api.github.com/repos/{repo}/releases/latest"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ALLOWED_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
MAX_SIZE = 400 * 1024 * 1024


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def _ver(v):
    nums = re.findall(r"\d+", str(v))[:3]
    return tuple(int(x) for x in nums) + (0,) * (3 - len(nums))


def is_newer(latest, current=__version__):
    return _ver(latest) > _ver(current)


def clean_repo(text):
    """يقبل username/repo أو رابط GitHub كاملًا."""
    t = (text or "").strip().rstrip("/")
    t = re.sub(r"^https?://github\.com/", "", t)
    t = re.sub(r"\.git$", "", t)
    return t if REPO_RE.match(t) else ""


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Sayl-updater",
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def check(repo, timeout=15):
    """يعيد dict: {latest, newer, page, notes, asset_url, asset_size, sha256} أو يرفع RuntimeError."""
    repo = clean_repo(repo)
    if not repo:
        raise RuntimeError("اسم المستودع غير صالح. الصيغة: username/repo")
    try:
        with _get(API.format(repo=repo), timeout) as r:
            data = json.load(r)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"تعذّر الوصول إلى GitHub: {e}") from e
    tag = data.get("tag_name") or ""
    asset = next((a for a in data.get("assets", [])
                  if str(a.get("name", "")).lower() == "sayl.exe"), None)
    digest = (asset or {}).get("digest") or ""
    return {"latest": tag.lstrip("vV"), "newer": is_newer(tag), "page": data.get("html_url", ""),
            "notes": (data.get("body") or "")[:600],
            "asset_url": (asset or {}).get("browser_download_url", ""),
            "asset_size": (asset or {}).get("size", 0),
            "sha256": digest.split(":", 1)[1] if digest.startswith("sha256:") else ""}


def _safe_url(url):
    from urllib.parse import urlparse
    p = urlparse(url)
    return p.scheme == "https" and (p.hostname or "") in ALLOWED_HOSTS


def can_apply():
    return is_frozen() and sys.platform.startswith("win")


def download(info, progress=None, timeout=30):
    """ينزّل الإصدار الجديد إلى مجلد البرنامج ويعيد مساره. يتحقق من الحجم والبصمة."""
    url = info.get("asset_url", "")
    if not _safe_url(url):
        raise RuntimeError("رابط التنزيل غير موثوق")
    exe = sys.executable
    dest = os.path.join(os.path.dirname(exe), "Sayl.new.exe")
    h = hashlib.sha256()
    got = 0
    with _get(url, timeout) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or info.get("asset_size") or 0)
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            got += len(chunk)
            if got > MAX_SIZE:
                raise RuntimeError("الملف أكبر من المتوقع")
            h.update(chunk)
            f.write(chunk)
            if progress and total:
                progress(got / total)
    ok_size = not info.get("asset_size") or got == info["asset_size"]
    ok_hash = not info.get("sha256") or h.hexdigest() == info["sha256"]
    if not (ok_size and ok_hash and got > 1_000_000):
        os.remove(dest)
        raise RuntimeError("فشل التحقق من الملف المنزَّل")
    return dest


def clean_env(env=None):
    """بيئة نظيفة لتشغيل نسخة جديدة من exe.

    PyInstaller (وضع onefile) يضع متغيرات بيئة تجعل أي exe نشغّله من داخله يظن أنه تابع للنسخة الحالية
    ويعيد استعمال مجلدها المؤقت _MEI*، وهذا المجلد يُحذف عند خروجنا، فيظهر الخطأ
    «Failed to load Python DLL». لذلك نحذف هذه المتغيرات ونطلب إعادة ضبط البيئة.
    """
    env = dict(os.environ if env is None else env)
    for k in list(env):
        if k.startswith("_PYI") or k.upper() in ("_MEIPASS2", "PYINSTALLER_RESET_ENVIRONMENT"):
            env.pop(k)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def apply_and_restart(new_path):
    """يكتب سكربت يستبدل exe بعد إغلاقنا ثم يشغّله. على المستدعي إغلاق البرنامج فورًا بعدها."""
    exe = sys.executable
    bat = os.path.join(tempfile.gettempdir(), "sayl_update.bat")
    with open(bat, "w", encoding="ascii", errors="ignore") as f:
        f.write(
            "@echo off\r\n"
            "set tries=0\r\n"
            ":retry\r\n"
            "set /a tries+=1\r\n"
            "if %tries% gtr 40 exit /b 1\r\n"
            "timeout /t 1 /nobreak >nul\r\n"
            f'move /y "{new_path}" "{exe}" >nul 2>&1\r\n'
            "if errorlevel 1 goto retry\r\n"
            "set _MEIPASS2=\r\n"
            "set PYINSTALLER_RESET_ENVIRONMENT=1\r\n"
            "timeout /t 2 /nobreak >nul\r\n"
            f'start "" "{exe}"\r\n'
            'del "%~f0"\r\n')
    flags = 0x00000008 | 0x08000000       # DETACHED_PROCESS | CREATE_NO_WINDOW
    subprocess.Popen(["cmd", "/c", bat], creationflags=flags, close_fds=True, env=clean_env())
