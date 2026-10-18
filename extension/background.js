// سيل: يرصد الوسائط في كل تبويب، ويلتقط التحميلات، ويرسلها إلى البرنامج المحلي.
importScripts("lib.js");

const DEFAULTS = { port: 6800, token: "", enabled: true, minSizeMB: 0 };
const MAX_PER_TAB = 30;
const getSettings = () => chrome.storage.local.get(DEFAULTS);

// الإقران التلقائي: البرنامج يكتب pair.json (المنفذ والرمز) داخل مجلد الإضافة عند تصديرها
async function syncPair() {
  try {
    const r = await fetch(chrome.runtime.getURL("pair.json"));
    if (!r.ok) return;
    const p = await r.json();
    if (p && p.version && p.version !== chrome.runtime.getManifest().version) {
      chrome.runtime.reload();                       // ملفات الإضافة حُدّثت من البرنامج: أعد تحميل نفسك
      return;
    }
    if (p && p.token) await chrome.storage.local.set({ port: p.port || 6800, token: p.token });
  } catch (e) { /* لا يوجد ملف: إدخال يدوي من الخيارات */ }
}
syncPair();
chrome.runtime.onStartup.addListener(syncPair);

// ربط تلقائي: يعمل فقط بعد أن يضغط المستخدم «ربط الإضافة» في البرنامج (نافذة دقائق قليلة)
async function pairFromApp(port) {
  try {
    const r = await fetch(`http://127.0.0.1:${port}/pair`);
    if (!r.ok) return false;
    const p = await r.json();
    if (!p.token) return false;
    await chrome.storage.local.set({ port: p.port || port, token: p.token });
    return true;
  } catch (e) { return false; }
}

// ---- النقل الأساسي: Native Messaging (بلا رمز ولا منفذ). الاحتياطي: HTTP محلي بالرمز.
const HOST = "com.sayl.host";
let nport = null;
let nSeq = 0;
let nBadUntil = 0;
const nPending = new Map();

function nativeConnect() {
  if (nport) return nport;
  nport = chrome.runtime.connectNative(HOST);
  nport.onMessage.addListener((m) => {
    const p = nPending.get(m.id);
    if (p) { nPending.delete(m.id); chrome.storage.local.remove("nativeError"); p.resolve(m); }
  });
  nport.onDisconnect.addListener(() => {
    const err = chrome.runtime.lastError?.message || "disconnected";
    nport = null;
    for (const p of nPending.values()) p.reject(new Error(err));
    nPending.clear();
  });
  return nport;
}

function nativeCall(path, options) {
  if (Date.now() < nBadUntil) return Promise.resolve(null);
  return new Promise((resolve, reject) => {
    const id = ++nSeq;
    const timer = setTimeout(() => { nPending.delete(id); reject(new Error("timeout")); }, 45000);
    nPending.set(id, {
      resolve: (m) => { clearTimeout(timer); resolve(m); },
      reject: (e) => { clearTimeout(timer); reject(e); },
    });
    try {
      nativeConnect().postMessage({
        id, path, method: options.method || "GET",
        body: options.body ? JSON.parse(options.body) : null, ua: navigator.userAgent,
      });
    } catch (e) { nPending.delete(id); clearTimeout(timer); reject(e); }
  }).catch((e) => {
    nBadUntil = Date.now() + 15000;     // الجسر غير مسجّل أو معطّل: نستخدم HTTP لحظات ثم نعيد المحاولة
    chrome.storage.local.set({ nativeError: String(e && e.message || e) });
    return null;
  });
}

