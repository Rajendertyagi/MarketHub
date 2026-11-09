# Yahoo Finance / yfinance — AI Research & Data Capability

> MarketHub can answer questions conversationally using Yahoo Finance data.
> This is an **AI research/data source**, not a market-data feed.

## What this is

```
User / AI  →  MarketHub Yahoo tools  →  yfinance  →  Yahoo Finance
                                  ↓
                          structured JSON result
                                  ↓
                          AI conversation
```

MarketHub reaches Yahoo through
[`yfinance`](https://github.com/ranaroussi/yfinance) (upstream, **not**
vendored — it is a declared dependency). yfinance exposes Yahoo's publicly
available endpoints; MarketHub adds the MCP tool surface, validation,
normalization, and honest freshness reporting.

## What this is NOT

Yahoo is deliberately **disconnected** from the broker-authoritative path.
It never:

- writes quotes into `MarketService` or the canonical quote store;
- feeds, subscribes to, or drives a market feed;
- enters the F&O universe, provider routing, futures board, or heatmap;
- replaces the broker option chain (`option_chain` / `analyze_option_chain`
  remain authoritative);
- stores Yahoo cookies, crumbs, or session material.

Every Yahoo response is labeled `"source": "yahoo"` so it can never be
mistaken for broker data. Yahoo option data is explicitly marked
research-only in the response warnings.

## The nine tools

| Tool | Purpose |
|---|---|
| `yahoo_search` | Symbol/company discovery ("Reliance Industries" → `RELIANCE.NS`) |
| `yahoo_quote` | Current quote snapshot for any Yahoo instrument |
| `yahoo_history` | Historical OHLCV candles (period or date range, any interval) |
| `yahoo_company` | Company/instrument profile, sector, industry, business summary |
| `yahoo_financials` | Income statement / balance sheet / cash flow (annual or quarterly) |
| `yahoo_corporate_actions` | Dividend and split history |
| `yahoo_holders` | Major / institutional / insider ownership |
| `yahoo_options` | Option chain (research-only, not the broker F&O path) |
| `yahoo_live_quote` | One bounded Yahoo WebSocket tick |

The AI calls these **only when a question actually needs Yahoo data**. There
is no prefetch on every chat turn.

## Supported asset classes

One set of tools covers every Yahoo symbol model. The same `yahoo_quote`
handles all of these; `quote_type` in the response tells the AI which is
which.

| Asset class | Examples | Available via |
|---|---|---|
| US equities | `AAPL`, `MSFT`, `TSLA` | all tools |
| Indian equities | `RELIANCE.NS`, `TCS.NS` | quote, history, company, financials, actions |
| Indices | `^NSEI`, `^NSEBANK`, `^GSPC` | quote, history (no statements) |
| Crypto | `BTC-USD`, `ETH-USD`, `BTC-INR` | quote, history, live tick |
| Commodities / futures | `GC=F`, `CL=F`, `SI=F` | quote, history, live tick |
| Forex | `EURUSD=X`, `USDINR=X` | quote, history, live tick |
| ETFs | `SPY`, `QQQ`, `VTI` | quote, history, company, options |
| International | `0700.HK`, `7203.T`, `BABA` | quote, history, company, financials |

## Symbol format

Yahoo symbols are not MarketHub instruments. Use Yahoo's own notation:

- `.NS` / `.BO` — India (NSE / BSE), e.g. `RELIANCE.NS`
- `^` prefix — indices, e.g. `^NSEI`, `^INDIAVIX`
- `-USD` / `-INR` — crypto, e.g. `BTC-USD`
- `=F` — futures, e.g. `GC=F`
- `=X` — forex pairs, e.g. `EURUSD=X`
- `.HK`, `.T`, `.L` — international exchanges

Use `yahoo_search` whenever the user names a company rather than a symbol.
No fixed security universe is hardcoded anywhere.

## Freshness — read this before calling anything "live"

Every response carries a `freshness` field. MarketHub never manufactures a
real-time claim; the value is derived strictly from Yahoo's own evidence.

| Value | Meaning |
|---|---|
| `LIVE_STREAM` | Only from an actual WebSocket tick (`yahoo_live_quote`) |
| `SNAPSHOT` | A point-in-time quote with no verifiable delay metadata |
| `DELAYED` | Yahoo explicitly reported a delay (e.g. Indian NSE is typically 15 min) |
| `HISTORICAL` | Candles/statements/actions — historical by nature |
| `UNKNOWN` | Not enough evidence to claim anything |
| `UNAVAILABLE` | No data returned |

