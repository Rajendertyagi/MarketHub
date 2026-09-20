// Market session helpers. Inferred from IST wall-clock only; never
// broker-confirmed. UI must always label this as "inferred".

const MARKET_OPEN_IST_MIN = 9 * 60 + 15; // 09:15 IST
const MARKET_CLOSE_IST_MIN = 15 * 60 + 30; // 15:30 IST

// Infer NSE equity session open/closed from IST time.
export function inferredMarketOpen(now: Date = new Date()): boolean {
  const ist = new Date(now.getTime() + (330 + now.getTimezoneOffset()) * 60000);
  const day = ist.getDay();
  const mins = ist.getHours() * 60 + ist.getMinutes();
  return day >= 1 && day <= 5 && mins >= MARKET_OPEN_IST_MIN && mins <= MARKET_CLOSE_IST_MIN;
}
