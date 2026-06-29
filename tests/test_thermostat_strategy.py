from __future__ import annotations

import pandas as pd

from stock_picker.strategies.thermostat import (
    REQUIRED_ADVICE_COLUMNS,
    classify_regime,
    evaluate_thermostat,
)


def _history(closes: list[float], symbol: str = "600001.SH") -> pd.DataFrame:
    dates = pd.date_range("2026-04-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": dates.strftime("%Y-%m-%d"),
            "open": closes,
            "high": [value * 1.01 for value in closes],
            "low": [value * 0.99 for value in closes],
            "close": closes,
            "volume": [100000] * len(closes),
        }
    )


def test_thermostat_advice_contains_required_fields_and_buckets() -> None:
    result = evaluate_thermostat(
        histories={"600001.SH": _history([10 + i * 0.2 for i in range(40)])},
        market_history=_history([3000 + i * 10 for i in range(40)], "000001.SH"),
        candidates=[{"symbol": "600001.SH", "name": "A"}],
        cash=100000,
        as_of="2026-05-10",
    )

    assert set(REQUIRED_ADVICE_COLUMNS).issubset(result.new_candidates.columns)
    assert "holding_advice" in result.tables
    assert "new_candidates" in result.tables
    assert result.new_candidates.loc[0, "strategy_family"] == "trend_following"
    assert result.new_candidates.loc[0, "action"] == "buy"


def test_insufficient_data_is_marked_and_never_strong_buy() -> None:
    result = evaluate_thermostat(
        histories={"600001.SH": _history([10, 10.1, 10.2])},
        market_history=_history([3000, 3001, 3002], "000001.SH"),
        candidates=[{"symbol": "600001.SH", "name": "A"}],
        cash=100000,
        as_of="2026-04-03",
    )

    row = result.new_candidates.loc[0]
    assert row["stock_regime"] == "insufficient_data"
    assert row["action"] in {"observe", "wait_confirm"}
    assert row["data_sufficient"] == False  # noqa: E712


def test_market_regime_is_explainable() -> None:
    result = evaluate_thermostat(
        histories={"600001.SH": _history([10 + i * 0.2 for i in range(40)])},
        market_history=_history([3000 + i * 10 for i in range(40)], "000001.SH"),
        candidates=[{"symbol": "600001.SH", "name": "A"}],
        cash=100000,
        as_of="2026-05-10",
    )

    overview = result.market_overview.loc[0]
    assert overview["market_regime"] == "uptrend"
    assert overview["data_source"]
    assert "20日收益" in overview["evidence"]
    assert overview["regime_date"] == "2026-05-10"


def test_regime_classifier_handles_up_down_range_and_transition() -> None:
    assert classify_regime(_history([10 + i * 0.25 for i in range(40)]))["regime"] == "uptrend"
    assert classify_regime(_history([20 - i * 0.25 for i in range(40)]))["regime"] == "downtrend"
    assert classify_regime(_history([10, 10.5, 9.7, 10.3] * 10))["regime"] == "range"
    assert classify_regime(_history([10, 11, 9, 12, 8, 13, 9, 12] * 5))["regime"] == "transition"


def test_regime_routing_outputs_expected_strategy_family_fields() -> None:
    up = evaluate_thermostat(
        histories={"600001.SH": _history([10 + i * 0.25 for i in range(40)])},
        market_history=_history([3000 + i * 10 for i in range(40)], "000001.SH"),
        candidates=[{"symbol": "600001.SH", "name": "A"}],
        cash=100000,
    )
    down = evaluate_thermostat(
        histories={"600002.SH": _history([20 - i * 0.25 for i in range(40)], "600002.SH")},
        market_history=_history([3000 - i * 10 for i in range(40)], "000001.SH"),
        candidates=[{"symbol": "600002.SH", "name": "B"}],
        cash=100000,
    )
    ranging = evaluate_thermostat(
        histories={"600003.SH": _history([10, 10.4, 9.8, 10.2] * 10, "600003.SH")},
        market_history=_history([3000, 3020, 2980, 3010] * 10, "000001.SH"),
        candidates=[{"symbol": "600003.SH", "name": "C"}],
        cash=100000,
    )

    assert up.new_candidates.loc[0, "strategy_family"] == "trend_following"
    assert up.new_candidates.loc[0, "stop_price"] > 0
    assert down.new_candidates.empty
    grid = ranging.grid_advice.loc[0]
    assert grid["strategy_family"] == "grid"
    assert grid["grid_upper"] > grid["grid_mid"] > grid["grid_lower"]
    assert grid["grid_max_layers"] > 0
    assert grid["grid_stop_condition"]


def test_market_stock_conflict_downgrades_advice_strength() -> None:
    result = evaluate_thermostat(
        histories={"600001.SH": _history([10 + i * 0.25 for i in range(40)])},
        market_history=_history([3000 - i * 10 for i in range(40)], "000001.SH"),
        candidates=[{"symbol": "600001.SH", "name": "A"}],
        cash=100000,
    )

    row = result.new_candidates.loc[0]
    assert row["action"] == "observe"
    assert row["strength"] == "reduced"
    assert "逆市场风险" in row["risk_note"]


def test_existing_holdings_are_always_evaluated_before_new_candidates() -> None:
    result = evaluate_thermostat(
        histories={
            "600001.SH": _history([20 - i * 0.25 for i in range(40)], "600001.SH"),
            "600002.SH": _history([10 + i * 0.25 for i in range(40)], "600002.SH"),
        },
        market_history=_history([3000 + i * 10 for i in range(40)], "000001.SH"),
        candidates=[{"symbol": "600002.SH", "name": "Candidate"}],
        holdings=pd.DataFrame([{"symbol": "600001.SH", "name": "Held", "shares": 100, "avg_cost": 18.0}]),
        cash=100000,
    )

    assert result.holding_advice.loc[0, "symbol"] == "600001.SH"
    assert result.holding_advice.loc[0, "action"] in {"sell", "reduce"}
    assert result.new_candidates.loc[0, "symbol"] == "600002.SH"
