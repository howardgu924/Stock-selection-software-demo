from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from stock_picker.strategies.adaptive_trend_v1_3.phase4_models import (
    CandidateInput,
    ExistingHolding,
    IndustryClassificationSnapshot,
    Phase4Status,
)
from stock_picker.strategies.adaptive_trend_v1_3.portfolio_allocator import (
    allocate_portfolio,
)


def _returns(sign=1):
    start = date(2025, 1, 1)
    return {
        start + timedelta(days=i): Decimal(sign * ((i % 7) - 3)) / Decimal("100")
        for i in range(60)
    }


def _code(value: str) -> str:
    if value[:6].isdigit():
        return value if "." in value else f"{value}.SH"
    fixed = {"A": 1, "B": 2, "C": 3, "H": 200, "TOP": 301, "LOW": 302}
    if value.startswith("S") and value[1:].isdigit():
        number = 100 + int(value[1:])
    elif value.startswith("H") and value[1:].isdigit():
        number = 200 + int(value[1:])
    else:
        number = fixed[value]
    return f"{600000 + number:06d}.SH"


def _industry(symbol: str, industry: str) -> IndustryClassificationSnapshot:
    return IndustryClassificationSnapshot(
        symbol=_code(symbol), industry_code=industry, industry_name=industry,
        effective_date="2024-01-01", known_at="2024-01-02 09:00+08:00",
        source="sw", classification_version="v1",
    )


def _candidate(symbol: str, rank: int, **overrides) -> CandidateInput:
    code = _code(symbol)
    industry = overrides.pop("industry", "Bank")
    values = dict(
        symbol=code, opportunity_status="VALID", opportunity_score=Decimal(100 - rank),
        entry_threshold=Decimal("60"), opportunity_rank=rank,
        rs60=Decimal("0.1"), rs20=Decimal("0.1"), signed_er20=Decimal("0.2"),
        market_paused=False, emergency_gate="NORMAL", risk_overlay="ALLOW",
        execution_gate="PASS", t1_risk_status="VALID", t1_loss_q=Decimal("0.05"),
        entry_atr=Decimal("1"), entry_price=Decimal("100"),
        industry_snapshot=_industry(symbol, industry),
        cooldown_blocked=False, daily_returns=_returns(), execution_price=Decimal("1"),
        buy_lot_size=1,
    )
    values.update(overrides)
    return CandidateInput(**values)


def _holding(symbol: str, weight, industry="Tech", loss="0.05", scenario="0.10"):
    return ExistingHolding(
        symbol=_code(symbol),
        actual_weight=Decimal(weight) if isinstance(weight, str) else weight,
        industry_snapshot=_industry(symbol, industry),
        t1_loss_q=Decimal(loss) if isinstance(loss, str) else loss,
        daily_returns=_returns(-1),
        scenario_loss_pct=Decimal(scenario) if isinstance(scenario, str) else scenario,
    )


def _result_by_symbol(result, symbol):
    return next(item for item in result.sizing_results if item.symbol == _code(symbol))


def test_max_six_holdings_and_stable_ranking() -> None:
    holdings = [_holding("H", "0.1")]
    candidates = [_candidate(f"S{i}", i) for i in range(1, 7)]
    result = allocate_portfolio(
        list(reversed(candidates)), holdings,
        portfolio_equity="100000", effective_exposure_cap="1", evaluation_as_of="2025-01-01",
    )
    assert result.selected_symbols == tuple(_code(f"S{i}") for i in range(1, 6))
    assert _result_by_symbol(result, "S6").failure_reasons == ("max_positions",)


def test_industry_cap_scales_new_candidates_proportionally() -> None:
    result = allocate_portfolio(
        [_candidate("A", 1), _candidate("B", 2)],
        [_holding("H", "0.20", industry="Bank")],
        portfolio_equity="100000", effective_exposure_cap="1", evaluation_as_of="2025-01-01",
    )
    assert _result_by_symbol(result, "A").industry_scaled_weight == Decimal("0.05")
    assert _result_by_symbol(result, "B").industry_scaled_weight == Decimal("0.05")
    assert dict(result.industry_weights)["Bank"] <= Decimal("0.30")


