"""تحميل التورنت: روابط magnet وملفات .torrent (من رابط أو من الجهاز) عبر libtorrent.

جلسة libtorrent واحدة مشتركة بين كل التورنتات. التحميل يتوقف بعد الاكتمال (بلا رفع)،
وكل تورنت يحفظ ملف .torrent في ~/.sayl/torrents ليُستأنف بعد إعادة التشغيل دون جلب البيانات من جديد.
"""
import os
import re
import shutil
import threading
import time
import urllib.parse
import uuid

import requests

from .config import APP_DIR
from .engine import (CANCELED, COMPLETED, DOWNLOADING, ERROR, PAUSED, PROBING, QUEUED,
                     RateLimiter, sanitize_filename)

try:                               # الحزمة اختيارية: بدونها تظهر رسالة واضحة عند إضافة تورنت
    import libtorrent as lt
except Exception:                  # noqa: BLE001
    lt = None

TORRENT_DIR = os.path.join(APP_DIR, "torrents")
MAX_TORRENT_BYTES = 20 * 1024 * 1024


def available():
    return lt is not None


def is_magnet(s):
    return str(s or "").strip().lower().startswith("magnet:?")


def _local_path(s):
    s = str(s or "").strip().strip('"')
    if s.lower().startswith("file:///"):
        s = urllib.parse.unquote(s[8:] if re.match(r"file:///[A-Za-z]:", s) else s[7:])
    return s


def is_torrent_url(s):
    """magnet، أو رابط/مسار ينتهي بـ .torrent."""
    s = str(s or "").strip()
    if is_magnet(s):
        return True
    path = s.split("?")[0].split("#")[0].lower()
    return path.endswith(".torrent")


def magnet_hash(url):
    m = re.search(r"xt=urn:btih:([0-9A-Za-z]+)", url or "", re.I)
    return m.group(1).lower() if m else ""


def magnet_name(url):
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        return sanitize_filename(q["dn"][0]) if q.get("dn") else ""
    except (ValueError, KeyError, IndexError):
        return ""


def friendly_error(e):
    t = str(e)
    if "No space" in t or "disk full" in t.lower():
        return "لا توجد مساحة كافية على القرص"
    if "Permission" in t or "Access" in t:
        return "لا صلاحية للكتابة في مجلد الحفظ"
    return t[:200] or "فشل تحميل التورنت"


# ------------------------------------------------------------------ الجلسة المشتركة
_ses = None
_ses_lock = threading.Lock()
_rate = 0


def _settings(extra=None):
    s = {
        "listen_interfaces": "0.0.0.0:6881,[::]:6881",
        "enable_dht": True, "enable_lsd": True, "enable_upnp": True, "enable_natpmp": True,
        "user_agent": "Sayl/2",
        "active_downloads": -1, "active_seeds": -1, "active_limit": -1,
        "connections_limit": 400,
        "download_rate_limit": _rate, "upload_rate_limit": 0,
    }
    s.update(extra or {})
    return s


def session(extra=None):
    global _ses
    if lt is None:
        raise ValueError("دعم التورنت يحتاج الحزمة libtorrent (pip install libtorrent)")
    with _ses_lock:
        if _ses is None:
            _ses = lt.session(_settings(extra))
            try:
                for host, port in (("router.bittorrent.com", 6881), ("dht.transmissionbt.com", 6881),
                                   ("router.utorrent.com", 6881), ("dht.libtorrent.org", 25401)):
                    _ses.add_dht_router(host, port)
            except Exception:  # noqa: BLE001
                pass
        return _ses


def reset_session():
    """للاختبارات: إنهاء الجلسة الحالية."""
    global _ses
    with _ses_lock:
        _ses = None


