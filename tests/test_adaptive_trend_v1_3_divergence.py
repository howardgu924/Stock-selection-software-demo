from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_picker.strategies.adaptive_trend_v1_3.divergence import (
    _build_signals,
    _classify_pair,
    _latest_signal,
    calculate_divergence,
    calculate_macd,
    find_swing_points,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase2_models import DivergenceType


def _history(days: int = 80) -> pd.DataFrame:
    close = 100.0 + np.linspace(0, 8, days) + np.sin(np.arange(days) / 3.0)
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2025-01-02", periods=days),
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
        }
    )


def _factor_history(days: int = 80) -> pd.DataFrame:
    frame = _history(days)
    frame["dif"] = 0.0
    frame["dea"] = 0.0
    frame["macd_hist"] = 0.0
    frame["atr20"] = 2.0
    return frame


def _pivots(frame: pd.DataFrame, kind: str, positions: list[int]) -> pd.DataFrame:
    rows = []
    for position in positions:
        usable = position + 4
        rows.append(
            {
                "kind": kind,
                "pivot_position": position,
                "pivot_date": frame.iloc[position]["date"],
                "confirmed_position": position + 3,
                "confirmed_date": frame.iloc[position + 3]["date"],
                "first_usable_position": usable,
                "first_usable_date": frame.iloc[usable]["date"] if usable < len(frame) else pd.NaT,
            }
        )
    return pd.DataFrame(rows)


def test_macd_matches_frozen_pandas_formula() -> None:
    close = pd.Series(np.linspace(10.0, 20.0, 60) + np.sin(np.arange(60)))
    result = calculate_macd(close)
    ema12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False, min_periods=9).mean()

    pd.testing.assert_series_equal(result["ema12"], ema12, check_names=False)
    pd.testing.assert_series_equal(result["ema26"], ema26, check_names=False)
    pd.testing.assert_series_equal(result["dif"], dif, check_names=False)
    pd.testing.assert_series_equal(result["dea"], dea, check_names=False)
    pd.testing.assert_series_equal(result["macd_hist"], 2.0 * (dif - dea), check_names=False)


def test_strict_three_by_three_pivots_and_confirmation_dates() -> None:
    frame = _history(12)
    frame["high"] = 10.0
    frame["low"] = 5.0
    frame.loc[4, "high"] = 20.0
    frame.loc[7, "low"] = 1.0

    pivots = find_swing_points(frame)

    high = pivots[(pivots["kind"] == "HIGH") & (pivots["pivot_position"] == 4)].iloc[0]
    low = pivots[(pivots["kind"] == "LOW") & (pivots["pivot_position"] == 7)].iloc[0]
    assert high["confirmed_position"] == 7
    assert high["first_usable_position"] == 8
    assert low["confirmed_position"] == 10
    assert low["first_usable_position"] == 11


def test_equal_flat_top_and_bottom_are_not_pivots() -> None:
    frame = _history(12)
    frame["high"] = 10.0
    frame["low"] = 5.0
    frame.loc[[4, 5], "high"] = 20.0
    frame.loc[[7, 8], "low"] = 1.0

    pivots = find_swing_points(frame)

    assert not pivots["pivot_position"].isin([4, 5, 7, 8]).any()


def test_first_usable_date_prevents_right_bar_lookahead() -> None:
    short = _factor_history(24)
    short.loc[10, ["high", "dif", "macd_hist"]] = [110.0, 2.0, 2.0]
    short.loc[20, ["high", "dif", "macd_hist"]] = [112.0, 1.0, 1.0]
    short_pivots = _pivots(short, "HIGH", [10, 20])

    long = _factor_history(25)
    long.loc[10, ["high", "dif", "macd_hist"]] = [110.0, 2.0, 2.0]
    long.loc[20, ["high", "dif", "macd_hist"]] = [112.0, 1.0, 1.0]
    long_pivots = _pivots(long, "HIGH", [10, 20])

    assert _build_signals(short, short_pivots) == []
    signal = _build_signals(long, long_pivots)[0]
    assert signal.first_usable_date == long.iloc[24]["date"].strftime("%Y-%m-%d")


def test_public_divergence_pipeline_does_not_backfill_before_first_usable_date() -> None:
    days = 90
    close = np.zeros(days)
    close[:41] = np.linspace(100.0, 140.0, 41)
    close[41:49] = np.linspace(138.0, 125.0, 8)
    close[49:61] = np.linspace(126.0, 142.0, 12)
    close[61:] = np.linspace(138.0, 130.0, days - 61)
    frame = pd.DataFrame(
        {
            "date": pd.bdate_range("2025-01-02", periods=days),
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
        }
    )

    before_usable = calculate_divergence(frame.iloc[:64])
    first_usable = calculate_divergence(frame.iloc[:65])
    expired = calculate_divergence(frame)

    assert before_usable.top_signal is None
    assert first_usable.top_signal is not None
    assert first_usable.top_signal.divergence_type == DivergenceType.TOP
    assert first_usable.top_signal.first_usable_date == frame.iloc[64]["date"].strftime("%Y-%m-%d")
    assert first_usable.top_signal.is_active is True
    assert expired.top_signal is not None
    assert expired.top_signal.is_active is False


