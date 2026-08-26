# FYERS TBT DEPTH CHANNEL IMPLEMENTATION PLAN

**Date**: 2026-08-27  
**Status**: Plan Ready for Approval  
**Scope**: Supplement HSM with 50-level depth, SAME MarketService state  

---

## EXECUTIVE SUMMARY

Implement Fyers TBT as an **async-native depth channel** that supplements the existing HSM feed. Both feeds update the SAME canonical MarketService state. TBT provides 50-level order book depth while HSM continues providing quotes/OHLC/OI.

**Key Principles:**
- ✅ HSM stays unchanged (authoritative for quotes)
- ✅ TBT adds depth only (no quote overwrites)
- ✅ Async-native (no callback wrapping)
- ✅ Reuses existing auth, identity resolver, lifecycle
- ✅ Local order book reconstruction with sequence gap detection
- ✅ 24 comprehensive tests (protocol + integration + lifecycle)

---

## ARCHITECTURE

```
FyersProvider (existing)
├── HSM Connection (existing)
│   ├── Quote updates → MarketService.apply_quote()
│   └── 5-level depth → MarketService.apply_depth()
│
└── TBT Connection (NEW)
    ├── 50-level depth → MarketService.apply_depth()
    ├── Local order book state
    ├── Snapshot/delta reconstruction
    └── Sequence gap detection
                          ↓
                   SAME MarketService
                   (quotes from HSM,
                    depth from TBT)
```

**File Structure:**
```
brokers/fyers/
├── auth.py              (existing - reuse)
├── feed.py              (existing HSM - unchanged)
└── tbt/                 (NEW directory)
    ├── __init__.py
    ├── feed.py          # TBT async client
    ├── normalizer.py    # Protobuf → canonical Depth
    └── proto/
        └── msg_pb2.py   # Official protobuf bindings

sources/registry.py      (modify - register tbt_enabled flag)
market/models.py         (verify - Depth already supports 50 levels)
test/test_fyers_tbt.py   (NEW - 24 tests)
```

---

## PROTOCOL SPECIFICATIONS (OFFICIAL)

### WebSocket URL
```python
# Dynamically fetched (do NOT hardcode)
URL = GET https://api-t1.fyers.in/indus/home/tbtws
      Header: Authorization: {access_token}
      Response: {"data": {"socket_url": "wss://rtsocket-api.fyers.in/versova"}}
```

### Subscription Protocol (JSON)
```python
# Subscribe
{
    "type": 1,
    "data": {
        "subs": 1,
        "symbols": ["NSE:RELIANCE-EQ"],
        "mode": "depth",
        "channel": "1"
    }
}

# Unsubscribe
{
    "type": 1,
    "data": {
        "subs": -1,
        "symbols": ["NSE:RELIANCE-EQ"],
        "mode": "depth",
        "channel": "1"
    }
}

# Pause/Resume channels
{
    "type": 2,
    "data": {
        "resumeChannels": ["1"],
        "pauseChannels": []
    }
}
```

### Data Protocol (Protobuf Binary)
```python
# Parse as SocketMessage
socket_msg = SocketMessage()
socket_msg.ParseFromString(binary_frame)

# Access depth data
for symbol, feed in socket_msg.feeds.items():
    if feed.HasField('depth'):
        depth = feed.depth
        # 50 levels max
        for bid in depth.bids[:50]:
            price = bid.price.value / 100  # cents to rupees
            qty = bid.qty.value
            orders = bid.nord.value
```

### Rate Limits (Official)
- Max 3 connections per user
- Max 5 symbols per connection (market depth mode)
- Max 50 channels per connection

---

## IMPLEMENTATION DETAILS

### 1. TBT Feed Client (`brokers/fyers/tbt/feed.py`)

**Class**: `FyersTbtFeed`

```python
class FyersTbtFeed:
    """Async-native Fyers TBT depth feed."""
    
    WS_URL_ENDPOINT = "https://api-t1.fyers.in/indus/home/tbtws"
    MAX_CONNECTIONS = 3
    SYMBOLS_PER_CONNECTION = 5
    MAX_CHANNELS = 50
    
    def __init__(self, access_token_getter, config, market_service):
        self._token_getter = access_token_getter
        self._market_service = market_service
        self._desired_symbols = set()
        self._lock = asyncio.Lock()
        self._task = None
        self._stop_event = asyncio.Event()
        self._ws = None
        self._order_books = {}  # symbol -> local book state
        self._sequence_numbers = {}  # symbol -> last seq no
        self._stats = {...}  # counters for observability
```

