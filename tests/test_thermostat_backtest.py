from __future__ import annotations

import pandas as pd

from stock_picker.strategies.thermostat import legacy_backtest_thermostat_strategy


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


def test_thermostat_backtest_summary_contains_required_metrics() -> None:
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

    required = {
        "total_return",
        "annualized_return",
        "max_drawdown",
        "win_rate",
        "profit_loss_ratio",
        "average_holding_days",
        "trade_count",
        "position_utilization",
        "cash_ratio",
        "benchmark_return",
    }
    assert required.issubset(result.summary.columns)
    assert result.summary.loc[0, "strategy"] == "thermostat"


def test_thermostat_backtest_splits_performance_by_regime() -> None:
    result = legacy_backtest_thermostat_strategy(
        FakeService(
            {
                "600001.SH": _history(([10 + i * 0.08 for i in range(40)] + [13 - i * 0.08 for i in range(40)]), "600001.SH"),
                "000001.SH": _history(([3000 + i * 5 for i in range(40)] + [3200 - i * 5 for i in range(40)]), "000001.SH"),
            }
        ),
        symbols=["600001.SH"],
        start_date="20260101",
        end_date="20260320",
        initial_cash=100000,
    )

    assert {"market_regime", "return", "max_drawdown"}.issubset(result.regime_performance.columns)
    assert result.diagnostics.loc[0, "regime_switch_count"] >= 1
    assert "average_after_switch_return" in result.diagnostics.columns


def test_thermostat_backtest_reports_grid_and_trend_risk_counts() -> None:
    result = legacy_backtest_thermostat_strategy(
        FakeService(
            {
                "600001.SH": _history([10, 10.3, 9.8, 10.2] * 20, "600001.SH"),
                "000001.SH": _history([3000, 3020, 2980, 3010] * 20, "000001.SH"),
            }
        ),
        symbols=["600001.SH"],
        start_date="20260101",
        end_date="20260320",
        initial_cash=100000,
    )

    assert "grid_invalid_count" in result.diagnostics.columns
    assert "trend_stop_count" in result.diagnostics.columns
