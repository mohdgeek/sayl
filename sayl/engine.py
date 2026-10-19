"""محرّك التحميل: تقسيم الملف إلى أجزاء تُحمَّل بالتوازي عبر HTTP Range،
مع استئناف وتقسيم ديناميكي وتحديد للسرعة."""
import json
import os
import re
import threading
import time
import urllib.parse
import uuid

import requests

from .categories import LABELS, category_of

CHUNK = 64 * 1024
MIN_SPLIT = 512 * 1024      # أصغر جزء يمكن تقسيمه
MAX_RETRIES = 6
META_EXT = ".sayl"

QUEUED, PROBING, DOWNLOADING, PAUSED, COMPLETED, ERROR, CANCELED = (
    "queued", "probing", "downloading", "paused", "completed", "error", "canceled")


class RateLimiter:
    """محدّد سرعة مشترك بين كل الاتصالات (بايت/ثانية، 0 = بلا حد)."""

    def __init__(self, rate=0):
        self.rate = rate
        self._lock = threading.Lock()
        self._allow = 0.0
        self._t = time.monotonic()

    def set_rate(self, rate):
        with self._lock:
            self.rate = max(0, int(rate))

    def acquire(self, n):
        while True:
            with self._lock:
                rate = self.rate
                if rate <= 0:
                    return
                now = time.monotonic()
                self._allow = min(max(rate, n), self._allow + (now - self._t) * rate)
                self._t = now
                if self._allow >= n:
                    self._allow -= n
                    return
                wait = (n - self._allow) / rate
            time.sleep(min(wait, 0.25))


def sanitize_filename(name):
    name = urllib.parse.unquote(name or "").strip().strip(".")
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    return name[:200] or "download"


