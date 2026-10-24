"""عند لصق رابط صفحة ويب (لا ملف): نستخرج منها روابط الملفات القابلة للتحميل (exe, zip, pdf…)."""
import html
import re
import urllib.parse

import requests

FILE_EXT = ("zip rar 7z tar gz tgz bz2 xz zst iso img dmg exe msi apk deb rpm pkg jar bin cab appx msix "
            "appimage pdf doc docx xls xlsx ppt pptx csv epub torrent mp3 mp4 mkv avi mov flac wav ")
EXT_RE = "|".join(FILE_EXT.split())
HREF = re.compile(r"""(?:href|src)\s*=\s*["']([^"'#>]+?)["']""", re.I)
OK = re.compile(r"\.(" + EXT_RE + r")(?:[?#].*)?$", re.I)


def extract(page_html, base_url):
    seen, out = set(), []
    for m in HREF.finditer(page_html):
        u = urllib.parse.urljoin(base_url, html.unescape(m.group(1)).strip())
        if not u.lower().startswith(("http://", "https://")):
            continue
        if not OK.search(urllib.parse.urlsplit(u).path):
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def scan(url, session=None, timeout=12):
    """روابط الملفات في الصفحة، أو [] إن لم يكن الرابط صفحة HTML."""
    s = session or requests.Session()
    with s.get(url, stream=True, timeout=(8, timeout), headers={"User-Agent": "Mozilla/5.0 Sayl/1.0"}) as r:
        if r.status_code >= 400:
            return []
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "html" not in ctype or "attachment" in (r.headers.get("Content-Disposition") or "").lower():
            return []
        body = r.raw.read(2_000_000, decode_content=True)
        return extract(body.decode(r.encoding or "utf-8", "replace"), r.url)