The evidence used is `exchangeDataDelayedBy` and `quoteSourceName`. A quote
is only `LIVE_STREAM` when Yahoo reports zero delay **and** a real-time
source name. Absence of evidence is `UNKNOWN`, never "real-time".

## Live WebSocket — bounded by design

`yahoo_live_quote` opens a **short-lived** `AsyncWebSocket`, subscribes to one
symbol, captures **one** tick, and closes. There is:

- no permanent socket per request;
- no permanent Yahoo polling loop;
- no uncontrolled subscription storm;
- a hard timeout (max 30s) and a guaranteed close, including on timeout.

Stream entitlement is not guaranteed for every symbol or exchange. When no
tick arrives the result is `stream_unavailable` — that is correct behavior,
not a failure. Fall back to `yahoo_quote`.

Implementation note: yfinance's `AsyncWebSocket.listen()` is a **coroutine**
that runs forever and pushes into a callback; it is not an async iterator.
The service runs it as a task, captures the first message, then cancels and
closes.

## Error model

Provider failures are returned as structured data with a closed code set —
never raw exceptions, cookies, crumbs, headers, or stack traces:

`invalid_symbol`, `symbol_not_found`, `data_unavailable`,
`unavailable_for_asset`, `rate_limited`, `network_error`,
`unsupported_operation`, `stream_unavailable`, `service_unavailable`,
`invalid_period`, `invalid_interval`, `invalid_statement`,
`invalid_holder_type`, `invalid_arguments`, `invalid_request`.

## Performance & rate limits

- A minimum gap (default 0.5s) is enforced between outbound yfinance calls.
- All result sizes are bounded server-side (history, statements, holders,
  options, search), so one AI turn cannot become a request storm.
- HTTP 429 maps to `rate_limited` with a redacted reason.
- There is **no** background prefetch — reads happen only when asked.

## Configuration

None required. yfinance's public endpoints need no API key, no login, and no
stored cookie, so there is nothing for a non-technical user to configure.
Optional tuning (`min_request_interval_seconds`, `stream_timeout_seconds`)
lives in `yfinance_service/config.py` with safe defaults.

## Structured responses

All tools return JSON-compatible dictionaries. Raw pandas `DataFrame` /
`Series` objects never cross the MCP boundary. `NaN`, `NaT`, and infinities
are normalized to `null`; timestamps are ISO-8601 UTC; numpy scalars become
plain Python scalars.

Every response carries: `source`, `symbol`, `requested_symbol`,
`resolved_symbol`, `data_type`, `quote_type`, `timestamp`, `freshness`,
`currency`, `exchange`, `data`, and `warnings` (plus `code`/`reason` on
failure).

## Example questions the AI can now answer

- "What is RELIANCE.NS trading at?"
- "Compare TCS and INFY revenue growth."
- "Show NIFTY's last 30 trading days."
- "What is Apple's market cap?"
- "Give me the latest quarterly income statement for Microsoft."
- "What are the current NIFTY options for the nearest expiry?" (use the
  broker `option_chain` for authoritative Indian F&O)
- "Show Reliance's dividend history."
- "Find Tesla and give me its current quote."
- "What's Bitcoin at?" / "Gold futures?" / "EURUSD?"

## Known limitations

- Yahoo does not publish options for Indian NSE symbols or indices —
  `unavailable_for_asset` is expected.
- Financial statements, holders, and company metadata apply to equities/ETFs
  only; indices, crypto, forex, and futures return an honest empty result.
- Intraday (1m/2m) history is limited by Yahoo to a short recent window.
- `Ticker.earnings` and `Ticker.shares` are unavailable in current yfinance;
  use `yahoo_financials` (income statement) instead.
- Yahoo may throttle or change endpoints; failures surface as
  `rate_limited` / `network_error` rather than as fabricated data.

## Legal / usage

yfinance is an **unofficial** client. Yahoo, Y!Finance, and Yahoo Finance are
trademarks of Yahoo, Inc. yfinance is not affiliated with or endorsed by
Yahoo. Yahoo's finance API is intended for personal use; consult Yahoo's
terms for your rights to the data. MarketHub makes no claim that Yahoo has
licensed it to redistribute Yahoo data, and this capability is positioned as
AI research assistance rather than a licensed data feed.
