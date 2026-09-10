import { useState } from "react";
import { Button, Field, Input, Select } from "@/components/ui";
import { ALERT_FIELDS, ALERT_OPERATORS, DEFAULT_ALERT_EXCHANGE } from "../constants";
import { ALERT_FIELD_LABELS, ALERT_OPERATOR_LABELS } from "../constants";
import { useAlertMutations } from "../useAlerts";
import type { AlertField, AlertOperator, CreateAlertInput } from "../types";

const EMPTY: CreateAlertInput = {
  exchange: DEFAULT_ALERT_EXCHANGE,
  instrument_token: "",
  tradingsymbol: "",
  field: "ltp",
  operator: "gt",
  threshold: 0,
};

// Create-alert form. Posts the exact backend contract; never computes anything
// client-side. Symbol defaults to the instrument token when left blank.
export function AlertForm() {
  const m = useAlertMutations();
  const [form, setForm] = useState<CreateAlertInput>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const update = <K extends keyof CreateAlertInput>(
    key: K,
    value: CreateAlertInput[K],
  ) => setForm((f) => ({ ...f, [key]: value }));

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!form.instrument_token.trim()) {
      setError("Instrument token is required.");
      return;
    }
    const payload: CreateAlertInput = {
      ...form,
      instrument_token: form.instrument_token.trim(),
      tradingsymbol: form.tradingsymbol.trim() || form.instrument_token.trim(),
      threshold: Number(form.threshold),
    };
    if (Number.isNaN(payload.threshold)) {
      setError("Threshold must be a number.");
      return;
    }
    setBusy(true);
    try {
      await m.create(payload);
      setForm(EMPTY);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create alert.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card alert-form" onSubmit={onSubmit}>
      <div className="card-header">
        <h2>New alert</h2>
      </div>
      <div className="control-row">
        <Field label="Instrument token">
          <Input
            placeholder="e.g. NSE_EQ|RELIANCE"
            value={form.instrument_token}
            onChange={(e) => update("instrument_token", e.target.value)}
          />
        </Field>
        <Field label="Symbol (optional)">
          <Input
            placeholder="defaults to token"
            value={form.tradingsymbol}
            onChange={(e) => update("tradingsymbol", e.target.value)}
          />
        </Field>
        <Field label="Field">
          <Select
            value={form.field}
            onChange={(e) => update("field", e.target.value as AlertField)}
          >
            {ALERT_FIELDS.map((f) => (
              <option key={f} value={f}>
                {ALERT_FIELD_LABELS[f]}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Operator">
          <Select
            value={form.operator}
            onChange={(e) => update("operator", e.target.value as AlertOperator)}
          >
            {ALERT_OPERATORS.map((o) => (
              <option key={o} value={o}>
                {ALERT_OPERATOR_LABELS[o]}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Threshold">
          <Input
            type="number"
            step="any"
            value={form.threshold}
            onChange={(e) => update("threshold", Number(e.target.value))}
          />
        </Field>
        <Button type="submit" variant="primary" disabled={busy}>
          {busy ? "Adding…" : "Add alert"}
        </Button>
      </div>
      {error ? <p className="hint err">{error}</p> : null}
    </form>
  );
}
