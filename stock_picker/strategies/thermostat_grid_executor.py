from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from math import isfinite
from typing import Mapping

from stock_picker.data.models import normalize_symbol

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
from .thermostat_state import (
    GridLayerPosition,
    GridLayerStatus,
    PendingSellLevel,
    ThermostatPositionState,
)


_MODES = {"range", "downtrend", "chaotic", "insufficient_data"}


@dataclass
class _GridDayContext:
    symbol: str
    mode: str
    warnings: list[str]
    effective_count: int
    valid: bool
    plan_order_id: str
    lower_break: bool = False
    finalized: bool = False
    portfolio_marks: Mapping[str, float] = field(default_factory=dict)

    @property
    def plan_token(self) -> str:
        return self.plan_order_id


def prepare_grid_day(
    plan: Mapping[str, object],
    bar: DailyBar,
    ledger: PortfolioLedger,
    settings: T1ExecutionSettings,
    trade_date: date,
    portfolio_marks: Mapping[str, float] | None = None,
) -> _GridDayContext:
    raw_symbol = str(plan.get("symbol") or "INVALID")
    try:
        symbol = normalize_symbol(raw_symbol)
    except (TypeError, ValueError):
        symbol = raw_symbol
    key = ("grid", symbol, trade_date)
    existing = ledger._execution_contexts.get(key)
    if isinstance(existing, _GridDayContext):
        return existing

    invalid, buy_levels, sell_levels = _validate_plan(plan, bar, trade_date)
    mode = str(plan.get("stock_mode") or plan.get("mode") or "")
    if invalid:
        reason = "trade_date_bar_date_mismatch" if invalid == ["trade_date_bar_date_mismatch"] else "invalid_plan"
        _audit_failure(ledger, plan, trade_date, reason, invalid)
        context = _GridDayContext(
            symbol, mode, list(invalid), 0, False, ledger.orders[-1].order_id,
        )
        ledger._execution_contexts[key] = context
        return context

    warnings: list[str] = []
    plan_order = _audit_plan(ledger, symbol, mode, trade_date)
    for field_name in ("high", "low", "close"):
        if getattr(bar, field_name) is None:
            _warn(warnings, f"missing_daily_{field_name}")
    effective_count = len(buy_levels)
    effective_symbol_cap = min(
        settings.grid_symbol_base_max,
        float(plan["max_position_pct"]),
        float(plan["target_position_pct"]),
    )
    state = _find_state(ledger, symbol)
    if state is not None and symbol not in ledger.positions:
        ledger.positions[symbol] = state
    if state is not None:
        state.start_trading_day(trade_date)
        state.transition_mode(
            mode,
            current_position_ratio=ledger.symbol_position_ratio(symbol, _marks(ledger, symbol, bar)),
            range_cap_ratio=effective_symbol_cap if mode == "range" else None,
        )
    if mode == "range":
        if state is None:
            state = ThermostatPositionState(
                symbol=symbol, current_mode="range", blocked_new_buy=False,
                last_trading_date=trade_date,
            )
            ledger.positions[symbol] = state
        _synchronize_layers(
            state, buy_levels, sell_levels, effective_symbol_cap,
            ledger.initial_capital, settings.buy_lot_size,
        )
    lower = _optional_finite(plan.get("grid_lower"))
    lower_break = bool(mode == "range" and bar.low is not None and lower is not None and bar.low < lower)
    if lower_break and state is not None:
        state.blocked_new_buy = True
        _warn(warnings, "grid_lower_break_approximation")
        _warn(warnings, "daily_bar_intraday_sequence_approximation")
        _disable_and_cancel_waiting(ledger, state, symbol, mode, trade_date)
        _warn(warnings, "grid_lower_break_risk_exit")
    elif mode == "downtrend" and state is not None:
        _warn(warnings, "downtrend_grid_risk_exit")
    if mode == "insufficient_data":
        _warn(warnings, "insufficient_data")
        if state is not None and state.total_shares:
            _warn(warnings, "holdings_preserved_without_current_plan_indicators")
    context = _GridDayContext(
        symbol, mode, warnings, effective_count, True, plan_order.order_id,
        lower_break, portfolio_marks=dict(portfolio_marks or {}),
    )
    ledger._execution_contexts[key] = context
    return context


