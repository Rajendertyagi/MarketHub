import type { ReactNode } from "react";
import type { ScanRow } from "@/types";
import { fmtNum, fmtPct, fmtSigned, fmtVol } from "@/utils/format";
import { ivToPercent } from "@/utils/iv";

export type ScannerKind = "equity" | "future" | "option";

export interface Column {
  key: keyof ScanRow | "iv_pct";
  label: string;
  numeric?: boolean;
}

export const COLUMNS: Record<ScannerKind, Column[]> = {
  equity: [
    { key: "symbol", label: "Symbol" },
    { key: "sector", label: "Sector" },
    { key: "ltp", label: "LTP", numeric: true },
    { key: "change_percent", label: "Chg%", numeric: true },
    { key: "volume", label: "Volume", numeric: true },
    { key: "status", label: "Status" },
    { key: "freshness", label: "Updated" },
  ],
  future: [
    { key: "symbol", label: "Symbol" },
    { key: "contract", label: "Contract" },
    { key: "expiry", label: "Expiry" },
    { key: "ltp", label: "LTP", numeric: true },
    { key: "change_percent", label: "Chg%", numeric: true },
    { key: "oi", label: "OI", numeric: true },
    { key: "oi_change", label: "OI Δ", numeric: true },
    { key: "oi_change_percent", label: "OI Δ%", numeric: true },
    { key: "status", label: "Status" },
    { key: "freshness", label: "Updated" },
  ],
  option: [
    { key: "symbol", label: "Underlying" },
    { key: "contract", label: "Contract" },
    { key: "expiry", label: "Expiry" },
    { key: "strike", label: "Strike", numeric: true },
    { key: "option_type", label: "CE/PE" },
    { key: "ltp", label: "LTP", numeric: true },
    { key: "iv_pct", label: "IV%", numeric: true },
    { key: "oi", label: "OI", numeric: true },
    { key: "status", label: "Status" },
    { key: "freshness", label: "Updated" },
  ],
};

// Resolve the navigation target for a row, preserving EXACT instrument identity.
// For derivatives we open the contract (never substitute the underlying cash
// equity); for equities we open the symbol. Returns the tradingsymbol + type
// that `resolveInstrument` will map to the canonical instrument_token.
export function rowNavigation(
  row: ScanRow,
  kind: ScannerKind,
): { tradingsymbol: string; type: string } | null {
  if (kind === "future") {
    const q = row.contract || row.symbol;
    return q ? { tradingsymbol: q, type: "FUTURE" } : null;
  }
  if (kind === "option") {
    const q = row.contract || row.symbol;
    return q ? { tradingsymbol: q, type: "OPTION" } : null;
  }
  return row.symbol ? { tradingsymbol: row.symbol, type: "EQUITY" } : null;
}

function cellContent(key: Column["key"], row: ScanRow): ReactNode {
  if (key === "iv_pct") {
    return ivToPercent(row.iv); // canonical fraction -> percent (display only)
  }
  const v = row[key as keyof ScanRow];
  switch (key) {
    case "ltp":
    case "strike":
      return fmtNum(v as number | null);
    case "volume":
    case "oi":
      return fmtVol(v as number | null);
    case "change_percent":
    case "oi_change_percent": {
      const n = v as number | null;
      if (n == null) return "-";
      return <span className={n > 0 ? "pos" : n < 0 ? "neg" : ""}>{fmtPct(n)}</span>;
    }
    case "oi_change": {
      const n = v as number | null;
      if (n == null) return "-";
      return <span className={n > 0 ? "pos" : n < 0 ? "neg" : ""}>{fmtSigned(n)}</span>;
    }
    case "status":
      return (
        <span className={v === "unavailable" ? "chip chip-off" : "chip chip-on"}>
          {String(v ?? "-")}
        </span>
      );
    case "freshness":
      return <span className="muted">{v ? String(v) : "-"}</span>;
    default:
      return v == null || v === "" ? "-" : String(v);
  }
}

export function renderCell(key: Column["key"], row: ScanRow): ReactNode {
  return cellContent(key, row);
}
