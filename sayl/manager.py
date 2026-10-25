"""مدير التحميلات: الطابور، الحد الأقصى للتحميلات المتزامنة، وحفظ القائمة."""
import base64
import json
import os
import threading
import time
from collections import deque

import datetime as dt
import re
import urllib.parse

from .config import APP_DIR, Config
from .engine import (CANCELED, COMPLETED, DOWNLOADING, ERROR, PAUSED, PROBING,
                     QUEUED, Download, RateLimiter, make_download)
from . import checksums, dupes, engine, rules, torrent
from .categories import LABELS, category_of
from .torrent import is_magnet
from .media import MediaDownload, available as media_available, is_media_url
from .schedule import in_window

WATCH_EVERY = 5.0


class DuplicateError(Exception):
    """الرابط أو الملف محمَّل سابقًا (أو قيد التحميل)."""

    def __init__(self, existing):
        super().__init__("duplicate")
        self.existing = existing


def norm_url(url):
    if torrent.is_magnet(url):
        return "magnet:" + (torrent.magnet_hash(url) or url.strip().lower())
    try:
        p = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return url.strip()
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.query, ""))

HISTORY_LEN = 90
RETRY_DELAYS = (5, 15, 30, 60, 120, 300)     # ثوانٍ بين محاولات الاستئناف التلقائي
MAX_AUTO_RETRIES = 30


def is_online(timeout=2.0):
    """فحص سريع لعودة الاتصال بالإنترنت (اتصال TCP بخادم DNS عام)."""
    import socket
    for host in ("1.1.1.1", "8.8.8.8"):
        try:
            socket.create_connection((host, 443), timeout=timeout).close()
            return True
        except OSError:
            continue
    return False


