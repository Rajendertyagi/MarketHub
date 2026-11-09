import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Button, Field, Select, Tabs } from "@/components/ui";
import { InstrumentsView } from "@/features/instruments/InstrumentsView";
import { SubscriptionsView } from "@/features/subscriptions/SubscriptionsView";
import { fmtInt } from "@/utils/format";
import { useFnoCoverage, useFnoMutations } from "./useMarketData";

type Tab = "catalog" | "coverage";

// Unified Market Data page: CATALOG (what exists) -> LIVE COVERAGE (what
// streams). Two clearly separated sections over one coherent destination.
export function MarketDataView() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) === "coverage" ? "coverage" : "catalog";
  const setTab = (t: Tab) =>
    setParams(
      (prev) => {
        const n = new URLSearchParams(prev);
        n.set("tab", t);
        return n;
      },
      { replace: true },
    );
  const [expiry, setExpiry] = useState<string>("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const cov = useFnoCoverage(expiry || undefined);
  const m = useFnoMutations();
  const data = cov.data;

  const onApply = async () => {
    setMsg(null);
    try {
      const res = (await m.apply(expiry || undefined)) as {
        routed?: number;
        by_provider?: Record<string, number>;
      };
      const alloc = Object.entries(res.by_provider ?? {})
        .map(([p, n]) => `${p}:${n}`)
        .join(" ");
      setMsg({
        ok: true,
        text: `Applied — ${res.routed ?? "?"} routed ${alloc}`.trim(),
      });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : "apply failed" });
    }
  };

  const onRelease = async () => {
    setMsg(null);
    try {
      await m.release();
      setMsg({ ok: true, text: "Released F&O coverage (other owners untouched)" });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : "release failed" });
    }
  };

  const states = data?.states ?? {};
  const providers = data?.providers ?? [];

  const coverageSection = (
    <>
      <div className="card">
        <div className="panel-header">
          <h2>F&amp;O Live Coverage</h2>
          {data && (
            <span className="muted">
              {data.expiry_resolved ?? "—"} · {fmtInt(data.routed)} routed / {fmtInt(data.desired)}{" "}
              desired
            </span>
          )}
        </div>
        <div className="control-row toolbar">
          <Field label="Expiry">
            <Select value={expiry} onChange={(e) => setExpiry(e.target.value)}>
              <option value="">Default ({data?.expiry_resolved ?? "nearest"})</option>
              {(data?.expiries ?? []).map((e) => (
                <option key={e} value={e}>
                  {e}
                </option>
              ))}
            </Select>
          </Field>
          <Button variant="primary" onClick={onApply}>
            Apply coverage
          </Button>
          <Button variant="default" onClick={onRelease}>
            Release
          </Button>
          {msg && <span className={msg.ok ? "hint ok" : "hint err"}>{msg.text}</span>}
        </div>

        {data && (
          <div className="stat-grid">
            <div className="stat-card">
              <span className="stat-label">Desired</span>
              <span className="stat-value">{fmtInt(data.desired)}</span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Routed</span>
              <span className="stat-value">{fmtInt(data.routed)}</span>
            </div>
            {Object.entries(states).map(([state, n]) => (
              <div className="stat-card" key={state}>
                <span className="stat-label">{state}</span>
                <span className="stat-value">{fmtInt(n)}</span>
              </div>
            ))}
          </div>
        )}

        {providers.length > 0 && (
          <table className="table table-compact">
            <thead>
              <tr>
                <th>Provider</th>
                <th>Status</th>
                <th>Available</th>
                <th className="num">Budget</th>
                <th className="num">Used</th>
                <th className="num">Free</th>
              </tr>
            </thead>
            <tbody>
              {providers.map((p) => {
                const cap = data?.capacity?.[p.provider];
                return (
                  <tr key={p.provider}>
                    <td>{p.label}</td>
                    <td className="muted">{p.status ?? "—"}</td>
                    <td>{p.available ? "yes" : "no"}</td>
                    <td className="num">{cap?.budget ?? "—"}</td>
                    <td className="num">{cap?.used ?? 0}</td>
                    <td className="num">{cap?.available ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        {data && data.uncovered.length > 0 && (
          <p className="hint err">
            {data.uncovered.length} contract(s) uncovered / capacity-limited.
          </p>
        )}
        {data && data.errors.length > 0 && <p className="hint err">{data.errors.join("; ")}</p>}
        {cov.isError && <p className="hint err">Coverage unavailable.</p>}
      </div>

      <SubscriptionsView embedded />
    </>
  );

  return (
    <div className="panel market-data-view">
      <div className="page-header">
        <h1 className="page-title">Market Data</h1>
        <span className="muted">Catalog (what exists) &rarr; Live Coverage (what streams)</span>
      </div>

      <Tabs<Tab>
        tabs={[
          { value: "catalog", label: "Catalog" },
          { value: "coverage", label: "Live Coverage" },
        ]}
        active={tab}
        onChange={setTab}
      />

      <div style={{ marginTop: 12 }}>
        {tab === "catalog" ? <InstrumentsView embedded /> : coverageSection}
      </div>
    </div>
  );
}
