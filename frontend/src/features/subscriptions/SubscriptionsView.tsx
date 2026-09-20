import { useState } from "react";
import { AsyncStateView, Button, Field, Input, Select, Tabs } from "@/components/ui";
import type { ApiError, DerivativeRule, SubscriptionIndex, SubscriptionStock } from "@/types";
import { useSubscriptionMutations, useSubscriptions } from "./useSubscriptions";

type Tab = "indices" | "stocks" | "rules";

function IndexRow({
  index,
  onToggle,
}: {
  index: SubscriptionIndex;
  onToggle: (enabled: boolean) => void;
}) {
  return (
    <tr>
      <td>
        <label className="switch">
          <input
            type="checkbox"
            checked={index.enabled}
            onChange={(e) => onToggle(e.target.checked)}
          />
          <span />
        </label>
      </td>
      <td>{index.label}</td>
      <td className="muted">{index.key}</td>
      <td className="muted">{index.fyers_symbol}</td>
    </tr>
  );
}

function StockRow({
  stock,
  onToggle,
  onRemove,
}: {
  stock: SubscriptionStock;
  onToggle: (enabled: boolean) => void;
  onRemove: () => void;
}) {
  return (
    <tr>
      <td>
        <label className="switch">
          <input
            type="checkbox"
            checked={stock.enabled}
            onChange={(e) => onToggle(e.target.checked)}
          />
          <span />
        </label>
      </td>
      <td>{stock.label}</td>
      <td className="muted">{stock.key}</td>
      <td>
        <Button variant="default" className="btn-compact sub-remove" onClick={onRemove}>
          Remove
        </Button>
      </td>
    </tr>
  );
}

function RuleEditor({
  rule,
  onSave,
  onDelete,
}: {
  rule: DerivativeRule;
  onSave: (rule: DerivativeRule) => void;
  onDelete: () => void;
}) {
  const [draft, setDraft] = useState<DerivativeRule>(rule);
  const set = <K extends keyof DerivativeRule>(k: K, v: DerivativeRule[K]) =>
    setDraft((d) => ({ ...d, [k]: v }));

  return (
    <div className="card rule-editor">
      <div className="rule-editor-head">
        <strong>{rule.underlying}</strong>
      </div>
      <div className="control-row">
        <Field label="Futures">
          <Select
            value={draft.futures_enabled ? "on" : "off"}
            onChange={(e) => set("futures_enabled", e.target.value === "on")}
          >
            <option value="off">Off</option>
            <option value="on">On</option>
          </Select>
        </Field>
        <Field label="Futures expiry">
          <Select
            value={String(draft.futures_count)}
            onChange={(e) => set("futures_count", Number(e.target.value))}
          >
            <option value="1">Current</option>
            <option value="2">Current + Next</option>
          </Select>
        </Field>
        <Field label="Options">
          <Select
            value={draft.options_enabled ? "on" : "off"}
            onChange={(e) => set("options_enabled", e.target.value === "on")}
          >
            <option value="off">Off</option>
            <option value="on">On</option>
          </Select>
        </Field>
        <Field label="Options expiry">
          <Select
            value={String(draft.options_count)}
            onChange={(e) => set("options_count", Number(e.target.value))}
          >
            <option value="1">Nearest</option>
            <option value="2">Nearest + Next</option>
          </Select>
        </Field>
      </div>
      <div className="control-row">
        <Field label="ATM strikes below">
          <Input
            type="number"
            min={0}
            max={50}
            value={draft.strikes_below}
            onChange={(e) => set("strikes_below", Number(e.target.value))}
          />
        </Field>
        <Field label="ATM strikes above">
          <Input
            type="number"
            min={0}
            max={50}
            value={draft.strikes_above}
            onChange={(e) => set("strikes_above", Number(e.target.value))}
          />
        </Field>
        <Field label="Calls (CE)">
          <Select
            value={draft.calls_enabled ? "on" : "off"}
            onChange={(e) => set("calls_enabled", e.target.value === "on")}
          >
            <option value="off">Off</option>
            <option value="on">On</option>
          </Select>
        </Field>
        <Field label="Puts (PE)">
          <Select
            value={draft.puts_enabled ? "on" : "off"}
            onChange={(e) => set("puts_enabled", e.target.value === "on")}
          >
            <option value="off">Off</option>
            <option value="on">On</option>
          </Select>
        </Field>
      </div>
      <div className="control-row">
        <Button variant="primary" onClick={() => onSave(draft)}>
          Save rule
        </Button>
        <Button variant="default" onClick={onDelete}>
          Delete
        </Button>
      </div>
    </div>
  );
}

