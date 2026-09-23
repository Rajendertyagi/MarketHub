#!/usr/bin/env python3
"""X/Twitter feature tests (V1 plan Rev 2, acceptance criteria).

Covers (no live X, no twitter-cli binary — fake runner + tmp store):
  XT1  cli adapter: pin check (ok + drift), typed error map, 404-retry-once,
       arg-arrays-only (no shell), timeout typing
  XT2  generic matcher: contains / not_contains / any_of + numeric unchanged
       + validation rules
  XT3  tweet.new alone produces NO alert (no rule → no alert.triggered)
  XT4  rule match → existing alert.triggered pipeline (keyword, handle,
       any_of OR, min-likes gt, one-shot vs recurring)
  XT5  poll ordering: fetch → persist → publish → cursor; cursor NOT advanced
       on mid-batch publish failure; re-poll completes without double-fire
  XT6  expired auth pauses polling (loop result, no crash); rate limit backs
       off (second cycle skipped)
  XT7  migration 22→23: fresh DB has tables; existing v22 DB migrates with
       data intact; config defaults + floor/clamp
  XT8  contract/registry: 7 tools registered, CONTRACT_VERSION bumped;
       routes factory returns the 7 endpoints

NO LIVE BROKER. Synthetic only.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import uuid

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R  # noqa: E402


def _mk_store():
    tmp = tempfile.TemporaryDirectory()
    from core.persistence.store import EventStore
    return EventStore(os.path.join(tmp.name, "t.db")), tmp


class _FakeCreds:
    """Duck-typed credential store (provider → (api_key, api_secret))."""

    def __init__(self):
        self._d: dict[str, tuple[str, str]] = {}

    def save_app_credentials(self, provider, api_key, api_secret):
        self._d[provider] = (api_key, api_secret)

    def load_app_credentials(self, provider):
        hit = self._d.get(provider)
        if hit is None:
            return None
        return {"api_key": hit[0], "api_secret": hit[1]}

    def delete_app_credentials(self, provider):
        return self._d.pop(provider, None) is not None


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _tweet(tid, text, handle="elonmusk", likes=0, retweets=0):
    return {
        "id": tid,
        "handle": handle,
        "author_name": handle.title(),
        "text": text,
        "url": f"https://x.com/{handle}/status/{tid}",
        "metrics": {"likes": likes, "retweets": retweets, "replies": 0,
                    "views": 0, "bookmarks": 0},
        "is_retweet": False,
        "posted_at": "2026-09-22T00:00:00+00:00",
        "fetched_at": "2026-09-22T00:01:00+00:00",
    }


def _feed_runner(tweets, cursor="cursor-1", fail=None):
    """Fake subprocess runner serving one feed payload (or failure)."""
    calls: list[list[str]] = []

    def _run(cmd, **kw):
        calls.append(list(cmd))
        # --version probe
        if cmd[-1] == "--version":
            return _Proc(0, "twitter-cli 0.8.5\n", "")
        if fail is not None:
            raise fail
        return _Proc(0, json.dumps({"tweets": tweets, "cursor": cursor}), "")

    _run.calls = calls
    return _run


# -- XT1: cli adapter ------------------------------------------------------------

def test_xt1_pin_check_ok_and_drift(runner: R) -> None:
    from x_twitter import cli as xcli

    def _ok(cmd, **kw):
        return _Proc(0, "twitter-cli 0.8.5\n", "")
    info = xcli.check_pin("/fake/twitter-cli", runner=_ok)
    runner.assert_eq("XT1-pin-ok", info.version, "0.8.5")
    runner.assert_eq("XT1-pin-flag", info.pinned, True)

    def _drift(cmd, **kw):
        return _Proc(0, "twitter-cli 9.9.9\n", "")
    try:
        xcli.check_pin("/fake/twitter-cli", runner=_drift)
        runner.assert_true("XT1-drift-raises", False, "expected XUnavailable")
    except xcli.XUnavailable:
        runner.assert_true("XT1-drift-raises", True, "")


def test_xt1_error_map_and_404_retry(runner: R) -> None:
    from x_twitter import cli as xcli

    # 404 surfaces not_found; retry happens ONLY when opted in (search).
    # Default is fail-fast: one attempt, no doubled CLI startup cost.
    calls: list[list[str]] = []

    def _404(cmd, **kw):
        calls.append(list(cmd))
        return _Proc(1, "", "404 not found: query id rotated")

    try:
        xcli.run_cli(["feed", "--json"], cli_path="/fake/cli", runner=_404)
        runner.assert_true("XT1-404-raises", False, "expected XNotFound")
    except xcli.XNotFound:
        runner.assert_true("XT1-404-raises", True, "")
    runner.assert_eq("XT1-404-no-retry-default", len(calls), 1)
    calls.clear()
    try:
        xcli.run_cli(["feed", "--json"], cli_path="/fake/cli", runner=_404,
                     retry_404_once=True)
        runner.assert_true("XT1-404-optin", False, "expected XNotFound")
    except xcli.XNotFound:
        runner.assert_true("XT1-404-optin", True, "")
    runner.assert_eq("XT1-404-retry-once", len(calls), 2)

    # rate limit / auth / missing binary map to typed codes
    cases = [
        (_Proc(1, "", "Rate limit exceeded (429)"), xcli.XRateLimited, "rate_limited"),
        (_Proc(1, "", "Not authenticated, login required"), xcli.XNotAuthenticated,
         "not_authenticated"),
    ]
    for i, (proc, exc_type, code) in enumerate(cases):
        try:
            xcli.run_cli(["feed", "--json"], cli_path="/fake/cli",
                         runner=lambda cmd, _p=proc, **kw: _p,
                         retry_404_once=False)
            runner.assert_true(f"XT1-map-{i}", False, "expected raise")
        except exc_type as exc:
            runner.assert_eq(f"XT1-map-{i}-code", exc.code, code)

    def _missing(cmd, **kw):
        raise FileNotFoundError("nope")

    try:
        xcli.run_cli(["feed", "--json"], cli_path="/fake/cli", runner=_missing)
        runner.assert_true("XT1-missing", False, "expected XUnavailable")
    except xcli.XUnavailable as exc:
        runner.assert_eq("XT1-missing-code", exc.code, "x_unavailable")


def test_xt1_timeout_and_arg_arrays(runner: R) -> None:
    import subprocess as _sp

    from x_twitter import cli as xcli

    seen: dict[str, object] = {}

    def _timeout(cmd, **kw):
        seen.update(kw)
        seen["cmd"] = list(cmd)
        raise _sp.TimeoutExpired(cmd, timeout=kw.get("timeout"))

    try:
        xcli.run_cli(["feed", "--json"], cli_path="/fake/cli", runner=_timeout)
        runner.assert_true("XT1-timeout", False, "expected XApiError")
    except xcli.XApiError as exc:
        runner.assert_eq("XT1-timeout-code", exc.code, "api_error")
    # arg arrays only: first element is the binary, no shell involved
    runner.assert_true("XT1-no-shell", "shell" not in seen, f"saw {sorted(seen)}")
    runner.assert_eq("XT1-binary-first", seen["cmd"][0], "/fake/cli")

    # non-string args rejected before any subprocess call
    try:
        xcli.run_cli(["feed", 123], cli_path="/fake/cli", runner=_timeout)
        runner.assert_true("XT1-args", False, "expected XApiError")
    except xcli.XApiError:
        runner.assert_true("XT1-args", True, "")


# -- XT2: generic matcher ----------------------------------------------------------

def test_xt2_string_operators(runner: R) -> None:
    from core.alerts import alert_matches, compare_values, validate_alert_definition

    runner.assert_eq("XT2-contains", compare_values("Hello NIFTY world", "contains", "nifty"), True)
    runner.assert_eq("XT2-contains-miss", compare_values("hello", "contains", "nifty"), False)
    runner.assert_eq("XT2-contains-nonstring", compare_values(123, "contains", "1"), False)
    runner.assert_eq("XT2-not-contains", compare_values("hello", "not_contains", "nifty"), True)
    runner.assert_eq("XT2-not-contains-hit",
                     compare_values("hello NIFTY", "not_contains", "nifty"), False)
    runner.assert_eq("XT2-any-of", compare_values("NIFTY rally today", "any_of",
                                                  ["banknifty", "nifty"]), True)
    runner.assert_eq("XT2-any-of-miss", compare_values("quiet day", "any_of",
                                                       ["nifty", "sensex"]), False)
    runner.assert_eq("XT2-any-of-list", compare_values(["NIFTY", "RELIANCE"], "any_of",
                                                       ["reliance"]), True)
    # numerics unchanged
    runner.assert_eq("XT2-gt", compare_values(150, "gt", 100), True)
    runner.assert_eq("XT2-gte-eq", compare_values(100, "gte", 100), True)
    runner.assert_eq("XT2-lt", compare_values(5, "lt", 10), True)

    alert = {"event_type": "tweet.new", "field_path": "text",
             "operator": "contains", "value": "@elonmusk"}
    runner.assert_eq("XT2-match", alert_matches(
        alert, {"type": "tweet.new", "data": {"text": "hi @ElonMusk"}}), True)
    runner.assert_eq("XT2-nomatch", alert_matches(
        alert, {"type": "tweet.new", "data": {"text": "nothing"}}), False)
    runner.assert_eq("XT2-type-filter", alert_matches(
        {**alert, "event_type": "other"},
        {"type": "tweet.new", "data": {"text": "hi @elonmusk"}}), False)

    # validation: any_of needs non-empty string list; contains needs string
    for bad in ("notalist", [], ["ok", 123], ["ok"] * 33):
        try:
            validate_alert_definition("c", "twitter", "text", "any_of", bad,
                                      None, "tweet.new", False)
            runner.assert_true(f"XT2-anyof-reject-{bad!r}", False, "expected raise")
        except Exception:
            runner.assert_true(f"XT2-anyof-reject-{bad!r}", True, "")
    try:
        validate_alert_definition("c", "twitter", "text", "contains", 123,
                                  None, "tweet.new", False)
        runner.assert_true("XT2-contains-reject", False, "expected raise")
    except Exception:
        runner.assert_true("XT2-contains-reject", True, "")
    # legit any_of validates
    validate_alert_definition("c", "twitter", "text", "any_of", ["a", "b"],
                              None, "tweet.new", False)


# -- XT3: tweet.new alone → no alert -----------------------------------------------

async def test_xt3_tweet_without_rule_no_alert(runner: R) -> None:
    from core import events as core_events
    from core.alerts import AlertEvaluator

    store, tmp = _mk_store()
    store.register_consumer("bot")
    evaluator = AlertEvaluator(store=store, subscription_bus=None)
    core_events.configure_alert_evaluator(evaluator.evaluate)
    try:
        await core_events.publish_event(
            event_type="tweet.new", source="twitter",
            data={"text": "markets up", "author": "@elonmusk",
                  "metrics": {"likes": 5}},
            persistent=True, store=store, bus=None)
    finally:
        core_events.configure_alert_evaluator(None)
    triggers = [e for e in store.list_pending(50) if e["type"] == "alert.triggered"]
    runner.assert_eq("XT3-no-rule-no-alert", len(triggers), 0)


# -- XT4: rule match → existing pipeline --------------------------------------------

async def test_xt4_rule_match_fires(runner: R) -> None:
    from core import events as core_events
    from core.alerts import AlertEvaluator

    store, tmp = _mk_store()
    store.register_consumer("bot")
    store.create_alert(alert_id=uuid.uuid4().hex, consumer_id="bot", name="kw",
                       source="twitter", event_type="tweet.new",
                       field_path="text", operator="contains",
                       value="NIFTY", one_shot=False)
    store.create_alert(alert_id=uuid.uuid4().hex, consumer_id="bot", name="likes",
                       source="twitter", event_type="tweet.new",
                       field_path="metrics.likes", operator="gte",
                       value=100, one_shot=True)
    evaluator = AlertEvaluator(store=store, subscription_bus=None)
    core_events.configure_alert_evaluator(evaluator.evaluate)
    try:
        await core_events.publish_event(
            event_type="tweet.new", source="twitter",
            data={"text": "NIFTY breaks out", "author": "@a",
                  "metrics": {"likes": 500}},
            persistent=True, store=store, bus=None)
        await core_events.publish_event(
            event_type="tweet.new", source="twitter",
            data={"text": "quiet day", "author": "@b",
                  "metrics": {"likes": 1}},
            persistent=True, store=store, bus=None)
    finally:
        core_events.configure_alert_evaluator(None)
    triggers = [e for e in store.list_pending(50) if e["type"] == "alert.triggered"]
    # first tweet matches BOTH rules (keyword + min-likes); second matches none
    runner.assert_eq("XT4-two-fires", len(triggers), 2)
    inbox = [e for e in store.list_relevant_events("bot")
             if e["type"] == "alert.triggered"]
    runner.assert_eq("XT4-owner-routed", len(inbox), 2)
    # one-shot likes rule auto-disabled after firing
    remaining = store.list_alerts_by_source_enabled("twitter")
    runner.assert_eq("XT4-oneshot-off", len(remaining), 1)
    runner.assert_eq("XT4-recurring-stays", remaining[0]["name"], "kw")


# -- XT5: poll ordering + cursor safety ----------------------------------------------

async def test_xt5_poll_persist_publish_cursor(runner: R) -> None:
    from x_twitter.service import XTwitterService

    store, tmp = _mk_store()
    creds = _FakeCreds()
    svc = XTwitterService(store=store, cred_store=creds, bus=None,
                          runner=_feed_runner([_tweet("1", "NIFTY up"),
                                               _tweet("2", "quiet")]))
    svc.save_credentials("tok", "ct0")
    store.set_x_config(enabled=True, poll_interval_seconds=60, default_limit=20)
    out = await svc.poll_once()
    runner.assert_eq("XT5-ok", out["status"], "ok")
    runner.assert_eq("XT5-new", out["new"], 2)
    runner.assert_eq("XT5-cached", len(store.list_x_tweets(limit=10)), 2)
    runner.assert_eq("XT5-cursor", store.get_source_state("twitter", "feed_cursor"),
                     "cursor-1")
    stored = [e for e in store.list_pending(50) if e["type"] == "tweet.new"]
    runner.assert_eq("XT5-events", len(stored), 2)


async def test_xt5_cursor_not_advanced_on_publish_failure(runner: R) -> None:
    """Mid-batch publish failure → cursor stays; re-poll completes the
    remainder without double-firing already-persisted tweets."""
    from core import events as core_events
    from x_twitter.service import XTwitterService

    store, tmp = _mk_store()
    creds = _FakeCreds()
    tweets = [_tweet("1", "first"), _tweet("2", "second"), _tweet("3", "third")]
    svc = XTwitterService(store=store, cred_store=creds, bus=None,
                          runner=_feed_runner(tweets, cursor="cursor-9"))
    svc.save_credentials("tok", "ct0")
    store.set_x_config(enabled=True, poll_interval_seconds=60, default_limit=20)

    real_publish = core_events.publish_event
    calls = {"n": 0}

    async def _flaky(**kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom mid-batch")
        return await real_publish(**kw)

    core_events.publish_event = _flaky
    try:
        out = await svc.poll_once()
    finally:
        core_events.publish_event = real_publish
    runner.assert_eq("XT5-fail-status", out["status"], "error")
    runner.assert_eq("XT5-fail-kind", out["error"], "publish_failed")
    # cursor NOT advanced despite 3 persisted rows
    runner.assert_eq("XT5-cursor-held", store.get_source_state("twitter", "feed_cursor"),
                     None)
    # exactly the first tweet published before the failure
    stored = [e for e in store.list_pending(50) if e["type"] == "tweet.new"]
    runner.assert_eq("XT5-partial", len(stored), 1)

    # re-poll: same window re-fetched; persisted rows are no-ops, only the
    # remainder publishes (no double-fire of tweet 1)
    out2 = await svc.poll_once()
    runner.assert_eq("XT5-retry-ok", out2["status"], "ok")
    runner.assert_eq("XT5-retry-new", out2["new"], 0)
    stored2 = [e for e in store.list_pending(50) if e["type"] == "tweet.new"]
    runner.assert_eq("XT5-no-double-fire", len(stored2), 1)
    # cursor still not advanced (nothing new completed) — next fresh window
    # with a new tweet completes and advances
    svc2 = XTwitterService(store=store, cred_store=creds, bus=None,
                           runner=_feed_runner(
                               tweets + [_tweet("4", "fourth")], cursor="cursor-10"))
    out3 = await svc2.poll_once()
    runner.assert_eq("XT5-complete", out3["status"], "ok")
    runner.assert_eq("XT5-complete-new", out3["new"], 1)
    runner.assert_eq("XT5-cursor-final",
                     store.get_source_state("twitter", "feed_cursor"), "cursor-10")
    stored3 = [e for e in store.list_pending(50) if e["type"] == "tweet.new"]
    runner.assert_eq("XT5-total", len(stored3), 2)


async def test_xt5_poll_dedupe_no_refire(runner: R) -> None:
    from x_twitter.service import XTwitterService

    store, tmp = _mk_store()
    creds = _FakeCreds()
    payload = [_tweet("1", "hello")]
    svc = XTwitterService(store=store, cred_store=creds, bus=None,
                          runner=_feed_runner(payload, cursor="c1"))
    svc.save_credentials("tok", "ct0")
    store.set_x_config(enabled=True, poll_interval_seconds=60, default_limit=20)
    await svc.poll_once()
    out = await svc.poll_once()
    runner.assert_eq("XT5-dedupe-new", out["new"], 0)
    stored = [e for e in store.list_pending(50) if e["type"] == "tweet.new"]
    runner.assert_eq("XT5-dedupe-events", len(stored), 1)


# -- XT6: expired auth pauses; rate limit backs off -----------------------------------

async def test_xt6_expired_pauses_and_ratelimit_backoff(runner: R) -> None:
    from x_twitter import cli as xcli
    from x_twitter.service import XTwitterService

    store, tmp = _mk_store()
    creds = _FakeCreds()

    def _expired(cmd, **kw):
        if cmd[-1] == "--version":
            return _Proc(0, "twitter-cli 0.8.5\n", "")
        return _Proc(1, "", "Not authenticated, session expired")

    svc = XTwitterService(store=store, cred_store=creds, bus=None, runner=_expired)
    svc.save_credentials("tok", "ct0")
    store.set_x_config(enabled=True, poll_interval_seconds=60, default_limit=20)
    out = await svc.poll_once()
    runner.assert_eq("XT6-expired", out["error"], "expired/invalid")
    runner.assert_eq("XT6-state", svc.auth_state()["state"], "expired/invalid")
    runner.assert_eq("XT6-cached-intact", len(store.list_x_tweets(limit=5)), 0)
    # loop stays alive: a later good cycle recovers
    svc._runner = _feed_runner([_tweet("9", "back")], cursor="c9")
    out2 = await svc.poll_once()
    runner.assert_eq("XT6-recover", out2["status"], "ok")
    runner.assert_eq("XT6-recover-state", svc.auth_state()["state"], "authenticated")

    def _limited(cmd, **kw):
        if cmd[-1] == "--version":
            return _Proc(0, "twitter-cli 0.8.5\n", "")
        return _Proc(1, "", "Rate limit exceeded (429)")

    svc._runner = _limited
    out3 = await svc.poll_once()
    runner.assert_eq("XT6-limited", out3["error"], "rate_limited")
    out4 = await svc.poll_once()
    runner.assert_eq("XT6-backoff-skip", out4["status"], "skipped")
    runner.assert_eq("XT6-backoff-reason", out4["reason"], "backoff")
    assert isinstance(xcli.XRateLimited("x").code, str)


# -- XT7: migration ---------------------------------------------------------------------

def test_xt7_fresh_db_has_x_tables(runner: R) -> None:
    from core.persistence.modules.schema import SCHEMA_VERSION

    store, tmp = _mk_store()
    runner.assert_eq("XT7-version", store.schema_version(), SCHEMA_VERSION)
    runner.assert_true("XT7-v23", SCHEMA_VERSION >= 23, f"got {SCHEMA_VERSION}")
    conn = sqlite3.connect(store.db_path)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        conn.close()
    runner.assert_true("XT7-tweets", "x_tweets" in tables, f"missing: {sorted(tables)}")
    runner.assert_true("XT7-config", "x_config" in tables, f"missing: {sorted(tables)}")
    cfg = store.get_x_config()
    runner.assert_eq("XT7-default-disabled", cfg["enabled"], False)
    runner.assert_eq("XT7-default-limit", cfg["default_limit"], 20)


def test_xt7_migrate_v22_to_v23_preserves_data(runner: R) -> None:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA user_version = 22")
            conn.execute("""CREATE TABLE t_keep (id INTEGER PRIMARY KEY, v TEXT)""")
            conn.execute("INSERT INTO t_keep (v) VALUES ('keepme')")
            conn.commit()
            from core.persistence.modules.x_twitter import migrate_v22_to_v23
            migrate_v22_to_v23(conn)
            ver = conn.execute("PRAGMA user_version").fetchone()[0]
            kept = conn.execute("SELECT v FROM t_keep").fetchone()[0]
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        finally:
            conn.close()
        runner.assert_eq("XT7-mig-version", ver, 23)
        runner.assert_eq("XT7-mig-kept", kept, "keepme")
        runner.assert_true("XT7-mig-tables",
                           {"x_tweets", "x_config"} <= tables, f"{sorted(tables)}")
    finally:
        os.unlink(path)
    # store facade floor/clamp
    store, tmp = _mk_store()
    cfg = store.set_x_config(poll_interval_seconds=5, default_limit=99,
                             retention_days=7, enabled=True)
    runner.assert_eq("XT7-floor", cfg["poll_interval_seconds"], 60)
    runner.assert_eq("XT7-clamp", cfg["default_limit"], 20)
    runner.assert_eq("XT7-ret", cfg["retention_days"], 7)


def test_xt7_save_and_prune(runner: R) -> None:
    store, tmp = _mk_store()
    rows = [_tweet("1", "a"), _tweet("2", "b")]
    new_ids = store.save_x_tweets(rows)
    runner.assert_eq("XT7-save-new", sorted(new_ids), ["1", "2"])
    again = store.save_x_tweets(rows)
    runner.assert_eq("XT7-save-dedupe", again, [])
    got = store.get_x_tweet("1")
    runner.assert_eq("XT7-get", got["text"], "a")
    runner.assert_eq("XT7-metrics", got["metrics"]["likes"], 0)
    listed = store.list_x_tweets(limit=5)
    runner.assert_eq("XT7-list", len(listed), 2)
    pruned = store.prune_x_tweets(3650)
    runner.assert_eq("XT7-prune-fresh", pruned, 0)
    old = _tweet("9", "old")
    old["posted_at"] = "2000-01-01T00:00:00+00:00"
    store.save_x_tweets([old])
    pruned2 = store.prune_x_tweets(30)
    runner.assert_eq("XT7-prune-old", pruned2, 1)
    runner.assert_eq("XT7-prune-gone", store.get_x_tweet("9"), None)


# -- XT8: contract / registry / routes -----------------------------------------------------

def test_xt8_contract_registry_routes(runner: R) -> None:
    from mcp_server import contract as C
    from mcp_server import registry as R_

    runner.assert_eq("XT8-contract", C.CONTRACT_VERSION, "2.7.0")
    expected = ["twitter_feed", "twitter_search", "twitter_tweet",
                "twitter_article", "twitter_bookmarks", "twitter_user_posts",
                "twitter_user_profile"]
    for name in expected:
        const = getattr(C, "TOOL_X_" + name.upper().replace("TWITTER_", ""))
        runner.assert_eq(f"XT8-const-{name}", const, name)
        tool = R_.get_by_name(name)
        runner.assert_true(f"XT8-reg-{name}", tool is not None, "missing")
    names = [t["name"] for t in R_.TOOLS]
    runner.assert_eq("XT8-unique", len(names), len(set(names)))

    from api.x_routes import build_x_routes

    routes = build_x_routes(object())
    runner.assert_eq("XT8-routes", len(routes), 8)
    paths = sorted({r.path for r in routes})
    for p in ("/api/settings/x", "/api/x/config", "/api/x/status",
              "/api/x/test", "/api/x/tweets"):
        runner.assert_true(f"XT8-path-{p}", p in paths, f"got {paths}")

    # tool registration smoke (fake mcp records decorators)
    from mcp_server.tools.twitter_tools import register_twitter_tools

    seen: list[str] = []

    class _FakeMcp:
        def tool(self, name=None, description=None):
            def _deco(fn):
                seen.append(name)
                return fn
            return _deco

    class _Svc:
        pass

    register_twitter_tools(_FakeMcp(), type("S", (), {"x_twitter": _Svc()})())
    runner.assert_eq("XT8-tools-registered", len(seen), 7)


# -- XT9: CLI discovery / path resolution ----------------------------------------

def test_xt9_explicit_configured_path_wins(runner: R) -> None:
    """An explicitly configured cli_path always takes precedence."""
    from x_twitter.service import XTwitterService

    store, tmp = _mk_store()
    creds = _FakeCreds()
    svc = XTwitterService(store=store, cred_store=creds, bus=None)
    svc.save_credentials("tok", "ct0")
    store.set_x_config(enabled=True, cli_path="/configured/path/twitter")

    path = svc._cli_path()
    runner.assert_eq("XT9-configured-wins", path, "/configured/path/twitter")


def test_xt9_which_twitter_cli_preferred(runner: R) -> None:
    """shutil.which('twitter-cli') is checked before shutil.which('twitter')."""
    import shutil

    from x_twitter import cli as xcli

    # Mock both which calls: twitter-cli found first, twitter also present
    def mock_which(name):
        return {"twitter-cli": "/usr/local/bin/twitter-cli",
                "twitter": "/usr/local/bin/twitter"}[name]

    orig = shutil.which
    try:
        shutil.which = mock_which
        path = xcli.default_cli_path()
        runner.assert_eq("XT9-twitter-cli-pref", path, "/usr/local/bin/twitter-cli")
    finally:
        shutil.which = orig


def test_xt9_falls_back_to_twitter_name(runner: R) -> None:
    """When twitter-cli is absent, twitter is discovered."""
    import shutil

    from x_twitter import cli as xcli

    def mock_which(name):
        return {"twitter-cli": None,
                "twitter": "/opt/tools/twitter"}[name]

    orig = shutil.which
    try:
        shutil.which = mock_which
        path = xcli.default_cli_path()
        runner.assert_eq("XT9-twitter-fallback", path, "/opt/tools/twitter")
    finally:
        shutil.which = orig


def test_xt9_windows_exe_resolution(runner: R) -> None:
    """Windows: shutil.which resolves .exe without the caller needing to append it."""
    import shutil

    from x_twitter import cli as xcli

    def mock_which(name):
        if name == "twitter-cli":
            return None
        return r"C:\Tools\twitter.EXE"

    orig = shutil.which
    try:
        shutil.which = mock_which
        path = xcli.default_cli_path()
        runner.assert_eq("XT9-win-resolve", path, r"C:\Tools\twitter.EXE")
    finally:
        shutil.which = orig


def test_xt9_no_executable_returns_local_fallback(runner: R) -> None:
    """When nothing is found, falls back to local expected path (for error msg)."""
    import shutil

    from x_twitter import cli as xcli

    orig = shutil.which
    try:
        shutil.which = lambda name: None
        path = xcli.default_cli_path()
        # Should return the local fallback, not crash
        runner.assert_true("XT9-no-exe-path", "twitter-cli" in path)
    finally:
        shutil.which = orig


def test_xt9_version_drift_still_rejected(runner: R) -> None:
    """Version drift detection is unchanged by discovery fix."""
    from x_twitter import cli as xcli

    def _drift9(cmd, **kw):
        return _Proc(0, "twitter 9.9.9\n", "")
    try:
        xcli.check_pin("/fake/twitter", runner=_drift9)
        runner.assert_true("XT9-drift-raises", False, "expected XUnavailable")
    except xcli.XUnavailable:
        runner.assert_true("XT9-drift-raises", True, "")


def test_xt9_ping_version_accepted(runner: R) -> None:
    """Pinned version 0.8.5 accepted regardless of executable name prefix."""
    from x_twitter import cli as xcli

    for output in ("twitter-cli 0.8.5\n", "twitter, version 0.8.5\n", "0.8.5"):
        def _proc(cmd, output=output, **kw):
            return _Proc(0, output, "")
        info = xcli.check_pin("/fake/bin", runner=_proc)
        runner.assert_eq(f"XT9-ping-{output!r}", info.version, "0.8.5")
        runner.assert_true(f"XT9-ping-{output!r}", info.pinned)


# -- XT10: search/article flags, envelope errors, durable status, test link ----

def _capture_runner(payload=None, rc=0, stderr=""):
    """Fake runner returning one payload (or rc/stderr) while recording argv."""
    calls: list[list[str]] = []

    def _run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[-1] == "--version":
            return _Proc(0, "twitter-cli 0.8.5\n", "")
        body = payload if isinstance(payload, str) else json.dumps(
            payload if payload is not None else {})
        return _Proc(rc, body, stderr)

    _run.calls = calls
    return _run


def test_xt10_utf8_decode_contract(runner: R) -> None:
    """Subprocess output must decode as UTF-8 (never locale cp1252):
    tweet bytes are arbitrary Unicode and must not crash the reader."""
    from x_twitter import cli as xcli

    seen: dict[str, object] = {}

    def _ok(cmd, **kw):
        seen.update(kw)
        if cmd[-1] == "--version":
            return _Proc(0, "twitter-cli 0.8.5\n", "")
        return _Proc(0, '{"ok": true}', "")

    xcli.run_cli(["status", "--json"], cli_path="/fake/cli", runner=_ok)
    runner.assert_eq("XT10-enc-run", seen.get("encoding"), "utf-8")
    runner.assert_eq("XT10-enc-err", seen.get("errors"), "replace")
    seen.clear()
    xcli.check_pin("/fake/cli", runner=_ok)
    runner.assert_eq("XT10-enc-pin", seen.get("encoding"), "utf-8")
    runner.assert_eq("XT10-enc-pin-err", seen.get("errors"), "replace")


def test_xt10_search_uses_type_flag(runner: R) -> None:
    """twitter_search must emit --type (valid) and never --tab (rejected)."""
    from x_twitter import cli as xcli

    run = _capture_runner({"tweets": [], "cursor": None})
    xcli.search(cli_path="/fake/cli", query="NIFTY", limit=5, runner=run)
    argv = run.calls[-1]
    runner.assert_true("XT10-search-type", "--type" in argv, f"got {argv}")
    runner.assert_true("XT10-no-tab",
                       not any(a == "--tab" for a in argv), f"got {argv}")
    runner.assert_true("XT10-search-fulltext", "--full-text" in argv,
                       f"got {argv}")
    runner.assert_true("XT10-search-json", "--json" in argv, f"got {argv}")
    # unknown tab values fall back to latest instead of reaching the CLI
    run2 = _capture_runner({"tweets": []})
    xcli.search(cli_path="/fake/cli", query="NIFTY", tab="bogus", runner=run2)
    runner.assert_true("XT10-search-fallback",
                       "latest" in run2.calls[-1], f"got {run2.calls[-1]}")


def test_xt10_article_uses_json(runner: R) -> None:
    """twitter_article must emit --json (--markdown is exclusive, unparsed)."""
    from x_twitter import cli as xcli

    run = _capture_runner({"ok": True, "article": {"id": "1"}})
    out = xcli.get_article(cli_path="/fake/cli", url_or_id="123",
                           runner=run)
    argv = run.calls[-1]
    runner.assert_true("XT10-article-json", "--json" in argv, f"got {argv}")
    runner.assert_true("XT10-no-markdown",
                       "--markdown" not in argv and "-m" not in argv,
                       f"got {argv}")
    runner.assert_eq("XT10-article-payload", out["article"]["id"], "1")


def test_xt10_stdout_envelope_maps_typed(runner: R) -> None:
    """Structured stdout failure envelopes map to typed errors first."""
    from x_twitter import cli as xcli

    cases = [
        ({"ok": False, "error": {"code": "not_authenticated",
                                 "message": "expired session"}},
         xcli.XNotAuthenticated, "not_authenticated"),
        ({"ok": False, "error": {"code": "rate_limited", "message": "slow"}},
         xcli.XRateLimited, "rate_limited"),
        ({"ok": False, "error": {"code": "mystery", "message": "weird"}},
         xcli.XApiError, "mystery"),
    ]
    for i, (payload, exc_type, code) in enumerate(cases):
        run = _capture_runner(payload, rc=1, stderr="some log line")
        try:
            xcli.run_cli(["search", "x", "--json"], cli_path="/fake/cli",
                         runner=run, retry_404_once=False)
            runner.assert_true(f"XT10-env-{i}", False, "expected raise")
        except exc_type as exc:
            runner.assert_eq(f"XT10-env-{i}-code", exc.code, code)
    # envelope not_found still retries exactly once
    calls: list[list[str]] = []

    def _404env(cmd, **kw):
        calls.append(list(cmd))
        return _Proc(1, json.dumps({"ok": False, "error": {
            "code": "not_found", "message": "gone"}}), "")

    try:
        xcli.run_cli(["user", "h", "--json"], cli_path="/fake/cli",
                     runner=_404env, retry_404_once=True)
        runner.assert_true("XT10-env404", False, "expected XNotFound")
    except xcli.XNotFound:
        runner.assert_true("XT10-env404", True, "")
    runner.assert_eq("XT10-env404-retry", len(calls), 2)
    # non-envelope stdout keeps the legacy stderr path
    run = _capture_runner("not json at all", rc=1,
                          stderr="Rate limit exceeded (429)")
    try:
        xcli.run_cli(["feed", "--json"], cli_path="/fake/cli", runner=run,
                     retry_404_once=False)
        runner.assert_true("XT10-legacy", False, "expected XRateLimited")
    except xcli.XRateLimited:
        runner.assert_true("XT10-legacy", True, "")


def test_xt10_restart_keeps_configured(runner: R) -> None:
    """Fresh service instance (post-restart) with stored cookies reports
    configured — never not_configured."""
    from x_twitter.service import XTwitterService

    store, tmp = _mk_store()
    creds = _FakeCreds()
    creds.save_app_credentials("x", "tok", "ct0")
    fresh = XTwitterService(store=store, cred_store=creds, bus=None)
    runner.assert_eq("XT10-restart-state", fresh.auth_state()["state"],
                     "configured")
    runner.assert_true("XT10-restart-creds", fresh.has_credentials())
    # no cookies anywhere -> still honestly not_configured
    empty = XTwitterService(store=store, cred_store=_FakeCreds(), bus=None)
    runner.assert_eq("XT10-empty-state", empty.auth_state()["state"],
                     "not_configured")


async def test_xt10_connection_test_matrix(runner: R) -> None:
    """test_connection maps outcomes and stays side-effect free."""
    from x_twitter.service import XTwitterService

    async def _case(name, runner_fn, want_state, want_status="error"):
        store, tmp = _mk_store()
        creds = _FakeCreds()
        svc = XTwitterService(store=store, cred_store=creds, bus=None,
                              runner=runner_fn)
        svc.save_credentials("tok", "ct0")
        out = await svc.test_connection()
        runner.assert_eq(f"XT10-tc-{name}", out["state"], want_state)
        runner.assert_eq(f"XT10-tc-{name}-status", out["status"],
                         want_status)
        runner.assert_eq(f"XT10-tc-{name}-auth",
                         svc.auth_state()["state"], want_state
                         if want_state != "unavailable" else "configured")
        runner.assert_eq(f"XT10-tc-{name}-tweets",
                         len(store.list_x_tweets(limit=5)), 0)
        runner.assert_true(f"XT10-tc-{name}-nocursor",
                           store.get_source_state("twitter",
                                                  "feed_cursor") is None)
        return svc

    ok = _capture_runner({"ok": True, "user": "me"})
    await _case("ok", ok, "authenticated", "ok")

    def _expired(cmd, **kw):
        if cmd[-1] == "--version":
            return _Proc(0, "twitter-cli 0.8.5\n", "")
        return _Proc(1, json.dumps({"ok": False, "error": {
            "code": "not_authenticated", "message": "expired"}}), "")

    await _case("expired", _expired, "expired/invalid")

    def _limited(cmd, **kw):
        if cmd[-1] == "--version":
            return _Proc(0, "twitter-cli 0.8.5\n", "")
        return _Proc(1, "", "Rate limit exceeded (429)")

    await _case("limited", _limited, "rate_limited")

    def _nobin(cmd, **kw):
        raise FileNotFoundError("nope")

    await _case("nobin", _nobin, "unavailable")

    # no cookies -> no CLI call attempted (fresh double, not the shared one)
    nocreds_run = _capture_runner({"ok": True})
    store, tmp = _mk_store()
    bare = XTwitterService(store=store, cred_store=_FakeCreds(), bus=None,
                           runner=nocreds_run)
    out = await bare.test_connection()
    runner.assert_eq("XT10-tc-nocreds", out["state"], "not_configured")
    runner.assert_eq("XT10-tc-nocreds-calls", len(nocreds_run.calls), 0)


def test_xt11_search_404_is_unavailable(runner: R) -> None:
    """A search 404 is the upstream outage, never 'no results'."""
    from x_twitter import cli as xcli

    calls: list[list[str]] = []

    def _404env(cmd, **kw):
        calls.append(list(cmd))
        return _Proc(1, json.dumps({"ok": False, "error": {
            "code": "not_found",
            "message": "Twitter API error (HTTP 404): x"}}), "")

    try:
        xcli.search(cli_path="/fake/cli", query="Nifty", runner=_404env)
        runner.assert_true("XT11-search404", False, "expected XApiError")
    except xcli.XApiError as exc:
        runner.assert_eq("XT11-search404-code", exc.code,
                         "search_unavailable")
        runner.assert_true("XT11-search404-msg", "0.8.5" in str(exc),
                           str(exc)[:120])
    # queryId-rotation retry still happens first (2 CLI attempts total)
    runner.assert_eq("XT11-search404-retry", len(calls), 2)


def test_xt11_invalid_input_and_ok_false(runner: R) -> None:
    """invalid_input envelopes map explicitly; ok:false on rc==0 still raises."""
    from x_twitter import cli as xcli

    run = _capture_runner({"ok": False, "error": {
        "code": "invalid_input", "message": "bad query"}}, rc=1)
    try:
        xcli.run_cli(["search", "x", "--json"], cli_path="/fake/cli",
                     runner=run, retry_404_once=False)
        runner.assert_true("XT11-inv", False, "expected XApiError")
    except xcli.XApiError as exc:
        runner.assert_eq("XT11-inv-code", exc.code, "invalid_input")

    run0 = _capture_runner({"ok": False, "error": {
        "code": "not_authenticated", "message": "expired"}}, rc=0)
    try:
        xcli.run_cli(["user", "h", "--json"], cli_path="/fake/cli",
                     runner=run0, retry_404_once=False)
        runner.assert_true("XT11-okfalse", False, "expected raise")
    except xcli.XNotAuthenticated:
        runner.assert_true("XT11-okfalse", True, "")


async def test_xt11_tool_typed_payloads(runner: R) -> None:
    """Provider outcomes return as data; crashes + bad input still raise."""
    from core.errors import ValidationError
    from mcp_server.tools.twitter_tools import register_twitter_tools
    from x_twitter import cli as xcli

    fns: dict[str, object] = {}

    class _FakeMcp:
        def tool(self, name=None, description=None):
            def _deco(fn):
                fns[name] = fn
                return fn
            return _deco

    class _Boom:
        async def search(self, *a, **k):
            raise xcli.XNotAuthenticated("expired")
        async def get_article(self, *a, **k):
            raise xcli.XNotFound("gone")
        async def feed(self, *a, **k):
            raise xcli.XRateLimited("slow")
        async def get_tweet(self, *a, **k):
            raise RuntimeError("bug")
        async def get_bookmarks(self, *a, **k):
            raise xcli.XApiError("down", code="api_error")
        async def get_user_posts(self, *a, **k):
            raise xcli.XNotFound("no such handle")
        async def get_user_profile(self, *a, **k):
            return {"status": "ok", "profile": {}}

    register_twitter_tools(_FakeMcp(),
                           type("S", (), {"x_twitter": _Boom()})())
    out = await fns["twitter_search"]("Nifty")
    runner.assert_eq("XT11-t-search", out["code"], "not_authenticated")
    runner.assert_eq("XT11-t-search-status", out["status"], "error")
    out = await fns["twitter_article"]("1")
    runner.assert_eq("XT11-t-article", out["code"], "not_found")
    out = await fns["twitter_feed"]()
    runner.assert_eq("XT11-t-feed", out["code"], "rate_limited")
    out = await fns["twitter_bookmarks"]()
    runner.assert_eq("XT11-t-marks", out["code"], "api_error")
    out = await fns["twitter_user_posts"]("ghost")
    runner.assert_eq("XT11-t-posts", out["code"], "not_found")
    out = await fns["twitter_user_profile"]("me")
    runner.assert_eq("XT11-t-profile", out["status"], "ok")
    try:
        await fns["twitter_tweet"]("x")
        runner.assert_true("XT11-t-crash", False, "expected raise")
    except RuntimeError:
        runner.assert_true("XT11-t-crash", True, "")
    try:
        await fns["twitter_search"]("  ")
        runner.assert_true("XT11-t-input", False, "expected raise")
    except ValidationError:
        runner.assert_true("XT11-t-input", True, "")


# -- XT12: single-guard MCP boundary contract -------------------------------------

_XT12_TOOLS = ["twitter_feed", "twitter_search", "twitter_tweet",
               "twitter_article", "twitter_bookmarks", "twitter_user_posts",
               "twitter_user_profile"]

_XT12_CODES = ["not_authenticated", "not_found", "rate_limited",
               "x_unavailable", "api_error", "search_unavailable",
               "invalid_input"]

_XT12_ARGS = {"twitter_feed": (), "twitter_search": ("q",),
              "twitter_tweet": ("1",), "twitter_article": ("1",),
              "twitter_bookmarks": (), "twitter_user_posts": ("h",),
              "twitter_user_profile": ("h",)}


def _xt12_register(exc_factory):
    """Register tools over a service whose every method raises exc_factory()."""
    from mcp_server.tools.twitter_tools import register_twitter_tools
    fns: dict[str, object] = {}

    class _FakeMcp:
        def tool(self, name=None, description=None):
            def _deco(fn):
                fns[name] = fn
                return fn
            return _deco

    class _Svc:
        def __getattr__(self, name):
            async def _boom(*a, **k):
                raise exc_factory()
            return _boom

    register_twitter_tools(_FakeMcp(),
                           type("S", (), {"x_twitter": _Svc()})())
    return fns


def test_xt12_retry_log_names_operation(runner: R) -> None:
    """The 404-retry log line names the operation (self-diagnosing logs)."""
    import logging

    from x_twitter import cli as xcli

    messages: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())

    logger = logging.getLogger("x_twitter.cli")
    handler = _Capture()
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        def _404(cmd, **kw):
            return _Proc(1, "", "404 gone")

        try:
            xcli.search(cli_path="/fake/cli", query="q", runner=_404)
            runner.assert_true("XT12-retrylog", False, "expected XApiError")
        except xcli.XApiError:
            runner.assert_true("XT12-retrylog", True, "")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    runner.assert_true("XT12-retrylog-op",
                       any("'search'" in m for m in messages),
                       f"got {messages}")


async def test_xt12_all_tools_all_codes(runner: R) -> None:
    """Every tool × every approved code → identical stable payload shape."""
    from x_twitter import cli as xcli

    for code in _XT12_CODES:
        def _mk(c=code):
            return xcli.XApiError(f"probe {c}", code=c)
        fns = _xt12_register(_mk)
        for tool in _XT12_TOOLS:
            out = await fns[tool](*_XT12_ARGS[tool])
            runner.assert_eq(f"XT12-{tool}-{code}", out,
                             {"status": "error", "code": code,
                              "reason": f"probe {code}"})


async def test_xt12_alien_code_still_raises(runner: R) -> None:
    """Unknown .code values (any case) never map — they raise per tool."""

    class _Alien(Exception):
        def __init__(self, code):
            super().__init__(f"alien {code}")
            self.code = code

    for alien in ("weird", "NOT_FOUND", ""):
        fns = _xt12_register(lambda c=alien: _Alien(c))
        for tool in _XT12_TOOLS:
            try:
                await fns[tool](*_XT12_ARGS[tool])
                runner.assert_true(f"XT12-alien-{tool}-{alien!r}", False,
                                   "expected raise")
            except _Alien:
                runner.assert_true(f"XT12-alien-{tool}-{alien!r}", True, "")


async def test_xt12_validation_and_crash_raise(runner: R) -> None:
    """Input-contract errors and crashes stay raised on every tool."""
    from core.errors import StorageError, ValidationError
    from mcp_server.tools.twitter_tools import register_twitter_tools

    fns: dict[str, object] = {}

    class _FakeMcp:
        def tool(self, name=None, description=None):
            def _deco(fn):
                fns[name] = fn
                return fn
            return _deco

    class _Crash:
        def __getattr__(self, name):
            async def _boom(*a, **k):
                raise RuntimeError("bug")
            return _boom

    register_twitter_tools(_FakeMcp(),
                           type("S", (), {"x_twitter": _Crash()})())
    bad_args = {"twitter_feed": (0,), "twitter_search": ("  ",),
                "twitter_tweet": ("",), "twitter_article": ("",),
                "twitter_bookmarks": (99,), "twitter_user_posts": ("",),
                "twitter_user_profile": ("",)}
    for tool in _XT12_TOOLS:
        try:
            await fns[tool](*bad_args[tool])
            runner.assert_true(f"XT12-input-{tool}", False, "expected raise")
        except ValidationError:
            runner.assert_true(f"XT12-input-{tool}", True, "")
    # missing service -> StorageError on every tool
    fns2: dict[str, object] = {}

    class _FakeMcp2:
        def tool(self, name=None, description=None):
            def _deco(fn):
                fns2[name] = fn
                return fn
            return _deco

    register_twitter_tools(_FakeMcp2(),
                           type("S", (), {"x_twitter": None})())
    for tool in _XT12_TOOLS:
        try:
            await fns2[tool](*_XT12_ARGS[tool])
            runner.assert_true(f"XT12-nonsvc-{tool}", False,
                               "expected raise")
        except StorageError:
            runner.assert_true(f"XT12-nonsvc-{tool}", True, "")


def test_xt12_reason_sanitized(runner: R) -> None:
    """Reasons carry facts, never secrets/paths/params/dumps."""
    from mcp_server.tools.twitter_tools import _safe_reason

    token = "A" * 20 + "B" * 20  # 40-char opaque blob
    assert len(token) == 40
    cases = [
        (f"cookie {token} rejected",
         "cookie <redacted> rejected"),
        (r"twitter-cli not found at C:\Tools\twitter.EXE",
         "twitter-cli not found at twitter.EXE"),
        ("bad route /srv/data/x/events.db locked",
         "bad route events.db locked"),
        ("see https://x.com/i/article/123?token=abc&x=1 for detail",
         "see https://x.com/i/article/123 for detail"),
        ("Article not found: tweet_id=1967890123456789012",
         "Article not found: tweet_id=1967890123456789012"),
        ("expired session", "expired session"),
        ("line one\nline two\nline three", "line one"),
        ("", "provider error"),
    ]
    for i, (raw, want) in enumerate(cases):
        runner.assert_eq(f"XT12-safe-{i}", _safe_reason(Exception(raw)),
                         want)
    runner.assert_true("XT12-safe-cap",
                       len(_safe_reason(Exception("z" * 500))) <= 200)


async def test_xt12_success_identity(runner: R) -> None:
    """The guard returns the exact successful object, unchanged."""
    from mcp_server.tools.twitter_tools import register_twitter_tools

    sentinel = {"status": "ok", "tweets": [{"id": "1"}]}
    fns: dict[str, object] = {}

    class _FakeMcp:
        def tool(self, name=None, description=None):
            def _deco(fn):
                fns[name] = fn
                return fn
            return _deco

    class _Ok:
        async def feed(self, *a, **k):
            return sentinel

    register_twitter_tools(_FakeMcp(),
                           type("S", (), {"x_twitter": _Ok()})())
    out = await fns["twitter_feed"]()
    runner.assert_true("XT12-identity", out is sentinel, "guard altered it")


def test_xt12_guard_is_single_sourced(runner: R) -> None:
    """One guard definition; no per-tool try/except; signatures intact."""
    import inspect

    import mcp_server.tools.twitter_tools as tt

    src = inspect.getsource(tt._guard)
    runner.assert_true("XT12-one-guard", src.count("def _wrapper") == 1)
    expected = {
        "twitter_feed": ["limit", "cursor", "live"],
        "twitter_search": ["query", "limit"],
        "twitter_tweet": ["url_or_id"],
        "twitter_article": ["url_or_id"],
        "twitter_bookmarks": ["limit"],
        "twitter_user_posts": ["handle", "limit"],
        "twitter_user_profile": ["handle"],
    }
    fns: dict[str, object] = {}

    class _FakeMcp:
        def tool(self, name=None, description=None):
            def _deco(fn):
                fns[name] = fn
                return fn
            return _deco

    class _Svc:
        pass

    tt.register_twitter_tools(_FakeMcp(),
                              type("S", (), {"x_twitter": _Svc()})())
    for tool, params in expected.items():
        fn = fns[tool]
        runner.assert_true(f"XT12-wrapped-{tool}",
                           hasattr(fn, "__wrapped__"))
        body = inspect.getsource(fn.__wrapped__)
        runner.assert_true(f"XT12-notry-{tool}",
                           "try:" not in body and "except " not in body,
                           "per-tool handling re-introduced")
        sig = list(inspect.signature(fn).parameters)
        runner.assert_eq(f"XT12-sig-{tool}", sig, params)


def test_xt11_registry_documents_outage(runner: R) -> None:
    from mcp_server import registry as R_

    search = next(t for t in R_.TOOLS if t["name"] == "twitter_search")
    runner.assert_true("XT11-reg-search",
                       "search_unavailable" in search["notes"], search["notes"])
    posts = next(t for t in R_.TOOLS if t["name"] == "twitter_user_posts")
    runner.assert_true("XT11-reg-posts", "not_found" in posts["notes"],
                       posts["notes"])


def test_xt10_test_route_redacted(runner: R) -> None:
    """POST /api/x/test returns only safe metadata (no cookie values)."""
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from api.x_routes import build_x_routes
    from x_twitter.service import XTwitterService

    store, tmp = _mk_store()
    creds = _FakeCreds()
    svc = XTwitterService(store=store, cred_store=creds, bus=None,
                          runner=_capture_runner({"ok": True}))
    svc.save_credentials("SECRET-TOK-XYZ", "SECRET-CT0-XYZ")
    client = TestClient(Starlette(routes=build_x_routes(svc)))
    resp = client.post("/api/x/test")
    runner.assert_eq("XT10-route-status", resp.status_code, 200)
    body = resp.json()
    blob = json.dumps(body)
    runner.assert_not_in("XT10-route-tok", "SECRET-TOK-XYZ", blob)
    runner.assert_not_in("XT10-route-ct0", "SECRET-CT0-XYZ", blob)
    runner.assert_eq("XT10-route-state", body.get("state"), "authenticated")
    runner.assert_true("XT10-route-checked", bool(body.get("checked_at")))
