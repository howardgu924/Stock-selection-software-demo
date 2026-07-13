from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from enum import StrEnum
from math import isfinite
from typing import Callable, Mapping, Sequence

import pandas as pd

from stock_picker.data.backtest_data import BacktestDataRequest, load_t1_backtest_data
from stock_picker.data.models import normalize_symbol

from .thermostat import TRIGGER_PLAN_OUTPUT_COLUMNS, evaluate_thermostat
from .thermostat_execution import (
    BacktestOrder,
    DailyBar,
    ExecutionPhase,
    OrderStatus,
    PortfolioLedger,
    T1ExecutionSettings,
    process_pending_sells,
    stable_sort_candidates,
)
from .thermostat_grid_executor import (
    execute_grid_candidate,
    finalize_grid_day,
    prepare_grid_day,
    preview_grid_phase,
)
from .thermostat_metrics import (
    CLOSED_TRADE_CYCLE_COLUMNS,
    EQUITY_DRAWDOWN_COLUMNS,
    MARKET_PERFORMANCE_COLUMNS,
    METRIC_SUMMARY_COLUMNS,
    PERFORMANCE_COLUMNS,
    compute_t1_thermostat_metrics,
    cumulative_realized_net_pnl,
)
from .thermostat_state import ThermostatPositionState
from .thermostat_trend_executor import (
    execute_trend_candidate,
    finalize_trend_day,
    prepare_trend_day,
    preview_trend_phase,
)


class BacktestPrecision(StrEnum):
    DAILY_APPROXIMATE = "daily_approximate"
    MINUTE_5M = "minute_5m"


DAILY_ASSET_COLUMNS = [
    "date", "cash", "position_value", "total_asset", "cash_ratio",
    "position_ratio", "realized_pnl", "unrealized_pnl", "precision",
    "precision_disclosure", "approximate_intraday_sequence",
]
DAILY_POSITION_COLUMNS = [
    "date", "symbol", "stock_mode", "total_shares", "available_shares",
    "today_bought_shares", "trend_shares", "grid_shares", "average_cost",
    "close", "market_value", "unrealized_pnl", "pending_sell_level",
    "pending_count", "precision", "precision_disclosure",
    "approximate_intraday_sequence",
]
DAILY_TRIGGER_PLAN_COLUMNS = list(TRIGGER_PLAN_OUTPUT_COLUMNS) + [
    "data_cutoff_date", "precision", "precision_disclosure",
    "approximate_intraday_sequence", "approximation_warnings",
]
ORDER_COLUMNS = [field.name for field in BacktestOrder.__dataclass_fields__.values()]
PENDING_HISTORY_COLUMNS = [
    "date", "symbol", "episode_id", "event_type", "is_terminal",
    "level", "family", "owner_id", "remaining_shares",
    "requested_shares", "pending_since", "duration_days", "attempt_count",
    "last_attempt_date", "last_failure", "source_order_id", "plan_trace_id",
]
TREND_BATCH_COLUMNS = [
    "date", "symbol", "batch_index", "target_ratio", "trigger_price",
    "planned_shares", "filled_shares", "actual_shares", "fill_price",
    "fill_date", "first_fill_date", "last_fill_date", "available_shares",
    "today_bought_shares", "status",
]
GRID_LAYER_COLUMNS = [
    "date", "symbol", "layer_id", "buy_price", "sell_price",
    "target_position_pct", "target_shares", "held_shares",
    "available_shares", "today_bought_shares", "buy_date", "buy_cost",
    "status",
]
DATA_QUALITY_COLUMNS = [
    "date", "symbol", "code", "severity", "stream", "message", "details",
    "observation_expected", "observation_missing",
]
CORPORATE_ACTION_COLUMNS = ["date", "symbol", "code", "evidence", "details"]
PARAMETER_COLUMNS = [
    "parameter_name", "parameter_value", "parameter_source",
    "user_overridden", "note",
]
STOCK_POOL_METADATA_COLUMNS = ["metadata_key", "metadata_value"]

RESULT_TABLE_COLUMNS = {
    "summary": METRIC_SUMMARY_COLUMNS,
    "daily_assets": DAILY_ASSET_COLUMNS,
    "equity_drawdown": EQUITY_DRAWDOWN_COLUMNS,
    "daily_positions": DAILY_POSITION_COLUMNS,
    "daily_trigger_plans": DAILY_TRIGGER_PLAN_COLUMNS,
    "lifecycle_orders": ORDER_COLUMNS,
    "fills": ORDER_COLUMNS,
    "failed_cancelled_orders": ORDER_COLUMNS,
    "pending_history": PENDING_HISTORY_COLUMNS,
    "trend_batches": TREND_BATCH_COLUMNS,
    "grid_layers": GRID_LAYER_COLUMNS,
    "symbol_performance": PERFORMANCE_COLUMNS,
    "trend_performance": PERFORMANCE_COLUMNS,
    "grid_performance": PERFORMANCE_COLUMNS,
    "market_performance": MARKET_PERFORMANCE_COLUMNS,
    "data_quality": DATA_QUALITY_COLUMNS,
    "corporate_actions": CORPORATE_ACTION_COLUMNS,
    "parameters": PARAMETER_COLUMNS,
    "stock_pool_metadata": STOCK_POOL_METADATA_COLUMNS,
    "closed_trade_cycles": CLOSED_TRADE_CYCLE_COLUMNS,
}


