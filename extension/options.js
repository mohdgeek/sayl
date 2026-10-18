const $ = (id) => document.getElementById(id);

async function load() {
  try {
    const p = await (await fetch(chrome.runtime.getURL("pair.json"))).json();
    if (p && p.token) await chrome.storage.local.set({ port: p.port || 6800, token: p.token });
  } catch (e) { /* إدخال يدوي */ }
  return chrome.storage.local.get({ port: 6800, token: "", enabled: true, minSizeMB: 0 });
}
load().then((s) => {
  $("port").value = s.port;
  $("token").value = s.token;
  $("enabled").checked = s.enabled;
  $("minSizeMB").value = s.minSizeMB;
});

$("save").addEventListener("click", async () => {
  const s = {
    port: parseInt($("port").value, 10) || 6800,
    token: $("token").value.trim(),
    enabled: $("enabled").checked,
    minSizeMB: parseInt($("minSizeMB").value, 10) || 0,
  };
  await chrome.storage.local.set(s);
  const st = $("status");
  try {
    const res = await fetch(`http://127.0.0.1:${s.port}/ping`, { headers: { "X-Sayl-Token": s.token } });
    if (res.ok) { st.textContent = "تم الاتصال بالبرنامج ✔"; st.className = "ok"; }
    else { st.textContent = res.status === 401 ? "الرمز غير صحيح" : `خطأ ${res.status}`; st.className = "bad"; }
  } catch (e) {
    st.textContent = "البرنامج غير شغّال أو المنفذ خاطئ"; st.className = "bad";
  }
});


$("pair").addEventListener("click", async () => {
  const st = $("status");
  const port = parseInt($("port").value, 10) || 6800;
  try {
    const r = await fetch(`http://127.0.0.1:${port}/pair`);
    const p = await r.json();
    if (r.ok && p.token) {
      $("token").value = p.token;
      $("port").value = p.port || port;
      $("save").click();
      return;
    }
    st.textContent = "اضغط أولًا «ربط الإضافة» في برنامج سيل (القائمة ⋮) ثم أعد المحاولة";
  } catch (e) {
    st.textContent = "البرنامج غير شغّال أو المنفذ خاطئ";
  }
  st.className = "bad";
});
