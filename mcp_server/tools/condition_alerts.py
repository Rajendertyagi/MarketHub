"""Advanced market_condition alert MCP tools (B5 public contract).

Public family: condition_alert_create / _list / _get / _set_enabled / _delete.

These are THIN adapters over the B2/B4 ConditionAlertEngine + EventStore.
No engine logic is duplicated here. Instrument references are human/canonical
(exchange+symbol, exchange+underlying+expiry, exchange+underlying+expiry+
strike+option_type) and are resolved to the provider-neutral canonical
identity internally — callers never need broker tokens.

Condition schema:
  * v1 (condition_version=1): a single leaf
      {condition_version:1, metric, operator, value, instrument:{...}}
  * v2 (condition_version=2): a nested all/any group
      {condition_version:2, logic:"all"|"any", conditions:[...]}

Metrics and operators are fixed enums (see the module constants).
Supports multi-instrument, multi-chain analytics, and mixed quote/analytics groups.
Instrument references are human/canonical (exchange+symbol,
exchange+underlying+expiry, ...) — no broker tokens required.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from core.errors import (
    AlertNotFoundError,
    ConditionValidationError,
    ConsumerNotFoundError,
    StorageError,
    ValidationError,
)
from core.persistence.modules.condition_alerts import (
    CONDITION_VERSION_V1,
    CONDITION_VERSION_V2,
    MAX_CONDITION_DEPTH,
    MAX_CONDITION_LEAVES,
    VALID_OPERATORS,
    VALID_TRIGGER_MODES,
)
from market.condition_metrics import METRIC_SET, METRIC_SOURCE
from mcp_server.contract import (
    TOOL_CONDITION_ALERT_CREATE,
    TOOL_CONDITION_ALERT_DELETE,
    TOOL_CONDITION_ALERT_GET,
    TOOL_CONDITION_ALERT_LIST,
    TOOL_CONDITION_ALERT_SET_ENABLED,
)
from mcp_server.registry import get_tool_description

# greeks.* metrics are only meaningful on OPTION instruments.
_GREEKS_METRICS = frozenset({
    "greeks.delta", "greeks.gamma", "greeks.theta",
    "greeks.vega", "greeks.rho", "greeks.iv",
})


# ---------------------------------------------------------------------------
# Public instrument-reference resolution
# ---------------------------------------------------------------------------


def _resolve_public_instrument(services: Any,
                               ref: dict[str, Any]) -> dict[str, Any]:
    """Resolve a structured public instrument reference to a canonical identity.

    ref keys (all optional except exchange + one symbol/underlying):
        exchange, symbol, underlying, expiry, strike, option_type

    Returns {canonical_id, exchange, instrument_type, symbol, name,
    underlying, expiry, strike, option_type}. Raises ValidationError on
    unresolved/ambiguous references.
    """
    catalog = getattr(services, "instrument_catalog", None)
    resolver = getattr(services, "condition_identity_resolver", None)
    if catalog is None or resolver is None:
        raise StorageError("instrument resolution services not available")

    exchange = (ref.get("exchange") or "").strip().upper()
    if not exchange:
        raise ValidationError("instrument.exchange is required")
    symbol = (ref.get("symbol") or "").strip()
    underlying = (ref.get("underlying") or "").strip()
    expiry = (ref.get("expiry") or "").strip() or None
    strike = ref.get("strike")
    option_type = (ref.get("option_type") or "").strip().upper() or None

    # Determine the instrument type from the reference shape.
    if option_type is not None:
        if option_type not in ("CE", "PE"):
            raise ValidationError(
                f"instrument.option_type must be 'CE' or 'PE' (got {option_type!r})")
        if not underlying:
            raise ValidationError("instrument.underlying is required for an option")
        if strike is None:
            raise ValidationError("instrument.strike is required for an option")
        if not expiry:
            raise ValidationError("instrument.expiry is required for an option")
        try:
            strike_f = float(strike)
        except (TypeError, ValueError):
            raise ValidationError("instrument.strike must be a number")
        rows = catalog.search(exchange=exchange, instrument_type="OPTION",
                              underlying=underlying, expiry=expiry,
                              option_type=option_type, strike=strike_f,
                              limit=10)
    elif expiry is not None:
        if not underlying:
            raise ValidationError("instrument.underlying is required for a future")
        rows = catalog.search(exchange=exchange, instrument_type="FUTURE",
                              underlying=underlying, expiry=expiry, limit=10)
    else:
        if not symbol:
            raise ValidationError("instrument.symbol (or underlying) is required")
        rows = []
        for itype in ("INDEX", "EQUITY", "ETF"):
            rows = catalog.search(exchange=exchange, instrument_type=itype,
                                  q=symbol, limit=10)
            if rows:
                break

    if not rows:
        raise ValidationError(
            f"unresolved instrument: {exchange} {symbol or underlying or ''}")

    # Prefer an exact symbol/underlying match; reject ambiguity otherwise.
    exact = None
    for row in rows:
        if symbol and (row.get("tradingsymbol") or "").upper() == symbol.upper():
            exact = row
            break
        if underlying and (row.get("underlying") or "").upper() == underlying.upper():
            exact = row
            break
    row = exact if exact is not None else rows[0]
    if exact is None and len(rows) > 1:
        raise ValidationError(
            f"ambiguous instrument: {exchange} {symbol or underlying or ''}")

    canonical_id = resolver.canonical_id_for_row(row)
    if canonical_id is None:
        raise ValidationError(
            f"cannot derive canonical identity for {exchange} "
            f"{row.get('tradingsymbol')}")
    return {
        "canonical_id": canonical_id,
        "exchange": row.get("exchange") or exchange,
        "instrument_type": row.get("instrument_type"),
        "symbol": row.get("tradingsymbol"),
        "name": row.get("name"),
        "underlying": row.get("underlying"),
        "expiry": row.get("expiry"),
        "strike": row.get("strike"),
        "option_type": row.get("option_type"),
    }


# ---------------------------------------------------------------------------
# Public condition normalization (human refs -> internal canonical tree)
# ---------------------------------------------------------------------------


def _tree_canonical_id(node: dict[str, Any]) -> str | None:
    if node.get("condition_version") == CONDITION_VERSION_V1:
        return node.get("instrument", {}).get("canonical_id")
    children = node.get("conditions", [])
    if children:
        return _tree_canonical_id(children[0])
    return None


def _normalize_leaf(services: Any, node: dict[str, Any], *,
                    leaf_count: list[int]) -> dict[str, Any]:
    if "logic" in node or "conditions" in node:
        raise ConditionValidationError(
            "leaf must not contain logic/conditions (use a group node)")
    metric = node.get("metric")
    if metric not in METRIC_SET:
        raise ConditionValidationError(f"unknown metric: {metric!r}")
    operator = node.get("operator")
    if operator not in VALID_OPERATORS:
        raise ConditionValidationError(f"unknown operator: {operator!r}")
    value = node.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConditionValidationError(
            "value must be numeric (bool is not numeric)")
    instrument = node.get("instrument")
    if not isinstance(instrument, dict):
        raise ConditionValidationError("instrument must be an object")

    is_analytics = METRIC_SOURCE.get(metric) == "analytics"

    if is_analytics:
        expiry = (instrument.get("expiry") or "").strip()
        if not expiry:
            raise ConditionValidationError(
                "analytics metric requires instrument.expiry (YYYY-MM-DD)")
        ref_without_expiry = {k: v for k, v in instrument.items()
                              if k != "expiry"}
        resolved = _resolve_public_instrument(services, ref_without_expiry)
        canonical_id = resolved["canonical_id"]
        try:
            from datetime import datetime
            datetime.strptime(expiry, "%Y-%m-%d")
        except (ValueError, TypeError):
            raise ConditionValidationError(
                f"instrument.expiry must be YYYY-MM-DD (got {expiry!r})")
        chain_key = f"analytics:{canonical_id}:{expiry}"
    else:
        resolved = _resolve_public_instrument(services, instrument)
        canonical_id = resolved["canonical_id"]
        chain_key = f"quote:{canonical_id}"

    if metric in _GREEKS_METRICS and resolved["instrument_type"] != "OPTION":
        raise ConditionValidationError(
            f"metric {metric!r} is only supported on OPTION instruments")
    leaf_count[0] += 1
    if leaf_count[0] > MAX_CONDITION_LEAVES:
        raise ConditionValidationError(
            f"too many leaves (max {MAX_CONDITION_LEAVES})")
    result = {
        "condition_version": CONDITION_VERSION_V1,
        "condition_id": uuid.uuid4().hex,
        "metric": metric,
        "operator": operator,
        "value": value,
        "instrument": {"canonical_id": canonical_id},
        "_dependency_key": chain_key,
    }
    if is_analytics:
        result["instrument"]["expiry"] = expiry
    return result


def _normalize_group(services: Any, node: dict[str, Any], *,
                     depth: int, leaf_count: list[int]) -> dict[str, Any]:
    if depth >= MAX_CONDITION_DEPTH:
        raise ConditionValidationError(
            f"max condition depth ({MAX_CONDITION_DEPTH}) exceeded")
    logic = node.get("logic")
    if logic not in ("all", "any"):
        raise ConditionValidationError(
            f"invalid logic: {logic!r} (expected 'all' or 'any')")
    conditions = node.get("conditions")
    if not isinstance(conditions, list) or len(conditions) == 0:
        raise ConditionValidationError("conditions must be a non-empty array")
    if len(conditions) > MAX_CONDITION_LEAVES:
        raise ConditionValidationError(
            f"too many children ({len(conditions)}), max {MAX_CONDITION_LEAVES}")
    normalized: list[dict[str, Any]] = []
    for child in conditions:
        if not isinstance(child, dict):
            raise ConditionValidationError("each condition must be an object")
        if child.get("condition_version") == CONDITION_VERSION_V2:
            nv = _normalize_group(services, child, depth=depth + 1,
                                  leaf_count=leaf_count)
        else:
            nv = _normalize_leaf(services, child, leaf_count=leaf_count)
        normalized.append(nv)
    return {"condition_version": CONDITION_VERSION_V2, "logic": logic,
            "conditions": normalized}


def _normalize_public_condition(services: Any,
                                condition: dict[str, Any]) -> dict[str, Any]:
    """Convert a public condition (v1 leaf or v2 group) to the internal tree.

    Assigns condition_ids, resolves instrument references to canonical ids,
    and enforces depth/leaf limits. Multi-instrument and mixed-source groups
    are supported. Raises ConditionValidationError on any violation.
    """
    if not isinstance(condition, dict):
        raise ConditionValidationError("condition must be a JSON object")
    version = condition.get("condition_version")
    if version not in (CONDITION_VERSION_V1, CONDITION_VERSION_V2):
        raise ConditionValidationError(
            f"condition_version must be 1 or 2 (got {version!r})")
    leaf_count: list[int] = [0]
    if version == CONDITION_VERSION_V1:
        return _normalize_leaf(services, condition, leaf_count=leaf_count)
    return _normalize_group(services, condition, depth=0,
                            leaf_count=leaf_count)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_condition_alert_tools(mcp, services, **kwargs) -> None:
    """Register the 5 public advanced condition-alert tools (B5)."""
    store = getattr(services, "store", None)
    engine = getattr(services, "condition_alert_engine", None)
    analytics = getattr(services, "analytics_service", None)

    def _reload_engine() -> None:
        if engine is not None:
            try:
                engine.reload()
            except Exception:
                pass

    def _public_alert(alert: dict[str, Any]) -> dict[str, Any]:
        """Public-safe representation (no provider tokens, no internal state)."""
        condition = alert.get("condition") or {}
        # Preserve analytics-specific fields for public output.
        public_condition = dict(condition)
        if "conditions" in public_condition:
            public_conditions = []
            for child in public_condition["conditions"]:
                pc = dict(child)
                public_conditions.append(pc)
            public_condition["conditions"] = public_conditions
        # Copy expiry from instrument if present (analytics).
        inst = public_condition.get("instrument", {})
        if "expiry" in inst:
            public_condition["instrument"] = dict(inst)
        return {
            "alert_id": alert.get("alert_id"),
            "consumer_id": alert.get("consumer_id"),
            "name": alert.get("name"),
            "enabled": bool(alert.get("enabled")),
            "trigger_mode": alert.get("trigger_mode"),
            "condition": public_condition,
            "metadata": alert.get("metadata") or {},
            "created_at": alert.get("created_at"),
            "updated_at": alert.get("updated_at"),
            "trigger_count": int(alert.get("trigger_count") or 0),
            "last_triggered_at": alert.get("last_triggered_at"),
        }

    def _get_owned(consumer_id: str, alert_id: str) -> dict[str, Any]:
        """Fetch an alert and enforce consumer ownership (not-found on cross)."""
        if store is None:
            raise StorageError("condition alert store not available")
        alert = store.get_condition_alert(alert_id)
        if alert is None:
            raise AlertNotFoundError(alert_id)
        if alert.get("consumer_id") != consumer_id:
            # Cross-owner access is treated as not-found (existing pattern).
            raise AlertNotFoundError(alert_id)
        return alert

    def _extract_analytics_chains(condition: dict[str, Any]) -> set[str]:
        """Extract analytics chain keys from a normalized condition tree."""
        keys: set[str] = set()
        version = condition.get("condition_version")
        if version == CONDITION_VERSION_V1:
            dep = condition.get("_dependency_key")
            if dep:
                keys.add(dep)
        elif version == CONDITION_VERSION_V2:
            for child in condition.get("conditions", []):
                keys |= _extract_analytics_chains(child)
        return keys

    def _extract_all_deps(condition: dict[str, Any]) -> set[str]:
        """Extract all dependency keys (quote + analytics) from a normalized condition tree."""
        keys: set[str] = set()
        version = condition.get("condition_version")
        if version == CONDITION_VERSION_V1:
            dep = condition.get("_dependency_key")
            if dep:
                keys.add(dep)
        elif version == CONDITION_VERSION_V2:
            for child in condition.get("conditions", []):
                keys |= _extract_all_deps(child)
        return keys

    def _register_chains_for_alert(alert: dict[str, Any]) -> None:
        """Register analytics chains if this alert uses analytics metrics."""
        if analytics is None:
            return
        condition = alert.get("condition")
        if not isinstance(condition, dict):
            return
        for key in _extract_analytics_chains(condition):
            analytics.register_chain(key, alert["alert_id"])

    def _unregister_chains_for_alert(alert: dict[str, Any]) -> None:
        """Unregister analytics chains for this alert."""
        if analytics is None:
            return
        condition = alert.get("condition")
        if not isinstance(condition, dict):
            return
        for key in _extract_analytics_chains(condition):
            analytics.unregister_chain(key, alert["alert_id"])

    @mcp.tool(name=TOOL_CONDITION_ALERT_CREATE, description=get_tool_description(TOOL_CONDITION_ALERT_CREATE))
    async def condition_alert_create(
        consumer_id: str,
        condition: dict[str, object],
        trigger_mode: str = "repeat",
        name: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        """Create a consumer-owned advanced market-condition alert.

        condition_version=1 is a single leaf; condition_version=2 is a
        nested all/any group supporting multi-instrument and mixed
        quote/analytics targets. Metrics and operators are fixed enums.
        Instrument references are human/canonical (exchange+symbol,
        exchange+underlying+expiry, ...) — no broker tokens required.
        """
        if store is None:
            raise StorageError("condition alert store not available")
        consumer_id = (consumer_id or "").strip()
        if not consumer_id:
            raise ValidationError("consumer_id must not be empty")
        trigger_mode = (trigger_mode or "repeat").strip()
        if trigger_mode not in VALID_TRIGGER_MODES:
            raise ValidationError(
                f"trigger_mode must be one of {sorted(VALID_TRIGGER_MODES)}")
        if name is not None:
            name = name.strip() or None
        normalized = _normalize_public_condition(services, condition)

        def _create():
            return store.create_condition_alert(
                consumer_id=consumer_id, name=name, trigger_mode=trigger_mode,
                condition_json=normalized, metadata=metadata)

        try:
            alert_id = await asyncio.to_thread(_create)
        except (ConsumerNotFoundError, ConditionValidationError):
            raise
        except Exception as exc:
            raise StorageError("condition alert creation failed", exc) from exc
        _reload_engine()
        alert = store.get_condition_alert(alert_id)
        _register_chains_for_alert(alert)
        return {"status": "created", "alert": _public_alert(alert)}

    @mcp.tool(name=TOOL_CONDITION_ALERT_LIST, description=get_tool_description(TOOL_CONDITION_ALERT_LIST))
    async def condition_alert_list(
        consumer_id: str,
        enabled: bool | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """List condition alerts owned by a consumer.

        enabled filters to enabled (true) or disabled (false) alerts.
        limit caps the returned count (default 50, max 200).
        """
        if store is None:
            raise StorageError("condition alert store not available")
        consumer_id = (consumer_id or "").strip()
        if not consumer_id:
            raise ValidationError("consumer_id must not be empty")
        alerts = await asyncio.to_thread(store.list_condition_alerts, consumer_id)
        if enabled is not None:
            alerts = [a for a in alerts if bool(a.get("enabled")) == enabled]
        if limit is not None:
            limit = max(1, min(int(limit), 200))
            alerts = alerts[:limit]
        return {"status": "ok", "count": len(alerts),
                "alerts": [_public_alert(a) for a in alerts]}

    @mcp.tool(name=TOOL_CONDITION_ALERT_GET, description=get_tool_description(TOOL_CONDITION_ALERT_GET))
    async def condition_alert_get(
        consumer_id: str,
        alert_id: str,
    ) -> dict[str, Any]:
        """Get one condition alert by id (ownership enforced)."""
        consumer_id = (consumer_id or "").strip()
        alert_id = (alert_id or "").strip()
        if not consumer_id:
            raise ValidationError("consumer_id must not be empty")
        if not alert_id:
            raise ValidationError("alert_id must not be empty")
        alert = await asyncio.to_thread(_get_owned, consumer_id, alert_id)
        return {"status": "ok", "alert": _public_alert(alert)}

    @mcp.tool(name=TOOL_CONDITION_ALERT_SET_ENABLED, description=get_tool_description(TOOL_CONDITION_ALERT_SET_ENABLED))
    async def condition_alert_set_enabled(
        consumer_id: str,
        alert_id: str,
        enabled: bool,
    ) -> dict[str, Any]:
        """Enable or disable a condition alert (ownership enforced).

        Enabling an already-disabled alert explicitly re-arms it: runtime
        state is reset to UNKNOWN so a once-mode alert can fire again.
        """
        consumer_id = (consumer_id or "").strip()
        alert_id = (alert_id or "").strip()
        if not consumer_id:
            raise ValidationError("consumer_id must not be empty")
        if not alert_id:
            raise ValidationError("alert_id must not be empty")
        # Ownership check first (not-found on cross-owner).
        await asyncio.to_thread(_get_owned, consumer_id, alert_id)

        def _set():
            if enabled:
                # Re-arm: clear runtime state so the alert starts from UNKNOWN.
                store.reset_condition_runtime_state(alert_id)
            store.set_condition_alert_enabled(alert_id, enabled)

        try:
            await asyncio.to_thread(_set)
        except AlertNotFoundError:
            raise
        except Exception as exc:
            raise StorageError("condition alert enable/disable failed", exc) from exc
        _reload_engine()
        # Re-register chains after enable (re-arm may need fresh chain data).
        if enabled:
            alert = store.get_condition_alert(alert_id)
            if alert is not None:
                _register_chains_for_alert(alert)
        return {"status": "enabled" if enabled else "disabled",
                "ok": True, "alert_id": alert_id, "enabled": bool(enabled)}

    @mcp.tool(name=TOOL_CONDITION_ALERT_DELETE, description=get_tool_description(TOOL_CONDITION_ALERT_DELETE))
    async def condition_alert_delete(
        consumer_id: str,
        alert_id: str,
    ) -> dict[str, Any]:
        """Delete a condition alert (ownership enforced).

        Historical trigger records are preserved (deleting an alert never
        erases its past firings).
        """
        consumer_id = (consumer_id or "").strip()
        alert_id = (alert_id or "").strip()
        if not consumer_id:
            raise ValidationError("consumer_id must not be empty")
        if not alert_id:
            raise ValidationError("alert_id must not be empty")
        # Ownership check first (not-found on cross-owner).
        await asyncio.to_thread(_get_owned, consumer_id, alert_id)

        def _del():
            store.delete_condition_alert(alert_id)

        try:
            await asyncio.to_thread(_del)
        except AlertNotFoundError:
            raise
        except Exception as exc:
            raise StorageError("condition alert deletion failed", exc) from exc
        # Unregister chains before reload (alert no longer exists).
        _unregister_chains_for_alert({"alert_id": alert_id, "condition": {}})
        _reload_engine()
        return {"status": "deleted", "ok": True, "alert_id": alert_id}
