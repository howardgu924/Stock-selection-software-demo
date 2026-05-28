from __future__ import annotations

from pathlib import Path
import tempfile
from uuid import uuid4

import pandas as pd

from stock_picker.data.models import StockInfo
from stock_picker.data.providers import AkShareProvider
from stock_picker.data.service import DataSourceConfig, DataSourceError, MarketDataService
from stock_picker.data.storage import SQLiteMarketDataStore


def workspace_path(name: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=f"{name}-{uuid4().hex}-"))


class FakeHistoryProvider:
    def __init__(
        self,
        trade_dates: list[str] | None = None,
        failing_symbols: set[str] | None = None,
        fail_trade_dates: bool = False,
    ) -> None:
        self.trade_dates = trade_dates or []
        self.failing_symbols = failing_symbols or set()
        self.fail_trade_dates = fail_trade_dates
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
        if self.fail_trade_dates:
            raise RuntimeError("trade calendar unavailable")
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


class FakeMarketProvider(FakeStockProvider):
    def get_market_snapshot(self, symbols=None) -> pd.DataFrame:
        frame = pd.DataFrame(
            [
                {
                    "symbol": "600519.SH",
                    "code": "600519",
                    "name": "Kweichow Moutai",
                    "price": 10.0,
                    "pct_chg": 1.0,
                    "change": 0.1,
                    "volume": 100.0,
                    "amount": 1000.0,
                    "high": 11.0,
                    "low": 9.0,
                    "open": 10.0,
                    "prev_close": 9.9,
                    "turnover": 0.5,
                    "market_cap": 2_500_000_000.0,
                    "pe_dynamic": 20.0,
                    "pb": 1.5,
                },
                {
                    "symbol": "000001.SZ",
                    "code": "000001",
                    "name": "Ping An Bank",
                    "price": 8.0,
                    "pct_chg": 0.5,
                    "change": 0.04,
                    "volume": 200.0,
                    "amount": 1600.0,
                    "high": 8.2,
                    "low": 7.8,
                    "open": 7.9,
                    "prev_close": 7.96,
                    "turnover": 0.7,
                    "market_cap": 3_000_000_000.0,
                    "pe_dynamic": 10.0,
                    "pb": 0.8,
                },
            ]
        )
        if symbols:
            wanted = set(symbols)
            frame = frame[frame["symbol"].isin(wanted)]
        return frame.reset_index(drop=True)

    def get_financial_indicators(
        self,
        symbol: str,
        start_year: str = "1900",
    ) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "symbol": symbol,
                    "date": "2024-12-31",
                    "current_ratio": 1.5,
                    "debt_asset_ratio": 60.0,
                    "total_assets": 1000.0,
                }
            ]
        )

    def get_valuation_history(
        self,
        symbol: str,
        indicator: str = "总市值",
        period: str = "近一年",
    ) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "symbol": symbol,
                    "indicator": indicator,
                    "date": "2024-12-31",
                    "value": 25.0,
                }
            ]
        )

    def get_index_members(self, index_code: str) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "index_code": index_code,
                    "symbol": "600519.SH",
                    "code": "600519",
                    "name": "Kweichow Moutai",
                    "weight": 10.0,
                },
                {
                    "index_code": index_code,
                    "symbol": "000001.SZ",
                    "code": "000001",
                    "name": "Ping An Bank",
                    "weight": 8.0,
                },
            ]
        )

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
                    "date": "2024-01-02",
                    "open": 100.0,
                    "close": 101.0,
                    "high": 102.0,
                    "low": 99.0,
                    "volume": 1.0,
                    "amount": 1.0,
                    "amplitude": None,
                    "pct_chg": None,
                    "change": None,
                    "turnover": None,
                }
            ]
        )

    def get_minute_history(
        self,
        symbol: str,
        start_datetime: str,
        end_datetime: str,
        period: str = "5",
        adjust: str = "",
    ) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "symbol": "600519.SH",
                    "datetime": start_datetime,
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "volume": 100.0,
                    "amount": 1000.0,
                    "average_price": None,
                    "price": None,
                    "amplitude": 2.0,
                    "pct_chg": 1.0,
                    "change": 0.1,
                    "turnover": 0.5,
                }
            ]
        )

    def get_boards(self, board_type: str) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "board_type": board_type,
                    "rank": 1,
                    "name": "Semiconductor",
                    "code": "BK1036",
                    "price": 100.0,
                    "change": 1.0,
                    "pct_chg": 1.2,
                    "market_cap": 1000000.0,
                    "turnover": 2.0,
                    "up_count": 30,
                    "down_count": 10,
                    "leader": "Example Stock",
                    "leader_pct_chg": 10.0,
                }
            ]
        )

    def get_board_members(self, board_type: str, board: str) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "board_type": board_type,
                    "board": board,
                    "symbol": "600519.SH",
                    "rank": 1,
                    "code": "600519",
                    "name": "Kweichow Moutai",
                    "price": 10.0,
                    "pct_chg": 1.0,
                    "change": 0.1,
                    "volume": 100.0,
                    "amount": 1000.0,
                    "amplitude": 2.0,
                    "high": 11.0,
                    "low": 9.0,
                    "open": 10.0,
                    "prev_close": 9.9,
                    "turnover": 0.5,
                    "pe_dynamic": 20.0,
                    "pb": 3.0,
                }
            ]
        )

    def get_board_minute_history(
        self,
        board_type: str,
        board: str,
        period: str = "5",
    ) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "board_type": board_type,
                    "board": board,
                    "datetime": "2024-01-02 09:35:00",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "volume": 100.0,
                    "amount": 1000.0,
                    "average_price": None,
                    "price": None,
                    "amplitude": 2.0,
                    "pct_chg": 1.0,
                    "change": 0.1,
                    "turnover": 0.5,
                }
            ]
        )


