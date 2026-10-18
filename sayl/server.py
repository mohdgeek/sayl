"""خادم محلي (127.0.0.1) تستخدمه إضافة المتصفح لإرسال التحميلات إلى البرنامج.

الحماية: يقبل الاتصال من الجهاز نفسه فقط، ويتحقق من ترويسة Host (ضد DNS rebinding)،
ويشترط رمز مصادقة سرّيًا في ترويسة X-Sayl-Token.
"""
from .manager import DuplicateError
import hmac
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .media import available as media_available

ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
ALLOWED_HEADERS = {"cookie", "referer", "user-agent", "authorization", "origin"}
MAX_BODY = 64 * 1024


_PAIR = {"until": 0.0}
STATS = {"seen": 0.0, "browser": "", "bad": 0.0, "via": ""}   # آخر اتصال ناجح من الإضافة، ومحاولة برمز خاطئ


def _browser_of(ua):
    for key, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Vivaldi", "Vivaldi"),
                      ("Firefox", "Firefox"), ("Chrome", "Chrome")):
        if key in ua:
            return name
    return ""


def open_pairing(seconds=300):
    """يفتح نافذة قصيرة تسمح لإضافة المتصفح (فقط) بجلب الرمز تلقائيًا بعد أن يطلب المستخدم ذلك."""
    _PAIR["until"] = time.time() + seconds


def _fp(tok):
    import hashlib
    return hashlib.sha256((tok or "").encode()).hexdigest()[:8] if tok else "empty"


def _tok_ok(given, expected):
    return bool(expected) and hmac.compare_digest((given or "").encode(), expected.encode())


