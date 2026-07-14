from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_picker.strategies.adaptive_trend_v1_3.market_overlay import (
    MARKET_INDEX_WEIGHTS,
    calculate_market_overlay,
    effective_exposure_cap,
    score_index_factors,
    wilder_atr,
)


def _index_history(*, days: int = 90, daily_change: float = 0.10) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=days)
    close = 100.0 + np.arange(days) * daily_change
    return pd.DataFrame(
        {
            "date": dates,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
        }
    )


def _indexes() -> dict[str, pd.DataFrame]:
    return {
        "000300.SH": _index_history(daily_change=0.05),
        "000852.SH": _index_history(daily_change=0.10),
        "399006.SZ": _index_history(daily_change=-0.02),
    }


@pytest.mark.parametrize(
    ("factor", "value", "expected"),
    [
        ("index_price_score", -1.0, 0.0),
        ("index_price_score", 0.0, 50.0),
        ("index_price_score", 1.0, 100.0),
        ("index_structure_score", -1.0, 0.0),
        ("index_structure_score", 0.25, 50.0),
        ("index_structure_score", 1.5, 100.0),
        ("index_slope_score", -0.5, 0.0),
        ("index_slope_score", 0.25, 50.0),
        ("index_slope_score", 1.0, 100.0),
    ],
)
def test_index_factor_score_boundaries(factor, value, expected) -> None:
    inputs = {"x1": 0.0, "x2": 0.25, "x3": 0.25}
    inputs[{"index_price_score": "x1", "index_structure_score": "x2", "index_slope_score": "x3"}[factor]] = value

    result = score_index_factors(inputs["x1"], inputs["x2"], inputs["x3"])

    assert result[factor] == pytest.approx(expected)


def test_wilder_atr_seed_and_recursive_value() -> None:
    high = pd.Series([11.0] * 20 + [12.0])
    low = pd.Series([9.0] * 21)
    close = pd.Series([10.0] * 21)

    result = wilder_atr(high, low, close)

    assert result.iloc[18] != result.iloc[18]
    assert result.iloc[19] == pytest.approx(2.0)
    assert result.iloc[20] == pytest.approx((2.0 * 19 + 3.0) / 20)


def test_three_index_raw_market_score_uses_fixed_weights() -> None:
    result = calculate_market_overlay(_indexes())
    row = result[result["status"].eq("VALID")].iloc[-1]

    expected = (
        0.40 * row["hs300_score"]
        + 0.40 * row["csi1000_score"]
        + 0.20 * row["chinext_score"]
    )
    assert row["raw_market_score"] == pytest.approx(expected)
    assert dict(MARKET_INDEX_WEIGHTS) == {
        "000300.SH": 0.40,
        "000852.SH": 0.40,
        "399006.SZ": 0.20,
    }


def test_missing_index_is_invalid_without_weight_renormalization() -> None:
    histories = _indexes()
    histories.pop("399006.SZ")

    result = calculate_market_overlay(histories)

    assert not result.empty
    assert result["status"].eq("INVALID").all()
    assert all("missing_index:399006.SZ" in reasons for reasons in result["invalid_reasons"])
    assert result["raw_market_score"].isna().all()


def test_ema_initial_second_and_intermediate_invalid_does_not_update_state() -> None:
    histories = _indexes()
    invalid_date = histories["000852.SH"].iloc[75]["date"]
    histories["000852.SH"].loc[75, "close"] = np.nan

    result = calculate_market_overlay(histories)
    invalid_position = result.index[result["date"].eq(invalid_date.strftime("%Y-%m-%d"))][0]
    previous_valid = result.loc[: invalid_position - 1]
    previous_valid = previous_valid[previous_valid["status"].eq("VALID")].iloc[-1]
    next_valid = result.loc[invalid_position + 1 :]
    next_valid = next_valid[next_valid["status"].eq("VALID")].iloc[0]
    first_valid = result[result["status"].eq("VALID")].iloc[0]

    assert first_valid["smoothed_market_score"] == pytest.approx(first_valid["raw_market_score"])
    assert result.loc[invalid_position, "status"] == "INVALID"
    assert pd.isna(result.loc[invalid_position, "smoothed_market_score"])
    assert next_valid["smoothed_market_score"] == pytest.approx(
        next_valid["raw_market_score"] / 3.0
        + previous_valid["smoothed_market_score"] * 2.0 / 3.0
    )


def test_entry_threshold_and_exposure_cap_use_unrounded_smoothed_score() -> None:
    row = calculate_market_overlay(_indexes()).query("status == 'VALID'").iloc[0]
    smoothed = row["smoothed_market_score"]

    assert row["entry_threshold"] == pytest.approx(70.0 + 8.0 * (1.0 - smoothed / 100.0))
    assert row["raw_exposure_cap"] == pytest.approx(0.90 * smoothed / 100.0)
    assert row["effective_exposure_cap"] == pytest.approx(row["raw_exposure_cap"])


def test_effective_exposure_cap_normal_decline_limit_and_emergency_bypass() -> None:
    assert effective_exposure_cap(0.30, 0.70) == pytest.approx(0.60)
    assert effective_exposure_cap(0.65, 0.70) == pytest.approx(0.65)
    assert effective_exposure_cap(0.30, 0.70, remove_drop_limit=True) == pytest.approx(0.30)


def test_missing_middle_index_date_is_invalid_and_later_ema_continues() -> None:
    histories = _indexes()
    missing_date = histories["000300.SH"].iloc[75]["date"]
    histories["000300.SH"] = histories["000300.SH"].drop(index=75)

    result = calculate_market_overlay(histories)
    row = result[result["date"].eq(missing_date.strftime("%Y-%m-%d"))].iloc[0]

    assert row["status"] == "INVALID"
    assert f"missing_index_date:000300.SH:{missing_date:%Y-%m-%d}" in row["invalid_reasons"]
    assert result.loc[result.index > row.name, "status"].eq("VALID").any()


def test_conflicting_duplicate_index_date_is_stable_invalid() -> None:
    histories = _indexes()
    duplicate = histories["000300.SH"].iloc[[-1]].copy()
    duplicate.loc[:, "close"] += 1.0
    conflict_date = duplicate.iloc[0]["date"]
    histories["000300.SH"] = pd.concat(
        [duplicate, histories["000300.SH"]], ignore_index=True
    )

    result = calculate_market_overlay(histories)

    reason = f"conflicting_duplicate_date:{conflict_date:%Y-%m-%d}"
    assert result["status"].eq("INVALID").all()
    assert all(reason in reasons for reasons in result["invalid_reasons"])


def test_nonpositive_wilder_atr_is_stable_invalid() -> None:
    histories = _indexes()
    for frame in histories.values():
        frame[["high", "low", "close"]] = 100.0

    result = calculate_market_overlay(histories)
    late_row = result.iloc[-1]

    assert late_row["status"] == "INVALID"
    assert any(reason.startswith("invalid_atr20:") for reason in late_row["invalid_reasons"])


def test_market_overlay_as_of_is_inclusive_and_future_data_isolated() -> None:
    histories = _indexes()
    cutoff = histories["000300.SH"].iloc[80]["date"]
    changed = {symbol: frame.copy() for symbol, frame in histories.items()}
    for frame in changed.values():
        frame.loc[frame["date"].gt(cutoff), ["high", "low", "close"]] *= 10.0

    baseline = calculate_market_overlay(histories, as_of=cutoff)
    result = calculate_market_overlay(changed, as_of=cutoff)

    pd.testing.assert_frame_equal(baseline, result)
    assert result.iloc[-1]["date"] == cutoff.strftime("%Y-%m-%d")