def preview_grid_phase(
    plan: Mapping[str, object],
    bar: DailyBar,
    ledger: PortfolioLedger,
    settings: T1ExecutionSettings,
    trade_date: date,
    phase: ExecutionPhase,
) -> list[ExecutionCandidate]:
    del settings
    symbol = normalize_symbol(str(plan["symbol"]))
    context = ledger._execution_contexts.get(("grid", symbol, trade_date))
    if not isinstance(context, _GridDayContext):
        raise RuntimeError("prepare_grid_day must be called before preview")
    phase = ExecutionPhase(phase)
    if context.finalized or not context.valid or context.mode in {"chaotic", "insufficient_data"}:
        return []
    state = _find_state(ledger, symbol)
    if state is None:
        return []
    if phase is ExecutionPhase.RISK_CONTROL and (context.mode == "downtrend" or context.lower_break):
        trigger = (
            bar.close or state.average_cost or 1.0
            if context.mode == "downtrend"
            else float(plan["grid_lower"])
        )
        return [
            _grid_candidate(context, trade_date, phase, "risk_control_sell", trigger, "sell", layer.layer_id, 100)
            for layer in state.grid_layers.values() if layer.held_shares > 0
        ]
    if context.mode != "range" or context.lower_break or _has_grid_fill_today(ledger, symbol, trade_date):
        return []
    if phase is ExecutionPhase.GRID_SELL:
        layer = _select_sell_layer(state, bar.high)
        return [] if layer is None else [
            _grid_candidate(context, trade_date, phase, "grid_sell", layer.sell_price, "sell", layer.layer_id, 0)
        ]
    if phase is ExecutionPhase.GRID_BUY and not state.blocked_new_buy:
        layer = _select_buy_layer(state, bar.low)
        return [] if layer is None else [
            _grid_candidate(context, trade_date, phase, "grid_buy", layer.buy_price, "buy", layer.layer_id, 0)
        ]
    return []