def test_exposure_cap_scales_only_new_candidates() -> None:
    holding = _holding("H", "0.50")
    result = allocate_portfolio(
        [_candidate("A", 1), _candidate("B", 2, industry="Health")], [holding],
        portfolio_equity="100000", effective_exposure_cap="0.60", evaluation_as_of="2025-01-01",
    )
    assert result.existing_exposure == Decimal("0.50")
    assert sum(item.exposure_scaled_weight for item in result.sizing_results) == Decimal("0.10")
    assert holding.actual_weight == Decimal("0.50")


def test_portfolio_stress_cap_scales_new_weight() -> None:
    result = allocate_portfolio(
        [_candidate("A", 1)], [_holding("H", "0.49", loss="0.10")],
        portfolio_equity="100000", effective_exposure_cap="1", evaluation_as_of="2025-01-01",
    )
    sizing = _result_by_symbol(result, "A")
    assert sizing.exposure_scaled_weight == Decimal("0.10")
    assert sizing.stress_scaled_weight == Decimal("0.02")
    assert result.stress_status == "NORMAL"
    assert result.allocation_status == "READY"
    assert result.final_new_stress == Decimal("0.001")
    assert result.final_portfolio_stress == Decimal("0.05")


def test_fixed_scaling_fields_and_lot_rounding_no_reallocation() -> None:
    holdings = [_holding(f"H{i}", "0.01") for i in range(5)]
    top = _candidate("TOP", 1, execution_price=Decimal("1000"), buy_lot_size=100)
    lower = _candidate("LOW", 2)
    result = allocate_portfolio(
        [lower, top], holdings,
        portfolio_equity="10000", effective_exposure_cap="1", evaluation_as_of="2025-01-01",
    )
    top_result = _result_by_symbol(result, "TOP")
    low_result = _result_by_symbol(result, "LOW")
    assert result.selected_symbols == (_code("TOP"),)
    assert top_result.order_qty == 0
    assert "below_min_trade" in top_result.failure_reasons
    assert low_result.order_qty == 0
    assert "max_positions" in low_result.failure_reasons


def test_industry_exposure_stress_scaling_order_is_frozen() -> None:
    holdings = [
        _holding("H1", "0.25", industry="Bank", loss="0.10"),
        _holding("H2", "0.24", industry="Tech", loss="0.10"),
    ]
    result = allocate_portfolio(
        [_candidate("A", 1)], holdings,
        portfolio_equity="100000", effective_exposure_cap="0.60", evaluation_as_of="2025-01-01",
    )
    sizing = _result_by_symbol(result, "A")
    assert sizing.adjusted_weight == Decimal("0.10")
    assert sizing.industry_scaled_weight == Decimal("0.05")
    assert sizing.exposure_scaled_weight == Decimal("0.05")
    assert sizing.stress_scaled_weight == Decimal("0.02")


def test_existing_stress_at_cap_blocks_new_and_reports_scenarios() -> None:
    holdings = [_holding("A", "0.5", loss="0.1"), _holding("B", "0.1", loss="0.1")]
    result = allocate_portfolio(
        [_candidate("C", 1)], holdings,
        portfolio_equity="100000", effective_exposure_cap="1", evaluation_as_of="2025-01-01",
    )
    assert result.status == Phase4Status.VALID
    assert _result_by_symbol(result, "C").final_target_weight == 0
    assert result.stress_status == "OVER_LIMIT"
    assert result.allocation_status == "BLOCK_NEW"
    assert result.final_new_stress == 0
    assert result.final_portfolio_stress == Decimal("0.06")
    assert "existing_stress_over_limit" in result.reasons
    assert result.two_limit_down_scenario_loss == Decimal("0.06")
    assert result.highest_correlation_pair[:2] == (_code("A"), _code("B"))


def test_invalid_portfolio_inputs_are_stable() -> None:
    result = allocate_portfolio(
        [], [], portfolio_equity="0", effective_exposure_cap="1", evaluation_as_of="2025-01-01"
    )
    assert result.status == Phase4Status.INVALID
    assert result.stress_status == "INVALID"
    assert result.allocation_status == "INVALID"
    assert result.final_new_stress == 0
    assert result.reasons == ("invalid_portfolio_input",)