class Stats:
    """إحصاءات المكتمل: بايتات يومية وحسب الموقع، تُحفظ في stats.json."""

    def __init__(self, path):
        self.path = path
        self.data = {"days": {}, "hosts": {}, "total": 0, "files": 0}
        self._lock = threading.Lock()
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.data.update({k: d[k] for k in self.data if k in d})
        except (OSError, ValueError):
            pass

    def add(self, nbytes, host="", day=None):
        day = day or dt.date.today().isoformat()
        with self._lock:
            d = self.data
            d["days"][day] = d["days"].get(day, 0) + int(nbytes)
            if host:
                d["hosts"][host] = d["hosts"].get(host, 0) + int(nbytes)
            d["total"] += int(nbytes)
            d["files"] += 1
            if len(d["days"]) > 400:                   # نحتفظ بآخر ~13 شهرًا
                for k in sorted(d["days"])[:-400]:
                    d["days"].pop(k, None)
            if len(d["hosts"]) > 300:
                for k, _ in sorted(d["hosts"].items(), key=lambda kv: kv[1])[:-300]:
                    d["hosts"].pop(k, None)
        self.save()

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with self._lock, open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def summary(self, today=None):
        today = today or dt.date.today()
        with self._lock:
            days = dict(self.data["days"])
            hosts = dict(self.data["hosts"])
            total, files = self.data["total"], self.data["files"]
        last = [(today - dt.timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
        week = sum(days.get((today - dt.timedelta(days=i)).isoformat(), 0) for i in range(7))
        month = sum(days.get((today - dt.timedelta(days=i)).isoformat(), 0) for i in range(30))
        return {"today": days.get(today.isoformat(), 0), "week": week, "month": month,
                "total": total, "files": files,
                "series": [(d, days.get(d, 0)) for d in last],
                "hosts": sorted(hosts.items(), key=lambda kv: kv[1], reverse=True)[:8]}
_HASH_ALGOS = {32: "md5", 40: "sha1", 64: "sha256", 128: "sha512"}


def normalize_hash(text):
    """يقبل 'sha256:abc…' أو البصمة وحدها؛ يعيد '' إن لم تكن بصمة صالحة."""
    t = str(text or "").strip().lower()
    if ":" in t:
        t = t.split(":", 1)[1].strip()
    t = t.replace(" ", "")
    return t if len(t) in _HASH_ALGOS and re.fullmatch(r"[0-9a-f]+", t) else ""


def hash_file(path, algo, should_stop=lambda: False):
    import hashlib
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            if should_stop():
                return None
            h.update(chunk)
    return h.hexdigest()


class DownloadManager:
    def __init__(self, config=None, state_path=None, autostart=True, now_fn=None):
        self.config = config or Config()
        self.now_fn = now_fn or dt.datetime.now
        self.events = deque(maxlen=50)
        self._last_watch = 0.0
        self.state_path = state_path or os.path.join(APP_DIR, "downloads.json")
        self.limiter = RateLimiter(self.config["speed_limit_kb"] * 1024)
        self.stats = Stats(os.path.join(os.path.dirname(self.state_path), "stats.json"))
        self._online_at, self._online = 0.0, True
        self.items = {}
        self._order = []
        self._lock = threading.RLock()
        self._save_lock = threading.Lock()
        self._stop = threading.Event()
        self.history = deque([0.0] * HISTORY_LEN, maxlen=HISTORY_LEN)   # بايت/ث كل ثانية
        self._load()
        if autostart:
            threading.Thread(target=self._scheduler, daemon=True, name="scheduler").start()

    # ------------------------------------------------------------ العمليات
    def add(self, url, headers=None, dest_dir=None, filename=None, start=True,
            kind="auto", height=None, audio_only=False, allow_duplicate=False,
            force=True, subs=None, subfolder=None, note="", skip=None, expected_hash="", start_at=None):
        url = url.strip().strip('"')
        is_tor = kind == "torrent" or torrent.is_torrent_url(url)
        if not is_tor and not url.lower().startswith(("http://", "https://")):
            raise ValueError("الرابط يجب أن يبدأ بـ http:// أو https:// أو magnet:")
        if not allow_duplicate and self.config["warn_duplicates"]:
            dup = self.find_duplicate(url, filename)
            if dup:
                raise DuplicateError(dup)
        if is_tor:
            kind = "torrent"
        elif kind == "auto":
            kind = "media" if is_media_url(url) else "http"
        dest = (dest_dir or rules.folder_for(self.config, url, filename, category_of(filename or "", kind))
                or self.config["download_dir"])
        if kind == "torrent" and not dest_dir and dest == self.config["download_dir"] and self.config["subfolders"]:
            dest = os.path.join(dest, LABELS["torrent"])       # الترتيب الافتراضي: مجلد «تورنت»
        if kind == "torrent":
            if not torrent.available():
                raise ValueError("دعم التورنت يحتاج الحزمة libtorrent (pip install libtorrent)")
            if is_magnet(url) and not torrent.magnet_hash(url):
                raise ValueError("رابط magnet غير صالح: ينقصه معرّف التورنت (xt=urn:btih:…)")
            d = torrent.TorrentDownload(url, dest, headers=headers, limiter=self.limiter,
                                        filename=filename)
            d.skip = sorted({int(i) for i in (skip or [])})
        elif kind == "media":
            if not media_available():
                raise ValueError("ميزة الفيديو تحتاج حزمة yt-dlp (pip install yt-dlp)")
            if height is None and not audio_only and self.config["video_quality"]:
                height = int(self.config["video_quality"])
            if subs is None and self.config["subtitles"]:
                subs = [x.strip() for x in str(self.config["sub_langs"]).split(",") if x.strip()]
            d = MediaDownload(url, dest, headers=headers, limiter=self.limiter, height=height,
                              audio_only=audio_only, ffmpeg_path=self.config["ffmpeg_path"],
                              subs=subs)
        else:
            from . import mega
            if mega.is_folder(url):
                return self._add_mega_folder(url, dest, headers, start, force, note)
            if mega.is_mega(url) and not mega.parse(url):
                raise ValueError("رابط MEGA ناقص: انسخه كاملًا مع الجزء الذي بعد علامة #")
            d = make_download(url, dest, headers=headers, connections=self.config["connections"],
                              limiter=self.limiter, filename=filename)
        d.subfolder = bool(self.config["subfolders"]) if subfolder is None else bool(subfolder)
        d.note = note or ""
        d.force = bool(force)
        d.expected_hash = normalize_hash(expected_hash)
        d.hash_state = None
        d.start_at = float(start_at) if start_at else None
        if d.start_at:
            d.force = False
        if not start and not d.start_at:
            d.state = PAUSED
        with self._lock:
            self.items[d.id] = d
            self._order.insert(0, d.id)       # الأحدث أولًا
        self.save()
        return d

    def _add_mega_folder(self, url, dest, headers, start, force, note):
        """مجلد MEGA عام: يُعدَّد محتواه وتُضاف كل ملفاته مع الحفاظ على هيكل المجلدات الفرعية."""
        from . import mega
        try:
            info = mega.list_folder(url)
        except (IOError, ValueError):
            raise
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"تعذّر قراءة مجلد MEGA: {e}") from e
        parsed = mega.parse_folder(url)
        fkey_b64 = base64.urlsafe_b64encode(parsed[1]).decode().rstrip("=")
        first = None
        with self._lock:
            order = []
            for f in info["files"]:
                sub = os.path.dirname(f["path"])
                d = make_download(mega.folder_file_url(info["fid"], fkey_b64, f["h"]),
                                  os.path.join(dest, *sub.split("/")) if sub else dest,
                                  headers=None, connections=1, limiter=self.limiter, filename=f["name"],
                                  node={"fid": info["fid"], "h": f["h"], "key": f["key"].hex()})
                d.size = f["size"] or None
                d.subfolder = False
                d.note = note or ""
                d.force = bool(force)
                if not start:
                    d.state = PAUSED
                self.items[d.id] = d
                order.append(d.id)
                first = first or d
            self._order[0:0] = list(reversed(order))
        self.events.append(f"مجلد MEGA «{info['name']}»: أُضيف {len(info['files'])} ملف"
                           + (" (اقتُصر على أول 2000)" if info.get("truncated") else ""))
        self.save()
        return first

    def find_duplicate(self, url, filename=None):
        key = norm_url(url)
        with self._lock:
            for d in self.items.values():
                if norm_url(d.url) == key and d.state != CANCELED:
                    return d
                if (filename and d.state == COMPLETED and d.filename == filename):
                    return d
        return None

    def pop_events(self):
        out = []
        while self.events:
            out.append(self.events.popleft())
        return out

    def pause(self, id):
        d = self.items.get(id)
        if d:
            d.pause()
            self.save()

    def resume(self, id):
        d = self.items.get(id)
        if d and d.state in (PAUSED, ERROR, CANCELED):
            d.force = True          # الاستئناف اليدوي يتجاوز نافذة الجدولة
            d.retries, d.retry_at = 0, None
            d.state = QUEUED
            self.save()

    def remove(self, id, delete_files=False):
        with self._lock:
            d = self.items.pop(id, None)
            if id in self._order:
                self._order.remove(id)
        if d:
            if d.state != COMPLETED:
                d.cancel(delete_files=True)
            elif delete_files:
                try:
                    os.remove(os.path.join(d.folder, d.filename))
                except OSError:
                    pass
        self.save()

    def pause_all(self):
        for id in list(self.items):
            self.pause(id)

    def resume_all(self):
        for id in list(self.items):
            self.resume(id)

    def clear_completed(self):
        for id in [i for i, d in self.items.items() if d.state == COMPLETED]:
            self.remove(id)

    def apply_settings(self):
        self.limiter.set_rate(self.effective_limit_kb() * 1024)
        for d in self.items.values():
            if d.kind == "http":
                d.connections = self.config["connections"]
        self.config.save()

    def snapshot(self):
        with self._lock:
            return [self.items[i] for i in self._order if i in self.items]

    def total_speed(self):
        return sum(d.speed for d in self.items.values() if d.state == DOWNLOADING)

    # ------------------------------------------------------------ المجدول
    def schedule_open(self, now=None):
        c = self.config
        if not c["schedule_enabled"]:
            return True
        return in_window(now or self.now_fn(), c["sched_start"], c["sched_stop"])

    def effective_limit_kb(self, now=None):
        c = self.config
        if c["peak_enabled"] and in_window(now or self.now_fn(), c["peak_start"], c["peak_stop"]):
            return int(c["peak_kb"])
        return int(c["speed_limit_kb"])

    def _track_day(self):
        """يجمع بايتات اليوم الحالي (فروق التنزيل بين نبضتين) لحدّ الاستهلاك اليومي."""
        today = dt.date.today().isoformat()
        if getattr(self, "_day", None) != today:
            self._day, self.day_bytes, self._seen = today, 0, {}
        seen = getattr(self, "_seen", {})
        for d in list(self.items.values()):
            try:
                cur = int(d.downloaded)
            except Exception:  # noqa: BLE001
                continue
            prev = seen.get(d.id)
            if prev is not None and cur > prev:
                self.day_bytes += cur - prev
            seen[d.id] = cur
        self._seen = seen

    def capped(self):
        cap = float(self.config["daily_cap_gb"] or 0)
        return cap > 0 and getattr(self, "day_bytes", 0) >= cap * 1024 ** 3

    def _tick(self):
        """خطوة مجدولة واحدة (متزامنة لتسهل الاختبار)."""
        now = self.now_fn()
        self._track_day()
        is_open = self.schedule_open(now) and not self.capped()
        self.limiter.set_rate(self.effective_limit_kb(now) * 1024)
        torrent.apply_limit(self.limiter.rate)
        engine.CLEAN_NAMES = bool(self.config["clean_names"])
        self._verify_pending()
        self._count_completed()
        self._auto_retry()
        self._auto_checksum()
        self._check_dupes()
        with self._lock:
            if not is_open:     # أغلقت النافذة: أوقف ما لم يُطلب يدويًا وأعده للطابور
                capped = self.capped()
                for d in self.items.values():
                    if d.active and (capped or not d.force):
                        d.pause()
                        d.state = QUEUED
            running = sum(1 for d in self.items.values() if d.state in (PROBING, DOWNLOADING))
            limit = max(1, int(self.config["max_concurrent"]))
            for i in reversed(self._order):       # الأقدم أولًا
                d = self.items.get(i)
                if running >= limit:
                    break
                if d and d.state == QUEUED and not d.active:
                    if d.start_at:
                        if time.time() < d.start_at:
                            continue            # مجدول لوقت لاحق: لا يستهلك مكانًا في الطابور
                        d.start_at = None
                        d.force = True          # حان وقته: يبدأ حتى خارج نافذة الجدولة العامة
                    elif not (is_open or (d.force and not self.capped())):
                        continue
                    d.start()
                    running += 1

    def _count_completed(self):
        for d in list(self.items.values()):
            if d.state == COMPLETED and not getattr(d, "_counted", False):
                d._counted = True
                try:
                    host = urllib.parse.urlsplit(d.url).hostname or ("torrent" if d.kind == "torrent" else "")
                except ValueError:
                    host = ""
                if d.kind == "torrent":
                    host = "BitTorrent"
                nbytes = d.size or d.downloaded or 0
                if nbytes:
                    self.stats.add(nbytes, host)

    def _check_online(self):
        now = time.monotonic()
        if now - self._online_at > 4:
            self._online, self._online_at = is_online(), now
        return self._online

    def _auto_retry(self):
        """يعيد تشغيل التحميلات التي فشلت بسبب الشبكة، بتأخير متزايد وبعد التأكد من عودة الاتصال."""
        if not self.config["auto_retry"]:
            return
        now = time.time()
        for d in list(self.items.values()):
            if d.state != ERROR or not getattr(d, "net_error", False) or d.active:
                if d.state == COMPLETED:
                    d.retries = 0
                continue
            n = getattr(d, "retries", 0)
            if n >= MAX_AUTO_RETRIES:
                d.net_error = False
                continue
            if getattr(d, "retry_at", None) is None:
                d.retry_at = now + RETRY_DELAYS[min(n, len(RETRY_DELAYS) - 1)]
                continue
            if now < d.retry_at:
                continue
            if not self._check_online():            # لا نستهلك محاولة والشبكة غير متاحة
                d.retry_at = now + 5
                continue
            d.retries, d.retry_at = n + 1, None
            d.force = True
            d.state = QUEUED
            self.events.append(f"إعادة محاولة تلقائية ({n + 1}): {d.filename or d.url[:50]}")

    def schedule(self, id, when):
        """يجدول بدء عنصر في وقت محدد (طابع زمني). when=None يلغي الجدولة ويتركه متوقفًا."""
        d = self.items.get(id)
        if not d or d.state == COMPLETED:
            raise ValueError("لا يمكن جدولة هذا العنصر")
        if when is not None and when <= time.time():
            raise ValueError("اختر وقتًا في المستقبل")
        if d.active:
            d.pause()
            d.wait(10)
        if when is None:
            d.start_at, d.state = None, PAUSED
        else:
            d.start_at, d.state, d.force = float(when), QUEUED, False
        self.save()
        return d

    def refresh_url(self, id, new_url, headers=None):
        """يستبدل رابط تحميل منتهٍ/متوقف برابط جديد للملف نفسه ويكمل من حيث توقف (إن تطابق الحجم)."""
        d = self.items.get(id)
        new_url = (new_url or "").strip()
        if not d or d.kind != "http" or d.state == COMPLETED:
            raise ValueError("لا يمكن تحديث رابط هذا العنصر")
        if not new_url.lower().startswith(("http://", "https://")):
            raise ValueError("الرابط الجديد يجب أن يبدأ بـ http:// أو https://")
        d.pause()
        d.wait(15)
        d.url = d.final_url = new_url
        if headers:
            d.headers = dict(headers)
        d.size = None                 # يُعاد فحص الخادم؛ يستأنف فقط إن طابق الحجم والـ ETag
        d.error, d.net_error, d.retries, d.retry_at = "", False, 0, None
        d.force = True
        d.state = QUEUED
        self.save()
        return d

    def find_stale(self, filename):
        """تحميل متوقف/فاشل بنفس اسم الملف وفيه جزء منزَّل (لتحديث رابطه بدل البدء من الصفر)."""
        if not filename:
            return None
        with self._lock:
            for d in self.items.values():
                if (d.kind == "http" and d.filename == filename and d.state in (ERROR, PAUSED)
                        and not d.active and d.downloaded > 0):
                    return d
        return None

    def _verify_pending(self):
        """يتحقق من بصمة الملفات المكتملة التي طُلب التحقق منها (في خيط خلفي، دون تجميد الواجهة)."""
        for d in list(self.items.values()):
            exp = getattr(d, "expected_hash", "")
            if exp and d.state == COMPLETED and getattr(d, "hash_state", None) is None:
                d.hash_state = "checking"
                threading.Thread(target=self._verify_one, args=(d, exp), daemon=True).start()

    def _check_dupes(self):
        """عند معرفة اسم الملف وحجمه (أول التحميل) نبحث عن نسخة منه على القرص وننبّه المستخدم."""
        for d in list(self.items.values()):
            if (d.state in (DOWNLOADING, COMPLETED) and d.size and d.filename and d.kind in ("http", "media")
                    and not getattr(d, "_dup_checked", False)):
                d._dup_checked = True
                threading.Thread(target=self._find_dup, args=(d,), daemon=True, name="dup-" + d.id).start()

    def _find_dup(self, d):
        try:
            mine = os.path.join(d.folder, d.filename)
            hits = dupes.find(self.config, d.filename, d.size, [d.folder], skip=mine)
            hits += [(it.folder + os.sep + it.filename, "same") for it in list(self.items.values())
                     if it is not d and it.state == COMPLETED and it.size == d.size and it.filename
                     and it.filename.lower() == d.filename.lower()
                     and os.path.isfile(os.path.join(it.folder, it.filename))
                     and os.path.join(it.folder, it.filename) != mine]
        except Exception:  # noqa: BLE001
            return
        if hits:
            d.dup_of, d.dup_kind = hits[0]
            self.events.append(("⚠ لديك هذا الملف مسبقًا: " if d.dup_kind == "same"
                                else "ℹ يوجد ملف بنفس الحجم (ربما هو نفسه): ") + d.dup_of)

    def _auto_checksum(self):
        """للتحميلات العادية المكتملة بلا بصمة: نبحث عن البصمة الرسمية تلقائيًا ثم تتحقق _verify_pending."""
        if not self.config["auto_checksum"]:
            return
        for d in list(self.items.values()):
            if (d.state == COMPLETED and d.kind == "http" and not getattr(d, "expected_hash", "")
                    and not getattr(d, "_cs_tried", False) and d.filename
                    and "mega.nz" not in d.url and d.url.lower().startswith(("http://", "https://"))):
                d._cs_tried = True
                threading.Thread(target=self._find_checksum, args=(d,), daemon=True, name="cs-" + d.id).start()

    def _find_checksum(self, d):
        try:
            h = normalize_hash(checksums.find(d.url, d.filename))
        except Exception:  # noqa: BLE001
            h = ""
        if h and not getattr(d, "expected_hash", "") and d.id in self.items:
            d.expected_hash, d.hash_auto, d.hash_state = h, True, None
            self.events.append(f"عُثر على بصمة رسمية لـ «{d.filename}» وسيتم التحقق منها")
            self.save()

    def _verify_one(self, d, exp):
        path = os.path.join(d.folder, d.filename or "")
        try:
            if not os.path.isfile(path):
                d.hash_state = "skip"
                return
            digest = hash_file(path, _HASH_ALGOS[len(exp)], lambda: self._stop.is_set())
            if digest is None:
                d.hash_state = None
                return
            d.hash_actual = digest
            d.hash_state = "ok" if digest == exp else "bad"
            self.events.append(("✓ بصمة الملف صحيحة: " if d.hash_state == "ok"
                                else "⚠ بصمة الملف لا تطابق المتوقع (قد يكون الملف تالفًا أو معدَّلًا): ")
                               + (d.filename or ""))
        except OSError:
            d.hash_state = "skip"
        self.save()

    def set_expected_hash(self, id, text):
        """يعيّن بصمة متوقعة لعنصر مكتمل ويُعاد التحقق. يعيد False إن لم تكن البصمة صالحة."""
        d = self.items.get(id)
        h = normalize_hash(text)
        if not d or not h:
            return False
        d.expected_hash, d.hash_state = h, None
        self.save()
        return True

    def rename(self, id, new_name):
        """يعيد تسمية ملف مكتمل على القرص ويحدّث القائمة. يرفع ValueError برسالة عربية عند الفشل."""
        d = self.items.get(id)
        if not d or d.state != COMPLETED:
            raise ValueError("يمكن إعادة تسمية الملفات المكتملة فقط")
        new = engine.sanitize_filename(new_name)
        if not new or new == d.filename:
            return
        src = os.path.join(d.folder, d.filename)
        dst = os.path.join(d.folder, new)
        if not os.path.exists(src):
            raise ValueError("الملف غير موجود في مكانه")
        if os.path.exists(dst):
            raise ValueError("يوجد ملف بهذا الاسم بالفعل")
        try:
            os.rename(src, dst)
        except OSError as e:
            raise ValueError(f"تعذّرت إعادة التسمية: {e.strerror or e}") from e
        d.filename = new
        self.save()

    def _scheduler(self):
        last_save = last_hist = 0.0
        while not self._stop.is_set():
            try:   # لا يجوز أن يموت المجدول بسبب خطأ عابر
                self._tick()
                now = time.time()
                if now - last_hist >= 1.0:
                    self.history.append(self.total_speed())
                    last_hist = now
                if now - self._last_watch >= WATCH_EVERY:
                    self._last_watch = now
                    self._watch_tick()
                if now - last_save > 3:
                    self.save()
                    last_save = now
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.4)

    def _watch_tick(self):
        c = self.config
        folder = c["watch_folder"]
        if not c["watch_enabled"] or not folder or not os.path.isdir(folder):
            return 0
        added = 0
        for name in sorted(os.listdir(folder)):
            if not name.lower().endswith((".txt", ".url")):
                continue
            path = os.path.join(folder, name)
            try:
                if not os.path.isfile(path) or os.path.getsize(path) > 1_000_000:
                    continue
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except OSError:
                continue
            urls = re.findall(r"https?://[^\s\"'<>]+", text)[:500]
            n = 0
            for u in dict.fromkeys(urls):
                try:
                    self.add(u)
                    n += 1
                except (DuplicateError, ValueError):
                    pass
            try:
                os.replace(path, path + ".done")
            except OSError:
                continue
            added += n
            if n:
                self.events.append(f"المجلد المراقَب: أُضيف {n} رابط من {name}")
        return added

    def shutdown(self):
        self._stop.set()
        for d in list(self.items.values()):
            if d.active:
                d.pause()
        deadline = time.monotonic() + 4          # نمهل الأجزاء ثوانيَ لتحفظ موضع الاستئناف
        for d in list(self.items.values()):
            d.wait(max(0.0, deadline - time.monotonic()))
        self.save()

    # ------------------------------------------------------------ الحفظ
    def save(self):
        # يُستدعى من خيوط متعددة (المجدول، الواجهة، الخادم)، لذا تُسلسَل الكتابة كلها
        with self._save_lock:
            with self._lock:
                data = []
                for i in self._order:
                    if i in self.items:
                        it = self.items[i]
                        e = it.to_dict()
                        e["hash"] = getattr(it, "expected_hash", "")
                        e["hash_auto"] = bool(getattr(it, "hash_auto", False))
                        e["start_at"] = getattr(it, "start_at", None)
                        e["hash_state"] = getattr(it, "hash_state", None) if getattr(it, "hash_state", None) in ("ok", "bad") else None
                        data.append(e)
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self.state_path)
            try:
                os.chmod(self.state_path, 0o600)   # قد تحتوي الترويسات على كوكيز
            except OSError:
                pass

    def _load(self, path=None):
        try:
            with open(path or self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return 0
        added = 0
        for e in data:
            if e.get("id") in self.items:
                continue
            added += 1
            st = e.get("state", PAUSED)
            state = COMPLETED if st == COMPLETED else PAUSED
            if e.get("kind") == "torrent":
                d = torrent.TorrentDownload(e["url"], e["dest_dir"], limiter=self.limiter,
                                            filename=e.get("filename"), id=e["id"])
                d.size = e.get("size")
                d.skip = list(e.get("skip") or [])
            elif e.get("kind") == "media":
                d = MediaDownload(e["url"], e["dest_dir"], headers=e.get("headers"),
                                  limiter=self.limiter, height=e.get("height"),
                                  audio_only=e.get("audio_only", False),
                                  ffmpeg_path=self.config["ffmpeg_path"],
                                  filename=e.get("filename"), id=e["id"], subs=e.get("subs"))
                d.size = e.get("size")
            else:
                d = make_download(e["url"], e["dest_dir"], headers=e.get("headers"),
                                  connections=e.get("connections", 8), limiter=self.limiter,
                                  filename=e.get("filename"), id=e["id"], node=e.get("node"))
                d.size, d.resumable, d.etag = (e.get("size"), e.get("resumable", False),
                                               e.get("etag"))
            d._counted = state == COMPLETED          # لا نعيد عدّ ما اكتمل في جلسات سابقة
            d.expected_hash = e.get("hash", "")
            d.start_at = e.get("start_at")
            d.hash_state = e.get("hash_state")
            d.hash_auto = bool(e.get("hash_auto"))
            d._dup_checked = state == COMPLETED
            d._cs_tried = state == COMPLETED          # لا نبحث عن بصمات لما اكتمل في جلسات سابقة
            d.subfolder = e.get("subfolder", False)
            d.note = e.get("note", "")
            d.final_dir = e.get("final_dir")
            d.created = e.get("created", d.created)
            d.state = state
            if state != COMPLETED and d.start_at and d.start_at > time.time() - 86400:
                d.state, d.force = QUEUED, False      # الجدولة تبقى بعد إعادة التشغيل
            self.items[d.id] = d
            self._order.append(d.id)
        return added
