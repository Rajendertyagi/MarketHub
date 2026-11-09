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
  /** Selected quote per canonical id (S7 coordinator contract). */
  byCanonical?: Map<string, MarketQuote>;
}

interface IndexDef {
  label: string;
  key: string;
  canonical_id?: string | null;
  /** The other provider's native identifier for this index. */
  fyers_symbol?: string | null;
}

const VIX_LABEL = "INDIA VIX";

// Live major indices. The subscription registry owns the canonical index list
// (label + canonical_id); the quote join uses the canonical identity so the
// displayed quote is the backend-selected one for that instrument — never a
// provider-specific key. The native key remains a FALLBACK for payloads that
// predate the additive canonical_id field.
export function IndicesWidget({ quotes, byCanonical }: Props) {
  const subs = useQuery({
    queryKey: ["dash-subscriptions"],
    queryFn: ({ signal }) => getSubscriptions(signal),
  });

  const { byCanonicalId, byNativeKey } = useMemo(() => {
    const canonical = new Map<string, MarketQuote>();
    const native = new Map<string, MarketQuote>();
    for (const q of quotes) {
      const cid = (q as { canonical_id?: string | null }).canonical_id;
      if (cid && !canonical.has(cid)) canonical.set(cid, q);
      const nk = q.instrument_token;
      if (nk && !native.has(nk)) native.set(nk, q);
    }
    return { byCanonicalId: canonical, byNativeKey: native };
  }, [quotes]);

  const quoteFor = (idx: IndexDef): MarketQuote | undefined => {
    if (idx.canonical_id) {
      const selected = byCanonical?.get(idx.canonical_id) ?? byCanonicalId.get(idx.canonical_id);
      if (selected) return selected;
    }
    // Fallback: the quote store keys a quote by the NATIVE token of the
    // provider that served it, and either provider may be the one that
    // answered for this index. Try both identifiers.
    return (
      byNativeKey.get(idx.key) ??
      (idx.fyers_symbol ? byNativeKey.get(idx.fyers_symbol) : undefined)
    );
  };

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
      {vix && <IndexCard idx={vix} q={quoteFor(vix)} variant="vix" />}
      <div className="idx-grid">
        {others.map((idx) => (
          <IndexCard key={idx.canonical_id ?? idx.key} idx={idx} q={quoteFor(idx)} />
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
        to="/chart"
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
      to="/chart"
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
