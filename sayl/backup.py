"""نسخ احتياطي واستعادة: الإعدادات + قائمة التحميلات + الإحصاءات في ملف zip واحد."""
import json
import os
import tempfile
import time
import zipfile

from . import __version__

SECRET_KEYS = ("token", "app_exe", "port")


def make_backup(manager, config, path):
    manager.save()
    cfg = {k: v for k, v in config.data.items() if k not in SECRET_KEYS}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("meta.json", json.dumps({"app": "sayl", "version": __version__,
                                            "time": int(time.time())}))
        z.writestr("config.json", json.dumps(cfg, ensure_ascii=False, indent=2))
        for name, p in (("downloads.json", manager.state_path), ("stats.json", manager.stats.path)):
            if os.path.exists(p):
                z.write(p, name)
    return path


def restore_backup(manager, config, path):
    """يعيد الإعدادات ويدمج التحميلات (لا يكرر الموجود). يعيد عدد التحميلات المضافة."""
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "meta.json" not in names or json.loads(z.read("meta.json")).get("app") != "sayl":
            raise ValueError("هذا ليس ملف نسخة احتياطية من Sayl")
        cfg = json.loads(z.read("config.json")) if "config.json" in names else {}
        added = 0
        if "downloads.json" in names:
            with tempfile.TemporaryDirectory() as td:
                p = os.path.join(td, "d.json")
                with open(p, "wb") as f:
                    f.write(z.read("downloads.json"))
                added = manager._load(p)
        if "stats.json" in names and not manager.stats.data.get("days"):
            try:
                manager.stats.data = json.loads(z.read("stats.json"))
                manager.stats.save()
            except (ValueError, AttributeError, OSError):
                pass
    for k, v in cfg.items():
        if k not in SECRET_KEYS:
            config.data[k] = v
    config.save()
    manager.save()
    return added
