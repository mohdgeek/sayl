"""تحويل روابط مواقع الاستضافة المعروفة إلى روابط مباشرة قابلة للتنزيل.

Dropbox وGoogle Drive وMediaFire وتيليجرام (منشورات القنوات العامة التي فيها فيديو أو صورة).
كل مُحوِّل: handles(url) ثم resolve(url, session) -> {"url", "filename"?} أو يرفع IOError برسالة عربية.
"""
import base64
import html as _html
import re
import urllib.parse

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _host(url):
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _is(host, *names):
    return any(host == n or host.endswith("." + n) for n in names)


def kind_of(url):
    h = _host(url)
    if _is(h, "dropbox.com"):
        return "dropbox"
    if _is(h, "drive.google.com", "docs.google.com", "drive.usercontent.google.com"):
        return "gdrive"
    if _is(h, "mediafire.com"):
        return "mediafire"
    if _is(h, "t.me", "telegram.me", "telegram.dog"):
        return "telegram"
    return None


def handles(url):
    """هل نحتاج لمعالجة الرابط قبل تنزيله؟ (روابط CDN النهائية لا تحتاج)"""
    k = kind_of(url)
    if k == "mediafire":
        return "/file/" in url or "/view/" in url or "/download/" in url or "/?" in url
    if k == "gdrive" and _host(url) == "drive.usercontent.google.com":
        return False
    return k is not None


# ------------------------------------------------------------------ Dropbox
def dropbox_direct(url):
    p = urllib.parse.urlsplit(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if k not in ("dl", "raw")]
    q.append(("dl", "1"))
    host = p.hostname or "www.dropbox.com"
    return urllib.parse.urlunsplit((p.scheme or "https", host, p.path, urllib.parse.urlencode(q), ""))


# ------------------------------------------------------------------ Google Drive
_GD_PATH = re.compile(r"/file/(?:u/\d+/)?d/([\w-]{10,})")


def gdrive_id(url):
    p = urllib.parse.urlsplit(url)
    m = _GD_PATH.search(p.path)
    if m:
        return m.group(1)
    q = urllib.parse.parse_qs(p.query)
    if q.get("id"):
        return q["id"][0]
    return None


def gdrive_direct(file_id):
    return f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"


def parse_gdrive_form(page, base="https://drive.usercontent.google.com/download"):
    """صفحة «لا يمكن فحص الملف بحثًا عن فيروسات»: نبني رابط التنزيل من حقول النموذج المخفية."""
    m = re.search(r'<form[^>]+id="download-form"[^>]*action="([^"]+)"', page) or \
        re.search(r'<form[^>]+action="([^"]*download[^"]*)"', page)
    if not m:
        return None
    action = _html.unescape(m.group(1))
    fields = dict(re.findall(r'<input[^>]+type="hidden"[^>]+name="([^"]+)"[^>]+value="([^"]*)"', page))
    fields.update(dict((b, a) for a, b in re.findall(r'<input[^>]+value="([^"]*)"[^>]+name="([^"]+)"', page)))
    if not fields:
        return None
    return urllib.parse.urljoin(base, action) + ("&" if "?" in action else "?") + urllib.parse.urlencode(fields)


def parse_gdrive_name(page):
    m = re.search(r'class="uc-name-size"[^>]*><a[^>]*>([^<]+)</a>', page)
    return _html.unescape(m.group(1)).strip() if m else None


# ------------------------------------------------------------------ MediaFire
def parse_mediafire(page):
    m = re.search(r'data-scrambled-url="([^"]+)"', page)
    if m:
        try:
            url = base64.b64decode(m.group(1) + "=" * (-len(m.group(1)) % 4)).decode("utf-8")
            if url.startswith("http"):
                return _html.unescape(url)
        except Exception:  # noqa: BLE001
            pass
    for pat in (r'<a[^>]+id="downloadButton"[^>]+href="(https?://[^"]+)"',
                r'<a[^>]+href="(https?://[^"]+)"[^>]+id="downloadButton"',
                r'aria-label="Download file"[^>]+href="(https?://[^"]+)"',
                r'href="(https?://download[0-9]*\.mediafire\.com/[^"]+)"'):
        m = re.search(pat, page)
        if m:
            return _html.unescape(m.group(1))
    return None


# ------------------------------------------------------------------ تيليجرام
_TG = re.compile(r"^/(?:s/)?([A-Za-z][\w]{3,})/(\d+)/?$")