@dataclass(frozen=True)
class T1ThermostatBacktestRequest:
    service: object
    symbols: Sequence[str]
    start: str
    end: str
    initial_cash: float | None = None
    resolved_account_settings: object | None = None
    execution_settings: T1ExecutionSettings | None = None
    source: str = "baostock"
    refresh: bool = False
    stock_pool_metadata: Mapping[str, object] = field(default_factory=dict)
    trend_total_base_max: float = 0.65
    precision: BacktestPrecision = BacktestPrecision.DAILY_APPROXIMATE
    benchmark_symbol: str = "000300.SH"
    force_final_liquidation: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "precision", BacktestPrecision(self.precision))
        start = pd.Timestamp(self.start)
        end = pd.Timestamp(self.end)
        if start > end:
            raise ValueError("backtest start must not be after end")
        if not self.symbols:
            raise ValueError("symbols must not be empty")
        if self.precision is BacktestPrecision.MINUTE_5M:
            raise ValueError("BacktestPrecision.MINUTE_5M is reserved and unsupported")
        if self.force_final_liquidation:
            raise ValueError("force_final_liquidation is unsupported for T+1 v1")
        if not 0.60 <= float(self.trend_total_base_max) <= 0.70:
            raise ValueError("trend_total_base_max must be between 0.60 and 0.70")
        if self.initial_cash is not None and (
            not isfinite(float(self.initial_cash)) or float(self.initial_cash) <= 0
        ):
            raise ValueError("initial_cash must be positive and finite")


@dataclass
class T1ThermostatBacktestResult:
    summary: pd.DataFrame
    daily_assets: pd.DataFrame
    equity_drawdown: pd.DataFrame
    daily_positions: pd.DataFrame
    daily_trigger_plans: pd.DataFrame
    lifecycle_orders: pd.DataFrame
    fills: pd.DataFrame
    failed_cancelled_orders: pd.DataFrame
    pending_history: pd.DataFrame
    trend_batches: pd.DataFrame
    grid_layers: pd.DataFrame
    symbol_performance: pd.DataFrame
    trend_performance: pd.DataFrame
    grid_performance: pd.DataFrame
    market_performance: pd.DataFrame
    data_quality: pd.DataFrame
    corporate_actions: pd.DataFrame
    parameters: pd.DataFrame
    stock_pool_metadata: pd.DataFrame
    closed_trade_cycles: pd.DataFrame

    @property
    def tables(self) -> dict[str, pd.DataFrame]:
        return {name: getattr(self, name) for name in RESULT_TABLE_COLUMNS}


@dataclass(frozen=True)
class _PreparedFamily:
    family: str
    symbol: str
    plan: dict[str, object]
    bar: DailyBar
    plan_trace_id: str


