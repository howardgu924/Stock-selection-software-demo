from __future__ import annotations

from collections import OrderedDict

import pandas as pd
import pytest

from stock_picker.strategies.adaptive_trend_v1_3 import (
    BENCHMARK_WEIGHTS,
    OPPORTUNITY_OUTPUT_COLUMNS,
    OpportunityInputError,
    abs_rs,
    calculate_opportunity_scores,
    calculate_weighted_benchmark_returns,
    score_close_ma20,
    score_ma20_ma60,
    score_ma60_slope,
    score_signed_er,
    score_signed_er_change,
    signed_er_series,
)


def _history(
    *,
    days: int = 220,
    daily_return: float = 0.001,
    start: float = 100.0,
    symbol: str = "600001.SH",
) -> pd.DataFrame:
    closes = [start * ((1.0 + daily_return) ** index) for index in range(days)]
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": pd.bdate_range("2025-01-02", periods=days),
            "open": [value * 0.999 for value in closes],
            "high": [value * 1.01 for value in closes],
            "low": [value * 0.99 for value in closes],
            "close": closes,
            "volume": [1_000_000.0] * days,
        }
    )


def _benchmarks(
    hs300: float = 0.0004,
    csi1000: float = 0.0002,
    chinext: float = 0.0001,
) -> dict[str, pd.DataFrame]:
    return {
        "000300.SH": _history(daily_return=hs300, symbol="000300.SH"),
        "000852.SH": _history(daily_return=csi1000, symbol="000852.SH"),
        "399006.SZ": _history(daily_return=chinext, symbol="399006.SZ"),
    }


@pytest.mark.parametrize(
    ("value", "lower", "upper", "expected"),
    [
        (-0.20, -0.10, 0.10, 0.0),
        (-0.10, -0.10, 0.10, 0.0),
        (-0.05, -0.10, 0.10, 0.15),
        (0.0, -0.10, 0.10, 0.30),
        (0.05, -0.10, 0.10, 0.65),
        (0.10, -0.10, 0.10, 1.0),
        (0.20, -0.10, 0.10, 1.0),
        (-0.15, -0.15, 0.20, 0.0),
        (-0.075, -0.15, 0.20, 0.15),
        (0.10, -0.15, 0.20, 0.65),
        (0.20, -0.15, 0.20, 1.0),
    ],
)
def test_abs_rs_all_intervals_and_endpoints(value, lower, upper, expected) -> None:
    assert abs_rs(value, lower, upper) == pytest.approx(expected)


def test_three_index_benchmark_uses_fixed_40_40_20_weights() -> None:
    result = calculate_weighted_benchmark_returns(_benchmarks())

    assert result["status"] == "VALID"
    assert result["benchmark_return20"] == pytest.approx(
        0.4 * result["hs300_return20"]
        + 0.4 * result["csi1000_return20"]
        + 0.2 * result["chinext_return20"]
    )
    assert result["benchmark_return60"] == pytest.approx(
        0.4 * result["hs300_return60"]
        + 0.4 * result["csi1000_return60"]
        + 0.2 * result["chinext_return60"]
    )


def test_missing_benchmark_is_invalid_without_weight_renormalization() -> None:
    benchmarks = _benchmarks()
    benchmarks.pop("399006.SZ")

    result = calculate_weighted_benchmark_returns(benchmarks)

    assert result["status"] == "INVALID"
    assert result["invalid_reason"] == "missing_benchmark:399006.SZ"


def test_rs_scoring_uses_only_abs_rs_when_pool_has_fewer_than_ten() -> None:
    histories = {
        f"60000{index}.SH": _history(daily_return=0.0005 + index * 0.0001)
        for index in range(1, 4)
    }

    result = calculate_opportunity_scores(histories, _benchmarks()).set_index("symbol")

    assert not result["rs_pool_ranking_applied"].any()
    assert result["rs20_percentile"].isna().all()
    assert result["rs60_percentile"].isna().all()
    for row in result.itertuples():
        assert row.rs20_score == pytest.approx(15.0 * row.abs_rs20)
        assert row.rs60_score == pytest.approx(25.0 * row.abs_rs60)


