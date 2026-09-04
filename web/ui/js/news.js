/**
 * MarketHub WebUI — news entry point (N-UI1 compatibility shim).
 *
 * The News reader lives in ./features/news/ (state, feeds, article-list,
 * reader, sentiment, index). This module preserves the historical import
 * surface used by app.js (`initNewsUI`, `openNews`) and must stay thin:
 * lifecycle delegation only, no rendering or API logic here.
 */

import {
  initNewsUI as _initNewsUI,
  openNews as _openNews,
} from "./features/news/index.js";

export function initNewsUI() {
  _initNewsUI();
}

export async function openNews() {
  await _openNews();
}
