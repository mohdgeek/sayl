"""جسر Native Messaging بين إضافة المتصفح وبرنامج سيل (مكتبة بايثون القياسية فقط).

المتصفح يشغّل هذا البرنامج الصغير ويكلّمه عبر stdin/stdout، وهو يمرّر الطلب إلى سيل على الجهاز نفسه.
بهذا لا يحتاج المستخدم إلى رمز ولا منفذ: المتصفح نفسه يضمن أن إضافة سيل وحدها (حسب معرّفها الثابت)
هي من تستطيع استدعاءه، والرمز يقرؤه الجسر من ملف إعدادات سيل بنفسه.
"""
import json
import os
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request

APP_DIR = os.path.join(os.path.expanduser("~"), ".sayl")
CONFIG = os.environ.get("SAYL_CONFIG") or os.path.join(APP_DIR, "config.json")
MAX_MSG = 8 * 1024 * 1024


def read_config(path=None):
    try:
        with open(path or CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def read_message(stream):
    raw = stream.read(4)
    if len(raw) < 4:
        return None
    (n,) = struct.unpack("<I", raw)
    if n > MAX_MSG:
        return None
    return json.loads(stream.read(n).decode("utf-8"))


def write_message(stream, obj):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    stream.write(struct.pack("<I", len(data)) + data)
    stream.flush()


def _call(cfg, msg, timeout=30):
    url = f"http://127.0.0.1:{int(cfg.get('port') or 6800)}{msg['path']}"
    body = json.dumps(msg["body"]).encode() if msg.get("body") is not None else None
    req = urllib.request.Request(url, data=body, method=msg.get("method") or "GET", headers={
        "Content-Type": "application/json", "X-Sayl-Token": cfg.get("token", ""),
        "X-Sayl-Via": "native", "X-Sayl-UA": str(msg.get("ua", ""))[:300]})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"status": r.status, "json": json.load(r)}
    except urllib.error.HTTPError as e:
        try:
            payload = json.load(e)
        except ValueError:
            payload = {}
        return {"status": e.code, "json": payload}


def _launch_app(cfg):
    exe = cfg.get("app_exe")
    if not exe or not os.path.isfile(exe):
        return False
    flags = 0x00000008 | 0x08000000 if sys.platform.startswith("win") else 0   # DETACHED | NO_WINDOW
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI") and k != "_MEIPASS2"}
    try:
        subprocess.Popen([exe, "--minimized"], creationflags=flags, close_fds=True, env=env)
        return True
    except OSError:
        return False


def handle(msg, cfg=None, launch=True, wait=20.0):
    """يعالج رسالة واحدة ويعيد ردًا. إن كان سيل مغلقًا يحاول تشغيله وينتظر جاهزيته."""
    cfg = cfg or read_config()
    if not str(msg.get("path", "")).startswith("/"):
        return {"id": msg.get("id"), "error": "bad_request"}
    try:
        return {"id": msg.get("id"), **_call(cfg, msg)}
    except (urllib.error.URLError, OSError):
        pass
    if launch and _launch_app(cfg):
        end = time.time() + wait
        while time.time() < end:
            time.sleep(1)
            cfg = read_config()
            try:
                return {"id": msg.get("id"), **_call(cfg, msg)}
            except (urllib.error.URLError, OSError):
                continue
    return {"id": msg.get("id"), "error": "app_not_running"}


def main(stdin=None, stdout=None):
    if sys.platform.startswith("win"):
        import msvcrt
        msvcrt.setmode(0, os.O_BINARY)
        msvcrt.setmode(1, os.O_BINARY)
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    while True:
        try:
            msg = read_message(stdin)
        except (ValueError, OSError):
            return
        if msg is None:
            return
        write_message(stdout, handle(msg))


if __name__ == "__main__":
    main()
