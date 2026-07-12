from __future__ import annotations

import pandas as pd
import pytest

from stock_picker.strategies.thermostat import (
    MARKET_POSITION_DISCOUNTS,
    PENDING_SELL_LEVELS,
    REQUIRED_TRIGGER_PLAN_COLUMNS,
    STOCK_MODES,
    TRIGGER_TYPES,
    _grid_trigger_levels,
    _trigger_indicators,
    calculate_regime_metrics,
    check_plan_with_daily_bar,
    classify_market_regime,
    classify_regime,
    classify_stock_regime,
    evaluate_thermostat,
    is_fake_breakout,
    is_one_word_limit_up,
    run_thermostat_strategy,
)


def _history(closes: list[float], symbol: str = "600001.SH", start: str = "2025-01-01") -> pd.DataFrame:
    dates = pd.date_range(start, periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": dates.strftime("%Y-%m-%d"),
            "open": closes,
            "high": [value * 1.02 for value in closes],
            "low": [value * 0.98 for value in closes],
            "close": closes,
            "volume": [100000] * len(closes),
        }
    )


def _linear(start: float, step: float, count: int) -> list[float]:
    return [start + i * step for i in range(count)]


def _flat_wave(base: float = 10.0, count: int = 143, amplitude: float = 0.45) -> list[float]:
    pattern = [-1.0, -0.2, 0.7, 0.1, 1.0, -0.4, 0.3, -0.8]
    return [base + pattern[i % len(pattern)] * amplitude for i in range(count)]


def _strong_uptrend(count: int = 140) -> list[float]:
    return _linear(10, 0.025, count)


def _uptrend(count: int = 140) -> list[float]:
    return _linear(10, 0.016, count)


def _downtrend(count: int = 140) -> list[float]:
    return _linear(14, -0.02, count)


def _ohlcv_history(
    closes: list[float],
    symbol: str = "600001.SH",
    start: str = "2025-01-01",
    volume: int = 100000,
) -> pd.DataFrame:
    dates = pd.date_range(start, periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": dates.strftime("%Y-%m-%d"),
            "open": [round(value * 0.995, 2) for value in closes],
            "high": [round(value * 1.02, 2) for value in closes],
            "low": [round(value * 0.98, 2) for value in closes],
            "close": closes,
            "volume": [volume] * len(closes),
        }
    )


def _thermostat_rows(result) -> pd.DataFrame:
    return result.trigger_plan


class CompositeMarketService:
    def __init__(self, index_histories: dict[str, pd.DataFrame], stock_histories: dict[str, pd.DataFrame] | None = None) -> None:
        self.index_histories = index_histories
        self.stock_histories = stock_histories or {}
        self.requested_indexes: list[str] = []

    def get_history(self, symbol: str, **kwargs) -> pd.DataFrame:
        return self.stock_histories[symbol]

    def get_index_history(self, index_code: str, start_date: str, end_date: str, period: str = "daily") -> pd.DataFrame:
        self.requested_indexes.append(index_code)
        frame = self.index_histories.get(index_code)
        if frame is None:
            raise RuntimeError(f"missing index {index_code}")
        return frame


def test_metrics_cover_data_lengths_percentiles_and_zero_volatility() -> None:
    short = calculate_regime_metrics(_history(_linear(10, 0.1, 50)))
    mid = calculate_regime_metrics(_history(_linear(10, 0.05, 90)))
    normal = calculate_regime_metrics(_history(_linear(10, 0.03, 180)))
    long = calculate_regime_metrics(_history(_flat_wave(count=280, amplitude=0.2)))
    zero_vol = calculate_regime_metrics(_history([10.0] * 140))

    assert short["data_sufficient"] is False
    assert mid["length_bucket"] == "reduced"
    assert normal["length_bucket"] == "normal"
    assert normal["vol20_percentile_252"] is None
    assert long["length_bucket"] == "full"
    assert long["vol20_percentile_252"] is not None
    assert long["range20_percentile_252"] is not None
    assert zero_vol["trend_strength"] == 0


