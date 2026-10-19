"""دعم روابط MEGA العامة للملفات (mega.nz/file/ID#KEY و mega.nz/#!ID!KEY).

الملفات مشفّرة بـ AES-CTR والمفتاح داخل الرابط بعد #، فنجلب رابط التنزيل المؤقت من واجهة MEGA العامة
ثم نفكّ التشفير أثناء الكتابة. لا يُرسَل المفتاح إلى أي خادم.
"""
import base64
import json
import re
import struct
import urllib.parse

import requests

API = "https://g.api.mega.co.nz/cs"
_FILE = re.compile(r"^/file/([\w-]{8})$")
_ERRORS = {
    -2: "رابط MEGA غير صالح", -9: "الملف غير موجود أو حُذف من MEGA",
    -11: "لا صلاحية للوصول إلى الملف", -16: "الملف محجوب من MEGA (مخالفة)",
    -17: "تجاوزت حصة النقل المسموحة لعنوانك على MEGA، حاول لاحقًا",
    -18: "ملف MEGA غير متاح مؤقتًا، حاول لاحقًا",
}


def _b64d(s):
    s = s.replace("-", "+").replace("_", "/").replace(",", "")
    return base64.b64decode(s + "=" * (-len(s) % 4))


def is_mega(url):
    try:
        p = urllib.parse.urlparse(url)
    except ValueError:
        return False
    h = (p.hostname or "").lower()
    return h in ("mega.nz", "mega.co.nz", "www.mega.nz")


def is_folder(url):
    p = urllib.parse.urlparse(url)
    return is_mega(url) and ("/folder/" in p.path or p.fragment.startswith("F!"))


def parse(url):
    """يرجع (المعرّف، مفتاح AES 16 بايت، nonce 8 بايت) أو None."""
    p = urllib.parse.urlparse(url)
    if not is_mega(url):
        return None
    handle = key = None
    m = _FILE.match(p.path)
    if m and p.fragment:
        handle, key = m.group(1), p.fragment
    elif p.fragment.startswith("!"):
        parts = p.fragment.split("!")
        if len(parts) >= 3:
            handle, key = parts[1], parts[2]
    if not handle or not key:
        return None
    raw = _b64d(key)
    if len(raw) != 32:
        return None
    k = struct.unpack(">8I", raw)
    aes_key = struct.pack(">4I", k[0] ^ k[4], k[1] ^ k[5], k[2] ^ k[6], k[3] ^ k[7])
    return handle, aes_key, struct.pack(">2I", k[4], k[5])


def _aes():
    try:
        from Cryptodome.Cipher import AES
    except ImportError:
        from Crypto.Cipher import AES  # noqa: F401
    return AES


def ctr(aes_key, nonce):
    return _aes().new(aes_key, _aes().MODE_CTR, nonce=nonce, initial_value=0)


def fetch_info(url, session=None):
    """يرجع {url, size, name} لرابط ملف عام."""
    parsed = parse(url)
    if not parsed:
        raise ValueError("رابط MEGA غير مفهوم (تأكد أنه يحتوي المفتاح بعد #)")
    handle, aes_key, _ = parsed
    s = session or requests
    r = s.post(API, params={"id": 1}, data=json.dumps([{"a": "g", "g": 1, "ssl": 1, "p": handle}]),
               headers={"Content-Type": "application/json"}, timeout=(10, 30))
    data = r.json()
    if isinstance(data, int):
        data = [data]
    d = data[0]
    if isinstance(d, int):
        raise IOError(_ERRORS.get(d, f"خطأ من MEGA ({d})"))
    name = ""
    try:
        at = _b64d(d["at"])
        plain = _aes().new(aes_key, _aes().MODE_CBC, iv=b"\0" * 16).decrypt(at)
        if plain.startswith(b"MEGA"):
            name = json.loads(plain[4:].rstrip(b"\0").decode("utf-8")).get("n", "")
    except Exception:  # noqa: BLE001
        pass
    if not d.get("g"):
        raise IOError("لم تُرجع MEGA رابط تنزيل (قد يكون الملف مجلدًا أو محجوبًا)")
    return {"url": d["g"], "size": int(d.get("s") or 0) or None, "name": name}
