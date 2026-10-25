"""إرسال الملفات بين جهازين يعمل عليهما سيل على الشبكة نفسها.
الاستقبال معطّل افتراضيًا. حين يُفعَّل: نكتشف الجهاز عبر UDP، والمستقبل يوافق يدويًا على كل ملف."""
import hashlib
import http.client
import json
import os
import secrets
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .engine import sanitize_filename, unique_path

PORT = 47631            # TCP: عرض ونقل الملف
DISCOVER_PORT = 47632   # UDP: اكتشاف الأجهزة
MAGIC = b"SAYL-DISCOVER-1"
OFFER_TIMEOUT = 60      # مهلة موافقة المستخدم
CHUNK = 256 * 1024


def device_name():
    return socket.gethostname() or "جهاز"


def broadcast_targets():
    from .share import lan_ip
    ip = lan_ip()
    out = ["255.255.255.255"]
    if ip.count(".") == 3 and not ip.startswith("127."):
        out.append(ip.rsplit(".", 1)[0] + ".255")
    return out


# ------------------------------------------------------------------ الاستقبال
class LanReceiver:
    def __init__(self, recv_dir, name, on_offer, on_done=None, port=PORT, discover_port=DISCOVER_PORT):
        self.recv_dir, self.name = recv_dir, name
        self.on_offer = on_offer          # (info) -> bool ، يُستدعى من خيط الخادم وقد يحجب
        self.on_done = on_done            # (info) بعد الاكتمال
        self.id = secrets.token_hex(4)
        self._tokens = {}
        self._stop = threading.Event()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, obj):
                b = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                if self.path == "/info":
                    self._json(200, {"app": "sayl", "name": outer.name, "version": __version__})
                else:
                    self.send_error(404)

            def do_POST(self):
                if self.path != "/offer":
                    self.send_error(404)
                    return
                try:
                    n = int(self.headers.get("Content-Length", "0"))
                    if not 0 < n < 4096:
                        raise ValueError
                    d = json.loads(self.rfile.read(n))
                    name = sanitize_filename(os.path.basename(str(d["name"]))) or "file"
                    size = int(d["size"])
                    if size < 0:
                        raise ValueError
                except (ValueError, KeyError, TypeError):
                    self._json(400, {"ok": False, "error": "طلب غير صالح"})
                    return
                info = {"name": name, "size": size, "sender": str(d.get("sender", ""))[:60],
                        "addr": self.client_address[0]}
                if not _enough_space(outer.recv_dir, size):
                    self._json(507, {"ok": False, "error": "لا توجد مساحة كافية عند المستقبل"})
                    return
                try:
                    ok = bool(outer.on_offer(info))
                except Exception:  # noqa: BLE001
                    ok = False
                if not ok:
                    self._json(403, {"ok": False, "error": "رفض المستقبل الملف"})
                    return
                tok = secrets.token_urlsafe(12)
                outer._tokens[tok] = info
                self._json(200, {"ok": True, "token": tok})

            def do_PUT(self):
                tok = self.path.rsplit("/", 1)[-1] if self.path.startswith("/send/") else None
                info = outer._tokens.pop(tok, None) if tok else None
                if info is None:
                    self.send_error(403)
                    return
                os.makedirs(outer.recv_dir, exist_ok=True)
                dest = unique_path(os.path.join(outer.recv_dir, info["name"]))
                part = dest + ".sayl-part"
                h, got = hashlib.sha256(), 0
                n = int(self.headers.get("Content-Length", "0"))
                try:
                    if n != info["size"]:
                        raise IOError("الحجم لا يطابق العرض")
                    with open(part, "wb") as f:
                        while got < n:
                            chunk = self.rfile.read(min(CHUNK, n - got))
                            if not chunk:
                                raise IOError("انقطع الإرسال")
                            f.write(chunk)
                            h.update(chunk)
                            got += len(chunk)
                    os.replace(part, dest)
                except (IOError, OSError) as e:
                    try:
                        os.remove(part)
                    except OSError:
                        pass
                    try:
                        self._json(500, {"ok": False, "error": str(e)})
                    except OSError:
                        pass
                    return
                info = dict(info, path=dest, sha256=h.hexdigest())
                self._json(200, {"ok": True, "sha256": info["sha256"], "name": os.path.basename(dest)})
                if outer.on_done:
                    try:
                        outer.on_done(info)
                    except Exception:  # noqa: BLE001
                        pass

        self.httpd = ThreadingHTTPServer(("0.0.0.0", port), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="lan-recv").start()
        self._udp = None
        try:
            u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            u.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            u.bind(("0.0.0.0", discover_port))
            u.settimeout(0.5)
            self._udp = u
            threading.Thread(target=self._answer, daemon=True, name="lan-udp").start()
        except OSError:
            self._udp = None          # الاكتشاف غير متاح: يبقى الإدخال اليدوي للعنوان

    def _answer(self):
        while not self._stop.is_set():
            try:
                data, addr = self._udp.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                return
            if data.startswith(MAGIC):
                msg = json.dumps({"app": "sayl", "id": self.id, "name": self.name, "port": self.port}).encode()
                try:
                    self._udp.sendto(msg, addr)
                except OSError:
                    pass

    def stop(self):
        self._stop.set()
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except OSError:
            pass
        if self._udp:
            try:
                self._udp.close()
            except OSError:
                pass


