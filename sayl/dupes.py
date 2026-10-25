"""كشف الملفات المكررة على القرص قبل التحميل: نفس الاسم والحجم، أو نفس الحجم باسم مختلف."""
import os

MAX_ENTRIES = 20000


def _dirs(config, extra=()):
    base = config["download_dir"]
    out = [base]
    try:
        for e in os.scandir(base):
            if e.is_dir(follow_symlinks=False) and not e.name.startswith("."):
                out.append(e.path)
    except OSError:
        pass
    for d in list(extra) + list((config["category_dirs"] or {}).values()):
        if d and os.path.isdir(d) and d not in out:
            out.append(d)
    return out


def find(config, name, size, extra_dirs=()):
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
                en, ee = os.path.splitext(e.name)
                same = e.name.lower() == (name or "").lower() or (
                    ee.lower() == ext.lower() and en.lower().startswith(stem.lower() + " ("))
                hits.append((e.path, "same" if same else "size"))
    return _rank(hits)


def _rank(hits):
    hits.sort(key=lambda h: h[1] != "same")
    return hits[:3]
