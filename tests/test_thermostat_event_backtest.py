from __future__ import annotations

import pandas as pd

from stock_picker.strategies.thermostat import (
    backtest_thermostat_strategy,
    simplified_backtest_thermostat_strategy,
)


class FakeService:
    def __init__(self, histories: dict[str, pd.DataFrame]) -> None:
        self.histories = histories

    def get_history(self, symbol: str, **kwargs) -> pd.DataFrame:
        return self.histories[symbol]

    def get_index_history(self, index_code: str, start_date: str, end_date: str, period: str = "daily") -> pd.DataFrame:
        return self.histories["000001.SH"]


def _history(closes: list[float], symbol: str = "600001.SH") -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": dates.strftime("%Y-%m-%d"),
            "open": closes,
            "high": [value * 1.01 for value in closes],
            "low": [value * 0.99 for value in closes],
            "close": closes,
            "volume": [100000] * len(closes),
        }
    )


def test_default_thermostat_backtest_is_event_driven() -> None:
    result = backtest_thermostat_strategy(
        FakeService(
            {
                "600001.SH": _history([10 + i * 0.1 for i in range(80)], "600001.SH"),
                "000001.SH": _history([3000 + i * 5 for i in range(80)], "000001.SH"),
            }
        ),
        symbols=["600001.SH"],
        start_date="20260101",
        end_date="20260320",
        initial_cash=100000,
    )

    assert result.summary.loc[0, "backtest_type"] == "event_driven"
    assert hasattr(result, "trades")
    assert hasattr(result, "daily_portfolio")
    assert "simplified_backtest" not in result.summary.to_string()


def test_simplified_backtest_is_explicitly_marked() -> None:
    result = simplified_backtest_thermostat_strategy(
        FakeService(
            {
                "600001.SH": _history([10 + i * 0.1 for i in range(80)], "600001.SH"),
                "000001.SH": _history([3000 + i * 5 for i in range(80)], "000001.SH"),
            }
        ),
        symbols=["600001.SH"],
        start_date="20260101",
        end_date="20260320",
        initial_cash=100000,
    )

    assert result.summary.loc[0, "backtest_type"] == "simplified_backtest"
