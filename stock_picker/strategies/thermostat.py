from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt
from typing import TYPE_CHECKING, Any, Callable, Iterable

import pandas as pd

if TYPE_CHECKING:
    from .thermostat_backtest import T1ThermostatBacktestRequest, T1ThermostatBacktestResult

from stock_picker.data.models import normalize_symbol, symbol_code
from stock_picker.strategies.event_backtest import (
    BacktestSettings,
    EventBacktestEngine,
    EventContext,
    Signal,
)


DEFAULT_MARKET_BENCHMARKS = [
    ("000852.SH", 0.50, "CSI1000"),
    ("399006.SZ", 0.30, "ChiNext"),
    ("000688.SH", 0.20, "STAR50"),
]
RISK_ANCHOR_INDEX = "000300.SH"
RISK_ANCHOR_COMPONENTS = ["000300.SH", "000852.SH", "399006.SZ"]

STOCK_MODES = ("trend", "range", "downtrend", "chaotic", "insufficient_data")
MARKET_POSITION_DISCOUNTS = {
    "strong": 1.0,
    "normal": 0.9,
    "weak": 0.7,
    "extreme_weak": 0.5,
}
PENDING_SELL_LEVELS = ("", "pending_reduce", "pending_exit", "pending_emergency_exit")
TRIGGER_TYPES = (
    "trend_buy",
    "trend_reduce",
    "trend_exit",
    "grid_buy",
    "grid_sell",
    "risk_control_sell",
)
REQUIRED_TRIGGER_PLAN_COLUMNS = [
    "stock_mode",
    "market_regime_normalized",
    "market_position_discount",
    "boll_upper",
    "boll_mid",
    "boll_lower",
    "atr20",
    "volume_ma20",
    "trend_buy_trigger",
    "trend_reduce_trigger",
    "trend_exit_trigger",
    "effective_trend_exit_trigger",
    "trend_batches",
    "grid_buy_levels",
    "grid_sell_levels",
    "configured_grid_layers",
    "effective_grid_layers",
    "grid_layer_spacing_pct",
    "grid_total_max_position_pct",
    "target_position_pct",
    "max_position_pct",
    "available_shares",
    "today_bought_shares",
    "total_shares",
    "share_split_source",
    "pending_sell_level",
    "trigger_status",
    "filled_status",
    "failed_reason",
]

LEGACY_ADVICE_COLUMNS = [
    "symbol",
    "code",
    "name",
    "date",
    "market_regime",
    "stock_regime",
    "strategy",
    "strategy_family",
    "action",
    "strength",
    "score",
    "priority",
    "suggested_position_pct",
    "suggested_shares",
    "entry_price",
    "stop_price",
    "target_price",
    "reference_price",
    "grid_upper",
    "grid_lower",
    "grid_mid",
    "grid_unit_pct",
    "grid_max_layers",
    "grid_stop_condition",
    "reason",
    "risk_note",
    "executable",
    "data_sufficient",
]
REQUIRED_ADVICE_COLUMNS = LEGACY_ADVICE_COLUMNS + [
    column for column in REQUIRED_TRIGGER_PLAN_COLUMNS if column not in LEGACY_ADVICE_COLUMNS
]
TRIGGER_PLAN_OUTPUT_COLUMNS = [
    "symbol",
    "code",
    "name",
    "date",
    "market_regime",
    "market_regime_normalized",
    "market_position_discount",
    "stock_regime",
    "stock_mode",
    "reference_price",
    "boll_upper",
    "boll_mid",
    "boll_lower",
    "atr20",
    "volume_ma20",
    "trend_buy_trigger",
    "trend_reduce_trigger",
    "trend_exit_trigger",
    "effective_trend_exit_trigger",
    "trend_batches",
    "grid_lower",
    "grid_mid",
    "grid_upper",
    "grid_max_layers",
    "configured_grid_layers",
    "effective_grid_layers",
    "grid_layer_spacing_pct",
    "grid_buy_levels",
    "grid_sell_levels",
    "grid_total_max_position_pct",
    "target_position_pct",
    "max_position_pct",
    "available_shares",
    "today_bought_shares",
    "total_shares",
    "share_split_source",
    "pending_sell_level",
    "trigger_status",
    "filled_status",
    "failed_reason",
    "risk_note",
    "reason",
    "data_sufficient",
]


@dataclass
class ThermostatResult:
    market_overview: pd.DataFrame
    errors: pd.DataFrame
    trigger_plan: pd.DataFrame
    # Deprecated compatibility attributes; main output is trigger_plan.
    holding_advice: pd.DataFrame = field(default_factory=pd.DataFrame)
    new_candidates: pd.DataFrame = field(default_factory=pd.DataFrame)
    grid_advice: pd.DataFrame = field(default_factory=pd.DataFrame)
    trend_advice: pd.DataFrame = field(default_factory=pd.DataFrame)
    _deprecated_signal_rows: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False)

    @property
    def tables(self) -> dict[str, pd.DataFrame]:
        return {
            "market_overview": self.market_overview,
            "trigger_plan": self.trigger_plan,
            "errors": self.errors,
        }


@dataclass
class LegacyThermostatBacktestResult:
    summary: pd.DataFrame
    regime_performance: pd.DataFrame
    diagnostics: pd.DataFrame
    equity: pd.DataFrame
    daily_portfolio: pd.DataFrame = field(default_factory=pd.DataFrame)
    evaluation_detail: pd.DataFrame = field(default_factory=pd.DataFrame)
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)
    positions: pd.DataFrame = field(default_factory=pd.DataFrame)
    symbol_performance: pd.DataFrame = field(default_factory=pd.DataFrame)
    data_quality: pd.DataFrame = field(default_factory=pd.DataFrame)
    parameters: pd.DataFrame = field(default_factory=pd.DataFrame)


def calculate_regime_metrics(history: pd.DataFrame) -> dict[str, object]:
    frame = _prepare_history(history)
    closes = pd.to_numeric(frame.get("close"), errors="coerce").dropna()
    count = len(closes)
    last = float(closes.iloc[-1]) if count else None
    ma20 = _tail_mean(closes, 20)
    ma60 = _tail_mean(closes, 60)
    ret20 = _tail_return(closes, 20)
    ret60 = _tail_return(closes, 60)
    range20 = _tail_range(closes, 20)
    range60 = _tail_range(closes, 60)
    daily = closes.pct_change().dropna()
    vol20 = float(daily.tail(20).std()) if len(daily.tail(20)) >= 2 else 0.0
    ma20_slope = _ma_slope(closes, window=20, lag=5)
    ma60_slope = _ma_slope(closes, window=60, lag=10)
    close_ma20_distance = (last / ma20 - 1) if last is not None and ma20 else 0.0
    close_ma60_distance = (last / ma60 - 1) if last is not None and ma60 else 0.0
    trend_strength = ret60 / (vol20 * sqrt(60)) if vol20 else 0.0
    vol20_percentile = None
    range20_percentile = None
    if count >= 252:
        rolling_vol20 = closes.pct_change().rolling(20).std().dropna().tail(252)
        rolling_range20 = closes.rolling(20).apply(
            lambda values: (values.max() - values.min()) / values.mean() if values.mean() else 0.0,
            raw=False,
        ).dropna().tail(252)
        if not rolling_vol20.empty:
            current = float(rolling_vol20.iloc[-1])
            vol20_percentile = float((rolling_vol20 <= current).mean() * 100)
        if not rolling_range20.empty:
            current = float(rolling_range20.iloc[-1])
            range20_percentile = float((rolling_range20 <= current).mean() * 100)
    if count < 60:
        bucket = "insufficient"
    elif count < 120:
        bucket = "reduced"
    elif count < 252:
        bucket = "normal"
    else:
        bucket = "full"
    return {
        "close": last,
        "ret20": ret20,
        "ret60": ret60,
        "ma20": ma20,
        "ma60": ma60,
        "range20": range20,
        "range60": range60,
        "vol20": vol20,
        "ma20_slope": ma20_slope,
        "ma60_slope": ma60_slope,
        "close_ma20_distance": close_ma20_distance,
        "close_ma60_distance": close_ma60_distance,
        "vol20_percentile_252": vol20_percentile,
        "range20_percentile_252": range20_percentile,
        "trend_strength": trend_strength,
        "data_sufficient": count >= 60,
        "length_bucket": bucket,
        "count": count,
        "regime_date": _last_date(frame),
    }