def execute_grid_candidate(
    candidate: ExecutionCandidate,
    plan: Mapping[str, object],
    bar: DailyBar,
    ledger: PortfolioLedger,
    settings: T1ExecutionSettings,
    trade_date: date,
) -> list[BacktestOrder]:
    order_start = len(ledger.orders)
    symbol = normalize_symbol(str(plan["symbol"]))
    state = _find_state(ledger, symbol)
    layer = state.grid_layers.get(candidate.grid_layer or "") if state is not None else None
    context = ledger._execution_contexts.get(("grid", symbol, trade_date))
    stale = (
        not isinstance(context, _GridDayContext) or not context.valid or context.finalized
        or candidate.trade_date != trade_date or candidate.symbol != symbol
        or candidate.family != "grid" or candidate.mode != context.mode
        or candidate.phase not in {
            ExecutionPhase.RISK_CONTROL, ExecutionPhase.GRID_SELL,
            ExecutionPhase.GRID_BUY,
        }
        or layer is None
    )
    if not stale and candidate.phase is ExecutionPhase.GRID_BUY:
        stale = bool(
            context.mode != "range" or context.lower_break or state.blocked_new_buy
            or layer.status is not GridLayerStatus.WAITING_BUY or layer.held_shares
            or bar.low is None or bar.low > layer.buy_price
            or _has_grid_fill_today(ledger, symbol, trade_date)
        )
    elif not stale and candidate.phase is ExecutionPhase.GRID_SELL:
        stale = bool(
            context.mode != "range" or layer.available_shares <= 0
            or bar.high is None or bar.high < layer.sell_price
            or _has_grid_fill_today(ledger, symbol, trade_date)
        )
    elif not stale and candidate.phase is ExecutionPhase.RISK_CONTROL:
        stale = bool(not (context.mode == "downtrend" or context.lower_break) or layer.held_shares <= 0)
    if stale:
        _audit_stale_grid_candidate(ledger, candidate, trade_date)
        orders = ledger.orders[order_start:]
        _link_grid_candidate_orders(orders, candidate)
        return orders

    execution_settings = _grid_execution_settings(plan, settings)
    if candidate.phase is ExecutionPhase.RISK_CONTROL:
        available = layer.available_shares
        locked = layer.today_bought_shares
        if available:
            execute_sell(
                ledger, execution_settings, bar, symbol=symbol, mode=context.mode,
                family="grid", trigger_type="risk_control_sell",
                trigger_price=candidate.trigger_price, intended_shares=available,
                trade_date=trade_date, grid_layer=layer.layer_id,
                risk_rank=candidate.risk_rank, plan_priority=candidate.plan_priority,
                order_id=candidate.order_trace_id or None,
            )
        if locked:
            effective_pending = state.queue_pending(
                PendingSellLevel.PENDING_EXIT, layer.held_shares, "grid", trade_date,
                grid_layer_id=layer.layer_id,
            )
            warning = "t1_locked_grid_exit_queued"
            if effective_pending.level is PendingSellLevel.PENDING_EMERGENCY_EXIT:
                warning += ";pending_priority_upgraded_to_emergency_exit"
            ledger.orders.append(BacktestOrder(
                order_id=_next_order_id(ledger), trade_date=trade_date,
                symbol=symbol, mode=context.mode, family="grid",
                trigger_type="risk_control_sell", side="sell",
                status=OrderStatus.PENDING, trigger_price=candidate.trigger_price,
                intended_shares=locked, position_before=state.total_shares,
                position_after=state.total_shares,
                pending_level=effective_pending.level, grid_layer=layer.layer_id,
                risk_rank=candidate.risk_rank, plan_priority=candidate.plan_priority,
                quality_warning=warning,
            ))
    elif candidate.phase is ExecutionPhase.GRID_BUY:
        intended = max(0, layer.target_shares - layer.held_shares)
        execute_buy(
            ledger, execution_settings, bar, symbol=symbol, mode="range", family="grid",
            trigger_type="grid_buy", trigger_price=layer.buy_price,
            intended_shares=intended, trade_date=trade_date, grid_layer=layer.layer_id,
            order_id=candidate.order_trace_id or None,
            portfolio_marks=context.portfolio_marks,
        )
    else:
        intended = layer.available_shares
        execute_sell(
            ledger, execution_settings, bar, symbol=symbol, mode=context.mode, family="grid",
            trigger_type=candidate.trigger_type, trigger_price=candidate.trigger_price,
            intended_shares=intended, trade_date=trade_date, grid_layer=layer.layer_id,
            risk_rank=candidate.risk_rank, plan_priority=candidate.plan_priority,
            order_id=candidate.order_trace_id or None,
        )
    orders = ledger.orders[order_start:]
    _link_grid_candidate_orders(orders, candidate)
    return orders


def finalize_grid_day(
    plan: Mapping[str, object], bar: DailyBar, ledger: PortfolioLedger,
    settings: T1ExecutionSettings, trade_date: date,
) -> None:
    del bar, settings
    symbol = normalize_symbol(str(plan["symbol"]))
    context = ledger._execution_contexts.get(("grid", symbol, trade_date))
    if isinstance(context, _GridDayContext):
        context.finalized = True


