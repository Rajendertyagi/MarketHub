/**
 * MarketHub WebUI — News sentiment presentation (N-UI1).
 *
 * Sentiment is compact metadata (row badges, reader header, slim strip),
 * never a standalone dashboard panel. All formatting/aggregation lives
 * here so list + reader + strip stay consistent.
 */

export function scoreClass(label) {
  // Maps domain sentiment to MarketHub ui-badge modifier classes.
  return label === "positive" ? "bull"
    : label === "negative" ? "bear" : "neutral";
}

export function formatScore(score) {
  if (score == null || isNaN(Number(score))) return "n/a";
  const v = Number(score);
  return (v > 0 ? "+" : "") + v.toFixed(2);
}

export function labelText(label) {
  if (label === "positive" || label === "negative" || label === "neutral") {
    return label[0].toUpperCase() + label.slice(1);
  }
  return "n/a";
}

/**
 * Aggregate over the current article order using the sentiment map.
 * Returns null when no scores are available (honest unavailable state).
 */
export function aggregateSentiment(order, byId) {
  if (!order || !order.length || !byId || !byId.size) return null;
  let sum = 0; let n = 0; let pos = 0; let neg = 0; let neu = 0;
  order.forEach((id) => {
    const s = byId.get(id);
    if (!s || s.score == null) return;
    n++; sum += Number(s.score);
    if (s.sentiment === "positive") pos++;
    else if (s.sentiment === "negative") neg++;
    else neu++;
  });
  if (!n) return null;
  return { avg: sum / n, count: n, pos, neg, neu };
}

export function aggregateText(count, agg) {
  if (!agg) return `${count} article${count === 1 ? "" : "s"} | sentiment unavailable`;
  const avg = agg.avg;
  const sign = avg > 0 ? "+" : "";
  return `${count} article${count === 1 ? "" : "s"} | Overall ${sign}${avg.toFixed(2)} | ` +
    `${agg.pos} positive · ${agg.neu} neutral · ${agg.neg} negative`;
}