def test_existing_stress_exactly_at_limit_blocks_new_with_stable_status() -> None:
    holding = _holding("H", "0.50", loss="0.10")
    result = allocate_portfolio(
        [_candidate("A", 1)], [holding], portfolio_equity="100000",
        effective_exposure_cap="1", evaluation_as_of="2025-01-01",
    )
    sizing = _result_by_symbol(result, "A")
    assert result.status != Phase4Status.INVALID
    assert result.existing_stress == Decimal("0.0500")
    assert result.stress_status == "AT_LIMIT"
    assert result.allocation_status == "BLOCK_NEW"
    assert result.final_new_stress == 0
    assert result.final_portfolio_stress == Decimal("0.0500")
    assert sizing.final_target_weight == 0 and sizing.order_qty == 0
    assert "existing_stress_at_limit" in result.reasons


def test_existing_stress_over_limit_blocks_without_clipping_or_mutation() -> None:
    holding = _holding("H", "0.51", loss="0.10")
    before = holding
    result = allocate_portfolio(
        [_candidate("A", 1)], [holding], portfolio_equity="100000",
        effective_exposure_cap="1", evaluation_as_of="2025-01-01",
    )
    sizing = _result_by_symbol(result, "A")
    assert result.status != Phase4Status.INVALID
    assert result.existing_stress == Decimal("0.0510")
    assert result.stress_status == "OVER_LIMIT"
    assert result.allocation_status == "BLOCK_NEW"
    assert result.final_new_stress == 0
    assert result.final_portfolio_stress == Decimal("0.0510")
    assert sizing.stress_scaled_weight >= 0
    assert sizing.final_target_weight == 0 and sizing.order_qty == 0
    assert "existing_stress_over_limit" in result.reasons
    assert holding is before


def test_empty_candidates_and_no_new_slots_have_stable_pressure_fields() -> None:
    empty = allocate_portfolio(
        [], [], portfolio_equity="100000", effective_exposure_cap="1",
        evaluation_as_of="2025-01-01",
    )
    full = allocate_portfolio(
        [_candidate("A", 1)],
        [_holding(f"H{i}", "0.01", loss="0.01") for i in range(1, 7)],
        portfolio_equity="100000", effective_exposure_cap="1",
        evaluation_as_of="2025-01-01",
    )
    for result in (empty, full):
        assert isinstance(result.final_new_stress, Decimal)
        assert isinstance(result.stress_status, str)
        assert isinstance(result.allocation_status, str)
        assert result.final_new_stress == 0


def test_duplicate_normalized_candidate_symbol_is_invalid() -> None:
    result = allocate_portfolio(
        [_candidate("600001", 1), _candidate("600001.SH", 2)],
        [],
        portfolio_equity="100000",
        effective_exposure_cap="1",
        evaluation_as_of="2025-01-01",
    )
    assert result.status == Phase4Status.INVALID
    assert result.reasons == ("duplicate_normalized_candidate_symbol:600001.SH",)
    assert all(item.final_target_weight == 0 and item.order_qty == 0 for item in result.sizing_results)


def test_duplicate_normalized_existing_symbol_is_invalid() -> None:
    first = _holding("600001", "0.1")
    second = replace(_holding("600001.SH", "0.1"), symbol="600001.SH")
    result = allocate_portfolio(
        [_candidate("A", 1)],
        [first, second],
        portfolio_equity="100000",
        effective_exposure_cap="1",
        evaluation_as_of="2025-01-01",
    )
    assert result.status == Phase4Status.INVALID
    assert result.reasons == ("duplicate_normalized_existing_symbol:600001.SH",)


@pytest.mark.parametrize(
    "cap", [Decimal("-0.1"), Decimal("1.1"), Decimal("NaN"), Decimal("Infinity"), 0.5, True]
)
def test_invalid_exposure_cap_rejects_without_orders(cap) -> None:
    result = allocate_portfolio(
        [_candidate("A", 1)], [], portfolio_equity="100000",
        effective_exposure_cap=cap, evaluation_as_of="2025-01-01",
    )
    assert result.status == Phase4Status.INVALID
    assert all(item.order_qty == 0 for item in result.sizing_results)


