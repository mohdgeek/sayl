"""تحميل الفيديو والصوت عبر yt-dlp (يوتيوب وغيره، وروابط HLS/DASH).

لا يدعم المحتوى المحمي بـ DRM، ولا يتجاوز أي حماية.
"""
import os
import shutil
import threading
import time
import urllib.parse
import uuid

from .categories import LABELS
from .engine import (CANCELED, COMPLETED, DOWNLOADING, ERROR, PAUSED, PROBING,
                     QUEUED, RateLimiter, sanitize_filename)

try:
    import yt_dlp
    from yt_dlp.utils import DownloadCancelled
except ImportError:  # يعمل البرنامج بدون الفيديو إن لم تُثبَّت الحزمة
    yt_dlp = None
    DownloadCancelled = Exception

MEDIA_HOSTS = (
    "youtube.com", "youtu.be", "vimeo.com", "dailymotion.com", "twitter.com", "x.com",
    "facebook.com", "fb.watch", "instagram.com", "tiktok.com", "twitch.tv",
    "soundcloud.com", "reddit.com", "bilibili.com", "ok.ru", "streamable.com",
)
MANIFEST_EXT = (".m3u8", ".mpd")


def available():
    return yt_dlp is not None


def is_media_url(url):
    """هل الرابط صفحة فيديو معروفة أو ملف قائمة بث (m3u8/mpd)؟"""
    try:
        p = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if p.path.lower().endswith(MANIFEST_EXT):
        return True
    host = (p.hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in MEDIA_HOSTS)


def find_ffmpeg(configured=""):
    if configured and os.path.isfile(configured):
        return configured
    return shutil.which("ffmpeg")


class _Log:
    def __init__(self):
        self.last_error = ""

    def debug(self, m):
        pass

    def info(self, m):
        pass

    def warning(self, m):
        pass

    def error(self, m):
        self.last_error = str(m).replace("ERROR: ", "")[:300]


def looks_like_playlist(url):
    """هل الرابط قائمة تشغيل أو قناة (لا فيديو واحد)؟"""
    try:
        p = urllib.parse.urlparse(url)
    except ValueError:
        return False
    q = urllib.parse.parse_qs(p.query)
    host = (p.hostname or "").lower()
    path = p.path.lower()
    if host.endswith(("youtube.com", "youtu.be")):
        return path.startswith("/playlist") or ("list" in q and "v" not in q) \
            or path.startswith(("/@", "/channel/", "/c/", "/user/"))
    return "/playlist" in path or "/sets/" in path or "/playlists/" in path


def _entry_url(e):
    u = e.get("url") or e.get("webpage_url")
    if u and str(u).startswith("http"):
        return u
    if e.get("id") and str(e.get("ie_key") or "").lower().startswith("youtube"):
        return f"https://www.youtube.com/watch?v={e['id']}"
    return None


def probe(url, headers=None, max_entries=300):
    """يفحص الرابط دون تحميل: فيديو واحد (العنوان والمدة والجودات) أو قائمة تشغيل (العناصر)."""
    if yt_dlp is None:
        raise RuntimeError("حزمة yt-dlp غير مثبّتة")
    log = _Log()
    pl = looks_like_playlist(url)
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": not pl,
            "extract_flat": "in_playlist", "socket_timeout": 15, "logger": log,
            "http_headers": dict(headers or {})}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(log.last_error or str(e)[:300]) from e
    if info.get("_type") == "playlist":
        entries = []
        for e in info.get("entries") or []:
            if not e:
                continue
            u = _entry_url(e)
            if u:
                entries.append({"url": u, "title": e.get("title") or u, "duration": e.get("duration")})
            if len(entries) >= max_entries:
                break
        if not entries:
            raise RuntimeError("القائمة فارغة أو غير مدعومة")
        return {"playlist": True, "title": info.get("title") or "", "uploader": info.get("uploader") or "",
                "entries": entries}
    heights = sorted({f["height"] for f in info.get("formats", [])
                      if f.get("height") and f.get("vcodec") not in (None, "none")}, reverse=True)
    return {"playlist": False, "title": info.get("title") or "", "duration": info.get("duration"),
            "uploader": info.get("uploader") or "", "heights": heights,
            "is_live": bool(info.get("is_live")), "thumbnail": info.get("thumbnail")}


def build_format(height=None, audio_only=False, ffmpeg=False):
    if audio_only:
        return "bestaudio/best"
    if ffmpeg:
        if height:
            return f"bv*[height<={height}]+ba/b[height<={height}]/b"
        return "bv*+ba/b"
    # بدون ffmpeg لا يمكن دمج الصوت والصورة، فنختار ملفًا واحدًا جاهزًا
    return f"b[height<={height}]/b" if height else "b"