def _grid_candidate(
    context: _GridDayContext, trade_date: date, phase: ExecutionPhase,
    trigger_type: str, trigger_price: float, side: str, layer_id: str, risk_rank: int,
) -> ExecutionCandidate:
    return ExecutionCandidate(
        candidate_id=f"grid:{trade_date.isoformat()}:{context.symbol}:{phase.value}:{layer_id}",
        trade_date=trade_date, symbol=context.symbol, mode=context.mode, family="grid", phase=phase,
        trigger_type=trigger_type, trigger_price=trigger_price, side=side,
        owner_id=layer_id, grid_layer=layer_id, risk_rank=risk_rank,
        plan_trace_id=context.plan_token,
        order_trace_id=f"{context.plan_order_id}:grid:{phase.value}:{layer_id}",
        metadata=((
            "quantity_policy",
            "layer_target_gap" if side == "buy" else
            "layer_held" if phase is ExecutionPhase.RISK_CONTROL else
            "layer_available",
        ),),
    )


def _grid_execution_settings(
    plan: Mapping[str, object], settings: T1ExecutionSettings,
) -> T1ExecutionSettings:
    return replace(
        settings,
        grid_symbol_base_max=min(
            settings.grid_symbol_base_max,
            float(plan["max_position_pct"]), float(plan["target_position_pct"]),
        ),
        grid_total_hard_max=min(
            0.40, settings.grid_total_hard_max,
            float(plan["grid_total_max_position_pct"]),
        ),
    )


def _audit_stale_grid_candidate(
    ledger: PortfolioLedger, candidate: ExecutionCandidate, trade_date: date,
) -> None:
    ledger.orders.append(BacktestOrder(
        order_id=candidate.order_trace_id, trade_date=trade_date,
        symbol=candidate.symbol, mode=candidate.mode, family="grid",
        trigger_type=candidate.trigger_type, side=candidate.side,
        status=OrderStatus.CANCELLED, trigger_price=candidate.trigger_price,
        grid_layer=candidate.grid_layer, risk_rank=candidate.risk_rank,
        plan_priority=candidate.plan_priority, failure_reason="stale_candidate",
        quality_warning="candidate_revalidation_failed",
        plan_trace_id=candidate.plan_trace_id,
        candidate_trace_id=candidate.candidate_id,
    ))


def _link_grid_candidate_orders(
    orders: list[BacktestOrder], candidate: ExecutionCandidate,
) -> None:
    for order in orders:
        order.plan_trace_id = candidate.plan_trace_id
        order.candidate_trace_id = candidate.candidate_id


@dataclass
class GridExecutionResult:
    orders: list[BacktestOrder] = field(default_factory=list)
    data_quality_warnings: list[str] = field(default_factory=list)
    effective_layer_count: int = 0
    selected_buy_layer: str | None = None
    selected_sell_layer: str | None = None
    buys_blocked_for_day: bool = False


def execute_grid_day(
    plan: Mapping[str, object],
    bar: DailyBar,
    ledger: PortfolioLedger,
    settings: T1ExecutionSettings,
    trade_date: date,
) -> GridExecutionResult:
    order_start = len(ledger.orders)
    context = prepare_grid_day(plan, bar, ledger, settings, trade_date)
    selected_buy = None
    selected_sell = None
    if context.valid:
        for phase in (
            ExecutionPhase.RISK_CONTROL,
            ExecutionPhase.GRID_SELL,
            ExecutionPhase.GRID_BUY,
        ):
            for candidate in sorted(
                preview_grid_phase(plan, bar, ledger, settings, trade_date, phase),
            ):
                execute_grid_candidate(
                    candidate, plan, bar, ledger, settings, trade_date,
                )
                if phase is ExecutionPhase.GRID_SELL:
                    selected_sell = candidate.grid_layer
                elif phase is ExecutionPhase.GRID_BUY:
                    selected_buy = candidate.grid_layer
        finalize_grid_day(plan, bar, ledger, settings, trade_date)
    state = _find_state(ledger, context.symbol)
    blocked = bool(
        not context.valid or context.mode != "range" or context.lower_break
        or state is not None and state.blocked_new_buy
    )
    return _result(
        ledger, order_start, context.warnings, context.effective_count,
        selected_buy, selected_sell, blocked,
    )