@pytest.mark.parametrize(
    "field,value",
    [
        ("weight", Decimal("-0.1")),
        ("weight", Decimal("NaN")),
        ("loss", Decimal("1.1")),
        ("loss", Decimal("Infinity")),
        ("scenario", Decimal("-0.1")),
        ("scenario", Decimal("1.1")),
        ("scenario", 0.1),
    ],
)
def test_invalid_existing_ranges_make_portfolio_invalid(field, value) -> None:
    kwargs = {"weight": "0.1", "loss": "0.05", "scenario": "0.10"}
    kwargs[field] = value
    holding = _holding("H", **kwargs)
    result = allocate_portfolio(
        [_candidate("A", 1)], [holding], portfolio_equity="100000",
        effective_exposure_cap="1", evaluation_as_of="2025-01-01",
    )
    assert result.status == Phase4Status.INVALID
    assert all(item.final_target_weight == 0 for item in result.sizing_results)


@pytest.mark.parametrize("loss", [Decimal("-0.1"), Decimal("1.1"), 0.1, True])
def test_invalid_candidate_t1_loss_makes_portfolio_invalid(loss) -> None:
    result = allocate_portfolio(
        [_candidate("A", 1, t1_loss_q=loss)], [], portfolio_equity="100000",
        effective_exposure_cap="1", evaluation_as_of="2025-01-01",
    )
    assert result.status == Phase4Status.INVALID
    assert all(item.final_target_weight == 0 and item.order_qty == 0 for item in result.sizing_results)


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        IndustryClassificationSnapshot("600001.SH", "", "Bank", "2024-01-01", "2024-01-02 09:00+08:00", "sw", "v1"),
        IndustryClassificationSnapshot("600001.SH", "801780", "Bank", "2026-01-01", "2024-01-02 09:00+08:00", "sw", "v1"),
        IndustryClassificationSnapshot("600001.SH", "801780", "Bank", "2024-01-01", "2026-01-02 09:00+08:00", "sw", "v1"),
        IndustryClassificationSnapshot("600001.SH", "801780", "Bank", "2024-01-01", "2024-01-02 09:00+08:00", "", "v1"),
        IndustryClassificationSnapshot("600001.SH", "801780", "Bank", "2024-01-01", "2024-01-02 09:00+08:00", "sw", ""),
    ],
)
def test_invalid_candidate_industry_snapshot_only_blocks_candidate(snapshot) -> None:
    candidate = replace(_candidate("A", 1), industry_snapshot=snapshot)
    result = allocate_portfolio(
        [candidate], [], portfolio_equity="100000", effective_exposure_cap="1",
        evaluation_as_of="2025-01-01",
    )
    assert result.status == Phase4Status.VALID
    sizing = _result_by_symbol(result, "A")
    assert sizing.final_target_weight == 0 and sizing.order_qty == 0
    assert sizing.failure_reasons == (f"invalid_candidate_industry_metadata:{_code('A')}",)


def test_invalid_existing_industry_snapshot_invalidates_portfolio() -> None:
    holding = replace(_holding("H", "0.1"), industry_snapshot=None)
    result = allocate_portfolio(
        [_candidate("A", 1)], [holding], portfolio_equity="100000",
        effective_exposure_cap="1", evaluation_as_of="2025-01-01",
    )
    assert result.status == Phase4Status.INVALID
    assert result.reasons == (f"invalid_existing_industry_metadata:{_code('H')}",)


def test_selected_symbols_are_normalized_unique_and_industry_weights_immutable() -> None:
    result = allocate_portfolio(
        [_candidate("600001", 1), _candidate("600002", 2)], [],
        portfolio_equity="100000", effective_exposure_cap="1",
        evaluation_as_of="2025-01-01",
    )
    assert result.selected_symbols == ("600001.SH", "600002.SH")
    assert len(set(result.selected_symbols)) == len(result.selected_symbols)
    assert isinstance(result.industry_weights, tuple)
    with pytest.raises(TypeError):
        result.industry_weights[0] = ("X", Decimal("1"))