class FakeRealtimeProvider:
    def get_realtime_quotes(self, symbols=None) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "symbol": "600519.SH",
                    "name": "Kweichow Moutai",
                    "price": 10.0,
                    "pct_chg": 1.0,
                    "change": 0.1,
                    "volume": 100.0,
                    "amount": 1000.0,
                    "high": 11.0,
                    "low": 9.0,
                    "open": 10.0,
                    "prev_close": 9.9,
                    "turnover": 0.5,
                }
            ]
        )


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


def test_get_history_returns_cached_rows_when_trade_calendar_fails() -> None:
    tmp_path = workspace_path("cached-history-calendar-failure")
    store = SQLiteMarketDataStore(tmp_path / "market.sqlite3")
    seed_provider = FakeHistoryProvider(trade_dates=["2024-01-02", "2024-01-03"])
    store.save_history(seed_provider.get_history("600519", "2024-01-02", "2024-01-03"))
    provider = FakeHistoryProvider(fail_trade_dates=True)
    service = MarketDataService(
        history_provider=provider,
        stock_provider=FakeStockProvider(),
        store=store,
    )

    frame = service.get_history("600519", "20240102", "20240103")

    assert provider.history_calls == []
    assert frame["date"].tolist() == ["2024-01-02", "2024-01-03"]


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


def test_get_history_can_add_technical_indicators() -> None:
    tmp_path = workspace_path("history-indicators")
    store = SQLiteMarketDataStore(tmp_path / "market.sqlite3")
    provider = FakeHistoryProvider(
        trade_dates=[f"2024-01-{day:02d}" for day in range(2, 32)]
    )
    service = MarketDataService(
        history_provider=provider,
        stock_provider=FakeStockProvider(),
        store=store,
    )

    frame = service.get_history(
        "600519",
        "20240102",
        "20240131",
        indicators=True,
    )

    assert {"ma5", "ma10", "ma30", "macd_dif", "macd_dea", "macd"}.issubset(
        frame.columns
    )
    assert frame["ma5"].iloc[3] != frame["ma5"].iloc[3]
    assert frame["ma5"].iloc[4] == 1.0


