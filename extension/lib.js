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

  return { classify, dedupeKey, fmtSize, pickBest, hostOf };
})();
if (typeof module !== "undefined") module.exports = SaylLib;
