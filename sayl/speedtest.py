"""اختبار الاتصال: زمن الاستجابة، DNS، وسرعة التنزيل مع تقدير مدة تحميل أحجام شائعة."""
import socket
import time

import requests

DEFAULT_URL = "https://speed.cloudflare.com/__down?bytes=25000000"
HOSTS = (("1.1.1.1", 443), ("8.8.8.8", 443))


def latency(hosts=HOSTS, timeout=3):
    best = None
    for h, p in hosts:
        try:
            t = time.perf_counter()
            socket.create_connection((h, p), timeout=timeout).close()
            ms = (time.perf_counter() - t) * 1000
            best = ms if best is None else min(best, ms)
        except OSError:
            continue
    return best


def dns_time(name="www.google.com"):
    try:
        t = time.perf_counter()
        socket.getaddrinfo(name, 443)
        return (time.perf_counter() - t) * 1000
    except OSError:
        return None


def download_speed(url=DEFAULT_URL, seconds=8, session=None):
    """بايت/ثانية، بقياس تنزيل ما يصل إلى `seconds` ثوانٍ."""
    s = session or requests.Session()
    got, t0 = 0, None
    with s.get(url, stream=True, timeout=(8, 10)) as r:
        r.raise_for_status()
        t0 = time.perf_counter()
        for chunk in r.iter_content(64 * 1024):
            got += len(chunk)
            if time.perf_counter() - t0 >= seconds:
                break
    dt = max(time.perf_counter() - t0, 1e-6)
    return got / dt


def eta(size_bytes, bps):
    if not bps:
        return "—"
    s = size_bytes / bps
    if s < 60:
        return f"{int(s) + 1} ثانية"
    if s < 3600:
        return f"{int(s // 60)} دقيقة"
    return f"{int(s // 3600)} ساعة و{int(s % 3600 // 60)} دقيقة"


def run(url=DEFAULT_URL, seconds=8, hosts=HOSTS):
    out = {"latency": latency(hosts), "dns": dns_time(), "bps": None, "error": None}
    try:
        out["bps"] = download_speed(url, seconds)
    except (requests.RequestException, OSError) as e:
        out["error"] = str(e)[:120]
    return out