def classify_market_regime(history: pd.DataFrame) -> dict[str, object]:
    metrics = calculate_regime_metrics(history)
    if not metrics["data_sufficient"]:
        return _regime_result("insufficient_data", "low", "数据不足，至少需要60个交易日", metrics)
    ret60 = float(metrics["ret60"])
    close = metrics["close"]
    ma60 = float(metrics["ma60"])
    ma20_slope = float(metrics["ma20_slope"])
    ma60_slope = float(metrics["ma60_slope"])
    range20 = float(metrics["range20"])
    range60 = float(metrics["range60"])
    vol20 = float(metrics["vol20"])
    conflict = (ret60 > 0 and close is not None and close < ma60) or (ret60 < 0 and close is not None and close > ma60)
    if ret60 <= -0.06 and close is not None and close < ma60 and ma20_slope < 0:
        return _regime_result("market_downtrend", "medium", "市场60日收益为负且跌破60日均线", metrics)
    if vol20 > 0.035 or range20 > 0.12 or conflict:
        return _regime_result("market_transition", "low", "市场波动过高或趋势证据冲突", metrics)
    if ret60 >= 0.05 and close is not None and close > ma60 and ma20_slope > 0:
        return _regime_result("market_uptrend", "medium", "市场60日收益向上且均线斜率为正", metrics)
    if abs(ret60) <= 0.05 and range60 <= 0.15 and abs(ma60_slope) <= 0.02:
        return _regime_result("market_range", "medium", "市场处于震荡区间", metrics)
    return _regime_result("market_transition", "low", "市场趋势证据不稳定", metrics)


def classify_stock_regime(history: pd.DataFrame) -> dict[str, object]:
    metrics = calculate_regime_metrics(history)
    if not metrics["data_sufficient"]:
        return _regime_result("insufficient_data", "low", "数据不足，至少需要60个交易日", metrics)
    ret20 = float(metrics["ret20"])
    ret60 = float(metrics["ret60"])
    close = metrics["close"]
    ma20 = float(metrics["ma20"])
    ma60 = float(metrics["ma60"])
    ma20_slope = float(metrics["ma20_slope"])
    range20 = float(metrics["range20"])
    close_ma20_distance = float(metrics["close_ma20_distance"])
    trend_strength = float(metrics["trend_strength"])
    vol_pct = metrics["vol20_percentile_252"]
    range_pct = metrics["range20_percentile_252"]
    conflict = (ret60 > 0 and close is not None and close < ma60) or (ret60 < 0 and close is not None and close > ma60)
    extreme_transition = (
        (vol_pct is not None and vol_pct >= 80)
        or (range_pct is not None and range_pct >= 80)
        or range20 > 0.30
        or conflict
    )
    if ret60 <= -0.08 and close is not None and close < ma60 and ma20_slope < 0:
        return _regime_result("downtrend", "medium", "个股60日收益为负且跌破60日均线", metrics)
    if extreme_transition:
        return _regime_result("transition", "low", "个股波动过高或趋势证据冲突", metrics)
    if ret60 >= 0.12 and close is not None and close > ma20 and ma20 > ma60 and trend_strength >= 1.2:
        return _regime_result("strong_uptrend", "high", "个股强势上升趋势", metrics)
    if ret60 >= 0.08 and close is not None and close > ma60 and ma20 > ma60 and ma20_slope > 0:
        return _regime_result("uptrend", "medium", "个股中期上升趋势", metrics)
    if abs(ret20) <= 0.05 and 0.06 <= range20 <= 0.20 and abs(ma20_slope) <= 0.02 and abs(close_ma20_distance) <= 0.03:
        return _regime_result("range", "medium", "个股震荡区间", metrics)
    return _regime_result("transition", "low", "个股状态不稳定", metrics)


def classify_regime(history: pd.DataFrame, min_periods: int = 20, mode: str = "stock") -> dict[str, object]:
    if mode == "market":
        return classify_market_regime(history)
    if mode == "stock":
        return classify_stock_regime(history)
    frame = _prepare_history(history)
    if len(frame) < min_periods:
        return _regime_result("insufficient_data", "low", f"数据不足，至少需要{min_periods}条", calculate_regime_metrics(frame))
    return classify_stock_regime(frame)


def evaluate_thermostat(
    histories: dict[str, pd.DataFrame],
    market_history: pd.DataFrame | None,
    candidates: Iterable[dict[str, object]] | None = None,
    holdings: pd.DataFrame | None = None,
    cash: float = 0.0,
    as_of: str | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
) -> ThermostatResult:
    if progress_callback:
        progress_callback({"stage": "classify_market", "completed": 0, "total": 1, "current_symbol": "", "node": "判断市场状态"})
    market_frame = market_history if market_history is not None and not market_history.empty else _aggregate_market_history(histories)
    market = classify_market_regime(market_frame)
    if bool(getattr(market_frame, "attrs", {}).get("defensive_anchor")):
        market["regime"] = "market_downtrend"
        market["evidence"] = f"{market['evidence']}；沪深300、中证1000、创业板指同时下行，进入防守状态"
    market_regime = str(market["regime"])
    date = as_of or str(market.get("regime_date") or "")
    if progress_callback:
        progress_callback({"stage": "classify_market", "completed": 1, "total": 1, "current_symbol": "", "node": "判断市场状态"})

    stock_classifications = {
        symbol: classify_stock_regime(frame)
        for symbol, frame in histories.items()
    }
    pool = _pool_strength(histories, stock_classifications)
    overview = pd.DataFrame(
        [
            {
                "market_regime": market_regime,
                "confidence": market["confidence"],
                "evidence": market["evidence"],
                "regime_date": date,
                "data_source": getattr(market_frame, "attrs", {}).get("data_source", "index_history" if market_history is not None else "candidate_aggregate"),
                "data_sufficient": bool(market["data_sufficient"]),
                "pool_regime": pool["pool_regime"],
                "pool_above_ma20_ratio": pool["pool_above_ma20_ratio"],
                "pool_uptrend_count": pool["pool_uptrend_count"],
                "pool_downtrend_count": pool["pool_downtrend_count"],
                "pool_ret20": pool["pool_ret20"],
                "pool_avg_vol20": pool["pool_avg_vol20"],
            }
        ]
    )

    holding_rows: list[dict[str, object]] = []
    holding_items = _records(holdings)
    for index, item in enumerate(holding_items, start=1):
        symbol = normalize_symbol(str(item.get("symbol", "")))
        if not symbol:
            continue
        stock = stock_classifications.get(symbol) or classify_stock_regime(histories.get(symbol, pd.DataFrame()))
        holding_rows.append(
            _advice_row(
                item=item,
                history=histories.get(symbol, pd.DataFrame()),
                stock=stock,
                market_regime=market_regime,
                date=date or str(stock.get("regime_date") or ""),
                cash=cash,
                is_holding=True,
                pool_regime=str(pool["pool_regime"]),
            )
        )
        if progress_callback:
            progress_callback({"stage": "evaluate_holdings", "completed": index, "total": len(holding_items), "current_symbol": symbol, "node": "评估当前持仓"})

    candidate_rows: list[dict[str, object]] = []
    holding_symbols = {str(row["symbol"]) for row in holding_rows}
    candidate_items = list(candidates or [])
    for index, item in enumerate(candidate_items, start=1):
        symbol = normalize_symbol(str(item.get("symbol", "")))
        if not symbol or symbol in holding_symbols:
            continue
        stock = stock_classifications.get(symbol) or classify_stock_regime(histories.get(symbol, pd.DataFrame()))
        candidate_rows.append(
            _advice_row(
                item={**item, "symbol": symbol},
                history=histories.get(symbol, pd.DataFrame()),
                stock=stock,
                market_regime=market_regime,
                date=date or str(stock.get("regime_date") or ""),
                cash=cash,
                is_holding=False,
                pool_regime=str(pool["pool_regime"]),
            )
        )
        if progress_callback:
            progress_callback({"stage": "evaluate_candidates", "completed": index, "total": len(candidate_items), "current_symbol": symbol, "node": "评估候选股"})

    all_rows = _apply_grid_limits(candidate_rows + holding_rows, market_regime, market)
    candidate_rows = [row for row in all_rows if row["symbol"] not in holding_symbols]
    holding_rows = [row for row in all_rows if row["symbol"] in holding_symbols]
    trigger_plan = _trigger_plan_frame(all_rows)
    return ThermostatResult(
        market_overview=overview,
        errors=pd.DataFrame(columns=["symbol", "error"]),
        trigger_plan=trigger_plan,
        _deprecated_signal_rows=_advice_frame(all_rows),
    )


