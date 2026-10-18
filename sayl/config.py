"""إعدادات البرنامج: تُحفظ في ~/.sayl/config.json"""
import json
import os
import secrets
import threading

APP_DIR = os.path.join(os.path.expanduser("~"), ".sayl")
_OLD_DIR = os.path.join(os.path.expanduser("~"), ".mydm")   # الاسم القديم: ننقل بياناتك إن وُجدت
if os.path.isdir(_OLD_DIR) and not os.path.exists(APP_DIR):
    try:
        os.rename(_OLD_DIR, APP_DIR)
    except OSError:
        pass

DEFAULTS = {
    "download_dir": os.path.join(os.path.expanduser("~"), "Downloads"),
    "connections": 8,          # عدد الاتصالات لكل ملف
    "max_concurrent": 3,       # عدد الملفات المتزامنة
    "speed_limit_kb": 0,       # 0 = بدون حد
    "port": 6800,              # منفذ الخادم المحلي للإضافة
    "token": "",               # رمز مصادقة الإضافة
    "theme": "dark",           # dark | light
    "clipboard_watch": True,   # التقاط الروابط المنسوخة
    "notify": True,            # إشعار عند اكتمال التحميل
    "video_quality": 0,        # 0 = أفضل جودة، أو ارتفاع بالبكسل (1080/720/480)
    "ffmpeg_path": "",         # اتركه فارغًا للبحث التلقائي في PATH
    # المظهر
    "dark_base": "dark",       # القالب الداكن الذي يعود إليه زر السمة
    "accent": "#FFB020",       # لون الإبراز
    "ok_color": "",            # لون النجاح (فارغ = الافتراضي)
    "err_color": "",           # لون الخطأ (فارغ = الافتراضي)
    # المميزات
    "subfolders": False,       # حفظ كل نوع في مجلد فرعي (فيديو، مستندات…)
    "warn_duplicates": True,   # تنبيه عند تكرار رابط أو ملف
    "subtitles": False,        # تنزيل ترجمة الفيديو
    "sub_langs": "ar,en",
    "watch_enabled": False,    # مراقبة مجلد روابط
    "watch_folder": "",
    "schedule_enabled": False, # تشغيل الطابور داخل نافذة زمنية فقط
    "sched_start": "02:00",
    "sched_stop": "07:00",
    "peak_enabled": False,     # حدّ سرعة مختلف خلال ساعات معيّنة
    "peak_start": "09:00",
    "peak_stop": "17:00",
    "peak_kb": 500,
    "after_all": "none",
    "update_repo": "",          # مثل: username/sayl
    "auto_update_check": True,       # none | quit | shutdown
}


class Config:
    def __init__(self, path=None):
        self.path = path or os.path.join(APP_DIR, "config.json")
        self._lock = threading.Lock()
        self.data = dict(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data.update(json.load(f))
        except (OSError, ValueError):
            pass
        if not self.data.get("token"):
            self.data["token"] = secrets.token_urlsafe(24)
            self.save()

    def save(self):
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value
