from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from stock_picker.strategies.adaptive_trend_v1_3.date_range import resolve_date_range
from stock_picker.strategies.adaptive_trend_v1_3.market_overlay import calculate_market_overlay
from stock_picker.strategies.adaptive_trend_v1_3.phase5_models import DateRangeSpec, Phase5Error
from stock_picker.strategies.adaptive_trend_v1_3.runtime_data_adapter import (
    RuntimeDataAdapter,
    normalize_baostock_minute_frame,
)


def _calendar():
    return tuple(pd.date_range("2024-01-01", periods=400, freq="D").date)


def test_recent_range_uses_provider_latest_available_date():
    result = resolve_date_range(
        DateRangeSpec("RECENT_MONTHS", value=1), _calendar(),
        latest_available_date=date(2025, 1, 15),
    )
    assert result.requested_end_date == date(2025, 1, 15)


def test_custom_range_over_provider_latest_is_rejected_without_truncation():
    with pytest.raises(Phase5Error, match="data_max_date:2025-01-15"):
        resolve_date_range(
            DateRangeSpec("CUSTOM", start_date="2025-01-01", end_date="2025-01-16"),
            _calendar(), latest_available_date=date(2025, 1, 15),
        )


def test_baostock_completed_minute_timestamp_becomes_contract_bar_start():
    raw = pd.DataFrame([
        {"datetime": "2026-08-05 09:35:00", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10, "amount": 20},
        {"datetime": "2026-08-05 15:00:00", "open": 2, "high": 3, "low": 2, "close": 3, "volume": 10, "amount": 30},
    ])
    result = normalize_baostock_minute_frame(raw, "600000.SH")
    assert result["trade_date"].tolist() == ["2026-08-05", "2026-08-05"]
    assert result["bar_start"].tolist() == ["2026-08-05 09:30:00", "2026-08-05 14:55:00"]
    assert set(result["trade_status"]) == {"normal"}
    assert set(result["limit_status"]) == {"normal"}


def test_runtime_adapter_uses_snapshot_partition_symbol_for_benchmarks():
    dates = pd.date_range("2025-01-02", periods=80, freq="B")

    def rows(raw_identity):
        return tuple({
            **raw_identity,
            "date": day.strftime("%Y-%m-%d"),
            "open": 100 + position,
            "high": 102 + position,
            "low": 99 + position,
            "close": 101 + position,
            "volume": 1000,
        } for position, day in enumerate(dates))

    partition_rows = {
        "p300": rows({"index_code": "000300"}),
        "p852": rows({"symbol": "000852.SZ"}),
        "p399": rows({"symbol": "399006.SZ"}),
    }
    metadata = tuple(
        (partition_id, "benchmark_daily_bar", f"benchmark_daily_bar:{symbol}",
         "fixture", "1", "1d", symbol, "[]", "[]")
        for partition_id, symbol in (
            ("p300", "000300.SH"), ("p852", "000852.SH"), ("p399", "399006.SZ")
        )
    )

    class Cache:
        def load_snapshot(self, _snapshot_id):
            return SimpleNamespace(
                partition_ids=tuple(partition_rows), partition_metadata=metadata,
            )

        def load_rows(self, partition_id):
            return partition_rows[partition_id]

    class Runs:
        def load_snapshot_bundle(self, _run_id):
            return {"config": {"data_snapshot_id": "snapshot"}}

    adapter = RuntimeDataAdapter(Cache(), Runs(), "run")
    raw = adapter.dependencies().decision_1000_data(
        {"cash": "100000"}, SimpleNamespace(trade_date=date(2025, 5, 1)),
    )
    histories = raw["market_overlay"]["index_histories"]

    assert set(histories) == {"000300.SH", "000852.SH", "399006.SZ"}
    assert all(len(frame) == 80 for frame in histories.values())
    assert set(histories["000300.SH"]["symbol"]) == {"000300.SH"}
    assert set(histories["000852.SH"]["symbol"]) == {"000852.SH"}
    overlay = calculate_market_overlay(**raw["market_overlay"])
    assert not overlay[overlay["status"].eq("VALID")].empty
