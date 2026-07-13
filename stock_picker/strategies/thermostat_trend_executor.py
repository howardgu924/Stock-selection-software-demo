from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from math import isfinite
from typing import Mapping

from stock_picker.data.models import normalize_symbol

from .thermostat import is_fake_breakout
from .thermostat_execution import (
    BacktestOrder,
    DailyBar,
    ExecutionCandidate,
    ExecutionPhase,
    OrderStatus,
    PortfolioLedger,
    T1ExecutionSettings,
    execute_buy,
    execute_sell,
    round_buy_shares,
)
from .thermostat_state import PendingSellLevel, ThermostatPositionState, TrendBatchRecord


_MODES = {"trend", "downtrend", "chaotic", "insufficient_data"}
_BATCH_RATIOS = (0.40, 0.35, 0.25)


@dataclass
class _TrendDayContext:
    symbol: str
    mode: str
    warnings: list[str]
    valid: bool
    plan_order_id: str
    effective_exit: float | None = None
    mid: float | None = None
    mid_was_armed: bool = False
    had_no_holding: bool = False
    batch_index: int | None = None
    batch_trigger: float | None = None
    ambiguous_exit: bool = False
    ambiguous_reduce: bool = False
    bought: bool = False
    buy_candidate: ExecutionCandidate | None = None
    blocked: bool = False
    strict_fake_breakout: bool = False
    finalized: bool = False
    portfolio_marks: Mapping[str, float] = field(default_factory=dict)

    @property
    def plan_token(self) -> str:
        return self.plan_order_id


def prepare_trend_day(
    plan: Mapping[str, object], bar: DailyBar, ledger: PortfolioLedger,
    settings: T1ExecutionSettings, trade_date: date,
    portfolio_marks: Mapping[str, float] | None = None,
) -> _TrendDayContext:
    raw_symbol = str(plan.get("symbol") or "INVALID")
    try:
        symbol = normalize_symbol(raw_symbol)
    except (TypeError, ValueError):
        symbol = raw_symbol
    key = ("trend", symbol, trade_date)
    existing = ledger._execution_contexts.get(key)
    if isinstance(existing, _TrendDayContext):
        return existing
    invalid = _validate_plan(plan, bar, trade_date)
    mode = str(plan.get("stock_mode") or plan.get("mode") or "")
    if invalid:
        reason = "trade_date_bar_date_mismatch" if invalid == ["trade_date_bar_date_mismatch"] else "invalid_plan"
        _audit_failure(ledger, plan, trade_date, reason, invalid)
        context = _TrendDayContext(
            symbol, mode, list(invalid), False, ledger.orders[-1].order_id,
        )
        ledger._execution_contexts[key] = context
        return context

    warnings: list[str] = []
    plan_order = _audit_plan(ledger, symbol, mode, trade_date)
    for field_name in ("high", "low", "close"):
        if getattr(bar, field_name) is None:
            _warn(warnings, f"missing_daily_{field_name}")
    state = _find_state(ledger, symbol)
    if state is not None and symbol not in ledger.positions:
        ledger.positions[symbol] = state
    if state is not None:
        state.start_trading_day(trade_date)
        state.transition_mode(
            mode,
            current_position_ratio=ledger.symbol_position_ratio(symbol, _marks(ledger, symbol, bar)),
        )
    if mode == "insufficient_data":
        _warn(warnings, "insufficient_data")
        if state is not None and state.total_shares:
            _warn(warnings, "holdings_preserved_without_current_plan_indicators")
    effective_exit = _effective_exit(plan, state)
    mid = _optional_finite(plan.get("trend_reduce_trigger"))
    had_no_holding = state is None or state.total_shares == 0
    batch_index = None
    batch_trigger = None
    if mode == "trend":
        batch_index, batch_trigger = _eligible_batch(plan, state, ledger, trade_date, warnings)
    buy_reached = batch_trigger is not None and _reached_above(bar.high, batch_trigger)
    context = _TrendDayContext(
        symbol=symbol, mode=mode, warnings=warnings, valid=True,
        plan_order_id=plan_order.order_id,
        effective_exit=effective_exit, mid=mid,
        mid_was_armed=bool(state is not None and state.mid_band_state == "above"),
        had_no_holding=had_no_holding, batch_index=batch_index,
        batch_trigger=batch_trigger,
        ambiguous_exit=bool(had_no_holding and buy_reached and _reached_below(bar.low, effective_exit)),
        ambiguous_reduce=bool(had_no_holding and buy_reached and _reached_below(bar.low, mid)),
        portfolio_marks=dict(portfolio_marks or {}),
    )
    ledger._execution_contexts[key] = context
    return context