def test_rs_scoring_blends_abs_rs_and_percentile_when_pool_has_ten() -> None:
    histories = {
        f"600{index:03d}.SH": _history(daily_return=0.0003 + index * 0.0001)
        for index in range(10)
    }

    result = calculate_opportunity_scores(histories, _benchmarks())

    assert result["rs_pool_ranking_applied"].all()
    row = result.iloc[0]
    assert row["rs20_score"] == pytest.approx(
        15.0 * (0.70 * row["abs_rs20"] + 0.30 * row["rs20_percentile"])
    )
    assert row["rs60_score"] == pytest.approx(
        25.0 * (0.70 * row["abs_rs60"] + 0.30 * row["rs60_percentile"])
    )


def test_tied_rs_values_use_average_rank() -> None:
    histories = {
        "600001.SH": _history(daily_return=0.002),
        "600002.SH": _history(daily_return=0.002),
        **{
            f"600{index:03d}.SH": _history(daily_return=0.001 - index * 0.00003)
            for index in range(3, 11)
        },
    }

    result = calculate_opportunity_scores(histories, _benchmarks()).set_index("symbol")

    assert result.loc["600001.SH", "rs20_average_rank"] == pytest.approx(1.5)
    assert result.loc["600002.SH", "rs20_average_rank"] == pytest.approx(1.5)
    assert result.loc["600001.SH", "rs60_average_rank"] == pytest.approx(1.5)
    assert result.loc["600002.SH", "rs60_average_rank"] == pytest.approx(1.5)
    expected_percentile = (10 - 1.5) / 9
    assert result.loc["600001.SH", "rs20_percentile"] == pytest.approx(expected_percentile)
    assert result.loc["600002.SH", "rs60_percentile"] == pytest.approx(expected_percentile)
    assert result.loc["600001.SH", "rs20_score"] == pytest.approx(
        15.0
        * (
            0.70 * result.loc["600001.SH", "abs_rs20"]
            + 0.30 * expected_percentile
        )
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-2.0, 0.0), (-1.0, 0.0), (0.0, 5.0), (1.0, 10.0), (2.0, 10.0)],
)
def test_x1_score_boundaries_and_midpoint(value, expected) -> None:
    assert score_close_ma20(value) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-2.0, 0.0), (-1.0, 0.0), (0.0, 6.0), (1.5, 15.0), (2.0, 15.0)],
)
def test_x2_score_boundaries_and_midpoint(value, expected) -> None:
    assert score_ma20_ma60(value) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-1.0, 0.0), (-0.5, 0.0), (0.0, 10.0 / 3.0), (1.0, 10.0), (2.0, 10.0)],
)
def test_x3_score_boundaries_and_midpoint(value, expected) -> None:
    assert score_ma60_slope(value) == pytest.approx(expected)


def test_signed_er_denominator_zero_returns_zero() -> None:
    result = signed_er_series(pd.Series([10.0] * 30), period=20)

    assert result.iloc[-1] == 0.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-0.20, 0.0), (0.0, 3.75), (0.20, 7.50), (0.60, 15.0)],
)
def test_signed_er_score_spec_points(value, expected) -> None:
    assert score_signed_er(value) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-0.15, 0.0), (0.0, 5.0), (0.15, 10.0)],
)
def test_signed_er_change_score_spec_points(value, expected) -> None:
    assert score_signed_er_change(value) == pytest.approx(expected)


def test_opportunity_score_is_within_zero_and_one_hundred_and_has_all_fields() -> None:
    result = calculate_opportunity_scores(
        {"600001.SH": _history(daily_return=0.001)},
        _benchmarks(),
    )

    assert result.columns.tolist() == OPPORTUNITY_OUTPUT_COLUMNS
    assert result.loc[0, "status"] == "VALID"
    assert 0.0 <= result.loc[0, "opportunity_score"] <= 100.0
    assert result.loc[0, "opportunity_score"] == pytest.approx(
        result.loc[0, "rs20_score"]
        + result.loc[0, "rs60_score"]
        + result.loc[0, "close_ma20_score"]
        + result.loc[0, "ma20_ma60_score"]
        + result.loc[0, "ma60_slope_score"]
        + result.loc[0, "signed_er_score"]
        + result.loc[0, "signed_er_change_score"]
    )


