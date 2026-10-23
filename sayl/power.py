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


def cancel_shutdown():
    if sys.platform.startswith("win"):
        subprocess.Popen(["shutdown", "/a"])