def filename_from_response(resp, url):
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*\s*=\s*[^']*'[^']*'([^;]+)", cd, re.I)
    if m:
        return sanitize_filename(m.group(1))
    m = re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.I)
    if m:
        return sanitize_filename(m.group(1))
    path = urllib.parse.urlparse(resp.url or url).path
    return sanitize_filename(os.path.basename(path)) if os.path.basename(path) else "download"


def unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


class NoRangeError(IOError):
    """الخادم تجاهل Range وأعاد الملف كاملًا: نتحوّل إلى اتصال واحد."""


def _set_sparse(f):
    """ويندوز: بدون ملف متناثر (sparse) يملأ NTFS بالأصفار كل ما قبل أول كتابة بعيدة،
    فيتجمّد تحميل ملف 8GB بالأجزاء المتعددة دقائق عند 0%."""
    if os.name != "nt":
        return
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes
        h = msvcrt.get_osfhandle(f.fileno())
        ret = wintypes.DWORD()
        ctypes.windll.kernel32.DeviceIoControl(wintypes.HANDLE(h), 0x000900C4, None, 0, None, 0,
                                               ctypes.byref(ret), None)
    except Exception:  # noqa: BLE001
        pass


class Segment:
    __slots__ = ("start", "end", "pos")

    def __init__(self, start, end, pos=None):
        self.start, self.end = start, end   # end حصري
        self.pos = start if pos is None else pos

    def to_list(self):
        return [self.start, self.end, self.pos]


class Download:
    kind = "http"

    def __init__(self, url, dest_dir, headers=None, connections=8, limiter=None,
                 filename=None, id=None):
        self.id = id or uuid.uuid4().hex[:10]
        self.url = url
        self.final_url = url
        self.headers = dict(headers or {})
        self.dest_dir = dest_dir
        self.filename = filename
        self.connections = max(1, int(connections))
        self.limiter = limiter or RateLimiter(0)
        self.size = None
        self.resumable = False
        self.etag = None
        self.segments = []
        self.state = QUEUED
        self.error = ""
        self.speed = 0.0
        self.created = time.time()
        self.single_bytes = 0          # تقدّم الوضع غير المقسّم
        self.subfolder = False         # حفظ الملف في مجلد فرعي حسب نوعه
        self.final_dir = None          # المجلد النهائي بعد الاكتمال
        self.force = True              # يبدأ حتى خارج نافذة الجدولة
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._session = requests.Session()
        self._tmp = None

    # ------------------------------------------------------------ الحالة
    @property
    def downloaded(self):
        if self.segments:
            return sum(s.pos - s.start for s in self.segments)
        return self.single_bytes

    @property
    def progress(self):
        if self.state == COMPLETED:
            return 1.0
        if self.size:
            return min(1.0, self.downloaded / self.size)
        return 0.0

    @property
    def eta(self):
        if self.size and self.speed > 1:
            return (self.size - self.downloaded) / self.speed
        return None

    @property
    def active(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def folder(self):
        return self.final_dir or self.dest_dir

    def to_dict(self):
        return {
            "id": self.id, "kind": "http", "url": self.url, "headers": self.headers,
            "subfolder": self.subfolder, "final_dir": self.final_dir,
            "dest_dir": self.dest_dir, "filename": self.filename,
            "connections": self.connections, "size": self.size,
            "resumable": self.resumable, "etag": self.etag,
            "state": self.state, "created": self.created,
        }

    # ------------------------------------------------------------ التحكم
    last_issue = ""

    def start(self):
        if self.active:
            return
        self.error = ""
        self.state = PROBING
        old = self._thread
        if old is not None and old.is_alive():      # إيقاف سابق ما زال يُنهي اتصالاته: ننتظره بعيدًا عن الواجهة
            def later():
                old.join(timeout=40)
                if self.state == PROBING:
                    self._stop.clear()
                    self._run()
            self._thread = threading.Thread(target=later, daemon=True, name="dl-" + self.id)
        else:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True, name="dl-" + self.id)
        self._thread.start()

    def pause(self):
        """إيقاف فوري من جهة الواجهة؛ إغلاق الاتصالات يتم في الخلفية دون تجميد البرنامج."""
        if self.state == QUEUED:
            self.state = PAUSED
            return
        self._stop.set()
        if self.state not in (COMPLETED, ERROR, CANCELED):
            self.state = PAUSED
        self.speed = 0.0

    def wait(self, timeout=3.0):
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout)

    def cancel(self, delete_files=True):
        self._stop.set()
        self.state = CANCELED
        self.speed = 0.0
        if delete_files:
            def clean():
                self.wait(40)                       # بعد أن تُغلق الاتصالات نحذف الملفات
                for p in (self._tmp, self._tmp and self._tmp + ".json"):
                    if p:
                        try:
                            os.remove(p)
                        except OSError:
                            pass
            threading.Thread(target=clean, daemon=True).start()

    # ------------------------------------------------------------ التنفيذ
    def _req_headers(self, extra=None):
        h = dict(self.headers)
        h.setdefault("User-Agent", "Mozilla/5.0 Sayl/1.0")
        if extra:
            h.update(extra)
        return h

    def _probe(self):
        r = self._session.get(self.url, headers=self._req_headers({"Range": "bytes=0-0"}),
                              stream=True, timeout=(10, 30), allow_redirects=True)
        try:
            if r.status_code >= 400:
                raise IOError(f"HTTP {r.status_code}")
            self.final_url = r.url
            self.etag = r.headers.get("ETag") or r.headers.get("Last-Modified")
            if r.status_code == 206:
                m = re.search(r"/(\d+)$", r.headers.get("Content-Range", ""))
                self.size = int(m.group(1)) if m else None
                self.resumable = self.size is not None
            else:
                cl = r.headers.get("Content-Length")
                self.size = int(cl) if cl and cl.isdigit() else None
                self.resumable = False
            if not self.filename:
                self.filename = filename_from_response(r, self.url)
        finally:
            r.close()

    def _run(self):
        try:
            os.makedirs(self.dest_dir, exist_ok=True)
            if self.size is None or not self.filename:
                self._probe()
            if self._stop.is_set():
                return
            self._tmp = os.path.join(self.dest_dir, self.filename + META_EXT)
            self.state = DOWNLOADING
            if self.resumable and self.size:
                self._run_segmented()
            else:
                self._run_single()
            if self._stop.is_set():
                return
            self._finish()
        except Exception as e:  # noqa: BLE001
            if self._stop.is_set():
                return
            self.error = str(e)
            self.state = ERROR
        finally:
            self.speed = 0.0

    # ---- وضع الأجزاء المتعددة
    def _load_or_create_segments(self):
        meta = self._tmp + ".json"
        if self.segments:   # استئناف من الذاكرة
            return
        if os.path.exists(meta) and os.path.exists(self._tmp):
            try:
                with open(meta, "r", encoding="utf-8") as f:
                    m = json.load(f)
                if m.get("size") == self.size and m.get("etag") == self.etag:
                    self.segments = [Segment(*s) for s in m["segments"]]
                    return
            except (OSError, ValueError, TypeError):
                pass
        n = max(1, min(self.connections, self.size // MIN_SPLIT))
        part = self.size // n
        self.segments = [Segment(i * part, self.size if i == n - 1 else (i + 1) * part)
                         for i in range(n)]
        with open(self._tmp, "wb") as f:
            _set_sparse(f)
            f.truncate(self.size)

    def _save_meta(self):
        with self._lock:
            data = {"url": self.url, "size": self.size, "etag": self.etag,
                    "segments": [s.to_list() for s in self.segments]}
        tmp = self._tmp + ".json.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, self._tmp + ".json")

    def _steal(self):
        """يأخذ النصف العلوي من أكبر جزء متبقٍّ لإبقاء كل الاتصالات مشغولة."""
        with self._lock:
            victim = max(self.segments, key=lambda s: s.end - s.pos, default=None)
            if victim is None or victim.end - victim.pos < 2 * MIN_SPLIT:
                return None
            mid = victim.pos + (victim.end - victim.pos) // 2
            new = Segment(mid, victim.end)
            victim.end = mid
            self.segments.append(new)
            return new

    def _fetch_segment(self, seg):
        failures = 0
        while not self._stop.is_set():
            with self._lock:
                if seg.pos >= seg.end:
                    return
                start, end = seg.pos, seg.end
            try:
                hdr = {"Range": f"bytes={start}-{end - 1}"}
                if self.etag and not self.etag.startswith("W/"):
                    hdr["If-Range"] = self.etag
                with self._session.get(self.final_url, headers=self._req_headers(hdr),
                                       stream=True, timeout=(10, 30)) as r:
                    if r.status_code == 200:
                        raise NoRangeError("الخادم لا يدعم التحميل المجزّأ")
                    if r.status_code != 206:
                        raise IOError(f"استجابة غير متوقعة HTTP {r.status_code}")
                    with open(self._tmp, "r+b") as f:
                        for chunk in r.iter_content(CHUNK):
                            if self._stop.is_set():
                                return
                            self.limiter.acquire(len(chunk))
                            with self._lock:
                                allowed = seg.end - seg.pos
                                if allowed <= 0:
                                    break
                                data, pos = chunk[:allowed], seg.pos
                            f.seek(pos)
                            f.write(data)
                            with self._lock:
                                seg.pos += len(data)
                failures = 0 if seg.pos > start else failures + 1
                if failures == 0 and seg.pos < seg.end:
                    continue
            except NoRangeError:
                raise
            except Exception as e:  # noqa: BLE001
                failures += 1
                self.last_issue = str(e)[:200]
                if failures == 1:
                    try:
                        from .setup_tools import log
                        log(f"engine: فشل جزء ({self.filename}): {self.last_issue}")
                    except Exception:  # noqa: BLE001
                        pass
            if failures > MAX_RETRIES:
                raise IOError("فشل تحميل جزء بعد عدة محاولات")
            if failures:
                self._stop.wait(min(2 ** failures * 0.5, 15))

    def _worker(self, seg, errors):
        try:
            while seg is not None and not self._stop.is_set():
                self._fetch_segment(seg)
                seg = None if self._stop.is_set() else self._steal()
        except Exception as e:  # noqa: BLE001
            errors.append(e)
            self._stop.set()

    def _run_segmented(self):
        self._load_or_create_segments()
        errors = []
        threads = []
        for seg in list(self.segments):
            if seg.pos < seg.end:
                t = threading.Thread(target=self._worker, args=(seg, errors), daemon=True)
                t.start()
                threads.append(t)
        last_bytes, last_t, last_save = self.downloaded, time.monotonic(), 0.0
        while any(t.is_alive() for t in threads):
            time.sleep(0.25)
            now = time.monotonic()
            cur = self.downloaded
            inst = (cur - last_bytes) / max(now - last_t, 1e-6)
            self.speed = inst if self.speed == 0 else 0.6 * self.speed + 0.4 * inst
            last_bytes, last_t = cur, now
            if now - last_save > 1.0:
                self._save_meta()
                last_save = now
        for t in threads:
            t.join()
        self._save_meta()
        if errors:
            self._stop.clear()
            if isinstance(errors[0], NoRangeError):
                self.resumable, self.segments = False, []
                try:
                    os.remove(self._tmp + ".json")
                except OSError:
                    pass
                return self._run_single()
            raise errors[0]
        if not self._stop.is_set() and any(s.pos < s.end for s in self.segments):
            raise IOError("اكتمل التحميل جزئيًا")

    # ---- وضع الاتصال الواحد (بدون دعم Range)
    def _run_single(self):
        self.segments = []
        self.single_bytes = 0
        last_bytes, last_t = 0, time.monotonic()
        with self._session.get(self.final_url, headers=self._req_headers(), stream=True,
                               timeout=(10, 30)) as r:
            if r.status_code >= 400:
                raise IOError(f"HTTP {r.status_code}")
            with open(self._tmp, "wb") as f:
                for chunk in r.iter_content(CHUNK):
                    if self._stop.is_set():
                        return
                    self.limiter.acquire(len(chunk))
                    f.write(chunk)
                    self.single_bytes += len(chunk)
                    now = time.monotonic()
                    if now - last_t >= 0.5:
                        inst = (self.single_bytes - last_bytes) / (now - last_t)
                        self.speed = inst if self.speed == 0 else 0.6 * self.speed + 0.4 * inst
                        last_bytes, last_t = self.single_bytes, now
        if self.size is None:
            self.size = self.single_bytes

    def _finish(self):
        if self.size is not None and os.path.getsize(self._tmp) != self.size and self.resumable:
            raise IOError("حجم الملف النهائي لا يطابق الحجم المتوقع")
        target = self.dest_dir
        if self.subfolder:
            target = os.path.join(self.dest_dir, LABELS[category_of(self.filename)])
            os.makedirs(target, exist_ok=True)
        final = unique_path(os.path.join(target, self.filename))
        os.replace(self._tmp, final)
        self.filename = os.path.basename(final)
        self.final_dir = target
        try:
            os.remove(self._tmp + ".json")
        except OSError:
            pass
        self.state = COMPLETED