def test_market_and_stock_use_different_regime_thresholds() -> None:
    market_up = classify_market_regime(_history(_linear(3000, 3.0, 140), "000852.SH"))
    market_down = classify_market_regime(_history(_linear(3000, -3.5, 140), "000852.SH"))
    market_range = classify_market_regime(_history(_flat_wave(3000, 140, 18), "000852.SH"))
    volatile_market = classify_market_regime(_history([3000, 3250, 2900, 3300, 2850, 3350] * 25, "000852.SH"))

    stock_strong = classify_stock_regime(_history(_strong_uptrend()))
    stock_up = classify_stock_regime(_history(_uptrend()))
    stock_range = classify_stock_regime(_history(_flat_wave()))
    stock_down = classify_stock_regime(_history(_downtrend()))
    stock_transition = classify_stock_regime(_history([10, 14, 8, 15, 7, 16] * 25))

    assert market_up["regime"] == "market_uptrend"
    assert market_down["regime"] == "market_downtrend"
    assert market_range["regime"] == "market_range"
    assert volatile_market["regime"] == "market_transition"
    assert stock_strong["regime"] == "strong_uptrend"
    assert stock_up["regime"] == "uptrend"
    assert stock_range["regime"] == "range"
    assert stock_down["regime"] == "downtrend"
    assert stock_transition["regime"] == "transition"
    assert classify_regime(_history(_uptrend()), mode="stock")["regime"] == "uptrend"
    assert classify_regime(_history(_linear(3000, 3.0, 140), "000852.SH"), mode="market")["regime"] == "market_uptrend"


def test_run_thermostat_uses_composite_market_benchmark_and_defensive_anchor() -> None:
    service = CompositeMarketService(
        index_histories={
            "000852.SH": _history(_downtrend(), "000852.SH"),
            "399006.SZ": _history(_downtrend(), "399006.SZ"),
            "000688.SH": _history(_uptrend(), "000688.SH"),
            "000300.SH": _history(_downtrend(), "000300.SH"),
        },
        stock_histories={"600001.SH": _history(_strong_uptrend(), "600001.SH")},
    )

    result = run_thermostat_strategy(
        service=service,
        symbols=["600001"],
        start_date="20250101",
        end_date="20250520",
        cash=100000,
    )

    assert {"000852.SH", "399006.SZ", "000688.SH", "000300.SH"}.issubset(service.requested_indexes)
    assert result.market_overview.loc[0, "data_source"] == "composite_index"
    assert result.market_overview.loc[0, "market_regime"] == "market_downtrend"
    row = result.trigger_plan.loc[0]
    assert row["stock_mode"] == "trend"
    assert row["market_regime_normalized"] == "extreme_weak"
    assert row["market_position_discount"] == 0.5
    assert float(row["max_position_pct"]) <= 0.10


def test_composite_market_falls_back_to_candidate_aggregate_when_all_indexes_missing() -> None:
    service = CompositeMarketService(
        index_histories={},
        stock_histories={"600001.SH": _history(_strong_uptrend(), "600001.SH")},
    )

    result = run_thermostat_strategy(
        service=service,
        symbols=["600001"],
        start_date="20250101",
        end_date="20250520",
        cash=100000,
    )

    assert result.market_overview.loc[0, "data_source"] == "candidate_aggregate"
    assert result.trigger_plan.loc[0, "symbol"] == "600001.SH"


def test_short_history_never_generates_buy_add_or_grid() -> None:
    result = evaluate_thermostat(
        histories={"600001.SH": _history(_linear(10, 0.2, 40))},
        market_history=_history(_linear(3000, 3.0, 140), "000852.SH"),
        candidates=[{"symbol": "600001.SH", "name": "A"}],
        cash=100000,
        as_of="2026-05-10",
    )

    row = result.trigger_plan.loc[0]
    assert row["stock_regime"] == "insufficient_data"
    assert row["stock_mode"] == "insufficient_data"
    assert row["target_position_pct"] == 0
    assert row["max_position_pct"] == 0
    assert row["data_sufficient"] == False  # noqa: E712
    assert "数据不足" in row["reason"] or "数据不足" in row["risk_note"]