@pytest.mark.parametrize(
    ("mutator", "reason_prefix"),
    [
        (lambda frame: frame.drop(columns=["high"]), "missing_required_field:high"),
        (
            lambda frame: frame.assign(close=10.0, high=10.0, low=10.0),
            "invalid_atr20:<=0",
        ),
        (
            lambda frame: frame.assign(
                close=frame["close"].where(frame.index != frame.index[-1], 0.0)
            ),
            "invalid_price:<=0",
        ),
    ],
)
def test_required_field_atr_and_price_validation(mutator, reason_prefix) -> None:
    history = mutator(_history())

    result = calculate_opportunity_scores({"600001.SH": history}, _benchmarks())

    assert result.loc[0, "status"] == "INVALID"
    assert result.loc[0, "invalid_reason"].startswith(reason_prefix)
    assert pd.isna(result.loc[0, "opportunity_score"])
    assert pd.isna(result.loc[0, "opportunity_rank"])


def test_199_days_is_invalid_and_200_days_is_valid() -> None:
    history = _history()
    result = calculate_opportunity_scores(
        {
            "600001.SH": history.tail(199).reset_index(drop=True),
            "600002.SH": history.tail(200).reset_index(drop=True),
        },
        _benchmarks(),
    ).set_index("symbol")

    assert result.loc["600001.SH", "status"] == "INVALID"
    assert result.loc["600001.SH", "invalid_reason"] == "insufficient_valid_history"
    assert result.loc["600002.SH", "status"] == "VALID"
    assert result.loc["600002.SH", "valid_history_days"] == 200


def test_candidate_input_order_does_not_change_stable_ranking() -> None:
    items = [
        (f"600{index:03d}.SH", _history(daily_return=0.0003 + index * 0.0001))
        for index in range(12)
    ]
    forward = OrderedDict(items)
    reverse = OrderedDict(reversed(items))

    first = calculate_opportunity_scores(forward, _benchmarks())
    second = calculate_opportunity_scores(reverse, _benchmarks())

    columns = [
        "symbol",
        "rs20_average_rank",
        "rs60_average_rank",
        "rs20_percentile",
        "rs60_percentile",
        "opportunity_score",
        "opportunity_rank",
    ]
    pd.testing.assert_frame_equal(first[columns], second[columns])


def test_opportunity_tie_uses_symbol_as_final_stable_tiebreaker() -> None:
    histories = {
        "600002.SH": _history(daily_return=0.001),
        "600001.SH": _history(daily_return=0.001),
    }

    result = calculate_opportunity_scores(histories, _benchmarks())

    assert result["symbol"].tolist() == ["600001.SH", "600002.SH"]
    assert result["opportunity_rank"].tolist() == [1.0, 2.0]


def test_candidate_missing_common_return_anchor_is_invalid() -> None:
    history = _history()
    anchor_t20 = _benchmarks()["000300.SH"].iloc[-21]["date"]
    history = history[history["date"].ne(anchor_t20)]

    result = calculate_opportunity_scores({"600001.SH": history}, _benchmarks())

    assert result.loc[0, "status"] == "INVALID"
    assert result.loc[0, "invalid_reason"] == f"missing_return_anchor:t20:{anchor_t20:%Y-%m-%d}"


def test_index_invalid_at_common_anchor_makes_benchmark_invalid() -> None:
    benchmarks = _benchmarks()
    anchor_index = benchmarks["000300.SH"].index[-21]
    anchor_date = benchmarks["000300.SH"].loc[anchor_index, "date"]
    benchmarks["000300.SH"].loc[anchor_index, "close"] = pd.NA

    result = calculate_weighted_benchmark_returns(benchmarks)

    assert result["status"] == "INVALID"
    assert result["invalid_reason"] == (
        f"invalid_return_anchor:000300.SH:{anchor_date:%Y-%m-%d}"
    )


def test_benchmark_returns_use_shared_calendar_when_index_dates_differ() -> None:
    benchmarks = _benchmarks()
    benchmarks["000300.SH"] = benchmarks["000300.SH"].drop(
        index=benchmarks["000300.SH"].index[-10]
    )
    common_dates = sorted(
        set(benchmarks["000300.SH"]["date"])
        & set(benchmarks["000852.SH"]["date"])
        & set(benchmarks["399006.SZ"]["date"])
    )
    t, t20, t60 = common_dates[-1], common_dates[-21], common_dates[-61]

    result = calculate_weighted_benchmark_returns(benchmarks)

    assert result["status"] == "VALID"
    for symbol, prefix in (
        ("000300.SH", "hs300"),
        ("000852.SH", "csi1000"),
        ("399006.SZ", "chinext"),
    ):
        by_date = benchmarks[symbol].set_index("date")["close"]
        assert result[f"{prefix}_return20"] == pytest.approx(by_date[t] / by_date[t20] - 1)
        assert result[f"{prefix}_return60"] == pytest.approx(by_date[t] / by_date[t60] - 1)


