"""أدوات تسهّل على المستخدم العادي: تصدير الإضافة جاهزة، التشغيل مع ويندوز، وسجل الأخطاء."""
import json
import os
import shutil
import sys

from .config import APP_DIR

EXT_NAME = "Sayl-Extension"


def resource_path(rel):
    base = getattr(sys, "_MEIPASS", None) or os.path.join(os.path.dirname(__file__), "..")
    return os.path.normpath(os.path.join(base, rel))


def default_extension_dir():
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    return os.path.join(docs if os.path.isdir(docs) else APP_DIR, EXT_NAME)


def _app_version():
    from . import __version__
    return __version__


def refresh_extension(config):
    """إن كانت الإضافة مصدَّرة سابقًا بنسخة أقدم، نحدّث ملفاتها بصمت (الإضافة تُعيد تحميل نفسها)."""
    dest = default_extension_dir()
    try:
        with open(os.path.join(dest, "pair.json"), encoding="utf-8") as f:
            old = json.load(f)
        if old.get("version") != _app_version() or old.get("token") != config["token"] \
                or old.get("port") != int(config["port"]):
            export_extension(config, dest)
    except (OSError, ValueError):
        pass


def export_extension(config, dest=None):
    """ينسخ الإضافة إلى مجلد ثابت ويكتب فيها الرمز والمنفذ، فلا يلصق المستخدم شيئًا يدويًا."""
    src = resource_path("extension")
    if not os.path.isfile(os.path.join(src, "manifest.json")):
        raise FileNotFoundError("ملفات الإضافة غير موجودة داخل البرنامج")
    dest = dest or default_extension_dir()
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    pair = os.path.join(dest, "pair.json")
    with open(pair, "w", encoding="utf-8") as f:
        json.dump({"port": int(config["port"]), "token": config["token"],
                   "version": _app_version()}, f)
    try:
        os.chmod(pair, 0o600)
    except OSError:
        pass
    return dest


# ------------------------------------------------------------ Firefox
FIREFOX_ID = "sayl@mohdgeek.github.io"
_FF_SHIM = "if (typeof browser !== 'undefined') { globalThis.chrome = browser; }\n"


def firefox_manifest(manifest):
    """يحوّل manifest الإضافة (Chrome) إلى صيغة Firefox: صفحة خلفية بدل service worker + معرّف ثابت."""
    m = json.loads(json.dumps(manifest))
    m.pop("key", None)
    m["background"] = {"scripts": ["shim.js", "lib.js", "background.js"]}
    for cs in m.get("content_scripts", []):
        cs["js"] = ["shim.js"] + [j for j in cs.get("js", []) if j != "shim.js"]
    m["browser_specific_settings"] = {"gecko": {"id": FIREFOX_ID, "strict_min_version": "115.0"}}
    return m


