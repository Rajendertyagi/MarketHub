
  // ── Instruments search + sync ───────────────────────────────────────────

  function initInstruments() {
    const input = $("instr-search");
    const msg = $("instr-sync-msg");
    let debounce = null;
    const doSearch = async () => {
      const q = input.value.trim();
      const exchange = $("instr-exchange").value;
      const url = "/api/instruments/search?limit=25" +
        (q ? "&q=" + encodeURIComponent(q) : "") +
        (exchange ? "&exchange=" + exchange : "");
      try {
        const res = await fetch(url);
        const data = await res.json();
        const body = $("instr-body");
        if (!data.results || !data.results.length) {
          body.innerHTML = '<tr><td colspan="9" class="empty-row">No matches. Sync a provider master first if the catalog is empty.</td></tr>';
          return;
        }
        body.innerHTML = data.results.map((r) =>
          `<tr data-tok="${r.instrument_token}" data-ex="${r.exchange}" data-sym="${r.tradingsymbol}">` +
          `<td>${r.tradingsymbol}</td><td>${r.name || "—"}</td>` +
          `<td>${r.exchange}</td><td>${r.instrument_type || "—"}</td>` +
          `<td>${r.expiry || "—"}</td><td>${r.strike != null ? r.strike : "—"}</td>` +
          `<td>${r.lot_size != null ? r.lot_size : "—"}</td><td>${r.provider}</td>` +
          `<td><button class="btn wl-add" style="padding:2px 8px;font-size:11px">+ Watchlist</button></td></tr>`
        ).join("");
      } catch { /* silent */ }
    };
    input.addEventListener("input", () => {
      clearTimeout(debounce);
      debounce = setTimeout(doSearch, 300);
    });
    $("instr-exchange").addEventListener("change", doSearch);
    document.getElementById("instr-table").addEventListener("click", async (e) => {
      const btn = e.target.closest(".wl-add");
      if (!btn) return;
      const tr = btn.closest("tr");
      try {
        let wlId = null;
        const res = await fetch("/api/watchlists");
        const data = await res.json();
        if (data.watchlists && data.watchlists.length) {
          wlId = data.watchlists[0].id;
        } else {
          const created = await fetch("/api/watchlists", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: "Default" }) });
          const cd = await created.json();
          wlId = cd.watchlist.id;
        }
        await fetch(`/api/watchlists/${wlId}/items`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ exchange: tr.dataset.ex,
            instrument_token: tr.dataset.tok,
            tradingsymbol: tr.dataset.sym }) });
        msg.textContent = `Added ${tr.dataset.sym} to watchlist.`;
        msg.className = "hint ok";
        loadWatchlists();
      } catch {
        msg.textContent = "Failed to add to watchlist.";
        msg.className = "hint err";
      }
    });
    const doSync = async (provider, btn) => {
      btn.disabled = true;
      msg.textContent = `Syncing ${provider} instrument master…`;
      msg.className = "hint";
      try {
        const res = await fetch("/api/instruments/sync", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ provider }) });
        const data = await res.json();
        msg.textContent = res.ok
          ? `${provider} sync complete: ${data.records} instruments.`
          : (data.error || "Sync failed.");
        msg.className = "hint " + (res.ok ? "ok" : "err");
        doSearch();
      } catch {
        msg.textContent = "Network error during sync.";
        msg.className = "hint err";
      } finally { btn.disabled = false; }
    };
    $("instr-sync-upstox").addEventListener("click",
      (e) => doSync("upstox", e.target));
    $("instr-sync-fyers").addEventListener("click",
      (e) => doSync("fyers", e.target));
    doSearch();
  }

  // ── Watchlists ──────────────────────────────────────────────────────────

  let currentWatchlistId = null;

  async function loadWatchlists() {
    try {
      const res = await fetch("/api/watchlists");
      const data = await res.json();
      const sel = $("wl-select");
      if (sel) sel.innerHTML = (data.watchlists || []).map((w) =>
        `<option value="${w.id}">${w.name}</option>`).join("");
      currentWatchlistId = sel && sel.value ? Number(sel.value) : null;
      renderWatchlistItems(data.watchlists || []);
    } catch { /* silent */ }
  }

  function renderWatchlistItems(watchlists) {
    const wl = (watchlists || []).find(
      (w) => String(w.id) === String(currentWatchlistId));
    const body = $("wl-body");
    if (!wl || !wl.items.length) {
      body.innerHTML = '<tr><td colspan="8" class="empty-row">No items. Add instruments from the Instruments page.</td></tr>';
      return;
    }
    body.innerHTML = wl.items.map((it) => {
      const q = quotes.get(it.instrument_token)
        || quotes.get(it.exchange + ":" + it.instrument_token);
      const ltp = q && q.ltp != null ? fmt(q.ltp) : "—";
      const chg = q && q.change != null ? fmt(q.change) : "—";
      const pct = q && q.change_percent != null
        ? fmt(q.change_percent) + "%" : "—";
      const cls = q ? chgClass(q.change ?? 0) : "";
      return `<tr data-item-id="${it.id}" data-token="${it.instrument_token}" data-ex="${it.exchange}">` +
        `<td>${it.tradingsymbol}</td><td>${ltp}</td>` +
        `<td class="${cls}">${chg}</td><td class="${cls}">${pct}</td>` +
        `<td>${q ? fmtVol(q.volume) : "—"}</td>` +
        `<td>${q && q.best_bid != null ? fmt(q.best_bid) : "—"}</td>` +
        `<td>${q && q.best_ask != null ? fmt(q.best_ask) : "—"}</td>` +
        `<td><button class="btn wl-remove" style="padding:2px 8px;font-size:11px">✕</button></td></tr>`;
    }).join("");
  }

  function initWatchlists() {
    $("wl-create").addEventListener("click", async () => {
      const name = $("wl-new-name").value.trim();
      if (!name) return;
      await fetch("/api/watchlists", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }) });
      $("wl-new-name").value = "";
      loadWatchlists();
    });
    $("wl-rename").addEventListener("click", async () => {
      const name = $("wl-new-name").value.trim();
      if (!name || !currentWatchlistId) return;
      await fetch(`/api/watchlists/${currentWatchlistId}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }) });
      $("wl-new-name").value = "";
      loadWatchlists();
    });
    $("wl-delete").addEventListener("click", async () => {
      if (!currentWatchlistId ||
          !confirm("Delete this watchlist and its items?")) return;
      await fetch(`/api/watchlists/${currentWatchlistId}`,
        { method: "DELETE" });
      loadWatchlists();
    });
    $("wl-select").addEventListener("change", loadWatchlists);
    $("wl-table").addEventListener("click", async (e) => {
      const btn = e.target.closest(".wl-remove");
      if (!btn) return;
      const itemId = btn.closest("tr").dataset.itemId;
      await fetch(`/api/watchlists/items/${itemId}`, { method: "DELETE" });
      loadWatchlists();
    });
    setInterval(loadWatchlists, 10000);   // refresh live values from SSE state
    loadWatchlists();
  }

  // ── Option chain (catalog-driven) ───────────────────────────────────────

  let ocUnderlying = "";
  let ocFullStrikes = [];

  function initOptionChain() {
    const search = $("oc-underlying-search");
    const sel = $("oc-underlying-select");
    const expSel = $("oc-expiry-select");
    const msg = $("oc-message");

    let debounce = null;
    search.addEventListener("input", () => {
      clearTimeout(debounce);
      debounce = setTimeout(async () => {
        const q = search.value.trim();
        if (!q) return;
        try {
          const res = await fetch("/api/options/underlyings?q=" +
            encodeURIComponent(q));
          const d = await res.json();
          sel.innerHTML = '<option value="">Underlying…</option>' +
            (d.underlyings || []).map((u) =>
              `<option value="${u}">${u}</option>`).join("");
        } catch { /* silent */ }
      }, 300);
    });

    sel.addEventListener("change", async () => {
      ocUnderlying = sel.value;
      expSel.innerHTML = '<option value="">Expiry…</option>';
      expSel.disabled = true;
      if (!ocUnderlying) return;
      try {
        const res = await fetch("/api/options/expiries?underlying=" +
          encodeURIComponent(ocUnderlying));
        const d = await res.json();
        expSel.innerHTML = '<option value="">Expiry…</option>' +
          (d.expiries || []).map((e) =>
            `<option value="${e}">${e}</option>`).join("");
        expSel.disabled = !(d.expiries || []).length;
      } catch { /* silent */ }
    });

    $("oc-load").addEventListener("click", async () => {
      const expiry = expSel.value;
      msg.textContent = "";
      msg.className = "hint";
      if (!ocUnderlying || !expiry) {
        msg.textContent = "Pick an underlying and expiry first.";
        msg.className = "hint err";
        return;
      }
      // Resolve the underlying's instrument key from catalog search.
      try {
        const sres = await fetch("/api/instruments/search?q=" +
          encodeURIComponent(ocUnderlying) + "&limit=5");
        const sd = await sres.json();
        const hit = (sd.results || []).find(
          (r) => r.name === ocUnderlying || r.tradingsymbol === ocUnderlying)
          || (sd.results || [])[0];
        if (!hit) {
          msg.textContent = "Underlying not found in catalog.";
          msg.className = "hint err";
          return;
        }
        const res = await fetch("/api/options/chain?instrument_key=" +
          encodeURIComponent(hit.instrument_token) + "&exchange=" +
          encodeURIComponent(hit.exchange) + "&tradingsymbol=" +
          encodeURIComponent(hit.tradingsymbol) + "&expiry=" + expiry);
        const d = await res.json();
        if (!res.ok) {
          msg.textContent = d.error || "Chain load failed.";
          msg.className = "hint err";
          return;
        }
        $("oc-spot").textContent = d.spot_price != null
          ? fmt(d.spot_price) : "—";
        $("oc-atm").textContent = d.atm_strike != null
          ? fmt(d.atm_strike) : "—";
        ocFullStrikes = d.strikes || [];
        renderOcStrikes();
      } catch {
        msg.textContent = "Network error loading chain.";
        msg.className = "hint err";
      }
    });
  }

  function renderOcStrikes() {
    const win = Number($("oc-window").value) || 0;
    let rows = ocFullStrikes;
    if (win > 0 && ocFullStrikes.length) {
      const atmIdx = ocFullStrikes.findIndex((s) => s.atm);
      const center = atmIdx >= 0 ? atmIdx : Math.floor(rows.length / 2);
      rows = ocFullStrikes.slice(Math.max(0, center - win),
                                 center + win + 1);
    }
    const side = (x) => x ? [
      fmtVol(x.oi), fmtNum(x.oi_change), fmtVol(x.volume),
      x.iv != null ? fmt(x.iv) : "—",
      x.ltp != null ? fmt(x.ltp) : "—",
      (x.close != null && x.ltp != null) ? fmt(x.ltp - x.close) : "—",
    ].map((v) => `<td>${v}</td>`).join("") : "<td>—</td>".repeat(6);
    $("oc-body").innerHTML = rows.map((s) => {
      const rowCls = s.atm ? ' style="background:var(--accent-dim)"' : "";
      return `<tr${rowCls}>` + side(s.call) +
        `<td><b>${fmt(s.strike)}</b></td>` + side(s.put) + "</tr>";
    }).join("");
  }

  // ── Charts (ECharts candlestick + volume) ───────────────────────────────

  let chartInstance = null;

  function initCharts() {
    const today = new Date().toISOString().slice(0, 10);
    const monthAgo = new Date(Date.now() - 30 * 86400000)
      .toISOString().slice(0, 10);
    $("chart-from").value = monthAgo;
    $("chart-to").value = today;

    $("chart-load").addEventListener("click", async () => {
      const key = $("chart-instrument").value.trim();
      const unit = $("chart-unit").value;
      const interval = $("chart-interval").value || 1;
      const from = $("chart-from").value;
      const to = $("chart-to").value;
      const msg = $("chart-message");
      if (!key || !from || !to) {
        msg.textContent = "Instrument key, from and to dates are required.";
        msg.className = "hint err";
        return;
      }
      msg.textContent = "Loading history…";
      msg.className = "hint";
      try {
        const res = await fetch("/api/market/history?instrument_key=" +
          encodeURIComponent(key) + "&unit=" + unit +
          "&interval=" + interval + "&from=" + from + "&to=" + to);
        const d = await res.json();
        if (!res.ok) {
          msg.textContent = d.error || "History load failed.";
          msg.className = "hint err";
          return;
        }
        msg.textContent = `${d.candles.length} candles loaded.`;
        msg.className = "hint ok";
        renderChart(d.candles);
      } catch {
        msg.textContent = "Network error loading history.";
        msg.className = "hint err";
      }
    });
  }

  function renderChart(candles) {
    if (!window.echarts) {
      $("chart-message").textContent = "Chart library not loaded.";
      return;
    }
    if (!chartInstance) {
      chartInstance = echarts.init($("chart-container"));
    }
    const times = candles.map((c) =>
      c.timestamp.slice(0, 16).replace("T", " "));
    const kline = candles.map((c) => [c.open, c.close, c.low, c.high]);
    const vols = candles.map((c) => c.volume ?? 0);
    chartInstance.setOption({
      animation: false,
      tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: [{ left: 60, right: 20, top: 20, height: "58%" },
             { left: 60, right: 20, top: "72%", height: "18%" }],
      xAxis: [
        { type: "category", data: times },
        { type: "category", gridIndex: 1, data: times,
          axisLabel: { show: false } },
      ],
      yAxis: [
        { scale: true },
        { gridIndex: 1, axisLabel: { show: false } },
      ],
      series: [
        { type: "candlestick", data: kline,
          itemStyle: { color: "#3fb950", color0: "#f85149",
                       borderColor: "#3fb950", borderColor0: "#f85149" } },
        { type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: vols },
      ],
    });
  }

  // ── Alerts ──────────────────────────────────────────────────────────────

  function initAlerts() {
    $("alert-create").addEventListener("click", async () => {
      const token = $("alert-token").value.trim();
      const symbol = $("alert-symbol").value.trim() || token;
      const field = $("alert-field").value;
      const operator = $("alert-operator").value;
      const threshold = parseFloat($("alert-threshold").value);
      if (!token || isNaN(threshold)) return;
      await fetch("/api/alerts", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ exchange: "NSE",
          instrument_token: token, tradingsymbol: symbol,
          field, operator, threshold }) });
      $("alert-threshold").value = "";
      loadAlerts();
    });
    $("alerts-table").addEventListener("click", async (e) => {
      const row = e.target.closest("tr[data-alert-id]");
      if (!row) return;
      const id = row.dataset.alertId;
      if (e.target.classList.contains("alert-rearm")) {
        await fetch(`/api/alerts/${id}/rearm`, { method: "POST" });
      } else if (e.target.classList.contains("alert-toggle")) {
        await fetch(`/api/alerts/${id}/enabled`, { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            enabled: e.target.dataset.enabled !== "true" }) });
      } else if (e.target.classList.contains("alert-delete")) {
        await fetch(`/api/alerts/${id}`, { method: "DELETE" });
      } else { return; }
      loadAlerts();
    });
    loadAlerts();
    setInterval(loadAlerts, 15000);
  }

  async function loadAlerts() {
    try {
      const res = await fetch("/api/alerts");
      const data = await res.json();
      const body = $("alerts-body");
      const alerts = data.alerts || [];
      if (!alerts.length) {
        body.innerHTML = '<tr><td colspan="5" class="empty-row">No alerts configured.</td></tr>';
      } else {
        body.innerHTML = alerts.map((a) => {
          const cond = `${a.field} ${a.operator === "gt" ? ">" : "<"} ${a.threshold}`;
          const stateCls = a.state === "triggered" ? "chip chip-off" :
            (a.enabled ? "chip chip-on" : "chip");
          return `<tr data-alert-id="${a.id}">` +
            `<td>${a.tradingsymbol}</td><td>${cond}</td>` +
            `<td><span class="${stateCls}">${a.state}</span></td>` +
            `<td><button class="btn alert-toggle" data-enabled="${a.enabled ? "true" : "false"}" style="padding:2px 8px;font-size:11px">${a.enabled ? "On" : "Off"}</button></td>` +
            `<td><button class="btn alert-rearm" style="padding:2px 8px;font-size:11px">Re-arm</button> ` +
            `<button class="btn alert-delete" style="padding:2px 8px;font-size:11px;border-color:var(--red);color:var(--red)">✕</button></td></tr>`;
        }).join("");
      }
      const notes = data.notifications || [];
      $("alert-notifications").innerHTML = notes.length
        ? '<div class="panel"><div class="panel-header"><h2>Triggered</h2></div>' +
          notes.map((n) => `<div class="hint err">${n.tradingsymbol}: ${n.field} ${n.operator === "gt" ? ">" : "<"} ${n.threshold} (now ${n.value})</div>`).join("") +
          "</div>"
        : "";
    } catch { /* silent */ }
  }

  // ── Database backup (Settings) ──────────────────────────────────────────

  function initBackup() {
    const btn = $("db-backup");
    if (!btn) return;
    const msg = $("backup-message");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const res = await fetch("/api/admin/backup", { method: "POST" });
        const d = await res.json();
        if (res.ok) {
          msg.textContent = "Backup saved to data/backups/" + d.file +
            " (contains ciphertext only; master.key required to decrypt).";
          msg.className = "hint ok";
        } else {
          msg.textContent = d.error || "Backup failed.";
          msg.className = "hint err";
        }
      } catch {
        msg.textContent = "Network error during backup.";
        msg.className = "hint err";
      } finally { btn.disabled = false; }
    });
  }

  // ── Fyers credentials + login (Settings) ────────────────────────────────

  function initFyers() {
    const saveBtn = $("fyers-save");
    if (!saveBtn) return;
    const msg = $("fyers-message");
    const loginBtn = $("fyers-login-btn");

    async function refresh() {
      try {
        const res = await fetch("/api/settings/fyers");
        const d = await res.json();
        const chip = $("fyers-status");
        chip.textContent = d.login_available ? "Configured" : "Not configured";
        chip.className = "chip " + (d.login_available ? "chip-on" : "chip-off");
        loginBtn.style.display = d.login_available ? "" : "none";
      } catch { /* silent */ }
    }

    saveBtn.addEventListener("click", async () => {
      const appId = $("fyers-app-id").value.trim();
      const secret = $("fyers-secret").value.trim();
      msg.textContent = "";
      msg.className = "hint";
      if (!appId || !secret) {
        msg.textContent = "Both App ID and Secret Key are required.";
        msg.className = "hint err";
        return;
      }
      saveBtn.disabled = true;
      try {
        const res = await fetch("/api/settings/fyers", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ app_id: appId, secret_id: secret }) });
        const d = await res.json();
        if (res.ok && d.configured) {
          $("fyers-app-id").value = "";
          $("fyers-secret").value = "";
          msg.textContent = "Fyers credentials saved.";
          msg.className = "hint ok";
          refresh();
        } else {
          msg.textContent = d.error || "Failed to save Fyers credentials.";
          msg.className = "hint err";
        }
      } catch {
        msg.textContent = "Network error saving Fyers credentials.";
        msg.className = "hint err";
      } finally { saveBtn.disabled = false; }
    });

    loginBtn.addEventListener("click", () => {
      window.location.href = "/api/auth/fyers/login";
    });

    refresh();
  }

  // ── Alert push via generic event stream (low-frequency) ─────────────────

  let alertPushSource = null;

  function initAlertPush() {
    if (alertPushSource) return;   // never duplicate
    try {
      alertPushSource = new EventSource("/events/stream");
      alertPushSource.onmessage = (e) => {
        let envelope;
        try { envelope = JSON.parse(e.data); } catch { return; }
        const type = typeof envelope === "string"
          ? JSON.parse(envelope).type : envelope.type;
        if (type !== "alert.triggered") return;
        const d = typeof envelope.data === "string"
          ? JSON.parse(envelope.data) : envelope.data;
        pushAlertNotification(d);
      };
      alertPushSource.onerror = () => { /* EventSource auto-reconnects */ };
    } catch { /* silent */ }
  }

  function pushAlertNotification(d) {
    const notes = $("alert-notifications");
    if (!notes) return;
    const time = new Date().toLocaleTimeString();
    const cond = `${d.field} ${d.operator === "gt" ? ">" :
      d.operator === "lt" ? "<" : d.operator.replace("crosses_", "crosses ")} ${d.threshold}`;
    const div = document.createElement("div");
    div.className = "hint err";
    div.textContent =
      `${time} — ${d.tradingsymbol}: ${cond} (now ${d.observed_value})`;
    notes.prepend(div);
    while (notes.children.length > 20) notes.removeChild(notes.lastChild);
  }