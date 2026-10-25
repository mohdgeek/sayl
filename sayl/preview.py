"""معاينة الملف قبل تحميله: صورة، أول صفحة من PDF، أو قائمة محتويات أرشيف zip (بقراءة أجزاء فقط)."""
import io
import os
import re
import zipfile

import requests

IMG_EXT = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}
MAX_IMAGE = 6 * 1024 * 1024
MAX_PDF = 8 * 1024 * 1024
MAX_ZIP_FULL = 8 * 1024 * 1024         # إن لم يدعم الخادم Range نقرأ كامل الملف حتى هذا الحجم
UA = "Mozilla/5.0 Sayl/1.0"


def kind_for(name, size):
    ext = os.path.splitext(name or "")[1].lstrip(".").lower()
    if ext in IMG_EXT:
        return "image" if (size is None or size <= MAX_IMAGE) else None
    if ext == "pdf":
        return "pdf" if (size is None or size <= MAX_PDF) else None
    if ext == "zip":
        return "zip"
    return None


class RangeFile(io.RawIOBase):
    """ملف قابل للتمرير (seek) فوق HTTP باستخدام Range، فيكفي قراءة نهاية zip لمعرفة محتوياته."""

    def __init__(self, url, size, session, headers):
        self.url, self.size, self.s, self.h = url, size, session, headers
        self.pos = 0
        self.reads = 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=io.SEEK_SET):
        self.pos = {io.SEEK_SET: off, io.SEEK_CUR: self.pos + off, io.SEEK_END: self.size + off}[whence]
        self.pos = max(0, min(self.pos, self.size))
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        n = min(n, self.size - self.pos)
        if n <= 0:
            return b""
        self.reads += 1
        if self.reads > 40:
            raise IOError("كثرة الطلبات")
        r = self.s.get(self.url, headers={**self.h, "Range": f"bytes={self.pos}-{self.pos + n - 1}"}, timeout=(10, 20))
        if r.status_code != 206:
            raise IOError("الخادم لا يدعم القراءة الجزئية")
        data = r.content
        self.pos += len(data)
        return data

    def readinto(self, b):
        d = self.read(len(b))
        b[:len(d)] = d
        return len(d)


def _get_all(url, headers, session, limit, cancel=None):
    with session.get(url, headers=headers, stream=True, timeout=(10, 25)) as r:
        if r.status_code >= 400:
            raise IOError(f"HTTP {r.status_code}")
        buf = bytearray()
        for chunk in r.iter_content(64 * 1024):
            if cancel is not None and cancel.is_set():
                raise IOError("أُلغيت المعاينة")      # أُغلقت النافذة: لا نستهلك الشبكة بعد الآن
            buf += chunk
            if len(buf) > limit:
                raise IOError("الملف أكبر من حد المعاينة")
        return bytes(buf)


def _zip_entries(zf):
    infos = [i for i in zf.infolist() if not i.filename.endswith("/")]
    return {"count": len(infos), "total": sum(i.file_size for i in infos),
            "entries": [(i.filename, i.file_size) for i in infos[:300]]}


def fetch(url, name, size=None, headers=None, session=None, cancel=None):
    """يعيد {"kind": "image"|"pdf", "data": bytes} أو {"kind": "zip", ...} أو None إن لم تتوفر معاينة."""
    kind = kind_for(name, size)
    if not kind:
        return None
    s = session or requests.Session()
    h = {"User-Agent": UA, **(headers or {})}
    if kind == "image":
        return {"kind": "image", "data": _get_all(url, h, s, MAX_IMAGE, cancel)}
    if kind == "pdf":
        data = _get_all(url, h, s, MAX_PDF, cancel)
        return {"kind": "pdf", "data": data} if data[:5] == b"%PDF-" else None
    # zip
    if size:
        try:
            with zipfile.ZipFile(RangeFile(url, size, s, h)) as z:
                return {"kind": "zip", **_zip_entries(z)}
        except (IOError, zipfile.BadZipFile):
            if size > MAX_ZIP_FULL:
                return None
    data = _get_all(url, h, s, MAX_ZIP_FULL, cancel)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {"kind": "zip", **_zip_entries(z)}