def run_thermostat_strategy(
    service,
    symbols: list[str],
    start_date: str,
    end_date: str,
    cash: float = 0.0,
    portfolio=None,
    refresh: bool = False,
    market_index: str = "000001.SH",
    progress_callback: Callable[[dict[str, object]], None] | None = None,
) -> ThermostatResult:
    normalized = [normalize_symbol(symbol) for symbol in symbols]
    histories: dict[str, pd.DataFrame] = {}
    errors: list[dict[str, object]] = []
    if progress_callback:
        progress_callback({"stage": "initialize_task", "completed": 0, "total": len(normalized), "current_symbol": "", "node": "初始化任务"})
    for index, symbol in enumerate(normalized, start=1):
        try:
            histories[symbol] = service.get_history(symbol, start_date=start_date, end_date=end_date, refresh=refresh)
        except Exception as exc:  # pragma: no cover - defensive path for live providers
            histories[symbol] = pd.DataFrame()
            errors.append({"symbol": symbol, "error": str(exc)})
        if progress_callback:
            progress_callback({"stage": "load_candidate_history", "completed": index, "total": len(normalized), "current_symbol": symbol, "node": "加载候选股历史"})
    if progress_callback:
        progress_callback({"stage": "load_market_history", "completed": 0, "total": len(DEFAULT_MARKET_BENCHMARKS), "current_symbol": market_index, "node": "加载市场基准"})
    market_history = _load_composite_market_history(service, histories, start_date, end_date)
    if progress_callback:
        progress_callback({"stage": "load_market_history", "completed": 1, "total": 1, "current_symbol": "", "node": "加载市场基准"})
    holdings = getattr(portfolio, "positions", None)
    result = evaluate_thermostat(
        histories=histories,
        market_history=market_history,
        candidates=[{"symbol": symbol, "name": ""} for symbol in normalized],
        holdings=holdings,
        cash=cash,
        as_of=end_date,
        progress_callback=progress_callback,
    )
    if errors:
        result.errors = pd.DataFrame(errors)
    return result


def backtest_thermostat_strategy(
    request: T1ThermostatBacktestRequest,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
) -> T1ThermostatBacktestResult:
    """Run the public full T+1 thermostat backtest contract."""
    from .thermostat_backtest import run_t1_thermostat_backtest

    return run_t1_thermostat_backtest(request, progress_callback=progress_callback)


def legacy_backtest_thermostat_strategy(
    service,
    symbols: list[str],
    start_date: str,
    end_date: str,
    initial_cash: float = 100000.0,
    benchmark_symbol: str = "000001.SH",
) -> LegacyThermostatBacktestResult:
    normalized = [normalize_symbol(symbol) for symbol in symbols]
    histories = {
        symbol: _prepare_history(service.get_history(symbol, start_date=start_date, end_date=end_date))
        for symbol in normalized
    }
    benchmark = _prepare_history(service.get_index_history(benchmark_symbol, start_date, end_date))
    event_prices = _histories_to_event_prices(histories)
    engine = EventBacktestEngine(
        BacktestSettings(
            initial_cash=initial_cash,
            force_final_liquidation=True,
        )
    )

    def signal_provider(context: EventContext) -> list[Signal]:
        if context.time_point != "noon":
            return []
        truncated = _truncate_histories(histories, context.date)
        market = benchmark[benchmark["date"] <= pd.to_datetime(context.date)]
        thermostat = evaluate_thermostat(
            histories=truncated,
            market_history=market,
            candidates=[{"symbol": symbol, "name": ""} for symbol in normalized],
            cash=context.cash,
            as_of=context.date,
        )
        signal_frame = getattr(thermostat, "_deprecated_signal_rows", pd.DataFrame())
        rows = [] if signal_frame is None or signal_frame.empty else signal_frame.to_dict("records")
        signals: list[Signal] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            action = str(row.get("action") or "")
            executable = bool(row.get("executable"))
            shares = int(row.get("suggested_shares") or 0)
            symbol = normalize_symbol(str(row.get("symbol") or ""))
            if not symbol or (symbol, action) in seen:
                continue
            if action in {"buy", "add"} and executable and shares > 0:
                signals.append(
                    Signal(
                        symbol=symbol,
                        side="buy",
                        shares=shares,
                        reason=str(row.get("reason") or ""),
                        strategy_family=str(row.get("strategy_family") or "thermostat"),
                    )
                )
                seen.add((symbol, action))
            elif action == "sell" and executable:
                held = context.positions.get(symbol, {}).get("total_shares", 0)
                if int(held) > 0:
                    signals.append(
                        Signal(
                            symbol=symbol,
                            side="sell",
                            shares=int(held),
                            reason=str(row.get("reason") or ""),
                            strategy_family=str(row.get("strategy_family") or "thermostat"),
                        )
                    )
                    seen.add((symbol, action))
        return signals

    event_result = engine.run(event_prices, signal_provider=signal_provider)
    summary = event_result.summary.copy()
    for column, default in {
        "strategy": "thermostat",
        "total_return": 0.0,
        "annualized_return": 0.0,
        "max_drawdown": 0.0,
        "win_rate": 0.0,
        "profit_loss_ratio": 0.0,
        "average_holding_days": 0.0,
        "trade_count": 0,
        "position_utilization": 0.0,
        "cash_ratio": 1.0,
        "benchmark_return": _series_return(benchmark),
        "backtest_type": "event_driven",
    }.items():
        if column not in summary:
            summary[column] = default
    summary.loc[:, "strategy"] = "thermostat"
    summary.loc[:, "backtest_type"] = "event_driven"
    equity = event_result.daily_portfolio.rename(
        columns={"total_value_end": "total_value", "position_value_end": "position_value"}
    )
    if "total_value" not in equity:
        equity = pd.DataFrame(columns=["date", "total_value", "daily_return", "drawdown", "position_value"])
    elif "date" in equity:
        equity["date"] = pd.to_datetime(equity["date"], errors="coerce")
    regime_performance = _regime_performance(benchmark, equity)
    diagnostics = _diagnostics(regime_performance)
    return LegacyThermostatBacktestResult(
        summary=summary,
        regime_performance=regime_performance,
        diagnostics=diagnostics,
        equity=equity,
        daily_portfolio=event_result.daily_portfolio,
        evaluation_detail=event_result.evaluation_detail,
        trades=event_result.trades,
        positions=event_result.positions,
        symbol_performance=event_result.symbol_performance,
        data_quality=event_result.data_quality,
        parameters=event_result.parameters,
    )


def simplified_backtest_thermostat_strategy(
    service,
    symbols: list[str],
    start_date: str,
    end_date: str,
    initial_cash: float = 100000.0,
    benchmark_symbol: str = "000001.SH",
) -> LegacyThermostatBacktestResult:
    normalized = [normalize_symbol(symbol) for symbol in symbols]
    histories = {
        symbol: _prepare_history(service.get_history(symbol, start_date=start_date, end_date=end_date))
        for symbol in normalized
    }
    benchmark = _prepare_history(service.get_index_history(benchmark_symbol, start_date, end_date))
    equity = _simple_equity(histories, initial_cash)
    summary = _backtest_summary(equity, benchmark, initial_cash)
    regime_performance = _regime_performance(benchmark, equity)
    diagnostics = _diagnostics(regime_performance)
    summary["backtest_type"] = "simplified_backtest"
    return LegacyThermostatBacktestResult(
        summary=summary,
        regime_performance=regime_performance,
        diagnostics=diagnostics,
        equity=equity,
    )


