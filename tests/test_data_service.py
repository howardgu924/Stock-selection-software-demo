from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pandas as pd

from stock_picker.data.models import StockInfo
from stock_picker.data.service import MarketDataService
from stock_picker.data.storage import SQLiteMarketDataStore


def workspace_path(name: str) -> Path:
    path = Path(".test_tmp") / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


class FakeHistoryProvider:
    def __init__(
        self,
        trade_dates: list[str] | None = None,
        failing_symbols: set[str] | None = None,
    ) -> None:
        self.trade_dates = trade_dates or []
        self.failing_symbols = failing_symbols or set()
        self.history_calls: list[tuple[str, str, str]] = []

    def get_history(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str = "daily",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        if symbol in self.failing_symbols:
            raise RuntimeError("provider unavailable")
        self.history_calls.append((symbol, start_date, end_date))
        dates = [
            date
            for date in self.trade_dates
            if self._date_for_query(start_date) <= date <= self._date_for_query(end_date)
        ]
        return pd.DataFrame(
            [
                {
                    "symbol": self._normalize(symbol),
                    "date": date,
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": 1.0,
                    "volume": 1.0,
                    "amount": 1.0,
                    "amplitude": None,
                    "pct_chg": None,
                    "change": None,
                    "turnover": None,
                }
                for date in dates
            ]
        )

    def get_trade_dates(self, start_date: str, end_date: str) -> list[str]:
        start = self._date_for_query(start_date)
        end = self._date_for_query(end_date)
        return [date for date in self.trade_dates if start <= date <= end]

    def get_stock_symbols(self) -> list[StockInfo]:
        return [
            StockInfo.from_code_name("600519", "Kweichow Moutai"),
            StockInfo.from_code_name("000001", "Ping An Bank"),
        ]

    @staticmethod
    def _date_for_query(value: str) -> str:
        if "-" in value:
            return value
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"

    @staticmethod
    def _normalize(symbol: str) -> str:
        return f"{symbol}.SH" if symbol.startswith("6") else f"{symbol}.SZ"


class FakeStockProvider:
    def __init__(self, should_fail: bool = False) -> None:
        self.calls = 0
        self.should_fail = should_fail

    def get_stock_symbols(self) -> list[StockInfo]:
        self.calls += 1
        if self.should_fail:
            raise RuntimeError("stock list source failed")
        return [
            StockInfo.from_code_name("600519", "Kweichow Moutai"),
            StockInfo.from_code_name("000001", "Ping An Bank"),
        ]


def test_get_stock_symbols_uses_cache() -> None:
    tmp_path = workspace_path("stock-symbol-cache")
    store = SQLiteMarketDataStore(tmp_path / "market.sqlite3")
    stock_provider = FakeStockProvider()
    service = MarketDataService(
        history_provider=FakeHistoryProvider(),
        stock_provider=stock_provider,
        store=store,
    )

    first = service.get_stock_symbols()
    second = service.get_stock_symbols()

    assert [item.symbol for item in first] == ["600519.SH", "000001.SZ"]
    assert [item.symbol for item in second] == ["000001.SZ", "600519.SH"]
    assert stock_provider.calls == 1


def test_get_stock_symbols_falls_back_to_history_provider() -> None:
    tmp_path = workspace_path("stock-symbol-fallback")
    store = SQLiteMarketDataStore(tmp_path / "market.sqlite3")
    service = MarketDataService(
        history_provider=FakeHistoryProvider(),
        stock_provider=FakeStockProvider(should_fail=True),
        store=store,
    )

    symbols = service.get_stock_symbols(refresh=True)

    assert [item.symbol for item in symbols] == ["600519.SH", "000001.SZ"]


def test_get_history_fetches_only_missing_trade_dates() -> None:
    tmp_path = workspace_path("missing-history")
    store = SQLiteMarketDataStore(tmp_path / "market.sqlite3")
    provider = FakeHistoryProvider(
        trade_dates=["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    )
    store.save_history(provider.get_history("600519", "2024-01-02", "2024-01-02"))
    provider.history_calls.clear()

    service = MarketDataService(
        history_provider=provider,
        stock_provider=FakeStockProvider(),
        store=store,
    )

    frame = service.get_history("600519", "20240102", "20240105")

    assert provider.history_calls == [("600519", "2024-01-03", "2024-01-05")]
    assert frame["date"].tolist() == [
        "2024-01-02",
        "2024-01-03",
        "2024-01-04",
        "2024-01-05",
    ]


def test_update_history_skips_failures_and_logs_errors() -> None:
    tmp_path = workspace_path("history-errors")
    store = SQLiteMarketDataStore(tmp_path / "market.sqlite3")
    provider = FakeHistoryProvider(
        trade_dates=["2024-01-02"],
        failing_symbols={"000001"},
    )
    service = MarketDataService(
        history_provider=provider,
        stock_provider=FakeStockProvider(),
        store=store,
    )
    error_log = tmp_path / "history_errors.csv"

    summary = service.update_history(
        symbols=["600519", "000001"],
        start_date="20240102",
        end_date="20240102",
        error_log_path=error_log,
    )

    assert summary["status"].tolist() == ["ok", "failed"]
    assert "provider unavailable" in error_log.read_text()