def test_market_provider_minute_and_board_methods_are_exposed() -> None:
    service = MarketDataService(
        history_provider=FakeHistoryProvider(),
        stock_provider=FakeStockProvider(),
        market_provider=FakeMarketProvider(),
    )

    minute = service.get_minute_history(
        "600519",
        "2024-01-02 09:30:00",
        "2024-01-02 15:00:00",
    )
    boards = service.get_boards("industry")
    members = service.get_board_members("industry", "BK1036")
    board_minute = service.get_board_minute_history("industry", "BK1036")
    snapshot = service.get_market_snapshot(symbols=["600519.SH"])
    financial = service.get_financial_indicators("600519")
    index_members = service.get_index_members("399951")
    valuation = service.get_valuation_history("600519")
    index_history = service.get_index_history("000001", "20240102", "20240102")

    assert minute["symbol"].tolist() == ["600519.SH"]
    assert boards["code"].tolist() == ["BK1036"]
    assert members["symbol"].tolist() == ["600519.SH"]
    assert board_minute["board"].tolist() == ["BK1036"]
    assert snapshot["symbol"].tolist() == ["600519.SH"]
    assert financial["current_ratio"].tolist() == [1.5]
    assert index_members["symbol"].tolist() == ["600519.SH", "000001.SZ"]
    assert valuation["value"].tolist() == [25.0]
    assert index_history["close"].tolist() == [101.0]


def test_explicit_source_can_fallback_and_record_actual_source(monkeypatch) -> None:
    tmp_path = workspace_path("source-fallback")
    primary = FakeHistoryProvider(failing_symbols={"600519"})
    fallback = FakeHistoryProvider(trade_dates=["2024-01-02"])
    monkeypatch.setattr(
        MarketDataService,
        "PROVIDER_FACTORIES",
        {
            **MarketDataService.PROVIDER_FACTORIES,
            "joinquant": lambda: primary,
            "baostock": lambda: fallback,
        },
    )
    service = MarketDataService(
        realtime_provider=FakeRealtimeProvider(),
        stock_provider=FakeStockProvider(),
        market_provider=FakeMarketProvider(),
        store=SQLiteMarketDataStore(tmp_path / "market.sqlite3"),
        data_source_config=DataSourceConfig(
            history_source="joinquant",
            fallback_sources={"history": ["baostock"]},
        ),
    )

    frame = service.get_history("600519", "20240102", "20240102")

    result = service.last_source_results["history"]
    assert frame["symbol"].tolist() == ["600519.SH"]
    assert result.source == "baostock"
    assert result.fallback_from == "joinquant"
    assert "joinquant: provider unavailable" in result.fallback_errors


def test_default_history_source_can_fallback_to_joinquant(monkeypatch) -> None:
    tmp_path = workspace_path("source-fallback-joinquant")
    primary = FakeHistoryProvider(failing_symbols={"600519"})
    fallback = FakeHistoryProvider(trade_dates=["2024-01-02"])
    monkeypatch.setattr(
        MarketDataService,
        "PROVIDER_FACTORIES",
        {
            **MarketDataService.PROVIDER_FACTORIES,
            "baostock": lambda: primary,
            "joinquant": lambda: fallback,
        },
    )
    service = MarketDataService(
        realtime_provider=FakeRealtimeProvider(),
        stock_provider=FakeStockProvider(),
        market_provider=FakeMarketProvider(),
        store=SQLiteMarketDataStore(tmp_path / "market.sqlite3"),
        data_source_config=DataSourceConfig(
            history_source="baostock",
            fallback_sources={"history": ["joinquant"]},
        ),
    )

    frame = service.get_history("600519", "20240102", "20240102")

    result = service.last_source_results["history"]
    assert frame["symbol"].tolist() == ["600519.SH"]
    assert result.source == "joinquant"
    assert result.fallback_from == "baostock"
    assert "baostock: provider unavailable" in result.fallback_errors