def test_pool_strength_does_not_override_market_downtrend() -> None:
    histories = {
        f"60000{i}.SH": _history(_strong_uptrend(), f"60000{i}.SH")
        for i in range(1, 5)
    }
    result = evaluate_thermostat(
        histories=histories,
        market_history=_history(_downtrend(), "000852.SH"),
        candidates=[{"symbol": symbol, "name": symbol} for symbol in histories],
        cash=100000,
        as_of="2026-05-20",
    )

    assert result.market_overview.loc[0, "pool_regime"] == "pool_strong"
    assert not result.trigger_plan.empty
    assert set(result.trigger_plan["market_regime_normalized"]) == {"extreme_weak"}
    assert set(result.trigger_plan["market_position_discount"]) == {0.5}
    assert set(result.trigger_plan["max_position_pct"]) == {0.10}


def test_market_routing_and_cash_consistency() -> None:
    transition = evaluate_thermostat(
        histories={"600001.SH": _history(_strong_uptrend(), "600001.SH")},
        market_history=_history([3000, 3300, 2850, 3350, 2900, 3400] * 25, "000852.SH"),
        candidates=[{"symbol": "600001.SH", "name": "A"}],
        cash=100000,
        as_of="2026-05-20",
    ).trigger_plan.loc[0]
    uptrend = evaluate_thermostat(
        histories={"600002.SH": _history(_uptrend(), "600002.SH")},
        market_history=_history(_linear(3000, 3.0, 140), "000852.SH"),
        candidates=[{"symbol": "600002.SH", "name": "B"}],
        cash=100000,
        as_of="2026-05-20",
    ).trigger_plan.loc[0]
    no_cash = evaluate_thermostat(
        histories={"600003.SH": _history(_strong_uptrend(), "600003.SH")},
        market_history=_history(_linear(3000, 3.0, 140), "000852.SH"),
        candidates=[{"symbol": "600003.SH", "name": "C"}],
        cash=100,
        as_of="2026-05-20",
    ).trigger_plan.loc[0]

    assert transition["market_regime_normalized"] == "weak"
    assert 0 < float(transition["max_position_pct"]) <= 0.14
    assert uptrend["market_regime_normalized"] == "strong"
    assert 0.08 <= float(uptrend["target_position_pct"]) <= 0.20
    assert 0 < float(uptrend["max_position_pct"]) <= 0.20
    assert no_cash["stock_mode"] == "trend"
    assert "suggested_shares" not in no_cash.index


def test_grid_candidates_are_scored_limited_and_keep_grid_parameters() -> None:
    histories = {
        "600001.SH": _history(_flat_wave(10, 143, 0.7), "600001.SH"),
        "600002.SH": _history(_flat_wave(10, 143, 0.6), "600002.SH"),
        "600003.SH": _history(_flat_wave(10, 143, 0.5), "600003.SH"),
        "600004.SH": _history(_flat_wave(10, 143, 0.45), "600004.SH"),
    }

    result = evaluate_thermostat(
        histories=histories,
        market_history=_history(_flat_wave(3000, 140, 15), "000852.SH"),
        candidates=[{"symbol": symbol, "name": symbol} for symbol in histories],
        cash=100000,
        as_of="2026-05-20",
    )

    range_rows = result.trigger_plan[result.trigger_plan["stock_mode"] == "range"]
    assert len(range_rows) == 4
    assert set(range_rows["grid_max_layers"].dropna()) == {3}
    assert set(range_rows["configured_grid_layers"]) == {3}
    for _, row in range_rows.iterrows():
        buy_levels = [float(value) for value in str(row["grid_buy_levels"]).split("|")]
        sell_levels = [float(value) for value in str(row["grid_sell_levels"]).split("|")]
        assert len(buy_levels) == len(set(buy_levels)) == int(row["effective_grid_layers"])
        assert len(sell_levels) == len(set(sell_levels)) == int(row["effective_grid_layers"])
        assert buy_levels == sorted(buy_levels, reverse=True)
        assert sell_levels == sorted(sell_levels)
    assert "grid_unit_pct" not in result.trigger_plan.columns


