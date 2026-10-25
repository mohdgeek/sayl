"""كشف الملفات المكررة على القرص قبل التحميل: نفس الاسم والحجم، أو نفس الحجم باسم مختلف."""
import os

MAX_ENTRIES = 20000


def _walk(base, depth):
    out = [base]
    if depth <= 0:
        return out
    try:
        for e in os.scandir(base):
            if e.is_dir(follow_symlinks=False) and not e.name.startswith(".") and len(out) < 400:
                out += _walk(e.path, depth - 1)
    except OSError:
        pass
    return out


def _dirs(config, extra=()):
    """مجلد التنزيلات (حتى مستويين من المجلدات الفرعية) + كل مجلد استُخدم للحفظ سابقًا أو في القواعد."""
    out = []
    for d in _walk(config["download_dir"], 2):
        if d not in out:
            out.append(d)
    extras = list(extra) + list((config["category_dirs"] or {}).values())
    extras += [r.get("dest") or r.get("folder") or "" for r in (config["rules"] or []) if isinstance(r, dict)]
    for d in extras:
        if d and os.path.isdir(d):
            for w in _walk(d, 1):
                if w not in out:
                    out.append(w)
    return out


def find(config, name, size, extra_dirs=(), skip=None):
    """يعيد [(المسار، 'same' أو 'size')] لأفضل ما وُجد (حتى 3). 'same' = اسم وحجم متطابقان."""
    if not size or size < 50 * 1024:
        return []
    stem, ext = os.path.splitext(name or "")
    hits, seen = [], 0
    for d in _dirs(config, extra_dirs):
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                seen += 1
                if seen > MAX_ENTRIES:
                    return _rank(hits)
                try:
                    if not e.is_file(follow_symlinks=False) or e.stat().st_size != size:
                        continue
                except OSError:
                    continue
                if skip and os.path.abspath(e.path) == os.path.abspath(skip):
                    continue
                en, ee = os.path.splitext(e.name)
                same = e.name.lower() == (name or "").lower() or (
                    ee.lower() == ext.lower() and en.lower().startswith(stem.lower() + " ("))
                hits.append((e.path, "same" if same else "size"))
    return _rank(hits)


def _rank(hits):
    hits.sort(key=lambda h: h[1] != "same")
    return hits[:3]
