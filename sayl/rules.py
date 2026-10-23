"""قواعد الحفظ التلقائي: ملفات PDF إلى مجلد معيّن، أو كل ما يأتي من موقع إلى مجلد آخر…

كل قاعدة: {"type": "ext" | "host" | "cat", "value": "pdf, docx" | "example.com" | "video", "dir": "المجلد"}
تُجرَّب بالترتيب وأول قاعدة مطابقة تفوز.
"""
import os
import urllib.parse

TYPES = (("ext", "الامتداد"), ("host", "الموقع"), ("cat", "الفئة"))


def _ext(name):
    return os.path.splitext(name or "")[1].lstrip(".").lower()


def matches(rule, url, filename, category=None):
    value = str(rule.get("value", "")).strip().lower()
    if not value or not rule.get("dir"):
        return False
    t = rule.get("type")
    if t == "ext":
        name = filename or os.path.basename(urllib.parse.unquote(urllib.parse.urlsplit(str(url)).path))
        return _ext(name) in {v.strip().lstrip(".") for v in value.split(",") if v.strip()}
    if t == "host":
        try:
            host = (urllib.parse.urlsplit(str(url)).hostname or "").lower()
        except ValueError:
            return False
        return any(host == h or host.endswith("." + h)
                   for h in (v.strip().lstrip(".").removeprefix("www.") for v in value.split(",") if v.strip()))
    if t == "cat":
        return (category or "") == value
    return False


def folder_for(config, url, filename=None, category=None):
    """مجلد القاعدة المطابقة أو None."""
    try:
        rules = config["rules"] or []
    except (KeyError, TypeError):
        return None
    for r in rules:
        if isinstance(r, dict) and matches(r, url, filename, category):
            return r["dir"]
    return None