def test_trigger_plan_replaces_legacy_advice_as_main_contract() -> None:
    result = evaluate_thermostat(
        histories={"600001.SH": _history(_strong_uptrend(), "600001.SH")},
        market_history=_history(_linear(3000, 3.0, 140), "000852.SH"),
        candidates=[{"symbol": "600001.SH", "name": "A"}],
        cash=100000,
        as_of="2026-05-20",
    )

    assert set(result.tables) == {"market_overview", "trigger_plan", "errors"}
    assert result.holding_advice.empty
    assert result.new_candidates.empty
    assert result.grid_advice.empty
    assert result.trend_advice.empty
    assert not result.trigger_plan.empty
    assert set(REQUIRED_TRIGGER_PLAN_COLUMNS).issubset(result.trigger_plan.columns)
    assert {"symbol", "date", "market_regime", "stock_regime", "reason", "risk_note"}.issubset(result.trigger_plan.columns)
    assert {
        "action",
        "suggested_shares",
        "suggested_position_pct",
        "strategy_family",
        "grid_unit_pct",
        "executable",
    }.isdisjoint(result.trigger_plan.columns)


def test_t1_thermostat_vocabularies_and_contract_columns_are_defined() -> None:
    assert set(STOCK_MODES) == {"trend", "range", "downtrend", "chaotic", "insufficient_data"}
    assert {"strong", "normal", "weak", "extreme_weak"}.issubset(MARKET_POSITION_DISCOUNTS)
    assert set(PENDING_SELL_LEVELS) == {"", "pending_reduce", "pending_exit", "pending_emergency_exit"}
    assert {"trend_buy", "trend_reduce", "trend_exit", "grid_buy", "grid_sell"}.issubset(TRIGGER_TYPES)
    assert {"stock_mode", "trend_buy_trigger", "pending_sell_level"}.issubset(REQUIRED_TRIGGER_PLAN_COLUMNS)


def test_t1_trigger_plan_rows_expose_required_contract_fields() -> None:
    result = evaluate_thermostat(
        histories={"600001.SH": _ohlcv_history(_uptrend(140), "600001.SH")},
        market_history=_ohlcv_history(_linear(3000, 3.0, 140), "000852.SH"),
        candidates=[{"symbol": "600001.SH", "name": "A"}],
        cash=100000,
        as_of="2025-05-20",
    )

    row = result.trigger_plan.loc[0]
    assert set(REQUIRED_TRIGGER_PLAN_COLUMNS).issubset(result.trigger_plan.columns)
    assert row["stock_mode"] in STOCK_MODES
    assert row["market_position_discount"] > 0
    assert row["available_shares"] == 0
    assert row["today_bought_shares"] == 0
    assert row["total_shares"] == 0


def test_t1_each_stock_gets_exactly_one_mode_and_market_discount_does_not_override_it() -> None:
    histories = {
        "600001.SH": _ohlcv_history(_uptrend(140), "600001.SH"),
        "600002.SH": _ohlcv_history(_flat_wave(10, 143, 0.45), "600002.SH"),
        "600003.SH": _ohlcv_history(_downtrend(140), "600003.SH"),
        "600004.SH": _ohlcv_history([10, 14, 8, 15, 7, 16] * 25, "600004.SH"),
    }
    result = evaluate_thermostat(
        histories=histories,
        market_history=_ohlcv_history(_downtrend(140), "000852.SH"),
        candidates=[{"symbol": symbol, "name": symbol} for symbol in histories],
        cash=100000,
        as_of="2025-05-20",
    )

    rows = _thermostat_rows(result).drop_duplicates("symbol")
    assert set(rows["symbol"]) == set(histories)
    assert rows["stock_mode"].isin(STOCK_MODES).all()
    assert rows.loc[rows["symbol"] == "600001.SH", "stock_mode"].iloc[0] == "trend"
    assert rows.loc[rows["symbol"] == "600003.SH", "stock_mode"].iloc[0] == "downtrend"
    assert set(rows["market_regime_normalized"]) == {"extreme_weak"}
    assert set(rows["market_position_discount"]) == {0.5}


