/**
 * MarketHub — compact trading terminal application startup/orchestration.
 *
 * Plain ES module (no framework, no bundler). Feature areas with their
 * own UI state live in sibling modules (news list/history, source CRUD,
 * live logs, hash routing, shared REST/DOM helpers); this file keeps the
 * remaining terminal logic and wires everything together in init().
 *
 * One EventSource connection to /api/market/stream.
 * In-place DOM updates (no full rebuild).
 * Client-side section switching (no backend routes per view).
 */
"use strict";

import { initRouter, onViewEnter, initNav } from "./router.js";
import { initSourcesUI } from "./sources.js";
import { initNewsUI, openNews } from "./news.js";
import { initLogsUI, openLogs } from "./logs.js";
import { connectSSE, initFilter, loadInitialQuotes } from "./market.js";
import { initDrawer } from "./quotes.js";
import { initCharts } from "./charts.js";
import { initAlerts, initAlertPush } from "./alerts.js";
import { initAIAlerts, openAIAlerts } from "./ai-alerts.js";
import { initMCPTools, openMCPTools } from "./mcp-tools.js";
import { initInstruments } from "./instruments.js";
import { initWatchlists } from "./watchlists.js";
import { initOptionChain } from "./option-chain.js";
import { initSourceControls, pollSources } from "./market-sources.js";
import {
  initAuth, initCredentialDelete, initCredentialSettings, pollAuthStatus,
} from "./auth.js";

  // ── DOM shortcuts ───────────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);

  // ── Shared session state ──────────────────────────────────────────────
  // Feature state lives in its owning ES module (market/quotes/alerts/
  // watchlists/charts/option-chain/auth/market-sources); only truly
  // global orchestration remains here.

  // ── Utilities ───────────────────────────────────────────────────────────
  // (formatting/DOM helpers live in ./utils.js and are imported by the
  // feature modules that need them)

  // ── Theme ───────────────────────────────────────────────────────────────

  function initTheme() {
    const saved = localStorage.getItem("mh-theme") || "dark";
    applyTheme(saved);
    $("theme-toggle").addEventListener("click", toggleTheme);
    $("settings-theme-toggle").addEventListener("click", toggleTheme);
  }

  function applyTheme(theme) {
    document.documentElement.className = theme === "light" ? "wa-theme-light" : "wa-theme-dark";
    localStorage.setItem("mh-theme", theme);
  }

  function toggleTheme() {
    const cur = document.documentElement.classList.contains("wa-theme-light") ? "dark" : "light";
    applyTheme(cur);
  }

  // ── Navigation ──────────────────────────────────────────────────────────
  // (view state + switching live in ./router.js)

  // ── Live market data (SSE + tables + ticker + cards) lives in ./market.js.

  // ── Market source status + lifecycle UI lives in ./market-sources.js.

  // ── Upstox auth + credentials UI lives in ./auth.js.

  // ── Movers + market status live in ./market.js; drawer in ./quotes.js.
  // ── Market filter lives in ./market.js; snapshot fetch too. ──

  // ── Instruments search + sync lives in ./instruments.js. ──

  // ── Watchlists live in ./watchlists.js. ──

  // ── Option chain lives in ./option-chain.js. ──

  // ── Charts live in ./charts.js. ──

  // ── Alerts (CRUD/history/push) live in ./alerts.js. ──

// ==== AI provider settings ====
  async function initAIProvider() {
    const saveBtn = document.getElementById("ai-save");
    if (!saveBtn) return;
    const msg = document.getElementById("ai-message");
    try {
      const st = await (await fetch("/api/chat/status")).json();
      if (st.endpoint) {
        document.getElementById("ai-endpoint").value = st.endpoint;
      }
      if (st.model) {
        document.getElementById("ai-model").value = st.model;
      }
    } catch { /* optional */ }

    saveBtn.addEventListener("click", async () => {
      msg.textContent = "";
      msg.className = "hint";
      const endpoint =
        document.getElementById("ai-endpoint").value.trim();
      const model = document.getElementById("ai-model").value.trim();
      const key = document.getElementById("ai-key").value.trim();
      if (!endpoint || !model || !key) {
        msg.textContent = "Endpoint, model and API key are required.";
        msg.className = "hint err";
        return;
      }
      saveBtn.disabled = true;
      try {
        const res = await fetch("/api/chat/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ endpoint, model, api_key: key }),
        });
        const d = await res.json();
        if (res.ok && d.status === "saved") {
          msg.textContent = "AI provider saved. Chat is ready.";
          msg.className = "hint ok";
          document.getElementById("ai-key").value = "";
        } else {
          msg.textContent = d.error || "Save failed.";
          msg.className = "hint err";
        }
      } catch {
        msg.textContent = "Network error saving AI settings.";
        msg.className = "hint err";
      } finally { saveBtn.disabled = false; }
    });
  }