def _histories_to_event_prices(histories: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for symbol, history in histories.items():
        prepared = _prepare_history(history)
        for item in prepared.to_dict("records"):
            date = pd.to_datetime(item["date"]).strftime("%Y-%m-%d")
            open_price = float(item.get("open", item.get("close")) or item.get("close"))
            close_price = float(item["close"])
            noon_price = (open_price + close_price) / 2
            for time_point, price, simulated, warning in [
                ("morning_open", open_price, False, ""),
                ("noon", noon_price, True, "simulated_noon_price"),
                ("afternoon_open", noon_price, True, "simulated_afternoon_open_price"),
                ("close", close_price, False, ""),
            ]:
                rows.append(
                    {
                        "symbol": symbol,
                        "date": date,
                        "time_point": time_point,
                        "price": round(float(price), 4),
                        "limit_status": "normal",
                        "is_suspended": False,
                        "simulated": simulated,
                        "warning": warning,
                    }
                )
    return pd.DataFrame(rows)


def _truncate_histories(histories: dict[str, pd.DataFrame], date: str) -> dict[str, pd.DataFrame]:
    cutoff = pd.to_datetime(date)
    result: dict[str, pd.DataFrame] = {}
    for symbol, history in histories.items():
        prepared = _prepare_history(history)
        result[symbol] = prepared[prepared["date"] <= cutoff].reset_index(drop=True)
    return result


def _advice_row(
    item: dict[str, object],
    history: pd.DataFrame,
    stock: dict[str, object],
    market_regime: str,
    date: str,
    cash: float,
    is_holding: bool,
    pool_regime: str,
) -> dict[str, object]:
    symbol = normalize_symbol(str(item.get("symbol", "")))
    prepared = _prepare_history(history)
    last = _last_close(prepared)
    name = str(item.get("name") or "")
    stock_regime = str(stock["regime"])
    stock_evidence = str(stock["evidence"])
    data_sufficient = bool(stock["data_sufficient"])
    row = {
        "symbol": symbol,
        "code": symbol_code(symbol),
        "name": name,
        "date": date,
        "market_regime": market_regime,
        "stock_regime": stock_regime,
        "strategy": "thermostat",
        "strategy_family": "observe",
        "action": "observe",
        "strength": "normal",
        "score": 0.0,
        "priority": 99,
        "suggested_position_pct": 0.0,
        "suggested_shares": 0,
        "entry_price": last,
        "stop_price": None,
        "target_price": None,
        "reference_price": last,
        "grid_upper": None,
        "grid_lower": None,
        "grid_mid": None,
        "grid_unit_pct": None,
        "grid_max_layers": None,
        "grid_stop_condition": None,
        "reason": stock_evidence,
        "risk_note": "",
        "executable": False,
        "data_sufficient": data_sufficient,
    }
    row.update(_trigger_plan_fields(item, prepared, stock_regime, market_regime, date, is_holding, last))
    if not data_sufficient:
        row.update(
            {
                "action": "wait_confirm",
                "strength": "reduced",
                "risk_note": "数据不足，禁止买入、加仓或开网格",
                "reason": f"{stock_evidence}；数据不足，等待更多数据确认",
            }
        )
        return row
    if stock_regime == "downtrend":
        row.update(
            {
                "strategy_family": "risk_control",
                "action": "sell" if is_holding else "blocked",
                "score": 0.2,
                "priority": 1 if is_holding else 90,
                "stop_price": last,
                "risk_note": "个股下行，非持仓禁买，持仓进入风控",
                "executable": is_holding,
            }
        )
        if int(row.get("today_bought_shares") or 0) > 0 and int(row.get("available_shares") or 0) <= 0:
            row["pending_sell_level"] = "pending_exit"
        return row
    if stock_regime == "range":
        grid = _grid_prices(prepared)
        row.update(
            {
                "strategy_family": "grid",
                "action": "hold" if is_holding else "observe",
                "strength": "reduced" if market_regime in {"market_transition", "market_downtrend"} else "normal",
                "score": _grid_score(stock, grid),
                "priority": 20,
                "grid_upper": grid["upper"],
                "grid_lower": grid["lower"],
                "grid_mid": grid["mid"],
                "grid_unit_pct": 0.08,
                "grid_max_layers": 4,
                "grid_stop_condition": "价格跌破区间下沿或趋势突破区间后停止网格",
                "reason": f"{stock_evidence}；先作为网格候选排序",
                "risk_note": "网格候选需要经过市场状态和评分限制",
            }
        )
        return row
    if stock_regime in {"strong_uptrend", "uptrend"}:
        return _trend_row(row, prepared, stock_regime, stock_evidence, market_regime, cash, last, is_holding, pool_regime)
    row.update(
        {
            "strategy_family": "transition",
            "action": "observe",
            "strength": "reduced",
            "score": 0.3,
            "priority": 50,
            "risk_note": "个股状态不稳定，默认观察",
        }
    )
    return row


def _trend_row(
    row: dict[str, object],
    history: pd.DataFrame,
    stock_regime: str,
    stock_evidence: str,
    market_regime: str,
    cash: float,
    last: float | None,
    is_holding: bool,
    pool_regime: str,
) -> dict[str, object]:
    pct = 0.0
    action = "observe"
    strength = "normal"
    executable = False
    reason_suffix = ""
    if market_regime == "market_downtrend":
        action = "hold" if is_holding else "observe"
        strength = "reduced"
        reason_suffix = "；市场防守状态，不新买不加仓"
    elif market_regime == "market_transition":
        if stock_regime == "strong_uptrend" and not is_holding:
            pct = 0.04
            action = "buy"
            strength = "reduced"
            executable = True
            reason_suffix = "；市场过渡期，仅试探仓"
        else:
            action = "hold" if is_holding else "observe"
            strength = "reduced"
            reason_suffix = "；市场过渡期，普通趋势股观察"
    elif market_regime == "market_range":
        if stock_regime in {"strong_uptrend", "uptrend"}:
            pct = 0.04
            action = "add" if is_holding else "buy"
            strength = "reduced"
            executable = True
            reason_suffix = "；震荡市场，仅试探仓"
    elif market_regime == "market_uptrend":
        pct = 0.11 if stock_regime == "strong_uptrend" else 0.09
        action = "add" if is_holding else "buy"
        executable = True
        reason_suffix = "；上升市场，趋势跟随"
    if pool_regime in {"pool_weak", "pool_chaotic"} and pct > 0:
        pct = min(pct, 0.04)
        strength = "reduced"
        reason_suffix += "；股票池偏弱，降低仓位"
    shares, final_pct, cash_note = _position_from_cash(cash, last, pct)
    stop, target = _stop_target(history, last)
    row.update(
        {
            "strategy_family": "trend_following",
            "action": action,
            "strength": strength,
            "score": 0.9 if stock_regime == "strong_uptrend" else 0.75,
            "priority": 2 if is_holding else (3 if stock_regime == "strong_uptrend" else 6),
            "suggested_position_pct": final_pct,
            "suggested_shares": shares,
            "entry_price": last,
            "stop_price": stop,
            "target_price": target,
            "reason": f"{stock_evidence}{reason_suffix}{cash_note}",
            "risk_note": cash_note.strip("；") if cash_note else "",
            "executable": executable and shares > 0,
        }
    )
    if shares == 0 and pct > 0:
        row["action"] = "observe"
        row["executable"] = False
    return row


def _advice_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=REQUIRED_ADVICE_COLUMNS)
    return pd.DataFrame(rows).reindex(columns=REQUIRED_ADVICE_COLUMNS)


def _trigger_plan_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=TRIGGER_PLAN_OUTPUT_COLUMNS)
    frame = pd.DataFrame(rows)
    if "stock_mode" in frame and "grid_max_layers" in frame:
        range_mask = frame["stock_mode"] == "range"
        frame.loc[range_mask, "grid_max_layers"] = 3
    return frame.reindex(columns=TRIGGER_PLAN_OUTPUT_COLUMNS)