async function api(path, options = {}, retried = false) {
  const m = await nativeCall(path, options);
  if (m) {
    if (m.error === "app_not_running") throw new Error("برنامج سيل غير شغّال");
    if (m.error) throw new Error(m.error);
    if (m.status >= 400) throw new Error(`خطأ ${m.status}`);
    return m.json;
  }
  const { port, token } = await getSettings();
  if (!token && !(await pairFromApp(port))) throw new Error("الإضافة غير مرتبطة بسيل");
  const t = (await getSettings()).token;
  const res = await fetch(`http://127.0.0.1:${port}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", "X-Sayl-Token": t, ...(options.headers || {}) },
  });
  if (res.status === 401 && !retried && (await pairFromApp(port))) return api(path, options, true);
  if (!res.ok) throw new Error(res.status === 401 ? "الرمز غير صحيح" : `خطأ ${res.status}`);
  return res.json();
}

async function buildHeaders(url, referrer) {
  const headers = {};
  if (referrer) headers["Referer"] = referrer;
  try {
    const cookies = await chrome.cookies.getAll({ url });
    if (cookies.length) headers["Cookie"] = cookies.map((c) => `${c.name}=${c.value}`).join("; ");
  } catch (e) { /* لا بأس */ }
  headers["User-Agent"] = navigator.userAgent;
  return headers;
}

// kind: "auto" | "http" | "media"
async function sendToSayl(url, { referrer = "", kind = "auto", filename = null } = {}) {
  const headers = await buildHeaders(url, referrer);
  const r = await api("/add", { method: "POST", body: JSON.stringify({ url, headers, filename, kind }) });
  if (r && r.duplicate) notify("هذا الرابط موجود سابقًا في سيل");
  return r;
}

// ---------------------------------------------------------------- رصد الوسائط
const key = (tabId) => `m_${tabId}`;
let queue = Promise.resolve();            // يسلسل الكتابة كي لا تضيع إضافات متزامنة

function addDetected(tabId, entry) {
  queue = queue.then(async () => {
    const store = await chrome.storage.session.get(key(tabId));
    const list = store[key(tabId)] || [];
    const dk = SaylLib.dedupeKey(entry.url, entry.kind);
    if (list.some((e) => e.dk === dk)) return;
    list.push({ ...entry, dk });
    while (list.length > MAX_PER_TAB) list.shift();
    await chrome.storage.session.set({ [key(tabId)]: list });
    await chrome.action.setBadgeBackgroundColor({ tabId, color: "#FFB020" });
    await chrome.action.setBadgeTextColor?.({ tabId, color: "#1B1300" });
    await chrome.action.setBadgeText({ tabId, text: String(list.length) });
  }).catch(() => {});
}

chrome.webRequest.onHeadersReceived.addListener((d) => {
  if (d.tabId < 0 || d.statusCode >= 400) return;
  const info = SaylLib.classify(d.url, d.responseHeaders);
  if (!info) return;
  addDetected(d.tabId, { url: d.url, kind: info.kind, type: info.type, size: info.size, ts: Date.now() });
}, { urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "other"] }, ["responseHeaders"]);

function markPage(tabId, url) {
  if (!SaylLib.pageVideo(url)) return;
  chrome.action.setBadgeBackgroundColor({ tabId, color: "#FFB020" }).catch(() => {});
  chrome.action.setBadgeText({ tabId, text: "▶" }).catch(() => {});
}

chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  if (change.url || change.status === "complete") markPage(tabId, change.url || tab.url);
  if (change.status === "loading" && change.url) {       // تنقّل جديد: نصفّر القائمة
    chrome.storage.session.remove(key(tabId));
    chrome.action.setBadgeText({ tabId, text: "" }).catch(() => {});
  }
});
chrome.tabs.onRemoved.addListener((tabId) => chrome.storage.session.remove(key(tabId)));

// ---------------------------------------------------------------- التقاط تحميلات Chrome
chrome.downloads.onCreated.addListener(async (item) => {
  const s = await getSettings();
  if (!s.enabled || !s.token) return;
  const url = item.finalUrl || item.url;
  if (!/^https?:/i.test(url)) return;                    // تجاهل blob: وdata:
  if (s.minSizeMB && item.fileSize > 0 && item.fileSize < s.minSizeMB * 1048576) return;
  try {
    await sendToSayl(url, { referrer: item.referrer, kind: "http" });
    await chrome.downloads.cancel(item.id);
    await chrome.downloads.erase({ id: item.id });
  } catch (e) {
    // البرنامج غير متاح: يكمل Chrome التحميل بنفسه
  }
});

// ---------------------------------------------------------------- القائمة والرسائل
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "sayl-link", title: "التحميل عبر سيل", contexts: ["link", "video", "audio"],
  });
  chrome.contextMenus.create({
    id: "sayl-page", title: "تحميل فيديو هذه الصفحة عبر سيل", contexts: ["page"],
  });
});

function notify(message) {
  chrome.notifications.create({ type: "basic", iconUrl: "icon.png", title: "سيل", message });
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  try {
    if (info.menuItemId === "sayl-page") {
      await sendToSayl(tab.url, { referrer: tab.url, kind: "media" });
    } else {
      const url = info.linkUrl || info.srcUrl;
      if (!url) return;
      await sendToSayl(url, { referrer: tab && tab.url, kind: "auto" });
    }
    notify("تمت الإضافة إلى سيل");
  } catch (e) {
    notify("تعذّر الاتصال بالبرنامج. تأكد أنه يعمل وأن الرمز صحيح.");
  }
});

async function grab(msg, sender) {
  const tab = sender.tab;
  let url = msg.src && /^https?:/i.test(msg.src) ? msg.src : null;
  let kind = "auto";
  if (tab && SaylLib.pageVideo(tab.url) && (!url || /^blob:/i.test(msg.src || ""))) {
    url = tab.url; kind = "media";                        // يوتيوب وأمثاله: البرنامج يستخرج الفيديو من الصفحة
  }
  if (!url && tab) {                                      // فيديو blob: نبحث عن الأفضل المرصود
    const store = await chrome.storage.session.get(key(tab.id));
    const best = SaylLib.pickBest(store[key(tab.id)]);
    if (best) { url = best.url; kind = best.kind === "manifest" ? "media" : "auto"; }
    else { url = tab.url; kind = "media"; }               // آخر حل: رابط الصفحة
  }
  if (!url) throw new Error("لا يوجد رابط");
  return sendToSayl(url, { referrer: tab ? tab.url : "", kind });
}

chrome.runtime.onMessage.addListener((msg, sender, respond) => {
  (async () => {
    try {
      if (msg.type === "send") {
        await sendToSayl(msg.url, { referrer: msg.referrer || "", kind: msg.kind || "auto" });
      } else if (msg.type === "grab") {
        await grab(msg, sender);
      } else if (msg.type === "ping") {
        return respond({ ok: true, ...(await api("/ping")) });
      }
      respond({ ok: true });
    } catch (e) {
      respond({ ok: false, error: String(e.message || e) });
    }
  })();
  return true;                                            // استجابة غير متزامنة
});


// نبضة كل 30 ثانية: تُظهر للبرنامج أن الإضافة متصلة، وتربط نفسها تلقائيًا متى فُتحت نافذة الربط
chrome.alarms.create("sayl-ping", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === "sayl-ping") { syncPair(); api("/ping").catch(() => {}); }
});
api("/ping").catch(() => {});
