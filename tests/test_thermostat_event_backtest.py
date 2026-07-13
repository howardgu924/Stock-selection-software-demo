from __future__ import annotations

import pandas as pd

from stock_picker.strategies.thermostat import (
    legacy_backtest_thermostat_strategy,
    simplified_backtest_thermostat_strategy,
)
from stock_picker.strategies.event_backtest import BacktestSettings, EventBacktestEngine, Signal


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
    result = legacy_backtest_thermostat_strategy(
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


def test_t1_phase_one_does_not_replace_event_backtest_contract() -> None:
    result = legacy_backtest_thermostat_strategy(
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
    assert {"summary", "trades", "daily_portfolio", "positions"}.issubset(result.__dict__)
    assert "pending_sell_level" not in result.trades.columns


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


def test_generic_event_engine_keeps_other_strategy_final_liquidation_semantics() -> None:
    prices = pd.DataFrame(
        [
            {
                "symbol": "600001.SH", "date": date, "time_point": point,
                "price": price, "limit_status": "normal", "is_suspended": False,
            }
            for date, price in (("2026-01-02", 10.0), ("2026-01-05", 11.0))
            for point in ("morning_open", "noon", "afternoon_open", "close")
        ]
    )
    engine = EventBacktestEngine(
        BacktestSettings(initial_cash=20_000.0, force_final_liquidation=True, t_plus_one=False)
    )

    def other_strategy(context):
        if context.date == "2026-01-02" and context.time_point == "noon":
            return [Signal("600001.SH", "buy", 100, strategy_family="other_strategy")]
        return []

    result = engine.run(prices, signal_provider=other_strategy)

    assert list(result.trades["side"]) == ["buy", "sell"]
    assert result.positions.empty
