const $ = (id) => document.getElementById(id);

async function showStatus() {
  const r = await chrome.runtime.sendMessage({ type: "ping" }).catch(() => null);
  const el = $("conn");
  if (r && r.ok) { el.textContent = "متصلة بسيل ✔"; el.style.color = "#4FD1A1"; }
  else { el.textContent = "غير متصلة — شغّل سيل ثم أعد المحاولة"; el.style.color = "#FF6B6B"; }
}

chrome.storage.local.get({ port: 6800, token: "", enabled: true, minSizeMB: 0 }).then((s) => {
  $("port").value = s.port;
  $("token").value = s.token;
  $("enabled").checked = s.enabled;
  $("minSizeMB").value = s.minSizeMB;
});
showStatus();

$("save").addEventListener("click", async () => {
  await chrome.storage.local.set({
    port: parseInt($("port").value, 10) || 6800,
    token: $("token").value.trim(),
    enabled: $("enabled").checked,
    minSizeMB: parseInt($("minSizeMB").value, 10) || 0,
  });
  const st = $("status");
  const r = await chrome.runtime.sendMessage({ type: "ping" }).catch(() => null);
  if (r && r.ok) { st.textContent = "تم الاتصال بالبرنامج ✔"; st.className = "ok"; }
  else { st.textContent = "تعذّر الاتصال: شغّل سيل أولًا"; st.className = "bad"; }
  showStatus();
});

$("pair").addEventListener("click", async () => {
  const st = $("status");
  const port = parseInt($("port").value, 10) || 6800;
  try {
    const r = await fetch(`http://127.0.0.1:${port}/pair`);
    const p = await r.json();
    if (r.ok && p.token) { $("token").value = p.token; $("port").value = p.port || port; $("save").click(); return; }
    st.textContent = "اضغط أولًا «ربط الإضافة الآن» في برنامج سيل (تبويب المتصفحات)";
  } catch (e) { st.textContent = "البرنامج غير شغّال أو المنفذ خاطئ"; }
  st.className = "bad";
});
