// منطق نقي لرصد الوسائط (يُختبر بـ node خارج المتصفح).
const SaylLib = (() => {
  const MANIFEST = /\.(m3u8|mpd)(\?|#|$)/i;
  const FILE = /\.(mp4|webm|mkv|m4v|mov|flv|mp3|m4a|ogg|opus|wav|aac)(\?|#|$)/i;
  const SEGMENT = /\.(ts|m4s|cmfv|cmfa)(\?|#|$)/i;
  const IGNORE_HOSTS = ["googlevideo.com"]; // يعالجها البرنامج عبر رابط الصفحة
  const MIN_SIZE = 150 * 1024;

  function header(headers, name) {
    name = name.toLowerCase();
    for (const h of headers || []) {
      if (h.name && h.name.toLowerCase() === name) return h.value || "";
    }
    return "";
  }

  function sizeOf(headers) {
    const cr = header(headers, "content-range").match(/\/(\d+)$/);
    if (cr) return parseInt(cr[1], 10);
    const cl = parseInt(header(headers, "content-length"), 10);
    return Number.isFinite(cl) ? cl : null;
  }

  function hostOf(url) {
    try { return new URL(url).hostname.toLowerCase(); } catch (e) { return ""; }
  }

  // يرجع null إن لم يكن الطلب وسائط قابلة للتحميل
  function classify(url, headers) {
    if (!/^https?:/i.test(url)) return null;
    const host = hostOf(url);
    if (!host || host === "127.0.0.1" || host === "localhost") return null;
    if (IGNORE_HOSTS.some((h) => host === h || host.endsWith("." + h))) return null;
    if (SEGMENT.test(url)) return null;
    const ctype = header(headers, "content-type").toLowerCase().split(";")[0].trim();
    if (ctype === "video/mp2t" || ctype === "video/iso.segment") return null;
    const size = sizeOf(headers);
    if (MANIFEST.test(url) || ctype.includes("mpegurl") || ctype.includes("dash+xml")) {
      const dash = /\.mpd(\?|#|$)/i.test(url) || ctype.includes("dash+xml");
      return { kind: "manifest", type: dash ? "DASH" : "HLS", size: null };
    }
    const media = ctype.startsWith("video/") || ctype.startsWith("audio/");
    if (media || (FILE.test(url) && !ctype.startsWith("text/"))) {
      if (size !== null && size < MIN_SIZE) return null;
      let label = ctype.split("/")[1] || "";
      const m = url.match(FILE);
      if (m) label = m[1];
      return { kind: "file", type: (label || "media").toUpperCase(), size };
    }
    return null;
  }

  // ملفات عادية تُنزَّل (مضغوطة، برامج، مستندات، أقراص…) من أي صيغة: تعتمد على الترويسات لا الامتداد وحده
  const ARCHIVE = /\.(zip|rar|7z|tar|gz|tgz|bz2|xz|zst|iso|img|dmg|exe|msi|apk|deb|rpm|pkg|jar|bin|cab|pdf|docx?|xlsx?|pptx?|torrent|csv|epub|apks|xapk|appx)(\?|#|$)/i;
  const RENDERABLE = /^(text\/|image\/|video\/|audio\/|application\/(json|javascript|xml|xhtml\+xml|x-javascript|ld\+json|manifest\+json|wasm)|font\/)/;

  function classifyDownload(url, headers) {
    if (!/^https?:/i.test(url)) return null;
    const host = hostOf(url);
    if (!host || host === "127.0.0.1" || host === "localhost") return null;
    const ctype = header(headers, "content-type").toLowerCase().split(";")[0].trim();
    const disp = header(headers, "content-disposition");
    const attach = /attachment/i.test(disp) || /filename\*?=/i.test(disp);
    const binary = ctype === "application/octet-stream" || ctype === "binary/octet-stream" ||
      (ctype.startsWith("application/") && !RENDERABLE.test(ctype) && ctype !== "application/pdf");
    const byExt = ARCHIVE.test(url.split("?")[0]) || ARCHIVE.test(url);
    if (!(attach || binary || (byExt && !ctype.startsWith("text/")))) return null;
    const size = sizeOf(headers);      // الملفات الصغيرة (مثبّتات صغيرة، مستندات) تُلتقط أيضًا
    let label = "";
    const fn = disp.match(/filename\*?=(?:UTF-8'')?"?([^";]+)/i);
    if (fn) { const e = fn[1].match(/\.([A-Za-z0-9]{1,6})$/); if (e) label = e[1]; }
    if (!label) { const e = url.split("?")[0].match(/\.([A-Za-z0-9]{1,6})$/); if (e) label = e[1]; }
    if (!label) label = (ctype.split("/")[1] || "FILE").replace(/^x-/, "");
    return { kind: "file", type: label.toUpperCase().slice(0, 8), size };
  }

  function dedupeKey(url, kind) {
    try {
      const u = new URL(url);
      return kind === "manifest" ? u.origin + u.pathname : u.origin + u.pathname + u.search;
    } catch (e) { return url; }
  }

  function fmtSize(n) {
    if (n === null || n === undefined) return "";
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n.toFixed(0) : n.toFixed(1)) + " " + units[i];
  }

  // يختار أفضل مرشّح لتحميله عندما لا يكون لدينا رابط مباشر (فيديو blob)
  function pickBest(entries) {
    if (!entries || !entries.length) return null;
    const m = entries.filter((e) => e.kind === "manifest");
    if (m.length) return m[m.length - 1];
    return entries.slice().sort((a, b) => (b.size || 0) - (a.size || 0))[0];
  }

  // صفحة فيديو لموقع يدعمه البرنامج (يوتيوب وغيره): الفيديو هناك blob فلا يُرصد رابطه، فنعرض رابط الصفحة نفسه.
  function pageVideo(url) {
    let u;
    try { u = new URL(url); } catch (e) { return null; }
    if (!/^https?:$/.test(u.protocol)) return null;
    const h = u.hostname.toLowerCase().replace(/^(www|m|music)\./, "");
    const p = u.pathname;
    if (h === "youtube.com") {
      if (p === "/watch" && u.searchParams.get("v")) return "YouTube";
      if (/^\/(shorts|live|embed)\/[\w-]+/.test(p)) return "YouTube";
      return null;
    }
    if (h === "youtu.be") return p.length > 1 ? "YouTube" : null;
    const sites = { "vimeo.com": /^\/\d+/, "dailymotion.com": /^\/video\//, "twitch.tv": /^\/(videos\/\d+|[\w-]+\/clip\/)/,
      "tiktok.com": /\/video\//, "facebook.com": /\/(videos|watch|reel)/, "fb.watch": /./,
      "instagram.com": /^\/(p|reel|tv)\//, "x.com": /\/status\//, "twitter.com": /\/status\//,
      "reddit.com": /\/comments\//, "streamable.com": /^\/\w+/, "bilibili.com": /^\/video\//, "soundcloud.com": /^\/[^/]+\/[^/]+/ };
    const re = sites[h];
    return re && re.test(p) ? h.split(".")[0].replace(/^./, (c) => c.toUpperCase()) : null;
  }

  return { classify, classifyDownload, dedupeKey, fmtSize, pickBest, hostOf, pageVideo };
})();
if (typeof module !== "undefined") module.exports = SaylLib;
