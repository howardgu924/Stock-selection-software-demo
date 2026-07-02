from __future__ import annotations

from pathlib import Path
import tempfile
from uuid import uuid4

import pandas as pd

from stock_picker.data.storage import SQLiteMarketDataStore


def workspace_path(name: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=f"{name}-{uuid4().hex}-"))


def _event_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "600001.SH",
                "date": "2026-01-02",
                "time_point": "daily",
                "frequency": "daily",
                "adjust_type": "qfq",
                "source": "fixture",
                "price": 10.0,
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "prev_close": 9.9,
                "limit_up_price": 10.89,
                "limit_down_price": 8.91,
                "is_suspended": False,
                "limit_status": "normal",
                "simulated": False,
                "warning": "",
            },
            {
                "symbol": "600001.SH",
                "date": "2026-01-02",
                "time_point": "afternoon_open",
                "frequency": "snapshot",
                "adjust_type": "qfq",
                "source": "fixture",
                "price": 10.1,
                "open": None,
                "high": None,
                "low": None,
                "close": None,
                "prev_close": 9.9,
                "limit_up_price": 10.89,
                "limit_down_price": 8.91,
                "is_suspended": False,
                "limit_status": "normal",
                "simulated": True,
                "warning": "simulated_afternoon_open_price",
            },
        ]
    )


def test_event_price_cache_distinguishes_time_point_frequency_adjust_and_source() -> None:
    store = SQLiteMarketDataStore(workspace_path("event-cache") / "market.sqlite3")

    store.save_event_prices(_event_rows())
    rows = store.load_event_prices(
        symbols=["600001.SH"],
        start_date="20260102",
        end_date="20260102",
    )

    assert rows["time_point"].tolist() == ["afternoon_open", "daily"]
    assert set(rows["frequency"]) == {"daily", "snapshot"}
    assert set(rows["adjust_type"]) == {"qfq"}
    assert set(rows["source"]) == {"fixture"}


def test_event_price_cache_keeps_old_daily_history_compatible() -> None:
    store = SQLiteMarketDataStore(workspace_path("event-cache-history") / "market.sqlite3")
    history = pd.DataFrame(
        [
            {
                "symbol": "600001.SH",
                "date": "2026-01-02",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "volume": 100.0,
                "amount": 1000.0,
                "amplitude": None,
                "pct_chg": None,
                "change": None,
                "turnover": None,
            }
        ]
    )

    store.save_event_prices(_event_rows())
    store.save_history(history)

    loaded = store.load_history("600001.SH", "20260102", "20260102")
    assert loaded["close"].tolist() == [10.2]


def test_validate_event_cache_reports_missing_required_time_points_and_fields() -> None:
    store = SQLiteMarketDataStore(workspace_path("event-cache-validation") / "market.sqlite3")
    incomplete = _event_rows().query("time_point == 'daily'")
    store.save_event_prices(incomplete)

    result = store.validate_event_cache(
        symbols=["600001.SH"],
        start_date="20260102",
        end_date="20260102",
        required_time_points=["morning_open", "afternoon_open", "close"],
    )

    assert not result.ok
    assert {"morning_open", "afternoon_open", "close"}.issubset(
        {item["time_point"] for item in result.missing}
    )
    assert any("limit" in warning["warning"] for warning in result.warnings)

