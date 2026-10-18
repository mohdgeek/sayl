"""تحميل الفيديو والصوت عبر yt-dlp (يوتيوب وغيره، وروابط HLS/DASH).

لا يدعم المحتوى المحمي بـ DRM، ولا يتجاوز أي حماية.
"""
import os
import shutil
import threading
import time
import urllib.parse
import uuid

from . import updater  # noqa: F401  (يفعّل نسخة yt-dlp المحدَّثة قبل استيراده)
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


def clean_headers(url, headers):
    """مواقع مثل يوتيوب يتعامل معها yt-dlp بنفسه؛ تمرير Cookie/User-Agent المتصفح إليها يُفسد طلباته
    (يظهر الخطأ «The page needs to be reloaded»). نُبقي Referer فقط."""
    h = dict(headers or {})
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if any(host == s or host.endswith("." + s) for s in MEDIA_HOSTS):
        h = {k: v for k, v in h.items() if k.lower() == "referer"}
    return h


def js_runtime_opts():
    """يوتيوب يحتاج بيئة JavaScript لفك التوقيع (deno/node...). نستعمل الموجود أو ما نزّله البرنامج."""
    opts = {"remote_components": ["ejs:github"]}
    local = os.path.join(os.path.expanduser("~"), ".sayl", "bin", "deno.exe" if os.name == "nt" else "deno")
    if os.path.isfile(local):
        opts["js_runtimes"] = {"deno": {"path": local}}
    else:
        found = {n: {"path": shutil.which(n)} for n in ("deno", "node", "bun") if shutil.which(n)}
        if found:
            opts["js_runtimes"] = found
    return opts


def clean_subtitle_text(text):
    """ترجمات يوتيوب التلقائية «متدحرجة»: كل مقطع يكرر السطر السابق ثم يضيف الجديد، فيظهر سطران مكدّسان.
    نُبقي السطر الجديد فقط (حذف ما ظهر في المقطع السابق) فيظهر سطر واحد بترتيب صحيح."""
    import re
    blocks = re.split(r"\r?\n\r?\n+", text.strip("\ufeff\r\n") + "\n")
    out, prev, n = [], [], 0
    for b in blocks:
        lines = b.splitlines()
        ti = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if ti is None:
            out.append(b)          # ترويسة WEBVTT وما شابه
            continue
        body = [re.sub(r"<[^>]+>", "", l).strip() for l in lines[ti + 1:]]
        body = [l for l in body if l]
        new = [l for l in body if l not in prev] or ([] if body == prev else body)
        prev = body or prev
        if not new:
            continue
        n += 1
        head = [str(n)] if lines and lines[0].strip().isdigit() else []
        out.append("\n".join(head + [lines[ti]] + new))
    return "\n\n".join(out) + "\n"


def clean_subtitles_for(folder, stem):
    try:
        names = [n for n in os.listdir(folder) if n.startswith(stem[:60]) and n.lower().endswith((".srt", ".vtt"))]
    except OSError:
        return
    for n in names:
        p = os.path.join(folder, n)
        try:
            with open(p, "r", encoding="utf-8") as f:
                t = f.read()
            with open(p, "w", encoding="utf-8") as f:
                f.write(clean_subtitle_text(t))
        except (OSError, UnicodeError):
            pass


def aria2_path():
    local = os.path.join(os.path.expanduser("~"), ".sayl", "bin", "aria2c.exe" if os.name == "nt" else "aria2c")
    return local if os.path.isfile(local) else shutil.which("aria2c")


_SIZE_UNITS = {"B": 1, "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "TiB": 1024 ** 4}


def _aria_size(txt):
    import re
    mt = re.match(r"([\d.]+)([KMGT]?i?B)?$", txt)
    if not mt:
        return 0
    return int(float(mt.group(1)) * _SIZE_UNITS.get(mt.group(2) or "B", 1))


def parse_aria_readout(line):
    """[#2089b0 400KiB/33MiB(1%) CN:16 DL:2.1MiB ETA:4m] -> (منزَّل، إجمالي، سرعة) أو None."""
    import re
    mt = re.search(r"\[#\w+ ([\d.]+\w*)/([\d.]+\w*)\(\d+%\).*?DL:([\d.]+\w*)", line)
    if not mt:
        return None
    return _aria_size(mt.group(1)), _aria_size(mt.group(2)), _aria_size(mt.group(3))


_ARIA_PATCHED = []


def patch_aria2():
    """يجعل yt-dlp ينقل تقدّم aria2c إلى واجهتنا (افتراضيًا لا يعرض aria2c أي تقدّم)."""
    if _ARIA_PATCHED:
        return _ARIA_PATCHED[0]
    ok = False
    try:
        import subprocess
        import yt_dlp.downloader.external as ext

        class SaylAria2c(ext.Aria2cFD):
            def _call_process(self, cmd, info_dict):
                flags = 0x08000000 if os.name == "nt" else 0
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        creationflags=flags)
                tail, buf = [], ""
                try:
                    while True:
                        chunk = proc.stdout.read(128)
                        if not chunk:
                            break
                        buf += chunk.decode("utf-8", "replace")
                        parts = buf.replace("\r", "\n").split("\n")
                        buf = parts.pop()
                        for ln in parts:
                            r = parse_aria_readout(ln)
                            if r:
                                self._hook_progress({"status": "downloading", "downloaded_bytes": r[0],
                                                     "total_bytes": r[1], "speed": r[2]}, info_dict)
                            elif ln.strip():
                                tail.append(ln.strip())
                finally:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait()
                return "", "\n".join(tail[-5:]), proc.returncode

        ext._BY_NAME["aria2c"] = SaylAria2c
        ok = True
    except Exception:  # noqa: BLE001
        ok = False
    _ARIA_PATCHED.append(ok)
    return ok


