from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from stock_picker.data.models import StockInfo
from stock_picker.strategies import run_strategy


class FakeStrategyService:
    def __init__(self) -> None:
        self.history: dict[str, pd.DataFrame] = {}
        self.snapshot = pd.DataFrame()
        self.financial: dict[str, pd.DataFrame] = {}
        self.index_members = pd.DataFrame()

    def get_history(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        refresh: bool = False,
        indicators: bool = False,
    ) -> pd.DataFrame:
        return self.history[symbol]

    def get_market_snapshot(self, symbols=None) -> pd.DataFrame:
        frame = self.snapshot.copy()
        if symbols:
            wanted = set(symbols)
            frame = frame[frame["symbol"].isin(wanted)]
        return frame.reset_index(drop=True)

    def get_financial_indicators(
        self,
        symbol: str,
        start_year: str = "1900",
    ) -> pd.DataFrame:
        return self.financial[symbol]

    def get_valuation_history(
        self,
        symbol: str,
        indicator: str = "总市值",
        period: str = "近一年",
    ) -> pd.DataFrame:
        return self.financial[symbol]

    def get_index_members(self, index_code: str) -> pd.DataFrame:
        return self.index_members

    def get_board_members(self, board_type: str, board: str) -> pd.DataFrame:
        return pd.DataFrame()


def _history(symbol: str, closes: list[float]) -> pd.DataFrame:
    rows = []
    for index, close in enumerate(closes, start=1):
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
                "ma5": sum(closes[max(0, index - 5) : index]) / min(index, 5),
            }
        )
    return pd.DataFrame(rows)


def _snapshot() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "600001.SH",
                "code": "600001",
                "name": "A",
                "market_cap": 2_000_000_000.0,
                "pb": 1.0,
            },
            {
                "symbol": "600002.SH",
                "code": "600002",
                "name": "B",
                "market_cap": 3_000_000_000.0,
                "pb": 0.8,
            },
            {
                "symbol": "600003.SH",
                "code": "600003",
                "name": "C",
                "market_cap": 1_999_999_999.0,
                "pb": 1.5,
            },
            {
                "symbol": "600004.SH",
                "code": "600004",
                "name": "D",
                "market_cap": 3_000_000_001.0,
                "pb": 1.8,
            },
        ]
    )


def test_ma_cross_returns_buy_signal() -> None:
    service = FakeStrategyService()
    service.history["600519.SH"] = _history("600519.SH", [10.0, 10.0, 10.0, 10.0, 12.0])

    result = run_strategy(
        service,
        "ma_cross",
        start_date="20240101",
        end_date="20240105",
        symbols=[StockInfo("600519.SH", "600519", "Kweichow Moutai")],
    )

    assert result.errors.empty
    assert result.results.loc[0, "action"] == "buy"
    assert result.results.loc[0, "strategy"] == "ma_cross"


def test_turtle_returns_breakout_signal() -> None:
    service = FakeStrategyService()
    service.history["600519.SH"] = _history("600519.SH", [10.0] * 20 + [12.0])

    result = run_strategy(
        service,
        "turtle",
        start_date="20240101",
        end_date="20240121",
        symbols=["600519"],
    )

    assert result.results["action"].tolist() == ["buy"]
    assert "broke 20-day high" in result.results.loc[0, "reason"]


def test_small_cap_includes_market_cap_boundaries() -> None:
    service = FakeStrategyService()
    service.snapshot = _snapshot()

    result = run_strategy(service, "small_cap", as_of="20260527")

    assert result.results["symbol"].tolist() == ["600001.SH", "600002.SH"]
    assert result.results["rank"].tolist() == [1, 2]


def test_small_cap_can_use_historical_valuation_for_symbols() -> None:
    service = FakeStrategyService()
    service.financial = {
        "600001.SH": pd.DataFrame(
            [
                {"symbol": "600001.SH", "indicator": "总市值", "date": "2024-01-01", "value": 19.0},
                {"symbol": "600001.SH", "indicator": "总市值", "date": "2024-01-02", "value": 20.0},
            ]
        ),
        "600002.SH": pd.DataFrame(
            [
                {"symbol": "600002.SH", "indicator": "总市值", "date": "2024-01-02", "value": 30.0},
            ]
        ),
        "600003.SH": pd.DataFrame(
            [
                {"symbol": "600003.SH", "indicator": "总市值", "date": "2024-01-02", "value": 31.0},
            ]
        ),
    }

    result = run_strategy(
        service,
        "small_cap",
        symbols=["600001", "600002", "600003"],
        as_of="20240102",
    )

    assert result.results["symbol"].tolist() == ["600001.SH", "600002.SH"]
    assert "historical market cap" in result.results.loc[0, "reason"]


