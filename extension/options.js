const $ = (id) => document.getElementById(id);

chrome.storage.local.get({ port: 6800, token: "", enabled: true, minSizeMB: 0 }).then((s) => {
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