def telegram_parts(url):
    p = urllib.parse.urlsplit(url)
    m = _TG.match(p.path)
    return (m.group(1), m.group(2)) if m else None


def parse_telegram(page):
    """يعيد (رابط الوسائط، امتداد) من صفحة تضمين منشور عام، أو None."""
    m = re.search(r'<video[^>]+src="([^"]+)"', page) or re.search(r'<source[^>]+src="([^"]+)"', page)
    if m:
        return _html.unescape(m.group(1)), "mp4"
    m = re.search(r"tgme_widget_message_photo_wrap[^>]*background-image:\s*url\(['\"]?([^'\")]+)", page)
    if m:
        return _html.unescape(m.group(1)), "jpg"
    return None


# ------------------------------------------------------------------ الواجهة
def resolve(url, session):
    """يحوّل الرابط لرابط مباشر. يعيد dict أو None إن لم يكن الموقع مدعومًا."""
    k = kind_of(url)
    if k is None:
        return None
    hdr = {"User-Agent": UA}
    if k == "dropbox":
        return {"url": dropbox_direct(url)}
    if k == "gdrive":
        fid = gdrive_id(url)
        if not fid:
            if "/folders/" in url or "/drive/folders" in url:
                raise IOError("مجلدات Google Drive غير مدعومة: افتح كل ملف على حدة وانسخ رابطه")
            if "/document/" in url or "/spreadsheets/" in url or "/presentation/" in url:
                raise IOError("مستندات Google (Docs/Sheets/Slides) تُصدَّر من داخل Drive: حمّلها كملف من هناك")
            raise IOError("رابط Google Drive غير مفهوم (يلزم رابط ملف)")
        direct = gdrive_direct(fid)
        r = session.get(direct, headers=hdr, stream=True, timeout=(10, 30), allow_redirects=True)
        try:
            ctype = (r.headers.get("Content-Type") or "").lower()
            if r.status_code == 404:
                raise IOError("الملف غير موجود على Google Drive أو حُذف")
            if r.status_code in (401, 403):
                raise IOError("الملف خاص: اجعل مشاركته «أي شخص لديه الرابط» ثم أعد المحاولة")
            if "text/html" in ctype:
                page = r.text[:200000]
                nxt = parse_gdrive_form(page)
                if nxt:
                    return {"url": nxt, "filename": parse_gdrive_name(page)}
                if "quota" in page.lower():
                    raise IOError("تجاوز الملف حد التنزيل اليومي على Google Drive، حاول لاحقًا")
                raise IOError("تعذّر الحصول على رابط التنزيل من Google Drive (الملف خاص أو محجوب)")
            return {"url": direct}
        finally:
            r.close()
    if k == "mediafire":
        r = session.get(url, headers=hdr, timeout=(10, 30), allow_redirects=True)
        if r.status_code >= 400:
            raise IOError(f"تعذّر فتح صفحة MediaFire (HTTP {r.status_code})")
        direct = parse_mediafire(r.text)
        if not direct:
            raise IOError("لم أجد رابط التنزيل في صفحة MediaFire (الملف محذوف أو خاص)")
        name = urllib.parse.unquote(urllib.parse.urlsplit(direct).path.rsplit("/", 1)[-1]) or None
        return {"url": direct, "filename": name}
    if k == "telegram":
        parts = telegram_parts(url)
        if not parts:
            if "/c/" in url:
                raise IOError("منشورات القنوات الخاصة تحتاج حسابًا في تيليجرام ولا يمكن تنزيلها هنا")
            raise IOError("رابط تيليجرام غير مفهوم (يلزم رابط منشور مثل t.me/اسم_القناة/123)")
        chan, post = parts
        embed = f"https://t.me/{chan}/{post}?embed=1&mode=tme"
        r = session.get(embed, headers=hdr, timeout=(10, 30))
        if r.status_code >= 400:
            raise IOError(f"تعذّر فتح منشور تيليجرام (HTTP {r.status_code})")
        got = parse_telegram(r.text)
        if not got:
            raise IOError("هذا المنشور لا يحتوي فيديو أو صورة قابلة للتنزيل مباشرة "
                          "(الملفات والمستندات في تيليجرام تحتاج تطبيق تيليجرام نفسه)")
        media, ext = got
        return {"url": media, "filename": f"{chan}_{post}.{ext}"}
    return None
