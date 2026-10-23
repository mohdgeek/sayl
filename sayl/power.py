"""إجراءات الطاقة بعد انتهاء الطابور."""
import subprocess
import sys


def shutdown(delay=0):
    if sys.platform.startswith("win"):
        cmd = ["shutdown", "/s", "/t", str(delay)]
    elif sys.platform == "darwin":
        cmd = ["osascript", "-e", 'tell app "System Events" to shut down']
    else:
        cmd = ["systemctl", "poweroff"]
    subprocess.Popen(cmd)


ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
_awake = False


def _set_execution_state(flags):
    import ctypes
    return ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(flags))


def keep_awake(on):
    """يمنع نوم الجهاز ما دام on=True (ويندوز). الاستدعاء من الخيط نفسه (خيط الواجهة) يبقي الحالة.
    يعيد True إن تغيّرت الحالة فعلًا."""
    global _awake
    if not sys.platform.startswith("win"):
        return False
    try:
        # إعادة التأكيد دوريًا آمنة؛ الإلغاء يكون بـ ES_CONTINUOUS وحدها
        _set_execution_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED if on else ES_CONTINUOUS)
    except Exception:  # noqa: BLE001
        return False
    changed = on != _awake
    _awake = on
    return changed


def cancel_shutdown():
    if sys.platform.startswith("win"):
        subprocess.Popen(["shutdown", "/a"])
