from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Callable, Iterable

import pandas as pd

from stock_picker.data.models import normalize_symbol, symbol_code


REQUIRED_ADVICE_COLUMNS = [
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


@dataclass
class ThermostatResult:
    market_overview: pd.DataFrame
    holding_advice: pd.DataFrame
    new_candidates: pd.DataFrame
    grid_advice: pd.DataFrame
    trend_advice: pd.DataFrame
    errors: pd.DataFrame

    @property
    def tables(self) -> dict[str, pd.DataFrame]:
        return {
            "market_overview": self.market_overview,
            "holding_advice": self.holding_advice,
            "new_candidates": self.new_candidates,
            "grid_advice": self.grid_advice,
            "trend_advice": self.trend_advice,
            "errors": self.errors,
        }


@dataclass
class ThermostatBacktestResult:
    summary: pd.DataFrame
    regime_performance: pd.DataFrame
    diagnostics: pd.DataFrame
    equity: pd.DataFrame


def classify_regime(history: pd.DataFrame, min_periods: int = 20) -> dict[str, object]:
    frame = _prepare_history(history)
    if len(frame) < min_periods:
        return {
            "regime": "insufficient_data",
            "confidence": "low",
            "data_sufficient": False,
            "regime_date": _last_date(frame),
            "evidence": f"历史数据不足，至少需要{min_periods}条",
        }

    recent = frame.tail(min_periods).copy()
    closes = pd.to_numeric(recent["close"], errors="coerce").dropna()
    if len(closes) < min_periods:
        return {
            "regime": "insufficient_data",
            "confidence": "low",
            "data_sufficient": False,
            "regime_date": _last_date(frame),
            "evidence": "收盘价数据不足",
        }

    first = float(closes.iloc[0])
    last = float(closes.iloc[-1])
    ret20 = last / first - 1 if first else 0.0
    ma20 = float(closes.mean())
    daily = closes.pct_change().dropna()
    volatility = float(daily.std()) if not daily.empty else 0.0
    range_pct = (float(closes.max()) - float(closes.min())) / ma20 if ma20 else 0.0

    if volatility > 0.07 and range_pct > 0.25:
        regime = "transition"
        confidence = "low"
        label = "方向和波动证据冲突"
    elif abs(ret20) <= 0.04 and range_pct <= 0.18 and (volatility >= 0.01 or range_pct <= 0.01):
        regime = "range"
        confidence = "medium"
        label = "区间波动占主导"
    elif ret20 >= 0.03 and last >= ma20:
        regime = "uptrend"
        confidence = "medium"
        label = "价格位于20日均线上方"
    elif ret20 <= -0.03 and last <= ma20:
        regime = "downtrend"
        confidence = "medium"
        label = "价格位于20日均线下方"
    else:
        regime = "transition"
        confidence = "low"
        label = "趋势证据不稳定"

    return {
        "regime": regime,
        "confidence": confidence,
        "data_sufficient": True,
        "regime_date": _last_date(frame),
        "evidence": f"20日收益{ret20:.2%}，20日均线{ma20:.2f}，波动率{volatility:.2%}，区间宽度{range_pct:.2%}；{label}",
        "ret20": ret20,
        "ma20": ma20,
        "volatility": volatility,
        "range_pct": range_pct,
        "last_close": last,
    }


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
    market = classify_regime(market_history if market_history is not None else _aggregate_market_history(histories))
    market_regime = str(market["regime"])
    date = as_of or str(market.get("regime_date") or "")
    if progress_callback:
        progress_callback({"stage": "classify_market", "completed": 1, "total": 1, "current_symbol": "", "node": "判断市场状态"})
    overview = pd.DataFrame(
        [
            {
                "market_regime": market_regime,
                "confidence": market["confidence"],
                "evidence": market["evidence"],
                "regime_date": date,
                "data_source": "index_history" if market_history is not None else "candidate_aggregate",
                "data_sufficient": bool(market["data_sufficient"]),
            }
        ]
    )

    holding_rows: list[dict[str, object]] = []
    holding_items = _records(holdings)
    for index, item in enumerate(holding_items, start=1):
        symbol = normalize_symbol(str(item.get("symbol", "")))
        if not symbol:
            continue
        stock = classify_regime(histories.get(symbol, pd.DataFrame()))
        holding_rows.append(
            _advice_row(
                item=item,
                history=histories.get(symbol, pd.DataFrame()),
                stock_regime=str(stock["regime"]),
                market_regime=market_regime,
                date=date or str(stock.get("regime_date") or ""),
                cash=cash,
                is_holding=True,
                stock_evidence=str(stock["evidence"]),
                data_sufficient=bool(stock["data_sufficient"]),
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
        stock = classify_regime(histories.get(symbol, pd.DataFrame()))
        row = _advice_row(
            item={**item, "symbol": symbol},
            history=histories.get(symbol, pd.DataFrame()),
            stock_regime=str(stock["regime"]),
            market_regime=market_regime,
            date=date or str(stock.get("regime_date") or ""),
            cash=cash,
            is_holding=False,
            stock_evidence=str(stock["evidence"]),
            data_sufficient=bool(stock["data_sufficient"]),
        )
        if row["action"] != "blocked":
            candidate_rows.append(row)
        if progress_callback:
            progress_callback({"stage": "evaluate_candidates", "completed": index, "total": len(candidate_items), "current_symbol": symbol, "node": "评估候选股"})

    holding_advice = _advice_frame(holding_rows)
    new_candidates = _advice_frame([row for row in candidate_rows if row["action"] in {"buy", "observe", "wait_confirm"}])
    grid_advice = _advice_frame([row for row in candidate_rows + holding_rows if row["strategy_family"] == "grid"])
    trend_advice = _advice_frame([row for row in candidate_rows + holding_rows if row["strategy_family"] == "trend_following"])
    return ThermostatResult(
        market_overview=overview,
        holding_advice=holding_advice,
        new_candidates=new_candidates,
        grid_advice=grid_advice,
        trend_advice=trend_advice,
        errors=pd.DataFrame(columns=["symbol", "error"]),
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
        progress_callback({"stage": "load_market_history", "completed": 0, "total": 1, "current_symbol": market_index, "node": "加载市场基准"})
    market_history = _load_market_history(service, market_index, start_date, end_date)
    if progress_callback:
        progress_callback({"stage": "load_market_history", "completed": 1, "total": 1, "current_symbol": market_index, "node": "加载市场基准"})
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
    service,
    symbols: list[str],
    start_date: str,
    end_date: str,
    initial_cash: float = 100000.0,
    benchmark_symbol: str = "000001.SH",
) -> ThermostatBacktestResult:
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
    return ThermostatBacktestResult(
        summary=summary,
        regime_performance=regime_performance,
        diagnostics=diagnostics,
        equity=equity,
    )


def _advice_row(
    item: dict[str, object],
    history: pd.DataFrame,
    stock_regime: str,
    market_regime: str,
    date: str,
    cash: float,
    is_holding: bool,
    stock_evidence: str,
    data_sufficient: bool,
) -> dict[str, object]:
    symbol = normalize_symbol(str(item.get("symbol", "")))
    prepared = _prepare_history(history)
    last = _last_close(prepared)
    name = str(item.get("name") or "")
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
        "data_sufficient": bool(data_sufficient),
    }
    if not data_sufficient:
        row.update(
            {
                "action": "wait_confirm",
                "strength": "reduced",
                "risk_note": "数据不足，禁止强买入",
                "reason": f"{stock_evidence}；等待更多数据确认",
            }
        )
        return row

    if stock_regime == "downtrend":
        row.update(
            {
                "strategy_family": "risk_control",
                "action": "sell" if is_holding else "blocked",
                "strength": "normal",
                "score": 0.2,
                "priority": 1 if is_holding else 90,
                "stop_price": last,
                "reference_price": last,
                "risk_note": "趋势下行，默认不新增多仓",
            }
        )
        return row

    if stock_regime == "range":
        grid = _grid_prices(prepared)
        row.update(
            {
                "strategy_family": "grid",
                "action": "hold" if is_holding else "observe",
                "score": 0.55,
                "priority": 2 if is_holding else 20,
                "suggested_position_pct": 0.08,
                "suggested_shares": _suggested_shares(cash, last, 0.08),
                "grid_upper": grid["upper"],
                "grid_lower": grid["lower"],
                "grid_mid": grid["mid"],
                "grid_unit_pct": 0.08,
                "grid_max_layers": 4,
                "grid_stop_condition": "价格有效跌破区间下沿或向上突破区间上沿后停止网格",
                "risk_note": "震荡策略不得无限补仓",
            }
        )
        return row

    if stock_regime == "uptrend":
        conflict = market_regime == "downtrend"
        row.update(
            {
                "strategy_family": "trend_following",
                "action": "observe" if conflict else ("add" if is_holding else "buy"),
                "strength": "reduced" if conflict else "normal",
                "score": 0.62 if conflict else 0.82,
                "priority": 10 if conflict else (2 if is_holding else 5),
                "suggested_position_pct": 0.05 if conflict else 0.12,
                "suggested_shares": 0 if conflict else _suggested_shares(cash, last, 0.12),
                "entry_price": last,
                "stop_price": _round_price(last * 0.92),
                "target_price": _round_price(last * 1.18),
                "risk_note": "逆市场风险，降低建议强度" if conflict else "趋势恶化或跌破止损价时退出",
                "executable": not conflict,
            }
        )
        return row

    row.update(
        {
            "strategy_family": "transition",
            "action": "observe",
            "strength": "reduced",
            "score": 0.3,
            "priority": 50,
            "risk_note": "状态不稳定，等待确认",
        }
    )
    return row


def _advice_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=REQUIRED_ADVICE_COLUMNS)
    return pd.DataFrame(rows).reindex(columns=REQUIRED_ADVICE_COLUMNS)


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
    data["close"] = pd.to_numeric(data.get("close"), errors="coerce")
    return data.dropna(subset=["close"]).sort_values("date" if "date" in data else "close").reset_index(drop=True)


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
    return merged[["date", "close"]]


def _load_market_history(service, market_index: str, start_date: str, end_date: str) -> pd.DataFrame:
    try:
        return service.get_index_history(market_index, start_date=start_date, end_date=end_date)
    except Exception:
        return pd.DataFrame()


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


def _suggested_shares(cash: float, price: float | None, pct: float) -> int:
    if not price or price <= 0 or cash <= 0:
        return 0
    return max(int((cash * pct / price) // 100) * 100, 0)


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
        window = bench.iloc[start : start + 20]
        if len(window) < 5:
            continue
        regime = str(classify_regime(window, min_periods=min(20, len(window)))["regime"])
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