def run_t1_thermostat_backtest(
    request: T1ThermostatBacktestRequest,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
) -> T1ThermostatBacktestResult:
    symbols = tuple(dict.fromkeys(normalize_symbol(item) for item in request.symbols))
    benchmark_symbol = normalize_symbol(request.benchmark_symbol)
    load_symbols = symbols + (() if benchmark_symbol in symbols else (benchmark_symbol,))
    _emit_progress(progress_callback, "load_backtest_data", 0, 1)
    bundle = load_t1_backtest_data(
        request.service,
        BacktestDataRequest(
            symbols=load_symbols,
            start=_iso_date(request.start),
            end=_iso_date(request.end),
            source=request.source,
            refresh=request.refresh,
            warmup_trading_days=252,
        ),
    )
    _emit_progress(progress_callback, "load_backtest_data", 1, 1)
    initial_cash, settings, parameter_rows = _resolve_settings(request)
    ledger = PortfolioLedger(cash=initial_cash, initial_capital=initial_cash)
    trading_dates = _execution_dates(bundle, symbols, request.start, request.end)
    quality_rows = _quality_rows(bundle.quality_issues)
    corporate_rows = _corporate_rows(bundle.corporate_action_impacts)
    plan_rows: list[dict[str, object]] = []
    asset_rows: list[dict[str, object]] = []
    position_rows: list[dict[str, object]] = []
    pending_rows: list[dict[str, object]] = []
    trend_batch_rows: list[dict[str, object]] = []
    grid_layer_rows: list[dict[str, object]] = []
    last_valid_marks: dict[str, float] = {}

    _emit_progress(progress_callback, "simulate_daily", 0, len(trading_dates))
    for day_index, trading_date in enumerate(trading_dates):
        current_date = date.fromisoformat(trading_date)
        bars = {
            symbol: _daily_bar(bundle.symbols.get(symbol), trading_date)
            for symbol in symbols
        }
        for symbol, bar in bars.items():
            if bar is not None and not bar.suspended and bar.close is not None:
                last_valid_marks[symbol] = bar.close
        current_marks = dict(last_valid_marks)
        for symbol in symbols:
            bar = bars[symbol]
            missing_observation = bar is None
            quality_rows.append(_quality_record(
                date=trading_date, symbol=symbol,
                code=(
                    "missing_symbol_date_observation"
                    if missing_observation else "symbol_date_observation"
                ),
                stream="bfq", message=(
                    "current-date bfq observation is missing"
                    if missing_observation else "current-date bfq observation is available"
                ),
                observation_expected=True,
                observation_missing=missing_observation,
            ))
            state = ledger.positions.get(symbol)
            if (
                state is not None
                and state.total_shares > 0
                and (bar is None or bar.suspended)
                and symbol in last_valid_marks
            ):
                quality_rows.append(_quality_record(
                    date=trading_date,
                    symbol=symbol,
                    code="stale_valuation_mark",
                    stream="bfq",
                    message="last valid bfq close carried for valuation and position caps",
                    details={"mark": last_valid_marks[symbol]},
                ))
        for state in ledger.positions.values():
            state.start_trading_day(current_date)
        _process_pending_open(
            ledger, settings, bars, current_date, pending_rows,
            trading_dates[: day_index + 1],
        )

        histories = {
            symbol: _prior_qfq(bundle.symbols.get(symbol), trading_date)
            for symbol in symbols
        }
        benchmark_history = _prior_qfq(
            bundle.symbols.get(benchmark_symbol), trading_date,
        )
        market_cutoff = (
            _latest_frame_date(benchmark_history)
            or _data_cutoff(bundle.trading_calendar, trading_date)
        )
        thermostat = evaluate_thermostat(
            histories=histories,
            market_history=benchmark_history,
            candidates=[{"symbol": symbol, "name": ""} for symbol in symbols],
            holdings=_holdings_frame(ledger),
            cash=ledger.cash,
            as_of=market_cutoff,
        )
        evaluated = {
            normalize_symbol(str(row["symbol"])): row
            for row in thermostat.trigger_plan.to_dict("records")
        }
        prepared: list[_PreparedFamily] = []
        for symbol in symbols:
            plan = dict(evaluated.get(symbol) or _empty_plan(symbol))
            plan["symbol"] = symbol
            plan["date"] = trading_date
            plan["data_cutoff_date"] = _latest_frame_date(histories[symbol])
            available = len(histories[symbol].dropna(subset=["close"]))
            if available < 252:
                _mark_insufficient(plan)
                quality_rows.append(_quality_record(
                    date=trading_date, symbol=symbol, code="insufficient_data",
                    message=f"{available} prior qfq bars available; 252 required",
                    details={"available": available, "required": 252},
                ))
            bar = bars[symbol]
            snapshot = _plan_snapshot(plan, bar)
            plan_rows.append(snapshot)
            if bar is None:
                quality_rows.append(_quality_record(
                    date=trading_date, symbol=symbol,
                    code="missing_execution_price",
                    message="current-date bfq execution bar is unavailable",
                ))
                continue
            for family in _families_for_plan(plan, ledger.positions.get(symbol)):
                context = (
                    prepare_trend_day(
                        plan, bar, ledger, settings, current_date, current_marks,
                    )
                    if family == "trend"
                    else prepare_grid_day(
                        plan, bar, ledger, settings, current_date, current_marks,
                    )
                )
                prepared.append(_PreparedFamily(
                    family=family, symbol=symbol, plan=plan, bar=bar,
                    plan_trace_id=str(context.plan_trace_id if hasattr(context, "plan_trace_id") else context.plan_order_id),
                ))

        for phase in ExecutionPhase:
            candidates = []
            owner: dict[str, _PreparedFamily] = {}
            for item in prepared:
                if item.family == "trend" and phase not in {
                    ExecutionPhase.RISK_CONTROL, ExecutionPhase.TREND_EXIT,
                    ExecutionPhase.TREND_REDUCE, ExecutionPhase.TREND_BUY,
                }:
                    continue
                if item.family == "grid" and phase not in {
                    ExecutionPhase.RISK_CONTROL, ExecutionPhase.GRID_SELL,
                    ExecutionPhase.GRID_BUY,
                }:
                    continue
                previewed = (
                    preview_trend_phase(
                        item.plan, item.bar, ledger, settings, current_date, phase,
                    )
                    if item.family == "trend"
                    else preview_grid_phase(
                        item.plan, item.bar, ledger, settings, current_date, phase,
                    )
                )
                for candidate in previewed:
                    candidates.append(candidate)
                    owner[candidate.candidate_id] = item
            for candidate in stable_sort_candidates(candidates):
                item = owner[candidate.candidate_id]
                order_start = len(ledger.orders)
                if item.family == "trend":
                    execute_trend_candidate(
                        candidate, item.plan, item.bar, ledger, settings, current_date,
                    )
                else:
                    execute_grid_candidate(
                        candidate, item.plan, item.bar, ledger, settings, current_date,
                    )
                if "approximate_intraday_sequence" in candidate.approximation_warnings:
                    for order in ledger.orders[order_start:]:
                        order.quality_warning = _append_warning(
                            order.quality_warning, "approximate_intraday_sequence",
                        )

        for item in prepared:
            if item.family == "trend":
                finalize_trend_day(
                    item.plan, item.bar, ledger, settings, current_date,
                )
            else:
                finalize_grid_day(
                    item.plan, item.bar, ledger, settings, current_date,
                )
            _expire_untriggered_plan(ledger, item, current_date)

        _snapshot_close(
            trading_date=trading_date, trading_dates=trading_dates[: day_index + 1],
            symbols=symbols, ledger=ledger,
            valuation_marks=current_marks,
            asset_rows=asset_rows, position_rows=position_rows,
            pending_rows=pending_rows, trend_batch_rows=trend_batch_rows,
            grid_layer_rows=grid_layer_rows,
            approximate_intraday_sequence=any(
                bool(row["approximate_intraday_sequence"])
                for row in plan_rows if row["date"] == trading_date
            ),
        )
        _emit_progress(
            progress_callback, "simulate_daily", day_index + 1, len(trading_dates),
            current_symbol=symbols[-1] if symbols else "",
        )

    daily_assets = _frame(asset_rows, DAILY_ASSET_COLUMNS)
    daily_positions = _frame(position_rows, DAILY_POSITION_COLUMNS)
    daily_plans = _frame(plan_rows, DAILY_TRIGGER_PLAN_COLUMNS)
    lifecycle_orders = _orders_frame(ledger.orders)
    fills = _orders_frame(ledger.fills)
    failed = lifecycle_orders[
        lifecycle_orders["status"].isin(
            [OrderStatus.FAILED.value, OrderStatus.CANCELLED.value, OrderStatus.EXPIRED.value]
        )
    ].reset_index(drop=True).reindex(columns=ORDER_COLUMNS)
    pending_history = _frame(pending_rows, PENDING_HISTORY_COLUMNS)
    data_quality = _frame(quality_rows, DATA_QUALITY_COLUMNS)
    corporate_actions = _frame(corporate_rows, CORPORATE_ACTION_COLUMNS)
    benchmark = _benchmark_frame(bundle, benchmark_symbol, trading_dates)
    _emit_progress(progress_callback, "calculate_metrics", 0, 1)
    metrics = compute_t1_thermostat_metrics(
        daily_assets=daily_assets, fills=fills,
        lifecycle_orders=lifecycle_orders, pending_history=pending_history,
        data_quality=data_quality, corporate_actions=corporate_actions,
        benchmark=benchmark, initial_cash=initial_cash,
        daily_positions=daily_positions,
        daily_trigger_plans=daily_plans, benchmark_symbol=benchmark_symbol,
    )
    _emit_progress(progress_callback, "calculate_metrics", 1, 1)
    tables = {
        "summary": metrics.summary,
        "daily_assets": daily_assets,
        "equity_drawdown": metrics.equity_drawdown,
        "daily_positions": daily_positions,
        "daily_trigger_plans": daily_plans,
        "lifecycle_orders": lifecycle_orders,
        "fills": fills,
        "failed_cancelled_orders": failed,
        "pending_history": pending_history,
        "trend_batches": _frame(trend_batch_rows, TREND_BATCH_COLUMNS),
        "grid_layers": _frame(grid_layer_rows, GRID_LAYER_COLUMNS),
        "symbol_performance": metrics.symbol_performance,
        "trend_performance": metrics.trend_performance,
        "grid_performance": metrics.grid_performance,
        "market_performance": metrics.market_performance,
        "data_quality": data_quality,
        "corporate_actions": corporate_actions,
        "parameters": _frame(parameter_rows, PARAMETER_COLUMNS),
        "stock_pool_metadata": _metadata_frame(request.stock_pool_metadata),
        "closed_trade_cycles": metrics.closed_trade_cycles,
    }
    tables = {
        name: frame.reindex(columns=RESULT_TABLE_COLUMNS[name])
        for name, frame in tables.items()
    }
    return T1ThermostatBacktestResult(**tables)