def _disk_token(config):
    """الرمز المحفوظ في ملف الإعدادات الآن (يعالج حالة أن عملية قديمة تحمل رمزًا قديمًا في الذاكرة)."""
    try:
        with open(config.path, "r", encoding="utf-8") as f:
            return str(json.load(f).get("token") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def make_handler(manager, config, on_add=None, on_confirm=None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Sayl"

        def log_message(self, *a):   # بدون ضجيج في الطرفية
            pass

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            origin = self.headers.get("Origin", "")
            if origin.startswith("chrome-extension://"):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(body)

        def _check(self):
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            if host not in ALLOWED_HOSTS:
                self._send(403, {"ok": False, "error": "host"})
                return False
            origin = self.headers.get("Origin", "")
            if origin and not origin.startswith("chrome-extension://"):
                self._send(403, {"ok": False, "error": "origin"})
                return False
            native = self.headers.get("X-Sayl-Via") == "native"
            tok = self.headers.get("X-Sayl-Token", "")
            if not (_tok_ok(tok, config["token"]) or _tok_ok(tok, _disk_token(config))):
                if origin or native:
                    STATS["bad"] = time.time()
                self._send(401, {"ok": False, "error": "token", "server_fp": _fp(config["token"]),
                                 "server_cfg": getattr(config, "path", "")})
                return False
            if origin or native:
                STATS["seen"] = time.time()
                ua = self.headers.get("X-Sayl-UA") if native else self.headers.get("User-Agent", "")
                STATS["browser"] = _browser_of(ua or "")
                STATS["via"] = "native" if native else "http"
            return True

        def do_OPTIONS(self):
            origin = self.headers.get("Origin", "")
            self.send_response(204)
            if origin.startswith("chrome-extension://"):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Sayl-Token")
                self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.end_headers()

        def _pair(self):
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            origin = self.headers.get("Origin", "")
            if host not in ALLOWED_HOSTS or not origin.startswith("chrome-extension://"):
                return self._send(403, {"ok": False, "error": "origin"})
            if time.time() > _PAIR["until"]:
                return self._send(403, {"ok": False, "error": "pairing_closed"})
            self._send(200, {"ok": True, "token": config["token"], "port": int(config["port"])})

        def do_GET(self):
            if self.path == "/pair":
                return self._pair()
            if self.path == "/ping" and self._check():
                self._send(200, {"ok": True, "app": "Sayl", "media": media_available()})
            else:
                if self.path != "/ping":
                    self._send(404, {"ok": False})

        def _probe(self):
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if n <= 0 or n > MAX_BODY:
                    raise ValueError("size")
                data = json.loads(self.rfile.read(n))
                from . import media
                info = media.probe(str(data["url"]), data.get("headers"), max_entries=1)
                self._send(200, {"ok": True, "title": info.get("title", ""), "duration": info.get("duration"),
                                 "heights": info.get("heights", []), "playlist": bool(info.get("playlist")),
                                 "is_live": bool(info.get("is_live"))})
            except (ValueError, KeyError, TypeError) as e:
                self._send(400, {"ok": False, "error": str(e)})
            except Exception as e:  # noqa: BLE001
                self._send(200, {"ok": False, "error": str(e)[:300]})

        def do_POST(self):
            if self.path not in ("/add", "/probe"):
                return self._send(404, {"ok": False})
            if not self._check():
                return
            if self.path == "/probe":
                return self._probe()
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if n <= 0 or n > MAX_BODY:
                    raise ValueError("size")
                data = json.loads(self.rfile.read(n))
                url = str(data["url"])
                hdrs = {k: str(v) for k, v in (data.get("headers") or {}).items()
                        if k.lower() in ALLOWED_HEADERS}
                kind = str(data.get("kind") or "auto")
                if kind not in ("auto", "http", "media"):
                    raise ValueError("kind")
                height = data.get("height")
                height = int(height) if height else None
                if height is not None and not 100 <= height <= 4320:
                    raise ValueError("height")
                if data.get("confirm") and on_confirm:       # يفتح البرنامج نافذة اختيار الجودة والمجلد
                    on_confirm(url, hdrs, kind)
                    self._send(200, {"ok": True, "confirm": True})
                    return
                try:
                    d = manager.add(url, headers=hdrs, filename=data.get("filename") or None,
                                    kind=kind, height=height,
                                    audio_only=bool(data.get("audio_only")),
                                    allow_duplicate=bool(data.get("allow_duplicate")))
                except DuplicateError as dup:
                    self._send(200, {"ok": True, "duplicate": True, "id": dup.existing.id})
                    return
                if on_add:
                    on_add(d)
                self._send(200, {"ok": True, "id": d.id})
            except (ValueError, KeyError, TypeError) as e:
                self._send(400, {"ok": False, "error": str(e)})

    return Handler


class _Server(ThreadingHTTPServer):
    # على ويندوز يسمح SO_REUSEADDR لعمليتين بربط المنفذ نفسه، فتبقى النسخة القديمة هي التي تردّ
    # (وهذا ما سبّب خطأ الرمز). نمنع ذلك ليفشل الربط الثاني فنختار منفذًا آخر.
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def start_server(manager, config, on_add=None, on_confirm=None):
    """يشغّل الخادم على المنفذ المضبوط، وإن كان مشغولًا يختار منفذًا حرًّا ويحفظه في الإعدادات.

    يعيد (الخادم، هل تغيّر المنفذ؟). الجسر يقرأ المنفذ من ملف الإعدادات فيتبع التغيير تلقائيًا.
    """
    wanted = int(config["port"])
    try:
        srv = ApiServer(manager, config, on_add, on_confirm)
        fell_back = False
    except OSError:
        config["port"] = 0
        srv = ApiServer(manager, config, on_add, on_confirm)
        config["port"] = srv.port
        config.save()
        fell_back = True
    srv.start()
    srv.wanted_port = wanted
    return srv, fell_back


class ApiServer:
    def __init__(self, manager, config, on_add=None, on_confirm=None):
        self.httpd = _Server(("127.0.0.1", int(config["port"])),
                             make_handler(manager, config, on_add, on_confirm))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def port(self):
        return self.httpd.server_address[1]

    def start(self):
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