**Key Methods:**
- `connect()` — Fetch URL, connect WebSocket, start recv loop
- `subscribe(symbols)` — Add to desired set, send subscribe frames
- `unsubscribe(symbols)` — Remove from desired set, send unsubscribe frames
- `stop()` — Set stop event, await task cancellation
- `status()` — Return current state and counters

**Lifecycle Pattern** (matches HSM):
```python
async def run(self, publisher, stop_event):
    """Main lifecycle loop with reconnect."""
    while not stop_event.is_set():
        try:
            await self._run_session(stop_event)
        except AuthError:
            self._set_state("auth_required")
            break
        except Exception as e:
            self._log_reconnect(e)
            await asyncio.sleep(self._backoff_delay())
```

### 2. Protobuf Normalizer (`brokers/fyers/tbt/normalizer.py`)

**Function**: `normalize_tbt_depth(feed, symbol, received_ts)`

```python
def normalize_tbt_depth(
    feed: protomsg.MarketFeed,
    symbol: str,
    received_ts: datetime,
) -> Depth:
    """Convert TBT MarketFeed.depth to canonical Depth."""
    
    bids = []
    for level in feed.depth.bids[:50]:
        if level.HasField('price') and level.price.value > 0:
            bids.append(DepthLevel(
                price=level.price.value / 100,  # cents to rupees
                quantity=float(level.qty.value) if level.HasField('qty') else 0.0,
                orders=int(level.nord.value) if level.HasField('nord') and level.nord.value > 0 else None,
            ))
    
    asks = []
    for level in feed.depth.asks[:50]:
        if level.HasField('price') and level.price.value > 0:
            asks.append(DepthLevel(
                price=level.price.value / 100,
                quantity=float(level.qty.value) if level.HasField('qty') else 0.0,
                orders=int(level.nord.value) if level.HasField('nord') and level.nord.value > 0 else None,
            ))
    
    return Depth(
        instrument_token=symbol,
        exchange=_extract_exchange(symbol),
        tradingsymbol=_extract_tradingsymbol(symbol),
        received_ts=received_ts,
        bids=tuple(bids),
        asks=tuple(asks),
        exchange_ts=_parse_feed_time(feed.feed_time.value) if feed.HasField('feed_time') else None,
    )
```

**Helper**: `_extract_exchange(symbol)` — Parse "NSE:RELIANCE-EQ" → "NSE"
**Helper**: `_extract_tradingsymbol(symbol)` — Parse "NSE:RELIANCE-EQ" → "RELIANCE-EQ"

### 3. Local Order Book Reconstruction

**Purpose**: Handle snapshot + delta semantics correctly

```python
class _LocalOrderBook:
    """Per-instrument order book state for delta reconstruction."""
    
    def __init__(self):
        self.bids = {}   # price -> DepthLevel
        self.asks = {}   # price -> DepthLevel
        self.snapshot = False
    
    def apply_snapshot(self, depth: Depth):
        """Replace entire book (full snapshot)."""
        self.bids = {level.price: level for level in depth.bids}
        self.asks = {level.price: level for level in depth.asks}
        self.snapshot = True
    
    def apply_delta(self, depth: Depth):
        """Merge incremental update (delta)."""
        # Update/insert bids
        for level in depth.bids:
            if level.quantity == 0:
                self.bids.pop(level.price, None)  # Delete
            else:
                self.bids[level.price] = level
        
        # Update/insert asks
        for level in depth.asks:
            if level.quantity == 0:
                self.asks.pop(level.price, None)
            else:
                self.asks[level.price] = level
    
    def get_full_depth(self, received_ts: datetime) -> Depth:
        """Reconstruct canonical Depth from local state."""
        bids = tuple(sorted(self.bids.values(), key=lambda x: -x.price))
        asks = tuple(sorted(self.asks.values(), key=lambda x: x.price))
        return Depth(
            instrument_token=self.instrument_token,
            exchange=self.exchange,
            tradingsymbol=self.tradingsymbol,
            received_ts=received_ts,
            bids=bids,
            asks=asks,
        )
```

