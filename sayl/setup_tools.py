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
        json.dump({"port": int(config["port"]), "token": config["token"]}, f)
    try:
        os.chmod(pair, 0o600)
    except OSError:
        pass
    return dest


# ------------------------------------------------------------ التشغيل مع ويندوز
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_supported():
    return sys.platform.startswith("win") and bool(getattr(sys, "frozen", False))


def autostart_enabled():
    if not autostart_supported():
        return False
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
