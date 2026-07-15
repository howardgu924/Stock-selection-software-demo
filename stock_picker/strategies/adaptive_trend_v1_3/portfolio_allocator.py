"""Deterministic Phase 4A allocation for new candidates only."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Sequence

import pandas as pd

from stock_picker.strategies.adaptive_trend_v1_3.minute_contract import (
    SHANGHAI_TIMEZONE,
    normalize_security_symbol,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase4_models import (
    CandidateInput,
    ExistingHolding,
    IndustryClassificationSnapshot,
    Phase4Status,
    PortfolioAllocationResult,
    SizingResult,
)
from stock_picker.strategies.adaptive_trend_v1_3.position_sizing import (
    calculate_candidate_sizing,
    order_quantity,
    pearson_correlation,
)

ZERO = Decimal("0")
ONE = Decimal("1")
MAX_WEIGHT = Decimal("0.15")
INDUSTRY_CAP = Decimal("0.30")
STRESS_CAP = Decimal("0.05")


def allocate_portfolio(
    candidates: Sequence[CandidateInput],
    existing_holdings: Sequence[ExistingHolding],
    *,
    portfolio_equity: Decimal | str,
    effective_exposure_cap: Decimal | str,
    evaluation_as_of: object,
) -> PortfolioAllocationResult:
    """Apply the frozen sizing sequence and never resize existing holdings."""

    equity = _positive_decimal(portfolio_equity)
    cap = _unit_decimal(effective_exposure_cap)
    evaluation = _as_of(evaluation_as_of)
    if equity is None or cap is None or evaluation is None:
        return _invalid_result(candidates, "invalid_portfolio_input")

    normalized_candidates, reason = _normalize_candidates(candidates)
    if reason:
        return _invalid_result(candidates, reason)
    normalized_holdings, reason = _normalize_holdings(existing_holdings)
    if reason:
        return _invalid_result(normalized_candidates, reason)

    validated_holdings: list[ExistingHolding] = []
    for holding in normalized_holdings:
        weight = _unit_decimal(holding.actual_weight)
        loss = _unit_decimal(holding.t1_loss_q)
        scenario = _unit_decimal(holding.scenario_loss_pct)
        if weight is None or loss is None or scenario is None:
            return _invalid_result(normalized_candidates, "invalid_existing_holding_input")
        if not _industry_snapshot_valid(holding.industry_snapshot, holding.symbol, evaluation):
            return _invalid_result(
                normalized_candidates,
                f"invalid_existing_industry_metadata:{holding.symbol}",
            )
        validated_holdings.append(
            replace(
                holding,
                actual_weight=weight,
                t1_loss_q=loss,
                scenario_loss_pct=scenario,
            )
        )

    existing_exposure = sum((item.actual_weight for item in validated_holdings), ZERO)
    if existing_exposure < 0 or existing_exposure > 1:
        return _invalid_result(normalized_candidates, "invalid_existing_exposure")
    existing_stress = sum(
        (item.actual_weight * item.t1_loss_q for item in validated_holdings), ZERO
    )
    if not existing_stress.is_finite() or existing_stress < 0:
        return _invalid_result(normalized_candidates, "invalid_existing_stress")

    validated_candidates: list[CandidateInput] = []
    invalid_industry_symbols: set[str] = set()
    for candidate in normalized_candidates:
        parsed = _validated_candidate(candidate)
        if parsed is None:
            return _invalid_result(normalized_candidates, "invalid_candidate_input")
        if not _industry_snapshot_valid(parsed.industry_snapshot, parsed.symbol, evaluation):
            invalid_industry_symbols.add(parsed.symbol)
        validated_candidates.append(parsed)

    base: dict[str, SizingResult] = {}
    for candidate in validated_candidates:
        sizing = calculate_candidate_sizing(candidate, validated_holdings)
        if candidate.symbol in invalid_industry_symbols:
            sizing = _zero(
                sizing, f"invalid_candidate_industry_metadata:{candidate.symbol}"
            )
        if not _sizing_contract_valid(sizing):
            return _invalid_result(validated_candidates, "invalid_sizing_result")
        base[candidate.symbol] = sizing
    candidate_by_symbol = {candidate.symbol: candidate for candidate in validated_candidates}

    stable = sorted(
        (candidate for candidate in validated_candidates if base[candidate.symbol].eligible),
        key=lambda item: (
            -item.opportunity_score,
            -item.rs60,
            -item.rs20,
            -item.signed_er20,
            item.symbol,
        ),
    )
    slots = max(0, 6 - len(validated_holdings))
    selected = stable[:slots]
    selected_symbols = {item.symbol for item in selected}
    results: dict[str, SizingResult] = {}
    for candidate in validated_candidates:
        sizing = base[candidate.symbol]
        if sizing.eligible and candidate.symbol not in selected_symbols:
            sizing = _zero(sizing, "max_positions")
        results[candidate.symbol] = sizing

    existing_industry: dict[str, Decimal] = {}
    for holding in validated_holdings:
        industry = holding.industry_snapshot.industry_code  # validated above
        existing_industry[industry] = existing_industry.get(industry, ZERO) + holding.actual_weight
    for industry in sorted(
        {item.industry_snapshot.industry_code for item in selected}
    ):
        members = [
            item for item in selected if item.industry_snapshot.industry_code == industry
        ]
        total = sum((results[item.symbol].adjusted_weight for item in members), ZERO)
        room = max(ZERO, INDUSTRY_CAP - existing_industry.get(industry, ZERO))
        scale = ONE if total <= room or total == 0 else room / total
        for item in members:
            sizing = results[item.symbol]
            results[item.symbol] = replace(
                sizing, industry_scaled_weight=sizing.adjusted_weight * scale
            )

    industry_sum = sum(
        (results[item.symbol].industry_scaled_weight for item in selected), ZERO
    )
    exposure_room = max(ZERO, cap - existing_exposure)
    exposure_scale = (
        ONE
        if industry_sum <= exposure_room or industry_sum == 0
        else exposure_room / industry_sum
    )
    for item in selected:
        sizing = results[item.symbol]
        results[item.symbol] = replace(
            sizing,
            exposure_scaled_weight=sizing.industry_scaled_weight * exposure_scale,
        )

    new_stress = sum(
        (
            results[item.symbol].exposure_scaled_weight * item.t1_loss_q
            for item in selected
        ),
        ZERO,
    )
    stress_room = max(ZERO, STRESS_CAP - existing_stress)
    stress_scale = (
        ONE if new_stress <= stress_room or new_stress == 0 else stress_room / new_stress
    )
    for item in selected:
        sizing = results[item.symbol]
        final_weight = sizing.exposure_scaled_weight * stress_scale
        quantity, actual_weight, reason = order_quantity(
            equity, item.execution_price, item.buy_lot_size, final_weight
        )
        reasons = sizing.failure_reasons + ((reason,) if reason else ())
        results[item.symbol] = replace(
            sizing,
            stress_scaled_weight=final_weight,
            final_target_weight=final_weight,
            order_qty=quantity,
            actual_order_weight=actual_weight,
            failure_reasons=reasons,
        )

    ordered_results = tuple(results[symbol] for symbol in sorted(results))
    if any(not _sizing_contract_valid(item) for item in ordered_results):
        return _invalid_result(validated_candidates, "invalid_final_sizing_result")
    final_new_exposure = sum((item.actual_order_weight for item in ordered_results), ZERO)
    final_new_stress = sum(
        (
            item.final_target_weight * candidate_by_symbol[item.symbol].t1_loss_q
            for item in ordered_results
        ),
        ZERO,
    )
    final_stress = existing_stress + final_new_stress
    if final_new_exposure < 0 or not final_new_exposure.is_finite():
        return _invalid_result(validated_candidates, "invalid_final_new_exposure")
    if existing_stress < STRESS_CAP and final_stress > STRESS_CAP:
        return _invalid_result(validated_candidates, "portfolio_stress_limit_exceeded")

    industry_weights = dict(existing_industry)
    for item in ordered_results:
        snapshot = candidate_by_symbol[item.symbol].industry_snapshot
        if item.actual_order_weight == 0 or not _industry_snapshot_valid(
            snapshot, item.symbol, evaluation
        ):
            continue
        industry = snapshot.industry_code
        industry_weights[industry] = industry_weights.get(industry, ZERO) + item.actual_order_weight

    if existing_stress < STRESS_CAP:
        stress_status = "NORMAL"
        allocation_status = "READY"
        portfolio_reasons: tuple[str, ...] = ()
    elif existing_stress == STRESS_CAP:
        stress_status = "AT_LIMIT"
        allocation_status = "BLOCK_NEW"
        portfolio_reasons = ("existing_stress_at_limit",)
    else:
        stress_status = "OVER_LIMIT"
        allocation_status = "BLOCK_NEW"
        portfolio_reasons = ("existing_stress_over_limit",)

    return PortfolioAllocationResult(
        selected_symbols=tuple(item.symbol for item in selected),
        existing_exposure=existing_exposure,
        effective_exposure_cap=cap,
        existing_stress=existing_stress,
        final_new_stress=final_new_stress,
        final_new_exposure=final_new_exposure,
        final_portfolio_stress=final_stress,
        stress_status=stress_status,
        allocation_status=allocation_status,
        industry_weights=tuple(sorted(industry_weights.items())),
        highest_correlation_pair=_highest_correlation_pair(validated_holdings),
        two_limit_down_scenario_loss=sum(
            (item.actual_weight * item.scenario_loss_pct for item in validated_holdings),
            ZERO,
        ),
        status=Phase4Status.VALID,
        reasons=portfolio_reasons,
        sizing_results=ordered_results,
    )


def _normalize_candidates(
    candidates: Sequence[CandidateInput],
) -> tuple[list[CandidateInput], str]:
    result: list[CandidateInput] = []
    seen: set[str] = set()
    for candidate in candidates:
        symbol = _symbol(candidate.symbol)
        if symbol is None:
            return [], "invalid_candidate_symbol"
        if symbol in seen:
            return [], f"duplicate_normalized_candidate_symbol:{symbol}"
        seen.add(symbol)
        result.append(replace(candidate, symbol=symbol))
    return result, ""


def _normalize_holdings(
    holdings: Sequence[ExistingHolding],
) -> tuple[list[ExistingHolding], str]:
    result: list[ExistingHolding] = []
    seen: set[str] = set()
    for holding in holdings:
        symbol = _symbol(holding.symbol)
        if symbol is None:
            return [], "invalid_existing_symbol"
        if symbol in seen:
            return [], f"duplicate_normalized_existing_symbol:{symbol}"
        seen.add(symbol)
        result.append(replace(holding, symbol=symbol))
    return result, ""


def _validated_candidate(candidate: CandidateInput) -> CandidateInput | None:
    score = _decimal(candidate.opportunity_score)
    threshold = _decimal(candidate.entry_threshold)
    rs60 = _decimal(candidate.rs60)
    rs20 = _decimal(candidate.rs20)
    signed = _decimal(candidate.signed_er20)
    loss = _unit_decimal(candidate.t1_loss_q)
    atr = _positive_decimal(candidate.entry_atr)
    entry_price = _positive_decimal(candidate.entry_price)
    execution_price = _positive_decimal(candidate.execution_price)
    if any(
        value is None
        for value in (score, threshold, rs60, rs20, signed, loss, atr, entry_price, execution_price)
    ):
        return None
    if type(candidate.opportunity_rank) is not int or type(candidate.buy_lot_size) is not int:
        return None
    if candidate.buy_lot_size <= 0:
        return None
    if type(candidate.market_paused) is not bool or type(candidate.cooldown_blocked) is not bool:
        return None
    return replace(
        candidate,
        opportunity_score=score,
        entry_threshold=threshold,
        rs60=rs60,
        rs20=rs20,
        signed_er20=signed,
        t1_loss_q=loss,
        entry_atr=atr,
        entry_price=entry_price,
        execution_price=execution_price,
    )


def _industry_snapshot_valid(
    snapshot: IndustryClassificationSnapshot | None,
    expected_symbol: str,
    evaluation_as_of: pd.Timestamp,
) -> bool:
    if snapshot is None or _symbol(snapshot.symbol) != expected_symbol:
        return False
    effective = _date(snapshot.effective_date)
    known = _as_of(snapshot.known_at, date_only_at_ten=False)
    return bool(
        effective is not None
        and known is not None
        and effective <= evaluation_as_of.date()
        and known < evaluation_as_of
        and str(snapshot.industry_code).strip()
        and str(snapshot.industry_name).strip()
        and str(snapshot.source).strip()
        and str(snapshot.classification_version).strip()
    )


def _sizing_contract_valid(result: SizingResult) -> bool:
    values = (
        result.raw_weight,
        result.risk_multiplier,
        result.gate_multiplier,
        result.correlation_multiplier,
        result.adjusted_weight,
        result.industry_scaled_weight,
        result.exposure_scaled_weight,
        result.stress_scaled_weight,
        result.final_target_weight,
        result.actual_order_weight,
    )
    parsed = tuple(_unit_decimal(value) for value in values)
    if any(value is None for value in parsed):
        return False
    return all(
        value <= MAX_WEIGHT
        for value in (
            parsed[0],
            parsed[4],
            parsed[5],
            parsed[6],
            parsed[7],
            parsed[8],
            parsed[9],
        )
    )


def _zero(result: SizingResult, reason: str) -> SizingResult:
    return replace(
        result,
        eligible=False,
        adjusted_weight=ZERO,
        industry_scaled_weight=ZERO,
        exposure_scaled_weight=ZERO,
        stress_scaled_weight=ZERO,
        final_target_weight=ZERO,
        order_qty=0,
        actual_order_weight=ZERO,
        failure_reasons=tuple(dict.fromkeys(result.failure_reasons + (reason,))),
    )


def _blank_sizing(symbol: str, reason: str) -> SizingResult:
    return SizingResult(
        symbol=symbol,
        eligible=False,
        raw_weight=ZERO,
        risk_multiplier=ZERO,
        gate_multiplier=ZERO,
        correlation_multiplier=ZERO,
        adjusted_weight=ZERO,
        industry_scaled_weight=ZERO,
        exposure_scaled_weight=ZERO,
        stress_scaled_weight=ZERO,
        final_target_weight=ZERO,
        order_qty=0,
        actual_order_weight=ZERO,
        failure_reasons=(reason,),
    )


def _highest_correlation_pair(
    holdings: Sequence[ExistingHolding],
) -> tuple[str, str, Decimal] | None:
    best: tuple[str, str, Decimal] | None = None
    ordered = sorted(holdings, key=lambda item: item.symbol)
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            correlation = pearson_correlation(left.daily_returns, right.daily_returns)
            if correlation is None:
                continue
            candidate = (left.symbol, right.symbol, correlation)
            if best is None or candidate[2] > best[2] or (
                candidate[2] == best[2] and candidate[:2] < best[:2]
            ):
                best = candidate
    return best


def _invalid_result(
    candidates: Sequence[CandidateInput], reason: str
) -> PortfolioAllocationResult:
    symbols: set[str] = set()
    for candidate in candidates:
        symbols.add(_symbol(candidate.symbol) or str(candidate.symbol))
    sizing_results = tuple(_blank_sizing(symbol, reason) for symbol in sorted(symbols))
    return PortfolioAllocationResult(
        selected_symbols=(),
        existing_exposure=ZERO,
        effective_exposure_cap=ZERO,
        existing_stress=ZERO,
        final_new_stress=ZERO,
        final_new_exposure=ZERO,
        final_portfolio_stress=ZERO,
        stress_status="INVALID",
        allocation_status="INVALID",
        industry_weights=(),
        highest_correlation_pair=None,
        two_limit_down_scenario_loss=ZERO,
        status=Phase4Status.INVALID,
        reasons=(reason,),
        sizing_results=sizing_results,
    )


def _symbol(value: object) -> str | None:
    try:
        return normalize_security_symbol(str(value))
    except ValueError:
        return None


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (Decimal, str)):
        return None
    try:
        parsed = Decimal(value)
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _positive_decimal(value: object) -> Decimal | None:
    parsed = _decimal(value)
    return parsed if parsed is not None and parsed > 0 else None


def _unit_decimal(value: object) -> Decimal | None:
    parsed = _decimal(value)
    return parsed if parsed is not None and ZERO <= parsed <= ONE else None


def _date(value: object) -> date | None:
    parsed = _as_of(value, date_only_at_ten=False)
    return None if parsed is None else parsed.date()


def _as_of(value: object, *, date_only_at_ten: bool = True) -> pd.Timestamp | None:
    try:
        date_only = isinstance(value, date) and not isinstance(value, datetime)
        if isinstance(value, str):
            stripped = value.strip()
            date_only = len(stripped) == 10 and stripped[4:5] == "-" and stripped[7:8] == "-"
        parsed = pd.Timestamp(value)
        if pd.isna(parsed):
            return None
        if date_only and date_only_at_ten:
            parsed = pd.Timestamp(datetime.combine(parsed.date(), time(10, 0)))
        if parsed.tzinfo is None:
            return parsed.tz_localize(SHANGHAI_TIMEZONE)
        return parsed.tz_convert(SHANGHAI_TIMEZONE)
    except (TypeError, ValueError, OverflowError):
        return None
