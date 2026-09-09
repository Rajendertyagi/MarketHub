import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ApiError, type Instrument, type InstrumentType } from "@/types";
import {
  getHistory,
  resolveInstrument,
  searchInstruments,
} from "@/api/market";
import { normalizeCandles } from "./candleUtils";
import { buildChartOption } from "./chartOption";
import { EChart } from "@/components/EChart";
import { AsyncStateView, Button, Field, Input, Select } from "@/components/ui";

const INTERVALS = [1, 5, 15, 30, 60];
const RANGES = [7, 15, 30, 60, 90, 180, 365];

function rangeDates(days: number): { from: string; to: string } {
  const to = new Date();
  const from = new Date(Date.now() - days * 86_400_000);
  return {
    from: from.toISOString().slice(0, 10),
    to: to.toISOString().slice(0, 10),
  };
}

export function ChartsView() {
  const [params, setParams] = useSearchParams();

  const [instrument, setInstrument] = useState<Instrument | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Instrument[]>([]);
  const [interval, setInterval] = useState<number>(1);
  const [range, setRange] = useState<number>(30);
  const [unit, setUnit] = useState<string>("days");
  const [provider, setProvider] = useState<string>("");
  const [message, setMessage] = useState<{ text: string; kind: "ok" | "err" }>({
    text: "",
    kind: "ok",
  });

  // Hydrate exact instrument identity from the URL (deep-link / scanner nav).
  useEffect(() => {
    const key = params.get("key");
    const sym = params.get("sym");
    const ex = params.get("ex") ?? "";
    const type = (params.get("type") as InstrumentType) ?? "EQUITY";
    if (key && sym) {
      setInstrument({
        instrument_token: key,
        exchange: ex,
        tradingsymbol: sym,
        instrument_type: type,
      });
    } else if (sym && type) {
      resolveInstrument(sym, type).then((r) => {
        if (r) setInstrument(r);
      });
    }
    // Run once on mount with the initial URL.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Debounced instrument search.
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setResults([]);
      return;
    }
    const ctrl = new AbortController();
    const t = setTimeout(async () => {
      try {
        const found = await searchInstruments({ q, limit: 15 }, ctrl.signal);
        setResults(found);
      } catch {
        setResults([]);
      }
    }, 300);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [query]);

  const { from, to } = useMemo(() => rangeDates(range), [range]);

  const history = useQuery({
    queryKey: [
      "history",
      instrument?.instrument_token,
      interval,
      unit,
      range,
      provider,
    ],
    enabled: !!instrument,
    queryFn: ({ signal }) =>
      getHistory(
        {
          instrument_key: instrument!.instrument_token,
          provider,
          unit,
          interval,
          from,
          to,
        },
        signal,
      ),
  });

  const candles = useMemo(
    () => (history.data ? normalizeCandles(history.data.candles) : []),
    [history.data],
  );

  const option = useMemo(() => buildChartOption(candles), [candles]);

  const selectInstrument = (inst: Instrument) => {
    setInstrument(inst);
    setResults([]);
    setQuery(inst.tradingsymbol);
    setParams({
      key: inst.instrument_token,
      sym: inst.tradingsymbol,
      ex: inst.exchange,
      type: inst.instrument_type,
    });
  };

  const load = () => {
    if (!instrument) {
      setMessage({ text: "Search and select an instrument first.", kind: "err" });
      return;
    }
    setMessage({ text: "", kind: "ok" });
    history.refetch();
  };

  let body: React.ReactNode;
  if (!instrument) {
    body = (
      <AsyncStateView
        status="empty"
        emptyLabel="Search an instrument above to load its historical chart."
      />
    );
  } else if (history.isLoading) {
    body = <AsyncStateView status="loading" loadingLabel="Loading history…" />;
  } else if (history.isError) {
    const err = history.error as ApiError;
    const text = err.isUnsupported
      ? `Provider "${provider || "default"}" does not support history for this instrument.`
      : err.message;
    body = <AsyncStateView status="error" error={err} onRetry={load} />;
    if (err.isUnsupported) {
      body = (
        <div className="state">
          <h3>Unsupported provider</h3>
          <p className="hint err">{text}</p>
        </div>
      );
    }
  } else if (!candles.length) {
    body = (
      <AsyncStateView
        status="empty"
        emptyLabel="No history data returned for this range."
      />
    );
  } else {
    const first = candles[0]!.timestamp.slice(0, 10);
    const last = candles[candles.length - 1]!.timestamp.slice(0, 10);
    body = (
      <>
        <div className="hint ok">
          {candles.length} candles · {first} → {last}
        </div>
        <EChart option={option} />
      </>
    );
  }

  return (
    <div className="panel">
      <div className="page-header">
        <h1 className="page-title">Historical Charts</h1>
        {instrument && (
          <span className="muted">
            {instrument.tradingsymbol} · {instrument.exchange} ·{" "}
            {instrument.instrument_type}
          </span>
        )}
      </div>

      <div className="chart-toolbar">
        <Field label="Instrument">
          <Input
            placeholder="Search symbol…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{ width: 240 }}
          />
        </Field>
        <Field label="Interval">
          <Select
            value={interval}
            onChange={(e) => setInterval(Number(e.target.value))}
          >
            {INTERVALS.map((i) => (
              <option key={i} value={i}>
                {i}m
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Range">
          <Select
            value={range}
            onChange={(e) => setRange(Number(e.target.value))}
          >
            {RANGES.map((r) => (
              <option key={r} value={r}>
                {r}d
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Unit">
          <Select value={unit} onChange={(e) => setUnit(e.target.value)}>
            <option value="days">days</option>
            <option value="minutes">minutes</option>
          </Select>
        </Field>
        <Field label="Provider">
          <Input
            placeholder="default"
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            style={{ width: 120 }}
          />
        </Field>
        <Button variant="primary" onClick={load}>
          Load
        </Button>
      </div>

      {results.length > 0 && (
        <div className="card" style={{ padding: 0 }}>
          <table className="table">
            <tbody>
              {results.map((r) => (
                <tr
                  key={r.instrument_token}
                  onClick={() => selectInstrument(r)}
                >
                  <td>{r.tradingsymbol}</td>
                  <td className="muted">{r.exchange}</td>
                  <td className="muted">{r.instrument_type}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {message.text && (
        <div className={`hint ${message.kind}`}>{message.text}</div>
      )}

      {body}
    </div>
  );
}
