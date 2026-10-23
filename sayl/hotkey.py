"""اختصار لوحة مفاتيح عام (ويندوز): يفتح نافذة إضافة رابط من أي برنامج، ويلصق الرابط المنسوخ."""
import sys
import threading

MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8, "meta": 0x8}
WM_HOTKEY, WM_QUIT, MOD_NOREPEAT = 0x0312, 0x0012, 0x4000


def parse(spec):
    """'Ctrl+Alt+S' -> (mods, vk) أو None إن لم يكن صالحًا (يلزم معدِّل واحد على الأقل ومفتاح)."""
    mods, vk = 0, None
    for part in str(spec or "").replace(" ", "").split("+"):
        p = part.lower()
        if not p:
            return None
        if p in MODS:
            mods |= MODS[p]
        elif len(p) == 1 and (p.isalpha() or p.isdigit()) and p.isascii():
            vk = ord(p.upper())
        elif p.startswith("f") and p[1:].isdigit() and 1 <= int(p[1:]) <= 12:
            vk = 0x70 + int(p[1:]) - 1
        else:
            return None
    if not mods or vk is None:
        return None
    return mods, vk


class GlobalHotkey:
    def __init__(self, callback):
        self.callback = callback
        self._thread = None
        self._tid = None
        self.ok = False
        self.error = ""

    def start(self, spec):
        """يسجّل الاختصار. يعيد True عند النجاح (يعمل على ويندوز فقط)."""
        self.stop()
        parsed = parse(spec)
        if not sys.platform.startswith("win"):
            self.error = "الاختصار العام يعمل على ويندوز فقط"
            return False
        if not parsed:
            self.error = "صيغة الاختصار غير صالحة (مثال: Ctrl+Alt+S)"
            return False
        ready = threading.Event()
        res = {}

        def loop():
            import ctypes
            from ctypes import wintypes
            u, k = ctypes.windll.user32, ctypes.windll.kernel32
            self._tid = k.GetCurrentThreadId()
            res["ok"] = bool(u.RegisterHotKey(None, 1, parsed[0] | MOD_NOREPEAT, parsed[1]))
            ready.set()
            if not res["ok"]:
                return
            msg = wintypes.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    try:
                        self.callback()
                    except Exception:  # noqa: BLE001
                        pass
            u.UnregisterHotKey(None, 1)

        self._thread = threading.Thread(target=loop, daemon=True, name="hotkey")
        self._thread.start()
        ready.wait(2)
        self.ok = bool(res.get("ok"))
        if not self.ok:
            self.error = "الاختصار مستخدم في برنامج آخر، جرّب تركيبة مختلفة"
        return self.ok

    def stop(self):
        if self._thread is not None and self._tid and sys.platform.startswith("win"):
            try:
                import ctypes
                ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            except Exception:  # noqa: BLE001
                pass
        self._thread, self._tid, self.ok = None, None, False
