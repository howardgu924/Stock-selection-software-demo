"""Pure Phase 4B hard/soft exit decisions; execution remains in Phase 3."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from types import MappingProxyType
from typing import Iterable, Sequence

import pandas as pd

from stock_picker.strategies.adaptive_trend_v1_3.minute_contract import (
    LEGAL_BAR_START_TIMES,
    SHANGHAI_TIMEZONE,
    normalize_security_symbol,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase3_models import (
    ExecutionType,
    FillRequest,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase4_models import PositionState
from stock_picker.strategies.adaptive_trend_v1_3.phase4b_models import (
    ExitControlState,
    ExitDecisionResult,
    ExitDecisionStatus,
    ExitIntent,
)


EXIT_PRIORITY = MappingProxyType({
    "EMERGENCY_MARKET": 100,
    "INITIAL_STOP": 90,
    "TRAILING_STOP": 85,
    "STRONG_TOP_DIVERGENCE": 70,
    "MA60_TREND_BREAK": 65,
    "WEAK_SCORE_CONFIRMED": 60,
    "NORMAL_TOP_DIVERGENCE_REDUCTION": 50,
    "MA20_BREAK_REDUCTION": 45,
    "PORTFOLIO_EXPOSURE_REDUCTION": 40,
    "REPLACEMENT_EXIT": 30,
})
_REASON_ORDER = tuple(EXIT_PRIORITY)
_STICKY_REASONS = frozenset({"EMERGENCY_MARKET", "INITIAL_STOP", "TRAILING_STOP"})
_SELL_EXECUTION_TYPES = frozenset(
    {
        ExecutionType.HARD_EXIT,
        ExecutionType.SOFT_EXIT,
        ExecutionType.REPLACEMENT_EXIT,
        ExecutionType.ORDINARY_REDUCTION,
    }
)


def canonical_exit_priority(reason: object) -> int:
    """Return the sole authoritative priority for a frozen exit reason."""

    value = str(getattr(reason, "value", reason)).strip()
    if value not in EXIT_PRIORITY:
        raise ValueError("invalid_exit_priority")
    return EXIT_PRIORITY[value]


def valid_exit_intent(intent: object) -> bool:
    """Defensively validate the externally constructible immutable contract."""

    if not isinstance(intent, ExitIntent):
        return False
    symbol = _symbol(intent.symbol)
    if symbol is None:
        return False
    try:
        priority = canonical_exit_priority(intent.reason)
    except ValueError:
        return False
    return (
        intent.priority == priority
        and intent.execution_type in _SELL_EXECUTION_TYPES
        and type(intent.requested_target_qty) is int
        and intent.requested_target_qty > 0
        and type(intent.full_exit) is bool
        and type(intent.sticky) is bool
        and type(intent.requires_revalidation) is bool
    )


def evaluate_hard_exit(
    position: PositionState,
    control: ExitControlState,
    *,
    trigger_bar_start: object,
    completed_bar_low: Decimal | str,
    emergency_status: object,
    price_basis_id: str,
) -> ExitDecisionResult:
    """Evaluate one completed five-minute bar without reading future prices."""

    symbol = _symbol(position.symbol)
    bar = _timestamp(trigger_bar_start)
    low = _decimal(completed_bar_low)
    if symbol is None or symbol != _symbol(control.symbol):
        return _invalid(position, control, "symbol_mismatch")
    if str(price_basis_id).strip() != control.price_basis_id:
        return _invalid(position, control, "price_basis_mismatch")
    if (
        bar is None
        or bar.time().replace(tzinfo=None) not in LEGAL_BAR_START_TIMES
        or low is None
        or low <= 0
    ):
        return _invalid(position, control, "invalid_completed_bar")
    if not _valid_quantities(position):
        return _invalid(position, control, "invalid_position_quantity")

    reasons: list[str] = []
    emergency_value = getattr(emergency_status, "emergency_status", emergency_status)
    emergency = str(getattr(emergency_value, "value", emergency_value)).upper()
    if emergency in {"LEVEL_1", "LEVEL_2", "EMERGENCY"}:
        reasons.append("EMERGENCY_MARKET")
    initial_hit = low <= control.initial_stop
    trailing_hit = control.trailing_stop > control.initial_stop and low <= control.trailing_stop
    if initial_hit:
        reasons.append("INITIAL_STOP")
    if trailing_hit:
        reasons.append("TRAILING_STOP")
    if not reasons:
        return _no_action(position, control)

    if "EMERGENCY_MARKET" in reasons:
        selected_reason = "EMERGENCY_MARKET"
        active_stop = max(control.initial_stop, control.trailing_stop)
    elif initial_hit and trailing_hit:
        if control.trailing_stop > control.initial_stop:
            selected_reason = "TRAILING_STOP"
            active_stop = control.trailing_stop
        else:
            selected_reason = "INITIAL_STOP"
            active_stop = control.initial_stop
    elif trailing_hit:
        selected_reason = "TRAILING_STOP"
        active_stop = control.trailing_stop
    else:
        selected_reason = "INITIAL_STOP"
        active_stop = control.initial_stop
    created = bar + pd.Timedelta(minutes=5)
    intent = _intent(
        symbol=symbol,
        day=bar.date(),
        decision_time=created.strftime("%H:%M"),
        execution_type=ExecutionType.HARD_EXIT,
        reason=selected_reason,
        target_qty=position.total_qty,
        full_exit=True,
        sticky=True,
        requires_revalidation=False,
        episode_id="",
        trigger_bar_start=bar,
        trigger_price=low,
        active_stop=active_stop,
        created_at=created,
        reasons=tuple(_sorted_reasons(reasons)),
    )
    return _decision(position, control, intent, reasons, active_stop)


def evaluate_1430_exit(
    position: PositionState,
    control: ExitControlState,
    *,
    decision_trade_date: date | str,
    p1430: Decimal | str,
    previous_ma20: Decimal | str,
    previous_ma60: Decimal | str,
    ma20_slope5: Decimal | str,
    opportunity_status: str,
    opportunity_score: Decimal | str,
    entry_threshold: Decimal | str,
    strong_top_divergence: bool,
    normal_top_divergence: bool,
    divergence_episode_id: str,
    partial_sell_lot_size: int,
    protected: bool,
    market_data_valid: bool = True,
) -> ExitDecisionResult:
    """Evaluate 14:30 signals using only the completed 14:25 bar and prior MAs."""

    symbol = _symbol(position.symbol)
    day = _date(decision_trade_date)
    price = _decimal(p1430)
    ma20 = _decimal(previous_ma20)
    ma60 = _decimal(previous_ma60)
    slope = _decimal(ma20_slope5)
    opportunity = str(getattr(opportunity_status, "value", opportunity_status)).upper()
    score = _decimal(opportunity_score) if opportunity == "VALID" else None
    threshold = _decimal(entry_threshold)
    if (
        symbol is None
        or symbol != _symbol(control.symbol)
        or day is None
        or opportunity not in {"VALID", "INVALID"}
        or type(strong_top_divergence) is not bool
        or type(normal_top_divergence) is not bool
        or type(protected) is not bool
        or type(market_data_valid) is not bool
    ):
        return _invalid(position, control, "invalid_soft_exit_input")
    if not _valid_quantities(position) or type(partial_sell_lot_size) is not int or partial_sell_lot_size <= 0:
        return _invalid(position, control, "invalid_soft_exit_quantity")
    if threshold is None or (opportunity == "VALID" and score is None):
        return _invalid(position, control, "invalid_soft_exit_decimal")
    if market_data_valid and any(value is None for value in (price, ma20, ma60, slope)):
        return _invalid(position, control, "invalid_soft_exit_decimal")

    repeated = control.last_1430_evaluation_date == day
    weak_streak = control.weak_score_streak
    ma20_episode = control.ma20_episode_id
    recovery = control.ma20_recovery_count
    if not repeated:
        if opportunity == "VALID":
            assert score is not None
            weak_streak = weak_streak + 1 if score < threshold - Decimal("12") else 0
        if market_data_valid:
            assert price is not None and ma20 is not None and slope is not None
            if price < ma20 and slope <= 0:
                if not ma20_episode:
                    ma20_episode = f"MA20_BREAK:{day.isoformat()}"
                recovery = 0
            elif ma20_episode and price >= ma20:
                recovery += 1
                if recovery >= 2:
                    ma20_episode = ""
                    recovery = 0
    new_control = replace(
        control,
        weak_score_streak=weak_streak,
        ma20_episode_id=ma20_episode,
        ma20_recovery_count=recovery,
        last_1430_evaluation_date=day,
    )

    if protected:
        return _no_action(position, new_control, previous=control)

    intents: list[ExitIntent] = []
    created = _at(day, time(14, 30))
    if strong_top_divergence:
        intents.append(_soft_full(symbol, day, "STRONG_TOP_DIVERGENCE", position.total_qty, price, created))
    if market_data_valid:
        assert price is not None and ma20 is not None and ma60 is not None
    if market_data_valid and price < ma60 and ma20 <= ma60:
        intents.append(_soft_full(symbol, day, "MA60_TREND_BREAK", position.total_qty, price, created))
    if weak_streak >= 2:
        intents.append(_soft_full(symbol, day, "WEAK_SCORE_CONFIRMED", position.total_qty, price, created))

    if not intents and normal_top_divergence:
        episode = str(divergence_episode_id).strip()
        if episode and episode not in control.acted_episode_ids:
            qty = reduction_quantity(position.total_qty, partial_sell_lot_size)
            if qty > 0:
                intents.append(
                    _soft_reduction(
                        symbol, day, "NORMAL_TOP_DIVERGENCE_REDUCTION", qty, price, created, episode
                    )
                )
    if (
        not intents
        and market_data_valid
        and ma20_episode
        and ma20_episode not in control.acted_episode_ids
    ):
        qty = reduction_quantity(position.total_qty, partial_sell_lot_size)
        if qty > 0:
            intents.append(
                _soft_reduction(symbol, day, "MA20_BREAK_REDUCTION", qty, price, created, ma20_episode)
            )
    if not intents:
        failure = "below_min_reduction_lot" if (
            (normal_top_divergence or (market_data_valid and bool(ma20_episode)))
            and reduction_quantity(position.total_qty, partial_sell_lot_size) == 0
        ) else ("market_data_invalid" if not market_data_valid else "")
        result = _no_action(position, new_control, previous=control)
        return replace(result, failure_reason=failure)

    selected = select_highest_intent(intents)
    assert selected is not None
    return _decision(
        position,
        control,
        selected,
        [intent.reason for intent in intents],
        max(control.initial_stop, control.trailing_stop),
        new_control=new_control,
    )


def select_highest_intent(intents: Sequence[ExitIntent]) -> ExitIntent | None:
    """Select by frozen priority then frozen reason order, never input order."""

    valid = [intent for intent in intents if valid_exit_intent(intent)]
    if not valid:
        return None
    return sorted(
        valid,
        key=lambda item: (
            -canonical_exit_priority(item.reason),
            _REASON_ORDER.index(item.reason) if item.reason in _REASON_ORDER else len(_REASON_ORDER),
            item.symbol,
            item.episode_id,
        ),
    )[0]


def reduction_quantity(total_qty: int, partial_sell_lot_size: int) -> int:
    if type(total_qty) is not int or total_qty <= 0:
        return 0
    if type(partial_sell_lot_size) is not int or partial_sell_lot_size <= 0:
        return 0
    raw = total_qty // 2
    return (raw // partial_sell_lot_size) * partial_sell_lot_size


def build_exit_fill_request(
    intent: ExitIntent,
    *,
    executable_qty: int,
    position_qty: int,
    sellable_qty: int,
) -> FillRequest:
    """Bridge an ExitIntent to Phase 3 without exceeding SellableQty."""

    if not isinstance(intent, ExitIntent) or _symbol(intent.symbol) is None:
        raise ValueError("invalid_exit_intent_contract")
    if intent.execution_type not in _SELL_EXECUTION_TYPES:
        raise ValueError("invalid_exit_execution_type")
    if not valid_exit_intent(intent):
        raise ValueError("invalid_exit_intent_contract")
    if any(
        type(value) is not int or value < 0
        for value in (executable_qty, position_qty, sellable_qty)
    ):
        raise ValueError("invalid_exit_fill_quantity")
    if (
        executable_qty <= 0
        or executable_qty > intent.requested_target_qty
        or executable_qty > sellable_qty
        or executable_qty > position_qty
        or sellable_qty > position_qty
    ):
        raise ValueError("invalid_exit_fill_quantity")
    signal_time: object = (
        intent.trigger_bar_start
        if intent.execution_type == ExecutionType.HARD_EXIT
        else _at(intent.decision_trade_date, time(14, 30))
    )
    return FillRequest(
        execution_type=intent.execution_type,
        symbol=_symbol(intent.symbol),
        requested_qty=executable_qty,
        signal_time=signal_time,
        cash_available=Decimal("0"),
        position_qty=position_qty,
        sellable_qty=sellable_qty,
    )


def _soft_full(
    symbol: str, day: date, reason: str, qty: int, price: Decimal, created: pd.Timestamp
) -> ExitIntent:
    return _intent(
        symbol, day, "14:30", ExecutionType.SOFT_EXIT, reason, qty, True, False, True,
        "", _at(day, time(14, 25)), price, None, created, (reason,)
    )


def _soft_reduction(
    symbol: str,
    day: date,
    reason: str,
    qty: int,
    price: Decimal,
    created: pd.Timestamp,
    episode_id: str,
) -> ExitIntent:
    return _intent(
        symbol, day, "14:30", ExecutionType.ORDINARY_REDUCTION, reason, qty, False,
        False, True, episode_id, _at(day, time(14, 25)), price, None, created, (reason,)
    )


def _intent(
    symbol: str,
    day: date,
    decision_time: str,
    execution_type: ExecutionType,
    reason: str,
    target_qty: int,
    full_exit: bool,
    sticky: bool,
    requires_revalidation: bool,
    episode_id: str,
    trigger_bar_start: pd.Timestamp | None,
    trigger_price: Decimal | None,
    active_stop: Decimal | None,
    created_at: pd.Timestamp,
    reasons: tuple[str, ...],
) -> ExitIntent:
    return ExitIntent(
        symbol=symbol,
        decision_trade_date=day,
        decision_time=decision_time,
        execution_type=execution_type,
        reason=reason,
        priority=canonical_exit_priority(reason),
        requested_target_qty=target_qty,
        full_exit=full_exit,
        sticky=sticky,
        requires_revalidation=requires_revalidation,
        episode_id=episode_id,
        trigger_bar_start=trigger_bar_start,
        trigger_price=trigger_price,
        active_stop=active_stop,
        created_at=created_at,
        reasons=reasons,
    )


def _decision(
    position: PositionState,
    previous_control: ExitControlState,
    intent: ExitIntent,
    reasons: Iterable[str],
    active_stop: Decimal,
    *,
    new_control: ExitControlState | None = None,
) -> ExitDecisionResult:
    desired = min(intent.requested_target_qty, position.total_qty)
    executable = min(desired, position.sellable_qty)
    pending = desired - executable
    return ExitDecisionResult(
        symbol=intent.symbol,
        status=ExitDecisionStatus.TRIGGERED,
        selected_intent=intent,
        all_triggered_reasons=tuple(_sorted_reasons(reasons)),
        active_stop=active_stop,
        sellable_qty=position.sellable_qty,
        unsellable_qty=position.total_qty - position.sellable_qty,
        executable_qty=executable,
        pending_remaining_qty=pending,
        previous_control_state=previous_control,
        new_control_state=new_control or previous_control,
    )


def _no_action(
    position: PositionState,
    control: ExitControlState,
    *,
    previous: ExitControlState | None = None,
) -> ExitDecisionResult:
    return ExitDecisionResult(
        symbol=_symbol(position.symbol) or str(position.symbol),
        status=ExitDecisionStatus.NO_ACTION,
        selected_intent=None,
        all_triggered_reasons=(),
        active_stop=max(control.initial_stop, control.trailing_stop),
        sellable_qty=position.sellable_qty,
        unsellable_qty=max(0, position.total_qty - position.sellable_qty),
        executable_qty=0,
        pending_remaining_qty=0,
        previous_control_state=previous or control,
        new_control_state=control,
    )


def _invalid(
    position: PositionState, control: ExitControlState, reason: str
) -> ExitDecisionResult:
    return replace(_no_action(position, control), status=ExitDecisionStatus.INVALID, failure_reason=reason)


def _sorted_reasons(values: Iterable[str]) -> list[str]:
    return sorted(
        set(values),
        key=lambda value: (
            -EXIT_PRIORITY.get(value, -1),
            _REASON_ORDER.index(value) if value in _REASON_ORDER else len(_REASON_ORDER),
            value,
        ),
    )


def _valid_quantities(position: PositionState) -> bool:
    return all(
        type(value) is int and value >= 0
        for value in (position.total_qty, position.sellable_qty, position.today_bought_qty)
    ) and position.sellable_qty <= position.total_qty


def _at(day: date, value: time) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(day, value)).tz_localize(SHANGHAI_TIMEZONE)


def _timestamp(value: object) -> pd.Timestamp | None:
    try:
        parsed = pd.Timestamp(value)
        if pd.isna(parsed):
            return None
        return parsed.tz_localize(SHANGHAI_TIMEZONE) if parsed.tzinfo is None else parsed.tz_convert(SHANGHAI_TIMEZONE)
    except (TypeError, ValueError, OverflowError):
        return None


def _date(value: object) -> date | None:
    try:
        parsed = pd.Timestamp(value)
        if pd.isna(parsed):
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.tz_convert(SHANGHAI_TIMEZONE)
        return parsed.date()
    except (TypeError, ValueError, OverflowError):
        return None


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
