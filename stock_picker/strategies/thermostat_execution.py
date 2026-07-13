from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from math import floor, isfinite
from typing import Any, Iterable, Mapping

from stock_picker.data.models import normalize_symbol

from .thermostat_state import PendingSellLevel, PendingSellState, ThermostatPositionState


@dataclass(frozen=True)
class T1ExecutionSettings:
    commission_rate: float = 0.0003
    minimum_commission: float = 5.0
    stamp_tax_rate: float = 0.001
    slippage_pct: float = 0.001
    buy_lot_size: int = 100
    trend_symbol_base_max: float = 0.20
    trend_total_base_max: float = 0.65
    grid_symbol_base_max: float = 0.15
    grid_total_hard_max: float = 0.40
    account_total_max: float = 0.95
    force_final_liquidation: bool = False

    def __post_init__(self) -> None:
        for name in (
            "commission_rate", "minimum_commission", "stamp_tax_rate", "slippage_pct",
            "trend_symbol_base_max", "trend_total_base_max", "grid_symbol_base_max",
            "grid_total_hard_max", "account_total_max",
        ):
            value = getattr(self, name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.buy_lot_size <= 0:
            raise ValueError("buy_lot_size must be positive")
        if self.force_final_liquidation:
            raise ValueError("force_final_liquidation is unsupported for T+1 v1")


@dataclass(frozen=True)
class DailyBar:
    date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float
    previous_close: float | None = None
    limit_up_price: float | None = None
    limit_down_price: float | None = None
    suspended: bool = False

    def __post_init__(self) -> None:
        for name in (
            "open", "high", "low", "close", "previous_close", "limit_up_price",
            "limit_down_price",
        ):
            value = getattr(self, name)
            if value is not None and (not isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be positive and finite when present")
        if not isfinite(self.volume) or self.volume < 0:
            raise ValueError("volume must be finite and non-negative")
        if self.high is not None and self.low is not None and self.high < self.low:
            raise ValueError("high cannot be below low")


class OrderStatus(StrEnum):
    PLAN_CREATED = "plan_created"
    ORDER_CREATED = "order_created"
    TRIGGERED = "triggered"
    FILLED = "filled"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PENDING = "pending"
    PENDING_RETRY = "pending_retry"
    EXPIRED = "expired"


class ExecutionPhase(StrEnum):
    RISK_CONTROL = "risk_control"
    TREND_EXIT = "trend_exit"
    TREND_REDUCE = "trend_reduce"
    GRID_SELL = "grid_sell"
    TREND_BUY = "trend_buy"
    GRID_BUY = "grid_buy"


_PHASE_ORDER = {phase: index for index, phase in enumerate(ExecutionPhase)}


@dataclass(frozen=True)
class ExecutionCandidate:
    candidate_id: str
    trade_date: date
    symbol: str
    mode: str
    family: str
    phase: ExecutionPhase
    trigger_type: str
    trigger_price: float
    side: str
    owner_id: str = ""
    grid_layer: str | None = None
    trend_batch: int | None = None
    risk_rank: int = 0
    plan_priority: int = 0
    plan_trace_id: str = ""
    order_trace_id: str = ""
    approximation_warnings: tuple[str, ...] = ()
    metadata: Mapping[str, Any] | tuple[tuple[Any, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        object.__setattr__(self, "phase", ExecutionPhase(self.phase))
        object.__setattr__(self, "approximation_warnings", tuple(self.approximation_warnings))
        frozen_metadata = _deep_freeze(self.metadata)
        object.__setattr__(self, "metadata", frozen_metadata)

    def sort_key(self) -> tuple[int, int, int, str, str]:
        return (
            _PHASE_ORDER[self.phase], -self.risk_rank, self.plan_priority,
            self.symbol, self.owner_id,
        )

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, ExecutionCandidate):
            return NotImplemented
        return self.sort_key() < other.sort_key()


def stable_sort_candidates(
    candidates: Iterable[ExecutionCandidate],
) -> list[ExecutionCandidate]:
    """Return candidates in reviewed global order, preserving equal-key order."""
    return sorted(candidates, key=ExecutionCandidate.sort_key)


@dataclass
class BacktestOrder:
    order_id: str
    trade_date: date
    symbol: str
    mode: str
    family: str
    trigger_type: str
    side: str = ""
    status: OrderStatus = OrderStatus.ORDER_CREATED
    trigger_price: float | None = None
    base_price: float | None = None
    execution_price: float | None = None
    intended_shares: int = 0
    actual_shares: int = 0
    gross_amount: float = 0.0
    commission: float = 0.0
    stamp_tax: float = 0.0
    slippage_cost: float = 0.0
    total_cost: float = 0.0
    cash_before: float = 0.0
    cash_after: float = 0.0
    position_before: int = 0
    position_after: int = 0
    pending_level: PendingSellLevel | None = None
    grid_layer: str | None = None
    trend_batch: int | None = None
    risk_rank: int = 0
    plan_priority: int = 0
    approximate_intraday_sequence: bool = True
    quality_warning: str = ""
    failure_reason: str = ""
    plan_trace_id: str = ""
    candidate_trace_id: str = ""
    origin_strategy_family: str = ""
    origin_owner: str = ""


@dataclass
class PortfolioLedger:
    cash: float
    initial_capital: float
    positions: dict[str, ThermostatPositionState] = field(default_factory=dict)
    fills: list[BacktestOrder] = field(default_factory=list)
    orders: list[BacktestOrder] = field(default_factory=list)
    _execution_contexts: dict[tuple[str, str, date], object] = field(
        default_factory=dict, repr=False,
    )

    def __post_init__(self) -> None:
        if not isfinite(self.cash) or self.cash < 0:
            raise ValueError("cash must be finite and non-negative")
        if not isfinite(self.initial_capital) or self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive and finite")

    def symbol_position_ratio(self, symbol: str, marks: Mapping[str, float]) -> float:
        state = self.positions.get(normalize_symbol(symbol))
        if state is None:
            state = next(
                (item for key, item in self.positions.items() if normalize_symbol(key) == normalize_symbol(symbol)),
                None,
            )
        return self._state_value(state, marks) / self.initial_capital

    def trend_position_ratio(self, marks: Mapping[str, float]) -> float:
        value = sum(self._mark_for(symbol, state, marks) * state.trend_shares for symbol, state in self.positions.items())
        return value / self.initial_capital

    def grid_position_ratio(self, marks: Mapping[str, float]) -> float:
        value = sum(
            self._mark_for(symbol, state, marks)
            * sum(layer.held_shares for layer in state.grid_layers.values())
            for symbol, state in self.positions.items()
        )
        return value / self.initial_capital

    def total_position_ratio(self, marks: Mapping[str, float]) -> float:
        value = sum(self._state_value(state, marks, symbol) for symbol, state in self.positions.items())
        return value / self.initial_capital

    @staticmethod
    def _mark_for(symbol: str, state: ThermostatPositionState, marks: Mapping[str, float]) -> float:
        normalized_marks = {normalize_symbol(key): value for key, value in marks.items()}
        value = normalized_marks.get(normalize_symbol(symbol), state.average_cost)
        if not isfinite(value) or value < 0:
            raise ValueError(f"invalid mark for {symbol}")
        return value

    def _state_value(
        self,
        state: ThermostatPositionState | None,
        marks: Mapping[str, float],
        symbol: str | None = None,
    ) -> float:
        if state is None:
            return 0.0
        symbol = symbol or state.symbol
        return self._mark_for(symbol, state, marks) * state.total_shares


def round_buy_shares(shares: int | float, lot_size: int = 100) -> int:
    if lot_size <= 0:
        raise ValueError("lot_size must be positive")
    if not isfinite(shares) or shares <= 0:
        return 0
    return int(floor(shares / lot_size) * lot_size)


def commission_fee(gross_amount: float, settings: T1ExecutionSettings) -> float:
    if gross_amount <= 0:
        return 0.0
    return max(gross_amount * settings.commission_rate, settings.minimum_commission)


def buy_fees(gross_amount: float, settings: T1ExecutionSettings) -> float:
    return commission_fee(gross_amount, settings)


def sell_fees(gross_amount: float, settings: T1ExecutionSettings) -> float:
    return commission_fee(gross_amount, settings) + gross_amount * settings.stamp_tax_rate


def conservative_execution_price(side: str, base_price: float, slippage_pct: float) -> float:
    if not isfinite(base_price) or base_price <= 0:
        raise ValueError("base_price must be positive and finite")
    if not isfinite(slippage_pct) or slippage_pct < 0:
        raise ValueError("slippage_pct must be finite and non-negative")
    if side == "buy":
        return base_price * (1 + slippage_pct)
    if side == "sell":
        return base_price * (1 - slippage_pct)
    raise ValueError("side must be buy or sell")


def conservative_base_price(trigger_type: str, trigger_price: float | None, bar: DailyBar) -> float:
    if trigger_type == "pending_sell":
        return _required_price(bar.open, "open")
    if trigger_type in {"trend_buy", "trend_add"}:
        trigger = _required_price(trigger_price, "trigger_price")
        return max(trigger, bar.close) if bar.close is not None else trigger
    if trigger_type in {"trend_reduce", "trend_exit"}:
        trigger = _required_price(trigger_price, "trigger_price")
        return min(trigger, bar.close) if bar.close is not None else trigger
    if trigger_type in {"grid_buy", "grid_sell"}:
        return _required_price(trigger_price, "layer_price")
    if trigger_type in {"risk_control_sell", "downtrend_risk_sell"}:
        return _required_price(bar.close, "close")
    raise ValueError(f"unsupported trigger_type: {trigger_type}")


_EVENT_PRIORITY = {
    "pending_sell": 0,
    "risk_control_sell": 1,
    "downtrend_risk_sell": 1,
    "trend_exit": 2,
    "trend_reduce": 3,
    "grid_sell": 4,
    "trend_buy": 5,
    "trend_add": 5,
    "grid_buy": 6,
}


def sort_execution_events(events: Iterable[BacktestOrder]) -> list[BacktestOrder]:
    indexed = list(enumerate(events))
    indexed.sort(
        key=lambda pair: (
            _EVENT_PRIORITY.get(pair[1].trigger_type, 99),
            -pair[1].risk_rank,
            pair[1].plan_priority,
            normalize_symbol(pair[1].symbol),
            _owner_sort_id(pair[1]),
            pair[0],
        )
    )
    return [event for _, event in indexed]


def is_one_word_limit(bar: DailyBar, direction: str) -> bool:
    limit = bar.limit_up_price if direction == "up" else bar.limit_down_price if direction == "down" else None
    if limit is None or bar.high is None or bar.low is None:
        return False
    return _price_equal(bar.high, limit) and _price_equal(bar.low, limit)


def is_pending_open_limit_down(bar: DailyBar) -> bool:
    return (
        bar.open is not None
        and bar.limit_down_price is not None
        and _price_equal(bar.open, bar.limit_down_price)
    )


def execute_buy(
    ledger: PortfolioLedger,
    settings: T1ExecutionSettings,
    bar: DailyBar,
    *,
    symbol: str,
    mode: str,
    family: str,
    trigger_type: str,
    trigger_price: float,
    intended_shares: int,
    trade_date: date,
    grid_layer: str | None = None,
    trend_batch: int | None = None,
    risk_rank: int = 0,
    plan_priority: int = 0,
    order_id: str | None = None,
    portfolio_marks: Mapping[str, float] | None = None,
) -> BacktestOrder:
    symbol = normalize_symbol(symbol)
    state = ledger.positions.get(symbol)
    new_state = state is None
    if state is None:
        state = ThermostatPositionState(symbol=symbol, current_mode=mode, blocked_new_buy=False)
    order = BacktestOrder(
        order_id or _next_order_id(ledger), trade_date, symbol, mode, family, trigger_type,
        side="buy", status=OrderStatus.TRIGGERED, trigger_price=trigger_price,
        intended_shares=intended_shares, cash_before=ledger.cash,
        cash_after=ledger.cash, position_before=state.total_shares,
        position_after=state.total_shares, grid_layer=grid_layer, trend_batch=trend_batch,
        risk_rank=risk_rank, plan_priority=plan_priority,
        origin_strategy_family=family,
        origin_owner=(grid_layer or (f"batch-{trend_batch}" if trend_batch is not None else "")),
    )
    if trade_date != bar.date:
        return _record_failure(ledger, order, "trade_date_bar_date_mismatch")
    if bar.suspended:
        return _record_failure(ledger, order, "suspended")
    if is_one_word_limit(bar, "up"):
        return _record_failure(ledger, order, "one_word_limit_up")
    if family not in {"trend", "grid"}:
        return _record_failure(ledger, order, "unsupported_family")
    if family == "grid" and grid_layer is None:
        return _record_failure(ledger, order, "missing_grid_layer")
    if (family == "trend" and mode != "trend") or (family == "grid" and mode != "range"):
        return _record_failure(ledger, order, "invalid_mode_for_owner")
    if state.current_mode != mode:
        return _record_failure(ledger, order, "position_mode_mismatch")
    if state.blocked_new_buy:
        return _record_failure(ledger, order, "new_buys_blocked")
    try:
        state.assert_invariants()
    except (AssertionError, ValueError):
        return _record_failure(ledger, order, "invalid_position_state")
    if bar.limit_up_price is not None and bar.high is not None and _price_equal(bar.high, bar.limit_up_price):
        order.quality_warning = "limit_up_intraday_sequence_ambiguous"

    order.base_price = conservative_base_price(trigger_type, trigger_price, bar)
    order.execution_price = _clamp_execution_price(
        "buy",
        conservative_execution_price("buy", order.base_price, settings.slippage_pct),
        bar,
    )
    requested = round_buy_shares(intended_shares, settings.buy_lot_size)
    affordable = _affordable_buy_shares(ledger.cash, order.execution_price, requested, settings)
    shares = min(requested, affordable)
    if shares < settings.buy_lot_size:
        return _record_failure(ledger, order, "below_one_lot")

    marks = {key: position.average_cost for key, position in ledger.positions.items()}
    if portfolio_marks:
        marks.update({normalize_symbol(key): value for key, value in portfolio_marks.items()})
    marks[symbol] = order.execution_price
    prospective_value = shares * order.execution_price
    symbol_ratio = ledger.symbol_position_ratio(symbol, marks) + prospective_value / ledger.initial_capital
    family_ratio = (
        ledger.trend_position_ratio(marks) if family == "trend" else ledger.grid_position_ratio(marks)
    ) + prospective_value / ledger.initial_capital
    total_ratio = ledger.total_position_ratio(marks) + prospective_value / ledger.initial_capital
    symbol_cap = settings.trend_symbol_base_max if family == "trend" else settings.grid_symbol_base_max
    family_cap = settings.trend_total_base_max if family == "trend" else settings.grid_total_hard_max
    if symbol_ratio > symbol_cap + 1e-12:
        return _record_failure(ledger, order, "symbol_cap_exceeded")
    if family_ratio > family_cap + 1e-12:
        reason = "trend_total_cap_exceeded" if family == "trend" else "grid_total_cap_exceeded"
        return _record_failure(ledger, order, reason)
    if total_ratio > settings.account_total_max + 1e-12:
        return _record_failure(ledger, order, "account_total_cap_exceeded")

    gross = shares * order.execution_price
    fee = buy_fees(gross, settings)
    snapshot = deepcopy(state.__dict__)
    try:
        if family == "trend":
            state.record_trend_buy(
                trend_batch or state.trend_batch_index + 1, shares, order.execution_price,
                trade_date, trigger_price=trigger_price, planned_shares=intended_shares,
            )
        elif family == "grid":
            assert grid_layer is not None
            state.record_grid_buy(
                grid_layer, shares, order.execution_price, trade_date,
                buy_price=trigger_price, sell_price=trigger_price,
                target_position_pct=prospective_value / ledger.initial_capital,
                target_shares=intended_shares,
            )
    except (AssertionError, KeyError, TypeError, ValueError):
        state.__dict__.clear()
        state.__dict__.update(snapshot)
        return _record_failure(ledger, order, "state_mutation_rejected")
    if new_state:
        ledger.positions[symbol] = state
    ledger.cash -= gross + fee
    order.actual_shares = shares
    order.gross_amount = gross
    order.commission = fee
    order.total_cost = gross + fee
    order.cash_after = ledger.cash
    order.position_after = state.total_shares
    order.status = OrderStatus.FILLED
    order.slippage_cost = (order.execution_price - order.base_price) * shares
    ledger.orders.append(order)
    ledger.fills.append(order)
    return order


_SELL_TRIGGER_LEVEL = {
    "risk_control_sell": PendingSellLevel.PENDING_EMERGENCY_EXIT,
    "downtrend_risk_sell": PendingSellLevel.PENDING_EMERGENCY_EXIT,
    "trend_exit": PendingSellLevel.PENDING_EXIT,
    "trend_reduce": PendingSellLevel.PENDING_REDUCE,
    "grid_sell": PendingSellLevel.PENDING_REDUCE,
}


def execute_sell(
    ledger: PortfolioLedger,
    settings: T1ExecutionSettings,
    bar: DailyBar,
    *,
    symbol: str,
    mode: str,
    family: str,
    trigger_type: str,
    trigger_price: float,
    intended_shares: int,
    trade_date: date,
    grid_layer: str | None = None,
    trend_batch: int | None = None,
    risk_rank: int = 0,
    plan_priority: int = 0,
    order_id: str | None = None,
) -> BacktestOrder:
    symbol = normalize_symbol(symbol)
    state = ledger.positions.get(symbol)
    position_before = state.total_shares if state is not None else 0
    order = BacktestOrder(
        order_id or _next_order_id(ledger), trade_date, symbol, mode, family, trigger_type,
        side="sell", status=OrderStatus.TRIGGERED, trigger_price=trigger_price,
        intended_shares=intended_shares, cash_before=ledger.cash, cash_after=ledger.cash,
        position_before=position_before, position_after=position_before,
        grid_layer=grid_layer, trend_batch=trend_batch, risk_rank=risk_rank,
        plan_priority=plan_priority,
    )
    if trade_date != bar.date:
        return _record_failure(ledger, order, "trade_date_bar_date_mismatch")
    if state is None:
        return _record_failure(ledger, order, "missing_position")
    if trigger_type not in _SELL_TRIGGER_LEVEL:
        return _record_failure(ledger, order, "unsupported_sell_trigger")
    if family not in {"trend", "grid"}:
        return _record_failure(ledger, order, "invalid_origin_family")
    if trigger_type.startswith("trend_") and family != "trend":
        return _record_failure(ledger, order, "invalid_owner_for_trigger")
    if trigger_type == "grid_sell" and family != "grid":
        return _record_failure(ledger, order, "invalid_owner_for_trigger")
    if state.current_mode != mode:
        return _record_failure(ledger, order, "position_mode_mismatch")
    if intended_shares <= 0:
        return _record_failure(ledger, order, "invalid_share_quantity")
    try:
        state.assert_invariants()
    except (AssertionError, ValueError):
        return _record_failure(ledger, order, "invalid_position_state")
    if family == "grid" and (grid_layer is None or grid_layer not in state.grid_layers):
        return _record_failure(ledger, order, "missing_grid_layer")
    if family == "trend" and trend_batch is not None and not any(
        batch.batch_index == trend_batch for batch in state.trend_batches
    ):
        return _record_failure(ledger, order, "missing_trend_batch")

    order.origin_strategy_family, order.origin_owner = _economic_owner(
        state, family, grid_layer, trend_batch,
    )

    total_owned, available = _sell_owner_shares(state, family, grid_layer, trend_batch)
    requested = min(intended_shares, total_owned)
    if intended_shares > total_owned:
        order.quality_warning = "intended_shares_exceed_owned_shares"
    if requested <= 0:
        return _record_failure(ledger, order, "no_owned_shares")
    pending_level = _SELL_TRIGGER_LEVEL[trigger_type]

    if bar.suspended or is_one_word_limit(bar, "down"):
        reason = "suspended" if bar.suspended else "one_word_limit_down"
        _queue_sell_pending(state, pending_level, requested, family, trade_date, grid_layer, trend_batch)
        order.status = OrderStatus.FAILED
        order.failure_reason = reason
        order.pending_level = pending_level
        ledger.orders.append(order)
        return order
    if bar.limit_down_price is not None and bar.low is not None and _price_equal(bar.low, bar.limit_down_price):
        order.quality_warning = _append_warning(
            order.quality_warning, "limit_down_intraday_sequence_ambiguous",
        )

    try:
        order.base_price = conservative_base_price(trigger_type, trigger_price, bar)
    except ValueError:
        return _record_failure(ledger, order, "missing_execution_price")
    order.execution_price = _clamp_execution_price(
        "sell",
        conservative_execution_price("sell", order.base_price, settings.slippage_pct),
        bar,
    )
    shares = min(requested, available)
    locked_remainder = min(requested - shares, total_owned - available)
    snapshot = deepcopy(state.__dict__)
    try:
        if shares:
            if family == "grid":
                assert grid_layer is not None
                state.record_grid_sell(grid_layer, shares, order.execution_price, trade_date)
            else:
                state.record_trend_sell(
                    shares, order.execution_price, trade_date, batch_index=trend_batch,
                )
        if locked_remainder:
            _queue_sell_pending(
                state, pending_level, locked_remainder, family, trade_date,
                grid_layer, trend_batch,
            )
    except (AssertionError, KeyError, TypeError, ValueError):
        state.__dict__.clear()
        state.__dict__.update(snapshot)
        return _record_failure(ledger, order, "state_mutation_rejected")

    if shares:
        gross = shares * order.execution_price
        commission = commission_fee(gross, settings)
        tax = gross * settings.stamp_tax_rate
        ledger.cash += gross - commission - tax
        order.actual_shares = shares
        order.gross_amount = gross
        order.commission = commission
        order.stamp_tax = tax
        order.slippage_cost = (order.base_price - order.execution_price) * shares
        order.total_cost = commission + tax
        ledger.fills.append(order)
    order.cash_after = ledger.cash
    order.position_after = state.total_shares
    if locked_remainder:
        order.status = OrderStatus.PENDING
        order.pending_level = pending_level
    elif shares:
        order.status = OrderStatus.FILLED
    else:
        order.status = OrderStatus.FAILED
        order.failure_reason = "no_available_shares"
    ledger.orders.append(order)
    return order


def process_pending_sells(
    ledger: PortfolioLedger,
    settings: T1ExecutionSettings,
    bar: DailyBar,
    state: ThermostatPositionState,
    trade_date: date,
) -> list[BacktestOrder]:
    if trade_date != bar.date:
        raise ValueError("trade_date must equal bar.date")
    results: list[BacktestOrder] = []
    for pending in list(state.pending_sells):
        if pending.last_attempt_date == trade_date:
            continue
        order = _pending_order(ledger, state, pending, trade_date)
        if pending.origin_family not in {"trend", "grid"}:
            state.attempt_pending(
                trade_date, False, failure_reason="invalid_origin_family",
                origin_family=pending.origin_family,
                grid_layer_id=pending.grid_layer_id,
                batch_index=pending.batch_index,
            )
            order.status = OrderStatus.PENDING_RETRY
            order.failure_reason = "invalid_origin_family"
            ledger.orders.append(order)
            results.append(order)
            continue
        failure = (
            "suspended" if bar.suspended else
            "no_valid_price" if all(
                value is None for value in (bar.open, bar.high, bar.low, bar.close)
            ) else
            "missing_open" if bar.open is None else
            "open_at_limit_down" if is_pending_open_limit_down(bar) else ""
        )
        if failure:
            state.attempt_pending(
                trade_date, False, failure_reason=failure,
                origin_family=pending.origin_family,
                grid_layer_id=pending.grid_layer_id,
                batch_index=pending.batch_index,
            )
            order.status = OrderStatus.PENDING_RETRY
            order.failure_reason = failure
            ledger.orders.append(order)
            results.append(order)
            continue

        available = _pending_available_shares(state, pending)
        shares = min(pending.remaining_shares, available)
        if shares <= 0:
            state.attempt_pending(
                trade_date, False, failure_reason="no_available_shares",
                origin_family=pending.origin_family,
                grid_layer_id=pending.grid_layer_id,
                batch_index=pending.batch_index,
            )
            order.status = OrderStatus.PENDING_RETRY
            order.failure_reason = "no_available_shares"
            ledger.orders.append(order)
            results.append(order)
            continue

        order.base_price = conservative_base_price("pending_sell", None, bar)
        order.execution_price = _clamp_execution_price(
            "sell",
            conservative_execution_price("sell", order.base_price, settings.slippage_pct),
            bar,
        )
        gross = shares * order.execution_price
        commission = commission_fee(gross, settings)
        tax = gross * settings.stamp_tax_rate
        if pending.origin_family == "grid":
            if pending.grid_layer_id is None:
                raise ValueError("grid pending sell requires grid_layer_id")
            state.record_grid_sell(pending.grid_layer_id, shares, order.execution_price, trade_date)
        else:
            state.record_trend_sell(
                shares, order.execution_price, trade_date,
                batch_index=pending.batch_index,
            )
        state.attempt_pending(
            trade_date, True, sold_shares=shares,
            origin_family=pending.origin_family,
            grid_layer_id=pending.grid_layer_id,
            batch_index=pending.batch_index,
        )
        ledger.cash += gross - commission - tax
        order.actual_shares = shares
        order.gross_amount = gross
        order.commission = commission
        order.stamp_tax = tax
        order.slippage_cost = (order.base_price - order.execution_price) * shares
        order.total_cost = commission + tax
        order.cash_after = ledger.cash
        order.position_after = state.total_shares
        order.status = OrderStatus.FILLED if pending.remaining_shares == 0 else OrderStatus.PENDING_RETRY
        order.failure_reason = "partial_available_shares" if order.status is OrderStatus.PENDING_RETRY else ""
        ledger.orders.append(order)
        ledger.fills.append(order)
        results.append(order)
    return results


def _pending_order(
    ledger: PortfolioLedger,
    state: ThermostatPositionState,
    pending: PendingSellState,
    trade_date: date,
) -> BacktestOrder:
    origin_family, origin_owner = _economic_owner(
        state, pending.origin_family, pending.grid_layer_id, pending.batch_index,
    )
    return BacktestOrder(
        _next_order_id(ledger), trade_date, normalize_symbol(state.symbol), state.current_mode,
        pending.origin_family, "pending_sell", side="sell", status=OrderStatus.TRIGGERED,
        intended_shares=pending.remaining_shares, cash_before=ledger.cash,
        cash_after=ledger.cash, position_before=state.total_shares,
        position_after=state.total_shares, pending_level=pending.level,
        grid_layer=pending.grid_layer_id, trend_batch=pending.batch_index,
        origin_strategy_family=origin_family, origin_owner=origin_owner,
    )


def _economic_owner(
    state: ThermostatPositionState,
    family: str,
    grid_layer: str | None,
    trend_batch: int | None,
) -> tuple[str, str]:
    """Return the original economic lot owner after an explicit mode migration."""
    if family == "grid" and grid_layer is not None:
        layer = state.grid_layers.get(grid_layer)
        if layer is not None:
            return (
                layer.origin_strategy_family or "grid",
                layer.origin_owner,
            )
        return "grid", grid_layer
    return "trend", f"batch-{trend_batch}" if trend_batch is not None else ""


def _pending_available_shares(state: ThermostatPositionState, pending: PendingSellState) -> int:
    if pending.origin_family == "grid":
        layer = state.grid_layers.get(pending.grid_layer_id or "")
        return layer.available_shares if layer is not None else 0
    if pending.batch_index is not None:
        batch = next((item for item in state.trend_batches if item.batch_index == pending.batch_index), None)
        return batch.available_shares if batch is not None else 0
    return state.trend_available_shares


def _sell_owner_shares(
    state: ThermostatPositionState,
    family: str,
    grid_layer: str | None,
    trend_batch: int | None,
) -> tuple[int, int]:
    if family == "grid":
        layer = state.grid_layers[grid_layer or ""]
        return layer.held_shares, layer.available_shares
    if trend_batch is not None:
        batch = next(item for item in state.trend_batches if item.batch_index == trend_batch)
        return batch.actual_shares, batch.available_shares
    return state.trend_shares, state.trend_available_shares


def _queue_sell_pending(
    state: ThermostatPositionState,
    level: PendingSellLevel,
    shares: int,
    family: str,
    pending_since: date,
    grid_layer: str | None,
    trend_batch: int | None,
) -> None:
    state.queue_pending(
        level, shares, family, pending_since,
        grid_layer_id=grid_layer, batch_index=trend_batch,
    )


def _clamp_execution_price(side: str, price: float, bar: DailyBar) -> float:
    if side == "buy" and bar.limit_up_price is not None:
        return min(price, bar.limit_up_price)
    if side == "sell" and bar.limit_down_price is not None:
        return max(price, bar.limit_down_price)
    return price


def _append_warning(existing: str, warning: str) -> str:
    return ";".join(item for item in (existing, warning) if item)


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple((_deep_freeze(key), _deep_freeze(item)) for key, item in value.items())
    if isinstance(value, tuple):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_deep_freeze(item) for item in value)
    return value


def _affordable_buy_shares(
    cash: float,
    price: float,
    requested_shares: int,
    settings: T1ExecutionSettings,
) -> int:
    shares = requested_shares
    while shares >= settings.buy_lot_size:
        gross = shares * price
        if gross + buy_fees(gross, settings) <= cash + 1e-12:
            return shares
        shares -= settings.buy_lot_size
    return 0


def _record_failure(ledger: PortfolioLedger, order: BacktestOrder, reason: str) -> BacktestOrder:
    order.status = OrderStatus.FAILED
    order.failure_reason = reason
    ledger.orders.append(order)
    return order


def _required_price(value: float | None, name: str) -> float:
    if value is None or not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return value


def _price_equal(left: float, right: float) -> bool:
    return abs(left - right) <= max(1e-8, abs(right) * 1e-8)


def _owner_sort_id(order: BacktestOrder) -> str:
    if order.grid_layer is not None:
        return str(order.grid_layer)
    if order.trend_batch is not None:
        return f"{order.trend_batch:010d}"
    return ""


def _next_order_id(ledger: PortfolioLedger) -> str:
    return f"order-{len(ledger.orders) + 1:08d}"