// ==== Chat (AI assistant over MarketHub tools) ====
  let chatHistory = [];   // client-side session only

  function chatAppend(role, text) {
    const box = document.getElementById("chat-messages");
    if (!box) return null;
    const div = document.createElement("div");
    div.className = "hint " + (role === "user" ? "" : "ok");
    div.style.whiteSpace = "pre-wrap";
    div.style.marginBottom = "6px";
    div.textContent = (role === "user" ? "You: " : "Assistant: ") + text;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
    return div;
  }

  function chatActivity(text) {
    const el = document.getElementById("chat-activity");
    if (el) el.textContent = text || "";
  }

  async function initChat() {
    const input = document.getElementById("chat-input");
    const send = document.getElementById("chat-send");
    const clear = document.getElementById("chat-clear");
    if (!input || !send) return;

    try {
      const st = await (await fetch("/api/chat/status")).json();
      const chip = document.getElementById("chat-provider-chip");
      if (chip) {
        chip.textContent = st.configured
          ? ("AI: " + st.model) : "AI not configured";
        chip.className = "chip " + (st.configured ? "chip-on" : "chip-off");
      }
    } catch { /* status optional */ }

    if (clear) clear.addEventListener("click", () => {
      chatHistory = [];
      const box = document.getElementById("chat-messages");
      if (box) box.innerHTML = "";
      chatActivity("");
    });

    async function sendMsg() {
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      send.disabled = true;
      const errEl = document.getElementById("chat-error");
      errEl.style.display = "none";
      chatAppend("user", text);
      let assistantText = "";
      const live = chatAppend("assistant", "\u2026");
      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text, history: chatHistory }),
        });
        if (!res.ok) {
          const d = await res.json().catch(() => ({}));
          throw new Error(d.error || ("HTTP " + res.status));
        }
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const chunk = await reader.read();
          if (chunk.done) break;
          buf += dec.decode(chunk.value, { stream: true });
          let idx;
          while ((idx = buf.indexOf("\n\n")) >= 0) {
            const raw = buf.slice(0, idx); buf = buf.slice(idx + 2);
            if (!raw.startsWith("data:")) continue;
            let ev;
            try { ev = JSON.parse(raw.slice(5).trim()); } catch { continue; }
            if (ev.type === "tool_start") {
              chatActivity("using tool: " + ev.name);
            } else if (ev.type === "delta") {
              assistantText += ev.text;
              live.textContent = "Assistant: " + assistantText;
              chatActivity("");
            } else if (ev.type === "error") {
              errEl.textContent = ev.message;
              errEl.style.display = "block";
            }
          }
        }
        chatHistory.push({ role: "user", content: text });
        if (assistantText) {
          chatHistory.push({ role: "assistant", content: assistantText });
        }
        live.textContent = "Assistant: " +
          (assistantText || "(no answer)");
      } catch (e) {
        live.textContent = "Assistant: (failed)";
        errEl.textContent = String(e.message || e);
        errEl.style.display = "block";
      } finally {
        send.disabled = false;
        input.focus();
      }
    }

    send.addEventListener("click", sendMsg);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") sendMsg();
    });
  }

  // (pushAlertNotification lives in ./alerts.js)

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

  function initFyers() {
    const saveBtn = $("fyers-save");
    if (!saveBtn) return;
    const msg = $("fyers-message");
    const loginBtn = $("fyers-login-btn");

    async function refresh() {
      try {
        const res = await fetch("/api/settings/fyers");
        const d = await res.json();
        // App Credentials: are App ID + Secret saved (encrypted)?
        const credChip = $("fyers-status");
        if (d.store_error) {
          credChip.textContent = "Credential Store Error";
          credChip.className = "chip chip-off";
          msg.textContent =
            "Encrypted credentials exist but the current master.key cannot " +
            "read them. Restore the matching master.key backup — do NOT " +
            "re-save credentials over them unless you intend to replace.";
          msg.className = "hint err";
        } else if (d.app_id_configured && d.secret_configured) {
          credChip.textContent = "Configured";
          credChip.className = "chip chip-on";
        } else {
          credChip.textContent = "Not configured";
          credChip.className = "chip chip-off";
        }
        // Daily Login: distinct from credentials — is a session usable now?
        const loginChip = $("fyers-login-status");
        if (!d.login_available) {
          loginChip.textContent = "Credentials Required";
          loginChip.className = "chip chip-off";
        } else if (d.access_token_active) {
          loginChip.textContent = "Daily Login Active";
          loginChip.className = "chip chip-on";
        } else {
          loginChip.textContent = "Login Required";
          loginChip.className = "chip chip-off";
        }
        loginBtn.style.display = d.login_available ? "" : "none";
        // Feed runtime state comes from the source manager, not auth.
        try {
          const sres = await fetch("/api/sources/status");
          const sd = await sres.json();
          const src = (sd.sources || []).find(
            (s) => s.name === "fyers" || (s.type || "").indexOf("fyers") >= 0);
          $("fyers-feed-state").textContent =
            src ? friendlyState(src.state || "unknown") : "source not configured";
        } catch { /* keep placeholder */ }
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
        const pin = ($("fyers-pin") || {}).value?.trim() || "";
        const res = await fetch("/api/settings/fyers", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ app_id: appId, secret_id: secret,
                                 pin: pin }) });
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

  function initAppSettings() {
    const saveBtn = $("app-save");
    if (!saveBtn) return;
    const msg = $("app-message");

    async function refresh() {
      try {
        const res = await fetch("/api/settings/app");
        const d = await res.json();
        $("app-base-url").textContent = d.public_base_url || "—";
        $("app-base-url").className =
          "chip " + (d.public_base_url ? "chip-on" : "chip-off");
        $("app-fyers-callback").textContent = d.fyers_callback_url || "—";
        $("app-base-url-input").value = d.public_base_url || "";
      } catch { /* silent */ }
    }

    saveBtn.addEventListener("click", async () => {
      const base = $("app-base-url-input").value.trim();
      msg.textContent = "";
      msg.className = "hint";
      if (!base) {
        msg.textContent = "Public Base URL is required.";
        msg.className = "hint err";
        return;
      }
      saveBtn.disabled = true;
      try {
        const res = await fetch("/api/settings/app", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ public_base_url: base }) });
        const d = await res.json();
        if (res.ok && d.status === "ok") {
          msg.textContent = "Saved. Restart MarketHub to apply.";
          msg.className = "hint ok";
          refresh();
        } else {
          msg.textContent = d.error || "Failed to save application settings.";
          msg.className = "hint err";
        }
      } catch {
        msg.textContent = "Network error saving application settings.";
        msg.className = "hint err";
      } finally { saveBtn.disabled = false; }
    });

    refresh();
  }

  // ── AI alert observability lives in ./ai-alerts.js. ──

  // ── MCP Tools UI lives in ./mcp-tools.js. ──

  // ── News + Logs views live in ES modules (./news.js, ./sources.js,
  // ./logs.js); init*UI() + router hooks are wired in init() below.

  function init() {
    initTheme();
    initNav();
    initFilter();
    initAuth();
    initCredentialSettings();
    initCredentialDelete();
    initDrawer();
    initInstruments();
    initWatchlists();
    initOptionChain();
    initCharts();
    initAlerts();
    initBackup();
    initFyers();
    initAppSettings();
    initAIProvider();
    initChat();
    initAlertPush();
    initSourceControls();
    initAIAlerts();
    initSourcesUI();
    initNewsUI();
    initMCPTools();
    initLogsUI();
    // Route hooks (registered once): direct #/view loads, F5,
    // and back/forward all initialize their views; nav clicks are also
    // observed by the router (switchView uses replaceState, silent).
    onViewEnter("news", openNews);
    onViewEnter("logs", openLogs);
    onViewEnter("ai-alerts", openAIAlerts);
    onViewEnter("mcp", openMCPTools);
    initRouter();
    loadInitialQuotes();
    connectSSE();
    pollSources();                     // immediate status render (no 10s wait)
    setInterval(pollSources, 10000);   // then poll source status every 10 s
    pollAuthStatus();                  // auth snapshot feeds Sources controls
    setInterval(pollAuthStatus, 10000);
  }

  document.addEventListener("DOMContentLoaded", init);
