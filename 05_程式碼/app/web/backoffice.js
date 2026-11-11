/* 管理後台：統計、對話紀錄、發布公告、發布紀錄。demo 頁右側面板和 /admin 全螢幕版共用這一份。
   金鑰由資料庫驗證；只存在這個瀏覽器分頁（sessionStorage），兩個頁面共用。 */
(() => {
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const ROUTE = {rag: "AI 回答", menu: "選單", no_data: "查無資料", handoff: "轉真人", chat: "閒聊", setting: "設定身分",
                 llm_error: "AI 出錯", error: "系統錯誤", ai_off: "AI 關閉時", limited: "超過上限"};
  const ROUTE_CLASS = {no_data: "warn", limited: "warn", ai_off: "warn", handoff: "crit", llm_error: "crit", error: "crit"};
  const STATE = {ok: ["ok", "正常"], warn: ["warn", "注意"], crit: ["crit", "異常"]};
  const fmt = iso => iso ? new Date(iso).toLocaleString("zh-TW", {month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false}) : "—";
  const pct = (a, b) => b ? Math.round(a / b * 100) : 0;

  let key = "";
  const loaders = [];  // 輸入金鑰後，同一頁上的統計和紀錄一起重新載入
  try { key = sessionStorage.getItem("admin_key") || ""; } catch {}
  const m = location.hash.match(/key=([^&]+)/);
  if (m) { key = decodeURIComponent(m[1]); history.replaceState(null, "", location.pathname); }
  const saveKey = () => { try { sessionStorage.setItem("admin_key", key); } catch {} };

  async function api(path, opts = {}) {
    const res = await fetch(path, {...opts, headers: {"X-Admin-Key": key, "Content-Type": "application/json"}});
    if (res.status === 401) throw Object.assign(new Error("金鑰不對"), {auth: true});
    if (!res.ok) {
      let why = "";
      try { const j = await res.json(); why = typeof j.detail === "string" ? j.detail : ""; } catch {}
      throw new Error(why || (res.status >= 500 ? `伺服器出錯（HTTP ${res.status}），請再按一次；還是不行請截圖給我們` : "HTTP " + res.status));
    }
    saveKey();
    return res.json();
  }

  function table(head, rows, empty) {
    if (!rows.length) return `<p class="bo-small">${empty}</p>`;
    return `<div class="bo-table"><table><thead><tr>${head.map(h => `<th${h.num ? ' class="num"' : ""}>${h.t ?? h}</th>`).join("")}</tr></thead>` +
      `<tbody>${rows.map(r => `<tr>${r.map((c, i) => `<td${head[i].num ? ' class="num"' : ""}>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }

  function shareRows(obj, label = x => x) {
    const rows = Object.entries(obj || {}).sort((a, b) => b[1] - a[1]);
    const total = rows.reduce((s, r) => s + r[1], 0), max = Math.max(1, ...rows.map(r => r[1]));
    return rows.map(([k, v]) => [esc(label(k)), v,
      `<span class="bo-share"><i style="width:${Math.round(v / max * 60)}px"></i>${pct(v, total)}%</span>`]);
  }

  function chart(el, rows) {
    const W = Math.max(260, el.clientWidth || 600), H = 140, L = 28, R = 4, T = 6, B = 20;
    const max = Math.max(4, ...rows.map(r => r.total));
    const raw = max / 3, mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const tick = [1, 2, 5, 10].map(f => f * mag).find(t => t >= raw), top = tick * Math.ceil(max / tick);
    const n = rows.length, slot = (W - L - R) / n, bw = Math.max(3, Math.min(24, slot - 4));
    const y = v => T + (H - T - B) * (1 - v / top);
    let g = "";
    for (let v = 0; v <= top + 1e-9; v += tick)
      g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--bo-line)"/><text class="bo-axis" x="${L - 5}" y="${y(v) + 3.5}" text-anchor="end">${Math.round(v)}</text>`;
    const every = Math.ceil(n / Math.max(3, Math.floor((W - L) / 56)));
    rows.forEach((r, i) => {
      const x = L + slot * i + (slot - bw) / 2, h = y(0) - y(r.total);
      if (h > 0) g += `<rect x="${x}" y="${y(r.total)}" width="${bw}" height="${h}" fill="var(--bo-accent)" rx="1.5"/>`;
      g += `<rect class="hit" data-i="${i}" x="${L + slot * i}" y="${T}" width="${slot}" height="${H - T - B}" fill="transparent"/>`;
      if (i % every === 0 || i === n - 1) {
        const [, mm, dd] = r.day.split("-");
        g += `<text class="bo-axis" x="${x + bw / 2}" y="${H - 5}" text-anchor="middle">${+mm}/${+dd}</text>`;
      }
    });
    el.innerHTML = `<div style="position:relative"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="每日提問數">${g}</svg><div class="bo-tip"></div></div>`;
    const svg = el.querySelector("svg"), tip = el.querySelector(".bo-tip");
    svg.addEventListener("mousemove", e => {
      const hit = e.target.closest(".hit");
      if (!hit) { tip.style.opacity = 0; return; }
      const i = +hit.dataset.i, r = rows[i], s = svg.getBoundingClientRect().width / W;
      tip.textContent = `${r.day}：提問 ${r.total}、AI 回答 ${r.answered}、查不到或出錯 ${r.gaps}`;
      tip.style.left = (L + slot * i + slot / 2) * s + "px"; tip.style.top = y(r.total) * s + "px"; tip.style.opacity = 1;
    });
    svg.addEventListener("mouseleave", () => { tip.style.opacity = 0; });
  }

  function login(el, msg, retry) {
    el.innerHTML = `<div class="bo-login"><p class="bo-muted" style="margin:0">這一區只給國際處和小組看，請輸入管理者金鑰（只存在這個分頁）。</p>
      <input type="password" autocomplete="off" placeholder="管理者金鑰" aria-label="管理者金鑰">
      <button type="button" class="bo-primary">進入</button><div class="bo-err">${esc(msg || "")}</div></div>`;
    const input = el.querySelector("input"), go = () => { key = input.value.trim(); (loaders.length ? loaders : [retry]).forEach(fn => fn()); };
    el.querySelector("button").addEventListener("click", go);
    input.addEventListener("keydown", e => { if (e.key === "Enter") go(); });
  }

  // ---------------------------------------------------------------- 統計
  function Stats(el) {
    let days = 7, data = null;
    async function load() {
      try {
        data = await api(`/api/admin/stats?days=${days}`);
        render();
      } catch (e) {
        if (e.auth) return login(el, key ? "金鑰不對，請重新輸入。" : "", load);
        el.innerHTML = `<p class="bo-err">讀取失敗（${esc(e.message)}），請按重新整理。</p>`;
      }
    }
    function render() {
      const d = data, c = d.checks, h = d.health_24h, t = d.totals, r = d.routes || {};
      const errRate = h.ai_calls ? h.llm_error / h.ai_calls : 0, p90 = d.latency?.p90_ms || 0;
      const status = [
        ["網站後端", "ok", "有回應"],
        ["資料庫", c.database.ok ? "ok" : "crit", c.database.detail],
        ["向量模型", c.embedder.ok ? (c.embedder.ms > 8000 ? "warn" : "ok") : "crit", c.embedder.ok ? `${(c.embedder.ms / 1000).toFixed(1)} 秒` : c.embedder.detail],
        ["檢索", c.retrieval.ok ? "ok" : "crit", c.retrieval.ok ? `測試題找到 ${c.retrieval.detail}` : c.retrieval.detail],
        ["Claude API", c.claude.ok ? "ok" : "crit", c.claude.ok ? "金鑰有效" : c.claude.detail],
        ["近 24 小時錯誤", errRate >= 0.2 ? "crit" : (errRate > 0 || h.error) ? "warn" : "ok", `AI 出錯 ${h.llm_error}／${h.ai_calls}、系統錯誤 ${h.error}`],
        ["回應速度", p90 > 10000 ? "warn" : "ok", d.latency?.n ? `平均 ${(d.latency.avg_ms / 1000).toFixed(1)} 秒，九成在 ${(p90 / 1000).toFixed(1)} 秒內` : "期間內沒有 AI 回答"],
        ["LINE", c.line.ok ? "ok" : "warn", c.line.ok ? "已設定" : "尚未接官方帳號"],
      ].map(([n, s, dtl]) => [esc(n), `<span class="${STATE[s][0]}">● ${STATE[s][1]}</span>`, `<span class="bo-muted">${esc(dtl)}</span>`]);
      const on = d.settings.ai_enabled !== "off", cap = +d.settings.daily_cap_site || 300;
      const askable = t.messages - (r.chat || 0) - (r.setting || 0);
      const summary = [
        ["提問數", t.messages, `今天 ${t.today}`],
        ["使用人數", t.users, "以帳號或 IP 雜湊計算"],
        ["AI 回答率", pct(t.answered, askable) + "%", `${t.answered}／${askable}，不含閒聊、設定身分`],
        ["查不到資料", t.no_data, "見下方知識缺口"],
        ["轉真人", t.handoff, "受傷、心理、糾紛等"],
        ["知識庫", d.kb.active.toLocaleString(), `段可用，最新 ${d.kb.newest || "—"}`],
      ].map(([k, v, n]) => [k, `<b>${esc(v)}</b>`, `<span class="bo-muted">${esc(n)}</span>`]);
      el.innerHTML = `
        <div class="bo-controls"><select aria-label="統計期間">${[1, 7, 14, 30].map(n => `<option value="${n}"${n === days ? " selected" : ""}>${n === 1 ? "今天" : "近 " + n + " 天"}</option>`).join("")}</select>
          <button type="button" data-act="reload">重新整理</button><span class="bo-small">更新於 ${fmt(d.now)}・最後一則提問 ${fmt(d.last_message_at)}</span></div>
        <div class="bo-ai"><span>AI 回答：<b class="${on ? "ok" : "warn"}">${on ? "開啟中" : "已關閉"}</b>
          <span class="bo-muted">・今天已用 ${d.llm_today}／${cap} 次，每人每天 ${d.settings.daily_cap_user} 次</span></span>
          <span data-ai><button type="button" data-act="ai">${on ? "關閉 AI 回答" : "重新開啟"}</button></span></div>
        <p class="bo-small" style="margin:4px 0 0">關閉後學生仍會收到官方頁面連結和承辦人電話，不會看到錯誤。</p>
        <div class="bo-twocol">
          <div><h3>系統狀態</h3>${table(["項目", "狀態", "說明"], status)}</div>
          <div><h3>摘要</h3>${table(["指標", {t: "數值", num: true}, "說明"], summary)}</div>
        </div>
        <h3>每日提問數</h3><div class="bo-chart" data-chart></div>
        <div class="bo-twocol">
          <div><h3>問題主題</h3>${table(["主題", {t: "次數", num: true}, "占比"], shareRows(d.topics), "期間內沒有提問")}</div>
          <div><h3>處理方式</h3>${table(["方式", {t: "次數", num: true}, "占比"], shareRows(r, k => ROUTE[k] || k), "期間內沒有提問")}</div>
        </div>
        <h3>知識缺口：查不到資料的問題</h3>
        ${table(["時間", "問題"], (d.gaps || []).map(g => [`<span class="bo-muted">${fmt(g.at)}</span>`, esc(g.q)]), "期間內沒有查不到的問題")}
        <p class="bo-small">建議補成 FAQ，或請承辦人確認官網資料。</p>
        <h3>轉真人</h3>
        ${table(["類別", {t: "次數", num: true}], Object.entries(d.handoffs || {}).map(([k, v]) => [esc(k), v]), "期間內沒有轉真人")}
        <p class="bo-small">只記類別和次數，不顯示學生寫的內容。系統檢查不會讓 Claude 產生回答，不花費用。</p>`;
      chart(el.querySelector("[data-chart]"), d.daily);
      el.querySelector("select").addEventListener("change", e => { days = +e.target.value; load(); });
      el.querySelector('[data-act="reload"]').addEventListener("click", load);
      el.querySelector('[data-act="ai"]').addEventListener("click", () => {
        const box = el.querySelector("[data-ai]"), next = on ? "off" : "on";
        box.innerHTML = `${on ? "確定要關閉？" : "確定要開啟？"} <button type="button" class="bo-primary" data-yes>確定</button> <button type="button" data-no>取消</button>`;
        box.querySelector("[data-no]").addEventListener("click", render);
        box.querySelector("[data-yes]").addEventListener("click", async () => {
          try { d.settings = await api("/api/admin/setting", {method: "POST", body: JSON.stringify({name: "ai_enabled", value: next})}); render(); }
          catch (e) { box.innerHTML = `<span class="bo-err">切換失敗（${esc(e.message)}）</span>`; }
        });
      });
    }
    loaders.push(load);
    return {load, redraw: () => data && chart(el.querySelector("[data-chart]"), data.daily)};
  }

  // ---------------------------------------------------------------- 對話紀錄
  function Messages(el) {
    const f = {days: 7, route: "", q: ""};
    let rows = [], total = 0, timer;
    async function load(reset = true) {
      try {
        const qs = new URLSearchParams({days: f.days, route: f.route, q: f.q, limit: 30, offset: reset ? 0 : rows.length});
        const r = await api(`/api/admin/messages?${qs}`);
        rows = reset ? r.rows : rows.concat(r.rows); total = r.total;
        render();
      } catch (e) {
        if (e.auth) return login(el, key ? "金鑰不對，請重新輸入。" : "", () => load(true));
        el.innerHTML = `<p class="bo-err">讀取失敗（${esc(e.message)}），請按重新整理。</p>`;
      }
    }
    function render() {
      if (!el.querySelector("[data-list]")) {
        el.innerHTML = `<div class="bo-controls">
            <select data-f="days" aria-label="期間"><option value="1">今天</option><option value="7" selected>近 7 天</option><option value="30">近 30 天</option></select>
            <select data-f="route" aria-label="處理方式"><option value="">全部處理方式</option><option value="answered">AI 回答＋選單</option>
              <option value="problem">查無資料、出錯、超過上限</option><option value="chat">閒聊</option><option value="handoff">轉真人</option><option value="setting">設定身分</option></select>
            <input type="search" data-f="q" placeholder="搜尋問題或回答" aria-label="搜尋">
            <button type="button" data-act="reload">重新整理</button></div>
          <p class="bo-small" data-count></p><div data-list></div>`;
        el.querySelectorAll("select[data-f]").forEach(s => s.addEventListener("change", () => { f[s.dataset.f] = s.value; load(); }));
        el.querySelector("input[data-f]").addEventListener("input", e => { clearTimeout(timer); timer = setTimeout(() => { f.q = e.target.value.trim(); load(); }, 350); });
        el.querySelector('[data-act="reload"]').addEventListener("click", () => load());
      }
      el.querySelector("[data-count]").textContent = `共 ${total} 筆，顯示 ${rows.length} 筆。點一列看完整回答和來源。`;
      const list = el.querySelector("[data-list]");
      if (!rows.length) { list.innerHTML = '<p class="bo-small">這個條件下沒有紀錄。</p>'; return; }
      list.innerHTML = `<div class="bo-table"><table><thead><tr><th>時間</th><th>身分</th><th>問題</th><th>處理</th></tr></thead><tbody>${
        rows.map((r, i) => `<tr class="bo-row" data-i="${i}" tabindex="0"><td class="bo-muted" style="white-space:nowrap">${fmt(r.created_at)}</td>
          <td class="bo-muted" style="white-space:nowrap">${esc(r.identity || "—")}</td>
          <td class="q">${r.question == null ? '<span class="bo-muted">（轉真人，內容不顯示）</span>' : esc(r.question)}</td>
          <td style="white-space:nowrap" class="${ROUTE_CLASS[r.route] || ""}">${esc(ROUTE[r.route] || r.route)}</td></tr>`).join("")
      }</tbody></table></div>${rows.length < total ? '<button type="button" class="bo-more" data-act="more">再載入 30 筆</button>' : ""}`;
      list.querySelectorAll("tr.bo-row").forEach(tr => {
        const toggle = () => {
          const next = tr.nextElementSibling;
          if (next && next.classList.contains("bo-detail")) { next.remove(); tr.classList.remove("open"); return; }
          const r = rows[+tr.dataset.i], src = r.sources || [];
          const d = document.createElement("tr");
          d.className = "bo-detail";
          d.innerHTML = `<td colspan="4">${r.answer == null ? '<p class="bo-muted">轉真人的對話不在後台顯示內容。</p>' :
            `<div class="bo-answer">${esc(r.answer)}</div>`}
            ${src.length ? `<div class="bo-small">用到的資料：</div><ol class="bo-sources">${src.map(s =>
              `<li>${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || s.url)}</a>` : esc(s.title)} <span class="bo-small">資料更新 ${esc(s.updated || "—")}</span></li>`).join("")}</ol>` : ""}
            <div class="bo-small">回應 ${r.latency_ms != null ? (r.latency_ms / 1000).toFixed(1) + " 秒" : "—"}・${r.channel === "line" ? "LINE" : "網頁"}</div></td>`;
          tr.after(d); tr.classList.add("open");
        };
        tr.addEventListener("click", toggle);
        tr.addEventListener("keydown", e => { if (e.key === "Enter") toggle(); });
      });
      const more = list.querySelector('[data-act="more"]');
      if (more) more.addEventListener("click", () => load(false));
    }
    loaders.push(() => load(true));
    return {load};
  }

  // ---------------------------------------------------------------- IG 圖卡（照國際處現有 IG 模板畫；API 還沒串接，下載後手動發文）
  const IG = (() => {
    const W = 1080, H = 1350;
    const NAVY = "#2f4a78", INK = "#1b1b1b", GREY = "#4d5a63";
    const TITLE = "'Baloo 2', 'Noto Sans TC', 'PingFang TC', 'Microsoft JhengHei', sans-serif";
    const HAND = "Gaegu, 'Noto Sans TC', 'PingFang TC', 'Microsoft JhengHei', sans-serif";
    const SANS = "'Noto Sans TC', 'PingFang TC', 'Microsoft JhengHei', sans-serif";
    const THEMES = {blue: {bg: "#ebf8fe", panel: "#faf5df", dots: ["#ebf8fe", "#faf5df"]},
                    cream: {bg: "#faf5df", panel: "#ebf8fe", dots: ["#faf5df", "#ebf8fe"]}};
    let cssReady = null;
    const imgs = {};
    function css() {  // 第一次用到才載入手寫字型；載不到就用系統字型，不影響產生圖卡
      if (cssReady) return cssReady;
      cssReady = new Promise(res => {
        const l = document.createElement("link");
        l.rel = "stylesheet"; l.onload = l.onerror = () => res();
        l.href = "https://fonts.googleapis.com/css2?family=Baloo+2:wght@700;800&family=Gaegu:wght@400;700&family=Noto+Sans+TC:wght@400;500;700;900&display=swap";
        document.head.appendChild(l);
        setTimeout(res, 2500);
      });
      return cssReady;
    }
    async function fonts(text) {
      await css();
      const t = text || "Aa";
      await Promise.all([`800 80px 'Baloo 2'`, `700 40px Gaegu`, `400 40px Gaegu`, `900 40px 'Noto Sans TC'`, `700 40px 'Noto Sans TC'`, `500 40px 'Noto Sans TC'`]
        .map(f => document.fonts?.load(f, t).catch(() => {}))).catch(() => {});
    }
    const img = src => imgs[src] ||= new Promise(r => { const i = new Image(); i.onload = () => r(i); i.onerror = () => r(null); i.src = src; });

    // 斷行：英文以單字為單位，中文一個字一個字斷
    const tokens = s => s.match(/[A-Za-z0-9@#&'’.,:;!?()\-\/%+$]+\s*|\s+|./gu) || [];
    function wrap(ctx, text, maxW, maxLines = 99) {
      const out = [];
      for (const para of String(text || "").split(/\n/)) {
        let cur = "";
        for (const t of tokens(para)) {
          if (/^[，。、；：！？）」』】,.!?;:)]/.test(t)) { cur += t; continue; }  // 標點不放行首
          if (cur && ctx.measureText(cur + t.trimEnd()).width > maxW) { out.push(cur.trimEnd()); cur = t.trimStart(); }
          else cur += t;
        }
        out.push(cur.trimEnd());
      }
      const lines = out.filter((l, i) => l || i < out.length - 1);
      if (lines.length > maxLines) {
        lines.length = maxLines;
        let last = lines[maxLines - 1];
        while (last && ctx.measureText(last + "…").width > maxW) last = last.slice(0, -1);
        lines[maxLines - 1] = last + "…";
      }
      return lines;
    }
    function fit(ctx, text, {w, lines, max, min, weight, family}) {
      for (let s = max; s >= min; s -= 2) {
        ctx.font = `${weight} ${s}px ${family}`;
        const ls = wrap(ctx, text, w);
        if (ls.length <= lines) return {size: s, ls};
      }
      ctx.font = `${weight} ${min}px ${family}`;
      return {size: min, ls: wrap(ctx, text, w, lines)};
    }
    function rr(ctx, x, y, w, h, r) { ctx.beginPath(); ctx.roundRect ? ctx.roundRect(x, y, w, h, r) : ctx.rect(x, y, w, h); }
    function center(ctx, ls, cx, y, lh) { ls.forEach((l, i) => ctx.fillText(l, cx, y + i * lh)); return y + ls.length * lh; }

    // 小圖示（橘色系，對應海報模板的日曆、時鐘、定位點）
    function icon(ctx, kind, cx, cy) {
      const O = "#e8926f";
      ctx.save(); ctx.lineWidth = 4; ctx.strokeStyle = O; ctx.fillStyle = O;
      if (kind === "date") {
        ctx.beginPath(); ctx.arc(cx, cy, 24, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = "#fff"; rr(ctx, cx - 12, cy - 10, 24, 22, 3); ctx.fill();
        ctx.fillStyle = O; ctx.fillRect(cx - 12, cy - 10, 24, 6);
        for (let i = 0; i < 3; i++) for (let j = 0; j < 2; j++) ctx.fillRect(cx - 8 + i * 6.5, cy - 1 + j * 6, 4, 3.5);
      } else if (kind === "time") {
        ctx.beginPath(); ctx.arc(cx, cy, 21, 0, Math.PI * 2); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(cx, cy - 12); ctx.lineTo(cx, cy); ctx.lineTo(cx + 9, cy + 6); ctx.stroke();
      } else {
        ctx.beginPath(); ctx.moveTo(cx, cy + 26);
        ctx.bezierCurveTo(cx - 6, cy + 12, cx - 20, cy, cx - 20, cy - 10);
        ctx.arc(cx, cy - 10, 20, Math.PI, 0); ctx.bezierCurveTo(cx + 20, cy, cx + 6, cy + 12, cx, cy + 26); ctx.fill();
        ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(cx, cy - 10, 7, 0, Math.PI * 2); ctx.fill();
      }
      ctx.restore();
    }

    async function poster(ctx, c, a, illus) {
      ctx.fillStyle = "#e1f2fc"; ctx.fillRect(0, 0, W, H);
      const logo = await img("/web/ig-logo.jpg");
      if (logo) ctx.drawImage(logo, (W - 340) / 2, 40, 340, 340 * logo.height / logo.width);
      const X = 108, Y = 196, CW = 864, CH = 968;
      ctx.fillStyle = "#fff"; rr(ctx, X, Y, CW, CH, 26); ctx.fill();
      ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
      let y = Y + 54;
      if (c.kicker) {  // 標題上面的小標籤（模板裡灰藍色圓角框那一行）
        ctx.font = `800 44px ${TITLE}`;
        const tw = Math.min(ctx.measureText(c.kicker).width, 700);
        ctx.fillStyle = "#dde4ef"; rr(ctx, W / 2 - tw / 2 - 34, y, tw + 68, 84, 20); ctx.fill();
        ctx.fillStyle = NAVY; ctx.fillText(c.kicker, W / 2, y + 58, 700);
        y += 84 + 30;
      }
      const t = fit(ctx, c.title || "（圖卡標題）", {w: 760, lines: 3, max: 84, min: 44, weight: 800, family: TITLE});
      ctx.fillStyle = NAVY;
      y = center(ctx, t.ls, W / 2, y + t.size, t.size * 1.22) - t.size * 0.22 + 40;
      const rows = [["date", c.date], ["time", c.time], ["place", c.place]].filter(r => (r[1] || "").trim());
      if (rows.length) {
        ctx.font = `700 36px ${HAND}`;
        const bw = Math.min(760, Math.max(460, ...rows.map(r => ctx.measureText(r[1]).width + 170)));
        const bh = 40 + rows.length * 72, bx = (W - bw) / 2;
        ctx.fillStyle = "#edeae1"; ctx.fillRect(bx, y, bw, bh);
        const tape = (x, yy, ang) => { ctx.save(); ctx.translate(x, yy); ctx.rotate(ang); ctx.fillStyle = "rgba(214,202,172,.85)"; ctx.fillRect(-52, -14, 104, 28); ctx.restore(); };
        tape(bx + bw - 10, y + 6, 0.5); tape(bx + 14, y + bh - 4, 0.5);
        ctx.textAlign = "left"; ctx.fillStyle = NAVY;
        rows.forEach(([k, v], i) => {
          const cy = y + 20 + 36 + i * 72;
          icon(ctx, k, bx + 62, cy);
          ctx.fillStyle = NAVY; ctx.font = `700 36px ${HAND}`; ctx.fillText(v, bx + 112, cy + 12, bw - 140);
        });
        ctx.textAlign = "center";
        y += bh + 40;
      }
      const bottom = Y + CH - 40, room = bottom - y;
      if (illus && room > 120) {  // 有上傳插圖就放插圖（模板裡的人物插畫位置）
        const s = Math.min(760 / illus.width, room / illus.height);
        ctx.drawImage(illus, (W - illus.width * s) / 2, y + (room - illus.height * s) / 2, illus.width * s, illus.height * s);
      } else if (room > 60) {  // 沒插圖就放公告內容：中文優先，剩下的空間放英文
        ctx.fillStyle = "#43556b";
        const zhMax = Math.max(1, Math.floor((a.summary_en && room > 260 ? room * 0.55 : room) / 50));
        const zh = fit(ctx, a.summary_zh || "", {w: 720, lines: zhMax, max: 34, min: 26, weight: 500, family: SANS});
        center(ctx, zh.ls, W / 2, y + zh.size, zh.size * 1.5);
        const end = y + zh.ls.length * zh.size * 1.5 + 18;
        const enMax = Math.floor((bottom - end) / 38);
        if (a.summary_en && enMax >= 1) {
          ctx.fillStyle = "#6b7a8c";
          const en = fit(ctx, a.summary_en, {w: 720, lines: enMax, max: 28, min: 22, weight: 500, family: SANS});
          center(ctx, en.ls, W / 2, end + en.size, en.size * 1.38);
        }
      }
    }

    function glyph(ctx, kind, cx, cy) {  // 生活指南卡底下四顆按鈕的圖示
      ctx.save(); ctx.fillStyle = INK; ctx.beginPath();
      if (kind === 0) {  // 愛心
        ctx.moveTo(cx, cy + 24); ctx.bezierCurveTo(cx - 40, cy - 4, cx - 22, cy - 34, cx, cy - 14);
        ctx.bezierCurveTo(cx + 22, cy - 34, cx + 40, cy - 4, cx, cy + 24);
      } else if (kind === 1) {  // 對話框
        ctx.arc(cx, cy - 2, 26, 0, Math.PI * 2); ctx.moveTo(cx - 22, cy + 10); ctx.lineTo(cx - 28, cy + 28); ctx.lineTo(cx - 8, cy + 20);
      } else if (kind === 2) {  // 紙飛機
        ctx.moveTo(cx - 30, cy - 18); ctx.lineTo(cx + 30, cy - 26); ctx.lineTo(cx + 2, cy + 30); ctx.lineTo(cx - 4, cy + 2); ctx.closePath();
        ctx.fill(); ctx.strokeStyle = "#fcfbf6"; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(cx - 4, cy + 2); ctx.lineTo(cx + 26, cy - 23); ctx.stroke();
        ctx.restore(); return;
      } else {  // 書籤
        ctx.moveTo(cx - 18, cy - 28); ctx.lineTo(cx + 18, cy - 28); ctx.lineTo(cx + 18, cy + 28); ctx.lineTo(cx, cy + 14); ctx.lineTo(cx - 18, cy + 28); ctx.closePath();
      }
      ctx.fill(); ctx.restore();
    }

    async function guide(ctx, c) {
      const th = THEMES[c.theme] || THEMES.blue;
      ctx.fillStyle = th.bg; ctx.fillRect(0, 0, W, H);
      const X = 84, Y = 78, CW = 912, CH = 1184, PANEL = 886;
      rr(ctx, X, Y, CW, CH, 64); ctx.fillStyle = "#fcfbf6"; ctx.fill();
      ctx.save(); rr(ctx, X, Y, CW, CH, 64); ctx.clip();
      ctx.fillStyle = th.panel; ctx.fillRect(X, PANEL, CW, Y + CH - PANEL); ctx.restore();
      ctx.lineWidth = 4; ctx.strokeStyle = INK; rr(ctx, X, Y, CW, CH, 64); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(X, PANEL); ctx.lineTo(X + CW, PANEL); ctx.stroke();
      ctx.textAlign = "center"; ctx.fillStyle = "#5d6670"; ctx.font = `400 38px ${SANS}`;
      ctx.fillText("@ncuoia.lifeguide", W / 2, 150);
      ctx.lineWidth = 2.5; ctx.beginPath(); ctx.moveTo(135, 190); ctx.lineTo(945, 190); ctx.stroke();
      const t = fit(ctx, c.title || "（圖卡標題）", {w: 790, lines: 2, max: 96, min: 54, weight: 800, family: TITLE});
      ctx.fillStyle = INK;
      let y = center(ctx, t.ls, W / 2, 236 + t.size, t.size * 1.12) + 28;
      const pts = (c.points || "").split("\n").map(s => s.trim()).filter(Boolean).slice(0, 4);
      const LIMIT = PANEL - 128;  // 圓形標誌上緣，重點不能壓到
      const height = (ls, sz) => ls.reduce((s, l) => s + l.length * sz * 1.25 + 24, 0);
      let size = 50, layout = [];
      for (; size >= 30; size -= 2) {  // 重點太多就把字縮小
        ctx.font = `700 ${size}px ${HAND}`;
        layout = pts.map(p => wrap(ctx, p, 640, 2));
        if (y + height(layout, size) <= LIMIT) break;
      }
      size = Math.max(size, 30);
      while (layout.length > 1 && y + height(layout, size) > LIMIT) layout.pop();  // 還是太多就只放前幾點
      ctx.textAlign = "left";
      layout.forEach((ls, i) => {
        const cy = y + size * 0.65;
        ctx.beginPath(); ctx.arc(196, cy - size * 0.32, 22, 0, Math.PI * 2);
        ctx.fillStyle = th.dots[i % 2]; ctx.fill(); ctx.lineWidth = 2; ctx.strokeStyle = "#9aa3a8"; ctx.stroke();
        ctx.fillStyle = GREY; ctx.font = `700 ${size}px ${HAND}`;
        ls.forEach((l, j) => ctx.fillText(l, 248, cy + j * size * 1.25));
        y += ls.length * size * 1.25 + 24;
      });
      // 中間的國際處圓形標誌
      const badge = await img("/web/ig-badge.jpg");
      ctx.beginPath(); ctx.arc(W / 2, PANEL, 108, 0, Math.PI * 2); ctx.fillStyle = "#fcfbf6"; ctx.fill();
      ctx.lineWidth = 4; ctx.strokeStyle = INK; ctx.stroke();
      if (badge) { ctx.save(); ctx.beginPath(); ctx.arc(W / 2, PANEL, 96, 0, Math.PI * 2); ctx.clip(); ctx.drawImage(badge, W / 2 - 96, PANEL - 96, 192, 192); ctx.restore(); }
      ["Like", "Comment", "Share", "Save"].forEach((label, i) => {
        const bx = 136 + i * 212, by = 1046;
        rr(ctx, bx, by, 184, 168, 28); ctx.fillStyle = "#fcfbf6"; ctx.fill(); ctx.lineWidth = 3.5; ctx.strokeStyle = INK; ctx.stroke();
        glyph(ctx, i, bx + 92, by + 64);
        ctx.textAlign = "center"; ctx.fillStyle = INK; ctx.font = `500 32px ${SANS}`; ctx.fillText(label, bx + 92, by + 140);
      });
    }

    async function draw(canvas, c, a = {}, illus = null) {
      canvas.width = W; canvas.height = H;
      await fonts([c.title, c.kicker, c.date, c.time, c.place, c.points, a.summary_zh, a.summary_en, "@ncuoia.lifeguide LikeCommentShareSave"].join(""));
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, W, H);
      await (c.tpl === "guide" ? guide(ctx, c) : poster(ctx, c, a, illus));
      return canvas;
    }
    async function download(c, a, illus) {
      const cv = await draw(document.createElement("canvas"), c, a, illus);
      const blob = await new Promise(r => cv.toBlob(r, "image/png"));
      const url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url; link.download = `IG圖卡_${(c.title || "公告").replace(/[\\/:*?"<>|\s]+/g, "_").slice(0, 30)}.png`;
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
    }
    async function copy(text) {
      try { await navigator.clipboard.writeText(text); return true; }
      catch {
        const t = document.createElement("textarea"); t.value = text; document.body.appendChild(t); t.select();
        const ok = document.execCommand("copy"); t.remove(); return ok;
      }
    }
    // 依公告內容產生預設值；承辦人改過的欄位就不再自動帶
    function defaults(f) {
      const dl = f.deadline ? new Date(f.deadline + "T00:00:00") : null;
      const dlTxt = dl && !isNaN(dl) ? dl.toLocaleDateString("en-GB", {weekday: "short", day: "numeric", month: "short", year: "numeric"}) : "";
      const zhPts = (f.summary_zh || "").split(/(?<=[。！？!?；])\s*/).map(s => s.replace(/[。；]$/, "").trim()).filter(Boolean).slice(0, 3);
      return {
        title: f.title || "", kicker: "OIA Notice", date: dlTxt ? `Deadline · ${dlTxt}` : "", time: "", place: "",
        points: [...zhPts, ...(f.deadline ? [`截止 Deadline：${md(f.deadline)}`] : [])].slice(0, 4).join("\n"),
        caption: [f.title, f.summary_zh, f.summary_en, f.deadline ? `截止 Deadline：${f.deadline}` : "",
                  "完整公告請看國際處 LINE 官方帳號（IG 貼文裡的連結點不開）\nFull notice: OIA LINE official account",
                  "#NCU #國立中央大學 #NCUOIA #InternationalStudents"].filter(Boolean).join("\n\n"),
      };
    }
    return {draw, download, copy, defaults};
  })();

  // ---------------------------------------------------------------- 發布公告（LINE 為主；Email、IG 圖卡、知識庫用勾選）
  const AUD = ["全體境外生", "外籍學位生", "交換生", "僑生", "陸生"];
  const todayStr = () => new Date().toLocaleDateString("sv-SE", {timeZone: "Asia/Taipei"});
  const md = d => d ? `${+d.slice(5, 7)}/${+d.slice(8, 10)}` : "";
  const SRC = {email: "Email 帶入", paste: "貼上信件", line: "後台直接寫"};
  // 每則公告各管道的狀態：[樣式, 文字]
  function chState(a) {
    if (a.status === "skipped") return {line: ["", "不發"], email: ["", "不發"], ig: ["", "—"], kb: ["", "—"]};
    if (a.status !== "published") return {line: ["warn", "待發布"], email: ["", "—"], ig: ["", "—"], kb: ["", "—"]};
    const c = a.channels || {}, lr = c.line || a.line_result || {};
    const line = lr.mode === "broadcast" ? ["ok", "✓ 已送出"] : lr.mode === "demo" ? ["ok", "✓ 已送出（模擬）"]
               : lr.mode === "error" ? ["crit", "✕ 失敗"] : lr.mode === "off" ? ["", "沒勾"] : ["", "—"];
    const e = c.email || {};
    const email = e.mode === "demo" ? ["ok", "✓ 已寄出（模擬）"] : e.mode === "sent" ? ["ok", "✓ 已寄出"]
                : e.mode === "error" ? ["crit", "✕ 失敗"] : e.mode === "off" ? ["", "沒勾"] : ["", "—"];
    const k = c.kb || {};
    const kb = k.status === "removed" ? ["", "已下架"] : k.mode === "off" ? ["", "沒勾"]
             : !a.kb_chunks ? ["", "—"]
             : a.deadline && a.deadline < todayStr() ? ["", "已過期停用"]
             : ["ok", a.deadline ? `有效至 ${md(a.deadline)}` : "有效（沒有截止日）"];
    const g = c.ig || {};
    const ig = g.mode === "manual" ? ["ok", "✓ 圖卡已產生"] : g.mode === "off" ? ["", "沒勾"] : g.mode === "paused" ? ["", "暫緩"] : ["", "—"];
    return {line, email, ig, kb};
  }
  const st = ([c, t]) => `<span class="${c}">${esc(t)}</span>`;

  // opts.onPublished(a)：發布成功後呼叫（demo 頁用它把卡片放進模擬手機）；opts.openHistory()：切到發布紀錄
  function Publish(el, opts = {}) {
    let list = [], cur = null, done = null, importOpen = false, pv = "line", msg = "", busy = false;
    let ch = {line: true, email: true, ig: false, kb: true};
    let mail = null;  // 寄信設定（遮住的收件人），用來顯示 Email 勾選說明和預覽
    let ig = {tpl: "poster", theme: "blue"}, igDirty = {}, illus = null, igTimer = null;
    const IG_KEYS = ["title", "kicker", "date", "time", "place", "points", "caption"];
    const igCard = () => { const d = IG.defaults(form); const c = {tpl: ig.tpl, theme: ig.theme};
                           IG_KEYS.forEach(k => c[k] = igDirty[k] ? (ig[k] ?? "") : d[k]); return c; };
    let form = {title: "", summary_zh: "", summary_en: "", deadline: "", audience: AUD[0]};
    const fromItem = a => ({title: a.title || "", summary_zh: a.summary_zh || "", summary_en: a.summary_en || "",
                            deadline: a.deadline || "", audience: a.audience || AUD[0]});
    async function load() {
      try {
        list = await api("/api/admin/announce");
        if (!mail) mail = await api("/api/admin/email-config").catch(() => ({on: false}));
        if (cur) cur = list.find(a => a.id === cur.id && a.status === "pending") || null;
        render();
      } catch (e) {
        if (e.auth) return login(el, key ? "金鑰不對，請重新輸入。" : "", load);
        el.innerHTML = `<p class="bo-err">讀取失敗（${esc(e.message)}），請重新整理。</p>`;
      }
    }
    function readForm() {
      const v = id => el.querySelector(id)?.value;
      if (!el.querySelector("#bo-title")) return;
      form = {title: v("#bo-title"), summary_zh: v("#bo-zh"), summary_en: v("#bo-en"), deadline: v("#bo-due"), audience: v("#bo-aud")};
    }
    async function act(fn, after) {
      if (busy) return;
      readForm();
      busy = true; msg = ""; render();
      try { await after(await fn()); } catch (e) { msg = `<span class="crit">失敗：${esc(e.message)}</span>`; }
      busy = false; render();
    }
    function render() {
      el.innerHTML = `<div class="bo-pub"><div class="bo-main">${done ? resultView(done) : composeView()}</div>
        <aside class="bo-side" aria-label="各管道預覽">${sideView()}</aside></div>`;
      wire();
      summary(); preview();
    }
    function composeView() {
      const pending = list.filter(a => a.status === "pending");
      const rows = pending.map(a => `<tr><td style="white-space:nowrap">${fmt(a.received_at)}</td>
          <td class="q">${esc(a.subject)}${a.is_sample ? ' <span class="bo-small">範例</span>' : ""}</td>
          <td>${esc(a.sender || "—")}</td>
          <td style="text-align:right"><button type="button" data-pick="${a.id}">帶入</button></td></tr>`).join("");
      const checks = (cur?.checks || []).map(c => `<li class="${c.level === "ok" ? "ok" : c.level === "warn" ? "warn" : ""}">${esc(c.text)}</li>`).join("");
      return `
        <div class="bo-head">
          <div><h3 style="margin:0">發布 LINE 公告</h3>
            <p class="bo-small" style="margin:2px 0 0">LINE 是主要管道。寫好之後，在下面勾選要不要同時寄 Email、放進小幫手知識庫。</p></div>
          <button type="button" data-act="import" aria-expanded="${importOpen}" aria-controls="bo-import">從 Email 帶入（${pending.length} 封待處理）</button>
        </div>
        <div class="bo-import" id="bo-import" ${importOpen ? "" : "hidden"}>
          <div class="bo-actions" style="margin:0;align-items:center"><p class="bo-small" style="margin:0;flex:1">寄群發信時密件副本給小幫手信箱${mail?.inbox ? `（${esc(mail.inbox)}）` : ""}，按「檢查信箱」就會帶進來。按「帶入」，AI 改寫好的內容會填進下面。</p>
            <button type="button" data-act="fetchmail">檢查信箱</button></div>
          ${pending.length ? `<div class="bo-table"><table><thead><tr><th>收到</th><th>信件主旨</th><th>寄件人</th><th><span class="bo-sr">動作</span></th></tr></thead><tbody>${rows}</tbody></table></div>`
            : '<p class="bo-small" style="margin:0">目前沒有待處理的信。</p>'}
          <details><summary>或貼上信件內容</summary>
            <div class="bo-form">
              <label for="bo-subject">信件主旨</label><input id="bo-subject" type="text" placeholder="例如：【國際處】寒假留宿申請開始">
              <label for="bo-raw">信件內容</label><textarea id="bo-raw" rows="5" placeholder="把要寄給學生的 Email 內容貼在這裡"></textarea>
              <button type="button" class="bo-primary" data-act="draft">AI 改寫成 LINE 公告</button>
            </div></details>
        </div>
        ${cur ? `<div class="bo-from"><span>來源：${esc(SRC[cur.source] || "Email")}「${esc(cur.subject)}」</span>
            <button type="button" data-act="new">改成重新寫一則</button></div>
          <details class="bo-small"><summary>原信內容</summary><div class="bo-answer">${esc(cur.raw_body)}</div></details>` : ""}
        <div class="bo-form">
          <label for="bo-title">標題</label><input id="bo-title" type="text" value="${esc(form.title)}" placeholder="例如：期末考週圖書館延長開放">
          <div class="bo-grid2">
            <div><label for="bo-zh">中文（120 字內）</label><textarea id="bo-zh" rows="4">${esc(form.summary_zh)}</textarea></div>
            <div><div class="bo-lab"><label for="bo-en">英文</label><button type="button" class="bo-link" data-act="assist">AI 幫我翻</button></div>
              <textarea id="bo-en" rows="4">${esc(form.summary_en)}</textarea></div>
          </div>
          <div class="bo-grid2">
            <div><label for="bo-due">截止日（沒有可以空著）</label><input id="bo-due" type="date" value="${esc(form.deadline)}"></div>
            <div><label for="bo-aud">發給誰</label><select id="bo-aud">${AUD.map(x => `<option${x === form.audience ? " selected" : ""}>${x}</option>`).join("")}</select></div>
          </div>
        </div>
        ${checks ? `<div class="bo-check"><strong>AI 檢查</strong><ul>${checks}</ul></div>` : ""}
        <fieldset class="bo-fs"><legend>同時發到</legend>
          <div class="bo-chlist">
            <label class="bo-ch main"><input type="checkbox" id="bo-ch-line" ${ch.line ? "checked" : ""}>
              <span><span class="t">LINE 官方帳號 <span class="bo-tag">主要管道</span></span>
              <span class="d">推播卡片訊息，附「查看全文」按鈕（demo 還沒有正式帳號金鑰，只在學生端畫面顯示）</span></span></label>
            <label class="bo-ch"><input type="checkbox" id="bo-ch-email" ${ch.email ? "checked" : ""}>
              <span><span class="t">Email${mail?.on ? ` <span class="bo-tag">${mail.ready ? "會真的寄出" : "設定不完整"}</span>` : ""}</span><span class="d">${mailDesc()}</span></span></label>
            <label class="bo-ch"><input type="checkbox" id="bo-ch-ig" ${ch.ig ? "checked" : ""}>
              <span><span class="t">Instagram 圖卡</span><span class="d">用國際處 IG 模板自動產生圖卡和貼文說明；IG 還沒串接 API，發布後下載圖卡手動發文</span></span></label>
            ${ch.ig ? igForm() : ""}
            <label class="bo-ch"><input type="checkbox" id="bo-ch-kb" ${ch.kb ? "checked" : ""}>
              <span><span class="t">小幫手知識庫</span><span class="d">學生之後問到時小幫手可以回答，截止日後自動停用</span></span></label>
          </div>
        </fieldset>
        <div class="bo-actions">
          <span>會發到：<strong id="bo-sum"></strong></span><span class="sp"></span>
          ${cur ? '<button type="button" data-act="skip">這封不發</button>' : '<button type="button" data-act="clearform">清空</button>'}
          <button type="button" class="bo-primary bo-big" data-act="publish">確認發布</button>
        </div>
        ${busy ? '<p class="bo-small">處理中，AI 改寫或寫入知識庫約需 5 到 15 秒…</p>' : ""}
        ${msg ? `<p class="bo-small">${msg}</p>` : ""}`;
    }
    function mailDesc() {
      if (!mail?.on) return "寄到國際生群組信箱，主旨自動加「【國際處公告】」，內文中英對照加全文連結（demo 為模擬寄出）";
      if (!mail.ready) return "Email 已開啟，但寄件帳號或密碼還沒設定好，按發布會在結果顯示失敗原因";
      return `寄到 ${mail.to.join("、")}${mail.to_count > 3 ? ` 等 ${mail.to_count} 人` : ""}（目前是測試收件人，正式上線改成國際生群組信箱）。主旨自動加「【國際處公告】」，內文中英對照加全文連結；每天最多 ${mail.cap} 則`;
    }
    function igForm() {
      const c = igCard(), v = k => esc(c[k]);
      const inp = (k, label, ph = "") => `<div><label for="ig-${k}">${label}</label><input id="ig-${k}" data-ig="${k}" type="text" value="${v(k)}" placeholder="${ph}"></div>`;
      return `<div class="bo-igform">
        <div class="bo-lab"><strong>IG 圖卡設定</strong><span class="bo-small">右邊「IG」預覽會跟著變</span></div>
        <div class="bo-seg" role="group" aria-label="圖卡版型" style="display:inline-flex;margin:6px 0 2px">
          <button type="button" data-igtpl="poster" aria-pressed="${ig.tpl === "poster"}">活動海報</button>
          <button type="button" data-igtpl="guide" aria-pressed="${ig.tpl === "guide"}">生活指南卡</button></div>
        <div class="bo-form">
          <div class="bo-grid2">${inp("title", "圖卡標題", "預設用公告標題，可以改成英文")}${ig.tpl === "poster" ? inp("kicker", "標題上方小標籤", "例如 OIA Notice") :
            `<div><label for="ig-theme">底色</label><select id="ig-theme">${[["blue", "淺藍"], ["cream", "米黃"]].map(([k, t]) => `<option value="${k}"${ig.theme === k ? " selected" : ""}>${t}</option>`).join("")}</select></div>`}</div>
          ${ig.tpl === "poster" ? `<div class="bo-grid3">${inp("date", "日期", "Wed, 11 Nov 2026")}${inp("time", "時間", "9:00 AM – 5:30 PM")}${inp("place", "地點", "Sanxia, New Taipei City")}</div>
            <div><label for="ig-illus">插圖（選填，放在下半部；沒放就顯示公告內容）</label>
              <input id="ig-illus" type="file" accept="image/png,image/jpeg,image/webp">${illus ? ' <button type="button" class="bo-link" data-act="igclear">拿掉插圖</button>' : ""}</div>`
          : `<div><label for="ig-points">重點（一行一點，最多 4 點）</label><textarea id="ig-points" data-ig="points" rows="4">${v("points")}</textarea></div>`}
          <div><div class="bo-lab"><label for="ig-caption">貼文說明</label><button type="button" class="bo-link" data-act="igreset">全部恢復自動產生</button></div>
            <textarea id="ig-caption" data-ig="caption" rows="5">${v("caption")}</textarea></div>
        </div></div>`;
    }
    function igSync() {  // 公告內容改了，沒手動改過的 IG 欄位跟著更新
      const c = igCard();
      el.querySelectorAll("[data-ig]").forEach(i => { if (!igDirty[i.dataset.ig]) i.value = c[i.dataset.ig]; });
    }
    const igTools = () => `<div class="bo-actions" style="margin-top:8px"><button type="button" class="bo-primary" data-igdl>下載圖卡 PNG</button>
        <button type="button" data-igcopy>複製貼文說明</button><span class="bo-small" data-ignote></span></div>`;
    function wireIgTools(box, c, a) {
      box.querySelector("[data-igdl]")?.addEventListener("click", () => IG.download(c, a, illus));
      box.querySelector("[data-igcopy]")?.addEventListener("click", async () => {
        const ok = await IG.copy(c.caption || "");
        const n = box.querySelector("[data-ignote]"); if (n) n.textContent = ok ? "貼文說明已複製" : "複製失敗，請手動選取";
      });
    }
    function resultView(a) {
      const s = chState(a), c = a.channels || {};
      return `<h3 style="margin-top:0">已發布：${esc(a.title)}</h3>
        <p class="bo-small" style="margin:0 0 10px">${fmt(a.published_at)}｜${esc(a.audience || "全體境外生")}</p>
        <div class="bo-table"><table><thead><tr><th>管道</th><th>結果</th><th>細節</th></tr></thead><tbody>
          <tr><td><strong>LINE 官方帳號（主要）</strong></td><td>${st(s.line)}</td><td>${esc((c.line || a.line_result || {}).text || "")}</td></tr>
          <tr><td>Email</td><td>${st(s.email)}</td><td>${esc((c.email || {}).text || "")}</td></tr>
          <tr><td>小幫手知識庫</td><td>${st(s.kb)}</td><td>${a.kb_chunks ? `切成 ${a.kb_chunks} 段${a.deadline ? `，${esc(a.deadline)} 之後自動停用` : ""}` : "沒有加入"}</td></tr>
          <tr><td>全文頁</td><td><span class="ok">✓ 已建立</span></td><td><a href="/notice/${a.id}" target="_blank" rel="noopener">開啟學生看到的全文頁 ↗</a></td></tr>
          <tr><td>Instagram 圖卡</td><td>${st(s.ig)}</td><td>${c.ig?.mode === "manual" ? "IG 還沒串接 API，請下載圖卡、複製說明後手動發文" : esc((c.ig || {}).text || "")}</td></tr>
        </tbody></table></div>
        ${c.ig?.mode === "manual" ? `<div class="bo-igdone">${igTools()}</div>` : ""}
        <p class="bo-small">任何一個管道失敗，其他管道照樣完成；失敗的會在「發布紀錄」標紅，可以重試。</p>
        <div class="bo-actions">
          ${opts.openHistory ? '<button type="button" class="bo-primary" data-act="history">到發布紀錄</button>' : ""}
          <button type="button" data-act="again">再發一則</button></div>`;
    }
    function sideView() {
      return `<h3 style="margin-top:0">各管道預覽</h3>
        <div class="bo-seg" role="group" aria-label="預覽管道" style="display:inline-flex;margin-bottom:10px">
          ${["line", "email", "ig"].map(k => `<button type="button" data-pv="${k}" aria-pressed="${pv === k}">${{line: "LINE", email: "Email", ig: "IG"}[k]}</button>`).join("")}
        </div>
        <div class="bo-pv" aria-live="polite"></div>`;
    }
    function cardData() { if (done) return done; readForm(); return form; }
    function preview() {
      const box = el.querySelector(".bo-pv");
      if (!box) return;
      const a = cardData(), t = a.title || "（標題）";
      const lineOn = done ? ((done.channels?.line || done.line_result || {}).mode || "demo") !== "off" : ch.line;
      if (pv === "line" && !lineOn) {
        box.innerHTML = '<div class="bo-dash">這次沒勾 LINE，學生的 LINE 不會收到。</div>';
      } else if (pv === "line") {
        box.innerHTML = `<div class="bo-preview">${noticeCard(a)}</div>
          <div class="bo-note"><strong>手機通知列只顯示一行</strong><div class="bo-muted">【國際處公告】${esc(t)}</div></div>`;
      } else if (pv === "email") {
        const on = done ? ["demo", "sent", "error"].includes((done.channels?.email || {}).mode) : ch.email;
        box.innerHTML = on ? `<div class="bo-mail"><div class="h"><div><span class="bo-muted">收件人：</span>${mail?.on ? esc(mail.to.join("、") || "（還沒設定）") + (mail.to_count > 3 ? ` 等 ${mail.to_count} 人` : "") : "[國際生群組信箱]"}</div>
            <strong>【國際處公告】${esc(t)}</strong></div>
          <div class="b"><span>${esc(a.summary_zh || "")}</span>${a.summary_en ? `<span class="bo-muted">${esc(a.summary_en)}</span>` : ""}
            ${a.deadline ? `<span class="warn"><strong>截止 Deadline：${esc(a.deadline)}</strong></span>` : ""}
            <span class="bo-link-t">查看全文與附件 Full notice</span>
            <span class="bo-small">這封信也同步發在國際處 LINE 官方帳號。</span></div></div>`
          : '<div class="bo-dash">這次沒勾 Email，不會寄信。</div>';
      } else {
        const card = done ? done.channels?.ig?.card : (ch.ig ? igCard() : null);
        if (!card) {
          box.innerHTML = `<div class="bo-dash">${done ? "這次沒勾 IG。" : "還沒勾「Instagram 圖卡」。勾選後會用國際處 IG 模板（活動海報或生活指南卡）產生 1080×1350 的圖卡。"}</div>`;
          return;
        }
        if (!box.querySelector("canvas")) {
          box.innerHTML = `<canvas class="bo-igcv" width="1080" height="1350" aria-label="IG 圖卡預覽"></canvas><div data-igtools></div>
            <details class="bo-small" style="margin-top:6px"><summary>貼文說明</summary><div class="bo-answer" data-igcap></div></details>`;
        }
        box.querySelector("[data-igcap]").textContent = card.caption || "";
        const tools = box.querySelector("[data-igtools]"); tools.innerHTML = igTools();
        wireIgTools(tools, card, a);
        clearTimeout(igTimer);
        igTimer = setTimeout(() => IG.draw(box.querySelector("canvas"), card, a, illus), 120);
      }
    }
    function summary() {
      const s = el.querySelector("#bo-sum");
      if (s) s.textContent = [ch.line && "LINE", ch.email && "Email", ch.ig && "IG 圖卡（手動發文）", ch.kb && "小幫手知識庫"].filter(Boolean).join("、") || "（都沒勾）";
    }
    function wire() {
      const on = (sel, fn) => el.querySelectorAll(sel).forEach(b => b.addEventListener("click", fn));
      on("[data-act=import]", e => { importOpen = !importOpen; e.currentTarget.setAttribute("aria-expanded", importOpen); el.querySelector("#bo-import").hidden = !importOpen; });
      on("[data-pv]", e => { pv = e.currentTarget.dataset.pv; el.querySelectorAll("[data-pv]").forEach(b => b.setAttribute("aria-pressed", b.dataset.pv === pv)); preview(); });
      el.querySelector("#bo-ch-line")?.addEventListener("change", e => { ch.line = e.target.checked; summary(); preview(); });
      el.querySelector("#bo-ch-email")?.addEventListener("change", e => { ch.email = e.target.checked; summary(); preview(); });
      el.querySelector("#bo-ch-kb")?.addEventListener("change", e => { ch.kb = e.target.checked; summary(); });
      el.querySelector("#bo-ch-ig")?.addEventListener("change", e => { readForm(); ch.ig = e.target.checked; if (ch.ig) pv = "ig"; render(); });
      ["#bo-title", "#bo-zh", "#bo-en", "#bo-due"].forEach(id => el.querySelector(id)?.addEventListener("input", () => { readForm(); igSync(); preview(); }));
      el.querySelector("#bo-aud")?.addEventListener("change", preview);
      el.querySelectorAll("[data-ig]").forEach(i => i.addEventListener("input", () => { ig[i.dataset.ig] = i.value; igDirty[i.dataset.ig] = true; preview(); }));
      on("[data-igtpl]", e => { readForm(); ig.tpl = e.currentTarget.dataset.igtpl; pv = "ig"; render(); });
      el.querySelector("#ig-theme")?.addEventListener("change", e => { ig.theme = e.target.value; preview(); });
      el.querySelector("#ig-illus")?.addEventListener("change", e => {
        const f = e.target.files?.[0];
        if (!f) return;
        const i = new Image(); i.onload = () => { illus = i; readForm(); pv = "ig"; render(); }; i.src = URL.createObjectURL(f);
      });
      on("[data-act=igclear]", () => { illus = null; readForm(); render(); });
      on("[data-act=igreset]", () => { readForm(); IG_KEYS.forEach(k => { delete igDirty[k]; delete ig[k]; }); render(); });
      on("[data-pick]", e => { cur = list.find(a => a.id === e.currentTarget.dataset.pick); form = fromItem(cur); importOpen = false; msg = "已帶入，請檢查內容再按確認發布。"; render(); el.querySelector("#bo-title")?.focus(); });
      on("[data-act=new]", () => { cur = null; msg = ""; readForm(); render(); });
      on("[data-act=clearform]", () => { cur = null; msg = ""; igDirty = {}; ig = {tpl: ig.tpl, theme: ig.theme}; illus = null; form = {title: "", summary_zh: "", summary_en: "", deadline: "", audience: AUD[0]}; render(); });
      on("[data-act=again]", () => { done = null; cur = null; msg = ""; igDirty = {}; ig = {tpl: ig.tpl, theme: ig.theme}; illus = null; form = {title: "", summary_zh: "", summary_en: "", deadline: "", audience: AUD[0]}; render(); });
      on("[data-act=history]", () => opts.openHistory && opts.openHistory());
      const igDone = el.querySelector(".bo-igdone");
      if (igDone && done?.channels?.ig?.card) wireIgTools(igDone, done.channels.ig.card, done);
      on("[data-act=fetchmail]", () => act(() => api("/api/admin/mail/fetch", {method: "POST"}),
          async r => { await load(); importOpen = true;
                       msg = r.imported ? `帶入 ${r.imported} 封新信，AI 已改寫好，按「帶入」檢查內容。` + (r.more ? `還有 ${r.more} 封，再按一次檢查信箱。` : "")
                                        : `沒有新信（${esc(r.inbox)} 近 14 天有 ${r.matched} 封，都已帶入過）。`; }));
      on("[data-act=draft]", () => {
        const subject = el.querySelector("#bo-subject").value, raw_body = el.querySelector("#bo-raw").value;
        act(() => api("/api/admin/announce", {method: "POST", body: JSON.stringify({subject, raw_body})}),
            async a => { await load(); cur = list.find(x => x.id === a.id) || a; form = fromItem(cur); importOpen = false;
                         msg = "AI 已改寫好，請檢查內容再按確認發布。"; });
      });
      on("[data-act=assist]", () => {
        readForm();
        if (!form.summary_zh.trim()) { msg = '<span class="warn">先寫中文內容，AI 才能翻。</span>'; return render(); }
        act(() => api("/api/admin/announce/assist", {method: "POST", body: JSON.stringify({title: form.title, summary_zh: form.summary_zh})}),
            r => { form.summary_en = r.summary_en || form.summary_en; if (!form.deadline && r.deadline) form.deadline = r.deadline;
                   msg = "英文版已填好" + (r.deadline ? `，AI 找到的日期是 ${esc(r.deadline)}，請確認截止日。` : "，請看一下再發布。"); });
      });
      on("[data-act=skip]", () => act(() => api(`/api/admin/announce/${cur.id}/skip`, {method: "POST"}),
                                      async () => { cur = null; form = fromItem({}); msg = "這封標為不發，不會推播也不會進知識庫。"; await load(); opts.onChanged && opts.onChanged(); }));
      on("[data-act=publish]", () => {
        readForm();
        if (!form.title.trim() || !form.summary_zh.trim()) { msg = '<span class="warn">標題和中文內容都要填。</span>'; return render(); }
        if (!ch.line && !ch.email) { msg = '<span class="warn">LINE 和 Email 至少要勾一個，學生才收得到。</span>'; return render(); }
        const body = {...form, channels: {...ch, ...(ch.ig ? {ig_card: igCard()} : {})}};
        act(async () => {
              const id = cur ? cur.id : (await api("/api/admin/announce/compose", {method: "POST", body: JSON.stringify(form)})).id;
              return api(`/api/admin/announce/${id}/publish`, {method: "POST", body: JSON.stringify(body)});
            },
            async a => { done = a; cur = null; pv = "line"; if (opts.onPublished && ch.line) opts.onPublished(a); opts.onChanged && opts.onChanged();
                         list = await api("/api/admin/announce"); });
      });
    }
    loaders.push(() => load());
    return {load};
  }

  // ---------------------------------------------------------------- 發布紀錄
  function History(el, opts = {}) {
    let list = [], sel = null, f = {st: "all", q: ""}, msg = "", busy = false, armed = "";
    async function load() {
      try {
        list = (await api("/api/admin/announce")).filter(a => a.status !== "pending")
          .sort((a, b) => (b.published_at || b.received_at || "").localeCompare(a.published_at || a.received_at || ""));
        render();
      } catch (e) {
        if (e.auth) return login(el, key ? "金鑰不對，請重新輸入。" : "", load);
        el.innerHTML = `<p class="bo-err">讀取失敗（${esc(e.message)}），請重新整理。</p>`;
      }
    }
    async function act(fn, ok) {
      if (busy) return;
      busy = true; msg = ""; armed = "";
      try { await fn(); msg = ok; list = (await api("/api/admin/announce")).filter(a => a.status !== "pending")
        .sort((a, b) => (b.published_at || b.received_at || "").localeCompare(a.published_at || a.received_at || ""));
        opts.onChanged && opts.onChanged(); }
      catch (e) { msg = `<span class="crit">失敗：${esc(e.message)}</span>`; }
      busy = false; render();
    }
    const failed = a => chState(a).email[0] === "crit" || chState(a).line[0] === "crit";
    function shown() {
      const q = f.q.trim().toLowerCase();
      return list.filter(a => (f.st === "all" || (f.st === "pub" && a.status === "published") || (f.st === "skip" && a.status === "skipped")
                               || (f.st === "fail" && failed(a)))
                            && (!q || `${a.title} ${a.subject} ${a.summary_zh}`.toLowerCase().includes(q)));
    }
    function render() {
      const month = todayStr().slice(0, 7);
      const pub = list.filter(a => a.status === "published");
      const nMonth = pub.filter(a => (a.published_at || "").slice(0, 7) === month).length;
      const nKb = pub.filter(a => chState(a).kb[0] === "ok").length;
      const nFail = list.filter(failed).length;
      el.innerHTML = `
        <div class="bo-cards">
          <div class="bo-card"><span class="bo-muted">本月發布</span><b>${nMonth} 則</b></div>
          <div class="bo-card"><span class="bo-muted">LINE 推播（demo 為模擬）</span><b>${pub.length} 則</b></div>
          <div class="bo-card"><span class="bo-muted">知識庫裡有效的公告</span><b>${nKb} 則</b></div>
          <div class="bo-card${nFail ? " crit" : ""}"><span class="bo-muted">要處理</span><b>${nFail ? `${nFail} 則寄送失敗` : "沒有"}</b></div>
        </div>
        <div class="bo-controls">
          <label for="h-st" class="bo-small">狀態</label>
          <select id="h-st">${[["all", "全部"], ["pub", "已發布"], ["fail", "有失敗"], ["skip", "不發"]].map(([v, t]) => `<option value="${v}"${f.st === v ? " selected" : ""}>${t}</option>`).join("")}</select>
          <input id="h-q" type="search" placeholder="搜尋標題或內容" aria-label="搜尋標題或內容" value="${esc(f.q)}">
        </div>
        <div id="h-table"></div>
        <div id="h-detail"></div>
        ${msg ? `<p class="bo-small">${msg}</p>` : ""}
        <div class="bo-controls" style="margin-top:14px;border-top:1px solid var(--bo-line);padding-top:10px">
          <span class="bo-small">demo 用：</span>
          <button type="button" data-act="seed">${list.some(a => a.is_sample) ? "重設範例公告" : "載入範例公告"}</button>
          ${list.some(a => a.is_sample) ? '<button type="button" data-act="clear">清除範例</button>' : ""}
        </div>`;
      el.querySelector("#h-st").addEventListener("change", e => { f.st = e.target.value; table(); });
      el.querySelector("#h-q").addEventListener("input", e => { f.q = e.target.value; table(); });
      el.querySelector("[data-act=seed]").addEventListener("click", () => act(() => api("/api/admin/announce-samples", {method: "POST"}), "範例公告已重設。"));
      el.querySelector("[data-act=clear]")?.addEventListener("click", () => { sel = null; act(() => api("/api/admin/announce-samples", {method: "DELETE"}), "範例公告已清除。"); });
      table(); detail();
    }
    function table() {
      const rows = shown();
      const box = el.querySelector("#h-table");
      if (!rows.length) { box.innerHTML = '<p class="bo-small">沒有符合的公告。到「發布公告」發一則，或按下面的「載入範例公告」。</p>'; return; }
      box.innerHTML = `<div class="bo-table" style="overflow-x:auto"><table style="min-width:760px"><thead><tr>
          <th>發布時間</th><th>標題（點開看細節）</th><th>LINE</th><th>Email</th><th>IG</th><th>知識庫</th><th class="num">學生問到</th></tr></thead><tbody>
        ${rows.map(a => { const s = chState(a); return `<tr class="bo-row${sel === a.id ? " open" : ""}" tabindex="0" data-id="${a.id}">
          <td style="white-space:nowrap">${fmt(a.published_at || a.received_at)}</td>
          <td class="q">${esc(a.title || a.subject)}${a.is_sample ? ' <span class="bo-small">範例</span>' : ""}</td>
          <td>${st(s.line)}</td><td>${st(s.email)}</td><td>${st(s.ig)}</td><td>${st(s.kb)}</td>
          <td class="num">${a.status === "published" ? `${a.asks || 0} 次` : "—"}</td></tr>`; }).join("")}
        </tbody></table></div>`;
      box.querySelectorAll("tr.bo-row").forEach(tr => {
        const pick = () => { sel = sel === tr.dataset.id ? null : tr.dataset.id; armed = ""; table(); detail(); };
        tr.addEventListener("click", pick);
        tr.addEventListener("keydown", e => { if (e.key === "Enter") pick(); });
      });
    }
    function detail() {
      const box = el.querySelector("#h-detail"), a = list.find(x => x.id === sel);
      if (!a) { box.innerHTML = ""; return; }
      const s = chState(a), c = a.channels || {};
      const btn = (act, label) => `<button type="button" data-d="${act}">${armed === act ? "確定？再按一次" : label}</button>`;
      const row = (name, state, text, action) => `<tr><td><strong>${name}</strong></td><td>${st(state)}</td><td>${esc(text || "")}</td><td>${action || ""}</td></tr>`;
      box.innerHTML = `<div class="bo-detail">
        <h3 style="margin-top:0">${esc(a.title || a.subject)}</h3>
        <p class="bo-small" style="margin:0 0 8px">${a.status === "published" ? `發布：${fmt(a.published_at)}` : "選了不發"}｜來源：${esc(SRC[a.source] || a.source)}｜對象：${esc(a.audience || "全體境外生")}</p>
        <div class="bo-twocol">
          <div><div class="bo-table"><table><thead><tr><th>管道</th><th>結果</th><th>細節</th><th><span class="bo-sr">動作</span></th></tr></thead><tbody>
            ${row("LINE", s.line, (c.line || a.line_result || {}).text)}
            ${row("Email", s.email, (c.email || {}).text, s.email[0] === "crit" ? btn("retry", "重試") : "")}
            ${row("Instagram 圖卡", s.ig, c.ig?.mode === "manual" ? "IG 還沒串接 API，手動發文" : (c.ig || {}).text,
                  c.ig?.mode === "manual" ? '<button type="button" data-igdl>下載圖卡</button> <button type="button" data-igcopy>複製說明</button>' : "")}
            ${row("小幫手知識庫", s.kb, a.kb_chunks ? `${a.kb_chunks} 段${a.deadline ? `，${a.deadline} 後自動停用` : ""}` : "",
                  s.kb[0] === "ok" ? btn("kbrm", "提早下架") : "")}
          </tbody></table></div>
          <p class="bo-small">LINE 推播送出後沒辦法收回；要更正請再發一則更正公告，並把舊的從知識庫下架。</p></div>
          <div>${a.status === "published" ? `<div class="bo-cards" style="grid-template-columns:repeat(2,minmax(0,1fr))">
              <div class="bo-card"><span class="bo-muted">點「查看全文」</span><b>${a.views || 0} 次</b></div>
              <div class="bo-card"><span class="bo-muted">學生問小幫手時用到</span><b>${a.asks || 0} 次</b></div></div>
              <a href="/notice/${a.id}" target="_blank" rel="noopener">開啟全文頁 ↗</a>` : ""}</div>
        </div></div>`;
      if (c.ig?.mode === "manual") {
        box.querySelector("[data-igdl]")?.addEventListener("click", () => IG.download(c.ig.card, a));
        box.querySelector("[data-igcopy]")?.addEventListener("click", async e => { const b = e.currentTarget; b.textContent = (await IG.copy(c.ig.card.caption || "")) ? "已複製" : "複製失敗"; });
      }
      box.querySelectorAll("[data-d]").forEach(b => b.addEventListener("click", () => {
        const d = b.dataset.d;
        if (armed !== d) { armed = d; return detail(); }
        if (d === "retry") act(() => api(`/api/admin/announce/${a.id}/retry-email`, {method: "POST"}), "已重新寄信，結果看上面 Email 那一列。");
        if (d === "kbrm") act(() => api(`/api/admin/announce/${a.id}/kb-remove`, {method: "POST"}), "已從知識庫下架，小幫手不會再用這則回答。");
      }));
    }
    loaders.push(() => load());
    return {load};
  }

  // LINE 公告卡片的樣子：後台預覽和 demo 手機共用
  function noticeCard(a, href) {
    return `<div class="notice-card"><div class="nc-head">國際處公告｜OIA Notice</div><div class="nc-body">
      <div class="nc-title">${esc(a.title || "（標題）")}</div><div>${esc(a.summary_zh || "")}</div>
      ${a.summary_en ? `<div class="nc-en">${esc(a.summary_en)}</div>` : ""}
      ${a.deadline ? `<div class="nc-due">截止 Deadline：${esc(a.deadline)}</div>` : ""}</div>
      ${href ? `<a class="nc-btn" href="${esc(href)}" data-notice="${esc(href)}">查看全文 Full notice ›</a>` : '<div class="nc-btn">查看全文 Full notice ›</div>'}</div>`;
  }

  // demo 期間後台不需要金鑰（資料庫設定 admin_open=on）；改回 off 時，頁面會自動要求輸入金鑰
  window.Backoffice = {Stats, Messages, Publish, History, noticeCard, IG, hasKey: () => true};
})();