def _execute_grid_day_legacy(
    plan: Mapping[str, object],
    bar: DailyBar,
    ledger: PortfolioLedger,
    settings: T1ExecutionSettings,
    trade_date: date,
) -> GridExecutionResult:
    """Execute one symbol's current-date grid plan against one daily bar."""
    order_start = len(ledger.orders)
    warnings: list[str] = []
    invalid, buy_levels, sell_levels = _validate_plan(plan, bar, trade_date)
    if invalid:
        warnings.extend(invalid)
        reason = (
            "trade_date_bar_date_mismatch"
            if invalid == ["trade_date_bar_date_mismatch"]
            else "invalid_plan"
        )
        _audit_failure(ledger, plan, trade_date, reason, warnings)
        return _result(ledger, order_start, warnings, 0, None, None, True)

    symbol = normalize_symbol(str(plan["symbol"]))
    mode = str(plan.get("stock_mode") or plan.get("mode"))
    effective_count = len(buy_levels)
    plan_cap = min(
        float(plan["max_position_pct"]), float(plan["target_position_pct"]),
    )
    plan_total_cap = float(plan["grid_total_max_position_pct"])
    effective_symbol_cap = min(settings.grid_symbol_base_max, plan_cap)
    execution_settings = replace(
        settings,
        grid_symbol_base_max=effective_symbol_cap,
        grid_total_hard_max=min(
            0.40, settings.grid_total_hard_max, plan_total_cap,
        ),
    )

    _audit_plan(ledger, symbol, mode, trade_date)
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
            current_position_ratio=ledger.symbol_position_ratio(
                symbol, _marks(ledger, symbol, bar),
            ),
            range_cap_ratio=effective_symbol_cap if mode == "range" else None,
        )

    if mode == "insufficient_data":
        _warn(warnings, "insufficient_data")
        if state is not None and state.total_shares:
            _warn(warnings, "holdings_preserved_without_current_plan_indicators")
        return _result(
            ledger, order_start, warnings, effective_count, None, None, True,
        )

    if mode == "chaotic":
        return _result(
            ledger, order_start, warnings, effective_count, None, None, True,
        )

    if mode == "downtrend":
        if state is not None:
            _risk_exit_layers(
                ledger, execution_settings, bar, state, symbol, mode,
                bar.close or state.average_cost or 1.0, trade_date, warnings,
                warning="downtrend_grid_risk_exit",
            )
        return _result(
            ledger, order_start, warnings, effective_count, None, None, True,
        )

    if state is None:
        state = ThermostatPositionState(
            symbol=symbol, current_mode="range", blocked_new_buy=False,
            last_trading_date=trade_date,
        )
        ledger.positions[symbol] = state
    _synchronize_layers(
        state, buy_levels, sell_levels, effective_symbol_cap, ledger.initial_capital,
        settings.buy_lot_size,
    )

    lower = _optional_finite(plan.get("grid_lower"))
    if bar.low is not None and lower is not None and bar.low < lower:
        state.blocked_new_buy = True
        _warn(warnings, "grid_lower_break_approximation")
        _warn(warnings, "daily_bar_intraday_sequence_approximation")
        _disable_and_cancel_waiting(ledger, state, symbol, mode, trade_date)
        _risk_exit_layers(
            ledger, execution_settings, bar, state, symbol, mode, lower,
            trade_date, warnings, warning="grid_lower_break_risk_exit",
        )
        return _result(
            ledger, order_start, warnings, effective_count, None, None, True,
        )

    if _has_grid_fill_today(ledger, symbol, trade_date):
        return _result(
            ledger, order_start, warnings, effective_count, None, None,
            state.blocked_new_buy,
        )

    sell_layer = _select_sell_layer(state, bar.high)
    if sell_layer is not None:
        execute_sell(
            ledger, execution_settings, bar, symbol=symbol, mode=mode,
            family="grid", trigger_type="grid_sell",
            trigger_price=sell_layer.sell_price,
            intended_shares=sell_layer.available_shares,
            trade_date=trade_date, grid_layer=sell_layer.layer_id,
        )
        return _result(
            ledger, order_start, warnings, effective_count, None,
            sell_layer.layer_id, state.blocked_new_buy,
        )

    buy_layer = None if state.blocked_new_buy else _select_buy_layer(state, bar.low)
    if buy_layer is not None:
        execute_buy(
            ledger, execution_settings, bar, symbol=symbol, mode="range",
            family="grid", trigger_type="grid_buy",
            trigger_price=buy_layer.buy_price,
            intended_shares=buy_layer.target_shares,
            trade_date=trade_date, grid_layer=buy_layer.layer_id,
        )
    return _result(
        ledger, order_start, warnings, effective_count,
        buy_layer.layer_id if buy_layer is not None else None,
        None, state.blocked_new_buy,
    )