def test_missing_date_column_is_invalid() -> None:
    result = calculate_opportunity_scores(
        {"600001.SH": _history().drop(columns=["date"])},
        _benchmarks(),
    )

    assert result.loc[0, "status"] == "INVALID"
    assert result.loc[0, "invalid_reason"] == "missing_required_field:date"


def test_invalid_date_value_is_invalid() -> None:
    history = _history()
    history["date"] = history["date"].astype(object)
    history.loc[history.index[-1], "date"] = "not-a-date"

    result = calculate_opportunity_scores({"600001.SH": history}, _benchmarks())

    assert result.loc[0, "status"] == "INVALID"
    assert result.loc[0, "invalid_reason"] == "invalid_date_value"


def test_invalid_as_of_raises_stable_domain_error() -> None:
    with pytest.raises(OpportunityInputError, match="^invalid_as_of$"):
        calculate_opportunity_scores(
            {"600001.SH": _history()},
            _benchmarks(),
            as_of="not-a-date",
        )


def test_unsorted_dates_produce_the_same_result() -> None:
    history = _history()
    shuffled = history.sample(frac=1.0, random_state=42).reset_index(drop=True)

    ordered = calculate_opportunity_scores({"600001.SH": history}, _benchmarks())
    unordered = calculate_opportunity_scores({"600001.SH": shuffled}, _benchmarks())

    pd.testing.assert_frame_equal(ordered, unordered)


def test_identical_duplicate_date_is_deduplicated_deterministically() -> None:
    history = _history()
    duplicate = history.iloc[[-1]].copy()
    first_order = pd.concat([history, duplicate], ignore_index=True)
    second_order = pd.concat([duplicate, history], ignore_index=True)

    first = calculate_opportunity_scores({"600001.SH": first_order}, _benchmarks())
    second = calculate_opportunity_scores({"600001.SH": second_order}, _benchmarks())

    pd.testing.assert_frame_equal(first, second)
    assert first.loc[0, "status"] == "VALID"
    assert first.loc[0, "valid_history_days"] == 220


def test_conflicting_duplicate_date_is_invalid_in_any_input_order() -> None:
    history = _history()
    duplicate = history.iloc[[-1]].copy()
    duplicate.loc[:, ["high", "low", "close"]] *= 1.10
    conflict_date = history.iloc[-1]["date"]
    inputs = (
        pd.concat([history, duplicate], ignore_index=True),
        pd.concat([duplicate, history], ignore_index=True),
    )

    reasons = []
    for frame in inputs:
        result = calculate_opportunity_scores({"600001.SH": frame}, _benchmarks())
        assert result.loc[0, "status"] == "INVALID"
        reasons.append(result.loc[0, "invalid_reason"])

    assert reasons == [
        f"conflicting_duplicate_date:{conflict_date:%Y-%m-%d}",
        f"conflicting_duplicate_date:{conflict_date:%Y-%m-%d}",
    ]


@pytest.mark.parametrize("column", ["high", "low", "close"])
@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan"), "bad"])
def test_nonfinite_and_nonnumeric_current_prices_are_invalid(column, value) -> None:
    history = _history()
    if isinstance(value, str):
        history[column] = history[column].astype(object)
    history.loc[history.index[-1], column] = value

    result = calculate_opportunity_scores({"600001.SH": history}, _benchmarks())

    assert result.loc[0, "status"] == "INVALID"
    assert result.loc[0, "invalid_reason"].startswith("invalid_return_anchor:t:")


def test_high_below_low_is_not_a_valid_history_row() -> None:
    history = _history()
    history.loc[0, ["high", "low"]] = [9.0, 10.0]

    result = calculate_opportunity_scores({"600001.SH": history}, _benchmarks())

    assert result.loc[0, "status"] == "VALID"
    assert result.loc[0, "valid_history_days"] == 219


@pytest.mark.parametrize("value", [0.0, -1.0, float("inf"), float("-inf")])
def test_invalid_historical_price_does_not_count_toward_200_days(value) -> None:
    history = _history()
    history.loc[0, ["high", "low", "close"]] = value

    result = calculate_opportunity_scores({"600001.SH": history}, _benchmarks())

    assert result.loc[0, "status"] == "VALID"
    assert result.loc[0, "valid_history_days"] == 219