### 4. Sequence Gap Detection

```python
def _check_sequence(self, symbol: str, seq_no: int) -> bool:
    """Detect sequence gaps. Returns True if gap detected."""
    last_seq = self._sequence_numbers.get(symbol)
    
    if last_seq is not None:
        expected = last_seq + 1
        if seq_no != expected:
            # Gap detected — invalidate local book
            logger.warning(
                "TBT sequence gap for %s: expected %d, got %d",
                symbol, expected, seq_no
            )
            self._stats["sequence_gaps"] += 1
            self._invalidate_book(symbol)
            return True
    
    self._sequence_numbers[symbol] = seq_no
    return False

def _invalidate_book(self, symbol: str):
    """Clear local book state, force full resubscribe."""
    if symbol in self._order_books:
        del self._order_books[symbol]
    self._sequence_numbers.pop(symbol, None)
```

### 5. Rate Limit Enforcement

```python
def _validate_subscription(self, symbols: list[str]) -> list[str]:
    """Check rate limits before subscribing."""
    if len(self._desired_symbols) + len(symbols) > self.SYMBOLS_PER_CONNECTION:
        raise ValueError(
            f"TBT rate limit: max {self.SYMBOLS_PER_CONNECTION} symbols "
            f"per connection, requested {len(self._desired_symbols) + len(symbols)}"
        )
    return symbols
```

---

## INTEGRATION POINTS

### 1. Registry Update (`sources/registry.py`)

```python
def _create_fyers_feed(config: dict, *, market_service: Any = None) -> Any:
    """Construct FyersFeed WITH optional TBT channel."""
    from brokers.fyers.feed import FyersFeed
    from brokers.fyers.tbt.feed import FyersTbtFeed
    
    # ... existing HSM feed creation ...
    
    hsm_feed = FyersFeed(...)
    
    # Optional TBT channel
    tbt_feed = None
    if config.get("tbt_enabled", False):
        tbt_feed = FyersTbtFeed(
            access_token_getter=getter,
            config=config,
            market_service=market_service,
        )
        # Start TBT in background
        asyncio.create_task(tbt_feed.run(publisher, stop_event))
    
    return hsm_feed, tbt_feed  # Return both
```

**Config Format:**
```json
{
    "type": "fyers_feed",
    "tbt_enabled": true,
    "instruments": [
        {"key": "NSE:RELIANCE-EQ", "exchange": "NSE", "tradingsymbol": "RELIANCE-EQ"}
    ]
}
```

### 2. MarketService Integration

```python
# In TBT recv loop:
async def _handle_depth_message(self, symbol: str, feed: protomsg.MarketFeed):
    received_ts = datetime.now(timezone.utc)
    
    # Check sequence
    seq_no = feed.sequence_no
    if self._check_sequence(symbol, seq_no):
        # Gap detected — request full resubscribe
        await self._resubscribe_symbol(symbol)
        return
    
    # Get or create local book
    if symbol not in self._order_books:
        self._order_books[symbol] = _LocalOrderBook()
    
    book = self._order_books[symbol]
    
    # Apply snapshot or delta
    if feed.snapshot:
        book.apply_snapshot(depth)
    else:
        book.apply_delta(depth)
    
    # Reconstruct full depth
    full_depth = book.get_full_depth(received_ts)
    
    # Dispatch to MarketService
    if self._market_service:
        await self._market_service.apply_depth(full_depth)
    
    self._stats["depth_count"] += 1
```

### 3. Identity Resolution

Reuse existing `app/instrument_identity.py`:
```python
# TBT uses same canonical symbol format as HSM
# "NSE:RELIANCE-EQ" is the canonical key
# No additional alias mapping needed
```

---

## DEPENDENCIES

### Required
```toml
[project]
dependencies = [
    "protobuf>=5.26.1",      # NEW: TBT protobuf parsing
    "websocket-client>=1.6.0",  # NEW: TBT WebSocket (NOT "websockets")
]
```

**Note**: MarketHub currently uses `websockets` package (async). TBT official package uses `websocket-client` (threaded). We'll use `websockets` with protobuf parsing directly (async-native).

### Protobuf Generation
Download official bindings from:
- `https://public.fyers.in/tbtproto/1.0.0/protogencode.zip`
- Extract: `protogencode/python/msg_pb2.py`
- Place in: `brokers/fyers/tbt/proto/msg_pb2.py`

