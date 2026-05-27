from __future__ import annotations

import builtins
from datetime import date

import pandas as pd
import pytest

from stock_picker.data.providers.joinquant_provider import JoinQuantProvider


class FakeJQDataSDK:
    def __init__(self) -> None:
        self.auth_calls: list[tuple[str, str]] = []
        self.price_calls: list[dict[str, object]] = []

    def auth(self, username: str, password: str) -> None:
        self.auth_calls.append((username, password))

    def get_query_count(self) -> dict[str, object]:
        return {"total": 1000, "spare": 900}

    def get_all_securities(self, types, date=None) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "display_name": "Kweichow Moutai",
                    "name": "Kweichow Moutai",
                    "start_date": "2001-08-27",
                    "end_date": "2200-01-01",
                    "type": "stock",
                },
                {
                    "display_name": "Ping An Bank",
                    "name": "Ping An Bank",
                    "start_date": "1991-04-03",
                    "end_date": "2200-01-01",
                    "type": "stock",
                },
            ],
            index=["600519.XSHG", "000001.XSHE"],
        )

    def get_trade_days(self, start_date, end_date) -> list[date]:
        return [date(2024, 1, 2), date(2024, 1, 3)]

    def get_price(self, **kwargs) -> pd.DataFrame:
        self.price_calls.append(kwargs)
        if kwargs["frequency"] == "daily":
            index = pd.to_datetime(["2024-01-02", "2024-01-03"])
        else:
            index = pd.to_datetime(["2024-01-02 09:35:00", "2024-01-02 09:40:00"])
        return pd.DataFrame(
            {
                "open": [10.0, 11.0],
                "high": [12.0, 12.5],
                "low": [9.5, 10.5],
                "close": [11.0, 12.0],
                "volume": [100.0, 200.0],
                "money": [1000.0, 2500.0],
            },
            index=index,
        )


class DateRangeLimitedJQDataSDK(FakeJQDataSDK):
    def get_price(self, **kwargs) -> pd.DataFrame:
        raise RuntimeError(
            "您的账号权限仅能获取2025-02-16至2026-02-23的数据，请调整时间参数后重试。"
        )
def test_missing_sdk_has_clear_error(monkeypatch) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "jqdatasdk":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="JQData SDK is not installed"):
        JoinQuantProvider(username="user", password="password")


def test_missing_credentials_has_clear_error(monkeypatch) -> None:
    monkeypatch.delenv("JQDATA_USERNAME", raising=False)
    monkeypatch.delenv("JQDATA_PASSWORD", raising=False)
    provider = JoinQuantProvider(sdk=FakeJQDataSDK())

    with pytest.raises(RuntimeError, match="JoinQuant credentials are required"):
        provider.get_query_count()


def test_stock_symbols_and_trade_dates_are_normalized() -> None:
    provider = JoinQuantProvider(
        username="user",
        password="password",
        sdk=FakeJQDataSDK(),
    )

    symbols = provider.get_stock_symbols()
    trade_dates = provider.get_trade_dates("20240102", "20240103")

    assert [item.symbol for item in symbols] == ["600519.SH", "000001.SZ"]
    assert [item.code for item in symbols] == ["600519", "000001"]
    assert trade_dates == ["2024-01-02", "2024-01-03"]


def test_daily_history_uses_get_price_and_normalizes_fields() -> None:
    sdk = FakeJQDataSDK()
    provider = JoinQuantProvider(username="user", password="password", sdk=sdk)

    frame = provider.get_history("600519", "20240102", "20240103")

    call = sdk.price_calls[-1]
    assert call["security"] == "600519.XSHG"
    assert call["frequency"] == "daily"
    assert call["fq"] == "pre"
    assert frame.columns.tolist() == [
        "symbol",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "amplitude",
        "pct_chg",
        "change",
        "turnover",
    ]
    assert frame["symbol"].tolist() == ["600519.SH", "600519.SH"]
    assert frame["date"].tolist() == ["2024-01-02", "2024-01-03"]
    assert frame["amount"].tolist() == [1000.0, 2500.0]
    assert frame["change"].iloc[1] == 1.0


def test_minute_history_uses_get_price_and_normalizes_fields() -> None:
    sdk = FakeJQDataSDK()
    provider = JoinQuantProvider(username="user", password="password", sdk=sdk)

    frame = provider.get_minute_history(
        "000001",
        "2024-01-02 09:30:00",
        "2024-01-02 10:00:00",
        period="5",
    )

    call = sdk.price_calls[-1]
    assert call["security"] == "000001.XSHE"
    assert call["frequency"] == "5m"
    assert frame["symbol"].tolist() == ["000001.SZ", "000001.SZ"]
    assert frame["datetime"].tolist() == [
        "2024-01-02 09:35:00",
        "2024-01-02 09:40:00",
    ]
    assert {"average_price", "price", "turnover"}.issubset(frame.columns)


def test_date_permission_error_explains_requested_and_allowed_ranges() -> None:
    provider = JoinQuantProvider(
        username="user",
        password="password",
        sdk=DateRangeLimitedJQDataSDK(),
    )

    with pytest.raises(RuntimeError) as exc_info:
        provider.get_history("600519", "20240101", "20240501")

    message = str(exc_info.value)
    assert "Requested: 2024-01-01 to 2024-05-01" in message
    assert "Allowed: 2025-02-16 to 2026-02-23" in message
    assert "Use the default data source" in message