"""إرسال ملف مكتمل إلى الهاتف: خادم محلي مؤقت على الشبكة المنزلية + رمز QR. يتوقف عند إغلاق النافذة."""
import io
import os
import secrets
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

IDLE_TIMEOUT = 15 * 60


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))        # لا يُرسَل شيء فعليًا، فقط لمعرفة واجهة الشبكة
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def qr_png(text, scale=7):
    import segno
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="png", scale=scale, border=2)
    return buf.getvalue()


class ShareServer:
    """يخدم ملفًا واحدًا فقط عبر مسار سري عشوائي، مع دعم الاستئناف (Range)."""

    def __init__(self, path, host=None):
        self.path = os.path.abspath(path)
        self.token = secrets.token_urlsafe(12)
        self.name = os.path.basename(path)
        self.size = os.path.getsize(path)
        self.hits = 0
        self.sent = 0
        self.cur = 0            # بايتات الطلب الجاري
        self.cur_total = 0      # حجم الطلب الجاري
        self.completed = 0      # عدد مرات وصول الملف كاملًا إلى الهاتف
        self.last = time.monotonic()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.last = time.monotonic()
                want = "/" + outer.token + "/" + urllib.parse.quote(outer.name)
                if self.path.split("?")[0] != want:
                    self.send_error(404)
                    return
                start, end = 0, outer.size - 1
                rng = self.headers.get("Range")
                code = 200
                if rng and rng.startswith("bytes="):
                    try:
                        a, b = rng[6:].split("-", 1)
                        start = int(a) if a else outer.size - int(b)
                        end = int(b) if (a and b) else outer.size - 1
                        end = min(end, outer.size - 1)
                        if start < 0 or start > end:
                            raise ValueError
                        code = 206
                    except ValueError:
                        self.send_error(416)
                        return
                outer.hits += 1
                outer.cur, outer.cur_total = 0, end - start + 1
                self.send_response(code)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Disposition",
                                 "attachment; filename*=UTF-8''" + urllib.parse.quote(outer.name))
                if code == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{outer.size}")
                self.end_headers()
                try:
                    with open(outer.path, "rb") as f:
                        f.seek(start)
                        left = end - start + 1
                        while left > 0:
                            chunk = f.read(min(256 * 1024, left))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            left -= len(chunk)
                            outer.sent += len(chunk)
                            outer.cur += len(chunk)
                            outer.last = time.monotonic()
                        if left == 0 and end == outer.size - 1:
                            outer.completed += 1
                except (BrokenPipeError, ConnectionError, OSError):
                    pass

        self.httpd = ThreadingHTTPServer(("0.0.0.0", 0), H)
        self.port = self.httpd.server_address[1]
        self.host = host or lan_ip()
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="share").start()

    @property
    def url(self):
        return f"http://{self.host}:{self.port}/{self.token}/{urllib.parse.quote(self.name)}"

    def idle(self):
        return time.monotonic() - self.last > IDLE_TIMEOUT

    def stop(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except OSError:
            pass