---

## TEST PLAN (24 Tests)

### Protocol Tests (TBT1-TBT12)
```python
# TBT1: Auth/connect request correct
# TBT2: Subscription message correct
# TBT3: Unsubscribe correct
# TBT4: Full snapshot decode
# TBT5: Delta decode
# TBT6: 50-level book reconstruction
# TBT7: Price-level update
# TBT8: Price-level delete (zero quantity)
# TBT9: Number-of-orders preserved
# TBT10: Sequence gap detected
# TBT11: Reconnect clears/rebuilds book safely
# TBT12: Runtime add/remove symbols
```

### Market Service Tests (TBT13-TBT18)
```python
# TBT13: HSM quote + TBT depth merge into SAME canonical state
# TBT14: TBT does not erase HSM LTP/OHLC/OI/etc.
# TBT15: Alias/catalog/TBT identifiers resolve to one instrument
# TBT16: Unsupported index depth rejected safely
# TBT17: 50 levels preserved in canonical Depth
# TBT18: Reconnect/resubscribe restores depth
```

### Lifecycle Tests (TBT19-TBT24)
```python
# TBT19: Stop closes task/socket
# TBT20: Restart produces exactly one task/socket
# TBT21: Duplicate start does not duplicate connection
# TBT22: Auth rejection stops retry loop
# TBT23: Transient disconnect reconnects
# TBT24: Shutdown during backoff exits cleanly
```

**Test Strategy:**
- Mock protobuf messages (no live broker)
- Stub WebSocket server
- Verify MarketService state after each test
- Check counters/logs for observability

---

## LOGGING & OBSERVABILITY

### Status Endpoint (Internal)
```python
@router.get("/api/tbt/status")
async def get_tbt_status():
    """Return TBT feed status (internal/testing only)."""
    return {
        "enabled": feed.is_enabled,
        "state": feed.state,
        "task_running": feed._task is not None,
        "desired_count": len(feed._desired_symbols),
        "subscribed_count": len(feed._order_books),
        "snapshot_count": feed._stats["snapshot_count"],
        "delta_count": feed._stats["delta_count"],
        "malformed_count": feed._stats["malformed_count"],
        "sequence_gap_count": feed._stats["sequence_gaps"],
        "reconnect_count": feed._stats["reconnects"],
        "last_message_at": feed._stats["last_message_at"],
        "safe_last_error": feed._stats["last_error"],
    }
```

### Log Events
```python
logger.info("TBT connected to %s", ws_url)
logger.info("TBT subscribing to %d symbols", len(symbols))
logger.warning("TBT sequence gap for %s: expected %d, got %d", ...)
logger.error("TBT auth failed: %s", error)
logger.info("TBT reconnect attempt %d/%d", attempt, max_attempts)
```

**Never log:**
- Access tokens
- Full WebSocket URLs (mask domain)
- Raw protobuf payloads
- Full order book state

---

## ERROR CLASSIFICATION

| Error Type | Handling |
|------------|----------|
| Auth failure | Set state `auth_required`, stop retry loop |
| Transient network | Reconnect with exponential backoff |
| Malformed protobuf | Increment counter, skip message, continue |
| Unsupported symbol | Log warning, skip subscription |
| Sequence gap | Invalidate local book, resubscribe |
| Rate limit exceeded | Queue symbols, subscribe in batches |
| Application shutdown | Cancel task, close WebSocket cleanly |

---

## RECONNECT STRATEGY

```python
async def _run_session(self, stop_event):
    """Single session with reconnect logic."""
    max_retries = 50
    retry_delay = 1
    
    for attempt in range(max_retries):
        if stop_event.is_set():
            break
        
        try:
            await self._connect_and_subscribe()
            await self._recv_loop(stop_event)
            break  # Clean shutdown
        except AuthError:
            raise  # Terminal
        except Exception as e:
            self._stats["reconnects"] += 1
            logger.warning("TBT reconnect in %.1fs (attempt %d)", retry_delay, attempt + 1)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)  # Exponential backoff
    
    # Clear state on final exit
    self._order_books.clear()
    self._sequence_numbers.clear()
```

---

## CONFIGURATION

