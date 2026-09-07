/**
 * MarketHub WebUI — Market Data Subscriptions settings.
 *
 * DB-backed subscription preferences: 8 canonical index toggles, stock
 * add/remove, per-underlying derivative rules (futures/options, ATM ±
 * strikes, CE/PE), preview of the resolved contract set, and one-click
 * runtime apply (no restart). Talks ONLY to /api/subscriptions*.
 */

import { apiGet, apiPost, apiPut, apiDelete } from "../../api.js";

const $ = (id) => document.getElementById(id);

const getJSON = apiGet;
const sendJSON = (path, method, body) => {
  if (method === "POST") return apiPost(path, body);
  if (method === "PUT") return apiPut(path, body);
  if (method === "PATCH") {
    // api.js has no PATCH wrapper; use fetch directly with the same
    // error contract as _send.
    return fetch(path, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(async (resp) => {
      let data = null;
      try { data = await resp.json(); } catch { /* empty body */ }
      if (!resp.ok) {
        throw new Error((data && data.message) || `Request failed (HTTP ${resp.status})`);
      }
      return data;
    });
  }
  if (method === "DELETE") return apiDelete(path);
  throw new Error(`unsupported method: ${method}`);
};

let _bound = false;
let _stockCandidates = [];

function _msg(text, isError = false) {
  const el = $("mdsub-message");
  if (!el) return;
  el.textContent = text || "";
  el.classList.toggle("err", !!isError);
}

function _setStatus(text, ok) {
  const el = $("mdsub-status");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("chip-off", !ok);
  el.classList.toggle("chip-on", !!ok);
}

function _renderIndices(indices) {
  const wrap = $("mdsub-indices");
  if (!wrap) return;
  wrap.innerHTML = "";
  for (const idx of indices) {
    const row = document.createElement("label");
    row.className = "mdsub-check";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = !!idx.enabled;
    cb.dataset.label = idx.label;
    cb.addEventListener("change", async () => {
      try {
        await sendJSON("/api/subscriptions/indices", "PATCH",
          { label: idx.label, enabled: cb.checked });
        _msg(`${idx.label} ${cb.checked ? "enabled" : "disabled"}`);
        await refreshStatus();
      } catch (e) {
        _msg(`Save failed: ${e.message || e}`, true);
        cb.checked = !cb.checked;
      }
    });
    row.appendChild(cb);
    row.appendChild(document.createTextNode(` ${idx.label}`));
    wrap.appendChild(row);
  }
}

function _renderStocks(stocks) {
  const body = $("mdsub-stocks-body");
  if (!body) return;
  body.innerHTML = "";
  if (!stocks.length) {
    body.innerHTML = '<tr><td colspan="4" class="empty-row">No stock subscriptions.</td></tr>';
    return;
  }
  for (const s of stocks) {
    const tr = document.createElement("tr");
    const tdSym = document.createElement("td");
    tdSym.textContent = s.label;
    const tdKey = document.createElement("td");
    tdKey.textContent = s.key;
    tdKey.className = "mono";
    const tdEn = document.createElement("td");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = !!s.enabled;
    cb.addEventListener("change", async () => {
      try {
        await sendJSON("/api/subscriptions/stocks", "PATCH",
          { key: s.key, enabled: cb.checked });
        await refreshStatus();
      } catch (e) {
        _msg(`Save failed: ${e.message || e}`, true);
        cb.checked = !cb.checked;
      }
    });
    tdEn.appendChild(cb);
    const tdAct = document.createElement("td");
    const del = document.createElement("button");
    del.className = "btn btn-compact btn-outline-danger";
    del.textContent = "Remove";
    del.addEventListener("click", async () => {
      try {
        await sendJSON(`/api/subscriptions/stocks?key=${encodeURIComponent(s.key)}`,
          "DELETE");
        await loadSubscriptions();
      } catch (e) {
        _msg(`Remove failed: ${e.message || e}`, true);
      }
    });
    tdAct.appendChild(del);
    tr.append(tdSym, tdKey, tdEn, tdAct);
    body.appendChild(tr);
  }
}

function _fillRuleForm(rule) {
  $("mdsub-fut-enabled").checked = !!rule.futures_enabled;
  $("mdsub-fut-count").value = String(rule.futures_count || 1);
  $("mdsub-opt-enabled").checked = !!rule.options_enabled;
  $("mdsub-opt-count").value = String(rule.options_count || 1);
  $("mdsub-strikes-below").value = String(rule.strikes_below || 0);
  $("mdsub-strikes-above").value = String(rule.strikes_above || 0);
  $("mdsub-calls").checked = rule.calls_enabled !== false;
  $("mdsub-puts").checked = rule.puts_enabled !== false;
}

function _readRuleForm(underlying) {
  return {
    underlying,
    futures_enabled: $("mdsub-fut-enabled").checked,
    futures_count: Number($("mdsub-fut-count").value) || 1,
    options_enabled: $("mdsub-opt-enabled").checked,
    options_count: Number($("mdsub-opt-count").value) || 1,
    strikes_below: Number($("mdsub-strikes-below").value) || 0,
    strikes_above: Number($("mdsub-strikes-above").value) || 0,
    calls_enabled: $("mdsub-calls").checked,
    puts_enabled: $("mdsub-puts").checked,
  };
}

async function refreshStatus() {
  try {
    const st = await getJSON("/api/subscriptions/status");
    const applied = st.last_apply && st.last_apply.results
      ? Object.values(st.last_apply.results) : [];
    const allApplied = applied.length > 0
      && applied.every((r) => r.applied);
    _setStatus(
      `Resolved: ${st.resolved_count ?? "?"} · applied: ${allApplied ? "yes" : "pending"}`,
      allApplied);
  } catch { _setStatus("—", false); }
}

export async function loadSubscriptions() {
  try {
    const prefs = await getJSON("/api/subscriptions");
    _renderIndices(prefs.indices || []);
    _renderStocks(prefs.stocks || []);
    await refreshStatus();
  } catch (e) {
    _msg(`Load failed: ${e.message || e}`, true);
  }
}

async function searchStocks(q) {
  const box = $("mdsub-stock-results");
  try {
    const data = await getJSON(
      `/api/instruments/search?q=${encodeURIComponent(q)}&limit=8`);
    _stockCandidates = (data.results || [])
      .filter((r) => r.instrument_token);
    box.textContent = _stockCandidates.length
      ? "Pick: " + _stockCandidates.map((r) => r.tradingsymbol).join(", ")
      : "No catalog matches";
  } catch (e) {
    box.textContent = `Search failed: ${e.message || e}`;
  }
}

async function addSelectedStock() {
  const q = $("mdsub-stock-search").value.trim();
  if (!q) return;
  // Resolve through the catalog: exact token match wins, else the first
  // candidate whose tradingsymbol matches the query (case-insensitive).
  const exact = _stockCandidates.find(
    (r) => (r.tradingsymbol || "").toUpperCase() === q.toUpperCase());
  const chosen = exact || _stockCandidates[0];
  if (!chosen) {
    await searchStocks(q);
    const again = _stockCandidates[0];
    if (!again) { _msg("No instrument found", true); return; }
    await _persistStock(again);
    return;
  }
  await _persistStock(chosen);
}

async function _persistStock(chosen) {
  try {
    await sendJSON("/api/subscriptions/stocks", "POST", {
      key: chosen.instrument_token,
      label: chosen.tradingsymbol || chosen.instrument_token,
    });
    $("mdsub-stock-search").value = "";
    $("mdsub-stock-results").textContent = "";
    _stockCandidates = [];
    await loadSubscriptions();
  } catch (e) {
    _msg(`Add failed: ${e.message || e}`, true);
  }
}

export function initSubscriptionUI() {
  if (_bound) return;
  _bound = true;

  $("mdsub-stock-search")?.addEventListener("input", (ev) => {
    const q = ev.target.value.trim();
    if (q.length >= 2) searchStocks(q);
  });
  $("mdsub-stock-add")?.addEventListener("click", () => addSelectedStock());
  $("mdsub-rule-load")?.addEventListener("click", async () => {
    const und = $("mdsub-rule-underlying").value.trim().toUpperCase();
    if (!und) return;
    try {
      const rules = (await getJSON("/api/subscriptions/rules")).rules || [];
      const rule = rules.find((r) => r.underlying === und);
      _fillRuleForm(rule || { underlying: und });
      _msg(rule ? `Loaded rule for ${und}` : `New rule for ${und}`);
    } catch (e) {
      _msg(`Load failed: ${e.message || e}`, true);
    }
  });
  $("mdsub-rule-save")?.addEventListener("click", async () => {
    const und = $("mdsub-rule-underlying").value.trim().toUpperCase();
    if (!und) { _msg("Underlying required", true); return; }
    try {
      await sendJSON("/api/subscriptions/rules", "PUT", _readRuleForm(und));
      _msg(`Rule saved for ${und}`);
      await refreshStatus();
    } catch (e) {
      _msg(`Save failed: ${e.message || e}`, true);
    }
  });
  $("mdsub-rule-preview")?.addEventListener("click", async () => {
    const und = $("mdsub-rule-underlying").value.trim().toUpperCase();
    if (!und) { _msg("Underlying required", true); return; }
    // Save first so preview reflects exactly what would be applied.
    try {
      await sendJSON("/api/subscriptions/rules", "PUT", _readRuleForm(und));
    } catch (e) {
      _msg(`Save failed: ${e.message || e}`, true);
      return;
    }
    try {
      const pv = await getJSON("/api/subscriptions/preview");
      const atms = Object.entries(pv.atm || {})
        .map(([u, s]) => `${u} ATM ${s}`).join(", ");
      const pend = (pv.pending || []).join(", ");
      $("mdsub-preview").textContent =
        `Resolved: ${pv.resolved_count} contracts `
        + Object.entries(pv.by_provider || {})
          .map(([p, n]) => `${p}: ${n}`).join(", ")
        + (atms ? ` | ${atms}` : "")
        + (pend ? ` | pending: ${pend}` : "")
        + (pv.notes || []).length ? ` | ${pv.notes.join("; ")}` : "";
      await refreshStatus();
    } catch (e) {
      _msg(`Preview failed: ${e.message || e}`, true);
    }
  });
  $("mdsub-rule-delete")?.addEventListener("click", async () => {
    const und = $("mdsub-rule-underlying").value.trim().toUpperCase();
    if (!und) return;
    try {
      await sendJSON(
        `/api/subscriptions/rules?underlying=${encodeURIComponent(und)}`,
        "DELETE");
      _msg(`Rule removed for ${und}`);
      $("mdsub-preview").textContent = "";
      await refreshStatus();
    } catch (e) {
      _msg(`Remove failed: ${e.message || e}`, true);
    }
  });
  $("mdsub-apply")?.addEventListener("click", async () => {
    try {
      const out = await sendJSON("/api/subscriptions/apply", "POST", {});
      const parts = Object.entries(out.apply?.results || {})
        .map(([p, r]) => `${p}: ${r.applied ? "applied" : r.reason}`
          + (r.applied ? ` (+${r.added}/-${r.removed})` : ""));
      _msg(`Apply — resolved ${out.resolved_count}. ${parts.join(" | ")}`);
      await refreshStatus();
    } catch (e) {
      _msg(`Apply failed: ${e.message || e}`, true);
    }
  });
}