def export_firefox(config, dest=None):
    """يجهّز مجلد وحزمة .xpi لـ Firefox. يعيد (المجلد، مسار xpi)."""
    import zipfile
    src = resource_path("extension")
    if not os.path.isfile(os.path.join(src, "manifest.json")):
        raise FileNotFoundError("ملفات الإضافة غير موجودة داخل البرنامج")
    dest = dest or (default_extension_dir() + "-Firefox")
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    with open(os.path.join(src, "manifest.json"), encoding="utf-8") as f:
        mf = firefox_manifest(json.load(f))
    with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(mf, f, ensure_ascii=False, indent=2)
    with open(os.path.join(dest, "shim.js"), "w", encoding="utf-8") as f:
        f.write(_FF_SHIM)
    with open(os.path.join(dest, "pair.json"), "w", encoding="utf-8") as f:
        json.dump({"port": int(config["port"]), "token": config["token"], "version": _app_version()}, f)
    xpi = dest + ".xpi"
    with zipfile.ZipFile(xpi, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(dest):
            for n in files:
                full = os.path.join(root, n)
                z.write(full, os.path.relpath(full, dest).replace(os.sep, "/"))
    return dest, xpi


def find_firefox():
    cands = [os.path.join(os.environ.get(v, ""), "Mozilla Firefox", "firefox.exe")
             for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
    cands.append("/Applications/Firefox.app/Contents/MacOS/firefox")
    return next((p for p in cands if os.path.isfile(p)), None) or shutil.which("firefox")


def open_firefox_debug():
    import subprocess
    exe = find_firefox()
    if not exe:
        raise FileNotFoundError("Firefox")
    subprocess.Popen([exe, "about:debugging#/runtime/this-firefox"])


# ------------------------------------------------------------ التشغيل مع ويندوز
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
_LA_PATH = os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents", "com.sayl.app.plist")
_XDG_AUTOSTART = os.path.join(os.path.expanduser("~"), ".config", "autostart", "sayl.desktop")
_XDG_APPS = os.path.join(os.path.expanduser("~"), ".local", "share", "applications")


def autostart_supported():
    """تشغيل تلقائي مع النظام: ويندوز (سجل)، ماك (LaunchAgent)، لينكس (autostart/.desktop) للنسخة المُغلَّفة."""
    return bool(getattr(sys, "frozen", False)) and (IS_WIN or IS_MAC or sys.platform.startswith("linux"))


def _desktop_entry(exec_line, mime=""):
    return ("[Desktop Entry]\nType=Application\nName=Sayl\nComment=Sayl download manager\n"
            f"Exec={exec_line}\nTerminal=false\nCategories=Network;FileTransfer;\n"
            + (f"MimeType={mime}\n" if mime else ""))


def autostart_enabled():
    if not autostart_supported():
        return False
    if IS_MAC:
        return os.path.isfile(_LA_PATH)
    if not IS_WIN:
        return os.path.isfile(_XDG_AUTOSTART)
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.QueryValueEx(k, "Sayl")
            return True
    except OSError:
        return False


def set_autostart(on):
    if not autostart_supported():
        return False
    if IS_MAC:
        if on:
            os.makedirs(os.path.dirname(_LA_PATH), exist_ok=True)
            with open(_LA_PATH, "w", encoding="utf-8") as f:
                f.write('<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>'
                        '<key>Label</key><string>com.sayl.app</string>'
                        f'<key>ProgramArguments</key><array><string>{sys.executable}</string>'
                        '<string>--minimized</string></array><key>RunAtLoad</key><true/></dict></plist>\n')
        elif os.path.isfile(_LA_PATH):
            os.remove(_LA_PATH)
        return True
    if not IS_WIN:
        if on:
            os.makedirs(os.path.dirname(_XDG_AUTOSTART), exist_ok=True)
            with open(_XDG_AUTOSTART, "w", encoding="utf-8") as f:
                f.write(_desktop_entry(f'"{sys.executable}" --minimized'))
        elif os.path.isfile(_XDG_AUTOSTART):
            os.remove(_XDG_AUTOSTART)
        return True
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "Sayl", 0, winreg.REG_SZ, f'"{sys.executable}" --minimized')
        else:
            try:
                winreg.DeleteValue(k, "Sayl")
            except OSError:
                pass
    return True


# ------------------------------------------------------------ ربط magnet و .torrent
_CLS = r"Software\Classes"


def _torrent_cmd():
    return f'"{sys.executable}" "%1"'


def torrent_assoc_supported():
    """ربط magnet/.torrent: ويندوز (سجل) ولينكس (xdg-mime). غير متاح على ماك (يحتاج توقيع التطبيق)."""
    return bool(getattr(sys, "frozen", False)) and (IS_WIN or sys.platform.startswith("linux"))


def torrent_assoc_enabled():
    if not torrent_assoc_supported():
        return False
    if not IS_WIN:
        return os.path.isfile(os.path.join(_XDG_APPS, "sayl.desktop"))
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _CLS + r"\magnet\shell\open\command") as k:
            return sys.executable.lower() in str(winreg.QueryValueEx(k, "")[0]).lower()
    except OSError:
        return False