def test_undervalued_filters_by_pb_liquidity_and_debt_median() -> None:
    service = FakeStrategyService()
    service.snapshot = pd.DataFrame(
        [
            {"symbol": "600001.SH", "code": "600001", "name": "A", "pb": 1.0},
            {"symbol": "600002.SH", "code": "600002", "name": "B", "pb": 1.5},
            {"symbol": "600003.SH", "code": "600003", "name": "C", "pb": 1.8},
            {"symbol": "600004.SH", "code": "600004", "name": "D", "pb": 2.2},
        ]
    )
    service.financial = {
        "600001.SH": pd.DataFrame(
            [
                {
                    "date": "2024-12-31",
                    "current_ratio": 1.5,
                    "debt_asset_ratio": 60.0,
                    "total_assets": 1000.0,
                }
            ]
        ),
        "600002.SH": pd.DataFrame(
            [
                {
                    "date": "2024-12-31",
                    "current_ratio": 1.3,
                    "debt_asset_ratio": 40.0,
                    "total_assets": 1000.0,
                }
            ]
        ),
        "600003.SH": pd.DataFrame(
            [
                {
                    "date": "2024-12-31",
                    "current_ratio": 1.1,
                    "debt_asset_ratio": 80.0,
                    "total_assets": 1000.0,
                }
            ]
        ),
    }

    result = run_strategy(service, "undervalued", as_of="20260527")

    assert result.results["symbol"].tolist() == ["600001.SH"]
    assert "debt/assets 60.00%" in result.results.loc[0, "reason"]


def test_bank_rotation_selects_lowest_pb_index_member() -> None:
    service = FakeStrategyService()
    service.index_members = pd.DataFrame(
        [
            {
                "index_code": "399951",
                "symbol": "600001.SH",
                "code": "600001",
                "name": "A",
                "pb": 1.2,
            },
            {
                "index_code": "399951",
                "symbol": "600002.SH",
                "code": "600002",
                "name": "B",
                "pb": 0.7,
            },
        ]
    )

    result = run_strategy(service, "bank_rotation", as_of="20260527")

    assert result.results["symbol"].tolist() == ["600002.SH"]
    assert result.results["weight"].tolist() == [1.0]


def test_bank_rotation_can_use_explicit_symbol_universe() -> None:
    service = FakeStrategyService()
    service.financial = {
        "600001.SH": pd.DataFrame(
            [
                {"symbol": "600001.SH", "indicator": "市净率", "date": "2024-01-02", "value": 1.1},
            ]
        ),
        "600002.SH": pd.DataFrame(
            [
                {"symbol": "600002.SH", "indicator": "市净率", "date": "2024-01-02", "value": 0.6},
            ]
        ),
    }

    result = run_strategy(
        service,
        "bank_rotation",
        symbols=["600001", "600002"],
        as_of="20240102",
    )

    assert result.results["symbol"].tolist() == ["600002.SH"]
    assert result.results.loc[0, "date"] == "2024-01-02"


def test_strategy_requires_known_name() -> None:
    with pytest.raises(ValueError, match="strategy must be one of"):
        run_strategy(FakeStrategyService(), "does_not_exist")


def test_run_strategy_cli_rejects_missing_history_arguments() -> None:
    script = Path(__file__).resolve().parents[1] / "examples" / "run_strategy.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--strategy", "ma_cross"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "require one of --symbol, --symbols, or --all" in completed.stderr


def test_run_strategy_cli_requires_small_cap_universe() -> None:
    script = Path(__file__).resolve().parents[1] / "examples" / "run_strategy.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--strategy", "small_cap", "--as-of", "20240102"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "small_cap requires one of --symbol, --symbols, or --all" in completed.stderr
