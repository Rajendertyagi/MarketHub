import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Link } from "react-router-dom";
import { getSubscriptions } from "@/api/market";
import { AsyncStateView } from "@/components/ui";
import type { ApiError } from "@/types";
import { fmtNum, fmtPct, tone } from "@/utils/format";
import { isStaleQuote } from "../format";
import type { MarketQuote } from "../types";

interface Props {
  quotes: MarketQuote[];
}

interface IndexDef {
  label: string;
  key: string;
}

const VIX_LABEL = "INDIA VIX";

// Live major indices. The subscription registry owns the canonical index list
// (label + upstox_key); each index key matches the live quote instrument_token,
// so we join on that and render the streaming LTP / change %.
export function IndicesWidget({ quotes }: Props) {
  const subs = useQuery({
    queryKey: ["dash-subscriptions"],
    queryFn: ({ signal }) => getSubscriptions(signal),
  });

  const byKey = useMemo(() => new Map(quotes.map((q) => [q.instrument_token, q])), [quotes]);

  const indices = (subs.data?.indices ?? []).filter((i) => i.enabled);
  const vix = indices.find((i) => i.label === VIX_LABEL);
  const others = indices.filter((i) => i.label !== VIX_LABEL);

  const status: "loading" | "error" | "empty" | null = subs.isLoading
    ? "loading"
    : subs.isError
      ? "error"
      : indices.length === 0
        ? "empty"
        : null;

  if (status) {
    return (
      <AsyncStateView
        status={status}
        error={subs.error as ApiError | undefined}
        loadingLabel="Loading indices…"
        emptyLabel="No indices subscribed"
      />
    );
  }

  return (
    <div className="idx-hero">
      {vix && <IndexCard idx={vix} q={byKey.get(vix.key)} variant="vix" />}
      <div className="idx-grid">
        {others.map((idx) => (
          <IndexCard key={idx.key} idx={idx} q={byKey.get(idx.key)} />
        ))}
      </div>
    </div>
  );
}

// A single hero stat card: the index name, its live value, and the change /
// % change colored by direction. A real link into Charts (never a clickable
// div) so keyboard and assistive tech navigate natively.
function IndexCard({
  idx,
  q,
  variant,
}: {
  idx: IndexDef;
  q: MarketQuote | undefined;
  variant?: "vix";
}) {
  const t = tone(q?.change);
  const stale = q ? isStaleQuote(q) : false;

  const value = q?.ltp != null ? fmtNum(q.ltp) : "—";
  const staleLabel = stale ? ", stale data" : "";
  const change = (
    <span className={`idx-change ${t}`}>
      <span className="idx-pts">
        {q?.change != null ? `${q.change > 0 ? "+" : ""}${fmtNum(q.change)}` : "—"}
      </span>
      <span className="pct">{q?.change_percent != null ? fmtPct(q.change_percent) : "—"}</span>
    </span>
  );

  if (variant === "vix") {
    return (
      <Link
        to="/charts"
        className={`idx-card idx-card--vix${stale ? " is-stale" : ""}`}
        aria-label={`${idx.label}${q?.ltp != null ? ` ${fmtNum(q.ltp)}` : ""}${staleLabel}`}
      >
        <span className="idx-vix-lead">
          <span className="idx-name">{idx.label}</span>
          <span className="idx-value">{value}</span>
          {stale && <span className="stale-tag">stale</span>}
        </span>
        {change}
      </Link>
    );
  }

  return (
    <Link
      to="/charts"
      className={`idx-card${stale ? " is-stale" : ""}`}
      aria-label={`${idx.label}${q?.ltp != null ? ` ${fmtNum(q.ltp)}` : ""}${staleLabel}`}
    >
      <span className="idx-name">{idx.label}</span>
      <span className="idx-value">{value}</span>
      {stale && <span className="stale-tag">stale</span>}
      {change}
    </Link>
  );
}