def _emit_progress(
    callback: Callable[[dict[str, object]], None] | None,
    stage: str,
    completed: int,
    total: int,
    *,
    current_symbol: str = "",
) -> None:
    if callback is None:
        return
    callback({
        "stage": stage,
        "completed": completed,
        "total": total,
        "current_symbol": current_symbol,
    })


def _resolve_settings(
    request: T1ThermostatBacktestRequest,
) -> tuple[float, T1ExecutionSettings, list[dict[str, object]]]:
    resolved_container = request.resolved_account_settings
    resolved = getattr(resolved_container, "settings", resolved_container)
    resolved_cash = getattr(resolved, "initial_cash", None)
    resolved_total_cap = getattr(resolved_container, "max_total_position_pct", None)
    resolved_parameters = getattr(resolved_container, "parameters", None)
    resolved_sources: dict[str, str] = {}
    if isinstance(resolved_parameters, pd.DataFrame):
        resolved_sources = {
            str(row["parameter_name"]): str(row["parameter_source"])
            for row in resolved_parameters.to_dict("records")
        }
    initial_cash = float(
        request.initial_cash if request.initial_cash is not None
        else resolved_cash if resolved_cash is not None else 100_000.0
    )
    base = request.execution_settings or T1ExecutionSettings(
        commission_rate=float(getattr(resolved, "commission_rate", 0.0003)),
        minimum_commission=float(
            getattr(resolved, "min_commission", getattr(resolved, "minimum_commission", 5.0))
        ),
        stamp_tax_rate=float(getattr(resolved, "stamp_tax_rate", 0.001)),
        slippage_pct=float(getattr(resolved, "slippage_pct", 0.001)),
        trend_total_base_max=request.trend_total_base_max,
        account_total_max=float(
            resolved_total_cap if resolved_total_cap is not None else 0.95
        ),
    )
    settings = T1ExecutionSettings(**{
        **asdict(base),
        "trend_total_base_max": request.trend_total_base_max,
        "force_final_liquidation": False,
    })
    execution_source_names = (
        set(asdict(request.execution_settings))
        if request.execution_settings is not None else set()
    )
    sources = {
        "initial_cash": "user_override" if request.initial_cash is not None else resolved_sources.get(
            "initial_cash",
            "account_setting" if resolved_cash is not None else "system_default",
        ),
        "commission_rate": resolved_sources.get("commission_rate", "system_default"),
        "minimum_commission": resolved_sources.get("min_commission", "system_default"),
        "stamp_tax_rate": resolved_sources.get("stamp_tax_rate", "system_default"),
        "slippage_pct": resolved_sources.get("slippage_pct", "system_default"),
        "buy_lot_size": resolved_sources.get("buy_lot_size", "system_default"),
        "trend_total_base_max": "user_override" if request.trend_total_base_max != 0.65 else "system_default",
        "account_total_max": resolved_sources.get(
            "max_total_position_pct",
            "account_setting" if resolved_total_cap is not None else "system_default",
        ),
    }
    if request.execution_settings is not None:
        sources.update({name: "user_override" for name in execution_source_names})
        sources["trend_total_base_max"] = (
            "user_override" if request.trend_total_base_max != 0.65 else "system_default"
        )
        sources["force_final_liquidation"] = "system_default"
    rows = []
    for name, value in {
        "initial_cash": initial_cash,
        **asdict(settings),
        "precision": request.precision.value,
        "benchmark_symbol": normalize_symbol(request.benchmark_symbol),
        "source": request.source,
        "refresh": request.refresh,
        "warmup_trading_days": 252,
    }.items():
        source = sources.get(name, "request" if name in {"precision", "benchmark_symbol", "source", "refresh"} else "system_default")
        rows.append({
            "parameter_name": name, "parameter_value": value,
            "parameter_source": source, "user_overridden": source == "user_override",
            "note": "daily-bar approximate execution" if name == "precision" else "",
        })
    return initial_cash, settings, rows


