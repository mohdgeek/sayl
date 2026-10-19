"""مدير التحميلات: الطابور، الحد الأقصى للتحميلات المتزامنة، وحفظ القائمة."""
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
from .media import MediaDownload, available as media_available, is_media_url
from .schedule import in_window

WATCH_EVERY = 5.0


class DuplicateError(Exception):
    """الرابط أو الملف محمَّل سابقًا (أو قيد التحميل)."""

    def __init__(self, existing):
        super().__init__("duplicate")
        self.existing = existing


def norm_url(url):
    try:
        p = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return url.strip()
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.query, ""))

HISTORY_LEN = 90


class DownloadManager:
    def __init__(self, config=None, state_path=None, autostart=True, now_fn=None):
        self.config = config or Config()
        self.now_fn = now_fn or dt.datetime.now
        self.events = deque(maxlen=50)
        self._last_watch = 0.0
        self.state_path = state_path or os.path.join(APP_DIR, "downloads.json")
        self.limiter = RateLimiter(self.config["speed_limit_kb"] * 1024)
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
            force=True, subs=None, subfolder=None, note=""):
        url = url.strip()
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("الرابط يجب أن يبدأ بـ http:// أو https://")
        if not allow_duplicate and self.config["warn_duplicates"]:
            dup = self.find_duplicate(url, filename)
            if dup:
                raise DuplicateError(dup)
        if kind == "auto":
            kind = "media" if is_media_url(url) else "http"
        dest = dest_dir or self.config["download_dir"]
        if kind == "media":
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
                raise ValueError("روابط مجلدات MEGA غير مدعومة بعد؛ افتح كل ملف على حدة وانسخ رابطه")
            if mega.is_mega(url) and not mega.parse(url):
                raise ValueError("رابط MEGA ناقص: انسخه كاملًا مع الجزء الذي بعد علامة #")
            d = make_download(url, dest, headers=headers, connections=self.config["connections"],
                              limiter=self.limiter, filename=filename)
        d.subfolder = bool(self.config["subfolders"]) if subfolder is None else bool(subfolder)
        d.note = note or ""
        d.force = bool(force)
        if not start:
            d.state = PAUSED
        with self._lock:
            self.items[d.id] = d
            self._order.insert(0, d.id)       # الأحدث أولًا
        self.save()
        return d

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

    def _tick(self):
        """خطوة مجدولة واحدة (متزامنة لتسهل الاختبار)."""
        now = self.now_fn()
        is_open = self.schedule_open(now)
        self.limiter.set_rate(self.effective_limit_kb(now) * 1024)
        with self._lock:
            if not is_open:     # أغلقت النافذة: أوقف ما لم يُطلب يدويًا وأعده للطابور
                for d in self.items.values():
                    if d.active and not d.force:
                        d.pause()
                        d.state = QUEUED
            running = sum(1 for d in self.items.values() if d.state in (PROBING, DOWNLOADING))
            limit = max(1, int(self.config["max_concurrent"]))
            for i in reversed(self._order):       # الأقدم أولًا
                d = self.items.get(i)
                if running >= limit:
                    break
                if d and d.state == QUEUED and not d.active and (is_open or d.force):
                    d.start()
                    running += 1

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
                data = [self.items[i].to_dict() for i in self._order if i in self.items]
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self.state_path)
            try:
                os.chmod(self.state_path, 0o600)   # قد تحتوي الترويسات على كوكيز
            except OSError:
                pass

    def _load(self):
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        for e in data:
            st = e.get("state", PAUSED)
            state = COMPLETED if st == COMPLETED else PAUSED
            if e.get("kind") == "media":
                d = MediaDownload(e["url"], e["dest_dir"], headers=e.get("headers"),
                                  limiter=self.limiter, height=e.get("height"),
                                  audio_only=e.get("audio_only", False),
                                  ffmpeg_path=self.config["ffmpeg_path"],
                                  filename=e.get("filename"), id=e["id"], subs=e.get("subs"))
                d.size = e.get("size")
            else:
                d = make_download(e["url"], e["dest_dir"], headers=e.get("headers"),
                                  connections=e.get("connections", 8), limiter=self.limiter,
                                  filename=e.get("filename"), id=e["id"])
                d.size, d.resumable, d.etag = (e.get("size"), e.get("resumable", False),
                                               e.get("etag"))
            d.subfolder = e.get("subfolder", False)
            d.note = e.get("note", "")
            d.final_dir = e.get("final_dir")
            d.created = e.get("created", d.created)
            d.state = state
            self.items[d.id] = d
            self._order.append(d.id)
