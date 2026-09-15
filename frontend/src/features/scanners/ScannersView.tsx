import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, type ScannerDef, type ScanResult, type ScanRow } from "@/types";
import { listScanners, resolveInstrument, runScanner } from "@/api/market";
import {
  COLUMNS,
  renderCell,
  rowNavigation,
  type ScannerKind,
} from "./columns";
import { AsyncStateView, Button, Field, Input, Select } from "@/components/ui";

const UNIVERSES = [
  "FNO",
  "NIFTY50",
  "BANKNIFTY",
  "FINNIFTY",
  "MIDCPNIFTY",
];

function kindOf(def: ScannerDef | undefined): ScannerKind {
  if (def?.contract_kind === "future") return "future";
  if (def?.contract_kind === "option") return "option";
  return "equity";
}

// Compact summary line shown on the card face once a scan resolves:
// the head of the ranking plus how many names matched.
function summarize(result: ScanResult | undefined) {
  if (!result) return null;
  const top = result.rows[0];
  return {
    matched: result.matched,
    eligible: result.eligible,
    quoted: result.quoted,
    top,
  };
}

function ScannerCard({
  def,
  universe,
  limit,
  expiry,
  atmRange,
  optionType,
  defaultOpen,
}: {
  def: ScannerDef;
  universe: string;
  limit: number;
  expiry: string;
  atmRange: number;
  optionType: "CE" | "PE" | "BOTH";
  defaultOpen?: boolean;
}) {
  const navigate = useNavigate();
  const kind = kindOf(def);
  const isOption = kind === "option";

  const [open, setOpen] = useState(!!defaultOpen);

  const query = useQuery({
    queryKey: [
      "scanner-run",
      def.name,
      universe,
      limit,
      isOption ? expiry : "",
      isOption ? atmRange : "",
      isOption ? optionType : "",
    ],
    queryFn: ({ signal }) =>
      runScanner(
        def.name,
        {
          universe,
          limit,
          expiry: isOption ? expiry : undefined,
          atm_range: isOption ? atmRange : undefined,
          option_type: isOption ? optionType : undefined,
        },
        signal,
      ),
  });

  const onRowClick = async (row: ScanRow) => {
    const nav = rowNavigation(row, kind);
    if (!nav) return;
    const inst = await resolveInstrument(nav.tradingsymbol, nav.type);
    if (!inst) return;
    navigate(
      `/charts?key=${encodeURIComponent(inst.instrument_token)}` +
        `&sym=${encodeURIComponent(inst.tradingsymbol)}` +
        `&ex=${encodeURIComponent(inst.exchange)}` +
        `&type=${encodeURIComponent(inst.instrument_type)}`,
    );
  };

  const columns = COLUMNS[kind];
  const rows = query.data?.rows ?? [];
  const summary = summarize(query.data);

  const toggle = () => setOpen((o) => !o);
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggle();
    }
  };

  let detail: React.ReactNode;
  if (query.isLoading && !query.data) {
    detail = (
      <AsyncStateView status="loading" loadingLabel="Scanning…" />
    );
  } else if (query.isError) {
    detail = (
      <AsyncStateView
        status="error"
        error={query.error as ApiError}
        onRetry={() => query.refetch()}
      />
    );
  } else if (!rows.length) {
    const r = query.data;
    detail = (
      <AsyncStateView
        status="empty"
        emptyLabel={`No rows (eligible ${r?.eligible ?? 0}, quoted ${r?.quoted ?? 0}).`}
      />
    );
  } else {
    detail = (
      <div className="scanner-card__table">
        <table className="table table-compact">
          <thead>
            <tr>
              <th>#</th>
              {columns.map((c) => (
                <th key={c.key} className={c.numeric ? "num" : ""}>
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr
                key={`${row.symbol}-${row.contract ?? ""}-${i}`}
                onClick={(e) => {
                  e.stopPropagation();
                  onRowClick(row);
                }}
              >
                <td className="muted">{i + 1}</td>
                {columns.map((c) => (
                  <td key={c.key} className={c.numeric ? "num" : ""}>
                    {renderCell(c.key, row)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <article
      className={`scanner-card${open ? " is-open" : ""}`}
      role="button"
      tabIndex={0}
      aria-expanded={open}
      onClick={toggle}
      onKeyDown={onKey}
    >
      <header className="scanner-card__head">
        <div className="scanner-card__titles">
          <h3 className="scanner-card__title">{def.title}</h3>
          <span className="scanner-card__chevron" aria-hidden="true">
            ⌄
          </span>
        </div>
        <span className="scanner-card__hint" aria-hidden="true">
          {open ? "Click to collapse" : "Click to expand"}
        </span>
        <div className="scanner-card__meta">
          <span className="scanner-tag">{def.instrument_class}</span>
          {def.contract_kind && (
            <span className="scanner-tag">{def.contract_kind}</span>
          )}
          <span className="scanner-tag">{def.metric}</span>
        </div>
        <p className="scanner-card__desc">{def.description}</p>
      </header>

      {summary && (
        <div className="scanner-card__summary">
          <span className="scanner-card__count">
            {summary.matched} matches
          </span>
          {summary.top && (
            <span className="scanner-card__top">
              Top&nbsp;
              <strong>{summary.top.symbol}</strong>
              {summary.top.change_percent != null ? (
                <span
                  className={
                    summary.top.change_percent > 0
                      ? "pos"
                      : summary.top.change_percent < 0
                        ? "neg"
                        : ""
                  }
                >
                  {summary.top.change_percent > 0 ? " +" : " "}
                  {summary.top.change_percent.toFixed(2)}%
                </span>
              ) : summary.top.ltp != null ? (
                <span className="muted">{summary.top.ltp}</span>
              ) : null}
            </span>
          )}
          <span className="muted scanner-card__coverage">
            {summary.eligible} eligible · {summary.quoted} quoted
          </span>
        </div>
      )}

      <div className="scanner-card__detail">
        <div className="scanner-card__detail-inner">{detail}</div>
      </div>
    </article>
  );
}

export function ScannersView() {
  const queryClient = useQueryClient();

  const scannersQuery = useQuery({
    queryKey: ["scanners"],
    queryFn: ({ signal }) => listScanners(signal),
  });

  const defs = scannersQuery.data ?? [];
  const [universe, setUniverse] = useState<string>("FNO");
  const [limit, setLimit] = useState<number>(25);
  const [expiry, setExpiry] = useState<string>("");
  const [atmRange, setAtmRange] = useState<number>(5);
  const [optionType, setOptionType] = useState<"CE" | "PE" | "BOTH">("BOTH");

  const hasOption = useMemo(
    () => defs.some((d) => kindOf(d) === "option"),
    [defs],
  );

  const refreshAll = () =>
    queryClient.invalidateQueries({ queryKey: ["scanner-run"] });

  let grid: React.ReactNode;
  if (scannersQuery.isLoading) {
    grid = <AsyncStateView status="loading" loadingLabel="Loading scanners…" />;
  } else if (scannersQuery.isError) {
    grid = (
      <AsyncStateView status="error" error={scannersQuery.error as ApiError} />
    );
  } else if (!defs.length) {
    grid = <AsyncStateView status="empty" emptyLabel="No scanners available." />;
  } else {
    grid = (
      <div className="scanner-grid">
        {defs.map((def, i) => (
          <ScannerCard
            key={def.name}
            def={def}
            universe={universe}
            limit={limit}
            expiry={expiry}
            atmRange={atmRange}
            optionType={optionType}
            defaultOpen={i === 0}
          />
        ))}
      </div>
    );
  }

  return (
    <div className="panel">
      <div className="page-header">
        <h1 className="page-title">Scanners</h1>
        <span className="muted">
          {defs.length} scanners · click a card to expand results
        </span>
      </div>

      <div className="toolbar">
        <Field label="Universe">
          <Select value={universe} onChange={(e) => setUniverse(e.target.value)}>
            {UNIVERSES.map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Limit">
          <Select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
            {[10, 25, 50, 100].map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </Select>
        </Field>

        {hasOption && (
          <>
            <Field label="Expiry">
              <Input
                placeholder="auto"
                value={expiry}
                onChange={(e) => setExpiry(e.target.value)}
                style={{ width: 120 }}
              />
            </Field>
            <Field label="ATM Range">
              <Select
                value={atmRange}
                onChange={(e) => setAtmRange(Number(e.target.value))}
              >
                {[1, 3, 5, 10, 20].map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="CE/PE">
              <Select
                value={optionType}
                onChange={(e) =>
                  setOptionType(e.target.value as "CE" | "PE" | "BOTH")
                }
              >
                <option value="BOTH">Both</option>
                <option value="CE">CE</option>
                <option value="PE">PE</option>
              </Select>
            </Field>
          </>
        )}

        <Button variant="primary" onClick={refreshAll}>
          Refresh
        </Button>
      </div>

      {grid}
    </div>
  );
}
