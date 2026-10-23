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


def keys_from_node(raw):
    """من مفتاح عقدة ملف (32 بايت) إلى (مفتاح AES، nonce)."""
    k = struct.unpack(">8I", raw)
    return (struct.pack(">4I", k[0] ^ k[4], k[1] ^ k[5], k[2] ^ k[6], k[3] ^ k[7]),
            struct.pack(">2I", k[4], k[5]))


def _node_name(at_b64, node_key):
    """يفك اسم عقدة؛ مفتاح الملف 32 بايت يُطوى إلى 16."""
    if len(node_key) == 32:
        node_key = keys_from_node(node_key)[0]
    try:
        plain = _aes().new(node_key, _aes().MODE_CBC, iv=b"\0" * 16).decrypt(_b64d(at_b64))
        if plain.startswith(b"MEGA"):
            return json.loads(plain[4:].rstrip(b"\0").decode("utf-8")).get("n", "")
    except Exception:  # noqa: BLE001
        pass
    return ""


# ------------------------------------------------------------------ المجلدات
_FOLDER = re.compile(r"^/folder/([\w-]{8})$")


def parse_folder(url):
    """يرجع (معرّف المجلد، مفتاحه 16 بايت، (نوع، معرّف) لعقدة فرعية محددة أو None) أو None."""
    p = urllib.parse.urlparse(url)
    if not is_mega(url):
        return None
    fid = key = None
    sub = None
    m = _FOLDER.match(p.path)
    if m and p.fragment:
        fid = m.group(1)
        parts = p.fragment.split("/")
        key = parts[0]
        if len(parts) >= 3 and parts[1] in ("file", "folder"):
            sub = (parts[1], parts[2])
    elif p.fragment.startswith("F!"):
        parts = p.fragment.split("!")
        if len(parts) >= 3:
            fid, key = parts[1], parts[2].split("!")[0].split("#")[0]
            if len(parts) >= 4 and parts[3]:
                sub = ("file", parts[3].lstrip("?"))
    if not fid or not key:
        return None
    raw = _b64d(key)
    if len(raw) != 16:
        return None
    return fid, raw, sub


def _api(session, fid, payload):
    s = session or requests
    r = s.post(API, params={"id": 1, "n": fid}, data=json.dumps([payload]),
               headers={"Content-Type": "application/json"}, timeout=(10, 30))
    data = r.json()
    if isinstance(data, int):
        raise IOError(_ERRORS.get(data, f"خطأ من MEGA ({data})"))
    d = data[0]
    if isinstance(d, int):
        raise IOError(_ERRORS.get(d, f"خطأ من MEGA ({d})"))
    return d


def list_folder(url, session=None, limit=2000):
    """يعدّد ملفات مجلد MEGA عام: {name, files:[{h, name, size, path, key(32 بايت)}]}."""
    parsed = parse_folder(url)
    if not parsed:
        raise ValueError("رابط مجلد MEGA ناقص: انسخه كاملًا مع الجزء الذي بعد علامة #")
    fid, fkey, sub = parsed
    d = _api(session, fid, {"a": "f", "c": 1, "r": 1})
    aes = _aes()
    nodes = {}
    for n in d.get("f", []):
        k = n.get("k", "")
        enc = None
        for part in k.split("/"):
            if ":" in part:
                enc = part.split(":", 1)[1]
                break
        if not enc or "h" not in n:
            continue
        try:
            raw = _b64d(enc)
            if len(raw) % 16:
                continue
            key = aes.new(fkey, aes.MODE_ECB).decrypt(raw)
        except Exception:  # noqa: BLE001
            continue
        nodes[n["h"]] = {"h": n["h"], "p": n.get("p", ""), "t": n.get("t"), "key": key,
                         "name": _node_name(n.get("a", ""), key) or n["h"], "size": int(n.get("s") or 0)}
    if not nodes:
        raise IOError("المجلد فارغ أو لا يمكن قراءة محتواه (تأكد من الرابط الكامل)")
    root = nodes.get(fid) or next((x for x in nodes.values() if x["t"] in (1, 2) and x["p"] not in nodes), None)
    rootname = root["name"] if root else "MEGA"

    def path_of(n):
        parts = []
        cur, guard = n, 0
        while cur and guard < 64:
            parts.append(cur["name"])
            if cur["h"] == (root or {}).get("h"):
                break
            cur = nodes.get(cur["p"])
            guard += 1
        return "/".join(reversed(parts))

    def under(n, anc):
        cur, guard = n, 0
        while cur and guard < 64:
            if cur["h"] == anc:
                return True
            cur = nodes.get(cur["p"])
            guard += 1
        return False

    files = []
    for n in nodes.values():
        if n["t"] != 0 or len(n["key"]) != 32:
            continue
        if sub and sub[0] == "file" and n["h"] != sub[1]:
            continue
        if sub and sub[0] == "folder" and not under(n, sub[1]):
            continue
        files.append({"h": n["h"], "name": sanitize(n["name"]), "size": n["size"],
                      "path": path_of(n), "key": n["key"]})
    if not files:
        raise IOError("لا توجد ملفات داخل هذا المجلد")
    files.sort(key=lambda f: f["path"].lower())
    return {"name": rootname, "files": files[:limit], "fid": fid, "truncated": len(files) > limit}


def sanitize(name):
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name or "").strip(". ") or "file"


def folder_file_url(fid, fkey_b64, handle):
    return f"https://mega.nz/folder/{fid}#{fkey_b64}/file/{handle}"


def fetch_node_info(fid, handle, node_key, session=None):
    """رابط تنزيل ملف داخل مجلد عام؛ يرجع {url, size, name}."""
    d = _api(session, fid, {"a": "g", "g": 1, "ssl": 1, "n": handle})
    if not d.get("g"):
        raise IOError("لم تُرجع MEGA رابط تنزيل لهذا الملف")
    return {"url": d["g"], "size": int(d.get("s") or 0) or None,
            "name": _node_name(d.get("at", ""), node_key)}


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