export function SubscriptionsView() {
  const [tab, setTab] = useState<Tab>("indices");
  const [newKey, setNewKey] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [newRuleUnderlying, setNewRuleUnderlying] = useState("");
  const [applyMsg, setApplyMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const query = useSubscriptions();
  const m = useSubscriptionMutations();

  const data = query.data;

  let body: React.ReactNode;
  if (query.isLoading) {
    body = <AsyncStateView status="loading" loadingLabel="Loading subscriptions…" />;
  } else if (query.isError) {
    body = (
      <AsyncStateView
        status="error"
        error={query.error as ApiError}
        onRetry={() => query.refetch()}
      />
    );
  } else if (!data) {
    body = <AsyncStateView status="empty" emptyLabel="No subscription data." />;
  } else if (tab === "indices") {
    body = (
      <div className="card" style={{ overflow: "auto" }}>
        <table className="table table-compact">
          <thead>
            <tr>
              <th>Enabled</th>
              <th>Index</th>
              <th>Key</th>
              <th>Fyers symbol</th>
            </tr>
          </thead>
          <tbody>
            {data.indices.map((idx) => (
              <IndexRow
                key={idx.label}
                index={idx}
                onToggle={(enabled) => m.toggleIndex(idx.label, enabled)}
              />
            ))}
          </tbody>
        </table>
      </div>
    );
  } else if (tab === "stocks") {
    body = (
      <>
        <div className="card">
          <div className="control-row">
            <Field label="Instrument key">
              <Input
                value={newKey}
                placeholder="NSE:RELIANCE"
                onChange={(e) => setNewKey(e.target.value)}
              />
            </Field>
            <Field label="Label">
              <Input
                value={newLabel}
                placeholder="RELIANCE"
                onChange={(e) => setNewLabel(e.target.value)}
              />
            </Field>
            <Button
              variant="primary"
              disabled={!newKey.trim()}
              onClick={async () => {
                await m.addStock(newKey.trim(), newLabel.trim() || newKey.trim());
                setNewKey("");
                setNewLabel("");
              }}
            >
              Add stock
            </Button>
          </div>
        </div>
        <div className="card" style={{ overflow: "auto" }}>
          {data.stocks.length === 0 ? (
            <span className="muted">No stock subscriptions.</span>
          ) : (
            <table className="table table-compact">
              <thead>
                <tr>
                  <th>Enabled</th>
                  <th>Label</th>
                  <th>Key</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.stocks.map((s) => (
                  <StockRow
                    key={s.key}
                    stock={s}
                    onToggle={(enabled) => m.toggleStock(s.key, enabled)}
                    onRemove={() => m.removeStock(s.key)}
                  />
                ))}
              </tbody>
            </table>
          )}
        </div>
      </>
    );
  } else {
    body = (
      <>
        <div className="card">
          <div className="control-row">
            <Field label="New rule underlying">
              <Input
                value={newRuleUnderlying}
                placeholder="RELIANCE"
                onChange={(e) => setNewRuleUnderlying(e.target.value)}
              />
            </Field>
            <Button
              variant="primary"
              disabled={!newRuleUnderlying.trim()}
              onClick={() =>
                m.saveRule({
                  underlying: newRuleUnderlying.trim().toUpperCase(),
                  futures_enabled: false,
                  futures_count: 1,
                  options_enabled: true,
                  options_count: 1,
                  strikes_below: 0,
                  strikes_above: 0,
                  calls_enabled: true,
                  puts_enabled: true,
                })
              }
            >
              Add rule
            </Button>
          </div>
        </div>
        {data.derivatives.length === 0 ? (
          <span className="muted">No derivative rules.</span>
        ) : (
          data.derivatives.map((rule) => (
            <RuleEditor
              key={rule.underlying}
              rule={rule}
              onSave={(draft) => m.saveRule(draft)}
              onDelete={() => m.deleteRule(rule.underlying)}
            />
          ))
        )}
      </>
    );
  }

  return (
    <div className="panel subscriptions-view">
      <div className="page-header">
        <h1 className="page-title">Subscriptions</h1>
        <span className="muted">
          DB-backed preferences · backend resolves contracts &amp; reconciles feeds
        </span>
      </div>

      <Tabs<Tab>
        tabs={[
          { value: "indices", label: "Indices" },
          { value: "stocks", label: "Stocks" },
          { value: "rules", label: "Derivative Rules" },
        ]}
        active={tab}
        onChange={setTab}
      />

      <div className="control-row" style={{ marginTop: 12 }}>
        <Button
          variant="primary"
          onClick={async () => {
            setApplyMsg(null);
            try {
              const res = (await m.apply()) as { resolved_count?: number };
              setApplyMsg({
                ok: true,
                text: `Applied — ${res.resolved_count ?? "?"} contracts resolved`,
              });
            } catch (e) {
              setApplyMsg({
                ok: false,
                text: e instanceof Error ? e.message : "apply failed",
              });
            }
          }}
        >
          Save &amp; Apply (no restart)
        </Button>
        {applyMsg && <span className={applyMsg.ok ? "hint ok" : "hint err"}>{applyMsg.text}</span>}
      </div>

      {body}
    </div>
  );
}