def _enough_space(folder, size):
    import shutil
    d = folder
    while d and not os.path.isdir(d):
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    try:
        return shutil.disk_usage(d or ".").free > size + 200 * 1024 * 1024
    except OSError:
        return True


# ------------------------------------------------------------------ الإرسال
def scan(timeout=1.5, targets=None, port=DISCOVER_PORT):
    """يعيد قائمة أجهزة سيل المتاحة: [{name, ip, port, id}]."""
    found = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.3)
    try:
        for t in (targets or broadcast_targets()):
            try:
                s.sendto(MAGIC, (t, port))
            except OSError:
                pass
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, addr = s.recvfrom(1024)
                d = json.loads(data)
                if d.get("app") == "sayl":
                    found[d["id"]] = {"name": d["name"], "ip": addr[0], "port": int(d["port"]), "id": d["id"]}
            except socket.timeout:
                continue
            except (ValueError, KeyError, OSError):
                continue
    finally:
        s.close()
    return list(found.values())


class SendError(Exception):
    pass


def send_file(ip, port, path, progress=None, cancel=None, sender=None):
    """يرسل ملفًا إلى جهاز سيل آخر. progress(sent, total). يعيد {name, sha256, verified}."""
    size = os.path.getsize(path)
    name = os.path.basename(path)
    try:
        c = http.client.HTTPConnection(ip, port, timeout=OFFER_TIMEOUT + 10)
        body = json.dumps({"name": name, "size": size, "sender": sender or device_name()}).encode()
        c.request("POST", "/offer", body, {"Content-Type": "application/json"})
        r = c.getresponse()
        resp = json.loads(r.read() or b"{}")
        if r.status != 200 or not resp.get("ok"):
            raise SendError(resp.get("error") or f"رفض الجهاز الطلب ({r.status})")
        c.close()
        c = http.client.HTTPConnection(ip, port, timeout=60)
        c.putrequest("PUT", "/send/" + resp["token"])
        c.putheader("Content-Length", str(size))
        c.putheader("Content-Type", "application/octet-stream")
        c.endheaders()
        h, sent = hashlib.sha256(), 0
        with open(path, "rb") as f:
            while True:
                if cancel is not None and cancel.is_set():
                    c.close()
                    raise SendError("أُلغي الإرسال")
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                c.send(chunk)
                h.update(chunk)
                sent += len(chunk)
                if progress:
                    progress(sent, size)
        r = c.getresponse()
        res = json.loads(r.read() or b"{}")
        c.close()
        if r.status != 200 or not res.get("ok"):
            raise SendError(res.get("error") or f"فشل الاستلام ({r.status})")
        return {"name": res.get("name", name), "sha256": h.hexdigest(),
                "verified": res.get("sha256") == h.hexdigest()}
    except (OSError, http.client.HTTPException, ValueError) as e:
        raise SendError(f"تعذّر الاتصال بالجهاز: {e}") from e