### Source Config
```json
{
    "type": "fyers_feed",
    "tbt_enabled": true,
    "instruments": [
        {"key": "NSE:RELIANCE-EQ", "exchange": "NSE", "tradingsymbol": "RELIANCE-EQ"}
    ]
}
```

### No New Secrets
- Reuses existing OAuth access token
- No additional credential storage
- TBT enabled via boolean flag only

---

## FILES TO CREATE/MODIFY

### New Files (6)
1. `brokers/fyers/tbt/__init__.py`
2. `brokers/fyers/tbt/feed.py` — Main TBT client (async)
3. `brokers/fyers/tbt/normalizer.py` — Protobuf → canonical Depth
4. `brokers/fyers/tbt/proto/__init__.py`
5. `brokers/fyers/tbt/proto/msg_pb2.py` — Official protobuf bindings
6. `test/test_fyers_tbt.py` — 24 comprehensive tests

### Modified Files (3)
1. `sources/registry.py` — Add `tbt_enabled` handling
2. `pyproject.toml` — Add `protobuf>=5.26.1` dependency
3. `api/product_routes.py` — Add `/api/tbt/status` (optional, for testing)

### Unchanged Files (Critical)
- ✅ `brokers/fyers/feed.py` (HSM — no changes)
- ✅ `market/models.py` (Depth already supports 50 levels)
- ✅ `market/normalize/fyers.py` (add new function, don't modify existing)
- ✅ `app/instrument_identity.py` (no changes)
- ✅ MCP, WebUI, alerts, chat (no changes)

---

## IMPLEMENTATION PHASES

### Phase 1: Foundation (Day 1)
- [ ] Download protobuf bindings
- [ ] Create `brokers/fyers/tbt/` directory structure
- [ ] Implement `normalizer.py` (Protobuf → canonical)
- [ ] Implement basic `feed.py` (connect, subscribe, recv)
- [ ] Add `protobuf` dependency to `pyproject.toml`
- [ ] Write basic unit tests for normalizer

### Phase 2: Core Logic (Day 1-2)
- [ ] Implement local order book state
- [ ] Implement sequence gap detection
- [ ] Implement snapshot vs delta semantics
- [ ] Implement reconnect with exponential backoff
- [ ] Add comprehensive protocol tests (TBT1-TBT12)
- [ ] Integrate with MarketService

### Phase 3: Integration (Day 2)
- [ ] Update source registry
- [ ] Add config flag handling
- [ ] Add status endpoint (internal)
- [ ] Write integration tests (TBT13-TBT18)
- [ ] Verify HSM + TBT coexistence
- [ ] Run full regression

### Phase 4: Lifecycle & Polish (Day 3)
- [ ] Write lifecycle tests (TBT19-TBT24)
- [ ] Add logging and observability
- [ ] Code review
- [ ] Final regression pass
- [ ] Commit and push

---

## RISK MITIGATION

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Protobuf parsing errors | Medium | High | Use official generated code, add tests |
| Sequence gap handling | Medium | Medium | Comprehensive tests, clear logging |
| Rate limit violations | Low | Medium | Enforce limits in subscription logic |
| HSM/TBT state collision | Low | High | Separate MarketService keys, verify no overwrites |
| Reconnect storms | Medium | Low | Exponential backoff, max retry limit |
| Memory leak (order books) | Low | Medium | Clear books on disconnect, bound by symbol count |

---

## SUCCESS CRITERIA

✅ **All 24 tests passing** (protocol + integration + lifecycle)  
✅ **HSM + TBT coexist** — same MarketService, no state corruption  
✅ **50-level depth** preserved in canonical model  
✅ **Sequence gap detection** working correctly  
✅ **Reconnect logic** robust (exponential backoff, max retries)  
✅ **No breaking changes** to existing HSM feed or other brokers  
✅ **Zero MCP/WebUI/alerts/chat modifications**  
✅ **CI green** on focused + full regression  

---

## FINAL VERDICT TEMPLATE

```
FYERS TBT DEPTH CHANNEL: [COMPLETE / INCOMPLETE]

COMPLETED:
- Protocol implementation
- 50-level depth normalization
- Sequence gap detection
- HSM/TBT coexistence
- 24/24 tests passing
- CI green

GAPS (if any):
- [List any remaining issues]
```

---

**Plan Created**: 2026-08-27  
**Estimated Timeline**: 2-3 days  
**Next Step**: Approval → Implementation