def _execution_dates(bundle, symbols: Sequence[str], start: str, end: str) -> list[str]:
    start_date = _iso_date(start)
    end_date = _iso_date(end)
    dates = set()
    for symbol in symbols:
        item = bundle.symbols.get(symbol)
        if item is None or item.execution_frame.empty:
            continue
        dates.update(
            value for value in item.execution_frame["date"].astype(str)
            if start_date <= value <= end_date
        )
    return sorted(dates)


def _prior_qfq(symbol_data, trading_date: str) -> pd.DataFrame:
    if symbol_data is None or symbol_data.indicator_frame.empty:
        return pd.DataFrame()
    frame = symbol_data.indicator_frame.copy()
    return frame[frame["date"].astype(str) < trading_date].sort_values("date").reset_index(drop=True)


def _daily_bar(symbol_data, trading_date: str) -> DailyBar | None:
    if symbol_data is None or symbol_data.execution_frame.empty:
        return None
    rows = symbol_data.execution_frame[
        symbol_data.execution_frame["date"].astype(str) == trading_date
    ]
    if rows.empty:
        return None
    row = rows.iloc[-1]
    return DailyBar(
        date=date.fromisoformat(trading_date),
        open=_optional_number(row.get("open")), high=_optional_number(row.get("high")),
        low=_optional_number(row.get("low")), close=_optional_number(row.get("close")),
        volume=_optional_number(row.get("volume")) or 0.0,
        previous_close=_optional_number(row.get("prev_close", row.get("previous_close"))),
        limit_up_price=_optional_number(row.get("limit_up_price")),
        limit_down_price=_optional_number(row.get("limit_down_price")),
        suspended=bool(row.get("is_suspended")) if not pd.isna(row.get("is_suspended")) else False,
    )