def preview_trend_phase(
    plan: Mapping[str, object], bar: DailyBar, ledger: PortfolioLedger,
    settings: T1ExecutionSettings, trade_date: date, phase: ExecutionPhase,
) -> list[ExecutionCandidate]:
    del settings
    symbol = normalize_symbol(str(plan["symbol"]))
    context = ledger._execution_contexts.get(("trend", symbol, trade_date))
    if not isinstance(context, _TrendDayContext):
        raise RuntimeError("prepare_trend_day must be called before preview")
    phase = ExecutionPhase(phase)
    if context.finalized or not context.valid:
        return []
    state = _find_state(ledger, symbol)
    if phase is ExecutionPhase.RISK_CONTROL and context.mode == "downtrend" and state is not None and state.trend_shares:
        return [_trend_candidate(context, trade_date, phase, "downtrend_risk_sell", bar.close or state.average_cost or 1.0, "sell", None, 100)]
    exit_reached = _reached_below(bar.low, context.effective_exit)
    if phase is ExecutionPhase.TREND_EXIT and not context.had_no_holding and context.mode in {"trend", "chaotic", "insufficient_data"} and exit_reached and state is not None and state.trend_shares:
        return [_trend_candidate(context, trade_date, phase, "trend_exit", context.effective_exit or 1.0, "sell", None, 0)]
    reduce_reached = context.mid_was_armed and _reached_below(bar.low, context.mid)
    if phase is ExecutionPhase.TREND_REDUCE and context.mode == "trend" and not context.had_no_holding and reduce_reached and state is not None and state.trend_shares:
        return [_trend_candidate(context, trade_date, phase, "trend_reduce", context.mid or 1.0, "sell", None, 0)]
    if phase is ExecutionPhase.TREND_BUY and not context.blocked and context.mode == "trend" and context.batch_index is not None and context.batch_trigger is not None and _reached_above(bar.high, context.batch_trigger):
        current = _find_state(ledger, symbol)
        if current is not None and any(
            batch.fill_date == trade_date for batch in current.trend_batches
        ):
            return []
        if current is None or not current.blocked_new_buy:
            warnings = tuple(
                item for item, enabled in (
                    ("approximate_intraday_sequence", context.ambiguous_exit or context.ambiguous_reduce),
                    ("no_holding_exit_after_buy", context.ambiguous_exit),
                    ("no_holding_reduce_after_buy", context.ambiguous_reduce),
                ) if enabled
            )
            return [_trend_candidate(
                context, trade_date, phase,
                "trend_buy" if context.batch_index == 1 else "trend_add",
                context.batch_trigger, "buy", context.batch_index, 0, warnings,
            )]
    return []


def execute_trend_candidate(
    candidate: ExecutionCandidate, plan: Mapping[str, object], bar: DailyBar,
    ledger: PortfolioLedger, settings: T1ExecutionSettings, trade_date: date,
) -> list[BacktestOrder]:
    order_start = len(ledger.orders)
    symbol = normalize_symbol(str(plan["symbol"]))
    context = ledger._execution_contexts.get(("trend", symbol, trade_date))
    state = _find_state(ledger, symbol)
    stale = (
        not isinstance(context, _TrendDayContext) or not context.valid or context.finalized
        or candidate.trade_date != trade_date or candidate.symbol != symbol
        or candidate.family != "trend" or candidate.mode != context.mode
        or candidate.phase not in {
            ExecutionPhase.RISK_CONTROL, ExecutionPhase.TREND_EXIT,
            ExecutionPhase.TREND_REDUCE, ExecutionPhase.TREND_BUY,
        }
    )
    if not stale and candidate.phase is ExecutionPhase.TREND_BUY:
        batch = _batch_record(state, candidate.trend_batch or 0)
        stale = bool(
            context.mode != "trend" or state is not None and state.blocked_new_buy
            or candidate.trend_batch != context.batch_index
            or candidate.trigger_price != context.batch_trigger
            or not _reached_above(bar.high, candidate.trigger_price)
            or batch is not None and (
                batch.fill_date == trade_date
                or batch.planned_shares <= _batch_filled_shares(batch)
            )
        )
    elif not stale and candidate.phase in {ExecutionPhase.RISK_CONTROL, ExecutionPhase.TREND_EXIT, ExecutionPhase.TREND_REDUCE}:
        stale = bool(state is None or state.trend_shares <= 0)
        if candidate.phase is ExecutionPhase.RISK_CONTROL:
            stale = stale or context.mode != "downtrend"
        elif candidate.phase is ExecutionPhase.TREND_EXIT:
            stale = stale or context.mode not in {"trend", "chaotic", "insufficient_data"} or not _reached_below(bar.low, context.effective_exit)
        else:
            stale = stale or context.mode != "trend" or not context.mid_was_armed or not _reached_below(bar.low, context.mid)
    if stale:
        _audit_stale_trend_candidate(ledger, candidate, trade_date)
        orders = ledger.orders[order_start:]
        _link_trend_candidate_orders(orders, candidate)
        return orders

    execution_settings = _trend_execution_settings(plan, settings)
    if candidate.phase is ExecutionPhase.TREND_BUY:
        intended = _batch_shares(
            plan, ledger, state, candidate.trend_batch or 1,
            candidate.trigger_price, execution_settings,
        )
        order = execute_buy(
            ledger, execution_settings, bar, symbol=symbol, mode="trend", family="trend",
            trigger_type=candidate.trigger_type, trigger_price=candidate.trigger_price,
            intended_shares=intended, trade_date=trade_date,
            trend_batch=candidate.trend_batch, risk_rank=candidate.risk_rank,
            plan_priority=candidate.plan_priority,
            order_id=candidate.order_trace_id or None,
            portfolio_marks=context.portfolio_marks,
        )
        context.bought = order.status is OrderStatus.FILLED
        if context.bought:
            context.buy_candidate = candidate
    elif candidate.phase is ExecutionPhase.TREND_REDUCE:
        assert state is not None
        _execute_trend_reduce(
            ledger, execution_settings, bar, state, symbol, context.mode,
            context.mid, trade_date,
        )
    else:
        assert state is not None
        execute_sell(
            ledger, execution_settings, bar, symbol=symbol, mode=context.mode,
            family="trend", trigger_type=candidate.trigger_type,
            trigger_price=candidate.trigger_price,
            intended_shares=state.trend_shares, trade_date=trade_date,
            risk_rank=candidate.risk_rank, plan_priority=candidate.plan_priority,
            order_id=candidate.order_trace_id or None,
        )
        context.blocked = candidate.phase in {
            ExecutionPhase.RISK_CONTROL, ExecutionPhase.TREND_EXIT,
        }
    orders = ledger.orders[order_start:]
    _link_trend_candidate_orders(orders, candidate)
    return orders


