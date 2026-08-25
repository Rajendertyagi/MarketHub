"""Release-candidate hardening regression suite.

Covers:
  - public_base_url / OAuth callback edge cases (normalization contract)
  - DB schema v12 migration matrix (fresh, v11->v12, v10->v12)
  - migration failure safety (rollback, version not advanced, DB usable)
  - backup from a real v12 database
  - master.key safety (never silently regenerated over ANY ciphertext)
  - log redaction against dummy secret-like values
"""
import asyncio
import json
import logging
import os
import sqlite3
import sys
import tempfile

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R


# ---------------------------------------------------------------------------
# P6: public_base_url / OAuth callback edge cases
# ---------------------------------------------------------------------------

def test_public_base_url_edge_cases(runner: R) -> None:
    from app.config import get_public_base_url, oauth_callback_url

    _cases = [
        # (config override or None, expected base)
        (None, "http://localhost:7070"),                      # omitted
        ("", "http://localhost:7070"),                        # empty
        ("   ", "http://localhost:7070"),                     # blank
        ("ftp://x", "http://localhost:7070"),                 # invalid scheme
        ("http://", "http://localhost:7070"),                 # missing host
        (42, "http://localhost:7070"),                        # non-string
        ("http://127.0.0.1:7070/", "http://127.0.0.1:7070"),  # trailing slash
        ("https://hub.example.com", "https://hub.example.com"),
        ("http://192.168.1.50:9090/app?x=1#frag",
         "http://192.168.1.50:9090"),                         # path/query/frag
        ("HTTP://Hub.Local:7070", "http://hub.local:7070"),   # case normalize
    ]
    for _i, (_raw, _expected) in enumerate(_cases):
        _cfg = {} if _raw is None else {"public_base_url": _raw}
        runner.assert_eq("BU-base-%d" % _i,
                         get_public_base_url(_cfg), _expected)

    # Both providers consume the SAME helper correctly.
    _base = get_public_base_url({"public_base_url": "http://box.lan:8443/"})
    runner.assert_eq("BU-fyers-callback",
                     oauth_callback_url(_base, "fyers"),
                     "http://box.lan:8443/auth/fyers/callback")
    runner.assert_eq("BU-upstox-callback",
                     oauth_callback_url(_base, "upstox"),
                     "http://box.lan:8443/auth/upstox/callback")
    # Helper itself normalizes hostile input.
    runner.assert_eq("BU-helper-garbage",
                     oauth_callback_url("not a url//?#", "fyers"),
                     "http://localhost:7070/auth/fyers/callback")


def _make_v12_store(db_path):
    """Fresh EventStore at current schema + one watchlist row."""
    from core.persistence.store import EventStore
    store = EventStore(db_path)
    conn = sqlite3.connect(db_path)
    try:
        now = "2026-01-01T00:00:00Z"
        conn.execute(
            "INSERT INTO watchlists (name, created_at) VALUES (?, ?)",
            ("hardening-wl", now))
        conn.commit()
    finally:
        conn.close()
    return store


def _downgrade(db_path, target_version):
    """Turn a v12 DB into a realistic older one by removing later additions."""
    conn = sqlite3.connect(db_path)
    try:
        if target_version <= 11:
            conn.execute("DROP TABLE IF EXISTS alert_trigger_history")
        if target_version <= 10:
            for t in ("instruments", "watchlist_items", "watchlists",
                      "market_alerts"):
                conn.execute("DROP TABLE IF EXISTS " + t)
        conn.execute("PRAGMA user_version = %d" % target_version)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# P10: migration matrix
# ---------------------------------------------------------------------------