def _validate_plan(
    plan: Mapping[str, object], bar: DailyBar, trade_date: date,
) -> tuple[list[str], list[float], list[float]]:
    if trade_date != bar.date:
        return ["trade_date_bar_date_mismatch"], [], []
    warnings: list[str] = []
    if not str(plan.get("symbol") or "").strip():
        warnings.append("invalid_plan_symbol")
    plan_date = _plan_date(plan.get("date"))
    if plan_date is None:
        warnings.append("invalid_plan_date")
    elif plan_date != trade_date:
        warnings.append("stale_plan_date" if plan_date < trade_date else "future_plan_date")
    cutoff = _plan_date(plan.get("data_cutoff_date"))
    if cutoff is None:
        warnings.append("invalid_data_cutoff_date")
    elif cutoff >= trade_date:
        warnings.append("data_cutoff_not_before_trade_date")
    mode = str(plan.get("stock_mode") or plan.get("mode") or "")
    if mode not in _MODES:
        warnings.append("invalid_plan_mode")
    discount = _optional_finite(plan.get("market_position_discount"))
    if discount is None or not 0 < discount <= 1:
        warnings.append("invalid_market_position_discount")
    for name in ("target_position_pct", "max_position_pct"):
        cap = _optional_finite(plan.get(name))
        if cap is None or not 0 < cap <= 1:
            warnings.append(f"invalid_{name}")
    total_cap = _optional_finite(plan.get("grid_total_max_position_pct"))
    if total_cap is None or not 0 < total_cap <= 0.40:
        warnings.append("invalid_grid_total_max_position_pct")

    buys: list[float] = []
    sells: list[float] = []
    if mode == "range":
        buys, buy_error = _parse_levels(plan.get("grid_buy_levels"))
        sells, sell_error = _parse_levels(plan.get("grid_sell_levels"))
        if buy_error or not buys or not _strictly_descending(buys):
            warnings.append("invalid_grid_buy_levels")
        if sell_error or not sells or not _strictly_ascending(sells):
            warnings.append("invalid_grid_sell_levels")
        if buys and sells and len(buys) != len(sells):
            warnings.append("mismatched_grid_layer_count")
        configured = _integer(plan.get("configured_grid_layers"))
        effective = _integer(plan.get("effective_grid_layers"))
        if configured != 3:
            warnings.append("invalid_configured_grid_layers")
        if effective != len(buys) or effective != len(sells):
            warnings.append("invalid_effective_grid_layers")
        for name in ("grid_lower", "grid_mid", "grid_upper"):
            value = _optional_finite(plan.get(name))
            if value is None or value <= 0:
                warnings.append(f"invalid_{name}")
        lower = _optional_finite(plan.get("grid_lower"))
        mid = _optional_finite(plan.get("grid_mid"))
        upper = _optional_finite(plan.get("grid_upper"))
        if lower is not None and mid is not None and upper is not None and not lower < mid < upper:
            warnings.append("invalid_grid_bounds")
    return list(dict.fromkeys(warnings)), buys, sells


def _parse_levels(value: object) -> tuple[list[float], bool]:
    if isinstance(value, str):
        raw = [item.strip() for item in value.split("|") if item.strip()]
    elif isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        return [], True
    parsed: list[float] = []
    invalid = False
    for item in raw:
        number = _optional_finite(item)
        if number is None or number <= 0:
            invalid = True
            continue
        if number not in parsed:
            parsed.append(number)
    return parsed, invalid