def _trigger_plan_fields(
    item: dict[str, object],
    history: pd.DataFrame,
    stock_regime: str,
    market_regime: str,
    date: str,
    is_holding: bool,
    last: float | None,
) -> dict[str, object]:
    stock_mode = _stock_mode(stock_regime)
    market_bucket = _market_bucket(market_regime)
    discount = MARKET_POSITION_DISCOUNTS[market_bucket]
    available, today_bought, total, split_source = _share_split(item, date, is_holding)
    fields: dict[str, object] = {
        "stock_mode": stock_mode,
        "market_regime_normalized": market_bucket,
        "market_position_discount": discount,
        "boll_upper": None,
        "boll_mid": None,
        "boll_lower": None,
        "atr20": None,
        "volume_ma20": None,
        "trend_buy_trigger": "",
        "trend_reduce_trigger": "",
        "trend_exit_trigger": "",
        "effective_trend_exit_trigger": "",
        "trend_batches": "",
        "grid_buy_levels": "",
        "grid_sell_levels": "",
        "configured_grid_layers": 0,
        "effective_grid_layers": 0,
        "grid_layer_spacing_pct": None,
        "grid_total_max_position_pct": 0.0,
        "target_position_pct": 0.0,
        "max_position_pct": 0.0,
        "available_shares": available,
        "today_bought_shares": today_bought,
        "total_shares": total,
        "share_split_source": split_source,
        "pending_sell_level": "",
        "trigger_status": "not_applicable",
        "filled_status": "not_checked",
        "failed_reason": "",
    }
    if stock_mode == "insufficient_data":
        fields["filled_status"] = "not_applicable"
        return fields
    indicators = _trigger_indicators(history)
    fields.update(
        {
            "boll_upper": indicators["boll_upper"],
            "boll_mid": indicators["boll_mid"],
            "boll_lower": indicators["boll_lower"],
            "atr20": indicators["atr20"],
            "volume_ma20": indicators["volume_ma20"],
        }
    )
    if stock_mode == "trend":
        upper = indicators["boll_upper"]
        mid = indicators["boll_mid"]
        lower = indicators["boll_lower"]
        atr20 = indicators["atr20"]
        close = last or indicators["close"]
        trend_average_cost = _trend_average_cost(item, close, is_holding)
        trend_exit_trigger = _trend_exit_trigger(lower, atr20, trend_average_cost)
        effective_exit_trigger = _effective_trend_exit_trigger(
            trend_exit_trigger,
            item.get("last_effective_exit_trigger"),
        )
        buffer = _trend_breakout_buffer(atr20, close)
        target_pct = round(0.20 * discount, 6)
        fields.update(
            {
                "trend_buy_trigger": _round_price((upper + buffer) if upper is not None and buffer is not None else None),
                "trend_reduce_trigger": mid,
                "trend_exit_trigger": trend_exit_trigger,
                "effective_trend_exit_trigger": effective_exit_trigger,
                "trend_batches": "40%,35%,25%",
                "target_position_pct": target_pct,
                "max_position_pct": target_pct,
                "trigger_status": "planned",
            }
        )
    elif stock_mode == "range":
        grid = _grid_trigger_levels(indicators)
        target_pct = round(0.15 * discount, 6)
        fields.update(
            {
                "grid_buy_levels": _format_levels(grid["buy"]),
                "grid_sell_levels": _format_levels(grid["sell"]),
                "configured_grid_layers": grid["configured_layers"],
                "effective_grid_layers": grid["effective_layers"],
                "grid_layer_spacing_pct": grid["spacing_pct"],
                "grid_total_max_position_pct": 0.40,
                "target_position_pct": target_pct,
                "max_position_pct": target_pct,
                "trigger_status": "planned",
            }
        )
    elif stock_mode == "downtrend":
        fields["pending_sell_level"] = "pending_exit" if today_bought > 0 and available <= 0 else ""
        fields["trigger_status"] = "not_applicable"
    else:
        fields["trigger_status"] = "not_applicable"
    return fields


def _stock_mode(stock_regime: str) -> str:
    if stock_regime in {"strong_uptrend", "uptrend"}:
        return "trend"
    if stock_regime == "range":
        return "range"
    if stock_regime == "downtrend":
        return "downtrend"
    if stock_regime == "insufficient_data":
        return "insufficient_data"
    return "chaotic"


def _market_bucket(market_regime: str) -> str:
    if market_regime == "market_uptrend":
        return "strong"
    if market_regime == "market_range":
        return "normal"
    if market_regime == "market_transition":
        return "weak"
    return "extreme_weak"


def _trigger_indicators(history: pd.DataFrame) -> dict[str, float | None]:
    prepared = _prepare_history(history)
    closes = pd.to_numeric(prepared.get("close"), errors="coerce").dropna()
    volumes = (
        pd.to_numeric(prepared["volume"], errors="coerce").dropna()
        if "volume" in prepared
        else pd.Series(dtype="float64")
    )
    if len(closes) >= 20:
        recent = closes.tail(20)
        mid = float(recent.mean())
        std = float(recent.std(ddof=0))
        upper = mid + 2 * std
        lower = mid - 2 * std
    elif not closes.empty:
        mid = float(closes.mean())
        upper = float(closes.max())
        lower = float(closes.min())
    else:
        mid = upper = lower = None
    return {
        "close": _round_price(float(closes.iloc[-1])) if not closes.empty else None,
        "boll_upper": _round_price(upper),
        "boll_mid": _round_price(mid),
        "boll_lower": _round_price(lower),
        "atr20": _round_price(_atr20(prepared)),
        "volume_ma20": float(volumes.tail(20).mean()) if not volumes.empty else None,
        "vol20": float(closes.pct_change().dropna().tail(20).std()) if len(closes) >= 3 else 0.0,
    }


def _trend_breakout_buffer(atr20: float | None, close: float | None) -> float | None:
    if close is None:
        return None
    if atr20 is None:
        return close * 0.005
    return min(0.2 * atr20, close * 0.005)


def _trend_average_cost(item: dict[str, object], close: float | None, is_holding: bool) -> float | None:
    for field in ("avg_cost", "average_cost", "trend_average_cost"):
        cost = _float_value(item.get(field))
        if cost is not None:
            return cost
    return close if not is_holding else None


def _trend_exit_trigger(
    lower: float | None,
    atr20: float | None,
    trend_average_cost: float | None,
) -> float | None:
    if lower is None:
        return None
    if atr20 is None or trend_average_cost is None:
        return lower
    return _round_price(max(lower, trend_average_cost - 2 * atr20))


def _effective_trend_exit_trigger(new_trigger: float | None, previous_trigger: object) -> float | None:
    previous = _float_value(previous_trigger)
    if new_trigger is None:
        return previous if previous is not None and previous > 0 else None
    if previous is None or previous <= 0:
        return new_trigger
    return _round_price(max(previous, new_trigger))


def _grid_trigger_levels(indicators: dict[str, float | None]) -> dict[str, object]:
    mid = indicators.get("boll_mid")
    upper = indicators.get("boll_upper")
    lower = indicators.get("boll_lower")
    if mid is None or upper is None or lower is None:
        return {
            "buy": [],
            "sell": [],
            "configured_layers": 3,
            "effective_layers": 0,
            "spacing_pct": None,
        }
    unit = _grid_unit_pct(float(indicators.get("vol20") or 0.0))
    buy_levels = [_round_price(float(mid) * (1 - unit * layer)) for layer in range(1, 4)]
    sell_levels = [_round_price(float(mid) * (1 + unit * layer)) for layer in range(1, 4)]
    buy_levels = [max(float(lower), float(level)) for level in buy_levels if level is not None]
    sell_levels = [min(float(upper), float(level)) for level in sell_levels if level is not None]
    buy = sorted({_round_price(level) for level in buy_levels}, reverse=True)
    sell = sorted({_round_price(level) for level in sell_levels})
    return {
        "buy": buy,
        "sell": sell,
        "configured_layers": 3,
        "effective_layers": min(len(buy), len(sell)),
        "spacing_pct": unit,
    }


def _grid_unit_pct(vol20: float) -> float:
    if vol20 <= 0.015:
        return 0.035
    if vol20 <= 0.03:
        return 0.055
    return 0.075


def _format_levels(levels: list[float | None]) -> str:
    return "|".join(f"{float(level):.2f}" for level in levels if level is not None)


def _share_split(item: dict[str, object], date: str, is_holding: bool) -> tuple[int, int, int, str]:
    if not is_holding:
        return 0, 0, 0, "candidate"
    explicit_available = _int_value(item.get("available_shares"))
    explicit_today = _int_value(item.get("today_bought_shares"))
    explicit_total = _int_value(item.get("total_shares"))
    shares = explicit_total or _int_value(item.get("shares")) or _int_value(item.get("quantity"))
    if explicit_available or explicit_today:
        total = explicit_total or explicit_available + explicit_today
        return explicit_available, explicit_today, total, "portfolio_split"
    if shares <= 0:
        return 0, 0, 0, "portfolio"
    trade_date = item.get("execution_date") or item.get("buy_date") or item.get("signal_date")
    if trade_date is not None and _same_date(trade_date, date):
        return 0, shares, shares, "execution_date"
    return shares, 0, shares, "portfolio"


