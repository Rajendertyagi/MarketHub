/**
 * MarketHub WebUI — Market Sentiment entry point.
 *
 * The Sentiment dashboard lives in ./features/sentiment/ (index.js). This
 * module preserves the historical import surface used by app.js
 * (initSentimentUI, openSentiment) and must stay thin: lifecycle delegation
 * only, no rendering or API logic here.
 */

import {
  initSentimentUI as _initSentimentUI,
  openSentiment as _openSentiment,
} from "./features/sentiment/index.js";

export function initSentimentUI() {
  _initSentimentUI();
}

export async function openSentiment() {
  await _openSentiment();
}
