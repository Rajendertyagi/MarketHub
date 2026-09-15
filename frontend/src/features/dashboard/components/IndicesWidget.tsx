import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getSubscriptions } from "@/api/market";
import type { ApiError } from "@/types";
import type { MarketQuote } from "../types";
import { AsyncStateView } from "@/components/ui";
import { fmtNum, fmtPct, tone } from "@/utils/format";

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

  const byKey = useMemo(
    () => new Map(quotes.map((q) => [q.instrument_token, q])),
    [quotes],
  );

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
      {vix && (
        <IndexCard idx={vix} q={byKey.get(vix.key)} variant="vix" />
      )}
      <div className="idx-grid">
        {others.map((idx) => (
          <IndexCard key={idx.key} idx={idx} q={byKey.get(idx.key)} />
        ))}
      </div>
    </div>
  );
}

// A single hero stat card: the index name, its live value, and the change /
// % change colored by direction. The whole surface is the affordance into
// Charts — it never nests buttons, so keyboard + pointer both navigate.
function IndexCard({
  idx,
  q,
  variant,
}: {
  idx: IndexDef;
  q: MarketQuote | undefined;
  variant?: "vix";
}) {
  const navigate = useNavigate();
  const t = tone(q?.change);
  const go = () => navigate("/charts");

  const value = q?.ltp != null ? fmtNum(q.ltp) : "—";
  const change = (
    <span className={`idx-change ${t}`}>
      <span className="idx-pts">
        {q?.change != null ? `${q.change > 0 ? "+" : ""}${fmtNum(q.change)}` : "—"}
      </span>
      <span className="pct">
        {q?.change_percent != null ? fmtPct(q.change_percent) : "—"}
      </span>
    </span>
  );

  if (variant === "vix") {
    return (
      <section
        className="idx-card idx-card--vix"
        role="link"
        tabIndex={0}
        aria-label={`${idx.label}${q?.ltp != null ? ` ${fmtNum(q.ltp)}` : ""}`}
        onClick={go}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            go();
          }
        }}
      >
        <span className="idx-vix-lead">
          <span className="idx-name">{idx.label}</span>
          <span className="idx-value">{value}</span>
        </span>
        {change}
      </section>
    );
  }

  return (
    <section
      className="idx-card"
      role="link"
      tabIndex={0}
      aria-label={`${idx.label}${q?.ltp != null ? ` ${fmtNum(q.ltp)}` : ""}`}
      onClick={go}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          go();
        }
      }}
    >
      <span className="idx-name">{idx.label}</span>
      <span className="idx-value">{value}</span>
      {change}
    </section>
  );
}
