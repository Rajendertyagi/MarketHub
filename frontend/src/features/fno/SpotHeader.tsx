import type { FnoUnderlyingKind } from "@/types";
import { fmtNum, fmtPct, tone } from "@/utils/format";
import { Button } from "@/components/ui";

interface Props {
  symbol: string;
  kind: FnoUnderlyingKind;
  ltp: number | null;
  change: number | null;
  changePercent: number | null;
  atm: number | null;
  spotBasis?: string | null;
  notes?: string[];
  onBack: () => void;
}

// Compact header strip: back · underlying · spot (live/stale/fallback labeled
// honestly) · ATM strike, all on one line. No value is fabricated; a missing
// spot is shown as unavailable rather than substituted with a midpoint.
export function SpotHeader({
  symbol,
  kind,
  ltp,
  change,
  changePercent,
  atm,
  spotBasis,
  notes,
  onBack,
}: Props) {
  const t = tone(change);
  const stale = spotBasis === "stale";
  const fallback = spotBasis === "fallback_mid_strike";
  const spotHint =
    stale
      ? "last-session"
      : fallback
        ? "midpoint"
        : spotBasis === "live" || spotBasis === "explicit"
          ? "live"
          : "";

  return (
    <div className="fno-topbar">
      <Button variant="default" onClick={onBack} title="Back to underlyings">
        ←
      </Button>
      <span className="fno-sym">
        {symbol}
        <span className={`chip ${kind === "index" ? "chip-info" : ""}`}>
          {kind === "index" ? "Index" : "Equity"}
        </span>
      </span>
      <span className="fno-spot">
        {ltp == null ? (
          <span className="stale-val">Spot unavailable</span>
        ) : (
          <>
            <span className="fno-ltp">₹{fmtNum(ltp)}</span>{" "}
            <span className={t}>
              {fmtNum(change)} ({fmtPct(changePercent)})
            </span>
            {spotHint && <span className="muted"> · {spotHint}</span>}
          </>
        )}
      </span>
      <span className="chip chip-atm fno-atm">
        ATM {atm == null ? "—" : fmtNum(atm)}
      </span>
      {notes && notes.length > 0 && (
        <span className="hint fno-notes">{notes.join("; ")}</span>
      )}
    </div>
  );
}