def _synchronize_layers(
    state: ThermostatPositionState,
    buys: list[float],
    sells: list[float],
    symbol_cap: float,
    initial_capital: float,
    lot_size: int,
) -> None:
    count = len(buys)
    layer_pct = symbol_cap / count
    active_ids: set[str] = set()
    for index, (buy_price, sell_price) in enumerate(zip(buys, sells), start=1):
        layer_id = f"grid-{index}"
        active_ids.add(layer_id)
        target_shares = round_buy_shares(
            initial_capital * layer_pct / buy_price, lot_size,
        )
        layer = state.grid_layers.get(layer_id)
        if layer is None:
            state.grid_layers[layer_id] = GridLayerPosition(
                layer_id=layer_id, buy_price=buy_price, sell_price=sell_price,
                target_position_pct=layer_pct, target_shares=target_shares,
            )
            continue
        layer.buy_price = buy_price
        layer.sell_price = sell_price
        layer.target_position_pct = layer_pct
        layer.target_shares = target_shares
        if layer.held_shares == 0:
            layer.status = GridLayerStatus.WAITING_BUY
    for layer_id, layer in state.grid_layers.items():
        if layer_id not in active_ids and layer.held_shares == 0:
            layer.status = GridLayerStatus.DISABLED
    state.assert_invariants()


def _select_buy_layer(
    state: ThermostatPositionState, daily_low: float | None,
) -> GridLayerPosition | None:
    if daily_low is None:
        return None
    candidates = [
        layer for layer in state.grid_layers.values()
        if layer.status is GridLayerStatus.WAITING_BUY
        and layer.held_shares == 0
        and daily_low <= layer.buy_price
    ]
    return max(candidates, key=lambda layer: layer.buy_price, default=None)


def _select_sell_layer(
    state: ThermostatPositionState, daily_high: float | None,
) -> GridLayerPosition | None:
    if daily_high is None:
        return None
    candidates = [
        layer for layer in state.grid_layers.values()
        if layer.available_shares > 0 and daily_high >= layer.sell_price
    ]
    return min(candidates, key=lambda layer: layer.sell_price, default=None)


def _disable_and_cancel_waiting(
    ledger: PortfolioLedger,
    state: ThermostatPositionState,
    symbol: str,
    mode: str,
    trade_date: date,
) -> None:
    for layer in state.grid_layers.values():
        if layer.held_shares or layer.status is GridLayerStatus.DISABLED:
            continue
        layer.status = GridLayerStatus.DISABLED
        ledger.orders.append(
            BacktestOrder(
                order_id=_next_order_id(ledger), trade_date=trade_date,
                symbol=symbol, mode=mode, family="grid", trigger_type="grid_buy",
                side="buy", status=OrderStatus.CANCELLED,
                trigger_price=layer.buy_price, intended_shares=layer.target_shares,
                grid_layer=layer.layer_id, failure_reason="grid_lower_broken",
                quality_warning="unfilled_grid_buy_cancelled",
            )
        )


