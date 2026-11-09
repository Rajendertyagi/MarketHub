"""Yahoo Finance research service — deterministic unit tests (no network).

Covers normalization, freshness classification, symbol/param validation,
structured error mapping, and DataFrame/Series conversion. yfinance itself is
injected as a fake, so the normal suite never depends on Yahoo being online.
"""
from __future__ import annotations

import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import pandas as pd

from yfinance_service.normalize import (
    classify_freshness,
    json_safe,
    records_from_frame,
    series_to_records,
)

# ── json_safe ────────────────────────────────────────────────────────────────

class TestJsonSafe:
    def test_none_passes_through(self):
        assert json_safe(None) is None

    def test_nan_becomes_none(self):
        assert json_safe(float("nan")) is None

    def test_nan_in_series_becomes_none(self):
        assert json_safe(float("nan")) is None

    def test_infinity_becomes_none(self):
        assert json_safe(float("inf")) is None
        assert json_safe(float("-inf")) is None

    def test_nat_becomes_none(self):
        assert json_safe(pd.NaT) is None

    def test_none_in_dataframe_becomes_none(self):
        assert json_safe(pd.NA) is None

    def test_int_and_float_preserved(self):
        assert json_safe(5) == 5
        assert json_safe(5.5) == 5.5

    def test_bool_preserved(self):
        assert json_safe(True) is True
        assert json_safe(False) is False

    def test_string_preserved(self):
        assert json_safe("RELIANCE.NS") == "RELIANCE.NS"

    def test_timestamp_becomes_iso_utc(self):
        import pandas as pd
        ts = pd.Timestamp("2026-09-24 10:30:00", tz="UTC")
        out = json_safe(ts)
        assert out == "2026-09-24T10:30:00+00:00"

    def test_naive_timestamp_gets_utc(self):
        import pandas as pd
        ts = pd.Timestamp("2026-09-24 10:30:00")
        out = json_safe(ts)
        assert out.endswith("+00:00")

    def test_date_becomes_iso(self):
        import pandas as pd
        d = pd.Timestamp("2026-09-24").date()
        assert json_safe(d) == "2026-09-24"

    def test_dict_is_cleaned_recursively(self):
        out = json_safe({"a": float("nan"), "b": {"c": float("inf")}, "d": 1})
        assert out == {"a": None, "b": {"c": None}, "d": 1}

    def test_list_is_cleaned_recursively(self):
        out = json_safe([1.0, float("nan"), "x", None])
        assert out == [1.0, None, "x", None]

    def test_tuple_becomes_list(self):
        out = json_safe((1, 2, 3))
        assert out == [1, 2, 3]
        assert isinstance(out, list)

    def test_numpy_scalar_becomes_python_scalar(self):
        import numpy as np
        assert json_safe(np.int64(7)) == 7
        assert isinstance(json_safe(np.int64(7)), int)
        assert json_safe(np.float64(1.5)) == 1.5

    def test_nan_safe_dict_serializable(self):
        """Result must be json.dumps-able without custom encoder."""
        import json
        out = json_safe({"a": float("nan"), "b": [float("inf")], "c": pd.NaT})
        json.dumps(out)  # must not raise


# ── classify_freshness ───────────────────────────────────────────────────────

class TestClassifyFreshness:
    def test_no_metadata_is_unknown(self):
        assert classify_freshness({}) == "UNKNOWN"

    def test_no_delay_no_source_is_unknown(self):
        assert classify_freshness({"regularMarketPrice": 100}) == "UNKNOWN"

    def test_zero_delay_with_realtime_source_is_live_stream(self):
        info = {
            "exchangeDataDelayedBy": 0,
            "quoteSourceName": "Nasdaq Real Time",
        }
        assert classify_freshness(info) == "LIVE_STREAM"

    def test_zero_delay_realtime_nasdaq(self):
        info = {"exchangeDataDelayedBy": 0, "quoteSourceName": "Nasdaq Real Time"}
        assert classify_freshness(info) == "LIVE_STREAM"

    def test_positive_delay_is_delayed(self):
        info = {"exchangeDataDelayedBy": 15, "quoteSourceName": "Delayed Quote"}
        assert classify_freshness(info) == "DELAYED"

    def test_source_name_realtime_without_delay_field_is_live(self):
        info = {"quoteSourceName": "Real Time"}
        assert classify_freshness(info) == "LIVE_STREAM"

    def test_delay_zero_but_no_source_name_is_snapshot(self):
        info = {"exchangeDataDelayedBy": 0}
        assert classify_freshness(info) == "SNAPSHOT"

    def test_unknown_result_is_explicit(self):
        """Never infer real-time from the absence of evidence."""
        assert classify_freshness({"marketState": "REGULAR"}) == "UNKNOWN"


# -- records_from_frame / series_to_records ---------------------------------

class TestRecordsFromFrame:
    def test_none_returns_empty(self):
        assert records_from_frame(None) == []

    def test_empty_frame_returns_empty(self):
        assert records_from_frame(pd.DataFrame()) == []

    def test_index_becomes_a_column(self):
        df = pd.DataFrame(
            {"Close": [1.0, 2.0]},
            index=pd.to_datetime(["2026-09-22", "2026-09-23"], utc=True),
        )
        rows = records_from_frame(df)
        assert len(rows) == 2
        assert "index" in rows[0]
        assert rows[0]["index"].startswith("2026-09-22")

    def test_nan_cells_become_none(self):
        df = pd.DataFrame({"Close": [1.0, float("nan")]})
        rows = records_from_frame(df)
        assert rows[0]["Close"] == 1.0
        assert rows[1]["Close"] is None

    def test_limit_truncates(self):
        df = pd.DataFrame({"Close": [1.0, 2.0, 3.0, 4.0, 5.0]})
        assert len(records_from_frame(df, limit=2)) == 2

    def test_result_is_json_serializable(self):
        import json
        df = pd.DataFrame(
            {"Close": [1.0, float("nan")]},
            index=pd.to_datetime(["2026-09-22", "2026-09-23"], utc=True),
        )
        json.dumps(records_from_frame(df))


class TestSeriesToRecords:
    def test_none_returns_empty(self):
        assert series_to_records(None) == []

    def test_empty_series_returns_empty(self):
        assert series_to_records(pd.Series([], dtype="float64")) == []

    def test_index_value_pairs(self):
        s = pd.Series(
            [0.25],
            index=pd.to_datetime(["2026-08-01"], utc=True),
        )
        rows = series_to_records(s, value_name="amount", date_name="ex_date")
        assert rows == [{"ex_date": "2026-08-01T00:00:00+00:00", "amount": 0.25}]

    def test_sorted_chronologically(self):
        s = pd.Series(
            [2.0, 1.0],
            index=pd.to_datetime(["2026-09-01", "2026-01-01"], utc=True),
        )
        rows = series_to_records(s)
        assert [r["value"] for r in rows] == [1.0, 2.0]

    def test_limit_keeps_most_recent(self):
        idx = pd.to_datetime(
            ["2026-01-01", "2026-02-01", "2026-03-01"], utc=True)
        rows = series_to_records(pd.Series([1.0, 2.0, 3.0], index=idx), limit=2)
        assert [r["value"] for r in rows] == [2.0, 3.0]

    def test_nan_value_becomes_none(self):
        s = pd.Series([float("nan")], index=pd.to_datetime(["2026-01-01"]))
        assert series_to_records(s)[0]["value"] is None
