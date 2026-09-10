import type { SentimentSummary } from "../types";

interface Props {
  summary: SentimentSummary;
  total: number;
  sources: number;
}

// Overall sentiment: label badge, average score, distribution bars.
export function SentimentSummary({ summary, total, sources }: Props) {
  const cls =
    summary.avg > 0 ? "bull" : summary.avg < 0 ? "bear" : "neutral";
  const denom = summary.count || 1;
  return (
    <div className="panel panel-spaced">
      <div className="panel-header">
        <h2>Overall Sentiment</h2>
      </div>
      <div className="sentiment-overall">
        <span className={`ui-badge ${cls}`}>{summary.label}</span>
        <span className="sentiment-avg">{summary.avg.toFixed(2)}</span>
        <span className="muted">
          {total} items · {sources} sources
        </span>
      </div>
      <div className="sentiment-bars">
        <Bar label="Bullish" value={summary.pos} total={denom} cls="bar-pos" />
        <Bar label="Neutral" value={summary.neu} total={denom} cls="bar-neu" />
        <Bar label="Bearish" value={summary.neg} total={denom} cls="bar-neg" />
      </div>
    </div>
  );
}

function Bar({
  label,
  value,
  total,
  cls,
}: {
  label: string;
  value: number;
  total: number;
  cls: string;
}) {
  const pct = total ? Math.round((value / total) * 100) : 0;
  return (
    <div className="sentiment-bar-row">
      <span className="sentiment-bar-label">{label}</span>
      <div className="sentiment-bar-track">
        <div
          className={`sentiment-bar-fill ${cls}`}
          style={{ ["--pct" as string]: `${pct}%` } as React.CSSProperties}
        />
      </div>
      <span className="sentiment-bar-count">{value}</span>
    </div>
  );
}
