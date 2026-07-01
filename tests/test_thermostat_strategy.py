from __future__ import annotations

import pandas as pd

from stock_picker.strategies.thermostat import (
    REQUIRED_ADVICE_COLUMNS,
    calculate_regime_metrics,
    classify_market_regime,
    classify_regime,
    classify_stock_regime,
    evaluate_thermostat,
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
    row = result.new_candidates.loc[0]
    assert row["action"] in {"observe", "blocked"}
    assert row["suggested_position_pct"] == 0
    assert row["suggested_shares"] == 0
    assert row["executable"] == False  # noqa: E712


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
    assert result.new_candidates.loc[0, "symbol"] == "600001.SH"


def test_short_history_never_generates_buy_add_or_grid() -> None:
    result = evaluate_thermostat(
        histories={"600001.SH": _history(_linear(10, 0.2, 40))},
        market_history=_history(_linear(3000, 3.0, 140), "000852.SH"),
        candidates=[{"symbol": "600001.SH", "name": "A"}],
        cash=100000,
        as_of="2026-05-10",
    )

    row = result.new_candidates.loc[0]
    assert row["stock_regime"] == "insufficient_data"
    assert row["action"] in {"observe", "wait_confirm"}
    assert row["suggested_position_pct"] == 0
    assert row["suggested_shares"] == 0
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
    assert not result.new_candidates.empty
    assert set(result.new_candidates["suggested_position_pct"]) == {0.0}
    assert set(result.new_candidates["suggested_shares"]) == {0}
    assert set(result.new_candidates["executable"]) == {False}


def test_market_routing_and_cash_consistency() -> None:
    transition = evaluate_thermostat(
        histories={"600001.SH": _history(_strong_uptrend(), "600001.SH")},
        market_history=_history([3000, 3300, 2850, 3350, 2900, 3400] * 25, "000852.SH"),
        candidates=[{"symbol": "600001.SH", "name": "A"}],
        cash=100000,
        as_of="2026-05-20",
    ).new_candidates.loc[0]
    uptrend = evaluate_thermostat(
        histories={"600002.SH": _history(_uptrend(), "600002.SH")},
        market_history=_history(_linear(3000, 3.0, 140), "000852.SH"),
        candidates=[{"symbol": "600002.SH", "name": "B"}],
        cash=100000,
        as_of="2026-05-20",
    ).new_candidates.loc[0]
    no_cash = evaluate_thermostat(
        histories={"600003.SH": _history(_strong_uptrend(), "600003.SH")},
        market_history=_history(_linear(3000, 3.0, 140), "000852.SH"),
        candidates=[{"symbol": "600003.SH", "name": "C"}],
        cash=100,
        as_of="2026-05-20",
    ).new_candidates.loc[0]

    assert transition["strength"] == "reduced"
    assert 0.03 <= float(transition["suggested_position_pct"]) <= 0.05
    assert "试探仓" in transition["reason"]
    assert uptrend["action"] in {"buy", "add"}
    assert 0.08 <= float(uptrend["suggested_position_pct"]) <= 0.10
    assert int(uptrend["suggested_shares"]) > 0
    assert no_cash["suggested_position_pct"] == 0
    assert no_cash["suggested_shares"] == 0
    assert "现金不足以买入一手" in no_cash["reason"] or "现金不足以买入一手" in no_cash["risk_note"]


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

    enabled = result.grid_advice[result.grid_advice["executable"] == True]  # noqa: E712
    disabled = result.grid_advice[result.grid_advice["executable"] == False]  # noqa: E712
    assert len(enabled) <= 3
    assert not disabled.empty
    assert set(result.grid_advice["grid_unit_pct"].dropna()) == {0.08}
    assert set(result.grid_advice["grid_max_layers"].dropna()) == {4}
    assert disabled["reason"].str.contains("网格优先级不足").any()


def test_atr_stop_target_fallback_and_required_columns() -> None:
    result = evaluate_thermostat(
        histories={"600001.SH": _history(_strong_uptrend(), "600001.SH")},
        market_history=_history(_linear(3000, 3.0, 140), "000852.SH"),
        candidates=[{"symbol": "600001.SH", "name": "A"}],
        cash=100000,
        as_of="2026-05-20",
    )

    row = result.new_candidates.loc[0]
    stop_pct = 1 - float(row["stop_price"]) / float(row["entry_price"])
    target_pct = float(row["target_price"]) / float(row["entry_price"]) - 1
    assert 0.06 <= stop_pct <= 0.12
    assert target_pct >= stop_pct * 1.9
    assert set(REQUIRED_ADVICE_COLUMNS).issubset(result.new_candidates.columns)
    assert "holding_advice" in result.tables
    assert "new_candidates" in result.tables