_RETRY_ERR = ("reloaded", "sign in", "not available", "403", "player response", "po token",
              "challenge", "n function", "signature", "unable to extract")
_CLIENTS = (None, ["android_vr"], ["tv"], ["mweb"])


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
            "http_headers": clean_headers(url, headers)}
    opts.update(js_runtime_opts())
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
        self.headers = clean_headers(url, headers)
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
        self._est = {}             # format_id -> الحجم التقديري من المعلومات
        self._no_aria = False
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
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        done = d.get("downloaded_bytes") or 0
        if d.get("status") == "finished":
            total = total or done
            self._seen[fid] = [total, total]
            self.speed = 0.0
        else:
            self._seen[fid] = [done, max(total, done)]
            self.speed = float(d.get("speed") or 0.0)
            self._eta = d.get("eta")
        # الإجمالي = مجموع أحجام كل المسارات (المعروف فعليًا، وإلا التقديري)، فلا يقفز التقدّم ولا الحجم
        ids = set(self._est) | set(self._seen)
        tot = sum((self._seen.get(i) or [0, 0])[1] or self._est.get(i, 0) for i in ids)
        got = sum(v[0] for v in self._seen.values())
        if tot:
            self.size = tot
            self._frac = min(got / tot, 0.999)
        if self.state == PROBING:
            self.state = DOWNLOADING

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
            "concurrent_fragment_downloads": 16, "restrictfilenames": False,
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
        try:
            self._attempt_all(log)
        except DownloadCancelled:
            return
        except Exception as e:  # noqa: BLE001
            if self._stop.is_set():
                return
            self.error = log.last_error or str(e)[:300]
            self.state = ERROR
        finally:
            self.speed = 0.0

    def _make_opts(self, log, clients):
        opts = self._build_opts(log)
        opts.update(js_runtime_opts())
        if clients:
            opts["extractor_args"] = {"youtube": {"player_client": clients}}
        ar = None if self._no_aria else aria2_path()
        if ar and patch_aria2():
            opts["external_downloader"] = {"http": ar, "https": ar}   # اتصالات متوازية: أسرع بكثير
        return opts

    def _attempt_all(self, log):
        yt = "youtu" in self.url
        last = None
        try:
            from .setup_tools import log as _syslog
            _syslog(f"media: yt-dlp {getattr(yt_dlp.version, '__version__', '?')} "
                    f"js={list(js_runtime_opts().get('js_runtimes', {})) or 'none'} "
                    f"aria2={'yes' if aria2_path() else 'no'} url={self.url[:80]}")
        except Exception:  # noqa: BLE001
            pass
        clients_list = list(_CLIENTS if yt else (None,))
        i = 0
        while i < len(clients_list):
            clients = clients_list[i]
            try:
                return self._attempt(self._make_opts(log, clients))
            except DownloadCancelled:
                raise
            except Exception as e:  # noqa: BLE001
                last = e
                text = (log.last_error or str(e)).lower()
                if self._stop.is_set():
                    break
                if self.subs and "subtitle" in text:
                    self.subs = []              # فشل الترجمة (مثل 429) لا يُفشل الفيديو
                elif not self._no_aria and "aria2" in text:
                    self._no_aria = True        # تعذّر المحمّل المتوازي: ارجع إلى المحمّل العادي
                elif any(t in text for t in _RETRY_ERR):
                    i += 1
                else:
                    break
                log.last_error = ""
        ver = getattr(yt_dlp.version, "__version__", "?")
        msg = (log.last_error or str(last))[:260]
        raise RuntimeError(f"{msg} (yt-dlp {ver})")

    def _attempt(self, opts):
        if True:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
                if info.get("_type") == "playlist" and info.get("entries"):
                    info = next(e for e in info["entries"] if e)
                if info.get("is_live"):
                    raise RuntimeError("البث المباشر غير مدعوم")
                self.title = info.get("title") or ""
                self._streams = len(info.get("requested_formats") or [1])
                self._est = {str(f.get("format_id", i)): (f.get("filesize") or f.get("filesize_approx") or 0)
                             for i, f in enumerate(info.get("requested_formats") or [info])}
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
                raise DownloadCancelled()
            if path and os.path.exists(path):
                self.filename = os.path.basename(path)
                self.final_dir = os.path.dirname(path)
                self.size = os.path.getsize(path)
                if self.subs:
                    clean_subtitles_for(self.final_dir, os.path.splitext(self.filename)[0])
            self.state = COMPLETED


def _glob_partials(folder, prefix, suffix):
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return [os.path.join(folder, n) for n in names
            if n.startswith(prefix[:60]) and n.endswith(suffix)]