def set_torrent_assoc(on):
    """يجعل سيل يفتح روابط magnet وملفات .torrent (على مستوى المستخدم الحالي دون صلاحيات إدارة)."""
    if not torrent_assoc_supported():
        return False
    if not IS_WIN:
        import subprocess
        path = os.path.join(_XDG_APPS, "sayl.desktop")
        if on:
            os.makedirs(_XDG_APPS, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(_desktop_entry(f'"{sys.executable}" %u',
                                       "x-scheme-handler/magnet;application/x-bittorrent;"))
            for mt in ("x-scheme-handler/magnet", "application/x-bittorrent"):
                try:
                    subprocess.run(["xdg-mime", "default", "sayl.desktop", mt], timeout=10, check=False)
                except (OSError, subprocess.SubprocessError):
                    pass
        elif os.path.isfile(path):
            os.remove(path)
        return True
    import winreg
    H = winreg.HKEY_CURRENT_USER
    if not on:
        for sub in (r"\magnet\shell\open\command", r"\magnet\shell\open", r"\magnet\shell", r"\magnet",
                    r"\Sayl.Torrent\shell\open\command", r"\Sayl.Torrent\shell\open",
                    r"\Sayl.Torrent\shell", r"\Sayl.Torrent"):
            try:
                winreg.DeleteKey(H, _CLS + sub)
            except OSError:
                pass
        return True
    cmd = _torrent_cmd()
    with winreg.CreateKey(H, _CLS + r"\magnet") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:Magnet Link")
        winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(H, _CLS + r"\magnet\shell\open\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, cmd)
    with winreg.CreateKey(H, _CLS + r"\Sayl.Torrent") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "ملف تورنت")
    with winreg.CreateKey(H, _CLS + r"\Sayl.Torrent\shell\open\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, cmd)
    with winreg.CreateKey(H, _CLS + r"\.torrent") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "Sayl.Torrent")
    return True


def forward_to_running(config, url):
    """يمرّر رابط magnet/ملف تورنت إلى نسخة سيل العاملة. يعيد True عند النجاح."""
    import json
    import urllib.request
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{int(config['port'])}/add",
            data=json.dumps({"url": url, "kind": "torrent", "confirm": True}).encode(),
            headers={"Content-Type": "application/json", "X-Sayl-Token": config["token"]})
        with urllib.request.urlopen(req, timeout=4) as r:
            return bool(json.loads(r.read()).get("ok"))
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------ السجل
LOG_PATH = os.path.join(APP_DIR, "sayl.log")


def install_excepthook():
    import datetime
    import traceback

    def hook(et, ev, tb):
        try:
            os.makedirs(APP_DIR, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}]\n")
                f.write("".join(traceback.format_exception(et, ev, tb)))
        except OSError:
            pass
    sys.excepthook = hook


# ------------------------------------------------------------ المتصفحات
def _win_paths(*rels):
    out = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        base = os.environ.get(var)
        if base:
            out += [os.path.join(base, r) for r in rels]
    return out


MAC_APPS = {
    "chrome": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "edge": "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "brave": "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "opera": "/Applications/Opera.app/Contents/MacOS/Opera",
    "vivaldi": "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi",
}

BROWSERS = [
    ("chrome", "Google Chrome", "chrome", ("Google/Chrome/Application/chrome.exe",), "google-chrome"),
    ("edge", "Microsoft Edge", "edge", ("Microsoft/Edge/Application/msedge.exe",), "microsoft-edge"),
    ("brave", "Brave", "brave", ("BraveSoftware/Brave-Browser/Application/brave.exe",), "brave-browser"),
    ("opera", "Opera", "opera", ("Programs/Opera/opera.exe", "Opera/opera.exe"), "opera"),
    ("vivaldi", "Vivaldi", "vivaldi", ("Vivaldi/Application/vivaldi.exe",), "vivaldi"),
]


def find_browsers():
    """يعيد قائمة dict: key, name, scheme, exe (أو None إن لم يُعثر عليه)."""
    out = []
    for key, name, scheme, rels, cmd in BROWSERS:
        exe = next((p for p in _win_paths(*rels) if os.path.isfile(p)), None)
        exe = exe or (MAC_APPS.get(key) if IS_MAC and os.path.isfile(MAC_APPS.get(key, "")) else None)
        exe = exe or shutil.which(cmd)
        out.append({"key": key, "name": name, "scheme": scheme, "exe": exe})
    return out


