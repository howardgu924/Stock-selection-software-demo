from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_picker.strategies.adaptive_trend_v1_3.execution_gate import (
    REQUIRED_BAR_STARTS,
    calculate_emergency_market_gate,
    calculate_execution_gate,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase2_models import (
    EmergencyIndexInput,
    EmergencyStatus,
    ExecutionStatus,
)


def _bars(price: float = 100.0, volume: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "bar_start": REQUIRED_BAR_STARTS,
            "high": [price] * 6,
            "low": [price] * 6,
            "close": [price] * 6,
            "volume": [volume] * 6,
        }
    )


def _emergency_inputs(shocks: tuple[float, float, float]):
    return {
        symbol: EmergencyIndexInput(previous_close=100.0, atr20=10.0, p10=100.0 + shock * 10.0)
        for symbol, shock in zip(("000300.SH", "000852.SH", "399006.SZ"), shocks)
    }


def test_execution_gate_accepts_exact_six_completed_bars() -> None:
    result = calculate_execution_gate(_bars(), ma20=100.0, atr20=10.0)

    assert result.execution_gate.value == "PASS"
    assert result.p10 == 100.0
    assert result.reasons == ()


def test_missing_duplicate_and_ten_oclock_bar_are_rejected() -> None:
    missing = calculate_execution_gate(_bars().iloc[:-1], ma20=100.0, atr20=10.0)
    duplicate = calculate_execution_gate(
        pd.concat([_bars(), _bars().iloc[[0]]], ignore_index=True), ma20=100.0, atr20=10.0
    )
    extra = _bars()
    extra = pd.concat(
        [
            extra,
            pd.DataFrame(
                {"bar_start": ["10:00"], "high": [1.0], "low": [1.0], "close": [9999.0], "volume": [1.0]}
            ),
        ],
        ignore_index=True,
    )
    with_ten = calculate_execution_gate(extra, ma20=100.0, atr20=10.0)

    assert "missing_bar_start:09:55" in missing.reasons
    assert "duplicate_bar_start:09:30" in duplicate.reasons
    assert "unexpected_bar_start:10:00" in with_ten.reasons
    assert with_ten.p10 is None


@pytest.mark.parametrize(
    ("mutator", "reason"),
    [
        (lambda frame: frame.assign(close=np.inf), "invalid_close:09:30"),
        (lambda frame: frame.assign(low=0.0), "invalid_low:09:30"),
        (lambda frame: frame.assign(volume=-1.0), "invalid_volume:09:30"),
    ],
)
def test_invalid_prices_and_negative_volume_reject(mutator, reason) -> None:
    result = calculate_execution_gate(mutator(_bars()), ma20=100.0, atr20=10.0)

    assert result.execution_gate.value == "REJECT"
    assert reason in result.reasons


def test_zero_total_volume_rejects() -> None:
    result = calculate_execution_gate(_bars(volume=0.0), ma20=100.0, atr20=10.0)

    assert result.execution_gate.value == "REJECT"
    assert "nonpositive_total_volume" in result.reasons


def test_morning_vwap_fixed_example() -> None:
    bars = _bars()
    bars["high"] = [101, 102, 103, 104, 105, 106]
    bars["low"] = [99, 100, 101, 102, 103, 104]
    bars["close"] = [100, 101, 102, 103, 104, 105]
    bars["volume"] = [1, 2, 3, 4, 5, 6]
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3.0

    result = calculate_execution_gate(bars, ma20=105.0, atr20=20.0)

    assert result.morning_vwap == pytest.approx((typical * bars["volume"]).sum() / 21.0)
    assert result.p10 == 105.0


@pytest.mark.parametrize(
    ("distance", "expected"),
    [(-1.01, "REJECT"), (-1.0, "PASS"), (1.49, "PASS"), (1.5, "HALF"), (2.0, "HALF"), (2.01, "REJECT")],
)
def test_distance_ma20_all_endpoints(distance, expected) -> None:
    result = calculate_execution_gate(_bars(), ma20=100.0 - distance * 10.0, atr20=10.0)

    assert result.distance_ma20 == pytest.approx(distance)
    assert result.execution_gate.value == expected


@pytest.mark.parametrize(
    ("drawdown", "expected"),
    [(0.49, "PASS"), (0.50, "HALF"), (0.75, "HALF"), (0.7501, "REJECT")],
)
def test_high_to_ten_drawdown_all_endpoints(drawdown, expected) -> None:
    bars = _bars()
    bars.loc[0, ["high", "low", "close"]] = 100.0 + drawdown * 10.0

    result = calculate_execution_gate(bars, ma20=100.0, atr20=10.0)

    assert result.high_to_10_drawdown == pytest.approx(drawdown)
    assert result.execution_gate.value == expected
    if expected != "PASS":
        assert f"high_to_10_drawdown:{expected}" in result.reasons


