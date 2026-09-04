/**
 * MarketHub WebUI — news source management (CRUD + test modal).
 *
 * Owns the Sources panel on the News view: table render, add/edit modal,
 * test-source, enable/disable/delete. Source ids are untrusted input:
 * they travel only via data attributes + delegated listeners (never
 * inline onclick) and are URL-encoded on the way out.
 */

import { $, esc, escAttr } from "./utils.js";
import { apiPost, apiDelete } from "./api.js";

let _newsSources = [];
let _actionsBound = false;

export function getNewsSources() {
  return _newsSources;
}

function _toggleSourceTypeFields() {
  const type = $("news-src-type")?.value;
  const urlRow = $("news-config-url-row");
  const subRow = $("news-config-sub-row");
  if (urlRow) urlRow.classList.toggle("hidden", type !== "rss");
  if (subRow) subRow.classList.toggle("hidden", type !== "reddit");
}

export async function loadNewsSources() {
  try {
    const resp = await fetch("/api/news/sources");
    if (!resp.ok) return _newsSources;
    const data = await resp.json();
    _newsSources = data.sources || [];
    _renderNewsSources();
  } catch { /* ignore */ }
  return _newsSources;
}

function _renderNewsSources() {
  const tbody = $("news-sources-body");
  if (!tbody) return;
  if (!_newsSources.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty-row">No sources configured</td></tr>';
    return;
  }
  tbody.innerHTML = "";
  _newsSources.forEach(s => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono text-xs">${esc(s.source_id)}</td>
      <td>${esc(s.name)}</td>
      <td><span class="news-action-btn">${esc(s.source_type)}</span></td>
      <td>${esc(s.category || "—")}</td>
      <td>${s.enabled
        ? '<span class="news-status-on">ON</span>'
        : '<span class="news-status-off">OFF</span>'}</td>
      <td class="mono text-xs max-col-180 truncate">${
        s.source_type === "rss"
          ? esc((s.config_json?.url || "").substring(0, 50))
          : "r/" + esc(s.config_json?.subreddit || "")
      }</td>
      <td>
        <button class="news-action-btn" data-news-action="toggle" data-news-id="${escAttr(s.source_id)}" data-news-enable="${s.enabled ? 0 : 1}">${s.enabled ? "Disable" : "Enable"}</button>
        <button class="news-action-btn" data-news-action="edit" data-news-id="${escAttr(s.source_id)}">Edit</button>
        <button class="news-action-btn danger" data-news-action="delete" data-news-id="${escAttr(s.source_id)}">Del</button>
      </td>`;
    tbody.appendChild(tr);
  });
}

function _onNewsActionClick(e) {
  const btn = e.target && e.target.closest
    ? e.target.closest("[data-news-action]") : null;
  if (!btn) return;
  const action = btn.getAttribute("data-news-action");
  const sourceId = btn.getAttribute("data-news-id") || "";
  if (action === "toggle") {
    _newsToggle(sourceId, btn.getAttribute("data-news-enable") === "1");
  } else if (action === "edit") {
    _newsEdit(sourceId);
  } else if (action === "delete") {
    _newsDelete(sourceId);
  }
}

function _openAddSourceModal() {
  $("news-modal-title").textContent = "Add Source";
  $("news-src-id").value = "";
  $("news-src-id").disabled = false;
  $("news-src-name").value = "";
  $("news-src-type").value = "rss";
  $("news-src-category").value = "";
  $("news-src-url").value = "";
  $("news-src-subreddit").value = "";
  $("news-test-result").textContent = "";
  _toggleSourceTypeFields();
  $("news-source-modal").classList.remove("hidden");
}

function _closeSourceModal() {
  $("news-source-modal").classList.add("hidden");
}

async function _saveSource() {
  const sourceId = $("news-src-id").value.trim();
  const name = $("news-src-name").value.trim();
  const sourceType = $("news-src-type").value;
  const category = $("news-src-category").value.trim();
  const configJson = sourceType === "rss"
    ? { url: $("news-src-url").value.trim() }
    : { subreddit: $("news-src-subreddit").value.trim() };

  if (!sourceId || !name) {
    $("news-test-result").textContent = "Source ID and Name are required";
    $("news-test-result").className = "hint err";
    return;
  }

  try {
    const data = await apiPost("/api/news/sources", {
      source_id: sourceId, name, source_type: sourceType,
      category, enabled: true, config_json: configJson,
    });
    if (data && data.status === "ok") {
      _closeSourceModal();
      loadNewsSources();
    } else {
      $("news-test-result").textContent = (data && data.message) || "Save failed";
      $("news-test-result").className = "hint err";
    }
  } catch (e) {
    $("news-test-result").textContent = e.message || "Save failed";
    $("news-test-result").className = "hint err";
  }
}

async function _testSource() {
  const sourceType = $("news-src-type").value;
  const configJson = sourceType === "rss"
    ? { url: $("news-src-url").value.trim() }
    : { subreddit: $("news-src-subreddit").value.trim() };

  const resultEl = $("news-test-result");
  resultEl.textContent = "Testing…";
  resultEl.className = "hint";
  resultEl.className = "hint";

  try {
    const resp = await fetch("/api/news/sources/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_type: sourceType, config_json: configJson }),
    });
    const data = await resp.json();
    if (data.reachable) {
      resultEl.textContent = `✓ ${data.message}` +
        (data.sample_titles ? ` — "${data.sample_titles[0]}"` : "");
      resultEl.className = "hint";
      resultEl.className = "hint ok";
    } else {
      resultEl.textContent = `✗ ${data.message}`;
      resultEl.className = "hint err";
      resultEl.className = "hint err";
    }
  } catch (e) {
    resultEl.textContent = "Test failed: " + e.message;
    resultEl.className = "hint err";
    resultEl.className = "hint err";
  }
}

async function _newsToggle(sourceId, enable) {
  const action = enable ? "enable" : "disable";
  try {
    await apiPost(`/api/news/sources/${encodeURIComponent(sourceId)}/${action}`, {});
    loadNewsSources();
  } catch { /* ignore */ }
}

function _newsEdit(sourceId) {
  const src = _newsSources.find(s => s.source_id === sourceId);
  if (!src) return;
  $("news-modal-title").textContent = "Edit Source";
  $("news-src-id").value = src.source_id;
  $("news-src-id").disabled = true;
  $("news-src-name").value = src.name;
  $("news-src-type").value = src.source_type;
  $("news-src-category").value = src.category || "";
  $("news-src-url").value = src.config_json?.url || "";
  $("news-src-subreddit").value = src.config_json?.subreddit || "";
  $("news-test-result").textContent = "";
  _toggleSourceTypeFields();
  $("news-source-modal").classList.remove("hidden");
}

async function _newsDelete(sourceId) {
  if (!confirm("Delete source " + sourceId + "?")) return;
  try {
    await apiDelete(`/api/news/sources/${encodeURIComponent(sourceId)}`);
    loadNewsSources();
  } catch { /* ignore */ }
}

export function initSourcesUI() {
  const addBtn = $("news-add-source");
  if (addBtn) addBtn.addEventListener("click", _openAddSourceModal);
  const closeBtn = $("news-modal-close");
  if (closeBtn) closeBtn.addEventListener("click", _closeSourceModal);
  const cancelBtn = $("news-modal-cancel");
  if (cancelBtn) cancelBtn.addEventListener("click", _closeSourceModal);
  const saveBtn = $("news-modal-save");
  if (saveBtn) saveBtn.addEventListener("click", _saveSource);
  const testBtn = $("news-test-source");
  if (testBtn) testBtn.addEventListener("click", _testSource);
  const typeSelect = $("news-src-type");
  if (typeSelect) typeSelect.addEventListener("change", _toggleSourceTypeFields);
  // Delegated action handling for the sources table (no inline JS:
  // source ids are untrusted input and must never enter onclick).
  // Registered once; the tbody persists across re-renders.
  if (!_actionsBound) {
    _actionsBound = true;
    const tbody = $("news-sources-body");
    if (tbody) tbody.addEventListener("click", _onNewsActionClick);
  }
}
