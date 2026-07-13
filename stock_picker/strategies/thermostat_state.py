from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from math import isfinite


class PendingSellLevel(StrEnum):
    PENDING_REDUCE = "pending_reduce"
    PENDING_EXIT = "pending_exit"
    PENDING_EMERGENCY_EXIT = "pending_emergency_exit"


class GridLayerStatus(StrEnum):
    WAITING_BUY = "waiting_buy"
    BOUGHT_TODAY = "bought_today"
    HOLDING_AVAILABLE = "holding_available"
    WAITING_SELL = "waiting_sell"
    COMPLETED = "completed"
    DISABLED = "disabled"


@dataclass
class TrendBatchRecord:
    batch_index: int
    target_ratio: float = 0.0
    trigger_price: float | None = None
    planned_shares: int = 0
    filled_shares: int = 0
    actual_shares: int = 0
    fill_price: float | None = None
    fill_date: date | None = None
    first_fill_date: date | None = None
    last_fill_date: date | None = None
    available_shares: int = 0
    today_bought_shares: int = 0
    status: str = "waiting_buy"


@dataclass
class GridLayerPosition:
    layer_id: str
    buy_price: float
    sell_price: float
    target_position_pct: float
    target_shares: int
    held_shares: int = 0
    available_shares: int = 0
    today_bought_shares: int = 0
    buy_date: date | None = None
    buy_cost: float | None = None
    status: GridLayerStatus = GridLayerStatus.WAITING_BUY
    origin_strategy_family: str = "grid"
    origin_owner: str = ""


@dataclass
class PendingSellState:
    level: PendingSellLevel
    requested_shares: int
    remaining_shares: int
    origin_family: str
    grid_layer_id: str | None = None
    batch_index: int | None = None
    pending_since: date | None = None
    attempt_count: int = 0
    last_attempt_date: date | None = None
    last_failure: str | None = None

    def attempt(
        self,
        attempt_date: date,
        success: bool,
        sold_shares: int = 0,
        failure_reason: str | None = None,
    ) -> bool:
        """Record at most one attempt per date and return whether all shares sold."""
        if self.last_attempt_date == attempt_date:
            return self.remaining_shares == 0
        if sold_shares < 0:
            raise ValueError("sold_shares must be non-negative")
        if sold_shares > self.remaining_shares:
            raise ValueError("sold_shares exceeds remaining shares")
        if not success and sold_shares:
            raise ValueError("a failed attempt cannot sell shares")

        self.attempt_count += 1
        self.last_attempt_date = attempt_date
        if success:
            self.remaining_shares -= sold_shares
            self.last_failure = None
        else:
            self.last_failure = failure_reason
        return success and self.remaining_shares == 0


_PENDING_PRIORITY = {
    PendingSellLevel.PENDING_REDUCE: 1,
    PendingSellLevel.PENDING_EXIT: 2,
    PendingSellLevel.PENDING_EMERGENCY_EXIT: 3,
}
_VALID_MODES = {"trend", "range", "downtrend", "chaotic", "insufficient_data"}