def test_eastmoney_request_context_bypasses_broken_environment_proxy(monkeypatch) -> None:
    import requests

    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = "{}"

    def fake_request(session, method, url, **kwargs):
        captured["trust_env_during_request"] = session.trust_env
        captured["headers"] = kwargs.get("headers")
        return FakeResponse()

    monkeypatch.setattr(requests.sessions.Session, "request", fake_request)

    with AkShareProvider._eastmoney_request_context():
        session = requests.Session()
        session.trust_env = True
        response = session.get("https://82.push2.eastmoney.com/api/test")

    assert response.status_code == 200
    assert captured["trust_env_during_request"] is False
    assert captured["headers"]["Referer"] == "https://quote.eastmoney.com/"
    assert session.trust_env is True


def test_eastmoney_request_context_can_keep_environment_proxy(monkeypatch) -> None:
    import requests

    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

    def fake_request(session, method, url, **kwargs):
        captured["trust_env_during_request"] = session.trust_env
        return FakeResponse()

    monkeypatch.setattr(requests.sessions.Session, "request", fake_request)
    monkeypatch.setenv("STOCK_PICKER_EASTMONEY_TRUST_ENV", "1")

    with AkShareProvider._eastmoney_request_context():
        session = requests.Session()
        session.trust_env = True
        session.get("https://82.push2.eastmoney.com/api/test")

    assert captured["trust_env_during_request"] is True
def test_explicit_source_failure_lists_fallback_errors(monkeypatch) -> None:
    primary = FakeHistoryProvider(failing_symbols={"600519"})
    fallback = FakeHistoryProvider(failing_symbols={"600519"})
    monkeypatch.setattr(
        MarketDataService,
        "PROVIDER_FACTORIES",
        {
            **MarketDataService.PROVIDER_FACTORIES,
            "joinquant": lambda: primary,
            "baostock": lambda: fallback,
        },
    )
    service = MarketDataService(
        realtime_provider=FakeRealtimeProvider(),
        stock_provider=FakeStockProvider(),
        market_provider=FakeMarketProvider(),
        data_source_config=DataSourceConfig(
            history_source="joinquant",
            fallback_sources={"history": ["baostock"]},
        ),
    )

    try:
        service.get_history("600519", "20240102", "20240102")
    except DataSourceError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected DataSourceError")

    assert "Selected source: joinquant" in message
    assert "Fallback sources: baostock" in message
    assert "joinquant: provider unavailable" in message
    assert "baostock: provider unavailable" in message


def test_unsupported_source_for_feature_has_clear_error() -> None:
    try:
        MarketDataService(
            history_provider=FakeHistoryProvider(),
            stock_provider=FakeStockProvider(),
            market_provider=FakeMarketProvider(),
            data_source_config=DataSourceConfig(realtime_source="joinquant"),
        )
    except DataSourceError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected DataSourceError")

    assert "joinquant does not support realtime data" in message


def test_akshare_minute_normalization_accepts_source_columns() -> None:
    raw = pd.DataFrame(
        [
            {
                "时间": "2024-01-02 09:35:00",
                "开盘": 10.0,
                "收盘": 10.5,
                "最高": 11.0,
                "最低": 9.0,
                "成交量": 100.0,
                "成交额": 1000.0,
                "振幅": 2.0,
                "涨跌幅": 1.0,
                "涨跌额": 0.1,
                "换手率": 0.5,
            }
        ]
    )

    frame = AkShareProvider._normalize_minute_frame(raw, symbol="600519.SH")

    assert frame.columns.tolist() == [
        "symbol",
        "datetime",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "average_price",
        "price",
        "amplitude",
        "pct_chg",
        "change",
        "turnover",
    ]
    assert frame.loc[0, "symbol"] == "600519.SH"
    assert frame.loc[0, "datetime"] == "2024-01-02 09:35:00"
