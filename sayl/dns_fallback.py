"""احتياط DNS: إن فشل DNS النظام في حلّ اسم موقع، نسأل DNS-over-HTTPS (مثل «DNS الآمن» في Chrome).

يُستخدم فقط عند الفشل، ويُخزَّن الناتج مؤقتًا. اتصال TLS يبقى باسم الموقع الأصلي فلا تتأثر الشهادة.
"""
import json
import socket
import threading
import time

import requests

# تُطلَب بعنوان IP مباشر كي لا نحتاج DNS لحلّها
ENDPOINTS = [
    ("https://1.1.1.1/dns-query", {"accept": "application/dns-json"}),
    ("https://8.8.8.8/resolve", {"accept": "application/dns-json"}),
]
_cache = {}
_lock = threading.Lock()
_orig = socket.getaddrinfo
_installed = False


def _doh(host):
    now = time.time()
    with _lock:
        hit = _cache.get(host)
        if hit and now - hit[0] < 600:
            return hit[1]
    for url, hdr in ENDPOINTS:
        try:
            r = requests.get(url, params={"name": host, "type": "A"}, headers=hdr, timeout=5)
            ips = [a["data"] for a in r.json().get("Answer", []) if a.get("type") == 1]
            if ips:
                with _lock:
                    _cache[host] = (now, ips)
                return ips
        except Exception:  # noqa: BLE001
            continue
    return []


def _getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    try:
        return _orig(host, port, family, type, proto, flags)
    except socket.gaierror:
        if not isinstance(host, str) or host.replace(".", "").isdigit() or ":" in host:
            raise
        ips = _doh(host)
        if not ips:
            raise
        try:
            from .setup_tools import log
            log(f"dns: DNS النظام فشل في حلّ {host}؛ استُخدم DNS الآمن ({ips[0]})")
        except Exception:  # noqa: BLE001
            pass
        out = []
        for ip in ips:
            out.extend(_orig(ip, port, family, type, proto, flags))
        return out


def install():
    global _installed
    if not _installed:
        socket.getaddrinfo = _getaddrinfo
        _installed = True