def _user_data_dirs(key):
    """مجلدات بيانات المتصفح المحتملة (حسب النظام) لاكتشاف الإضافة المثبّتة."""
    home = os.path.expanduser("~")
    la = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    ra = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
    mac = os.path.join(home, "Library", "Application Support")
    cfg = os.path.join(home, ".config")
    table = {
        "chrome": [os.path.join(la, "Google", "Chrome", "User Data"), os.path.join(mac, "Google", "Chrome"),
                   os.path.join(cfg, "google-chrome")],
        "edge": [os.path.join(la, "Microsoft", "Edge", "User Data"), os.path.join(mac, "Microsoft Edge"),
                 os.path.join(cfg, "microsoft-edge")],
        "brave": [os.path.join(la, "BraveSoftware", "Brave-Browser", "User Data"),
                  os.path.join(mac, "BraveSoftware", "Brave-Browser"), os.path.join(cfg, "BraveSoftware", "Brave-Browser")],
        "vivaldi": [os.path.join(la, "Vivaldi", "User Data"), os.path.join(mac, "Vivaldi"), os.path.join(cfg, "vivaldi")],
        "opera": [os.path.join(ra, "Opera Software", "Opera Stable"), os.path.join(mac, "com.operasoftware.Opera"),
                  os.path.join(cfg, "opera")],
    }
    return table.get(key, [])


def extension_installed(key, ext_id=None):
    """هل إضافة سيل مثبّتة في هذا المتصفح؟ يعيد "on" (مفعّلة) أو "off" (معطّلة) أو None.
    يقرأ ملفات Preferences في ملفات المتصفح الشخصية؛ لا يغيّر شيئًا."""
    try:
        ext_id = ext_id or extension_id()
    except Exception:  # noqa: BLE001
        return None
    found = None
    for base in _user_data_dirs(key):
        if not os.path.isdir(base):
            continue
        profiles = [base] + [os.path.join(base, d) for d in sorted(os.listdir(base))
                             if d == "Default" or d.startswith("Profile ")]
        for prof in profiles:
            for fn in ("Secure Preferences", "Preferences"):
                fp = os.path.join(prof, fn)
                if not os.path.isfile(fp):
                    continue
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    ent = data.get("extensions", {}).get("settings", {}).get(ext_id)
                except Exception:  # noqa: BLE001
                    continue
                if not ent:
                    continue
                if ent.get("state", 1) == 1 and not ent.get("disable_reasons"):
                    return "on"
                found = "off"
    return found


def open_extensions_page(browser):
    import subprocess
    if not browser.get("exe"):
        raise FileNotFoundError(browser["name"])
    subprocess.Popen([browser["exe"], f"{browser['scheme']}://extensions"])


# ------------------------------------------------------------ Native Messaging (بلا رمز ولا منفذ)
HOST_NAME = "com.sayl.host"


def extension_id():
    """معرّف الإضافة الثابت: يُشتق من المفتاح العام المكتوب في manifest.json (كما يفعل Chrome)."""
    import base64
    import hashlib
    with open(os.path.join(resource_path("extension"), "manifest.json"), "r", encoding="utf-8") as f:
        key = json.load(f)["key"]
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def host_command():
    """مسار ملف تنفيذي يشغّل الجسر: SaylHost.exe في النسخة المُغلَّفة، أو سكربت صغير أثناء التطوير."""
    if getattr(sys, "frozen", False):
        ext = ".exe" if IS_WIN else ""
        src = resource_path(os.path.join("host", "SaylHost" + ext))
        # اسم يحمل الإصدار: ملف الجسر القديم قد يكون قيد التشغيل (مقفلًا) فلا يمكن الكتابة فوقه
        dst = os.path.join(APP_DIR, f"SaylHost-{_app_version()}{ext}")
        if os.path.isfile(src):
            os.makedirs(APP_DIR, exist_ok=True)
            if not os.path.isfile(dst) or os.path.getsize(dst) != os.path.getsize(src):
                shutil.copyfile(src, dst)
                if not IS_WIN:
                    os.chmod(dst, 0o755)
            for n in os.listdir(APP_DIR):
                if n.startswith("SaylHost") and n != os.path.basename(dst) and \
                        (n.endswith(".exe") if IS_WIN else not n.endswith(".exe")):
                    try:
                        os.remove(os.path.join(APP_DIR, n))
                    except OSError:
                        pass                      # ما زال قيد التشغيل: يُحذف لاحقًا
        return dst
    run_host = resource_path("run_host.py")
    os.makedirs(APP_DIR, exist_ok=True)
    if sys.platform.startswith("win"):
        path = os.path.join(APP_DIR, "sayl-host.bat")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f'@echo off\r\n"{sys.executable}" "{run_host}"\r\n')
    else:
        path = os.path.join(APP_DIR, "sayl-host.sh")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f'#!/bin/sh\nexec "{sys.executable}" "{run_host}"\n')
        os.chmod(path, 0o755)
    return path