def test_cleaned_valid_history_199_and_200_day_boundary() -> None:
    histories = {}
    for invalid_count, symbol in ((21, "600001.SH"), (20, "600002.SH")):
        history = _history()
        history.loc[history.index[:invalid_count], ["high", "low", "close"]] = 0.0
        histories[symbol] = history

    result = calculate_opportunity_scores(histories, _benchmarks()).set_index("symbol")

    assert result.loc["600001.SH", "valid_history_days"] == 199
    assert result.loc["600001.SH", "invalid_reason"] == "insufficient_valid_history"
    assert result.loc["600002.SH", "valid_history_days"] == 200
    assert result.loc["600002.SH", "status"] == "VALID"


def test_normalized_symbol_collision_raises_stable_domain_error() -> None:
    with pytest.raises(
        OpportunityInputError,
        match=r"^duplicate_normalized_symbol:600001\.SH$",
    ):
        calculate_opportunity_scores(
            {"600001": _history(), "600001.SH": _history()},
            _benchmarks(),
        )


def test_ten_inputs_with_one_invalid_use_effective_pool_of_nine() -> None:
    histories = {
        f"600{index:03d}.SH": _history(daily_return=0.0003 + index * 0.0001)
        for index in range(10)
    }
    histories["600009.SH"] = _history(days=199)

    result = calculate_opportunity_scores(histories, _benchmarks())

    assert set(result["rs_pool_size"]) == {9}
    assert not result["rs_pool_ranking_applied"].any()
    assert result.loc[result["status"].eq("VALID"), "rs20_percentile"].isna().all()


def test_invalid_candidate_does_not_change_valid_percentiles_or_ranking() -> None:
    valid = {
        f"600{index:03d}.SH": _history(daily_return=0.0003 + index * 0.0001)
        for index in range(10)
    }
    with_invalid = {**valid, "601999.SH": _history(days=199)}

    baseline = calculate_opportunity_scores(valid, _benchmarks()).set_index("symbol")
    result = calculate_opportunity_scores(with_invalid, _benchmarks()).set_index("symbol")

    columns = [
        "rs20_average_rank",
        "rs60_average_rank",
        "rs20_percentile",
        "rs60_percentile",
        "rs20_score",
        "rs60_score",
        "opportunity_rank",
    ]
    pd.testing.assert_frame_equal(
        baseline[columns],
        result.loc[baseline.index, columns],
        check_dtype=False,
    )


def test_benchmark_weights_are_read_only() -> None:
    assert dict(BENCHMARK_WEIGHTS) == {
        "000300.SH": 0.40,
        "000852.SH": 0.40,
        "399006.SZ": 0.20,
    }
    with pytest.raises(TypeError):
        BENCHMARK_WEIGHTS["000300.SH"] = 0.0


def test_data_after_as_of_does_not_change_result() -> None:
    history = _history()
    cutoff = history.iloc[-6]["date"]
    changed = history.copy()
    changed.loc[changed["date"].gt(cutoff), ["high", "low", "close"]] *= 5.0
    benchmarks = _benchmarks()

    baseline = calculate_opportunity_scores(
        {"600001.SH": history}, benchmarks, as_of=cutoff
    )
    result = calculate_opportunity_scores(
        {"600001.SH": changed}, benchmarks, as_of=cutoff
    )

    pd.testing.assert_frame_equal(baseline, result)


def test_lagged_factors_use_cleaned_trading_sequence_positions() -> None:
    history = _history()
    history[["high", "low", "close"]] = history[
        ["high", "low", "close"]
    ].astype(object)
    history.loc[50, ["high", "low", "close"]] = "bad"

    result = calculate_opportunity_scores({"600001.SH": history}, _benchmarks()).iloc[0]
    cleaned = history.copy()
    cleaned[["high", "low", "close"]] = cleaned[["high", "low", "close"]].apply(
        pd.to_numeric, errors="coerce"
    )
    cleaned = cleaned.dropna(subset=["high", "low", "close"]).reset_index(drop=True)
    ma60 = cleaned["close"].rolling(60, min_periods=60).mean()
    signed_er = signed_er_series(cleaned["close"])

    assert result["status"] == "VALID"
    assert result["ma60_t_minus_10"] == pytest.approx(ma60.iloc[-11])
    assert result["signed_er20_t_minus_5"] == pytest.approx(signed_er.iloc[-6])


def test_output_contract_has_exactly_48_columns() -> None:
    assert len(OPPORTUNITY_OUTPUT_COLUMNS) == 48