def _process_pending_open(
    ledger: PortfolioLedger, settings: T1ExecutionSettings,
    bars: Mapping[str, DailyBar | None], trading_date: date,
    pending_rows: list[dict[str, object]], trading_dates: Sequence[str],
) -> None:
    states = sorted(
        (state for state in ledger.positions.values() if state.pending_sells),
        key=lambda state: normalize_symbol(state.symbol),
    )
    for state in states:
        bar = bars.get(normalize_symbol(state.symbol))
        if bar is None:
            bar = DailyBar(
                date=trading_date, open=None, high=None, low=None, close=None,
                volume=0.0,
            )
        pending_before = {
            (pending.origin_family, pending.grid_layer_id, pending.batch_index): pending
            for pending in state.pending_sells
        }
        sources = {
            key: _pending_source_order(
                ledger, normalize_symbol(state.symbol), pending,
            )
            for key, pending in pending_before.items()
        }
        orders = process_pending_sells(
            ledger, settings, bar, state, trading_date,
        )
        for order in orders:
            source = sources.get(
                (order.family, order.grid_layer, order.trend_batch),
            )
            if source is not None:
                order.plan_trace_id = source.plan_trace_id or source.order_id
                order.candidate_trace_id = source.candidate_trace_id
            pending = pending_before.get(
                (order.family, order.grid_layer, order.trend_batch),
            )
            if pending is not None and order.status is OrderStatus.FILLED:
                symbol = normalize_symbol(state.symbol)
                owner_id = pending.grid_layer_id or (
                    f"batch-{pending.batch_index}"
                    if pending.batch_index is not None else ""
                )
                pending_rows.append({
                    "date": trading_date.isoformat(), "symbol": symbol,
                    "episode_id": _pending_episode_id(symbol, pending),
                    "event_type": "filled", "is_terminal": True,
                    "level": pending.level.value,
                    "family": pending.origin_family, "owner_id": owner_id,
                    "remaining_shares": 0,
                    "requested_shares": pending.requested_shares,
                    "pending_since": _date_value(pending.pending_since),
                    "duration_days": _pending_duration(
                        trading_dates, pending.pending_since,
                        trading_date.isoformat(),
                    ),
                    "attempt_count": pending.attempt_count,
                    "last_attempt_date": trading_date.isoformat(),
                    "last_failure": "", "source_order_id": order.order_id,
                    "plan_trace_id": order.plan_trace_id,
                })


def _holdings_frame(ledger: PortfolioLedger) -> pd.DataFrame:
    rows = []
    for symbol, state in ledger.positions.items():
        rows.append({
            "symbol": symbol, "shares": state.total_shares,
            "total_shares": state.total_shares,
            "available_shares": state.available_shares,
            "today_bought_shares": state.today_bought_shares,
            "average_cost": state.average_cost, "avg_cost": state.average_cost,
            "trend_average_cost": state.trend_average_cost,
            "last_effective_exit_trigger": state.last_effective_exit_trigger,
        })
    return pd.DataFrame(rows)


def _families_for_plan(
    plan: Mapping[str, object], state: ThermostatPositionState | None,
) -> tuple[str, ...]:
    mode = str(plan.get("stock_mode") or "insufficient_data")
    has_grid = bool(state is not None and any(layer.held_shares for layer in state.grid_layers.values()))
    if mode == "range":
        return ("grid",)
    if mode == "downtrend":
        return ("trend", "grid")
    if mode == "chaotic" and has_grid:
        return ("trend", "grid")
    return ("trend",)


def _empty_plan(symbol: str) -> dict[str, object]:
    return {column: "" for column in TRIGGER_PLAN_OUTPUT_COLUMNS} | {
        "symbol": symbol, "stock_mode": "insufficient_data",
        "market_position_discount": 0.5,
    }


def _mark_insufficient(plan: dict[str, object]) -> None:
    plan["stock_mode"] = "insufficient_data"
    plan["data_sufficient"] = False
    plan["trigger_status"] = "not_applicable"
    plan["filled_status"] = "not_applicable"
    plan["failed_reason"] = "insufficient_data"
    for name in (
        "trend_buy_trigger", "trend_reduce_trigger", "trend_exit_trigger",
        "effective_trend_exit_trigger", "grid_buy_levels", "grid_sell_levels",
    ):
        plan[name] = ""


def _plan_snapshot(
    plan: Mapping[str, object], bar: DailyBar | None,
) -> dict[str, object]:
    ambiguous = _plan_has_ambiguous_daily_sequence(plan, bar)
    row = {column: plan.get(column, "") for column in TRIGGER_PLAN_OUTPUT_COLUMNS}
    row.update({
        "data_cutoff_date": plan.get("data_cutoff_date", ""),
        "precision": BacktestPrecision.DAILY_APPROXIMATE.value,
        "precision_disclosure": "daily bars; trigger sequence and fills are approximate",
        "approximate_intraday_sequence": ambiguous,
        "approximation_warnings": "approximate_intraday_sequence" if ambiguous else "",
    })
    return row


