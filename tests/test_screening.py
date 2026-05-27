from __future__ import annotations

import pandas as pd
import pytest

from stock_picker.data.models import StockInfo
from stock_picker.screening import screen_stocks
from stock_picker.screening.engine import available_rules


class FakeScreeningService:
    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames
        self.calls: list[str] = []

    def get_history(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        refresh: bool = False,
        indicators: bool = False,
    ) -> pd.DataFrame:
        self.calls.append(symbol)
        frame = self.frames[symbol]
        if not indicators:
            return frame
        return frame


def _frame(
    symbol: str,
    closes: list[float],
    volumes: list[float],
    macd_dif: list[float],
    macd_dea: list[float],
) -> pd.DataFrame:
    rows = []
    for index, close in enumerate(closes):
        rows.append(
            {
                "symbol": symbol,
                "date": f"2024-01-{index + 1:02d}",
                "open": close - 0.2,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": volumes[index],
                "amount": volumes[index] * close,
                "pct_chg": 1.0,
                "ma5": close - 0.1,
                "ma10": close - 0.2,
                "ma30": close - 1.0,
                "macd_dif": macd_dif[index],
                "macd_dea": macd_dea[index],
                "macd": (macd_dif[index] - macd_dea[index]) * 2,
            }
        )
    return pd.DataFrame(rows)


def test_screen_stocks_returns_matching_symbols_sorted() -> None:
    matching = _frame(
        "600519.SH",
        closes=[10.0] * 29 + [11.0, 12.0],
        volumes=[100.0] * 30 + [300.0],
        macd_dif=[-0.1] * 30 + [0.2],
        macd_dea=[0.0] * 31,
    )
    failing = _frame(
        "000001.SZ",
        closes=[10.0] * 31,
        volumes=[100.0] * 31,
        macd_dif=[-0.1] * 31,
        macd_dea=[0.0] * 31,
    )
    service = FakeScreeningService({"600519.SH": matching, "000001.SZ": failing})

    result = screen_stocks(
        service=service,
        symbols=[
            StockInfo(symbol="600519.SH", code="600519", name="Kweichow Moutai"),
            StockInfo(symbol="000001.SZ", code="000001", name="Ping An Bank"),
        ],
        start_date="20240101",
        end_date="20240131",
    )

    assert result.errors.empty
    assert result.results["symbol"].tolist() == ["600519.SH"]
    assert result.results.loc[0, "matched_rules"] == (
        "uptrend_20d,close_above_ma30,volume_up,macd_golden_cross,exclude_st"
    )
    assert result.results.loc[0, "score"] == 5


def test_screen_stocks_records_errors_when_provider_fails() -> None:
    service = FakeScreeningService({})

    result = screen_stocks(
        service=service,
        symbols=["600519"],
        start_date="20240101",
        end_date="20240131",
    )

    assert result.results.empty
    assert result.errors["symbol"].tolist() == ["600519.SH"]


def test_screen_stocks_rejects_unknown_rules() -> None:
    service = FakeScreeningService({})

    with pytest.raises(ValueError, match="Unknown screening rules"):
        screen_stocks(
            service=service,
            symbols=["600519"],
            start_date="20240101",
            end_date="20240131",
            rules=["does_not_exist"],
        )


def test_available_rules_lists_default_rule_names() -> None:
    assert "close_above_ma30" in available_rules()
    assert "volume_up" in available_rules()


def test_screening_uses_service_without_requiring_joinquant() -> None:
    frame = _frame(
        "600519.SH",
        closes=[10.0] * 31,
        volumes=[100.0] * 31,
        macd_dif=[-0.1] * 31,
        macd_dea=[0.0] * 31,
    )
    service = FakeScreeningService({"600519.SH": frame})

    result = screen_stocks(
        service=service,
        symbols=["600519"],
        start_date="20240101",
        end_date="20240131",
        rules=["close_above_ma30"],
    )

    assert service.calls == ["600519.SH"]
    assert result.results["symbol"].tolist() == ["600519.SH"]

def test_screen_stocks_can_sort_by_amount() -> None:
    low_amount = _frame(
        "600519.SH",
        closes=[10.0] * 29 + [11.0, 12.0],
        volumes=[100.0] * 30 + [300.0],
        macd_dif=[-0.1] * 30 + [0.2],
        macd_dea=[0.0] * 31,
    )
    high_amount = _frame(
        "000001.SZ",
        closes=[20.0] * 29 + [21.0, 22.0],
        volumes=[200.0] * 30 + [900.0],
        macd_dif=[-0.1] * 30 + [0.2],
        macd_dea=[0.0] * 31,
    )
    service = FakeScreeningService({"600519.SH": low_amount, "000001.SZ": high_amount})

    result = screen_stocks(
        service=service,
        symbols=["600519", "000001"],
        start_date="20240101",
        end_date="20240131",
        rules=["close_above_ma30", "volume_up"],
        sort_by="amount",
    )

    assert result.results["symbol"].tolist() == ["000001.SZ", "600519.SH"]


def test_exclude_st_rule_filters_stock_names() -> None:
    frame = _frame(
        "600519.SH",
        closes=[10.0] * 31,
        volumes=[100.0] * 31,
        macd_dif=[0.1] * 31,
        macd_dea=[0.0] * 31,
    )
    service = FakeScreeningService({"600519.SH": frame})

    result = screen_stocks(
        service=service,
        symbols=[StockInfo(symbol="600519.SH", code="600519", name="ST Example")],
        start_date="20240101",
        end_date="20240131",
        rules=["exclude_st"],
    )

    assert result.results.empty

def test_recent_three_day_year_high_and_volume_rules() -> None:
    closes = [10.0 + index * 0.01 for index in range(249)] + [15.0, 15.5, 16.0]
    volumes = [100.0] * 249 + [160.0, 170.0, 180.0]
    frame = _frame(
        "600519.SH",
        closes=closes,
        volumes=volumes,
        macd_dif=[0.1] * 252,
        macd_dea=[0.0] * 252,
    )
    frame["date"] = pd.date_range("2024-01-01", periods=252).strftime("%Y%m%d")
    service = FakeScreeningService({"600519.SH": frame})

    result = screen_stocks(
        service=service,
        symbols=[StockInfo(symbol="600519.SH", code="600519", name="Kweichow Moutai")],
        start_date="20240101",
        end_date="20241231",
        rules=["close_3d_252d_high", "volume_up_3d"],
    )

    assert result.results["symbol"].tolist() == ["600519.SH"]