def test_t1_insufficient_data_uses_prior_complete_daily_bars_and_blocks_normal_buy() -> None:
    result = evaluate_thermostat(
        histories={"600001.SH": _ohlcv_history(_linear(10, 0.2, 40), "600001.SH")},
        market_history=_ohlcv_history(_linear(3000, 3.0, 140), "000852.SH"),
        candidates=[{"symbol": "600001.SH", "name": "A"}],
        cash=100000,
        as_of="2025-02-10",
    )

    row = result.trigger_plan.loc[0]
    assert row["stock_mode"] == "insufficient_data"
    assert row["trigger_status"] == "not_applicable"
    assert row["trend_buy_trigger"] == ""
    assert row["grid_buy_levels"] == ""


def test_t1_trend_outputs_bollinger_atr_triggers_and_batches() -> None:
    result = evaluate_thermostat(
        histories={"600001.SH": _ohlcv_history(_uptrend(140), "600001.SH")},
        market_history=_ohlcv_history(_linear(3000, 3.0, 140), "000852.SH"),
        candidates=[{"symbol": "600001.SH", "name": "A"}],
        cash=100000,
        as_of="2025-05-20",
    )

    row = result.trigger_plan.loc[0]
    assert row["stock_mode"] == "trend"
    assert row["boll_upper"] > row["boll_mid"] > row["boll_lower"]
    assert row["atr20"] > 0
    expected_buffer = min(0.2 * float(row["atr20"]), float(row["reference_price"]) * 0.005)
    assert row["trend_buy_trigger"] == round(float(row["boll_upper"]) + expected_buffer, 2)
    assert row["trend_reduce_trigger"] == row["boll_mid"]
    expected_exit = max(
        float(row["boll_lower"]),
        float(row["reference_price"]) - 2 * float(row["atr20"]),
    )
    assert row["trend_exit_trigger"] == round(expected_exit, 2)
    assert row["effective_trend_exit_trigger"] == row["trend_exit_trigger"]
    assert row["volume_ma20"] == 100000
    assert row["trend_batches"] == "40%,35%,25%"
    assert 0 < float(row["max_position_pct"]) <= 0.20


def test_t1_trigger_indicators_allow_close_only_history_without_volume() -> None:
    indicators = _trigger_indicators(pd.DataFrame({"close": _uptrend(20)}))

    assert indicators["close"] is not None
    assert indicators["volume_ma20"] is None


def test_t1_range_outputs_three_layer_grid_and_position_caps() -> None:
    result = evaluate_thermostat(
        histories={"600001.SH": _ohlcv_history(_flat_wave(10, 143, 0.45), "600001.SH")},
        market_history=_ohlcv_history(_flat_wave(3000, 140, 15), "000852.SH"),
        candidates=[{"symbol": "600001.SH", "name": "A"}],
        cash=100000,
        as_of="2025-05-20",
    )

    row = result.trigger_plan.loc[0]
    assert row["stock_mode"] == "range"
    assert row["grid_upper"] > row["grid_mid"] > row["grid_lower"]
    buy_levels = [float(value) for value in str(row["grid_buy_levels"]).split("|")]
    sell_levels = [float(value) for value in str(row["grid_sell_levels"]).split("|")]
    assert len(buy_levels) == len(sell_levels) == int(row["effective_grid_layers"])
    assert buy_levels == sorted(set(buy_levels), reverse=True)
    assert sell_levels == sorted(set(sell_levels))
    assert 0 < float(row["max_position_pct"]) <= 0.15
    assert float(row["grid_total_max_position_pct"]) == 0.40
    assert int(row["grid_max_layers"]) == 3
    assert int(row["configured_grid_layers"]) == 3
    assert 1 <= int(row["effective_grid_layers"]) <= 3
    assert float(row["grid_layer_spacing_pct"]) in {0.035, 0.055, 0.075}