def finalize_trend_day(
    plan: Mapping[str, object], bar: DailyBar, ledger: PortfolioLedger,
    settings: T1ExecutionSettings, trade_date: date,
) -> None:
    symbol = normalize_symbol(str(plan["symbol"]))
    context = ledger._execution_contexts.get(("trend", symbol, trade_date))
    if not isinstance(context, _TrendDayContext) or context.finalized:
        return
    state = _find_state(ledger, symbol)
    order_start = len(ledger.orders)
    if context.bought and state is not None:
        strict = is_fake_breakout(
            {"open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close, "volume": bar.volume},
            {"boll_upper": plan.get("boll_upper"), "volume_ma20": plan.get("volume_ma20")},
        )
        context.strict_fake_breakout = strict
        execution_settings = _trend_execution_settings(plan, settings)
        if context.ambiguous_exit or strict:
            _warn(context.warnings, "approximate_intraday_sequence" if context.ambiguous_exit else "")
            _warn(context.warnings, "strict_fake_breakout" if strict else "")
            execute_sell(
                ledger, execution_settings, bar, symbol=symbol, mode="trend", family="trend",
                trigger_type="trend_exit", trigger_price=context.effective_exit or bar.close or 1.0,
                intended_shares=state.trend_shares, trade_date=trade_date,
            )
        elif context.ambiguous_reduce:
            _warn(context.warnings, "approximate_intraday_sequence")
            _execute_trend_reduce(
                ledger, execution_settings, bar, state, symbol, "trend", context.mid, trade_date,
            )
    _update_mid_state(state, bar, context.mid, trade_date)
    if context.buy_candidate is not None:
        _link_trend_candidate_orders(
            ledger.orders[order_start:], context.buy_candidate,
        )
    context.finalized = True


def _trend_candidate(
    context: _TrendDayContext, trade_date: date, phase: ExecutionPhase,
    trigger_type: str, trigger_price: float, side: str, batch: int | None,
    risk_rank: int, warnings: tuple[str, ...] = (),
) -> ExecutionCandidate:
    owner_id = f"{batch:010d}" if batch is not None else ""
    return ExecutionCandidate(
        candidate_id=f"trend:{trade_date.isoformat()}:{context.symbol}:{phase.value}:{owner_id}",
        trade_date=trade_date, symbol=context.symbol, mode=context.mode,
        family="trend", phase=phase, trigger_type=trigger_type,
        trigger_price=trigger_price, side=side, owner_id=owner_id,
        trend_batch=batch, risk_rank=risk_rank,
        plan_trace_id=context.plan_token,
        order_trace_id=f"{context.plan_order_id}:trend:{phase.value}:{owner_id or 'all'}",
        approximation_warnings=warnings,
        metadata=((
            "quantity_policy",
            "batch_planned_gap" if side == "buy" else
            "available_half_and_locked_proportion" if phase is ExecutionPhase.TREND_REDUCE else
            "current_trend_owner",
        ),),
    )


def _trend_execution_settings(
    plan: Mapping[str, object], settings: T1ExecutionSettings,
) -> T1ExecutionSettings:
    return replace(
        settings,
        trend_symbol_base_max=min(
            settings.trend_symbol_base_max,
            float(plan["max_position_pct"]), float(plan["target_position_pct"]),
        ),
        trend_total_base_max=settings.trend_total_base_max * float(plan["market_position_discount"]),
    )


def _audit_stale_trend_candidate(
    ledger: PortfolioLedger, candidate: ExecutionCandidate, trade_date: date,
) -> None:
    ledger.orders.append(BacktestOrder(
        order_id=candidate.order_trace_id, trade_date=trade_date,
        symbol=candidate.symbol, mode=candidate.mode, family="trend",
        trigger_type=candidate.trigger_type, side=candidate.side,
        status=OrderStatus.CANCELLED, trigger_price=candidate.trigger_price,
        trend_batch=candidate.trend_batch, risk_rank=candidate.risk_rank,
        plan_priority=candidate.plan_priority, failure_reason="stale_candidate",
        quality_warning="candidate_revalidation_failed",
        plan_trace_id=candidate.plan_trace_id,
        candidate_trace_id=candidate.candidate_id,
    ))


def _link_trend_candidate_orders(
    orders: list[BacktestOrder], candidate: ExecutionCandidate,
) -> None:
    for order in orders:
        order.plan_trace_id = candidate.plan_trace_id
        order.candidate_trace_id = candidate.candidate_id


@dataclass
class TrendExecutionResult:
    orders: list[BacktestOrder] = field(default_factory=list)
    data_quality_warnings: list[str] = field(default_factory=list)
    strict_fake_breakout: bool = False
    next_batch_index: int = 1
    buys_blocked_for_day: bool = False


def execute_trend_day(
    plan: Mapping[str, object],
    bar: DailyBar,
    ledger: PortfolioLedger,
    settings: T1ExecutionSettings,
    trade_date: date,
) -> TrendExecutionResult:
    order_start = len(ledger.orders)
    context = prepare_trend_day(plan, bar, ledger, settings, trade_date)
    if context.valid:
        for phase in (
            ExecutionPhase.RISK_CONTROL,
            ExecutionPhase.TREND_EXIT,
            ExecutionPhase.TREND_REDUCE,
            ExecutionPhase.TREND_BUY,
        ):
            for candidate in sorted(
                preview_trend_phase(plan, bar, ledger, settings, trade_date, phase),
            ):
                execute_trend_candidate(
                    candidate, plan, bar, ledger, settings, trade_date,
                )
        finalize_trend_day(plan, bar, ledger, settings, trade_date)
    state = _find_state(ledger, context.symbol)
    return _result(
        ledger, order_start, context.warnings, context.strict_fake_breakout,
        _next_batch(state), bool(not context.valid or context.mode != "trend" or context.blocked),
    )


def _execute_trend_day_legacy(
    plan: Mapping[str, object],
    bar: DailyBar,
    ledger: PortfolioLedger,
    settings: T1ExecutionSettings,
    trade_date: date,
) -> TrendExecutionResult:
    """Execute one symbol's previous-day trend plan against one daily bar."""
    order_start = len(ledger.orders)
    warnings: list[str] = []
    invalid = _validate_plan(plan, bar, trade_date)
    if invalid:
        warnings.extend(invalid)
        reason = (
            "trade_date_bar_date_mismatch"
            if invalid == ["trade_date_bar_date_mismatch"]
            else "invalid_plan"
        )
        _audit_failure(ledger, plan, trade_date, reason, warnings)
        return _result(ledger, order_start, warnings, False, 1, True)

    symbol = normalize_symbol(str(plan["symbol"]))
    mode = str(plan.get("stock_mode") or plan.get("mode"))
    discount = float(plan["market_position_discount"])
    symbol_cap = min(float(plan["max_position_pct"]), float(plan["target_position_pct"]))
    execution_settings = replace(
        settings,
        trend_symbol_base_max=min(settings.trend_symbol_base_max, symbol_cap),
        trend_total_base_max=settings.trend_total_base_max * discount,
    )
    _audit_plan(ledger, symbol, mode, trade_date)
    for field_name in ("high", "low", "close"):
        if getattr(bar, field_name) is None:
            warnings.append(f"missing_daily_{field_name}")
    state = _find_state(ledger, symbol)
    if state is not None and symbol not in ledger.positions:
        ledger.positions[symbol] = state

    if state is not None:
        state.start_trading_day(trade_date)
        current_ratio = ledger.symbol_position_ratio(symbol, _marks(ledger, symbol, bar))
        state.transition_mode(mode, current_position_ratio=current_ratio)

    if mode == "insufficient_data":
        warnings.append("insufficient_data")
        if state is not None and state.total_shares:
            warnings.append("holdings_preserved_without_current_plan_indicators")
        return _result(ledger, order_start, warnings, False, _next_batch(state), True)

    if mode == "downtrend":
        if state is not None and state.trend_shares:
            execute_sell(
                ledger, execution_settings, bar, symbol=symbol, mode=mode, family="trend",
                trigger_type="downtrend_risk_sell",
                trigger_price=bar.close or state.average_cost or 1.0,
                intended_shares=state.trend_shares, trade_date=trade_date,
            )
        return _result(ledger, order_start, warnings, False, _next_batch(state), True)

    effective_exit = _effective_exit(plan, state)
    exit_reached = _reached_below(bar.low, effective_exit)
    mid = _optional_finite(plan.get("trend_reduce_trigger"))
    mid_was_armed = bool(state is not None and state.mid_band_state == "above")
    reduce_reached = mid_was_armed and _reached_below(bar.low, mid)

    if mode == "chaotic":
        if exit_reached and state is not None and state.trend_shares:
            execute_sell(
                ledger, execution_settings, bar, symbol=symbol, mode=mode, family="trend",
                trigger_type="trend_exit", trigger_price=effective_exit,
                intended_shares=state.trend_shares, trade_date=trade_date,
            )
        return _result(ledger, order_start, warnings, False, _next_batch(state), True)

    # A no-position daily bar cannot establish event order. The contract uses
    # buy first, then preserves a same-day sell as T+1 pending state.
    had_no_holding = state is None or state.total_shares == 0
    batch_index, batch_trigger = _eligible_batch(
        plan, state, ledger, trade_date, warnings,
    )
    buy_reached = batch_trigger is not None and _reached_above(bar.high, batch_trigger)
    ambiguous_exit = had_no_holding and buy_reached and exit_reached
    ambiguous_reduce = had_no_holding and buy_reached and _reached_below(bar.low, mid)

    blocked = False
    if not had_no_holding and exit_reached and state is not None and state.trend_shares:
        execute_sell(
            ledger, execution_settings, bar, symbol=symbol, mode=mode, family="trend",
            trigger_type="trend_exit", trigger_price=effective_exit,
            intended_shares=state.trend_shares, trade_date=trade_date,
        )
        blocked = True
    elif not had_no_holding and reduce_reached and state is not None and state.trend_shares:
        _execute_trend_reduce(
            ledger, execution_settings, bar, state, symbol, mode, mid, trade_date,
        )

    bought = None
    if not blocked and buy_reached and batch_index is not None:
        state = _find_state(ledger, symbol)
        if state is None or not state.blocked_new_buy:
            intended = _batch_shares(
                plan, ledger, state, batch_index, batch_trigger, execution_settings,
            )
            bought = execute_buy(
                ledger, execution_settings, bar, symbol=symbol, mode="trend", family="trend",
                trigger_type="trend_buy" if batch_index == 1 else "trend_add",
                trigger_price=batch_trigger, intended_shares=intended,
                trade_date=trade_date, trend_batch=batch_index,
            )
            state = _find_state(ledger, symbol)

    if bought is not None and bought.status is OrderStatus.FILLED and state is not None:
        strict = is_fake_breakout(
            {
                "open": bar.open, "high": bar.high, "low": bar.low,
                "close": bar.close, "volume": bar.volume,
            },
            {
                "boll_upper": plan.get("boll_upper"),
                "volume_ma20": plan.get("volume_ma20"),
            },
        )
        if ambiguous_exit or strict:
            if ambiguous_exit:
                _warn(warnings, "approximate_intraday_sequence")
            if strict:
                _warn(warnings, "strict_fake_breakout")
            execute_sell(
                ledger, execution_settings, bar, symbol=symbol, mode="trend", family="trend",
                trigger_type="trend_exit", trigger_price=effective_exit,
                intended_shares=state.trend_shares, trade_date=trade_date,
            )
            blocked = True
        elif ambiguous_reduce:
            _warn(warnings, "approximate_intraday_sequence")
            _execute_trend_reduce(
                ledger, execution_settings, bar, state, symbol, "trend", mid, trade_date,
            )
        _update_mid_state(state, bar, mid, trade_date)
        return _result(ledger, order_start, warnings, strict, _next_batch(state), blocked)

    _update_mid_state(state, bar, mid, trade_date)
    return _result(ledger, order_start, warnings, False, _next_batch(state), blocked)


def _validate_plan(plan: Mapping[str, object], bar: DailyBar, trade_date: date) -> list[str]:
    if trade_date != bar.date:
        return ["trade_date_bar_date_mismatch"]
    warnings: list[str] = []
    symbol = str(plan.get("symbol") or "").strip()
    if not symbol:
        warnings.append("invalid_plan_symbol")
    plan_date = _plan_date(plan.get("date"))
    if plan_date is None:
        warnings.append("invalid_plan_date")
    elif plan_date != trade_date:
        warnings.append("stale_plan_date" if plan_date < trade_date else "future_plan_date")
    data_cutoff = _plan_date(plan.get("data_cutoff_date"))
    if data_cutoff is None:
        warnings.append("invalid_data_cutoff_date")
    elif data_cutoff >= trade_date:
        warnings.append("data_cutoff_not_before_trade_date")
    mode = str(plan.get("stock_mode") or plan.get("mode") or "")
    if mode not in _MODES:
        warnings.append("invalid_plan_mode")
    discount = _optional_finite(plan.get("market_position_discount"))
    if discount is None or discount <= 0 or discount > 1:
        warnings.append("invalid_market_position_discount")
    trigger_names = (
        "trend_buy_trigger", "trend_reduce_trigger", "trend_exit_trigger",
        "effective_trend_exit_trigger",
    )
    for name in trigger_names:
        value = plan.get(name)
        if value not in (None, "") and _optional_finite(value) is None:
            warnings.append(f"invalid_{name}")
    if mode == "trend":
        for name in (
            "trend_buy_trigger", "trend_reduce_trigger", "atr20", "boll_upper",
            "volume_ma20", "target_position_pct",
            "max_position_pct",
        ):
            value = _optional_finite(plan.get(name))
            if value is None or value <= 0 or (
                name in {"target_position_pct", "max_position_pct"} and value > 1
            ):
                warnings.append(f"invalid_{name}")
        if _optional_finite(plan.get("effective_trend_exit_trigger")) is None and _optional_finite(
            plan.get("trend_exit_trigger")
        ) is None:
            warnings.append("invalid_trend_exit_trigger")
    return list(dict.fromkeys(warnings))


def _eligible_batch(
    plan: Mapping[str, object],
    state: ThermostatPositionState | None,
    ledger: PortfolioLedger,
    trade_date: date,
    warnings: list[str],
) -> tuple[int | None, float | None]:
    if state is not None and any(batch.fill_date == trade_date for batch in state.trend_batches):
        return None, None
    index = _next_batch(state)
    buy_trigger = _optional_finite(plan.get("trend_buy_trigger"))
    atr = _optional_finite(plan.get("atr20"))
    while index <= 3:
        trigger = buy_trigger
        if index > 1:
            previous = _batch_record(state, index - 1)
            previous_fill = previous.fill_price if previous is not None else None
            trigger = (previous_fill or buy_trigger) + 0.5 * atr
            if previous is not None and previous.fill_date is not None and previous.fill_date >= trade_date:
                return None, None
        if state is None or not _batch_is_covered(plan, state, ledger, index, trigger):
            return index, trigger
        _mark_batch_covered(state, plan, index, trigger)
        _warn(warnings, f"trend_batch_{index}_covered_by_existing_holding")
        index += 1
    return None, None


def _batch_is_covered(
    plan: Mapping[str, object],
    state: ThermostatPositionState,
    ledger: PortfolioLedger,
    index: int,
    trigger: float,
) -> bool:
    target_pct = _optional_finite(plan.get("target_position_pct")) or 0.0
    batch = _batch_record(state, index)
    if batch is not None and batch.planned_shares > _batch_filled_shares(batch):
        return False
    cumulative_ratio = sum(_BATCH_RATIOS[:index])
    covered_value = state.total_shares * trigger
    target_value = ledger.initial_capital * target_pct * cumulative_ratio
    return bool(target_value > 0 and covered_value + 1e-12 >= target_value)


def _mark_batch_covered(
    state: ThermostatPositionState,
    plan: Mapping[str, object],
    index: int,
    trigger: float,
) -> None:
    batch = _batch_record(state, index)
    if batch is None:
        owner_dates = [
            item.fill_date for item in state.trend_batches if item.fill_date is not None
        ] + [
            layer.buy_date for layer in state.grid_layers.values() if layer.buy_date is not None
        ]
        cutoff = _plan_date(plan.get("data_cutoff_date"))
        if cutoff is not None:
            owner_dates.append(cutoff)
        synthetic_shares = state.trend_shares if not state.trend_batches else 0
        first_owner_date = min(owner_dates) if owner_dates else cutoff
        last_owner_date = max(owner_dates) if owner_dates else cutoff
        batch = TrendBatchRecord(
            batch_index=index,
            target_ratio=_BATCH_RATIOS[index - 1],
            trigger_price=trigger,
            planned_shares=synthetic_shares,
            filled_shares=synthetic_shares,
            actual_shares=synthetic_shares,
            fill_price=_covered_holding_cost(state),
            fill_date=last_owner_date,
            first_fill_date=first_owner_date,
            last_fill_date=last_owner_date,
            available_shares=state.trend_available_shares if synthetic_shares else 0,
            today_bought_shares=state.trend_today_bought_shares if synthetic_shares else 0,
            status="covered",
        )
        state.trend_batches.append(batch)
        state.trend_batches.sort(key=lambda item: item.batch_index)
    elif batch.status == "waiting_buy":
        batch.status = "covered"
    state.trend_batch_index = max(state.trend_batch_index, index)
    state.assert_invariants()


def _batch_shares(
    plan: Mapping[str, object],
    ledger: PortfolioLedger,
    state: ThermostatPositionState | None,
    index: int,
    trigger: float,
    settings: T1ExecutionSettings,
) -> int:
    target_pct = _optional_finite(plan.get("target_position_pct")) or 0.0
    batch = _batch_record(state, index)
    if batch is not None and batch.planned_shares > _batch_filled_shares(batch):
        return round_buy_shares(
            batch.planned_shares - _batch_filled_shares(batch), settings.buy_lot_size,
        )
    target_value = ledger.initial_capital * target_pct * _BATCH_RATIOS[index - 1]
    if index == 1 and state is not None and not state.trend_batches:
        target_value = max(0.0, target_value - state.total_shares * trigger)
    return round_buy_shares(target_value / trigger, settings.buy_lot_size)


def _audit_plan(
    ledger: PortfolioLedger, symbol: str, mode: str, trade_date: date,
) -> BacktestOrder:
    order = BacktestOrder(
            order_id=f"order-{len(ledger.orders) + 1:08d}", trade_date=trade_date,
            symbol=symbol, mode=mode, family="trend", trigger_type="trend_plan",
            status=OrderStatus.PLAN_CREATED,
    )
    ledger.orders.append(order)
    return order


def _effective_exit(
    plan: Mapping[str, object], state: ThermostatPositionState | None,
) -> float | None:
    candidate = _optional_finite(plan.get("effective_trend_exit_trigger"))
    if candidate is None:
        candidate = _optional_finite(plan.get("trend_exit_trigger"))
    if state is not None:
        return state.update_effective_exit_trigger(candidate)
    return candidate


def _execute_trend_reduce(
    ledger: PortfolioLedger,
    settings: T1ExecutionSettings,
    bar: DailyBar,
    state: ThermostatPositionState,
    symbol: str,
    mode: str,
    trigger: float | None,
    trade_date: date,
) -> None:
    if trigger is None:
        return
    available_reduce = _reduce_quantity(state.trend_available_shares, settings.buy_lot_size)
    if available_reduce:
        execute_sell(
            ledger, settings, bar, symbol=symbol, mode=mode, family="trend",
            trigger_type="trend_reduce", trigger_price=trigger,
            intended_shares=available_reduce, trade_date=trade_date,
        )

    locked_reduce = _reduce_quantity(state.trend_today_bought_shares, settings.buy_lot_size)
    if not locked_reduce:
        return
    allocations = _locked_reduce_allocations(state, locked_reduce, settings.buy_lot_size)
    for batch_index, shares in allocations:
        state.queue_pending(
            PendingSellLevel.PENDING_REDUCE, shares, "trend", trade_date,
            batch_index=batch_index,
        )
        ledger.orders.append(
            BacktestOrder(
                order_id=f"order-{len(ledger.orders) + 1:08d}",
                trade_date=trade_date, symbol=symbol, mode=mode, family="trend",
                trigger_type="trend_reduce", side="sell", status=OrderStatus.PENDING,
                trigger_price=trigger, intended_shares=shares,
                position_before=state.total_shares, position_after=state.total_shares,
                pending_level=PendingSellLevel.PENDING_REDUCE,
                trend_batch=batch_index,
                quality_warning="t1_locked_reduce_queued",
            )
        )


def _reduce_quantity(shares: int, lot_size: int = 100) -> int:
    if shares < 200:
        return shares
    return round_buy_shares(shares * 0.50, lot_size)


def _locked_reduce_allocations(
    state: ThermostatPositionState, intended: int, lot_size: int,
) -> list[tuple[int | None, int]]:
    owners = [
        (batch.batch_index, batch.today_bought_shares)
        for batch in state.trend_batches
        if batch.today_bought_shares > 0
    ]
    total = sum(shares for _, shares in owners)
    if not owners or total != state.trend_today_bought_shares:
        return [(None, intended)]
    if intended == total:
        return owners
    allocations = [
        [batch_index, min(shares, round_buy_shares(intended * shares / total, lot_size))]
        for batch_index, shares in owners
    ]
    remaining = intended - sum(int(item[1]) for item in allocations)
    for allocation, (_, capacity) in zip(allocations, owners):
        if remaining < lot_size:
            break
        room = capacity - int(allocation[1])
        addition = min(round_buy_shares(room, lot_size), remaining)
        allocation[1] = int(allocation[1]) + addition
        remaining -= addition
    if remaining:
        for allocation, (_, capacity) in zip(allocations, owners):
            room = capacity - int(allocation[1])
            addition = min(room, remaining)
            allocation[1] = int(allocation[1]) + addition
            remaining -= addition
            if remaining == 0:
                break
    return [(int(index) if index is not None else None, int(shares)) for index, shares in allocations if shares]


def _covered_holding_cost(state: ThermostatPositionState) -> float:
    if state.average_cost > 0:
        return state.average_cost
    if state.trend_average_cost > 0:
        return state.trend_average_cost
    weighted = sum(
        layer.held_shares * (layer.buy_cost or 0.0) for layer in state.grid_layers.values()
    )
    shares = sum(layer.held_shares for layer in state.grid_layers.values())
    return weighted / shares if shares else 0.0


def _update_mid_state(
    state: ThermostatPositionState | None,
    bar: DailyBar,
    mid: float | None,
    trade_date: date,
) -> None:
    if state is not None and bar.close is not None and mid is not None:
        state.observe_boll_mid(bar.close, mid, trade_date)


def _next_batch(state: ThermostatPositionState | None) -> int:
    if state is None:
        return 1
    for batch in sorted(state.trend_batches, key=lambda item: item.batch_index):
        if (
            1 <= batch.batch_index <= 3
            and batch.status != "covered"
            and batch.planned_shares > _batch_filled_shares(batch)
        ):
            return batch.batch_index
    return min(max(state.trend_batch_index + 1, 1), 4)


def _batch_record(state: ThermostatPositionState | None, index: int) -> TrendBatchRecord | None:
    if state is None:
        return None
    return next((item for item in state.trend_batches if item.batch_index == index), None)


def _batch_filled_shares(batch: TrendBatchRecord) -> int:
    return max(batch.filled_shares, batch.actual_shares)


def _find_state(ledger: PortfolioLedger, symbol: str) -> ThermostatPositionState | None:
    return ledger.positions.get(symbol) or next(
        (state for key, state in ledger.positions.items() if normalize_symbol(key) == symbol), None,
    )


def _marks(ledger: PortfolioLedger, symbol: str, bar: DailyBar) -> dict[str, float]:
    marks = {key: state.average_cost for key, state in ledger.positions.items()}
    state = _find_state(ledger, symbol)
    marks[symbol] = bar.close or (state.average_cost if state is not None else 0.0)
    return marks


def _audit_failure(
    ledger: PortfolioLedger,
    plan: Mapping[str, object],
    trade_date: date,
    reason: str,
    warnings: list[str],
) -> None:
    raw_symbol = str(plan.get("symbol") or "INVALID")
    try:
        symbol = normalize_symbol(raw_symbol)
    except (TypeError, ValueError):
        symbol = raw_symbol
    order = BacktestOrder(
        order_id=f"order-{len(ledger.orders) + 1:08d}", trade_date=trade_date,
        symbol=symbol, mode=str(plan.get("stock_mode") or plan.get("mode") or ""),
        family="trend", trigger_type="plan_validation", status=OrderStatus.FAILED,
        failure_reason=reason, quality_warning=";".join(warnings),
    )
    ledger.orders.append(order)


def _result(
    ledger: PortfolioLedger,
    order_start: int,
    warnings: list[str],
    strict: bool,
    next_batch: int,
    blocked: bool,
) -> TrendExecutionResult:
    orders = ledger.orders[order_start:]
    for order in orders:
        for warning in filter(None, order.quality_warning.split(";")):
            _warn(warnings, warning)
    return TrendExecutionResult(orders, warnings, strict, next_batch, blocked)


def _optional_finite(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _plan_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _reached_above(observed: float | None, trigger: float | None) -> bool:
    return observed is not None and trigger is not None and observed >= trigger


def _reached_below(observed: float | None, trigger: float | None) -> bool:
    return observed is not None and trigger is not None and observed <= trigger


def _warn(warnings: list[str], warning: str) -> None:
    if warning and warning not in warnings:
        warnings.append(warning)
