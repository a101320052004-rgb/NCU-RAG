/* 後台「這則公告取代了哪些舊資料」：發布後（發布結果、發布紀錄的細節）列出意思最接近的舊段落，
   承辦人勾選後下架，小幫手之後只會用新公告回答；下架的可以還原。
   由 demo 頁在 backoffice.js 後載入，不改 backoffice.js。 */
(() => {
  if (window.BoSupersede) return;
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const key = () => { try { return sessionStorage.getItem("admin_key") || ""; } catch { return ""; } };
  async function call(path, method = "GET", body) {
    const res = await fetch(path, {method, headers: {"X-Admin-Key": key(), "Content-Type": "application/json"},
                                   body: body ? JSON.stringify(body) : undefined});
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof j.detail === "string" ? j.detail : "HTTP " + res.status);
    return j;
  }
  const SRC = {manual: "人工整理", sheet: "國際處資料表", announcement: "公告"};
  const srcName = s => SRC[s] || (String(s || "").startsWith("crawler") ? "官網" : (s || ""));
  // 每則公告一份狀態：畫面重畫（例如點發布紀錄的其他列）後還在
  const state = new Map();
  const S = id => state.get(id) || state.set(id, {rows: null, picked: new Set(), busy: false, note: "", open: false}).get(id);

  function panel(id) {
    const s = S(id);
    const head = `<h4 style="margin:0 0 4px">這則公告取代了哪些舊資料？</h4>
      <p class="bo-small" style="margin:0 0 8px">規定改了時，把舊的說法下架，學生問小幫手就只會拿到新公告的內容。系統列出意思最接近的資料，由你勾選，下架後可以還原。</p>`;
    if (!s.open) return head + `<button type="button" data-sp="find">找出可能過時的舊資料</button>`;
    if (!s.rows) return head + `<p class="bo-small">${s.busy ? "比對中…" : esc(s.note)}</p>`;
    if (!s.rows.length) return head + '<p class="bo-small">沒有找到相近的舊資料。</p>';
    const row = r => {
      const pct = Math.max(0, Math.round((1 - r.distance) * 100));
      const box = r.superseded
        ? `<span class="crit">已下架</span>${r.by_this ? "" : '<br><span class="bo-small">（被其他公告）</span>'}`
        : `<input type="checkbox" data-pick="${esc(r.id)}" ${s.picked.has(r.id) ? "checked" : ""} aria-label="下架：${esc(r.title)}">`;
      return `<tr><td style="white-space:nowrap;text-align:center">${box}</td>
        <td><strong>${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a>` : esc(r.title)}</strong>
            <div class="bo-small">${esc(r.snippet)}…</div></td>
        <td class="bo-small" style="white-space:nowrap">${esc(srcName(r.source))}<br>${esc(String(r.last_verified || "").slice(0, 10))}</td>
        <td class="num">${pct}%</td>
        <td>${r.superseded ? `<button type="button" data-restore="${esc(r.id)}">還原</button>` : ""}</td></tr>`;
    };
    return head + `<div class="bo-table" style="overflow-x:auto"><table style="min-width:620px"><thead><tr>
        <th>下架</th><th>舊資料（點標題看原頁）</th><th>來源／日期</th><th class="num">相近</th><th><span class="bo-sr">還原</span></th></tr></thead>
        <tbody>${s.rows.map(row).join("")}</tbody></table></div>
      <div class="bo-actions" style="align-items:center">
        <button type="button" class="bo-primary" data-sp="retire"${s.picked.size && !s.busy ? "" : " disabled"}>下架勾選的 ${s.picked.size} 筆</button>
        <button type="button" data-sp="find">重新比對</button>
        ${s.note ? `<span class="bo-small" role="status">${s.note}</span>` : ""}</div>
      <p class="bo-small">只勾真的被這則公告改掉的規定；「相近」只是意思接近，不代表內容過時。</p>`;
  }

  async function load(id) {
    const s = S(id);
    s.open = true; s.busy = true; s.rows = null; s.note = ""; render(id);
    try { s.rows = await call(`/api/admin/announce/${id}/similar`); s.picked.clear(); }
    catch (e) { s.note = `<span class="crit">比對失敗：${esc(e.message)}</span>`; }
    s.busy = false; render(id);
  }
  async function retire(id) {
    const s = S(id);
    s.busy = true; render(id);
    try {
      const r = await call(`/api/admin/announce/${id}/supersede`, "POST", {ids: [...s.picked]});
      s.note = `已下架 ${r.superseded} 筆，約 1 分鐘內生效。`;
      s.rows.forEach(x => { if (s.picked.has(x.id)) { x.superseded = true; x.by_this = true; } });
      s.picked.clear();
    } catch (e) { s.note = `<span class="crit">下架失敗：${esc(e.message)}</span>`; }
    s.busy = false; render(id);
  }
  async function restore(id, cid) {
    const s = S(id);
    try {
      await call("/api/admin/kb-restore", "POST", {ids: [cid]});
      const x = s.rows.find(r => r.id === cid); if (x) { x.superseded = false; x.by_this = false; }
      s.note = "已還原，約 1 分鐘內生效。";
    } catch (e) { s.note = `<span class="crit">還原失敗：${esc(e.message)}</span>`; }
    render(id);
  }

  function render(id) {
    document.querySelectorAll(`[data-supersede="${id}"]`).forEach(box => {
      box.innerHTML = panel(id);
      box.querySelector('[data-sp="find"]')?.addEventListener("click", () => load(id));
      box.querySelector('[data-sp="retire"]')?.addEventListener("click", () => retire(id));
      box.querySelectorAll("[data-pick]").forEach(cb => cb.addEventListener("change", () => {
        const s = S(id); cb.checked ? s.picked.add(cb.dataset.pick) : s.picked.delete(cb.dataset.pick); render(id);
      }));
      box.querySelectorAll("[data-restore]").forEach(b => b.addEventListener("click", () => restore(id, b.dataset.restore)));
    });
  }

  // 發布結果（「已發布：…」）和發布紀錄的細節都有「全文頁」連結，從連結取公告 id，在後面加上面板
  function scan() {
    document.querySelectorAll(".bo-main, .bo-detail").forEach(host => {
      const a = host.querySelector('a[href^="/notice/"]');
      if (!a || host.querySelector("[data-supersede]")) return;
      if (host.classList.contains("bo-main") && !/^已發布/.test(host.querySelector("h3")?.textContent || "")) return;
      const id = a.getAttribute("href").split("/").pop();
      const box = document.createElement("div");
      box.dataset.supersede = id;
      box.style.cssText = "margin-top:16px;padding-top:12px;border-top:1px solid var(--bo-line, #dde3e7)";
      host.appendChild(box);
      render(id);
    });
  }
  new MutationObserver(scan).observe(document.body, {childList: true, subtree: true});
  scan();
  window.BoSupersede = {state, scan};
})();