def _risk_exit_layers(
    ledger: PortfolioLedger,
    settings: T1ExecutionSettings,
    bar: DailyBar,
    state: ThermostatPositionState,
    symbol: str,
    mode: str,
    trigger_price: float,
    trade_date: date,
    warnings: list[str],
    *,
    warning: str,
) -> None:
    _warn(warnings, warning)
    for layer in list(state.grid_layers.values()):
        available = layer.available_shares
        locked = layer.today_bought_shares
        if available:
            execute_sell(
                ledger, settings, bar, symbol=symbol, mode=mode, family="grid",
                trigger_type="risk_control_sell", trigger_price=trigger_price,
                intended_shares=available, trade_date=trade_date,
                grid_layer=layer.layer_id,
            )
        if locked:
            effective_pending = state.queue_pending(
                PendingSellLevel.PENDING_EXIT, layer.held_shares, "grid", trade_date,
                grid_layer_id=layer.layer_id,
            )
            pending_warning = "t1_locked_grid_exit_queued"
            if effective_pending.level is PendingSellLevel.PENDING_EMERGENCY_EXIT:
                pending_warning += ";pending_priority_upgraded_to_emergency_exit"
            ledger.orders.append(
                BacktestOrder(
                    order_id=_next_order_id(ledger), trade_date=trade_date,
                    symbol=symbol, mode=mode, family="grid",
                    trigger_type="risk_control_sell", side="sell",
                    status=OrderStatus.PENDING, trigger_price=trigger_price,
                    intended_shares=locked, position_before=state.total_shares,
                    position_after=state.total_shares,
                    pending_level=effective_pending.level,
                    grid_layer=layer.layer_id,
                    quality_warning=pending_warning,
                )
            )


def _audit_plan(
    ledger: PortfolioLedger, symbol: str, mode: str, trade_date: date,
) -> BacktestOrder:
    order = BacktestOrder(
            order_id=_next_order_id(ledger), trade_date=trade_date,
            symbol=symbol, mode=mode, family="grid", trigger_type="grid_plan",
            status=OrderStatus.PLAN_CREATED,
    )
    ledger.orders.append(order)
    return order


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
    ledger.orders.append(
        BacktestOrder(
            order_id=_next_order_id(ledger), trade_date=trade_date,
            symbol=symbol, mode=str(plan.get("stock_mode") or plan.get("mode") or ""),
            family="grid", trigger_type="plan_validation",
            status=OrderStatus.FAILED, failure_reason=reason,
            quality_warning=";".join(warnings),
        )
    )


def _result(
    ledger: PortfolioLedger,
    order_start: int,
    warnings: list[str],
    effective_count: int,
    selected_buy: str | None,
    selected_sell: str | None,
    blocked: bool,
) -> GridExecutionResult:
    orders = ledger.orders[order_start:]
    for order in orders:
        for warning in filter(None, order.quality_warning.split(";")):
            _warn(warnings, warning)
    return GridExecutionResult(
        orders=orders, data_quality_warnings=warnings,
        effective_layer_count=effective_count,
        selected_buy_layer=selected_buy,
        selected_sell_layer=selected_sell,
        buys_blocked_for_day=blocked,
    )


def _find_state(
    ledger: PortfolioLedger, symbol: str,
) -> ThermostatPositionState | None:
    return ledger.positions.get(symbol) or next(
        (state for key, state in ledger.positions.items() if normalize_symbol(key) == symbol),
        None,
    )


def _has_grid_fill_today(
    ledger: PortfolioLedger, symbol: str, trade_date: date,
) -> bool:
    return any(
        order.trade_date == trade_date
        and order.symbol == symbol
        and order.family == "grid"
        and order.status is OrderStatus.FILLED
        for order in ledger.fills
    )


def _marks(
    ledger: PortfolioLedger, symbol: str, bar: DailyBar,
) -> dict[str, float]:
    marks = {key: state.average_cost for key, state in ledger.positions.items()}
    state = _find_state(ledger, symbol)
    marks[symbol] = bar.close or (state.average_cost if state is not None else 0.0)
    return marks


def _strictly_descending(values: list[float]) -> bool:
    return all(left > right for left, right in zip(values, values[1:]))


def _strictly_ascending(values: list[float]) -> bool:
    return all(left < right for left, right in zip(values, values[1:]))


def _optional_finite(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _integer(value: object) -> int | None:
    parsed = _optional_finite(value)
    if parsed is None or not parsed.is_integer():
        return None
    return int(parsed)


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


def _next_order_id(ledger: PortfolioLedger) -> str:
    return f"order-{len(ledger.orders) + 1:08d}"


def _warn(warnings: list[str], warning: str) -> None:
    if warning and warning not in warnings:
        warnings.append(warning)