def _nm_dirs_posix():
    home = os.path.expanduser("~")
    if IS_MAC:
        cands = ["Library/Application Support/Google/Chrome", "Library/Application Support/Chromium",
                 "Library/Application Support/Microsoft Edge",
                 "Library/Application Support/BraveSoftware/Brave-Browser",
                 "Library/Application Support/Vivaldi", "Library/Application Support/com.operasoftware.Opera"]
    else:
        cands = [".config/google-chrome", ".config/chromium", ".config/microsoft-edge",
                 ".config/BraveSoftware/Brave-Browser", ".config/vivaldi", ".config/opera"]
    return [os.path.join(home, c, "NativeMessagingHosts") for c in cands
            if os.path.isdir(os.path.join(home, c))]


_WIN_NM_KEYS = (r"Software\Google\Chrome\NativeMessagingHosts",
                r"Software\Microsoft\Edge\NativeMessagingHosts",
                r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts")


def register_native_host(config=None, extra_dirs=None, registry=True):
    """يسجّل الجسر عند المتصفحات ليتصل بالإضافة تلقائيًا. آمن للاستدعاء عند كل تشغيل."""
    os.makedirs(APP_DIR, exist_ok=True)
    manifest = {
        "name": HOST_NAME,
        "description": "Sayl download manager bridge",
        "path": host_command(),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id()}/"],
    }
    main_path = os.path.join(APP_DIR, f"{HOST_NAME}.json")
    with open(main_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    written = [main_path]
    ff_manifest = dict(manifest)                    # Firefox يستعمل allowed_extensions بدل allowed_origins
    ff_manifest.pop("allowed_origins", None)
    ff_manifest["allowed_extensions"] = [FIREFOX_ID]
    ff_path = os.path.join(APP_DIR, f"{HOST_NAME}.firefox.json")
    with open(ff_path, "w", encoding="utf-8") as f:
        json.dump(ff_manifest, f, ensure_ascii=False, indent=2)
    if sys.platform.startswith("win"):
        if registry:
            import winreg
            for base in _WIN_NM_KEYS:
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + "\\" + HOST_NAME) as k:
                    winreg.SetValueEx(k, "", 0, winreg.REG_SZ, main_path)
                written.append("HKCU\\" + base)
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                  r"Software\Mozilla\NativeMessagingHosts\\" + HOST_NAME) as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, ff_path)
            written.append("HKCU\\Software\\Mozilla\\NativeMessagingHosts")
    else:
        for d in (extra_dirs or []) + (_nm_dirs_posix() if registry else []):
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, f"{HOST_NAME}.json")
            shutil.copyfile(main_path, p)
            written.append(p)
        if registry:
            ffd = (os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Mozilla",
                                "NativeMessagingHosts") if IS_MAC else
                   os.path.join(os.path.expanduser("~"), ".mozilla", "native-messaging-hosts"))
            os.makedirs(ffd, exist_ok=True)
            p = os.path.join(ffd, f"{HOST_NAME}.json")
            shutil.copyfile(ff_path, p)
            written.append(p)
    if config is not None and getattr(sys, "frozen", False):
        config["app_exe"] = sys.executable
        config.save()
    return written


