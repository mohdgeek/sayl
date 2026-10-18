const $ = (id) => document.getElementById(id);
const say = (text, ok) => { const m = $("msg"); m.textContent = text; m.className = ok ? "ok" : "bad"; };

async function send(url, kind, referrer, extra = {}) {
  const res = await chrome.runtime.sendMessage({ type: "send", url, kind, referrer, ...extra });
  if (res && res.ok) say(extra.confirm === false || kind !== "media" ? "تمت الإضافة إلى سيل" : "اختر الجودة في نافذة سيل", true);
  else say((res && res.error) || "تعذّر الاتصال بالبرنامج", false);
}

async function loadFormats(url, tabUrl, box) {
  const r = await chrome.runtime.sendMessage({ type: "formats", url }).catch(() => null);
  box.textContent = "";
  if (!r || !r.ok) { box.textContent = (r && r.error) ? "تعذّر جلب الجودات" : ""; box.title = (r && r.error) || ""; return; }
  const add = (label, extra) => {
    const c = document.createElement("button");
    c.className = "chip";
    c.textContent = label;
    c.addEventListener("click", () => send(url, "media", tabUrl, { ...extra, confirm: false }));
    box.append(c);
  };
  for (const h of (r.heights || []).slice(0, 8)) add(h + "p", { height: h });
  add("MP3", { audio_only: true });
}

function render(list, tabUrl) {
  const site = SaylLib.pageVideo(tabUrl);
  if (site) list = [{ url: tabUrl, kind: "page", type: site, size: null }, ...list];
  const ul = $("list");
  ul.textContent = "";
  $("count").textContent = list.length ? `${list.length} عنصر` : "";
  $("empty").style.display = list.length ? "none" : "block";
  for (const e of (site ? [list[0], ...list.slice(1).reverse()] : list.slice().reverse())) {
    const li = document.createElement("li");
    const type = document.createElement("span");
    type.className = "type" + (e.kind === "manifest" ? " hls" : "");
    type.textContent = e.type;
    const meta = document.createElement("div");
    meta.className = "meta";
    const u = document.createElement("div");
    u.className = "u";
    u.textContent = e.url;
    u.title = e.url;
    const s = document.createElement("div");
    s.className = "s";
    s.textContent = e.kind === "page" ? "فيديو هذه الصفحة · يُستخرج تلقائيًا"
      : [SaylLib.hostOf(e.url), SaylLib.fmtSize(e.size)].filter(Boolean).join(" · ");
    meta.append(u, s);
    const b = document.createElement("button");
    b.className = "btn small";
    b.textContent = "تحميل";
    b.addEventListener("click", () => send(e.url, e.kind === "manifest" || e.kind === "page" ? "media" : "auto", tabUrl));
    li.append(type, meta, b);
    if (e.kind === "page") {
      const chips = document.createElement("div");
      chips.className = "chips";
      chips.textContent = "جارٍ جلب الجودات…";
      li.append(chips);
      loadFormats(e.url, tabUrl, chips);
    }
    ul.append(li);
  }
}

async function init() {
  const s = await chrome.storage.local.get({ port: 6800, token: "", enabled: true });
  $("enabled").checked = s.enabled;
  $("enabled").addEventListener("change", (e) => chrome.storage.local.set({ enabled: e.target.checked }));
  const opts = () => chrome.runtime.openOptionsPage();
  $("gear").addEventListener("click", opts);
  $("open-opts").addEventListener("click", opts);

  const first = await chrome.runtime.sendMessage({ type: "ping" }).catch(() => null);
  if (!s.token && !(first && first.ok)) {
    $("setup").style.display = "block";
    $("app").style.display = "none";
    $("st").textContent = "غير متصلة";
    $("dot").className = "dot off";
    return;
  }
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const key = `m_${tab.id}`;
  const store = await chrome.storage.session.get(key);
  render(store[key] || [], tab.url);
  chrome.storage.onChanged.addListener((ch, area) => {
    if (area === "session" && ch[key]) render(ch[key].newValue || [], tab.url);
  });
  $("page").addEventListener("click", () => send(tab.url, "media", tab.url));

  const res = first;
  const on = res && res.ok;
  $("dot").className = "dot " + (on ? "on" : "off");
  $("st").textContent = on ? "متصل" : "البرنامج غير شغّال";
  if (on && res.media === false) say("ميزة الفيديو تحتاج yt-dlp في البرنامج", false);
}
init();