def _plan_has_ambiguous_daily_sequence(
    plan: Mapping[str, object], bar: DailyBar | None,
) -> bool:
    if bar is None or bar.high is None or bar.low is None:
        return False
    mode = str(plan.get("stock_mode") or "")
    if mode == "trend":
        buy = _optional_number(plan.get("trend_buy_trigger"))
        exit_trigger = _optional_number(
            plan.get("effective_trend_exit_trigger")
            or plan.get("trend_exit_trigger")
        )
        reduce = _optional_number(plan.get("trend_reduce_trigger"))
        return bool(
            buy is not None and bar.high >= buy
            and (
                exit_trigger is not None and bar.low <= exit_trigger
                or reduce is not None and bar.low <= reduce
            )
        )
    if mode == "range":
        buys = _levels(plan.get("grid_buy_levels"))
        sells = _levels(plan.get("grid_sell_levels"))
        return bool(
            buys and sells
            and bar.low <= max(buys)
            and bar.high >= min(sells)
        )
    return False


def _levels(value: object) -> list[float]:
    raw = value.split("|") if isinstance(value, str) else value if isinstance(value, (list, tuple)) else []
    return [number for item in raw if (number := _optional_number(item)) is not None]


def _data_cutoff(calendar: Sequence[str], trading_date: str) -> str:
    prior = sorted(str(item) for item in calendar if str(item) < trading_date)
    if prior:
        return prior[-1]
    return (pd.Timestamp(trading_date) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def _latest_frame_date(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty or "date" not in frame:
        return ""
    dates = frame["date"].dropna().astype(str)
    return dates.max() if not dates.empty else ""


def _expire_untriggered_plan(
    ledger: PortfolioLedger, item: _PreparedFamily, trading_date: date,
) -> None:
    linked = any(
        order.plan_trace_id == item.plan_trace_id
        and order.status is not OrderStatus.PLAN_CREATED
        for order in ledger.orders
    )
    if linked:
        return
    ledger.orders.append(BacktestOrder(
        order_id=f"{item.plan_trace_id}:expired",
        trade_date=trading_date, symbol=item.symbol,
        mode=str(item.plan.get("stock_mode") or ""), family=item.family,
        trigger_type=f"{item.family}_plan_expiry", status=OrderStatus.EXPIRED,
        failure_reason="untriggered_plan_expired",
        plan_trace_id=item.plan_trace_id,
        approximate_intraday_sequence=True,
        quality_warning="daily_plan_expires_after_close",
    ))


def _snapshot_close(
    *, trading_date: str, trading_dates: Sequence[str], symbols: Sequence[str],
    ledger: PortfolioLedger,
    valuation_marks: Mapping[str, float],
    asset_rows: list[dict[str, object]], position_rows: list[dict[str, object]],
    pending_rows: list[dict[str, object]],
    trend_batch_rows: list[dict[str, object]],
    grid_layer_rows: list[dict[str, object]],
    approximate_intraday_sequence: bool,
) -> None:
    position_value = 0.0
    unrealized = 0.0
    for symbol in symbols:
        state = ledger.positions.get(symbol)
        if state is None:
            continue
        mark = valuation_marks.get(symbol, state.average_cost)
        market_value = mark * state.total_shares
        state_unrealized = (mark - state.average_cost) * state.total_shares
        position_value += market_value
        unrealized += state_unrealized
        grid_shares = sum(layer.held_shares for layer in state.grid_layers.values())
        position_rows.append({
            "date": trading_date, "symbol": symbol,
            "stock_mode": state.current_mode,
            "total_shares": state.total_shares,
            "available_shares": state.available_shares,
            "today_bought_shares": state.today_bought_shares,
            "trend_shares": state.trend_shares, "grid_shares": grid_shares,
            "average_cost": state.average_cost, "close": mark,
            "market_value": market_value, "unrealized_pnl": state_unrealized,
            "pending_sell_level": (
                state.pending_sell.level.value if state.pending_sell is not None else ""
            ),
            "pending_count": len(state.pending_sells),
            "precision": BacktestPrecision.DAILY_APPROXIMATE.value,
            "precision_disclosure": "daily close position valuation",
            "approximate_intraday_sequence": approximate_intraday_sequence,
        })
        for pending in state.pending_sells:
            source = _pending_source_order(ledger, symbol, pending)
            owner_id = pending.grid_layer_id or (
                f"batch-{pending.batch_index}" if pending.batch_index is not None else ""
            )
            duration = _pending_duration(
                trading_dates, pending.pending_since, trading_date,
            )
            pending_rows.append({
                "date": trading_date, "symbol": symbol,
                "episode_id": _pending_episode_id(symbol, pending),
                "event_type": (
                    "retry"
                    if pending.last_attempt_date is not None
                    and pending.last_attempt_date.isoformat() == trading_date
                    and pending.last_failure
                    else "snapshot"
                ),
                "is_terminal": False,
                "level": pending.level.value, "family": pending.origin_family,
                "owner_id": owner_id, "remaining_shares": pending.remaining_shares,
                "requested_shares": pending.requested_shares,
                "pending_since": _date_value(pending.pending_since),
                "duration_days": duration, "attempt_count": pending.attempt_count,
                "last_attempt_date": _date_value(pending.last_attempt_date),
                "last_failure": pending.last_failure or "",
                "source_order_id": source.order_id if source is not None else "",
                "plan_trace_id": source.plan_trace_id if source is not None else "",
            })
        for batch in state.trend_batches:
            trend_batch_rows.append({
                "date": trading_date, "symbol": symbol, **asdict(batch),
            })
        for layer in state.grid_layers.values():
            grid_layer_rows.append({
                "date": trading_date, "symbol": symbol, **asdict(layer),
            })
    total_asset = ledger.cash + position_value
    asset_rows.append({
        "date": trading_date, "cash": ledger.cash,
        "position_value": position_value, "total_asset": total_asset,
        "cash_ratio": ledger.cash / total_asset if total_asset else 0.0,
        "position_ratio": position_value / total_asset if total_asset else 0.0,
        "realized_pnl": cumulative_realized_net_pnl(_orders_frame(ledger.fills)),
        "unrealized_pnl": unrealized,
        "precision": BacktestPrecision.DAILY_APPROXIMATE.value,
        "precision_disclosure": "daily close valuation; no final liquidation",
        "approximate_intraday_sequence": approximate_intraday_sequence,
    })


def _pending_source_order(ledger, symbol, pending):
    for order in reversed(ledger.orders):
        if normalize_symbol(order.symbol) != symbol or order.family != pending.origin_family:
            continue
        if pending.grid_layer_id is not None and order.grid_layer != pending.grid_layer_id:
            continue
        if pending.batch_index is not None and order.trend_batch != pending.batch_index:
            continue
        if order.pending_level is not None or order.status in {OrderStatus.PENDING, OrderStatus.PENDING_RETRY, OrderStatus.FAILED}:
            return order
    return None


def _pending_episode_id(symbol: str, pending) -> str:
    owner_id = pending.grid_layer_id or (
        f"batch-{pending.batch_index}" if pending.batch_index is not None else "all"
    )
    return ":".join((
        symbol, pending.origin_family, owner_id,
        _date_value(pending.pending_since),
    ))


def _pending_duration(
    trading_dates: Sequence[str], pending_since: date | None, through: str,
) -> int:
    if pending_since is None:
        return 0
    return max(0, sum(
        1 for value in trading_dates
        if pending_since.isoformat() <= value <= through
    ) - 1)


def _orders_frame(orders: Sequence[BacktestOrder]) -> pd.DataFrame:
    rows = []
    for order in orders:
        row = asdict(order)
        row["trade_date"] = _date_value(order.trade_date)
        row["status"] = order.status.value
        row["pending_level"] = order.pending_level.value if order.pending_level is not None else ""
        rows.append(row)
    return _frame(rows, ORDER_COLUMNS)


def _quality_rows(issues: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [
        _quality_record(
            date=str(issue.get("date") or ""), symbol=str(issue.get("symbol") or ""),
            code=str(issue.get("code") or "data_quality_issue"),
            stream=str(issue.get("stream") or ""),
            message=str(issue.get("message") or issue.get("error") or issue.get("warning") or ""),
            details=dict(issue),
        )
        for issue in issues
    ]


def _quality_record(
    *, date: str, symbol: str, code: str, message: str,
    stream: str = "", details: object = "",
    observation_expected: bool = False,
    observation_missing: bool = False,
) -> dict[str, object]:
    return {
        "date": date, "symbol": symbol, "code": code,
        "severity": "warning", "stream": stream,
        "message": message, "details": details,
        "observation_expected": observation_expected,
        "observation_missing": observation_missing,
    }


def _corporate_rows(issues: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [{
        "date": str(issue.get("date") or ""),
        "symbol": str(issue.get("symbol") or ""),
        "code": str(issue.get("code") or "unsupported_corporate_action"),
        "evidence": issue.get("evidence", ""), "details": dict(issue),
    } for issue in issues]


def _benchmark_frame(bundle, symbol: str, trading_dates: Sequence[str]) -> pd.DataFrame:
    item = bundle.symbols.get(symbol)
    if item is None or item.indicator_frame.empty:
        return pd.DataFrame(columns=["date", "close"])
    allowed = set(trading_dates)
    frame = item.indicator_frame[
        item.indicator_frame["date"].astype(str).isin(allowed)
    ].copy()
    return frame.reindex(columns=["date", "close"])


def _metadata_frame(metadata: Mapping[str, object]) -> pd.DataFrame:
    return _frame(
        [{"metadata_key": key, "metadata_value": value} for key, value in metadata.items()],
        STOCK_POOL_METADATA_COLUMNS,
    )


def _frame(rows: Sequence[Mapping[str, object]], columns: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame(list(rows)).reindex(columns=list(columns))


def _optional_number(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return number if isfinite(number) and number > 0 else None


def _date_value(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _iso_date(value: object) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _append_warning(existing: str, warning: str) -> str:
    return ";".join(item for item in (existing, warning) if item)