def apply_limit(rate_bytes):
    """سرعة التحميل الكلية (بايت/ث، 0 = بلا حد) من إعدادات سيل."""
    global _rate
    _rate = int(rate_bytes or 0)
    if _ses is not None:
        try:
            _ses.apply_settings({"download_rate_limit": _rate})
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------ التنزيل
class TorrentDownload:
    kind = "torrent"
    resumable = True
    connections = 1
    test_peers = []          # للاختبار فقط: أقران يُوصَل بهم مباشرة (host, port)

    def __init__(self, url, dest_dir, headers=None, limiter=None, filename=None, id=None,
                 subfolder=False):
        self.id = id or uuid.uuid4().hex[:10]
        self.url = url.strip()
        self.headers = dict(headers or {})
        self.dest_dir = dest_dir
        self.filename = filename or (magnet_name(self.url) if is_magnet(self.url) else None)
        self.subfolder = subfolder
        self.final_dir = None
        self.force = True
        self.note = ""
        self.limiter = limiter or RateLimiter(0)
        self.size = None
        self.state = QUEUED
        self.error = ""
        self.speed = 0.0
        self.created = time.time()
        self.etag = None
        self.segments = []
        self.torrent_file = os.path.join(TORRENT_DIR, self.id + ".torrent")
        self.peers = 0
        self.seeds = 0
        self.stage = ""            # وصف المرحلة الحالية (جلب البيانات، فحص الملفات…)
        self._frac = 0.0
        self._done = 0
        self._eta = None
        self._handle = None
        self._stop = threading.Event()
        self._thread = None

    # ---- نفس واجهة Download
    @property
    def downloaded(self):
        return self._done

    @property
    def progress(self):
        return 1.0 if self.state == COMPLETED else min(0.999, self._frac)

    @property
    def eta(self):
        return self._eta

    @property
    def active(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def folder(self):
        return self.final_dir or self.dest_dir

    def to_dict(self):
        return {"id": self.id, "kind": "torrent", "url": self.url, "headers": {},
                "dest_dir": self.dest_dir, "filename": self.filename, "size": self.size,
                "subfolder": self.subfolder, "final_dir": self.final_dir, "note": self.note,
                "state": self.state, "created": self.created}

    def start(self):
        if self.active:
            return
        self.error = ""
        self.state = PROBING
        old = self._thread
        if old is not None and old.is_alive():
            def later():
                old.join(timeout=20)
                if self.state == PROBING:
                    self._stop.clear()
                    self._run()
            self._thread = threading.Thread(target=later, daemon=True, name="tor-" + self.id)
        else:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True, name="tor-" + self.id)
        self._thread.start()

    def pause(self):
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

        def clean():
            self.wait(20)
            self._drop(delete_files=delete_files)
            try:
                os.remove(self.torrent_file)
            except OSError:
                pass
        threading.Thread(target=clean, daemon=True).start()

    # ---- التنفيذ
    def _drop(self, delete_files=False):
        h, self._handle = self._handle, None
        if h is None or _ses is None:
            return
        try:
            if delete_files:
                _ses.remove_torrent(h, getattr(lt.session, "delete_files", 1))
            else:
                _ses.remove_torrent(h)
        except Exception:  # noqa: BLE001
            pass

    def _params(self):
        """يجهّز add_torrent_params من ملف محفوظ، أو ملف/رابط .torrent، أو magnet."""
        os.makedirs(TORRENT_DIR, exist_ok=True)
        if not os.path.isfile(self.torrent_file):
            src = self.url
            if is_magnet(src):
                p = lt.parse_magnet_uri(src)
                p.save_path = self.dest_dir
                return p
            if src.lower().startswith(("http://", "https://")):
                r = requests.get(src, headers=self.headers, timeout=(10, 30), stream=True)
                if r.status_code >= 400:
                    raise IOError(f"تعذّر تنزيل ملف التورنت (HTTP {r.status_code})")
                data = r.raw.read(MAX_TORRENT_BYTES + 1, decode_content=True)
                if len(data) > MAX_TORRENT_BYTES:
                    raise IOError("ملف التورنت كبير بشكل غير معتاد")
                with open(self.torrent_file, "wb") as f:
                    f.write(data)
            else:
                local = _local_path(src)
                if not os.path.isfile(local):
                    raise IOError("ملف التورنت غير موجود: " + local)
                shutil.copyfile(local, self.torrent_file)
        try:
            ti = lt.torrent_info(self.torrent_file)
        except Exception as e:  # noqa: BLE001
            try:
                os.remove(self.torrent_file)
            except OSError:
                pass
            raise IOError("ملف التورنت تالف أو ليس تورنت صالحًا") from e
        p = lt.add_torrent_params()
        p.ti = ti
        p.save_path = self.dest_dir
        return p

    def _save_metadata(self, h):
        """يحفظ بيانات تورنت magnet بعد جلبها ليُستأنف لاحقًا دون إنترنت."""
        try:
            ti = h.torrent_file()
            if ti is None:
                return
            data = lt.bencode(lt.create_torrent(ti).generate())
            with open(self.torrent_file, "wb") as f:
                f.write(data)
        except Exception:  # noqa: BLE001
            pass

    def _run(self):
        try:
            ses = session()
            os.makedirs(self.dest_dir, exist_ok=True)
            params = self._params()
            params.save_path = self.dest_dir
            self._handle = h = ses.add_torrent(params)
            for peer in self.test_peers:
                h.connect_peer(peer)
            meta_saved = os.path.isfile(self.torrent_file)
            started = time.monotonic()
            while not self._stop.is_set():
                st = h.status()
                if self._stop.is_set():
                    break
                self.peers, self.seeds = st.num_peers, st.num_seeds
                if st.errc.value() != 0:
                    raise IOError(st.errc.message())
                if not st.has_metadata:
                    self.state = PROBING
                    self.stage = "جارٍ جلب بيانات التورنت من الشبكة…"
                    self.speed = 0.0
                    if time.monotonic() - started > 600:
                        raise IOError("تعذّر جلب بيانات التورنت: لا أقران متاحون (جرّب لاحقًا)")
                else:
                    if not meta_saved:
                        self._save_metadata(h)
                        meta_saved = True
                    ti = h.torrent_file()
                    if ti is not None:
                        self.filename = sanitize_filename(ti.name())
                        self.size = ti.total_size()
                    if st.state in (lt.torrent_status.checking_files,
                                    lt.torrent_status.checking_resume_data):
                        self.state, self.stage = DOWNLOADING, "جارٍ فحص الملفات الموجودة…"
                    elif st.state == lt.torrent_status.downloading_metadata:
                        self.state, self.stage = PROBING, "جارٍ جلب بيانات التورنت…"
                    else:
                        self.state, self.stage = DOWNLOADING, ""
                    self._done = int(st.total_wanted_done)
                    self._frac = float(st.progress)
                    self.speed = float(st.download_payload_rate)
                    left = int(st.total_wanted) - self._done
                    self._eta = left / self.speed if self.speed > 1 and left > 0 else None
                    if st.is_finished or st.state in (lt.torrent_status.seeding,
                                                      lt.torrent_status.finished):
                        self._done = int(st.total_wanted)
                        break
                time.sleep(0.4)
            if self._stop.is_set():
                return
            self._finish()
        except Exception as e:  # noqa: BLE001
            if not self._stop.is_set():
                self.state = ERROR
                self.error = friendly_error(e)
        finally:
            self.speed = 0.0
            self._drop()                # لا نبقى نرفع بعد الاكتمال أو الإيقاف؛ الملفات تبقى

    def _finish(self):
        self._frac = 1.0
        self.speed = 0.0
        self._eta = None
        self.stage = ""
        self.state = COMPLETED