def _int_value(value: object) -> int:
    try:
        if value is None or pd.isna(value):
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _same_date(left: object, right: object) -> bool:
    left_date = pd.to_datetime(left, errors="coerce")
    right_date = pd.to_datetime(right, errors="coerce")
    if pd.isna(left_date) or pd.isna(right_date):
        return False
    return left_date.date() == right_date.date()


def is_one_word_limit_up(row: dict[str, object] | pd.Series, limit_up_price: float | None = None) -> bool:
    values = _row_values(row)
    if limit_up_price is None:
        limit_up_price = _float_value(values.get("limit_up_price"))
    if limit_up_price is None:
        return False
    prices = [_float_value(values.get(column)) for column in ("open", "high", "low", "close")]
    return all(price is not None and abs(price - limit_up_price) < 0.001 for price in prices)


def is_fake_breakout(row: dict[str, object] | pd.Series, indicators: dict[str, object]) -> bool:
    values = _row_values(row)
    open_price = _float_value(values.get("open"))
    close = _float_value(values.get("close"))
    high = _float_value(values.get("high"))
    low = _float_value(values.get("low"))
    boll_upper = _float_value(indicators.get("boll_upper"))
    if boll_upper is None:
        boll_upper = _float_value(indicators.get("upper"))
    volume = _float_value(values.get("volume"))
    volume_ma20 = _float_value(indicators.get("volume_ma20"))
    if any(value is None for value in (open_price, close, high, low, boll_upper, volume, volume_ma20)):
        return False
    if volume_ma20 <= 0:
        return False
    epsilon = 1e-12
    upper_shadow_ratio = (high - max(open_price, close)) / max(high - low, epsilon)
    volume_ratio = volume / volume_ma20
    return bool(
        high > boll_upper
        and close < boll_upper
        and upper_shadow_ratio >= 0.50
        and volume_ratio >= 2.50
    )


def check_plan_with_daily_bar(
    plan: dict[str, object],
    daily_bar: dict[str, object] | pd.Series,
    available_shares: int | None = None,
    today_bought_shares: int | None = None,
    limit_up_price: float | None = None,
    is_limit_down: bool | None = None,
    is_suspended: bool | None = None,
) -> list[dict[str, object]]:
    mode = str(plan.get("stock_mode") or "")
    bar = _row_values(daily_bar)
    if limit_up_price is not None:
        bar["limit_up_price"] = limit_up_price
    if is_limit_down is not None:
        bar["is_limit_down"] = is_limit_down
    if is_suspended is not None:
        bar["is_suspended"] = is_suspended
    available = _int_value(available_shares if available_shares is not None else plan.get("available_shares"))
    today_bought = _int_value(today_bought_shares if today_bought_shares is not None else plan.get("today_bought_shares"))
    if bool(bar.get("is_suspended")):
        return [_execution_event(plan, "risk_control_sell", "failed", available, today_bought, "suspension", "pending_exit")]
    if mode == "trend":
        return _check_trend_plan(plan, bar, available, today_bought)
    if mode == "range":
        return _check_grid_plan(plan, bar, available, today_bought)
    if mode == "downtrend":
        return [_sell_event(plan, "risk_control_sell", available, today_bought, bar, "pending_exit")]
    return []


def _check_trend_plan(
    plan: dict[str, object],
    bar: dict[str, object],
    available: int,
    today_bought: int,
) -> list[dict[str, object]]:
    low = _float_value(bar.get("low"))
    high = _float_value(bar.get("high"))
    exit_trigger = _float_value(plan.get("trend_exit_trigger"))
    reduce_trigger = _float_value(plan.get("trend_reduce_trigger"))
    buy_trigger = _float_value(plan.get("trend_buy_trigger"))
    if low is not None and exit_trigger is not None and low <= exit_trigger:
        return [_sell_event(plan, "trend_exit", available, today_bought, bar, "pending_exit")]
    if low is not None and reduce_trigger is not None and low <= reduce_trigger:
        return [_sell_event(plan, "trend_reduce", available, today_bought, bar, "pending_reduce")]
    if high is not None and buy_trigger is not None and high >= buy_trigger:
        failed = "limit_up_buy_failed" if is_one_word_limit_up(bar, _float_value(bar.get("limit_up_price"))) else ""
        status = "failed" if failed else "filled"
        return [_execution_event(plan, "trend_buy", status, available, today_bought, failed, "")]
    return []


def _check_grid_plan(
    plan: dict[str, object],
    bar: dict[str, object],
    available: int,
    today_bought: int,
) -> list[dict[str, object]]:
    low = _float_value(bar.get("low"))
    high = _float_value(bar.get("high"))
    buy_levels = _parse_levels(plan.get("grid_buy_levels"))
    sell_levels = _parse_levels(plan.get("grid_sell_levels"))
    if low is not None and buy_levels and low <= min(buy_levels):
        return [_execution_event(plan, "grid_buy", "filled", available, today_bought, "", "")]
    if high is not None and sell_levels and high >= max(sell_levels):
        return [_sell_event(plan, "grid_sell", available, today_bought, bar, "pending_reduce")]
    return []


def _sell_event(
    plan: dict[str, object],
    trigger_type: str,
    available: int,
    today_bought: int,
    bar: dict[str, object],
    pending_level: str,
) -> dict[str, object]:
    if bool(bar.get("is_limit_down")):
        return _execution_event(plan, trigger_type, "failed", available, today_bought, "limit_down_sell_failed", pending_level)
    if available > 0:
        return _execution_event(plan, trigger_type, "filled", available, today_bought, "", "")
    if today_bought > 0:
        return _execution_event(plan, trigger_type, "pending", available, today_bought, "", pending_level)
    return _execution_event(plan, trigger_type, "failed", available, today_bought, "no_available_shares", "")


def _execution_event(
    plan: dict[str, object],
    trigger_type: str,
    filled_status: str,
    available: int,
    today_bought: int,
    failed_reason: str,
    pending_level: str,
) -> dict[str, object]:
    return {
        "symbol": plan.get("symbol"),
        "date": plan.get("date"),
        "stock_mode": plan.get("stock_mode"),
        "trigger_type": trigger_type,
        "trigger_status": "triggered",
        "filled_status": filled_status,
        "failed_reason": failed_reason,
        "pending_sell_level": pending_level if filled_status in {"pending", "failed"} else "",
        "available_shares": available,
        "today_bought_shares": today_bought,
    }


def _parse_levels(value: object) -> list[float]:
    if value is None or pd.isna(value):
        return []
    levels = []
    for part in str(value).split("|"):
        price = _float_value(part)
        if price is not None:
            levels.append(price)
    return levels


def _row_values(row: dict[str, object] | pd.Series) -> dict[str, object]:
    if isinstance(row, pd.Series):
        return row.to_dict()
    return dict(row)


def _float_value(value: object) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _records(frame: pd.DataFrame | None) -> list[dict[str, object]]:
    if frame is None or frame.empty:
        return []
    return frame.to_dict("records")


def _prepare_history(history: pd.DataFrame | None) -> pd.DataFrame:
    if history is None or history.empty:
        return pd.DataFrame(columns=["date", "close"])
    data = history.copy()
    if "date" in data:
        data["date"] = pd.to_datetime(data["date"], errors="coerce")
    else:
        data["date"] = pd.RangeIndex(start=0, stop=len(data), step=1)
    data["close"] = pd.to_numeric(data.get("close"), errors="coerce")
    if "high" in data:
        data["high"] = pd.to_numeric(data.get("high"), errors="coerce")
    if "low" in data:
        data["low"] = pd.to_numeric(data.get("low"), errors="coerce")
    return data.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)


def _aggregate_market_history(histories: dict[str, pd.DataFrame]) -> pd.DataFrame:
    frames = []
    for frame in histories.values():
        prepared = _prepare_history(frame)
        if not prepared.empty:
            frames.append(prepared[["date", "close"]].rename(columns={"close": f"close_{len(frames)}"}))
    if not frames:
        return pd.DataFrame(columns=["date", "close"])
    merged = frames[0]
    for frame in frames[1:]:
        merged = merged.merge(frame, on="date", how="inner")
    close_cols = [column for column in merged.columns if column.startswith("close_")]
    merged["close"] = merged[close_cols].mean(axis=1)
    result = merged[["date", "close"]]
    result.attrs["data_source"] = "candidate_aggregate"
    return result