def test_t1_grid_levels_are_deduplicated_and_ordered_after_bollinger_clipping() -> None:
    grid = _grid_trigger_levels(
        {
            "boll_mid": 10.0,
            "boll_lower": 9.3,
            "boll_upper": 10.7,
            "vol20": 0.015,
        }
    )

    assert grid["buy"] == [9.65, 9.3]
    assert grid["sell"] == [10.35, 10.7]
    assert all(left > right for left, right in zip(grid["buy"], grid["buy"][1:]))
    assert all(left < right for left, right in zip(grid["sell"], grid["sell"][1:]))
    assert grid["configured_layers"] == 3
    assert grid["effective_layers"] == 2
    assert grid["spacing_pct"] == 0.035


@pytest.mark.parametrize("cost_field", ["avg_cost", "average_cost", "trend_average_cost"])
def test_t1_trend_exit_uses_holding_average_cost_and_never_decreases_previous_line(cost_field: str) -> None:
    holding = {
        "symbol": "600001.SH",
        "name": "A",
        "shares": 100,
        cost_field: 20.0,
        "last_effective_exit_trigger": 25.0,
    }
    result = evaluate_thermostat(
        histories={"600001.SH": _ohlcv_history(_uptrend(140), "600001.SH")},
        market_history=_ohlcv_history(_linear(3000, 3.0, 140), "000852.SH"),
        holdings=pd.DataFrame([holding]),
        cash=100000,
        as_of="2025-05-20",
    )

    row = result.trigger_plan.loc[0]
    expected_new = round(max(float(row["boll_lower"]), 20.0 - 2 * float(row["atr20"])), 2)
    assert row["trend_exit_trigger"] == expected_new
    assert row["effective_trend_exit_trigger"] == 25.0


def test_t1_downtrend_and_chaotic_do_not_emit_normal_new_buy_plans() -> None:
    result = evaluate_thermostat(
        histories={
            "600001.SH": _ohlcv_history(_downtrend(140), "600001.SH"),
            "600002.SH": _ohlcv_history([10, 14, 8, 15, 7, 16] * 25, "600002.SH"),
        },
        market_history=_ohlcv_history(_linear(3000, 3.0, 140), "000852.SH"),
        candidates=[{"symbol": "600001.SH", "name": "A"}, {"symbol": "600002.SH", "name": "B"}],
        cash=100000,
        as_of="2025-05-20",
    )

    rows = result.trigger_plan.set_index("symbol")
    assert rows.loc["600001.SH", "stock_mode"] == "downtrend"
    assert rows.loc["600002.SH", "stock_mode"] == "chaotic"
    assert set(rows["trigger_status"]) == {"not_applicable"}
    assert set(rows["max_position_pct"]) == {0.0}


