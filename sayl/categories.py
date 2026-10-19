"""تصنيف الملفات حسب الامتداد."""
import os

# المفتاح، التسمية، لون الأيقونة (يُستخدم في الواجهة)
CATEGORIES = [
    ("video", "فيديو", "#8B7CFF"),
    ("audio", "موسيقى", "#FF7EB6"),
    ("docs", "مستندات", "#4DB6FF"),
    ("archive", "ضغط", "#FFB020"),
    ("apps", "برامج", "#5FD39A"),
    ("images", "صور", "#2DD4BF"),
    ("other", "أخرى", "#8A91A8"),
]
LABELS = {k: l for k, l, _ in CATEGORIES}
COLORS = {k: c for k, _, c in CATEGORIES}

_EXT = {
    "video": "mp4 mkv avi mov webm flv wmv m4v mpg mpeg 3gp ts m3u8 mpd",
    "audio": "mp3 m4a aac flac wav ogg opus wma aiff",
    "docs": "pdf doc docx xls xlsx ppt pptx txt rtf odt ods odp epub mobi csv md torrent",
    "archive": "zip rar 7z tar gz bz2 xz tgz zst cab lz lzma z arj",
    "apps": "exe msi apk apks xapk dmg deb rpm appimage pkg iso img bin bat jar appx msix",
    "images": "jpg jpeg png gif webp bmp svg ico tiff heic",
}
EXT_TO_CAT = {e: k for k, exts in _EXT.items() for e in exts.split()}


def ext_of(name):
    return os.path.splitext(name or "")[1].lstrip(".").lower()


def category_of(filename, kind="http"):
    if kind == "media":
        e = ext_of(filename)
        return "audio" if EXT_TO_CAT.get(e) == "audio" else "video"
    return EXT_TO_CAT.get(ext_of(filename), "other")