class MediaDownload:
    kind = "media"
    resumable = True
    connections = 1

    def __init__(self, url, dest_dir, headers=None, limiter=None, height=None,
                 audio_only=False, ffmpeg_path="", filename=None, id=None, subs=None,
                 subfolder=False):
        self.id = id or uuid.uuid4().hex[:10]
        self.url = url
        self.headers = dict(headers or {})
        self.dest_dir = dest_dir
        self.height = height
        self.audio_only = bool(audio_only)
        self.ffmpeg_path = ffmpeg_path
        self.subs = list(subs or [])         # رموز لغات الترجمة، مثل ["ar", "en"]
        self.subfolder = subfolder
        self.final_dir = None
        self.force = True
        self.limiter = limiter or RateLimiter(0)
        self.filename = filename
        self.title = ""
        self.size = None
        self.state = QUEUED
        self.error = ""
        self.speed = 0.0
        self.created = time.time()
        self._streams = 1
        self._seen = {}            # format_id -> [بايتات منزَّلة, الإجمالي]
        self._order = []
        self._frac = 0.0
        self._stop = threading.Event()
        self._thread = None
        self._eta = None
        self.etag = None
        self.segments = []

    # ---- نفس واجهة Download
    @property
    def downloaded(self):
        return sum(v[0] for v in self._seen.values())

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
        return {"id": self.id, "kind": "media", "url": self.url, "headers": self.headers,
                "dest_dir": self.dest_dir, "filename": self.filename, "size": self.size,
                "height": self.height, "audio_only": self.audio_only, "subs": self.subs,
                "subfolder": self.subfolder, "final_dir": self.final_dir,
                "state": self.state, "created": self.created}

    def start(self):
        if self.active:
            return
        self._stop.clear()
        self.error = ""
        self.state = PROBING
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"media-{self.id}")
        self._thread.start()

    def pause(self):
        if self.state == QUEUED:
            self.state = PAUSED
            return
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=20)
        if self.state not in (COMPLETED, ERROR, CANCELED):
            self.state = PAUSED
        self.speed = 0.0

    def cancel(self, delete_files=True):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=20)
        self.state = CANCELED
        if delete_files and self.title:
            base = os.path.join(self.dest_dir, sanitize_filename(self.title))
            for suffix in (".part", ".ytdl"):
                for p in _glob_partials(self.dest_dir, os.path.basename(base), suffix):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

    # ---- التنفيذ
    def _hook(self, d):
        if self._stop.is_set():
            raise DownloadCancelled()
        fid = (d.get("info_dict") or {}).get("format_id", "0")
        if fid not in self._order:
            self._order.append(fid)
        idx = self._order.index(fid)
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        done = d.get("downloaded_bytes") or 0
        self._seen[fid] = [done, total]
        if d.get("status") == "finished":
            frac = 1.0
            self.speed = 0.0
        else:
            frac = (done / total) if total else 0.0
            self.speed = float(d.get("speed") or 0.0)
            self._eta = d.get("eta")
        self._frac = (idx + frac) / max(self._streams, 1)
        if self.state == PROBING:
            self.state = DOWNLOADING
        if not self.size and self._seen:
            self.size = sum(v[1] for v in self._seen.values()) or None

    def _out_dir(self):
        if not self.subfolder:
            return self.dest_dir
        return os.path.join(self.dest_dir, LABELS["audio" if self.audio_only else "video"])

    def _build_opts(self, log):
        ff = find_ffmpeg(self.ffmpeg_path)
        out_dir = self._out_dir()
        os.makedirs(out_dir, exist_ok=True)
        opts = {
            "quiet": True, "no_warnings": True, "noplaylist": True, "logger": log,
            "outtmpl": os.path.join(out_dir, "%(title).150B [%(id).20B].%(ext)s"),
            "format": build_format(self.height, self.audio_only, bool(ff)),
            "http_headers": dict(self.headers), "progress_hooks": [self._hook],
            "socket_timeout": 20, "retries": 8, "fragment_retries": 8, "continuedl": True,
            "concurrent_fragment_downloads": 4, "restrictfilenames": False,
            "windowsfilenames": True, "overwrites": False,
        }
        if self.limiter.rate > 0:
            opts["ratelimit"] = self.limiter.rate
        pps = []
        if self.subs and not self.audio_only:
            opts.update({"writesubtitles": True, "writeautomaticsub": True,
                         "subtitleslangs": list(self.subs), "subtitlesformat": "srt/vtt/best",
                         "ignoreerrors": False})
            if ff:
                pps.append({"key": "FFmpegSubtitlesConvertor", "format": "srt"})
        if ff:
            opts["ffmpeg_location"] = ff
            if self.audio_only:
                pps.append({"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                            "preferredquality": "192"})
            else:
                opts["merge_output_format"] = "mp4"
        if pps:
            opts["postprocessors"] = pps
        return opts

    def _run(self):
        if yt_dlp is None:
            self.error, self.state = "حزمة yt-dlp غير مثبّتة", ERROR
            return
        log = _Log()
        opts = self._build_opts(log)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
                if info.get("_type") == "playlist" and info.get("entries"):
                    info = next(e for e in info["entries"] if e)
                if info.get("is_live"):
                    raise RuntimeError("البث المباشر غير مدعوم")
                self.title = info.get("title") or ""
                self._streams = len(info.get("requested_formats") or [1])
                est = sum((f.get("filesize") or f.get("filesize_approx") or 0)
                          for f in (info.get("requested_formats") or [info]))
                self.size = est or None
                if not self.filename:
                    self.filename = sanitize_filename(self.title) or "video"
                self.state = DOWNLOADING
                result = ydl.process_ie_result(info, download=True)
            rd = (result or {}).get("requested_downloads") or []
            path = rd[0].get("filepath") if rd else None
            if self._stop.is_set():
                return
            if path and os.path.exists(path):
                self.filename = os.path.basename(path)
                self.final_dir = os.path.dirname(path)
                self.size = os.path.getsize(path)
            self.state = COMPLETED
        except DownloadCancelled:
            return
        except Exception as e:  # noqa: BLE001
            if self._stop.is_set():
                return
            self.error = log.last_error or str(e)[:300]
            self.state = ERROR
        finally:
            self.speed = 0.0


def _glob_partials(folder, prefix, suffix):
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return [os.path.join(folder, n) for n in names
            if n.startswith(prefix[:60]) and n.endswith(suffix)]
