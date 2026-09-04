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
  initAuth, initCredentialDelete, initCredentialSettings, initFyers,
  pollAuthStatus,
} from "./auth.js";
import { initSettingsUI } from "./features/settings/index.js";
import { initGeneralSettings } from "./features/settings/general.js";
import { initAIMCPSettings } from "./features/settings/ai-mcp.js";
import { initBackupSettings } from "./features/settings/backup.js";
import { initSplitters } from "./core/splitter.js";

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
    const isLight = theme === "light";
    // MarketHub owns theming via the [data-theme] attribute only.
    document.documentElement.setAttribute("data-theme", isLight ? "light" : "dark");
    localStorage.setItem("mh-theme", theme);
  }

  function toggleTheme() {
    const cur = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
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
// ==== AI provider settings live in ./features/settings/ai-mcp.js ====

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

  // (backup control lives in ./features/settings/backup.js)

  // (Fyers credentials + login live in ./auth.js)

  // (application settings form lives in ./features/settings/general.js)

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
    initFyers();
    initGeneralSettings();
    initAIMCPSettings();
    initBackupSettings();
    initSettingsUI();
    initChat();
    initAlertPush();
    initSourceControls();
    initAIAlerts();
    initSourcesUI();
    initNewsUI();
    initMCPTools();
    initLogsUI();
    initSplitters();
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