def _load_composite_market_history(service, histories: dict[str, pd.DataFrame], start_date: str, end_date: str) -> pd.DataFrame:
    loaded: list[tuple[pd.DataFrame, float, str]] = []
    index_states: dict[str, str] = {}
    for code, weight, name in DEFAULT_MARKET_BENCHMARKS:
        frame = _load_index_history(service, code, start_date, end_date)
        if not frame.empty:
            loaded.append((frame, weight, name))
            index_states[code] = str(classify_market_regime(frame)["regime"])
    anchor = _load_index_history(service, RISK_ANCHOR_INDEX, start_date, end_date)
    if not anchor.empty:
        index_states[RISK_ANCHOR_INDEX] = str(classify_market_regime(anchor)["regime"])
    if not loaded:
        fallback = _aggregate_market_history(histories)
        fallback.attrs["data_source"] = "candidate_aggregate"
        return fallback
    total_weight = sum(weight for _, weight, _ in loaded)
    merged = None
    weighted_cols = []
    for index, (frame, weight, _) in enumerate(loaded):
        prepared = _prepare_history(frame)[["date", "close"]].copy()
        if prepared.empty:
            continue
        first = float(prepared.iloc[0]["close"])
        prepared[f"weighted_{index}"] = prepared["close"] / first * (weight / total_weight) if first else 0.0
        prepared = prepared[["date", f"weighted_{index}"]]
        weighted_cols.append(f"weighted_{index}")
        merged = prepared if merged is None else merged.merge(prepared, on="date", how="inner")
    if merged is None or merged.empty:
        fallback = _aggregate_market_history(histories)
        fallback.attrs["data_source"] = "candidate_aggregate"
        return fallback
    merged["close"] = merged[weighted_cols].sum(axis=1) * 1000
    result = merged[["date", "close"]]
    result.attrs["data_source"] = "composite_index"
    result.attrs["defensive_anchor"] = all(index_states.get(code) == "market_downtrend" for code in RISK_ANCHOR_COMPONENTS)
    return result


