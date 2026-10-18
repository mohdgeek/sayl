// زر «تحميل» عائم يظهر فوق أي فيديو في الصفحة عند تمرير الفأرة عليه.
(() => {
  if (window.__saylInjected) return;
  window.__saylInjected = true;

  const host = document.createElement("div");
  host.style.cssText = "all:initial;position:fixed;z-index:2147483647;top:0;left:0;width:0;height:0";
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `
    <style>
      .b{position:fixed;display:none;align-items:center;gap:7px;direction:rtl;cursor:pointer;
         border:0;border-radius:10px;padding:8px 13px;background:#FFB020;color:#1B1300;
         font:700 13px "Segoe UI",Tahoma,sans-serif;box-shadow:0 6px 20px rgba(0,0,0,.35)}
      .b:hover{background:#FFC24D}
      .b.ok{background:#4FD1A1}.b.err{background:#FF6B6B}
      svg{width:16px;height:16px;stroke:currentColor;fill:none;stroke-width:2.2;
          stroke-linecap:round;stroke-linejoin:round}
    </style>
    <button class="b" part="b"><svg viewBox="0 0 24 24"><path d="M12 4v11M6.5 10.5 12 16l5.5-5.5M5 20h14"/></svg><span>تحميل</span></button>`;
  const btn = root.querySelector(".b");
  const label = root.querySelector("span");
  document.documentElement.appendChild(host);

  let current = null, hideTimer = null, busy = false, lastMove = 0;

  function show(v) {
    const r = v.getBoundingClientRect();
    if (r.width < 200 || r.height < 110 || r.bottom < 0 || r.top > innerHeight) return hide();
    current = v;
    btn.style.display = "flex";
    btn.style.top = Math.max(8, r.top + 10) + "px";
    btn.style.left = Math.max(8, r.right - 10 - btn.offsetWidth) + "px";
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hide, 2500);
  }
  function hide() { if (!busy) btn.style.display = "none"; current = null; }

  document.addEventListener("mousemove", (e) => {
    const now = Date.now();
    if (now - lastMove < 120) return;
    lastMove = now;
    const v = document.elementsFromPoint(e.clientX, e.clientY).find((el) => el.tagName === "VIDEO");
    if (v) show(v);
  }, { passive: true });

  btn.addEventListener("mouseenter", () => clearTimeout(hideTimer));
  btn.addEventListener("click", async (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!current || busy) return;
    busy = true;
    label.textContent = "جارٍ الإرسال…";
    let res;
    try {
      res = await chrome.runtime.sendMessage({ type: "grab", src: current.currentSrc || current.src || "" });
    } catch (err) {
      res = { ok: false, error: "أعد تحميل الصفحة بعد تحديث الإضافة" };
    }
    btn.classList.add(res && res.ok ? "ok" : "err");
    label.textContent = res && res.ok ? "تمت الإضافة ✓" : (res && res.error) || "تعذّر الاتصال بالبرنامج";
    setTimeout(() => {
      busy = false;
      btn.classList.remove("ok", "err");
      label.textContent = "تحميل";
      hide();
    }, 2200);
  });
})();
