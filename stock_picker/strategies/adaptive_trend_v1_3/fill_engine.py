"""V1.3.4/V1.3.5 direct-fill calculator; it never mutates account state."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable

import pandas as pd

from stock_picker.strategies.adaptive_trend_v1_3.minute_contract import (
    SHANGHAI_TIMEZONE,
    normalize_security_symbol,
    resolve_next_execution_bar,
    validate_target_minute_bars,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase3_models import (
    ExecutionType,
    FeeRuleSnapshot,
    FillRequest,
    FillResult,
    FillSide,
    FillStatus,
    TradingRuleSnapshot,
)


CENT = Decimal("0.01")
ZERO = Decimal("0.00")
_RETRYABLE_REASONS = frozenset(
    {
        "suspended",
        "limit_down_sell",
        "unknown_trade_status",
        "unknown_limit_status",
        "missing_execution_bar",
    }
)
_SOFT_EXECUTIONS = frozenset(
    {
        ExecutionType.SOFT_EXIT,
        ExecutionType.REPLACEMENT_EXIT,
        ExecutionType.ORDINARY_REDUCTION,
    }
)


def execute_fill(
    request: FillRequest,
    minute_bars: pd.DataFrame,
    trading_rule: TradingRuleSnapshot,
    fee_rule: FeeRuleSnapshot,
    *,
    trading_calendar: Iterable[date | str | pd.Timestamp] = (),
) -> FillResult:
    """Attempt one all-or-nothing fill at the single contracted execution bar.

    Prices must be raw, unadjusted exchange prices supplied by the caller.
    The function performs no data lookup, quantity resizing, state mutation,
    partial fill, slippage, capacity check, queue simulation, or later-bar
    search.
    """

    execution_type = request.execution_type
    side = (
        FillSide.BUY
        if execution_type == ExecutionType.ENTRY_BUY
        else FillSide.SELL
    )
    try:
        symbol = normalize_security_symbol(request.symbol)
    except (TypeError, ValueError):
        return _failure(
            request,
            side,
            "invalid_symbol",
            status=FillStatus.INVALID,
            symbol=str(request.symbol),
        )

    target, resolution_reason = _execution_target(
        request, trading_calendar=trading_calendar
    )
    if resolution_reason:
        status = (
            FillStatus.FAILED
            if resolution_reason == "missing_execution_bar"
            else FillStatus.INVALID
        )
        return _failure(
            request,
            side,
            resolution_reason,
            status=status,
            symbol=symbol,
        )
    assert target is not None
    contract = validate_target_minute_bars(
        minute_bars, symbol=symbol, bar_start=target
    )
    if contract.status != "VALID":
        failure_reason = _contract_failure_reason(
            contract.invalid_reasons, contract.bars
        )
        return _failure(
            request,
            side,
            failure_reason,
            status=FillStatus.INVALID,
            symbol=symbol,
            target=target,
        )
    if contract.bars.empty:
        return _failure(
            request,
            side,
            "missing_execution_bar",
            status=FillStatus.FAILED,
            symbol=symbol,
            target=target,
        )

    quantity_reason = _quantity_input_reason(request)
    if quantity_reason:
        return _failure(
            request,
            side,
            quantity_reason,
            status=FillStatus.INVALID,
            symbol=symbol,
            target=target,
        )
    if _trading_rule_reason(trading_rule):
        return _failure(
            request,
            side,
            "invalid_rule_snapshot",
            status=FillStatus.INVALID,
            symbol=symbol,
            target=target,
        )
    if _fee_rule_reason(fee_rule):
        return _failure(
            request,
            side,
            "invalid_fee_snapshot",
            status=FillStatus.INVALID,
            symbol=symbol,
            target=target,
        )
    if not _rule_effective_on(trading_rule, target.date()):
        return _failure(
            request,
            side,
            "invalid_rule_snapshot",
            status=FillStatus.INVALID,
            symbol=symbol,
            target=target,
        )
    if not _fee_effective_on(fee_rule, target.date()):
        return _failure(
            request,
            side,
            "invalid_fee_snapshot",
            status=FillStatus.INVALID,
            symbol=symbol,
            target=target,
        )
    cash_available = _strict_decimal(request.cash_available)
    if cash_available is None or cash_available < 0:
        return _failure(
            request,
            side,
            "invalid_cash_available",
            status=FillStatus.INVALID,
            symbol=symbol,
            target=target,
        )
    bar = contract.bars.iloc[0]
    trade_status = str(bar["trade_status"])
    limit_status = str(bar["limit_status"])
    if trade_status == "suspended":
        return _failure(request, side, "suspended", symbol=symbol, target=target)
    if trade_status == "unknown":
        return _failure(
            request, side, "unknown_trade_status", symbol=symbol, target=target
        )
    if limit_status == "unknown":
        return _failure(
            request, side, "unknown_limit_status", symbol=symbol, target=target
        )
    if side == FillSide.BUY and limit_status == "limit_up":
        return _failure(request, side, "limit_up_buy", symbol=symbol, target=target)
    if side == FillSide.SELL and limit_status == "limit_down":
        return _failure(
            request, side, "limit_down_sell", symbol=symbol, target=target
        )

    if side == FillSide.BUY:
        if request.requested_qty % trading_rule.buy_lot_size != 0:
            return _failure(
                request,
                side,
                "invalid_lot_size",
                status=FillStatus.INVALID,
                symbol=symbol,
                target=target,
            )
    else:
        if request.requested_qty > request.position_qty:
            return _failure(
                request,
                side,
                "insufficient_position",
                symbol=symbol,
                target=target,
            )
        if request.requested_qty > request.sellable_qty:
            return _failure(
                request,
                side,
                "insufficient_sellable_qty",
                symbol=symbol,
                target=target,
            )
        full_exit = request.requested_qty == request.position_qty
        odd_lot_allowed = full_exit and trading_rule.full_exit_odd_lot_allowed
        if (
            not odd_lot_allowed
            and request.requested_qty % trading_rule.partial_sell_lot_size != 0
        ):
            return _failure(
                request,
                side,
                "invalid_lot_size",
                status=FillStatus.INVALID,
                symbol=symbol,
                target=target,
            )

    price = bar["open"]
    if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
        return _failure(
            request,
            side,
            "invalid_price",
            status=FillStatus.INVALID,
            symbol=symbol,
            target=target,
        )
    raw_gross = price * Decimal(request.requested_qty)
    fees = calculate_fill_fees(side, raw_gross, fee_rule)
    gross = _money(raw_gross)
    cash_required = (
        _money(raw_gross + fees["total_fees"])
        if side == FillSide.BUY
        else ZERO
    )
    net_proceeds = (
        _money(raw_gross - fees["total_fees"])
        if side == FillSide.SELL
        else ZERO
    )
    if side == FillSide.BUY:
        assert cash_available is not None
        if cash_available < cash_required:
            return _failure(
                request,
                side,
                "insufficient_cash",
                symbol=symbol,
                target=target,
            )

    return FillResult(
        status=FillStatus.FILLED,
        side=side,
        execution_type=execution_type,
        symbol=symbol,
        requested_qty=request.requested_qty,
        filled_qty=request.requested_qty,
        execution_trade_date=target.strftime("%Y-%m-%d"),
        execution_bar_start=target.isoformat(),
        execution_price=price,
        gross_amount=gross,
        commission=fees["commission"],
        stamp_tax=fees["stamp_tax"],
        transfer_fee=fees["transfer_fee"],
        settlement_fee=fees["settlement_fee"],
        total_fees=fees["total_fees"],
        cash_required=cash_required,
        net_proceeds=net_proceeds,
        failure_reason="",
        retryable=False,
        simplified_direct_fill=True,
    )


def calculate_fill_fees(
    side: FillSide,
    gross_amount: Decimal,
    rule: FeeRuleSnapshot,
) -> dict[str, Decimal]:
    """Calculate every fee component with Decimal and ROUND_HALF_UP cents."""

    gross = _strict_decimal(gross_amount)
    if gross is None:
        raise ValueError("invalid_gross_amount")
    if gross < 0:
        raise ValueError("negative_gross_amount")
    if gross == 0:
        return {
            "commission": ZERO,
            "stamp_tax": ZERO,
            "transfer_fee": ZERO,
            "settlement_fee": ZERO,
            "total_fees": ZERO,
        }
    commission = _money(
        max(
            gross * _required_strict_decimal(rule.commission_rate),
            _required_strict_decimal(rule.minimum_commission),
        )
    )
    if side == FillSide.BUY:
        transfer = _money(gross * _required_strict_decimal(rule.buy_transfer_fee_rate))
        settlement = _money(
            gross * _required_strict_decimal(rule.buy_settlement_fee_rate)
        )
        stamp_tax = ZERO
    else:
        transfer = _money(gross * _required_strict_decimal(rule.sell_transfer_fee_rate))
        settlement = _money(
            gross * _required_strict_decimal(rule.sell_settlement_fee_rate)
        )
        stamp_tax = _money(gross * _required_strict_decimal(rule.stamp_tax_rate))
    total = _money(commission + transfer + settlement + stamp_tax)
    return {
        "commission": commission,
        "stamp_tax": stamp_tax,
        "transfer_fee": transfer,
        "settlement_fee": settlement,
        "total_fees": total,
    }


def _execution_target(
    request: FillRequest,
    *,
    trading_calendar: Iterable[date | str | pd.Timestamp],
) -> tuple[pd.Timestamp | None, str]:
    signal = _parse_signal_time(request.signal_time)
    if signal is None:
        return None, "invalid_signal_time"
    if request.execution_type == ExecutionType.ENTRY_BUY:
        if signal.time().replace(tzinfo=None) != time(10, 0):
            return None, "invalid_signal_time"
        return _timestamp(signal.date(), time(10, 5)), ""
    if request.execution_type in _SOFT_EXECUTIONS:
        if signal.time().replace(tzinfo=None) != time(14, 30):
            return None, "invalid_signal_time"
        return _timestamp(signal.date(), time(14, 35)), ""
    if request.execution_type == ExecutionType.HARD_EXIT:
        resolution = resolve_next_execution_bar(signal, trading_calendar)
        return resolution.execution_bar_start, resolution.failure_reason
    return None, "invalid_execution_type"


def _failure(
    request: FillRequest,
    side: FillSide,
    reason: str,
    *,
    status: FillStatus = FillStatus.FAILED,
    symbol: str,
    target: pd.Timestamp | None = None,
) -> FillResult:
    return FillResult(
        status=status,
        side=side,
        execution_type=request.execution_type,
        symbol=symbol,
        requested_qty=request.requested_qty,
        filled_qty=0,
        execution_trade_date="" if target is None else target.strftime("%Y-%m-%d"),
        execution_bar_start="" if target is None else target.isoformat(),
        execution_price=None,
        gross_amount=ZERO,
        commission=ZERO,
        stamp_tax=ZERO,
        transfer_fee=ZERO,
        settlement_fee=ZERO,
        total_fees=ZERO,
        cash_required=ZERO,
        net_proceeds=ZERO,
        failure_reason=reason,
        retryable=reason in _RETRYABLE_REASONS,
        simplified_direct_fill=True,
    )


def _trading_rule_reason(rule: TradingRuleSnapshot) -> str:
    if not all(
        isinstance(value, str) and value.strip()
        for value in (rule.exchange, rule.board, rule.security_type)
    ):
        return "invalid_rule_snapshot"
    if not isinstance(rule.buy_lot_size, int) or isinstance(rule.buy_lot_size, bool) or rule.buy_lot_size <= 0:
        return "invalid_rule_snapshot"
    if (
        not isinstance(rule.partial_sell_lot_size, int)
        or isinstance(rule.partial_sell_lot_size, bool)
        or rule.partial_sell_lot_size <= 0
    ):
        return "invalid_rule_snapshot"
    if not isinstance(rule.full_exit_odd_lot_allowed, bool):
        return "invalid_rule_snapshot"
    tick = _strict_decimal(rule.price_tick)
    if tick is None or tick <= 0 or _parse_date(rule.effective_date) is None:
        return "invalid_rule_snapshot"
    return ""


def _fee_rule_reason(rule: FeeRuleSnapshot) -> str:
    for field in (
        "commission_rate",
        "minimum_commission",
        "buy_transfer_fee_rate",
        "sell_transfer_fee_rate",
        "buy_settlement_fee_rate",
        "sell_settlement_fee_rate",
        "stamp_tax_rate",
    ):
        value = _strict_decimal(getattr(rule, field))
        if value is None or value < 0:
            return "invalid_fee_snapshot"
        if field != "minimum_commission" and value > 1:
            return "invalid_fee_snapshot"
    if _parse_date(rule.effective_date) is None:
        return "invalid_fee_snapshot"
    return ""


def _rule_effective_on(rule: TradingRuleSnapshot, execution_date: date) -> bool:
    effective = _parse_date(rule.effective_date)
    return effective == execution_date


def _fee_effective_on(rule: FeeRuleSnapshot, execution_date: date) -> bool:
    return _parse_date(rule.effective_date) == execution_date


def _quantity_input_reason(request: FillRequest) -> str:
    if not _is_int(request.requested_qty) or request.requested_qty <= 0:
        return "invalid_quantity"
    if not _is_int(request.position_qty) or request.position_qty < 0:
        return "invalid_quantity"
    if not _is_int(request.sellable_qty) or request.sellable_qty < 0:
        return "invalid_quantity"
    return ""


def _contract_failure_reason(
    reasons: tuple[str, ...], bars: pd.DataFrame
) -> str:
    conflicts = sorted(
        reason
        for reason in reasons
        if reason.startswith("conflicting_duplicate_bar:")
    )
    if conflicts:
        return conflicts[0]
    if not bars.empty:
        open_price = bars.iloc[0]["open"]
        if (
            not isinstance(open_price, Decimal)
            or not open_price.is_finite()
            or open_price <= 0
        ):
            return "invalid_price"
    return "invalid_bar_contract"


def _parse_signal_time(value: object) -> pd.Timestamp | None:
    try:
        parsed = pd.Timestamp(value)
        if pd.isna(parsed):
            return None
        if parsed.tzinfo is None:
            return parsed.tz_localize(SHANGHAI_TIMEZONE)
        return parsed.tz_convert(SHANGHAI_TIMEZONE)
    except (TypeError, ValueError, OverflowError):
        return None


def _timestamp(trade_date: date, bar_time: time) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(trade_date, bar_time)).tz_localize(
        SHANGHAI_TIMEZONE
    )


def _parse_date(value: object) -> date | None:
    try:
        parsed = pd.Timestamp(value)
        return None if pd.isna(parsed) else parsed.date()
    except (TypeError, ValueError, OverflowError):
        return None


def _strict_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (Decimal, str)):
        return None
    try:
        parsed = Decimal(value)
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _required_strict_decimal(value: object) -> Decimal:
    parsed = _strict_decimal(value)
    if parsed is None:
        raise ValueError("invalid_decimal")
    return parsed


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)