# ------------------------------------------------------------ تشخيص
def log(msg):
    import datetime
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except OSError:
        pass


def native_status():
    """حالة تسجيل الجسر: قائمة (نص، سليم؟) تُعرض في تبويب المتصفحات."""
    out = []
    mf = os.path.join(APP_DIR, f"{HOST_NAME}.json")
    host = None
    try:
        with open(mf, "r", encoding="utf-8") as f:
            host = json.load(f)["path"]
        out.append(("ملف تسجيل الجسر موجود", True))
    except (OSError, ValueError, KeyError):
        out.append(("ملف تسجيل الجسر غير موجود", False))
    if host:
        out.append((f"ملف الجسر موجود: {host}" if os.path.isfile(host) else f"ملف الجسر مفقود: {host}",
                    os.path.isfile(host)))
    if sys.platform.startswith("win"):
        import winreg
        for label, base in zip(("Chrome", "Edge", "Brave"), _WIN_NM_KEYS):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + "\\" + HOST_NAME) as k:
                    ok = winreg.QueryValueEx(k, "")[0] == mf
            except OSError:
                ok = False
            out.append((f"مسجّل في {label}" if ok else f"غير مسجّل في {label}", ok))
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Mozilla\NativeMessagingHosts\\" + HOST_NAME) as k:
                ok = bool(winreg.QueryValueEx(k, "")[0])
        except OSError:
            ok = False
        out.append(("مسجّل في Firefox" if ok else "غير مسجّل في Firefox", ok))
    else:
        found = [d for d in _nm_dirs_posix() if os.path.isfile(os.path.join(d, f"{HOST_NAME}.json"))]
        if found:                      # لا نُظهر خطأً إن لم يوجد متصفح مثبّت أصلًا
            out.append((f"مسجّل في {len(found)} متصفح", True))
    return out


def self_test_host(timeout=20):
    """يشغّل ملف الجسر مباشرة (دون متصفح) ويرسل له ping: يكشف مشاكل الملف نفسه."""
    import struct
    import subprocess
    import threading
    try:
        cmd = host_command()
        env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI") and k != "_MEIPASS2"}
        p = subprocess.Popen([cmd], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env)
    except OSError as e:
        return False, f"تعذّر تشغيل ملف الجسر: {e}"
    msg = json.dumps({"id": 1, "path": "/ping", "method": "GET", "ua": "selftest"}).encode()
    result = {}

    def work():
        try:
            p.stdin.write(struct.pack("<I", len(msg)) + msg)
            p.stdin.flush()
            raw = p.stdout.read(4)
            if len(raw) == 4:
                result["r"] = json.loads(p.stdout.read(struct.unpack("<I", raw)[0]))
        except (OSError, ValueError):
            pass
    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(timeout)
    try:
        p.kill()
    except OSError:
        pass
    r = result.get("r")
    if not r:
        err = ""
        try:
            err = p.stderr.read(400).decode("utf-8", "ignore")
        except OSError:
            pass
        return False, "الجسر لم يردّ خلال المهلة. " + err
    if r.get("status") == 200:
        return True, "الجسر يعمل ويتصل بسيل"
    j = r.get("json") or {}
    if r.get("status") == 401 and not (r.get("json") or {}).get("server_fp"):
        return False, ("المنفذ يردّ عليه برنامج قديم أو غريب (ليس هذه النسخة من سيل). "
                       "أنهِ كل عمليات Sayl.exe من مدير المهام ثم شغّل سيل من جديد.")
    if r.get("status") == 401:
        return False, (f"الرمز لا يطابق. بصمة الجسر: {j.get('host_fp')} | بصمة البرنامج: {j.get('server_fp')} | "
                       f"ملف الجسر: {j.get('host_cfg')} (موجود: {j.get('host_cfg_exists')}) | "
                       f"ملف البرنامج: {j.get('server_cfg')}")
    return False, f"الجسر ردّ بخطأ: {r}"
