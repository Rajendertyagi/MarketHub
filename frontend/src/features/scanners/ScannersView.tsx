import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ApiError, type ScannerDef, type ScanRow } from "@/types";
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

export function ScannersView() {
  const navigate = useNavigate();

  const scannersQuery = useQuery({
    queryKey: ["scanners"],
    queryFn: ({ signal }) => listScanners(signal),
  });

  const defs = scannersQuery.data ?? [];
  const [name, setName] = useState<string>("");
  const [universe, setUniverse] = useState<string>("FNO");
  const [limit, setLimit] = useState<number>(25);
  const [expiry, setExpiry] = useState<string>("");
  const [atmRange, setAtmRange] = useState<number>(5);
  const [optionType, setOptionType] = useState<"CE" | "PE" | "BOTH">("BOTH");
  const [navError, setNavError] = useState<string>("");

  // Default to the first scanner once the list loads.
  useEffect(() => {
    if (!name && defs.length) setName(defs[0]!.name);
  }, [defs, name]);

  const selectedDef = useMemo(
    () => defs.find((d) => d.name === name),
    [defs, name],
  );
  const kind = kindOf(selectedDef);
  const isOption = kind === "option";

  const runQuery = useQuery({
    queryKey: [
      "scanner-run",
      name,
      universe,
      limit,
      expiry,
      atmRange,
      optionType,
    ],
    enabled: !!name,
    queryFn: ({ signal }) =>
      runScanner(
        name,
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
    setNavError("");
    const nav = rowNavigation(row, kind);
    if (!nav) return;
    const inst = await resolveInstrument(nav.tradingsymbol, nav.type);
    if (!inst) {
      setNavError(`Could not resolve instrument for ${nav.tradingsymbol}.`);
      return;
    }
    navigate(
      `/charts?key=${encodeURIComponent(inst.instrument_token)}` +
        `&sym=${encodeURIComponent(inst.tradingsymbol)}` +
        `&ex=${encodeURIComponent(inst.exchange)}` +
        `&type=${encodeURIComponent(inst.instrument_type)}`,
    );
  };

  const columns = COLUMNS[kind];
  const rows = runQuery.data?.rows ?? [];

  let body: React.ReactNode;
  if (scannersQuery.isLoading) {
    body = <AsyncStateView status="loading" loadingLabel="Loading scanners…" />;
  } else if (scannersQuery.isError) {
    body = (
      <AsyncStateView status="error" error={scannersQuery.error as ApiError} />
    );
  } else if (runQuery.isLoading) {
    body = <AsyncStateView status="loading" loadingLabel="Scanning…" />;
  } else if (runQuery.isError) {
    body = (
      <AsyncStateView
        status="error"
        error={runQuery.error as ApiError}
        onRetry={() => runQuery.refetch()}
      />
    );
  } else if (!rows.length) {
    const r = runQuery.data;
    body = (
      <AsyncStateView
        status="empty"
        emptyLabel={`No rows (eligible ${r?.eligible ?? 0}, quoted ${r?.quoted ?? 0}).`}
      />
    );
  } else {
    body = (
      <div className="card" style={{ padding: 0, overflow: "auto" }}>
        <table className="table">
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
              <tr key={`${row.symbol}-${row.contract ?? ""}-${i}`} onClick={() => onRowClick(row)}>
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
    <div className="panel">
      <div className="page-header">
        <h1 className="page-title">Scanners</h1>
        {runQuery.data && (
          <span className="muted">
            {runQuery.data.scanner} · {runQuery.data.universe}
            {runQuery.data.as_of
              ? ` · as of ${runQuery.data.as_of}`
              : ` · no live quotes (eligible ${runQuery.data.eligible}, quoted ${runQuery.data.quoted})`}
          </span>
        )}
      </div>

      <div className="control-row">
        <Field label="Scanner">
          <Select value={name} onChange={(e) => setName(e.target.value)}>
            {defs.map((d) => (
              <option key={d.name} value={d.name}>
                {d.title}
              </option>
            ))}
          </Select>
        </Field>
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

        {isOption && (
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

        <Button variant="primary" onClick={() => runQuery.refetch()}>
          Run
        </Button>
      </div>

      {navError && <div className="hint err">{navError}</div>}
      {body}
    </div>
  );
}
