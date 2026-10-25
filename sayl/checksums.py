"""يبحث تلقائيًا عن بصمة الملف الرسمية (ملفات .sha256 أو SHA256SUMS أو بصمة GitHub) لمقارنتها بعد التحميل."""
import re
import time
import urllib.parse

import requests

UA = "Mozilla/5.0 Sayl/1.0"
SIDE_EXT = (".sha256", ".sha256sum", ".sha512", ".sha1", ".md5")
DIR_FILES = ("SHA256SUMS", "sha256sums.txt", "SHA256SUMS.txt", "sha256sum.txt", "checksums.txt",
             "CHECKSUMS.txt", "SHA512SUMS", "MD5SUMS")
LENS = {32: "md5", 40: "sha1", 64: "sha256", 128: "sha512"}
HEX = re.compile(r"\b[0-9a-fA-F]{32,128}\b")


def parse_sums(text, filename):
    """بصمة filename من محتوى ملف بصمات، أو من ملف يحوي بصمة واحدة فقط. يعيد '' إن لم يجدها."""
    fn = filename.lower()
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]
    for l in lines:
        low = l.lower()
        if fn and fn in low.replace("*", " "):
            m = HEX.search(l)
            if m and len(m.group(0)) in LENS:
                return m.group(0).lower()
    if len(lines) == 1:                      # ملف .sha256 بجوار الملف: بصمة وحدها (وقد يتبعها الاسم)
        m = HEX.match(lines[0])
        if m and len(m.group(0)) in LENS:
            rest = lines[0][m.end():].strip(" *\t")
            if not rest or rest.lower() == fn or rest.lower().endswith(fn):
                return m.group(0).lower()
    return ""


def github_parts(url):
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/releases/download/([^/]+)/([^/?#]+)", url)
    return m.groups() if m else None


def candidates(url, filename):
    base = url.split("?")[0].split("#")[0]
    out = [base + e for e in SIDE_EXT]
    d = base.rsplit("/", 1)[0] + "/"
    out += [d + urllib.parse.quote(n) for n in DIR_FILES]
    return out


def _get_text(s, url, timeout=6):
    try:
        with s.get(url, stream=True, timeout=(5, timeout), headers={"User-Agent": UA}) as r:
            if r.status_code != 200:
                return ""
            if "html" in (r.headers.get("Content-Type") or "").lower():
                return ""
            n = int(r.headers.get("Content-Length") or 0)
            if n > 1_500_000:
                return ""
            return r.raw.read(1_500_000, decode_content=True).decode("utf-8", "replace")
    except (requests.RequestException, OSError):
        return ""


def github_digest(s, url):
    g = github_parts(url)
    if not g:
        return ""
    owner, repo, tag, name = g
    try:
        r = s.get(f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}", timeout=8,
                  headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
        if r.status_code != 200:
            return ""
        for a in r.json().get("assets", []):
            if a.get("name") == urllib.parse.unquote(name) and a.get("digest"):
                return a["digest"]            # مثل "sha256:abc…"
    except (requests.RequestException, ValueError):
        pass
    return ""


def find(url, filename, session=None, budget=25):
    """يعيد 'algo:hex' أو ''. يبحث في ملفات البصمات المجاورة وفي بصمة GitHub الرسمية."""
    s = session or requests.Session()
    end = time.monotonic() + budget
    d = github_digest(s, url)
    if d:
        return d
    for c in candidates(url, filename):
        if time.monotonic() > end:
            break
        t = _get_text(s, c)
        if t:
            h = parse_sums(t, filename)
            if h:
                return f"{LENS[len(h)]}:{h}"
    return ""
