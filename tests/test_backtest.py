from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from stock_picker.strategies import backtest_strategy


class FakeBacktestService:
    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames
        self.minute_frames: dict[str, pd.DataFrame] = {}
        self.valuations: dict[str, pd.DataFrame] = {}

    def get_history(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        refresh: bool = False,
        indicators: bool = False,
    ) -> pd.DataFrame:
        return self.frames[symbol]

    def get_valuation_history(
        self,
        symbol: str,
        indicator: str = "市净率",
        period: str = "近一年",
    ) -> pd.DataFrame:
        return self.valuations[symbol]

    def get_minute_history(
        self,
        symbol: str,
        start_datetime: str,
        end_datetime: str,
        period: str = "5",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        return self.minute_frames[symbol]

    def get_index_history(
        self,
        index_code: str,
        start_date: str,
        end_date: str,
        period: str = "daily",
    ) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "index_code": index_code,
                    "date": "2024-01-01",
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 1.0,
                    "amount": 1.0,
                    "amplitude": 0.0,
                    "pct_chg": 0.0,
                    "change": 0.0,
                    "turnover": 0.0,
                },
                {
                    "index_code": index_code,
                    "date": "2024-01-06",
                    "open": 105.0,
                    "high": 105.0,
                    "low": 105.0,
                    "close": 105.0,
                    "volume": 1.0,
                    "amount": 1.0,
                    "amplitude": 0.0,
                    "pct_chg": 0.0,
                    "change": 5.0,
                    "turnover": 0.0,
                },
            ]
        )


def _frame(symbol: str, closes: list[float]) -> pd.DataFrame:
    rows = []
    for index, close in enumerate(closes, start=1):
        window = closes[max(0, index - 5) : index]
        rows.append(
            {
                "symbol": symbol,
                "date": f"2024-01-{index:02d}",
                "open": close,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 100.0,
                "amount": close * 100.0,
                "ma5": sum(window) / len(window),
            }
        )
    return pd.DataFrame(rows)


def _minute_frame(symbol: str, rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "datetime": timestamp,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 100.0,
                "amount": close * 100.0,
                "average_price": pd.NA,
                "price": pd.NA,
                "amplitude": pd.NA,
                "pct_chg": pd.NA,
                "change": pd.NA,
                "turnover": pd.NA,
            }
            for timestamp, open_, high, low, close in rows
        ]
    )


def test_backtest_ma_cross_buys_and_marks_equity() -> None:
    service = FakeBacktestService(
        {"600519.SH": _frame("600519.SH", [10.0, 10.0, 10.0, 10.0, 12.0, 13.0, 15.0])}
    )

    result = backtest_strategy(
        service=service,
        strategy="ma_cross",
        symbols=["600519"],
        start_date="20240101",
        end_date="20240107",
        initial_cash=100_000.0,
    )

    assert result.errors.empty
    assert not result.equity.empty
    assert result.trades["action"].tolist() == ["buy", "sell"]
    assert result.trades.iloc[0]["date"] == "2024-01-06"
    assert result.trades.iloc[-1]["reason"] == "final liquidation"
    assert result.summary.loc[0, "trade_count"] == 2
    assert result.summary.loc[0, "final_value"] > 100_000.0
    assert result.summary.loc[0, "benchmark_return"] == pytest.approx(0.05)


def test_backtest_turtle_can_sell_after_exit_break() -> None:
    closes = [10.0] * 20 + [12.0, 9.0]
    service = FakeBacktestService({"600519.SH": _frame("600519.SH", closes)})

    result = backtest_strategy(
        service=service,
        strategy="turtle",
        symbols=["600519"],
        start_date="20240101",
        end_date="20240122",
        initial_cash=100_000.0,
    )

    assert result.trades["action"].tolist() == ["buy", "sell"]
    assert result.trades.iloc[0]["date"] == "2024-01-22"
    assert result.summary.loc[0, "trade_count"] == 2


