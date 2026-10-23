"""فك ضغط الأرشيفات بأمان: zip وtar (gz/bz2/xz) مدمجان، و7z/rar عبر برنامج 7-Zip إن كان مثبّتًا."""
import os
import shutil
import subprocess
import sys
import tarfile
import uuid
import zipfile

TAR_EXTS = (".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".tar")
EXTS = (".zip",) + TAR_EXTS + (".7z", ".rar")
MIN_FREE_AFTER = 500 * 1024 * 1024        # نترك 500MB على الأقل حرة بعد الفك


def is_archive(name):
    n = (name or "").lower()
    return n.endswith(EXTS)


def stem_of(name):
    n = name
    low = n.lower()
    for e in sorted(EXTS, key=len, reverse=True):
        if low.endswith(e):
            return n[: -len(e)] or "extracted"
    return os.path.splitext(n)[0] or "extracted"


def find_7zip():
    cands = [os.path.join(os.environ.get(v, ""), "7-Zip", "7z.exe") for v in ("ProgramFiles", "ProgramFiles(x86)")]
    for p in cands:
        if os.path.isfile(p):
            return p
    for n in ("7z", "7za", "7zz"):
        p = shutil.which(n)
        if p:
            return p
    return None


def _unique_dir(path):
    if not os.path.exists(path):
        return path
    i = 1
    while os.path.exists(f"{path} ({i})"):
        i += 1
    return f"{path} ({i})"


def _check_space(folder, need):
    free = shutil.disk_usage(folder).free
    if need + MIN_FREE_AFTER > free:
        raise IOError("لا توجد مساحة كافية على القرص لفك الضغط")


def _inside(base, target):
    base = os.path.realpath(base)
    target = os.path.realpath(target)
    return target == base or target.startswith(base + os.sep)


def _extract_zip(path, dest):
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        total = sum(i.file_size for i in infos)
        comp = sum(i.compress_size for i in infos) or 1
        if total > (1 << 30) and total / comp > 1000:
            raise IOError("الأرشيف مشبوه (نسبة ضغط غير طبيعية) فلم يُفك")
        _check_space(os.path.dirname(dest), total)
        for i in infos:
            if not _inside(dest, os.path.join(dest, i.filename)):
                raise IOError("الأرشيف يحاول الكتابة خارج مجلده (غير آمن) فلم يُفك")
            if i.flag_bits & 0x1:
                raise IOError("الأرشيف محميّ بكلمة سر")
        z.extractall(dest)


def _extract_tar(path, dest):
    with tarfile.open(path) as t:
        members = t.getmembers()
        _check_space(os.path.dirname(dest), sum(m.size for m in members))
        for m in members:
            if not _inside(dest, os.path.join(dest, m.name)) or m.issym() or m.islnk() and not _inside(
                    dest, os.path.join(dest, m.linkname)):
                raise IOError("الأرشيف يحتوي مسارًا غير آمن فلم يُفك")
        try:
            t.extractall(dest, filter="data")
        except TypeError:                      # بايثون أقدم بلا مرشّحات
            t.extractall(dest)


def _extract_7z(path, dest):
    exe = find_7zip()
    if not exe:
        raise IOError("لفك ضغط 7z وRAR ثبّت برنامج 7-Zip المجاني ثم أعد المحاولة")
    flags = 0x08000000 if sys.platform.startswith("win") else 0       # CREATE_NO_WINDOW
    r = subprocess.run([exe, "x", "-y", "-bd", f"-o{dest}", "-p-", path], capture_output=True, text=True,
                       creationflags=flags, timeout=3600)
    if r.returncode != 0:
        out = (r.stdout + r.stderr).lower()
        if "wrong password" in out or "encrypted" in out or "enter password" in out:
            raise IOError("الأرشيف محميّ بكلمة سر")
        raise IOError("فشل فك الضغط: " + (r.stderr or r.stdout).strip().splitlines()[-1][:150]
                      if (r.stderr or r.stdout).strip() else "فشل فك الضغط")


def extract(path, delete=False):
    """يفك الأرشيف إلى مجلد بجانبه باسم الأرشيف (ويتجنّب مجلدًا مكرّرًا إن كان له مجلد جذر وحيد).
    يعيد مسار الناتج؛ يرفع IOError برسالة عربية عند الفشل."""
    if not os.path.isfile(path):
        raise IOError("ملف الأرشيف غير موجود")
    folder = os.path.dirname(path)
    name = os.path.basename(path)
    tmp = os.path.join(folder, f".sayl-extract-{uuid.uuid4().hex[:8]}")
    os.makedirs(tmp)
    try:
        low = name.lower()
        if low.endswith(".zip"):
            _extract_zip(path, tmp)
        elif low.endswith(TAR_EXTS):
            _extract_tar(path, tmp)
        elif low.endswith((".7z", ".rar")):
            _extract_7z(path, tmp)
        else:
            raise IOError("صيغة الأرشيف غير مدعومة")
        entries = os.listdir(tmp)
        if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])):
            final = _unique_dir(os.path.join(folder, entries[0]))
            os.rename(os.path.join(tmp, entries[0]), final)
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            final = _unique_dir(os.path.join(folder, stem_of(name)))
            os.rename(tmp, final)
    except zipfile.BadZipFile as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise IOError("الأرشيف تالف أو غير مكتمل") from e
    except (tarfile.TarError, EOFError) as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise IOError("الأرشيف تالف أو غير مكتمل") from e
    except RuntimeError as e:                    # zipfile: كلمة سر
        shutil.rmtree(tmp, ignore_errors=True)
        raise IOError("الأرشيف محميّ بكلمة سر" if "password" in str(e).lower() or "encrypted" in str(e).lower()
                      else str(e)) from e
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    if delete:
        try:
            os.remove(path)
        except OSError:
            pass
    return final
