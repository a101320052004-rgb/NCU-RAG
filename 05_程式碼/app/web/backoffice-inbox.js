/* 後台「檢查信箱」：承辦人寄群發信時密件副本到小幫手地址，這裡按一下就用 IMAP 帶進「待處理」。
   伺服器把這個檔接在 backoffice.js 後面一起送出，包住 Backoffice.Publish，在「從 Email 帶入」區塊加按鈕。 */
(() => {
  const BO = window.Backoffice;
  if (!BO || BO.inbox) return;
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const key = () => { try { return sessionStorage.getItem("admin_key") || ""; } catch { return ""; } };
  async function call(path, method = "GET") {
    const res = await fetch(path, {method, headers: {"X-Admin-Key": key(), "Content-Type": "application/json"}});
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof j.detail === "string" ? j.detail : "HTTP " + res.status);
    return j;
  }
  const Publish = BO.Publish;
  BO.Publish = (el, opts = {}) => {
    const view = Publish(el, opts);
    let info = null, busy = false, note = "";
    function bar() {
      const box = el.querySelector("#bo-import");
      if (!box) return;
      box.querySelector("[data-inbox]")?.remove();
      const div = document.createElement("div");
      div.dataset.inbox = "";
      div.className = "bo-actions";
      div.style.cssText = "margin:0;align-items:center;flex-wrap:wrap";
      div.innerHTML = `<span class="bo-small" style="flex:1;min-width:220px">寄群發信時密件副本給小幫手信箱${info?.inbox ? `（${esc(info.inbox)}）` : ""}，按「檢查信箱」帶進來，再按「帶入」，AI 改寫好的內容會填進下面。</span>
        <button type="button"${busy ? " disabled" : ""}>${busy ? "檢查中…" : "檢查信箱"}</button>
        ${note ? `<span class="bo-small" role="status" style="flex-basis:100%">${note}</span>` : ""}`;
      const old = box.querySelector(":scope > p.bo-small");  // 原本「demo 用範例信代替」的說明
      if (old) old.replaceWith(div); else box.prepend(div);
      div.querySelector("button").addEventListener("click", async () => {
        busy = true; note = ""; bar();
        try {
          const r = await call("/api/admin/mail/fetch", "POST");
          note = r.imported ? `帶入 ${r.imported} 封新信：${r.subjects.map(esc).join("、")}。AI 已改寫好，按該封的「帶入」檢查內容。` +
                              (r.more ? `還有 ${r.more} 封，再按一次。` : "")
                            : `沒有新信（近 14 天寄到 ${esc(r.inbox)} 的信有 ${r.matched} 封，都已帶入過）。`;
        } catch (e) { note = `<span class="crit">檢查失敗：${esc(e.message)}</span>`; }
        busy = false;
        await view.load();
        bar();
      });
    }
    new MutationObserver(() => { const b = el.querySelector("#bo-import"); if (b && !b.querySelector("[data-inbox]")) bar(); })
      .observe(el, {childList: true, subtree: true});
    call("/api/admin/email-config").then(c => { info = c; bar(); }).catch(() => {});
    return view;
  };
  BO.inbox = true;
})();