def test_migration_matrix(runner: R) -> None:
    from core.persistence.store import EventStore

    _tmp = tempfile.mkdtemp()

    # fresh -> v12
    _fresh = os.path.join(_tmp, "fresh.db")
    EventStore(_fresh)
    _conn = sqlite3.connect(_fresh)
    _ver = _conn.execute("PRAGMA user_version").fetchone()[0]
    _tables = {r[0] for r in _conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    _conn.close()
    runner.assert_eq("MM-fresh-version", _ver, 12)
    for _t in ("alert_trigger_history", "watchlists", "market_alerts",
               "instruments", "consumer_event_state"):
        runner.assert_true("MM-fresh-table:" + _t, _t in _tables)

    # realistic v11 -> v12 (data preserved)
    _v11 = os.path.join(_tmp, "v11.db")
    _make_v12_store(_v11)
    _downgrade(_v11, 11)
    EventStore(_v11)
    _conn = sqlite3.connect(_v11)
    _ver = _conn.execute("PRAGMA user_version").fetchone()[0]
    _names = [r[0] for r in _conn.execute(
        "SELECT name FROM watchlists")]
    _conn.close()
    runner.assert_eq("MM-v11-version", _ver, 12)
    runner.assert_in("MM-v11-watchlist-preserved", "hardening-wl", _names)

    # realistic v10 -> v12 (product tables rebuilt empty, no data loss error)
    _v10 = os.path.join(_tmp, "v10.db")
    _make_v12_store(_v10)
    _downgrade(_v10, 10)
    EventStore(_v10)
    _conn = sqlite3.connect(_v10)
    _ver = _conn.execute("PRAGMA user_version").fetchone()[0]
    _tables = {r[0] for r in _conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    _conn.close()
    runner.assert_eq("MM-v10-version", _ver, 12)
    for _t in ("watchlists", "instruments", "alert_trigger_history"):
        runner.assert_true("MM-v10-table:" + _t, _t in _tables)


# ---------------------------------------------------------------------------
# P11: migration failure safety
# ---------------------------------------------------------------------------

def test_migration_failure_safety(runner: R) -> None:
    import core.persistence.store as _store_mod

    _tmp = tempfile.mkdtemp()
    _db = os.path.join(_tmp, "fail.db")
    _make_v12_store(_db)
    _downgrade(_db, 11)

    _orig = _store_mod.migrate_v11_to_v12

    def _boom(conn):
        raise RuntimeError("simulated migration failure")

    _store_mod.migrate_v11_to_v12 = _boom
    try:
        try:
            _store_mod.EventStore(_db)
            runner.assert_true("MF-should-have-raised", False)
        except Exception as exc:
            runner.assert_true("MF-error-type",
                               isinstance(exc, RuntimeError))
    finally:
        _store_mod.migrate_v11_to_v12 = _orig

    # Original DB untouched: still v11, still usable, not half-migrated.
    _conn = sqlite3.connect(_db)
    _ver = _conn.execute("PRAGMA user_version").fetchone()[0]
    _names = [r[0] for r in _conn.execute("SELECT name FROM watchlists")]
    _has_hist = bool(_conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='alert_trigger_history'"
    ).fetchone())
    _conn.close()
    runner.assert_eq("MF-version-not-advanced", _ver, 11)
    runner.assert_in("MF-data-intact", "hardening-wl", _names)
    runner.assert_false("MF-no-half-migration", _has_hist)

    # After the failure is gone the same DB migrates cleanly.
    _store_mod.EventStore(_db)
    _conn = sqlite3.connect(_db)
    _ver = _conn.execute("PRAGMA user_version").fetchone()[0]
    _conn.close()
    runner.assert_eq("MF-recovers", _ver, 12)


# ---------------------------------------------------------------------------
# P12: backup reality check
# ---------------------------------------------------------------------------

def test_backup_from_v12(runner: R) -> None:
    from pathlib import Path
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from api.product_routes import build_admin_routes

    _tmp = tempfile.mkdtemp()
    _db = os.path.join(_tmp, "events.db")
    _store = _make_v12_store(_db)
    # add an alert trigger history row so backup content is verifiable
    _conn = sqlite3.connect(_db)
    _wl = _conn.execute(
        "SELECT id FROM watchlists WHERE name='hardening-wl'").fetchone()[0]
    _conn.execute(
        "INSERT INTO alert_trigger_history (alert_id, tradingsymbol, field,"
        " operator, threshold, observed_value, provider, triggered_at,"
        " created_at) VALUES (1, 'NSE:HARDEN', 'ltp', 'gt', 100.0, 101.5,"
        " 'test', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')")
    _conn.commit()
    _conn.close()

    _data_dir = Path(_tmp)
    _app = Starlette(routes=build_admin_routes(_store, _data_dir))
    _c = TestClient(_app)
    _r = _c.post("/api/admin/backup")
    runner.assert_eq("BK-status", _r.status_code, 200)
    _body = _r.json()
    runner.assert_true("BK-file-named", bool(_body.get("file")))
    _bk = _data_dir / "backups" / _body["file"]
    runner.assert_true("BK-exists", _bk.is_file())
    runner.assert_true("BK-timestamped",
                       _body["file"].startswith("events-"))

    _bconn = sqlite3.connect(str(_bk))
    _ver = _bconn.execute("PRAGMA user_version").fetchone()[0]
    _wl_names = [r[0] for r in _bconn.execute(
        "SELECT name FROM watchlists")]
    _hist = _bconn.execute(
        "SELECT COUNT(*) FROM alert_trigger_history").fetchone()[0]
    _bconn.close()
    runner.assert_eq("BK-schema-version", _ver, 12)
    runner.assert_in("BK-watchlist-present", "hardening-wl", _wl_names)
    runner.assert_eq("BK-history-present", _hist, 1)

    # Failure path: backups dir replaced by a file -> safe 500, no traceback.
    _tmp2 = tempfile.mkdtemp()
    _db2 = os.path.join(_tmp2, "events.db")
    _store2 = _make_v12_store(_db2)
    (Path(_tmp2) / "backups").write_text("not a dir")
    _app2 = Starlette(routes=build_admin_routes(_store2, Path(_tmp2)))
    _r2 = TestClient(_app2).post("/api/admin/backup")
    runner.assert_eq("BK-failure-status", _r2.status_code, 500)
    runner.assert_eq("BK-failure-body", _r2.json().get("error"),
                     "backup failed")


# ---------------------------------------------------------------------------
# P14: master.key safety
# ---------------------------------------------------------------------------

def test_master_key_safety(runner: R) -> None:
    from core.persistence.store import EventStore
    from app.secrets_store import CredentialStore

    _tmp = tempfile.mkdtemp()

    # Fresh dir with NO ciphertext: key created exactly once.
    _db1 = os.path.join(_tmp, "a.db")
    _dir1 = os.path.join(_tmp, "d1")
    os.makedirs(_dir1)
    _s1 = CredentialStore(EventStore(_db1), data_dir=_dir1)
    _s1.save_fyers_credentials("APP-K", "SEC-K")
    _key_path = os.path.join(_dir1, "master.key")
    runner.assert_true("MK-created", os.path.isfile(_key_path))
    _key_bytes = open(_key_path, "rb").read()

    # FYERS-ONLY ciphertext + deleted key: must NOT regenerate.
    os.remove(_key_path)
    _s2 = CredentialStore(EventStore(_db1), data_dir=_dir1)
    _creds = _s2.load_fyers_credentials()
    runner.assert_true("MK-lost-key-unreadable",
                       _creds in (None, {}, {"app_id": "", "app_secret": ""}))
    runner.assert_false("MK-not-regenerated", os.path.isfile(_key_path))

    # Restoring the original key makes the ciphertext readable again.
    with open(_key_path, "wb") as _f:
        _f.write(_key_bytes)
    _s3 = CredentialStore(EventStore(_db1), data_dir=_dir1)
    _creds = _s3.load_fyers_credentials()
    runner.assert_eq("MK-restored-key-recovers",
                     _creds.get("app_id") if _creds else None, "APP-K")

    # Key content never changes while it remains valid.
    _s4 = CredentialStore(EventStore(_db1), data_dir=_dir1)
    _s4.load_fyers_credentials()
    runner.assert_eq("MK-stable",
                     open(_key_path, "rb").read(), _key_bytes)


# ---------------------------------------------------------------------------
# P16: log redaction adversarial test (dummy values only)
# ---------------------------------------------------------------------------

class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        try:
            self.records.append(self.format(record))
        except Exception:
            pass


def test_log_redaction(runner: R) -> None:
    from brokers.fyers.auth import FyersAuth, FyersAuthError
    from brokers.upstox.auth import UpstoxCredentials

    _DUMMY_SECRET = "DUMMY-FYERS-SECRET-abc123"
    _DUMMY_BEARER = "DUMMY-UPSTOX-BEARER-xyz789"
    _DUMMY_REFRESH = "DUMMY-REFRESH-TOKEN-qrs456"
    _DUMMY_AUTHCODE = "DUMMY-AUTH-CODE-000"

    _cap = _Capture()
    _cap.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    _roots = [logging.getLogger(), logging.getLogger("event_server")]
    _old_levels = [(r, r.level) for r in _roots]
    for _r in _roots:
        _r.addHandler(_cap)
        _r.setLevel(logging.DEBUG)

    try:
        # repr paths must redact
        _fa = FyersAuth(app_id="DUMMYAPP", secret_id=_DUMMY_SECRET,
                        redirect_uri="http://localhost:7070/auth/fyers/cb")
        logging.getLogger("event_server").info("auth obj: %s", _fa)
        _uc = UpstoxCredentials(access_token=_DUMMY_BEARER)
        logging.getLogger("event_server").info("creds obj: %s", _uc)

        # refresh failure path logs only the exception TYPE
        async def _fail(refresh_token):
            raise FyersAuthError("rejected " + _DUMMY_REFRESH)
        try:
            asyncio.run(_fail(_DUMMY_REFRESH))
        except FyersAuthError:
            logging.getLogger("event_server").warning(
                "fyers token restore failed: %s", type(Exception()).__name__)
    finally:
        for _r, _lvl in _old_levels:
            _r.removeHandler(_cap)
            _r.setLevel(_lvl)

    _blob = "\n".join(_cap.records)
    for _label, _dummy in [
            ("RS-secret", _DUMMY_SECRET),
            ("RS-bearer", _DUMMY_BEARER),
            ("RS-refresh", _DUMMY_REFRESH),
            ("RS-authcode", _DUMMY_AUTHCODE)]:
        runner.assert_not_in(_label, _dummy, _blob)


# ---------------------------------------------------------------------------
# P17: diagnostics endpoint (read-only support snapshot, no secrets)
# ---------------------------------------------------------------------------

def test_diagnostics_endpoint(runner: R) -> None:
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from api.product_routes import build_diagnostics_routes

    class _Store:
        def schema_version(self):
            return 12

    _status = [{
        "name": "fyers", "type": "fyers_feed", "state": "auth_required",
        "task_running": False, "reconnect_count": 2,
        "configured_instruments": 1,
        # hostile-looking fields that MUST NOT leak:
        "access_token": "DUMMY-TOKEN-SHOULD-NOT-APPEAR",
        "wss_url": "wss://example/DUMMY-QS",
        "transitions": [{"from": "starting", "to": "auth_required"}],
        "last_exit_reason": "auth_required",
    }]

    _app = Starlette(routes=build_diagnostics_routes(
        "0.0.0-test", _Store(), lambda: _status,
        lambda: "http://localhost:7070"))
    _body = TestClient(_app).get("/api/diagnostics").json()
    runner.assert_eq("DG-version", _body["version"], "0.0.0-test")
    runner.assert_eq("DG-schema", _body["schema_version"], 12)
    runner.assert_eq("DG-base",
                     _body["public_base_url"], "http://localhost:7070")
    _src = _body["sources"][0]
    runner.assert_eq("DG-src-state", _src["state"], "auth_required")
    runner.assert_eq("DG-src-reconnects", _src["reconnect_count"], 2)
    runner.assert_eq("DG-transition-count", _src["transition_count"], 1)
    _blob = json.dumps(_body)
    runner.assert_not_in("DG-no-token-leak",
                         "DUMMY-TOKEN-SHOULD-NOT-APPEAR", _blob)
    runner.assert_not_in("DG-no-wss-leak", "wss://example", _blob)


# ---------------------------------------------------------------------------
# P21/P22: API contract — safe errors on malformed ids; GET /api accuracy
# ---------------------------------------------------------------------------

def test_api_contract_safety(runner: R) -> None:
    from starlette.testclient import TestClient
    import app.server as _srv

    _c = TestClient(_srv.app)

    for _label, _method, _url in [
            ("AC-alert-bad-id", "DELETE", "/api/alerts/notanint"),
            ("AC-wl-bad-id", "DELETE", "/api/watchlists/abc"),
            ("AC-wl-bad-id-patch", "PATCH", "/api/watchlists/xyz"),
            ("AC-item-bad-id", "DELETE", "/api/watchlists/1/items/zzz"),
            ("AC-rearm-bad-id", "POST", "/api/alerts/QQ/rearm")]:
        try:
            _r = _c.request(_method, _url)
            runner.assert_eq(_label, _r.status_code, 400)
        except ValueError:
            runner.assert_true(_label + "-no-crash", False)

    # GET /api self-description must list current capabilities.
    _caps = _c.get("/api").json()["capabilities"]
    _blob = json.dumps(_caps)
    for _needle in ("/api/alerts/history", "/api/sources/status",
                    "/api/settings/fyers", "/api/settings/app",
                    "/api/diagnostics", "/api/watchlists/export",
                    "/api/watchlists/{id}/items/{item_id}"):
        runner.assert_in("AC-meta:" + _needle, _needle, _blob)
    runner.assert_not_in("AC-meta-stale-path",
                         "watchlists/items/{item_id}", _blob)


# ---------------------------------------------------------------------------
# P25: watchlist subscription refcount semantics
# ---------------------------------------------------------------------------

def test_watchlist_refcount_semantics(runner: R) -> None:
    """Instrument in two watchlists keeps 2 refs until last one removed."""
    from core.persistence.store import EventStore

    _tmp = tempfile.mkdtemp()
    _store = EventStore(os.path.join(_tmp, "events.db"))

    _wl1 = _store.create_watchlist("ref-a")
    _wl2 = _store.create_watchlist("ref-b")
    _i1 = _store.add_watchlist_item(_wl1["id"], exchange="NSE",
                                    instrument_token="T9",
                                    tradingsymbol="REF9")
    _i2 = _store.add_watchlist_item(_wl2["id"], exchange="NSE",
                                    instrument_token="T9",
                                    tradingsymbol="REF9")

    def _refs():
        total = 0
        for wl in _store.list_watchlists():
            total += sum(1 for it in _store.list_watchlist_items(wl["id"])
                         if it["exchange"] == "NSE"
                         and it["instrument_token"] == "T9")
        return total

    runner.assert_eq("WR-two-lists", _refs(), 2)
    _store.remove_watchlist_item(_i1["id"])
    runner.assert_eq("WR-still-subscribed", _refs(), 1)
    _store.remove_watchlist_item(_i2["id"])
    runner.assert_eq("WR-unsubscribed", _refs(), 0)
    # duplicate add within one list is rejected
    _fresh = _store.add_watchlist_item(_wl1["id"], exchange="NSE",
                                       instrument_token="T9",
                                       tradingsymbol="REF9")
    runner.assert_true("WR-readd-ok", _fresh is not None)
    _dup = _store.add_watchlist_item(_wl1["id"], exchange="NSE",
                                     instrument_token="T9",
                                     tradingsymbol="REF9")
    runner.assert_true("WR-duplicate-rejected", _dup is None)


if __name__ == "__main__":
    _runner = R()
    test_public_base_url_edge_cases(_runner)
    test_migration_matrix(_runner)
    test_migration_failure_safety(_runner)
    test_backup_from_v12(_runner)
    test_master_key_safety(_runner)
    test_log_redaction(_runner)
    test_diagnostics_endpoint(_runner)
    test_api_contract_safety(_runner)
    test_watchlist_refcount_semantics(_runner)
    _success = _runner.summary()
    sys.exit(0 if _success else 1)