def test_t1_holding_share_split_and_pending_sell_levels() -> None:
    holdings = pd.DataFrame(
        [
            {"symbol": "600001.SH", "name": "A", "shares": 300, "execution_date": "2025-05-20"},
            {"symbol": "600002.SH", "name": "B", "shares": 200, "execution_date": "2025-05-19"},
        ]
    )
    result = evaluate_thermostat(
        histories={
            "600001.SH": _ohlcv_history(_uptrend(140), "600001.SH"),
            "600002.SH": _ohlcv_history(_uptrend(140), "600002.SH"),
        },
        market_history=_ohlcv_history(_linear(3000, 3.0, 140), "000852.SH"),
        holdings=holdings,
        cash=100000,
        as_of="2025-05-20",
    )

    rows = result.trigger_plan.set_index("symbol")
    assert rows.loc["600001.SH", "today_bought_shares"] == 300
    assert rows.loc["600001.SH", "available_shares"] == 0
    assert rows.loc["600001.SH", "total_shares"] == 300
    assert rows.loc["600001.SH", "share_split_source"] == "execution_date"
    assert rows.loc["600002.SH", "available_shares"] == 200
    assert rows.loc["600002.SH", "today_bought_shares"] == 0

    plan = rows.loc["600001.SH"].to_dict()
    plan["trend_reduce_trigger"] = 10.0
    plan["trend_exit_trigger"] = 9.0
    result_rows = check_plan_with_daily_bar(plan, {"high": 11.0, "low": 8.5}, is_limit_down=False, is_suspended=False)
    assert result_rows[0]["filled_status"] == "pending"
    assert result_rows[0]["pending_sell_level"] == "pending_exit"


@pytest.mark.parametrize(
    ("bar", "indicators"),
    [
        ({"open": 10.5, "high": 10.9, "low": 10.0, "close": 10.8, "volume": 300000}, {"boll_upper": 11.0, "volume_ma20": 100000}),
        ({"open": 10.5, "high": 12.0, "low": 10.0, "close": 11.1, "volume": 300000}, {"boll_upper": 11.0, "volume_ma20": 100000}),
        ({"open": 11.5, "high": 12.0, "low": 10.0, "close": 10.8, "volume": 300000}, {"boll_upper": 11.0, "volume_ma20": 100000}),
        ({"open": 10.5, "high": 12.0, "low": 10.0, "close": 10.8, "volume": 249999}, {"boll_upper": 11.0, "volume_ma20": 100000}),
        ({"high": 12.0, "low": 10.0, "close": 10.8, "volume": 300000}, {"boll_upper": 11.0, "volume_ma20": 100000}),
        ({"open": 10.5, "high": 12.0, "low": 10.0, "close": 10.8}, {"boll_upper": 11.0, "volume_ma20": 100000}),
        ({"open": 10.5, "high": 12.0, "low": 10.0, "close": 10.8, "volume": 300000}, {"boll_upper": 11.0}),
    ],
    ids=[
        "high_not_above_upper",
        "close_not_below_upper",
        "upper_shadow_below_half",
        "volume_ratio_below_2_5",
        "missing_open",
        "missing_volume",
        "missing_volume_ma20",
    ],
)
def test_t1_fake_breakout_requires_all_four_conditions(bar: dict[str, float], indicators: dict[str, float]) -> None:
    assert is_fake_breakout(bar, indicators) is False


def test_t1_limit_failure_fake_breakout_and_conservative_trigger_priority() -> None:
    assert is_one_word_limit_up({"open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0}, 11.0)

    assert is_fake_breakout(
        {"open": 10.5, "high": 12.0, "low": 10.0, "close": 10.8, "volume": 300000},
        {"boll_upper": 11.0, "volume_ma20": 100000},
    )

    plan = {
        "symbol": "600001.SH",
        "date": "2025-05-20",
        "stock_mode": "trend",
        "trend_buy_trigger": 11.0,
        "trend_reduce_trigger": 10.0,
        "trend_exit_trigger": 9.0,
        "available_shares": 100,
        "today_bought_shares": 0,
    }
    rows = check_plan_with_daily_bar(
        plan,
        {"open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0},
        limit_up_price=11.0,
        is_limit_down=False,
        is_suspended=False,
    )
    assert rows[0]["trigger_type"] == "trend_buy"
    assert rows[0]["filled_status"] == "failed"
    assert rows[0]["failed_reason"] == "limit_up_buy_failed"

    rows = check_plan_with_daily_bar(plan, {"high": 11.5, "low": 8.5}, is_limit_down=True, is_suspended=False)
    assert rows[0]["trigger_type"] == "trend_exit"
    assert rows[0]["filled_status"] == "failed"
    assert rows[0]["failed_reason"] == "limit_down_sell_failed"