@pytest.mark.parametrize(("gap", "expected"), [(5, True), (40, True), (4, False), (41, False)])
def test_pivot_interval_boundaries_are_inclusive_from_five_to_forty(gap, expected) -> None:
    frame = _factor_history(70)
    first, second = 5, 5 + gap
    frame.loc[first, ["high", "dif", "macd_hist"]] = [110.0, 2.0, 2.0]
    frame.loc[second, ["high", "dif", "macd_hist"]] = [112.0, 1.0, 1.0]

    signals = _build_signals(frame, _pivots(frame, "HIGH", [first, second]))

    assert bool(signals) is expected


def test_normal_top_strong_top_and_bottom_formulas() -> None:
    normal_top = _classify_pair(
        "HIGH",
        pd.Series({"high": 100.0, "dif": 2.0, "macd_hist": 2.0, "atr20": 4.0}),
        pd.Series({"high": 101.0, "dif": 1.0, "macd_hist": 3.0, "atr20": 4.0}),
    )
    strong_top = _classify_pair(
        "HIGH",
        pd.Series({"high": 100.0, "dif": 2.0, "macd_hist": 2.0, "atr20": 4.0}),
        pd.Series({"high": 102.0, "dif": 1.0, "macd_hist": 1.0, "atr20": 4.0}),
    )
    bottom = _classify_pair(
        "LOW",
        pd.Series({"low": 100.0, "dif": -2.0, "macd_hist": -2.0}),
        pd.Series({"low": 99.0, "dif": -1.0, "macd_hist": -1.0}),
    )

    assert normal_top == DivergenceType.TOP
    assert strong_top == DivergenceType.STRONG_TOP
    assert bottom == DivergenceType.BOTTOM


def test_pairing_uses_nearest_valid_first_pivot_without_skipping_for_stronger_signal() -> None:
    frame = _factor_history(50)
    frame.loc[5, ["high", "dif", "macd_hist"]] = [100.0, 3.0, 3.0]
    frame.loc[10, ["high", "dif", "macd_hist"]] = [120.0, 1.0, 1.0]
    frame.loc[20, ["high", "dif", "macd_hist"]] = [110.0, 2.0, 2.0]

    signals = _build_signals(frame, _pivots(frame, "HIGH", [5, 10, 20]))

    assert all(signal.pivot_2_date != frame.iloc[20]["date"].strftime("%Y-%m-%d") for signal in signals)


def test_twenty_trading_day_active_window_boundary() -> None:
    frame = _factor_history(39)
    frame.loc[5, ["high", "dif", "macd_hist"]] = [100.0, 2.0, 2.0]
    frame.loc[15, ["high", "dif", "macd_hist"]] = [102.0, 1.0, 1.0]
    pivots = _pivots(frame, "HIGH", [5, 15])

    active = _build_signals(frame, pivots)[0]
    expired_frame = pd.concat([frame, _factor_history(40).iloc[[-1]]], ignore_index=True)
    expired = _build_signals(expired_frame, _pivots(expired_frame, "HIGH", [5, 15]))[0]

    assert active.first_usable_date == frame.iloc[19]["date"].strftime("%Y-%m-%d")
    assert active.active_until == frame.iloc[38]["date"].strftime("%Y-%m-%d")
    assert active.is_active is True
    assert expired.is_active is False


def test_newer_same_type_signal_overrides_older_signal() -> None:
    frame = _factor_history(60)
    for position, high, dif, hist in (
        (5, 100.0, 3.0, 3.0),
        (15, 102.0, 2.0, 2.0),
        (25, 104.0, 1.0, 1.0),
    ):
        frame.loc[position, ["high", "dif", "macd_hist"]] = [high, dif, hist]
    signals = _build_signals(frame, _pivots(frame, "HIGH", [5, 15, 25]))

    latest = _latest_signal(signals, {DivergenceType.TOP, DivergenceType.STRONG_TOP})

    assert latest is not None
    assert latest.pivot_2_date == frame.iloc[25]["date"].strftime("%Y-%m-%d")


def test_invalid_daily_price_returns_stable_invalid_snapshot() -> None:
    frame = _history()
    frame.loc[10, "close"] = np.inf

    result = calculate_divergence(frame)

    assert result.status.value == "INVALID"
    assert result.invalid_reasons == (f"invalid_price:{frame.iloc[10]['date']:%Y-%m-%d}",)


def test_nonpositive_current_atr_returns_stable_invalid_snapshot() -> None:
    frame = _history()
    frame[["high", "low", "close"]] = 100.0

    result = calculate_divergence(frame)

    assert result.status.value == "INVALID"
    assert result.invalid_reasons == ("invalid_atr20:<=0",)