def test_backtest_turtle_midday_signal_buys_same_day_afternoon_open() -> None:
    closes = [10.0] * 20 + [14.0, 15.0]
    service = FakeBacktestService({"600519.SH": _frame("600519.SH", closes)})
    service.minute_frames = {
        "600519.SH": _minute_frame(
            "600519.SH",
            [
                ("2024-01-21 09:35:00", 10.0, 11.0, 9.9, 11.0),
                ("2024-01-21 11:30:00", 11.0, 12.2, 10.8, 12.0),
                ("2024-01-21 13:00:00", 13.0, 13.5, 12.8, 13.2),
                ("2024-01-22 09:35:00", 15.0, 15.2, 14.8, 15.0),
                ("2024-01-22 11:30:00", 15.0, 15.2, 14.8, 15.0),
                ("2024-01-22 13:00:00", 15.0, 15.2, 14.8, 15.0),
            ],
        )
    }

    result = backtest_strategy(
        service=service,
        strategy="turtle",
        symbols=["600519"],
        start_date="20240101",
        end_date="20240122",
        initial_cash=100_000.0,
        execution_timing="same_day_pm_open",
    )

    assert result.trades.iloc[0]["action"] == "buy"
    assert result.trades.iloc[0]["date"] == "2024-01-21"
    assert result.trades.iloc[0]["price"] == pytest.approx(13.0)
    assert "signal_time=midday" in result.trades.iloc[0]["reason"]


def test_backtest_limits_simultaneous_positions() -> None:
    service = FakeBacktestService(
        {
            "600001.SH": _frame("600001.SH", [10.0, 10.0, 10.0, 10.0, 12.0, 13.0]),
            "600002.SH": _frame("600002.SH", [10.0, 10.0, 10.0, 10.0, 12.0, 13.0]),
        }
    )

    result = backtest_strategy(
        service=service,
        strategy="ma_cross",
        symbols=["600001", "600002"],
        start_date="20240101",
        end_date="20240106",
        initial_cash=100_000.0,
        max_positions=1,
    )

    assert result.trades["action"].tolist() == ["buy", "sell"]
    assert result.trades["symbol"].tolist() == ["600001.SH", "600001.SH"]


def test_turtle_portfolio_prefers_stronger_breakout() -> None:
    service = FakeBacktestService(
        {
            "600001.SH": _frame("600001.SH", [10.0] * 20 + [11.0, 12.0]),
            "600002.SH": _frame("600002.SH", [10.0] * 20 + [13.0, 14.0]),
        }
    )

    result = backtest_strategy(
        service=service,
        strategy="turtle",
        symbols=["600001", "600002"],
        start_date="20240101",
        end_date="20240122",
        initial_cash=100_000.0,
        max_positions=1,
    )

    assert result.trades["action"].tolist() == ["buy", "sell"]
    assert result.trades["symbol"].tolist() == ["600002.SH", "600002.SH"]


def test_backtest_bank_rotation_buys_lowest_pb_and_liquidates() -> None:
    service = FakeBacktestService(
        {
            "600001.SH": _frame("600001.SH", [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]),
            "600002.SH": _frame("600002.SH", [10.0, 10.0, 10.0, 10.0, 10.0, 10.0]),
        }
    )
    service.valuations = {
        "600001.SH": pd.DataFrame(
            [
                {"symbol": "600001.SH", "date": "2024-01-01", "value": 0.8},
                {"symbol": "600001.SH", "date": "2024-01-05", "value": 1.2},
            ]
        ),
        "600002.SH": pd.DataFrame(
            [
                {"symbol": "600002.SH", "date": "2024-01-01", "value": 1.0},
                {"symbol": "600002.SH", "date": "2024-01-05", "value": 0.7},
            ]
        ),
    }

    result = backtest_strategy(
        service=service,
        strategy="bank_rotation",
        symbols=["600001", "600002"],
        start_date="20240101",
        end_date="20240106",
        initial_cash=100_000.0,
    )

    assert result.errors.empty
    assert result.trades["symbol"].tolist() == ["600001.SH", "600001.SH"]
    assert result.trades["action"].tolist() == ["buy", "sell"]
    assert result.trades.iloc[-1]["reason"] == "final liquidation"
    assert result.summary.loc[0, "sharpe_ratio"] != 0


def test_backtest_rejects_unsupported_strategy() -> None:
    with pytest.raises(ValueError, match="history-price strategies only"):
        backtest_strategy(
            service=FakeBacktestService({}),
            strategy="small_cap",
            symbols=["600519"],
            start_date="20240101",
            end_date="20240131",
        )


def test_backtest_cli_rejects_non_history_strategy() -> None:
    script = Path(__file__).resolve().parents[1] / "examples" / "backtest_strategy.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--strategy",
            "small_cap",
            "--symbol",
            "600519",
            "--start",
            "20240101",
            "--end",
            "20240131",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "invalid choice" in completed.stderr
