from __future__ import annotations

import pandas as pd


PRICE_MA_WINDOWS = (5, 10, 30)
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9


def add_technical_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return _empty_with_indicator_columns(frame)

    result = frame.copy()
    result["close"] = pd.to_numeric(result["close"], errors="coerce")

    grouped_close = result.groupby("symbol", sort=False)["close"]
    for window in PRICE_MA_WINDOWS:
        result[f"ma{window}"] = grouped_close.transform(
            lambda series: series.rolling(window=window).mean()
        )

    ema_fast = grouped_close.transform(
        lambda series: series.ewm(span=MACD_FAST, adjust=False).mean()
    )
    ema_slow = grouped_close.transform(
        lambda series: series.ewm(span=MACD_SLOW, adjust=False).mean()
    )
    result["macd_dif"] = ema_fast - ema_slow
    result["macd_dea"] = result.groupby("symbol", sort=False)["macd_dif"].transform(
        lambda series: series.ewm(span=MACD_SIGNAL, adjust=False).mean()
    )
    result["macd"] = (result["macd_dif"] - result["macd_dea"]) * 2

    return result


def _empty_with_indicator_columns(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in _indicator_columns():
        result[column] = pd.Series(dtype="float64")
    return result


def _indicator_columns() -> list[str]:
    return [
        *(f"ma{window}" for window in PRICE_MA_WINDOWS),
        "macd_dif",
        "macd_dea",
        "macd",
    ]