@dataclass
class ThermostatPositionState:
    symbol: str
    total_shares: int = 0
    available_shares: int = 0
    today_bought_shares: int = 0
    average_cost: float = 0.0
    trend_shares: int = 0
    trend_available_shares: int = 0
    trend_today_bought_shares: int = 0
    trend_average_cost: float = 0.0
    trend_batch_index: int = 0
    trend_batches: list[TrendBatchRecord] = field(default_factory=list)
    grid_layers: dict[str, GridLayerPosition] = field(default_factory=dict)
    pending_sell: PendingSellState | None = None
    pending_sells: list[PendingSellState] = field(default_factory=list)
    last_effective_exit_trigger: float | None = None
    mid_band_state: str = "unknown"
    current_mode: str = "insufficient_data"
    blocked_new_buy: bool = True
    realized_pnl: float = 0.0
    risk_exit_required: bool = False
    trend_additions_stopped: bool = False
    last_trading_date: date | None = None

    def __post_init__(self) -> None:
        if self.current_mode not in _VALID_MODES:
            raise ValueError(f"unsupported thermostat mode: {self.current_mode}")
        self._sync_pending_view()
        self.assert_invariants()

    def start_trading_day(self, trading_date: date) -> None:
        if self.last_trading_date is not None and trading_date < self.last_trading_date:
            raise ValueError("trading_date cannot move backwards")

        for batch in self.trend_batches:
            if batch.fill_date is not None and batch.fill_date < trading_date:
                batch.available_shares += batch.today_bought_shares
                batch.today_bought_shares = 0
                if batch.actual_shares:
                    batch.status = "holding_available"

        for layer in self.grid_layers.values():
            if layer.buy_date is not None and layer.buy_date < trading_date:
                layer.available_shares += layer.today_bought_shares
                layer.today_bought_shares = 0
                if layer.held_shares and layer.status is GridLayerStatus.BOUGHT_TODAY:
                    layer.status = GridLayerStatus.HOLDING_AVAILABLE

        self._sync_owner_splits()
        self.last_trading_date = trading_date
        self.assert_invariants()

    def record_trend_buy(
        self,
        batch_index: int,
        shares: int,
        price: float,
        trade_date: date,
        target_ratio: float = 0.0,
        trigger_price: float | None = None,
        planned_shares: int | None = None,
    ) -> None:
        self._validate_trade(shares, price)
        if self.trend_additions_stopped:
            raise ValueError("trend additions are stopped in the current mode")

        aggregate_before = self.total_shares
        trend_before = self.trend_shares
        self.average_cost = _weighted_cost(self.average_cost, aggregate_before, price, shares)
        self.trend_average_cost = _weighted_cost(self.trend_average_cost, trend_before, price, shares)
        self.total_shares += shares
        self.today_bought_shares += shares
        self.trend_shares += shares
        self.trend_today_bought_shares += shares
        self.trend_batch_index = max(self.trend_batch_index, batch_index)

        batch = next((item for item in self.trend_batches if item.batch_index == batch_index), None)
        if batch is None:
            batch = TrendBatchRecord(batch_index=batch_index)
            self.trend_batches.append(batch)
            self.trend_batches.sort(key=lambda item: item.batch_index)
        batch.fill_price = _weighted_cost(batch.fill_price or 0.0, batch.actual_shares, price, shares)
        batch.target_ratio = target_ratio or batch.target_ratio
        batch.trigger_price = trigger_price if trigger_price is not None else batch.trigger_price
        batch.planned_shares = max(batch.planned_shares, planned_shares if planned_shares is not None else shares)
        batch.filled_shares += shares
        batch.actual_shares += shares
        batch.today_bought_shares += shares
        if batch.first_fill_date is None or trade_date < batch.first_fill_date:
            batch.first_fill_date = trade_date
        if batch.last_fill_date is None or trade_date > batch.last_fill_date:
            batch.last_fill_date = trade_date
        batch.fill_date = batch.last_fill_date
        batch.status = "bought_today"
        self.assert_invariants()

    def record_grid_buy(
        self,
        layer_id: str,
        shares: int,
        price: float,
        trade_date: date,
        buy_price: float | None = None,
        sell_price: float | None = None,
        target_position_pct: float = 0.0,
        target_shares: int | None = None,
    ) -> None:
        self._validate_trade(shares, price)
        aggregate_before = self.total_shares
        self.average_cost = _weighted_cost(self.average_cost, aggregate_before, price, shares)
        self.total_shares += shares
        self.today_bought_shares += shares

        layer = self.grid_layers.get(layer_id)
        if layer is None:
            layer = GridLayerPosition(
                layer_id=layer_id,
                buy_price=price if buy_price is None else buy_price,
                sell_price=price if sell_price is None else sell_price,
                target_position_pct=target_position_pct,
                target_shares=shares if target_shares is None else target_shares,
                origin_owner=layer_id,
            )
            self.grid_layers[layer_id] = layer
        layer.buy_cost = _weighted_cost(layer.buy_cost or 0.0, layer.held_shares, price, shares)
        layer.held_shares += shares
        layer.today_bought_shares += shares
        layer.buy_date = trade_date
        layer.status = GridLayerStatus.BOUGHT_TODAY
        self.assert_invariants()

    def record_trend_sell(
        self,
        shares: int,
        price: float,
        trade_date: date,
        batch_index: int | None = None,
    ) -> None:
        del trade_date
        self._validate_trade(shares, price)
        batches = self.trend_batches
        if batch_index is not None:
            batches = [batch for batch in self.trend_batches if batch.batch_index == batch_index]
            if not batches:
                raise KeyError(batch_index)
            if shares > batches[0].available_shares:
                raise ValueError("shares exceed available trend-batch shares")
        elif shares > self.trend_available_shares:
            raise ValueError("shares exceed available trend shares")

        remaining = shares
        consumed_cost = 0.0
        for batch in batches:
            consumed = min(remaining, batch.available_shares)
            if not consumed:
                continue
            consumed_cost += consumed * (
                batch.fill_price if batch.fill_price is not None else self.trend_average_cost
            )
            batch.available_shares -= consumed
            batch.actual_shares -= consumed
            remaining -= consumed
            batch.status = "completed" if batch.actual_shares == 0 else "holding_available"
            if remaining == 0:
                break

        if remaining:
            consumed_cost += remaining * self.trend_average_cost
        self.realized_pnl += price * shares - consumed_cost
        self.trend_shares -= shares
        self.trend_available_shares -= shares
        self.total_shares -= shares
        self.available_shares -= shares
        if self.trend_shares == 0:
            self.trend_average_cost = 0.0
        else:
            self._recompute_trend_average_cost()
        self._recompute_average_cost()
        self.assert_invariants()

    def record_grid_sell(self, layer_id: str, shares: int, price: float, trade_date: date) -> None:
        del trade_date
        self._validate_trade(shares, price)
        if layer_id not in self.grid_layers:
            raise KeyError(layer_id)
        layer = self.grid_layers[layer_id]
        if shares > layer.available_shares:
            raise ValueError("shares exceed available grid-layer shares")

        self.realized_pnl += (price - (layer.buy_cost or 0.0)) * shares
        layer.held_shares -= shares
        layer.available_shares -= shares
        layer.status = GridLayerStatus.COMPLETED if layer.held_shares == 0 else GridLayerStatus.HOLDING_AVAILABLE
        self.total_shares -= shares
        self.available_shares -= shares
        self._recompute_average_cost()
        self.assert_invariants()

    def queue_pending(
        self,
        level: PendingSellLevel | str,
        requested_shares: int,
        origin_family: str,
        pending_since: date,
        grid_layer_id: str | None = None,
        batch_index: int | None = None,
    ) -> PendingSellState:
        level = PendingSellLevel(level)
        if requested_shares <= 0:
            raise ValueError("requested_shares must be positive")
        self._sync_pending_view()
        pending = next(
            (
                item
                for item in self.pending_sells
                if self._pending_owner_key(item)
                == (origin_family, grid_layer_id, batch_index)
            ),
            None,
        )
        if pending is None:
            pending = PendingSellState(
                level=level,
                requested_shares=requested_shares,
                remaining_shares=requested_shares,
                origin_family=origin_family,
                grid_layer_id=grid_layer_id,
                batch_index=batch_index,
                pending_since=pending_since,
            )
            self.pending_sells.append(pending)
        else:
            if requested_shares > pending.requested_shares:
                pending.remaining_shares += requested_shares - pending.requested_shares
                pending.requested_shares = requested_shares
            if _PENDING_PRIORITY[level] > _PENDING_PRIORITY[pending.level]:
                pending.level = level
            if pending.pending_since is None or pending_since < pending.pending_since:
                pending.pending_since = pending_since
        self._sync_pending_view()
        self.assert_invariants()
        return pending

    def attempt_pending(
        self,
        attempt_date: date,
        success: bool,
        sold_shares: int = 0,
        failure_reason: str | None = None,
        origin_family: str | None = None,
        grid_layer_id: str | None = None,
        batch_index: int | None = None,
    ) -> bool:
        self._sync_pending_view()
        if origin_family is None:
            pending = self.pending_sell
        else:
            owner_key = (origin_family, grid_layer_id, batch_index)
            pending = next(
                (item for item in self.pending_sells if self._pending_owner_key(item) == owner_key),
                None,
            )
        if pending is None:
            return False
        cleared = pending.attempt(attempt_date, success, sold_shares, failure_reason)
        if cleared:
            self.pending_sells = [item for item in self.pending_sells if item is not pending]
            if self.pending_sell is pending:
                self.pending_sell = None
        self._sync_pending_view()
        self.assert_invariants()
        return cleared

    def update_effective_exit_trigger(self, new_value: float | None) -> float | None:
        if new_value is not None and isfinite(new_value) and new_value > 0:
            if self.last_effective_exit_trigger is None:
                self.last_effective_exit_trigger = float(new_value)
            else:
                self.last_effective_exit_trigger = max(self.last_effective_exit_trigger, float(new_value))
        self.assert_invariants()
        return self.last_effective_exit_trigger

    def observe_boll_mid(self, close: float, boll_mid: float, observation_date: date) -> bool:
        del observation_date
        crossed = False
        if close > boll_mid:
            self.mid_band_state = "above"
        elif close < boll_mid:
            crossed = self.mid_band_state == "above"
            self.mid_band_state = "below"
        self.assert_invariants()
        return crossed

    def transition_mode(
        self,
        new_mode: str,
        current_position_ratio: float = 0.0,
        range_cap_ratio: float | None = None,
        trend_batch_targets: tuple[float, ...] = (0.30, 0.60, 1.00),
        base_layer_id: str = "trend_base",
    ) -> None:
        if new_mode not in _VALID_MODES:
            raise ValueError(f"unsupported thermostat mode: {new_mode}")
        old_mode = self.current_mode

        if old_mode == "range" and new_mode == "trend":
            for layer in self.grid_layers.values():
                if layer.status is GridLayerStatus.WAITING_BUY:
                    layer.status = GridLayerStatus.DISABLED
            derived_batch = next(
                (index for index, target in enumerate(trend_batch_targets, start=1) if current_position_ratio < target),
                len(trend_batch_targets) + 1,
            )
            if current_position_ratio > 0:
                derived_batch = max(2, derived_batch)
            self.trend_batch_index = max(self.trend_batch_index, derived_batch)
            self.trend_additions_stopped = False

        if old_mode == "trend" and new_mode == "range":
            self._convert_trend_to_grid_base(base_layer_id, current_position_ratio)
            self.trend_additions_stopped = True

        self.current_mode = new_mode
        if new_mode in {"trend", "range"}:
            self.risk_exit_required = False
            self.blocked_new_buy = bool(
                new_mode == "range"
                and range_cap_ratio is not None
                and current_position_ratio > range_cap_ratio
            )
        elif new_mode == "downtrend":
            self.blocked_new_buy = True
            self.risk_exit_required = True
        else:
            self.blocked_new_buy = True
            self.risk_exit_required = False
        self.assert_invariants()

    def assert_invariants(self) -> None:
        for layer in self.grid_layers.values():
            for value in (
                layer.target_shares,
                layer.held_shares,
                layer.available_shares,
                layer.today_bought_shares,
            ):
                assert value >= 0, f"grid layer {layer.layer_id} counters cannot be negative"
        for batch in self.trend_batches:
            for value in (
                batch.planned_shares,
                batch.filled_shares,
                batch.actual_shares,
                batch.available_shares,
                batch.today_bought_shares,
            ):
                assert value >= 0, f"trend batch {batch.batch_index} counters cannot be negative"
        grid_total = sum(layer.held_shares for layer in self.grid_layers.values())
        grid_available = sum(layer.available_shares for layer in self.grid_layers.values())
        grid_today = sum(layer.today_bought_shares for layer in self.grid_layers.values())
        assert self.total_shares == self.trend_shares + grid_total, "aggregate total does not equal owned shares"
        assert self.available_shares == self.trend_available_shares + grid_available, "aggregate available does not equal owned shares"
        assert self.today_bought_shares == self.trend_today_bought_shares + grid_today, "aggregate today-bought does not equal owned shares"
        assert self.available_shares + self.today_bought_shares == self.total_shares, "aggregate share split is invalid"
        assert self.trend_available_shares + self.trend_today_bought_shares == self.trend_shares, "trend share split is invalid"
        for layer in self.grid_layers.values():
            assert layer.available_shares + layer.today_bought_shares == layer.held_shares, f"grid layer {layer.layer_id} share split is invalid"
        if self.trend_batches:
            assert sum(batch.actual_shares for batch in self.trend_batches) == self.trend_shares, "trend batches do not equal trend shares"
            assert sum(batch.available_shares for batch in self.trend_batches) == self.trend_available_shares, "trend batch available shares drifted"
            assert sum(batch.today_bought_shares for batch in self.trend_batches) == self.trend_today_bought_shares, "trend batch today-bought shares drifted"
        for value in (
            self.total_shares,
            self.available_shares,
            self.today_bought_shares,
            self.trend_shares,
            self.trend_available_shares,
            self.trend_today_bought_shares,
        ):
            assert value >= 0, "share counts cannot be negative"
        for pending in self.pending_sells:
            assert pending.requested_shares > 0, "pending requested shares must be positive"
            assert pending.remaining_shares >= 0, "pending remaining shares cannot be negative"

    def _sync_pending_view(self) -> None:
        if self.pending_sell is not None and all(
            item is not self.pending_sell for item in self.pending_sells
        ):
            self.pending_sells.append(self.pending_sell)
        self.pending_sells.sort(
            key=lambda item: (
                -_PENDING_PRIORITY[item.level],
                item.pending_since or date.max,
                item.origin_family,
                item.grid_layer_id or "",
                item.batch_index if item.batch_index is not None else -1,
            )
        )
        self.pending_sell = self.pending_sells[0] if self.pending_sells else None

    @staticmethod
    def _pending_owner_key(pending: PendingSellState) -> tuple[str, str | None, int | None]:
        return pending.origin_family, pending.grid_layer_id, pending.batch_index

    def _recompute_average_cost(self) -> None:
        if self.total_shares == 0:
            self.average_cost = 0.0
            return
        owned_cost = self.trend_average_cost * self.trend_shares
        owned_cost += sum((layer.buy_cost or 0.0) * layer.held_shares for layer in self.grid_layers.values())
        self.average_cost = owned_cost / self.total_shares

    def _sync_owner_splits(self) -> None:
        self.trend_available_shares = sum(batch.available_shares for batch in self.trend_batches)
        self.trend_today_bought_shares = sum(batch.today_bought_shares for batch in self.trend_batches)
        self.available_shares = self.trend_available_shares + sum(
            layer.available_shares for layer in self.grid_layers.values()
        )
        self.today_bought_shares = self.trend_today_bought_shares + sum(
            layer.today_bought_shares for layer in self.grid_layers.values()
        )

    def _convert_trend_to_grid_base(self, layer_id: str, target_position_pct: float) -> None:
        if not self.trend_shares:
            return
        trend_today_dates = [
            batch.fill_date
            for batch in self.trend_batches
            if batch.today_bought_shares and batch.fill_date is not None
        ]
        layer = self.grid_layers.get(layer_id)
        if layer is None:
            layer = GridLayerPosition(
                layer_id=layer_id,
                buy_price=self.trend_average_cost,
                sell_price=self.trend_average_cost,
                target_position_pct=target_position_pct,
                target_shares=self.trend_shares,
                origin_strategy_family="trend",
            )
            self.grid_layers[layer_id] = layer
        elif layer.held_shares and layer.origin_strategy_family != "trend":
            raise ValueError("trend base layer cannot mix economic ownership")
        else:
            layer.origin_strategy_family = "trend"
            layer.origin_owner = ""
        combined = layer.held_shares + self.trend_shares
        layer.buy_cost = _weighted_cost(
            layer.buy_cost or 0.0,
            layer.held_shares,
            self.trend_average_cost,
            self.trend_shares,
        )
        layer.held_shares = combined
        layer.available_shares += self.trend_available_shares
        layer.today_bought_shares += self.trend_today_bought_shares
        if trend_today_dates:
            latest_trend_buy = max(trend_today_dates)
            layer.buy_date = max(layer.buy_date, latest_trend_buy) if layer.buy_date else latest_trend_buy
        layer.target_shares = max(layer.target_shares, combined)
        layer.status = (
            GridLayerStatus.BOUGHT_TODAY if layer.today_bought_shares else GridLayerStatus.HOLDING_AVAILABLE
        )
        self._migrate_trend_pending_to_grid_base(layer_id)
        for batch in self.trend_batches:
            batch.actual_shares = 0
            batch.available_shares = 0
            batch.today_bought_shares = 0
            batch.status = "converted_to_grid"
        self.trend_shares = 0
        self.trend_available_shares = 0
        self.trend_today_bought_shares = 0
        self.trend_average_cost = 0.0

    def _recompute_trend_average_cost(self) -> None:
        if self.trend_shares == 0:
            self.trend_average_cost = 0.0
            return
        remaining_cost = sum(
            (batch.fill_price if batch.fill_price is not None else self.trend_average_cost)
            * batch.actual_shares
            for batch in self.trend_batches
        )
        self.trend_average_cost = remaining_cost / self.trend_shares

    def _migrate_trend_pending_to_grid_base(self, layer_id: str) -> None:
        target_key = ("grid", layer_id, None)
        migrated = [
            pending
            for pending in self.pending_sells
            if pending.origin_family == "trend" or self._pending_owner_key(pending) == target_key
        ]
        if not migrated:
            return

        levels = max(migrated, key=lambda pending: _PENDING_PRIORITY[pending.level]).level
        pending_dates = [pending.pending_since for pending in migrated if pending.pending_since is not None]
        attempt_dates = [pending.last_attempt_date for pending in migrated if pending.last_attempt_date is not None]
        failures = [pending for pending in migrated if pending.last_failure]
        merged = PendingSellState(
            level=levels,
            requested_shares=sum(pending.requested_shares for pending in migrated),
            remaining_shares=sum(pending.remaining_shares for pending in migrated),
            origin_family="grid",
            grid_layer_id=layer_id,
            pending_since=min(pending_dates) if pending_dates else None,
            attempt_count=sum(pending.attempt_count for pending in migrated),
            last_attempt_date=max(attempt_dates) if attempt_dates else None,
            last_failure=(
                max(failures, key=lambda pending: pending.last_attempt_date or date.min).last_failure
                if failures
                else None
            ),
        )
        migrated_ids = {id(pending) for pending in migrated}
        self.pending_sells = [
            pending for pending in self.pending_sells if id(pending) not in migrated_ids
        ]
        self.pending_sells.append(merged)
        if self.pending_sell is not None and id(self.pending_sell) in migrated_ids:
            self.pending_sell = None
        self._sync_pending_view()

    @staticmethod
    def _validate_trade(shares: int, price: float) -> None:
        if shares <= 0:
            raise ValueError("shares must be positive")
        if not isfinite(price) or price <= 0:
            raise ValueError("price must be positive and finite")


def _weighted_cost(old_cost: float, old_shares: int, price: float, shares: int) -> float:
    total = old_shares + shares
    return ((old_cost * old_shares) + (price * shares)) / total