@pytest.mark.parametrize(
    ("drawdown", "expected"),
    [(0.49, "PASS"), (0.50, "HALF"), (0.75, "HALF"), (0.7501, "REJECT")],
)
def test_morning_max_drawdown_all_endpoints(drawdown, expected) -> None:
    bars = _bars()
    bars.loc[3, "low"] = 100.0 - drawdown * 10.0

    result = calculate_execution_gate(bars, ma20=100.0, atr20=10.0)

    assert result.morning_max_drawdown == pytest.approx(drawdown)
    assert result.execution_gate.value == expected
    if expected != "PASS":
        assert f"morning_max_drawdown:{expected}" in result.reasons


@pytest.mark.parametrize(
    ("below", "expected"),
    [(0.24, "PASS"), (0.25, "HALF"), (0.50, "HALF"), (0.5001, "REJECT")],
)
def test_below_vwap_all_endpoints(below, expected) -> None:
    bars = _bars(100.0 + below * 10.0)
    bars.loc[5, "close"] = 100.0
    bars.loc[5, "volume"] = 0.0

    result = calculate_execution_gate(bars, ma20=100.0, atr20=10.0)

    assert result.below_vwap == pytest.approx(below)
    assert result.execution_gate.value == expected
    if expected != "PASS":
        assert f"below_vwap:{expected}" in result.reasons


def test_reject_priority_and_all_trigger_reasons_are_retained() -> None:
    bars = _bars()
    bars.loc[0, "high"] = 108.0
    bars.loc[2, "low"] = 90.0
    result = calculate_execution_gate(bars, ma20=79.0, atr20=10.0)

    assert result.execution_gate.value == "REJECT"
    assert {
        "distance_ma20_above:REJECT",
        "high_to_10_drawdown:REJECT",
        "morning_max_drawdown:REJECT",
    }.issubset(result.reasons)


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (ExecutionStatus(suspended=True), "security_suspended"),
        (ExecutionStatus(limit_status="limit_up"), "limit_up"),
        (ExecutionStatus(limit_status="unknown"), "limit_status_unknown"),
        (ExecutionStatus(trade_status="unknown"), "trade_status_unknown"),
    ],
)
def test_ten_oclock_security_status_rejects(status, reason) -> None:
    result = calculate_execution_gate(_bars(), ma20=100.0, atr20=10.0, execution_status=status)

    assert result.execution_gate.value == "REJECT"
    assert reason in result.reasons


def test_emergency_shock_formula() -> None:
    result = calculate_emergency_market_gate(_emergency_inputs((-0.5, -0.4, 0.2)))

    assert result.shock_for("000300.SH") == pytest.approx(-0.5)
    assert result.shock_for("000852.SH") == pytest.approx(-0.4)
    assert result.shock_for("399006.SZ") == pytest.approx(0.2)


@pytest.mark.parametrize(
    ("shocks", "expected"),
    [
        ((-0.79, -0.79, -2.0), "NORMAL"),
        ((-0.80, -0.80, 0.0), "LEVEL_1"),
        ((-1.19, -0.80, 0.0), "LEVEL_1"),
        ((-1.20, -1.20, 0.0), "LEVEL_2"),
    ],
)
def test_emergency_normal_level_one_level_two_and_exact_boundaries(shocks, expected) -> None:
    result = calculate_emergency_market_gate(_emergency_inputs(shocks))

    assert result.emergency_status.value == expected
    assert result.reject_new_entries is (expected != "NORMAL")
    assert result.remove_exposure_drop_limit is (expected == "LEVEL_2")
    assert result.max_reduction_pct == (0.30 if expected == "LEVEL_2" else 0.0)


def test_any_invalid_emergency_index_rejects_new_entries() -> None:
    inputs = _emergency_inputs((0.0, 0.0, 0.0))
    inputs["000852.SH"] = EmergencyIndexInput(previous_close=100.0, atr20=0.0, p10=100.0)

    result = calculate_emergency_market_gate(inputs)

    assert result.emergency_status.value == "INVALID"
    assert result.reject_new_entries is True
    assert "invalid_atr20:000852.SH" in result.reasons


def test_emergency_level_rejects_execution_gate() -> None:
    result = calculate_execution_gate(
        _bars(), ma20=100.0, atr20=10.0, emergency_status=EmergencyStatus.LEVEL_1
    )

    assert result.execution_gate.value == "REJECT"
    assert "emergency_market:LEVEL_1" in result.reasons
