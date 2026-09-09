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

// Header strip: underlying identity, spot (live/stale/fallback labeled honestly),
// and the backend-resolved ATM strike. No value is fabricated; a missing spot is
// shown as unavailable rather than substituted with a midpoint.
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
  const spotHint = stale
    ? "last-session value (no live feed)"
    : fallback
      ? "no live quote — midpoint is not a spot"
      : spotBasis === "live" || spotBasis === "explicit"
        ? "live"
        : "";

  return (
    <div className="fno-header">
      <div className="fno-header-main">
        <Button variant="default" onClick={onBack} title="Back to underlyings">
          ←
        </Button>
        <div>
          <div className="fno-symbol">
            {symbol}
            <span className={`chip ${kind === "index" ? "chip-info" : ""}`}>
              {kind === "index" ? "Index" : "Equity"}
            </span>
          </div>
          <div className="hint">
            {ltp == null ? (
              <span className="stale-val">Spot unavailable</span>
            ) : (
              <>
                <span className="fno-ltp">₹{fmtNum(ltp)}</span>{" "}
                <span className={`${t}`}>
                  {fmtNum(change)} ({fmtPct(changePercent)})
                </span>
                {spotHint && <span className="muted"> · {spotHint}</span>}
              </>
            )}
          </div>
        </div>
        <div className="fno-atm">
          <span className="stat-label">ATM</span>
          <span className="stat-value">{atm == null ? "—" : fmtNum(atm)}</span>
        </div>
      </div>
      {notes && notes.length > 0 && (
        <div className="hint">{notes.join("; ")}</div>
      )}
    </div>
  );
}