def _load_index_history(service, index_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    try:
        return _prepare_history(service.get_index_history(index_code, start_date=start_date, end_date=end_date))
    except Exception:
        return pd.DataFrame()


def _pool_strength(histories: dict[str, pd.DataFrame], classifications: dict[str, dict[str, object]]) -> dict[str, object]:
    metrics = [calculate_regime_metrics(frame) for frame in histories.values()]
    usable = [metric for metric in metrics if metric["data_sufficient"] and metric["ma20"] and metric["close"] is not None]
    if not usable:
        return {
            "pool_regime": "pool_neutral",
            "pool_above_ma20_ratio": 0.0,
            "pool_uptrend_count": 0,
            "pool_downtrend_count": 0,
            "pool_ret20": 0.0,
            "pool_avg_vol20": 0.0,
        }
    above_ratio = sum(1 for metric in usable if float(metric["close"]) > float(metric["ma20"])) / len(usable)
    ret20_values = [float(metric["ret20"]) for metric in usable]
    avg_vol20 = sum(float(metric["vol20"]) for metric in usable) / len(usable)
    ret20_std = pd.Series(ret20_values).std() if len(ret20_values) > 1 else 0.0
    regimes = [str(value["regime"]) for value in classifications.values()]
    if ret20_std >= 0.08 and avg_vol20 >= 0.04:
        pool_regime = "pool_chaotic"
    elif above_ratio >= 0.60:
        pool_regime = "pool_strong"
    elif above_ratio >= 0.40:
        pool_regime = "pool_neutral"
    else:
        pool_regime = "pool_weak"
    return {
        "pool_regime": pool_regime,
        "pool_above_ma20_ratio": above_ratio,
        "pool_uptrend_count": sum(1 for regime in regimes if regime in {"strong_uptrend", "uptrend"}),
        "pool_downtrend_count": sum(1 for regime in regimes if regime == "downtrend"),
        "pool_ret20": sum(ret20_values) / len(ret20_values),
        "pool_avg_vol20": avg_vol20,
    }


def _apply_grid_limits(rows: list[dict[str, object]], market_regime: str, market: dict[str, object]) -> list[dict[str, object]]:
    grid_rows = [row for row in rows if row["strategy_family"] == "grid"]
    if not grid_rows:
        return rows
    if market_regime in {"market_downtrend", "market_transition", "insufficient_data"}:
        limit = 0
    elif market_regime == "market_range":
        stable = float(market.get("range60", 1.0)) <= 0.10 and float(market.get("vol20", 1.0)) <= 0.015 and abs(float(market.get("ma60_slope", 1.0))) <= 0.01
        limit = 3 if stable else 2
    elif market_regime == "market_uptrend":
        has_trend = any(row["strategy_family"] == "trend_following" and row["executable"] for row in rows)
        limit = 1 if has_trend else 2
    else:
        limit = 0
    sorted_grid = sorted(grid_rows, key=lambda row: float(row.get("score") or 0), reverse=True)
    enabled_symbols = {row["symbol"] for row in sorted_grid[:limit]}
    for row in grid_rows:
        if row["symbol"] in enabled_symbols:
            row["action"] = "hold" if row["action"] == "hold" else "buy"
            row["executable"] = True
            row["reason"] = f"{row['reason']}；网格评分进入本轮启用名单"
        else:
            row["action"] = "observe"
            row["executable"] = False
            row["reason"] = f"{row['reason']}；符合震荡条件，但网格优先级不足，未进入本轮启用名单。"
            row["suggested_position_pct"] = 0.0
            row["suggested_shares"] = 0
    return rows


def _grid_score(stock: dict[str, object], grid: dict[str, float | None]) -> float:
    ret20 = abs(float(stock.get("ret20", 0.0)))
    ma20_slope = abs(float(stock.get("ma20_slope", 0.0)))
    range20 = float(stock.get("range20", 0.0))
    vol20 = float(stock.get("vol20", 0.0))
    close = stock.get("close")
    mid = grid.get("mid")
    upper = grid.get("upper")
    stability = max(0.0, 1 - ret20 / 0.05) * 0.15 + max(0.0, 1 - ma20_slope / 0.02) * 0.15
    width = max(0.0, 1 - abs(range20 - 0.13) / 0.10) * 0.20
    volatility = max(0.0, 1 - abs(vol20 - 0.03) / 0.04) * 0.20
    position = 0.0
    if close is not None and mid and upper:
        distance_mid = abs(float(close) / mid - 1)
        near_upper = float(close) >= mid + (upper - mid) * 0.75
        position = (max(0.0, 1 - distance_mid / 0.03) * 0.15) if not near_upper else 0.02
    return round(stability + width + volatility + position, 6)


def _position_from_cash(cash: float, price: float | None, pct: float) -> tuple[int, float, str]:
    if pct <= 0 or not price or price <= 0 or cash <= 0:
        return 0, 0.0, ""
    shares = int((cash * pct / price) // 100) * 100
    if shares <= 0:
        return 0, 0.0, "；现金不足以买入一手"
    return shares, pct, ""


def _stop_target(history: pd.DataFrame, close: float | None) -> tuple[float | None, float | None]:
    if close is None:
        return None, None
    prepared = _prepare_history(history)
    atr20 = _atr20(prepared)
    if atr20:
        stop_pct = min(max(2 * atr20 / close, 0.06), 0.12)
        target_pct = 2 * stop_pct
        return _round_price(close * (1 - stop_pct)), _round_price(close * (1 + target_pct))
    return _round_price(close * 0.92), _round_price(close * 1.18)


def _atr20(history: pd.DataFrame) -> float | None:
    if history.empty or "high" not in history or "low" not in history:
        return None
    data = history.copy()
    high = pd.to_numeric(data["high"], errors="coerce")
    low = pd.to_numeric(data["low"], errors="coerce")
    close = pd.to_numeric(data["close"], errors="coerce")
    prev_close = close.shift(1)
    true_range = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    values = true_range.dropna().tail(20)
    return float(values.mean()) if len(values) >= 5 else None


def _regime_result(regime: str, confidence: str, label: str, metrics: dict[str, object]) -> dict[str, object]:
    evidence = (
        f"ret20={float(metrics.get('ret20') or 0):.2%}，"
        f"ret60={float(metrics.get('ret60') or 0):.2%}，"
        f"ma20={float(metrics.get('ma20') or 0):.2f}，"
        f"ma60={float(metrics.get('ma60') or 0):.2f}，"
        f"range20={float(metrics.get('range20') or 0):.2%}，"
        f"vol20={float(metrics.get('vol20') or 0):.2%}；{label}"
    )
    return {
        "regime": regime,
        "confidence": confidence,
        "data_sufficient": bool(metrics.get("data_sufficient")),
        "regime_date": str(metrics.get("regime_date") or ""),
        "evidence": evidence,
        **metrics,
    }


def _tail_mean(closes: pd.Series, window: int) -> float:
    values = closes.tail(window)
    return float(values.mean()) if not values.empty else 0.0


def _tail_return(closes: pd.Series, window: int) -> float:
    if len(closes) < 2:
        return 0.0
    values = closes.tail(min(window, len(closes)))
    first = float(values.iloc[0])
    last = float(values.iloc[-1])
    return last / first - 1 if first else 0.0


def _tail_range(closes: pd.Series, window: int) -> float:
    values = closes.tail(min(window, len(closes)))
    if values.empty:
        return 0.0
    mean = float(values.mean())
    return (float(values.max()) - float(values.min())) / mean if mean else 0.0


def _ma_slope(closes: pd.Series, window: int, lag: int) -> float:
    if len(closes) < window + lag:
        return 0.0
    rolling = closes.rolling(window).mean().dropna()
    if len(rolling) <= lag:
        return 0.0
    current = float(rolling.iloc[-1])
    previous = float(rolling.iloc[-1 - lag])
    return current / previous - 1 if previous else 0.0


def _last_date(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty or "date" not in frame:
        return ""
    value = frame.iloc[-1]["date"]
    if pd.isna(value):
        return ""
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def _last_close(frame: pd.DataFrame) -> float | None:
    if frame is None or frame.empty:
        return None
    value = frame.iloc[-1]["close"]
    return _round_price(float(value)) if pd.notna(value) else None


def _grid_prices(history: pd.DataFrame) -> dict[str, float | None]:
    prepared = _prepare_history(history).tail(20)
    if prepared.empty:
        return {"upper": None, "lower": None, "mid": None}
    upper = float(prepared["close"].max())
    lower = float(prepared["close"].min())
    return {"upper": _round_price(upper), "lower": _round_price(lower), "mid": _round_price((upper + lower) / 2)}


def _round_price(value: float | None) -> float | None:
    return round(float(value), 2) if value is not None else None


def _simple_equity(histories: dict[str, pd.DataFrame], initial_cash: float) -> pd.DataFrame:
    if not histories:
        return pd.DataFrame(columns=["date", "total_value", "daily_return", "drawdown", "position_value"])
    aligned = None
    for symbol, frame in histories.items():
        data = _prepare_history(frame)[["date", "close"]].rename(columns={"close": symbol})
        aligned = data if aligned is None else aligned.merge(data, on="date", how="inner")
    if aligned is None or aligned.empty:
        return pd.DataFrame(columns=["date", "total_value", "daily_return", "drawdown", "position_value"])
    price_cols = [column for column in aligned.columns if column != "date"]
    normalized = aligned[price_cols].div(aligned[price_cols].iloc[0]).mean(axis=1)
    equity = pd.DataFrame({"date": aligned["date"], "total_value": initial_cash * normalized})
    equity["daily_return"] = equity["total_value"].pct_change().fillna(0.0)
    peak = equity["total_value"].cummax()
    equity["drawdown"] = equity["total_value"] / peak - 1
    equity["position_value"] = equity["total_value"] * 0.8
    return equity


def _backtest_summary(equity: pd.DataFrame, benchmark: pd.DataFrame, initial_cash: float) -> pd.DataFrame:
    if equity.empty:
        return pd.DataFrame(
            [
                {
                    "strategy": "thermostat",
                    "total_return": 0.0,
                    "annualized_return": 0.0,
                    "max_drawdown": 0.0,
                    "win_rate": 0.0,
                    "profit_loss_ratio": 0.0,
                    "average_holding_days": 0.0,
                    "trade_count": 0,
                    "position_utilization": 0.0,
                    "cash_ratio": 1.0,
                    "benchmark_return": 0.0,
                }
            ]
        )
    total_return = float(equity.iloc[-1]["total_value"]) / initial_cash - 1 if initial_cash else 0.0
    days = max(len(equity), 1)
    annualized = (1 + total_return) ** (252 / days) - 1 if total_return > -1 else -1.0
    positive = equity["daily_return"][equity["daily_return"] > 0]
    negative = equity["daily_return"][equity["daily_return"] < 0]
    benchmark_return = _series_return(benchmark)
    return pd.DataFrame(
        [
            {
                "strategy": "thermostat",
                "total_return": total_return,
                "annualized_return": annualized,
                "max_drawdown": float(equity["drawdown"].min()),
                "win_rate": len(positive) / max(len(positive) + len(negative), 1),
                "profit_loss_ratio": abs(float(positive.mean()) / float(negative.mean())) if len(positive) and len(negative) and float(negative.mean()) else 0.0,
                "average_holding_days": days,
                "trade_count": max(int(days // 20), 0),
                "position_utilization": float((equity["position_value"] > 0).mean()),
                "cash_ratio": 0.2,
                "benchmark_return": benchmark_return,
            }
        ]
    )


def _regime_performance(benchmark: pd.DataFrame, equity: pd.DataFrame) -> pd.DataFrame:
    if benchmark.empty or equity.empty:
        return pd.DataFrame(columns=["market_regime", "return", "max_drawdown", "period_count"])
    bench = _prepare_history(benchmark)
    rows = []
    previous_regime = None
    for start in range(0, len(bench), 20):
        window = bench.iloc[start : start + 60]
        if len(window) < 20:
            continue
        regime = str(classify_market_regime(window)["regime"])
        rows.append(
            {
                "market_regime": regime,
                "return": _window_equity_return(equity, window["date"].min(), window["date"].max()),
                "max_drawdown": _window_drawdown(equity, window["date"].min(), window["date"].max()),
                "period_count": 1,
                "switched": previous_regime is not None and previous_regime != regime,
            }
        )
        previous_regime = regime
    if not rows:
        return pd.DataFrame(columns=["market_regime", "return", "max_drawdown", "period_count"])
    data = pd.DataFrame(rows)
    return data.groupby("market_regime", as_index=False).agg(
        return_=("return", "mean"),
        max_drawdown=("max_drawdown", "min"),
        period_count=("period_count", "sum"),
        switch_count=("switched", "sum"),
    ).rename(columns={"return_": "return"})


def _diagnostics(regime_performance: pd.DataFrame) -> pd.DataFrame:
    if regime_performance.empty:
        switches = 0
        average_after_switch = 0.0
    else:
        switches = int(pd.to_numeric(regime_performance.get("switch_count"), errors="coerce").fillna(0).sum())
        average_after_switch = float(pd.to_numeric(regime_performance.get("return"), errors="coerce").fillna(0).mean())
    return pd.DataFrame(
        [
            {
                "regime_switch_count": switches,
                "average_after_switch_return": average_after_switch,
                "grid_invalid_count": 0,
                "trend_stop_count": 0,
            }
        ]
    )


def _series_return(frame: pd.DataFrame) -> float:
    data = _prepare_history(frame)
    if len(data) < 2:
        return 0.0
    start = float(data.iloc[0]["close"])
    end = float(data.iloc[-1]["close"])
    return end / start - 1 if start else 0.0


def _window_equity_return(equity: pd.DataFrame, start, end) -> float:
    window = equity[(equity["date"] >= start) & (equity["date"] <= end)]
    if len(window) < 2:
        return 0.0
    first = float(window.iloc[0]["total_value"])
    last = float(window.iloc[-1]["total_value"])
    return last / first - 1 if first else 0.0


def _window_drawdown(equity: pd.DataFrame, start, end) -> float:
    window = equity[(equity["date"] >= start) & (equity["date"] <= end)]
    if window.empty:
        return 0.0
    return float(window["drawdown"].min())
