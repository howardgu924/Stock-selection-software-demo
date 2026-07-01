from __future__ import annotations

import argparse
import html
import json
import math
import sys
import threading
import uuid
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stock_picker.data import DataSourceConfig, MarketDataService
from stock_picker.data.models import StockInfo, is_supported_stock_symbol, normalize_symbol, symbol_code
from stock_picker.execution import build_execution_plan
from stock_picker.pools import (
    lhb_range_dates,
    parse_manual_pool,
    resolve_lhb_pool,
    resolve_market_range_pool,
    resolve_watchlist_pool,
)
from stock_picker.strategies import (
    backtest_thermostat_strategy,
    run_thermostat_strategy,
    TurtleConfig,
    TurtleSystemResult,
    backtest_turtle_system,
    run_turtle_system,
)
from stock_picker.strategies.turtle_system import (
    TURTLE_EQUITY_COLUMNS,
    TURTLE_POSITION_COLUMNS,
    TURTLE_SIGNAL_COLUMNS,
    TURTLE_SUMMARY_COLUMNS,
    TURTLE_TRADE_COLUMNS,
)
from stock_picker.user import ManualPortfolioStore, WatchlistStore
from examples.list_lhb_candidates import build_lhb_candidates


DEFAULT_PORT = 8765
DEFAULT_USER_PATH = "data/user/default"
LAST_FORM: dict[str, str] = {}
JOBS: dict[str, "ThermostatJob"] = {}
JOBS_LOCK = threading.Lock()
PAGES = {"thermostat", "backtest", "portfolio"}
APP_NAME = "选股工作台"

TITLE_LABELS = {
    "Strategy": "策略运行",
    "Results": "策略结果",
    "Signals": "信号",
    "Errors": "错误",
    "Full Turtle System": "完整海龟系统",
    "Final Pool": "最终股票池",
    "LHB Ranking": "龙虎榜排名",
    "Holding Advice": "持仓建议",
    "New Buy Signals": "新买入信号",
    "Execution Plan": "手工执行计划",
    "Candidate Evaluation": "候选评估",
    "Turtle Backtest": "海龟回测",
    "Summary": "摘要",
    "Market Overview": "市场概览",
    "New Buy Candidates": "新买候选",
    "Grid Advice": "网格建议",
    "Trend Advice": "趋势建议",
    "LHB Top 20": "龙虎榜前 20 名",
    "LHB Top 30": "龙虎榜前 30 名",
    "LHB Top 50": "龙虎榜前 50 名",
    "Regime Performance": "市场状态表现",
    "Diagnostics": "诊断明细",
    "LHB Candidate Preview": "龙虎榜候选预览",
    "Thermostat Job Started": "恒温器任务已开始",
    "Trades": "交易流水",
    "Equity": "每日权益",
    "Drawdowns": "回撤明细",
    "Symbol PnL": "标的盈亏",
    "Benchmark": "基准对比",
    "Yearly Returns": "年度收益",
    "Monthly Returns": "月度收益",
    "Monthly Return Matrix": "月度盈亏表",
    "Trade Quality": "交易质量",
    "Holding Distribution": "持仓天数分布",
    "Backtest Pool": "回测股票池",
    "Backtest Candidate Difference": "股票池成交差异",
    "Portfolio Initialized": "账户已初始化",
    "Buy Recorded": "买入已记录",
    "Sell Recorded": "卖出已记录",
    "Portfolio Summary": "账户概览",
    "Positions": "当前持仓",
    "Stock Pool Summary": "股票池摘要",
    "Watchlists": "自选股组合",
}

PROGRESS_STAGE_LABELS = {
    "queued": "排队",
    "initialize_task": "正在初始化任务",
    "load_market_history": "正在加载市场历史",
    "load_candidate_history": "正在加载候选股历史",
    "classify_market": "正在生成市场状态",
    "evaluate_candidates": "正在评估候选股",
    "evaluate_holdings": "正在评估持仓",
    "build_execution_plan": "正在生成手工执行计划",
    "done": "完成",
    "failed": "失败",
}

COLUMN_LABELS = {
    "account_path": "账户路径",
    "account_cash": "账户现金",
    "action": "动作",
    "add_label_names": "新增标签",
    "annualized_return": "年化收益",
    "annualized_excess_return": "年化超额收益",
    "annualized_volatility": "年化波动率",
    "as_of": "快照日期",
    "avg_loss": "平均亏损",
    "avg_profit": "平均盈利",
    "average_holding_days": "平均持仓天数",
    "avg_cost": "平均成本",
    "benchmark_error": "基准错误",
    "benchmark_return": "基准收益",
    "benchmark_symbol": "基准",
    "bucket": "区间",
    "buy_count": "买入次数",
    "cash": "现金",
    "cash_after": "操作后现金",
    "close": "收盘价",
    "code": "代码",
    "commission_rate": "佣金率",
    "date": "日期",
    "daily_return": "日收益",
    "drawdown": "回撤",
    "end": "结束日期",
    "end_date": "结束日期",
    "entry_reason": "入场原因",
    "end_value": "期末权益",
    "estimated_cost": "预计占用资金",
    "executable": "可执行",
    "excess_return": "超额收益",
    "exclude_chinext": "剔除创业板",
    "execution_date": "执行日期",
    "exit_price": "通道退出价",
    "exit_reason": "退出原因",
    "expectancy": "单笔期望",
    "fallback_action": "备选操作",
    "fees": "手续费",
    "final_value": "最终资产",
    "held_symbols": "持仓标的数",
    "held_units": "持仓单元数",
    "holding_days": "持仓天数",
    "initial_cash": "初始资金",
    "last_entry_price": "最近入场价",
    "limit_pct": "涨跌停幅度",
    "limit_status": "涨跌停状态",
    "limit_up_price": "涨停价",
    "lhb_end": "龙虎榜结束日期",
    "lhb_start": "龙虎榜开始日期",
    "watchlist_name": "自选组合名称",
    "time_range": "时间范围",
    "source_detail": "来源说明",
    "raw_count": "原始数量",
    "excluded_count": "被剔除数量",
    "market_regime": "市场状态",
    "stock_regime": "个股状态",
    "confidence": "置信度",
    "data_source": "数据来源",
    "data_sufficient": "数据是否充足",
    "strategy_family": "策略类型",
    "skip_insufficient_cash": "资金不足跳过",
    "skip_volume_limit": "成交量限制跳过",
    "stock_pool_source": "股票池来源",
    "original_count": "原始数量",
    "deduped_count": "去重后数量",
    "filtered_count": "过滤后数量",
    "added_count": "新增数量",
    "duplicate_count": "重复数量",
    "invalid_count": "无效数量",
    "invalid_symbols": "无效代码",
    "removed_count": "被剔除数量",
    "warnings": "警告",
    "errors": "错误",
    "loss_count": "亏损次数",
    "mark_price": "最新价",
    "market_value": "市值",
    "max_drawdown": "最大回撤",
    "max_drawdown_days": "最大回撤天数",
    "min_commission": "最低佣金",
    "month": "月份",
    "n": "N/ATR",
    "name": "名称",
    "net_buy": "净买入额",
    "net_trades": "净交易次数",
    "next_day_max_price": "次日最高接受价",
    "next_add_price": "下次加仓价",
    "pool_mode": "股票池模式",
    "period": "周期",
    "position_utilization": "仓位利用率",
    "position_value": "持仓市值",
    "prev_close": "昨收",
    "price": "价格",
    "principal": "本金",
    "profit_count": "盈利次数",
    "profit_loss_ratio": "盈亏比",
    "rank": "排名",
    "realized_pnl": "已实现盈亏",
    "realized_pnl_pct": "已实现收益率",
    "reason": "原因",
    "recommended_action": "推荐操作",
    "evaluation_action": "分析结果",
    "refresh": "强制刷新",
    "risk_pct": "单元风险",
    "return": "收益率",
    "s1_entry": "S1入场",
    "s1_breakout_price": "S1突破价",
    "s1_exit": "S1退出",
    "s2_entry": "S2入场",
    "s2_breakout_price": "S2突破价",
    "s2_exit": "S2退出",
    "score": "得分",
    "sell_count": "卖出次数",
    "sell_trades": "卖出笔数",
    "shares": "股数",
    "side": "方向",
    "signal_action": "信号动作",
    "signal_date": "信号日期",
    "suggested_price": "建议价格",
    "suggested_shares": "建议股数",
    "slippage_rate": "滑点",
    "source": "历史源",
    "stamp_tax_rate": "印花税率",
    "start": "开始日期",
    "start_date": "开始日期",
    "start_value": "期初权益",
    "stock_source": "股票列表源",
    "stop_price": "止损价",
    "strategy": "策略",
    "symbol": "股票",
    "symbols": "股票池",
    "symbol_pnl": "标的盈亏",
    "sync_holdings": "同步账户持仓",
    "system": "系统",
    "target_sell_price": "目标卖出价",
    "tax": "印花税",
    "timestamp": "时间",
    "top": "Top数量",
    "total_asset": "总资产",
    "total_pnl": "总盈亏",
    "total_return": "总收益率",
    "total_value": "总资产",
    "trade_count": "交易次数",
    "trade_note": "交易提示",
    "traded": "是否成交",
    "trades": "交易次数",
    "unit_shares": "单元股数",
    "units": "单元数",
    "units_after": "操作后单元数",
    "unrealized_pnl": "浮动盈亏",
    "volume_limit_pct": "成交量限制比例",
    "weight": "权重",
    "win_count": "盈利次数",
    "win_rate": "胜率",
    "year": "年份",
    "zero_count": "持平次数",
}

COLUMN_LABELS.update(
    {
        "actual_lhb_range": "实际龙虎榜日期范围",
        "average_after_switch_return": "切换后平均收益",
        "cash_ratio": "现金比例",
        "candidate_count": "候选数量",
        "entry_price": "入场价",
        "evidence": "判断依据",
        "grid_invalid_count": "网格失效次数",
        "grid_lower": "网格下沿",
        "grid_max_layers": "最大网格层数",
        "grid_mid": "网格中枢",
        "grid_stop_condition": "网格停止条件",
        "grid_unit_pct": "单格仓位比例",
        "grid_upper": "网格上沿",
        "job_id": "任务编号",
        "message": "进度说明",
        "node": "当前节点",
        "period_count": "周期数",
        "priority": "优先级",
        "reference_price": "参考价",
        "regime_date": "状态日期",
        "regime_switch_count": "市场状态切换次数",
        "risk_note": "风险提示",
        "stage": "阶段",
        "status": "状态",
        "strength": "强度",
        "suggested_position_pct": "建议仓位比例",
        "switch_count": "切换次数",
        "target_price": "目标价",
        "top_options": "可选排名范围",
        "trend_stop_count": "趋势止损次数",
    }
)

COLUMN_LABELS.update(
    {
        "pool_regime": "股票池强弱",
        "pool_above_ma20_ratio": "股票池高于20日均线比例",
        "pool_uptrend_count": "股票池上升数量",
        "pool_downtrend_count": "股票池下跌数量",
        "pool_ret20": "股票池20日收益",
        "pool_avg_vol20": "股票池平均20日波动率",
        "ret20": "20日收益",
        "ret60": "60日收益",
        "ma20": "20日均线",
        "ma60": "60日均线",
        "range20": "20日区间宽度",
        "range60": "60日区间宽度",
        "vol20": "20日波动率",
        "ma20_slope": "20日均线斜率",
        "ma60_slope": "60日均线斜率",
        "close_ma20_distance": "收盘价偏离20日均线",
        "close_ma60_distance": "收盘价偏离60日均线",
        "trend_strength": "趋势强度",
        "grid_score": "网格评分",
    }
)

INTEGER_DISPLAY_COLUMNS = {
    "rank",
    "shares",
    "suggested_shares",
    "unit_shares",
    "units",
    "units_after",
    "held_symbols",
    "held_units",
    "trade_count",
    "buy_count",
    "sell_count",
    "win_count",
    "profit_count",
    "loss_count",
    "zero_count",
    "holding_days",
    "max_drawdown_days",
}

OPTION_LABELS = {
    "pool_mode": {
        "manual": "手动输入",
        "lhb_top30": "龙虎榜前30",
        "lhb_top50": "龙虎榜前50",
        "portfolio_holding": "账户持仓",
    },
    "stock_pool_source": {
        "manual": "手动输入",
        "watchlist": "自选股组合",
        "market_range": "市场范围",
        "lhb": "龙虎榜",
        "ths_lhb": "同花顺龙虎榜",
    },
    "market_range": {
        "all_a": "沪深 A 股",
        "star": "科创板",
        "sh": "沪市",
        "sz": "深市",
        "chinext": "创业板",
        "bj": "北交所",
    },
    "lhb_range": {
        "1w": "最近 1 周",
        "1m": "最近 1 个月",
        "3m": "最近 3 个月",
        "half_year": "最近半年",
        "1y": "最近 1 年",
        "custom": "自定义",
    },
    "lhb_confirmed_top": {"20": "前 20 名", "30": "前 30 名", "50": "前 50 名"},
    "strategy_date_range": {
        "1m": "最近 1 个月",
        "3m": "最近 3 个月",
        "half_year": "最近半年",
        "1y": "最近 1 年",
        "custom": "自定义",
    },
    "source": {"": "自动", "baostock": "BaoStock", "akshare": "AkShare", "joinquant": "JoinQuant"},
    "stock_source": {"": "自动", "baostock": "BaoStock", "akshare": "AkShare", "joinquant": "JoinQuant"},
    "realtime_source": {"sina": "新浪", "akshare": "AkShare"},
    "action": {
        "buy": "买入",
        "sell": "卖出",
        "hold": "持有",
        "add": "加仓",
        "observe": "观察",
        "wait_confirm": "等待确认",
        "blocked": "暂不参与",
        "stop_grid": "停止网格",
    },
    "evaluation_action": {"buy": "买入", "no_signal": "观望", "missing_history": "缺少历史数据"},
    "signal_action": {"buy": "买入", "sell": "卖出", "hold": "持有", "add": "加仓"},
    "recommended_action": {
        "buy_now": "立即买入",
        "buy": "买入",
        "queue_limit_up": "涨停排队",
        "buy_next_day_below_limit": "次日限价买入",
        "switch_alternative": "切换备选",
        "skip_insufficient_cash": "现金不足，跳过",
        "skip_volume_limit": "成交量不足，跳过",
        "skip": "跳过",
    },
    "fallback_action": {
        "buy_next_day_below_limit": "次日限价买入",
        "switch_alternative": "切换备选",
    },
    "limit_status": {"normal": "正常", "limit_up": "涨停", "unknown": "未知"},
    "market_regime": {
        "uptrend": "上升趋势",
        "downtrend": "下降趋势",
        "range": "震荡区间",
        "transition": "转换期",
    },
    "stock_regime": {
        "uptrend": "上升趋势",
        "downtrend": "下降趋势",
        "range": "震荡区间",
        "transition": "转换期",
    },
    "strategy_family": {
        "trend_following": "趋势跟随",
        "grid": "网格策略",
        "risk_control": "风险控制",
    },
    "confidence": {"high": "高", "medium": "中", "low": "低"},
    "data_source": {"index_history": "指数历史数据", "history": "历史数据", "realtime": "实时行情"},
    "data_sufficient": {"yes": "是", "no": "否", "True": "是", "False": "否", "true": "是", "false": "否"},
    "stage": PROGRESS_STAGE_LABELS,
    "side": {"buy": "买入", "sell": "卖出", "adjust_cost": "调整成本"},
    "strategy": {
        "ma_cross": "双均线",
        "turtle": "海龟",
        "turtle_system": "完整海龟系统",
        "small_cap": "小市值",
        "undervalued": "低估价值",
        "bank_rotation": "银行轮动",
    },
    "refresh": {"yes": "是", "no": "否", "on": "是"},
    "sync_holdings": {"yes": "是", "no": "否", "on": "是"},
    "exclude_chinext": {"yes": "是", "no": "否", "on": "是"},
    "traded": {"yes": "是", "no": "否"},
}

OPTION_LABELS.update(
    {
        "action": {
            **OPTION_LABELS["action"],
            "trial_buy": "试探买入",
        },
        "market_regime": {
            **OPTION_LABELS["market_regime"],
            "market_uptrend": "市场上升",
            "market_range": "市场震荡",
            "market_downtrend": "市场下行",
            "market_transition": "市场过渡",
            "insufficient_data": "数据不足",
        },
        "stock_regime": {
            **OPTION_LABELS["stock_regime"],
            "strong_uptrend": "强上升",
            "insufficient_data": "数据不足",
        },
        "strategy_family": {
            **OPTION_LABELS["strategy_family"],
            "grid_candidate": "网格候选",
            "observe": "观察",
            "transition": "过渡观察",
        },
        "data_source": {
            **OPTION_LABELS["data_source"],
            "composite_index": "组合市场基准",
            "candidate_aggregate": "候选池聚合",
        },
        "pool_regime": {
            "pool_strong": "股票池偏强",
            "pool_neutral": "股票池中性",
            "pool_weak": "股票池偏弱",
            "pool_chaotic": "股票池分化",
        },
        "strength": {
            "high": "高",
            "medium": "中",
            "low": "低",
            "normal": "正常",
            "reduced": "降低",
        },
        "executable": {
            "True": "是",
            "False": "否",
            "true": "是",
            "false": "否",
        },
    }
)


class WebAppHandler(BaseHTTPRequestHandler):
    server_version = "StockPickerWeb/1.1.4"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._send_text("ok")
            return
        if path == "/job":
            job_id = parse_qs(parsed.query).get("id", [""])[-1]
            self._send_json(job_status_payload(job_id))
            return
        if path == "/":
            self._send_page(render_page(page="thermostat", form=LAST_FORM))
            return
        page = path.strip("/")
        if page not in PAGES:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        query_form = _query_form(parsed.query)
        display_form = {**LAST_FORM, **query_form} if query_form else LAST_FORM
        result = None
        if page == "portfolio":
            try:
                result = handle_portfolio_summary({"path": display_form.get("path", DEFAULT_USER_PATH)})
            except Exception:
                result = None
        self._send_page(render_page(page=page, result=result, form=display_form))

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        form: dict[str, str] = {}
        try:
            form = self._read_form()
            display_form = form
            if path == "/thermostat":
                page = "thermostat"
                result = handle_thermostat(form)
            elif path == "/thermostat-lhb-preview":
                page = "thermostat"
                result = handle_thermostat_lhb_preview(form)
            elif path == "/thermostat-job":
                page = "thermostat"
                result = handle_thermostat_job(form)
            elif path == "/thermostat-backtest":
                page = "backtest"
                result = handle_thermostat_backtest(form)
            elif path == "/portfolio-init":
                page = "portfolio"
                result = handle_portfolio_init(form)
            elif path == "/portfolio-buy":
                page = "portfolio"
                result = handle_portfolio_buy(form)
                display_form = _clear_trade_form(form)
            elif path == "/portfolio-sell":
                page = "portfolio"
                result = handle_portfolio_sell(form)
                display_form = _clear_trade_form(form)
            elif path == "/portfolio-adjust-cost":
                page = "portfolio"
                result = handle_portfolio_adjust_cost(form)
                display_form = _clear_trade_form(form)
            elif path == "/portfolio-summary":
                page = "portfolio"
                result = handle_portfolio_summary(form)
            elif path == "/watchlist-save-manual":
                page = "thermostat"
                result = handle_watchlist_save_manual(form)
            elif path in {"/watchlist-create", "/watchlist-add-symbol", "/watchlist-remove-symbol", "/watchlist-rename", "/watchlist-delete"}:
                page = "portfolio"
                result = handle_watchlist_action(path, form)
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            LAST_FORM.clear()
            LAST_FORM.update(display_form)
            self._send_page(render_page(page=page, result=result, form=display_form))
        except Exception as exc:
            self._send_page(render_page(page=_page_for_path(path), error=str(exc), form=form))

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[web] {self.address_string()} - {fmt % args}", file=sys.stderr)

    def _read_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        values = parse_qs(raw, keep_blank_values=True)
        return {key: ",".join(value) if key == "market_range" else value[-1] for key, value in values.items()}

    def _send_page(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_text(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def handle_turtle(form: dict[str, str]) -> RenderResult:
    service = _service(form)
    end = _value(form, "end", _value(form, "as_of", ""))
    if not end:
        raise ValueError("海龟系统需要结束日期或快照日期。")
    start = _optional(form, "start") or (
        pd.to_datetime(end) - pd.Timedelta(days=180)
    ).strftime("%Y%m%d")
    config = _turtle_config(form)
    universe = _resolve_turtle_universe(form)
    if _checked(form, "exclude_chinext"):
        universe = _exclude_chinext_from_universe(universe)
    universe = _enrich_universe_names(service, universe)
    symbols = universe["symbols"]
    if not symbols:
        if _checked(form, "exclude_chinext"):
            raise ValueError("剔除创业板后股票池为空；请补充非创业板股票，或取消“剔除创业板”。")
        raise ValueError("海龟系统需要股票池：可以手动输入、选择龙虎榜，或同步账户持仓。")
    cash = float(universe["cash"])
    portfolio = universe["portfolio"]
    holdings = _holding_advice(service, portfolio, start, end, config, _checked(form, "refresh"))
    held_symbols = set(holdings["symbol"].dropna().astype(str)) if not holdings.empty else set()
    buy_symbols = [symbol for symbol in symbols if symbol not in held_symbols]
    result = (
        run_turtle_system(
            service=service,
            symbols=buy_symbols,
            start_date=start,
            end_date=end,
            cash=cash,
            config=config,
            refresh=_checked(form, "refresh"),
            skip_errors=True,
        )
        if buy_symbols
        else _empty_turtle_result(start, end, cash)
    )
    candidate_evaluation = _candidate_evaluation(
        service,
        buy_symbols,
        universe["pool"],
        start,
        end,
        cash,
        config,
        _checked(form, "refresh"),
        result.signals,
    )
    tables = [
        TableBlock("Final Pool", universe["pool"]),
        TableBlock("Holding Advice", holdings),
        TableBlock("Candidate Evaluation", candidate_evaluation),
        TableBlock("New Buy Signals", result.signals),
        TableBlock("Errors", result.errors),
    ]
    if not universe["lhb"].empty:
        tables.insert(1, TableBlock("LHB Ranking", universe["lhb"]))
    if _checked(form, "execution_plan") and not result.signals.empty:
        quotes = service.get_realtime_quotes(result.signals["symbol"].dropna().astype(str).tolist())
        plan = build_execution_plan(
            result.signals,
            quotes,
            cash=cash,
            lot_size=config.lot_size,
            commission_rate=config.commission_rate,
            min_commission=config.min_commission,
            next_day_premium=_float(form, "next_day_premium", 0.02),
            volume_limit_pct=_float(form, "volume_limit_pct", 0.10),
        )
        tables.insert(-1, TableBlock("Execution Plan", plan))
    return RenderResult(
        title="完整海龟系统",
        summaries=[_turtle_summary(form, start, end, cash, portfolio)],
        tables=tables,
    )


def handle_thermostat(form: dict[str, str], progress_callback=None) -> RenderResult:
    service = _service(form)
    start, end = _strategy_range_dates(form)
    account_path = _value(form, "account_path", DEFAULT_USER_PATH)
    portfolio = _load_portfolio(account_path)
    cash = _float(form, "cash", 5000.0) if _checked(form, "use_simulated_cash") else (portfolio.cash if portfolio is not None else _float(form, "cash", 5000.0))
    pool = _resolve_thermostat_stock_pool(form, service)
    if _value(form, "stock_pool_source", "manual") == "manual" and _optional(form, "symbols") is not None:
        WatchlistStore(account_path).save_last_manual_input(_optional(form, "symbols") or "")
    if pool.should_stop or not pool.symbols:
        return _stock_pool_error_result(pool)
    symbols = list(pool.symbols)
    result = run_thermostat_strategy(
        service=service,
        symbols=symbols,
        start_date=start,
        end_date=end,
        cash=cash,
        portfolio=portfolio,
        refresh=_checked(form, "refresh"),
        progress_callback=progress_callback,
    )
    tables = [
        TableBlock("Stock Pool Summary", _pool_summary_frame(pool)),
        TableBlock("Market Overview", result.market_overview),
        TableBlock("Holding Advice", result.holding_advice),
        TableBlock("New Buy Candidates", result.new_candidates),
        TableBlock("Grid Advice", result.grid_advice),
        TableBlock("Trend Advice", result.trend_advice),
        TableBlock("Errors", result.errors),
    ]
    if _checked(form, "execution_plan") and not result.new_candidates.empty:
        executable = result.new_candidates[result.new_candidates["action"].isin(["buy", "add"])].copy()
        if not executable.empty:
            if progress_callback:
                progress_callback({"stage": "build_execution_plan", "completed": 0, "total": len(executable), "current_symbol": "", "node": "生成执行计划"})
            quotes = service.get_realtime_quotes(executable["symbol"].dropna().astype(str).tolist())
            plan = build_execution_plan(
                executable,
                quotes,
                cash=cash,
                next_day_premium=_float(form, "next_day_premium", 0.02),
                volume_limit_pct=_float(form, "volume_limit_pct", 0.10),
            )
            if progress_callback:
                progress_callback({"stage": "build_execution_plan", "completed": len(executable), "total": len(executable), "current_symbol": "", "node": "生成执行计划"})
            tables.insert(5, TableBlock("Execution Plan", plan))
    return RenderResult(
        title="恒温器策略",
        summaries=[
            _request_summary(
                {**form, "start": start, "end": end, "account_path": account_path},
                ["stock_pool_source", "symbols", "watchlist_name", "market_range", "lhb_range", "start", "end", "account_path", "refresh"],
            )
        ],
        tables=tables,
    )


def handle_thermostat_lhb_preview(form: dict[str, str]) -> RenderResult:
    start, end = _lhb_dates_from_form(form)
    _top, ranked = build_lhb_candidates(start, end, 50)
    ranked = _prepare_lhb_preview_frame(ranked, exclude_star=_checked(form, "exclude_star"))
    if ranked.empty:
        return RenderResult(
            "LHB Candidate Preview",
            summaries=[{"actual_lhb_range": f"{start} 至 {end}", "candidate_count": 0, "errors": "龙虎榜候选池为空，请调整时间范围。"}],
        )
    tables = [
        TableBlock("LHB Top 20", ranked.head(20)),
        TableBlock("LHB Top 30", ranked.head(30)),
        TableBlock("LHB Top 50", ranked.head(50)),
    ]
    return RenderResult(
        "LHB Candidate Preview",
        summaries=[
            {
                "actual_lhb_range": f"{start} 至 {end}",
                "candidate_count": len(ranked),
                "top_options": "20 / 30 / 50",
                "source_detail": "东方财富龙虎榜",
            }
        ],
        tables=tables,
        extra_html=render_lhb_confirmation_form(form, ranked, start, end),
    )


def handle_thermostat_job(form: dict[str, str]) -> RenderResult:
    job = start_thermostat_job(form)
    return RenderResult(
        "Thermostat Job Started",
        summaries=[{"job_id": job.job_id, "stage": job.stage, "node": job.node, "message": job.message}],
        extra_html=render_job_progress(job.job_id),
    )


def _lhb_dates_from_form(form: dict[str, str]) -> tuple[str, str]:
    range_key = _value(form, "lhb_range", "1w")
    return lhb_range_dates(
        range_key,
        as_of=_value(form, "end", _today_yyyymmdd()),
        start_date=_value(form, "lhb_start"),
        end_date=_value(form, "lhb_end"),
    )


def _prepare_lhb_preview_frame(frame: pd.DataFrame, *, exclude_star: bool = False) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["code", "name", "net_buy", "rank"])
    data = frame.copy()
    data["code"] = data["code"].astype(str).str.zfill(6)
    if exclude_star:
        data = data[~data["code"].str.startswith("688")]
    if "rank" not in data.columns:
        data = data.reset_index(drop=True)
        data["rank"] = range(1, len(data) + 1)
    return data.reset_index(drop=True)


def render_lhb_confirmation_form(form: dict[str, str], ranked: pd.DataFrame, start: str, end: str) -> str:
    hidden = _hidden_inputs(
        form,
        [
            "account_path",
            "strategy_date_range",
            "start",
            "end",
            "cash",
            "use_simulated_cash",
            "refresh",
            "execution_plan",
            "next_day_premium",
            "volume_limit_pct",
            "exclude_star",
            "data_source",
            "history_source",
            "realtime_source",
            "stock_source",
        ],
    )
    symbols = ",".join(_symbols_from_lhb_frame(ranked.head(50)))
    selected_top = _value(form, "lhb_confirmed_top", "30")
    top_options = []
    for value, label in (("20", "前 20 名"), ("30", "前 30 名"), ("50", "前 50 名")):
        selected = " selected" if selected_top == value else ""
        top_options.append(f'<option value="{value}"{selected}>{label}</option>')
    return f"""
    <section class="candidate-confirm">
      <h3>确认龙虎榜候选池</h3>
      <p class="muted">实际日期范围：{html.escape(start)} 至 {html.escape(end)}。默认使用 Top 30，可改为 Top 20/30/50 后再运行。</p>
      <form method="post" action="/thermostat-job">
        {hidden}
        <input type="hidden" name="stock_pool_source" value="lhb">
        <input type="hidden" name="lhb_range" value="{html.escape(_value(form, "lhb_range", "1w"))}">
        <input type="hidden" name="lhb_start" value="{html.escape(start)}">
        <input type="hidden" name="lhb_end" value="{html.escape(end)}">
        <input type="hidden" name="confirmed_lhb_symbols" value="{html.escape(symbols)}">
        <label>候选池数量
          <select name="lhb_confirmed_top" data-lhb-top-selector>
            {"".join(top_options)}
          </select>
        </label>
        <button type="submit">确认候选池并运行恒温器</button>
      </form>
    </section>
    """


def _symbols_from_lhb_frame(frame: pd.DataFrame) -> list[str]:
    if frame is None or frame.empty or "code" not in frame:
        return []
    return frame["code"].astype(str).str.zfill(6).tolist()


def _hidden_inputs(form: dict[str, str], keys: list[str]) -> str:
    fields = []
    for key in keys:
        if key in form:
            fields.append(f'<input type="hidden" name="{html.escape(key)}" value="{html.escape(str(form[key]))}">')
    return "\n".join(fields)


class ThermostatJob:
    def __init__(self, job_id: str, form: dict[str, str]) -> None:
        self.job_id = job_id
        self.form = dict(form)
        self.status = "queued"
        self.stage = "queued"
        self.node = "排队"
        self.message = "任务已创建，等待开始。"
        self.completed = 0
        self.total = 0
        self.current_symbol = ""
        self.percent = 0
        self.error = ""
        self.result_html = ""

    def update(self, event: dict[str, object]) -> None:
        with JOBS_LOCK:
            self.status = "running"
            self.stage = str(event.get("stage") or self.stage)
            self.node = str(event.get("node") or _display_progress_stage(self.stage))
            self.completed = int(event.get("completed") or 0)
            self.total = int(event.get("total") or 0)
            self.current_symbol = str(event.get("current_symbol") or "")
            self.percent = _progress_percent(self.stage, self.completed, self.total)
            self.message = _progress_message(self.node, self.stage, self.completed, self.total, self.current_symbol)

    def complete(self, result: RenderResult) -> None:
        with JOBS_LOCK:
            self.status = "done"
            self.stage = "done"
            self.node = "完成"
            self.percent = 100
            self.message = "恒温器评估完成。"
            self.result_html = render_message(result, None)

    def fail(self, exc: Exception) -> None:
        with JOBS_LOCK:
            self.status = "failed"
            self.stage = "failed"
            self.node = "失败"
            self.message = f"任务失败：{exc}"
            self.error = self.message


def start_thermostat_job(form: dict[str, str]) -> ThermostatJob:
    job_id = uuid.uuid4().hex
    job = ThermostatJob(job_id, form)
    with JOBS_LOCK:
        JOBS[job_id] = job
    thread = threading.Thread(target=_run_thermostat_job, args=(job_id,), daemon=True)
    thread.start()
    return job


def _run_thermostat_job(job_id: str) -> None:
    job = JOBS[job_id]
    try:
        result = handle_thermostat(job.form, progress_callback=job.update)
        job.complete(result)
    except Exception as exc:  # pragma: no cover - defensive live-web path
        job.fail(exc)


def job_status_payload(job_id: str) -> dict[str, object]:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return {"status": "missing", "error": "任务不存在或已过期。"}
        return {
            "job_id": job.job_id,
            "status": job.status,
            "stage": job.stage,
            "node": job.node,
            "message": job.message,
            "completed": job.completed,
            "total": job.total,
            "current_symbol": job.current_symbol,
            "percent": job.percent,
            "error": job.error,
            "result_html": job.result_html,
        }


def render_job_progress(job_id: str) -> str:
    escaped = html.escape(job_id)
    return f"""
    <section class="job-progress" data-job-id="{escaped}">
      <h3>运行进度</h3>
      <progress max="100" value="0"></progress>
      <p class="job-stage">排队</p>
      <p class="job-detail">任务已创建，等待开始。</p>
      <div class="job-result"></div>
    </section>
    <script>
    (function() {{
      const box = document.querySelector('[data-job-id="{escaped}"]');
      if (!box) return;
      const progress = box.querySelector('progress');
      const stage = box.querySelector('.job-stage');
      const detail = box.querySelector('.job-detail');
      const result = box.querySelector('.job-result');
      async function poll() {{
        const response = await fetch('/job?id={escaped}');
        const data = await response.json();
        progress.value = data.percent || 0;
        stage.textContent = data.node || data.stage || '';
        detail.textContent = data.message || '';
        if (data.status === 'done') {{
          result.innerHTML = data.result_html || '';
          return;
        }}
        if (data.status === 'failed') {{
          detail.textContent = data.error || data.message || '任务失败';
          return;
        }}
        setTimeout(poll, 1000);
      }}
      poll();
    }})();
    </script>
    """


def _progress_percent(stage: str, completed: int, total: int) -> int:
    ranges = {
        "queued": (0, 2),
        "initialize_task": (2, 5),
        "load_market_history": (5, 10),
        "load_candidate_history": (10, 55),
        "classify_market": (55, 60),
        "evaluate_candidates": (60, 85),
        "evaluate_holdings": (85, 92),
        "build_execution_plan": (92, 98),
        "done": (100, 100),
    }
    start, end = ranges.get(stage, (5, 95))
    if total <= 0:
        return start
    ratio = min(max(completed / total, 0), 1)
    return int(start + (end - start) * ratio)


def _display_progress_stage(stage: str) -> str:
    return PROGRESS_STAGE_LABELS.get(stage, f"正在处理未知阶段：{stage}")


def _progress_message(node: str, stage: str, completed: int, total: int, current_symbol: str) -> str:
    suffix = ""
    if stage == "evaluate_candidates":
        suffix = "，生成市场状态、网格/趋势建议"
    elif stage == "evaluate_holdings":
        suffix = "，生成持仓处理建议"
    elif stage == "build_execution_plan":
        suffix = "，筛选可执行买入/加仓信号"
    if total > 0 and current_symbol:
        return f"{node}：已完成 {completed} / {total}，当前处理 {current_symbol}{suffix}"
    if total > 0:
        return f"{node}：已完成 {completed} / {total}{suffix}"
    return f"{node}{suffix}"


def handle_watchlist_save_manual(form: dict[str, str]) -> RenderResult:
    path = _value(form, "path", _value(form, "account_path", DEFAULT_USER_PATH))
    name = _value(form, "watchlist_name")
    if not name:
        raise ValueError("保存自选股需要组合名称。")
    pool = parse_manual_pool(_value(form, "symbols"))
    if pool.should_stop or not pool.symbols:
        return _stock_pool_error_result(pool)
    store = WatchlistStore(path)
    created = store.create(name)
    if created.status == "name_conflict":
        store.add_symbols(name, pool.symbols)
    else:
        store.add_symbols(name, pool.symbols)
    store.save_last_manual_input(_value(form, "symbols"))
    saved = store.get(name)
    return RenderResult(
        "自选股已保存",
        summaries=[{"watchlist_name": name, "filtered_count": len(saved.symbols) if saved else 0}],
        tables=[TableBlock("Watchlists", _watchlists_frame(store))],
    )


def handle_watchlist_action(path: str, form: dict[str, str]) -> RenderResult:
    store = WatchlistStore(_value(form, "path", DEFAULT_USER_PATH))
    result = None
    if path == "/watchlist-create":
        result = store.create(_value(form, "watchlist_name"))
    elif path == "/watchlist-add-symbol":
        result = store.add_symbols(_value(form, "watchlist_name"), [_value(form, "symbol")])
    elif path == "/watchlist-remove-symbol":
        result = store.remove_symbol(_value(form, "watchlist_name"), _value(form, "symbol"))
    elif path == "/watchlist-rename":
        result = store.rename(_value(form, "watchlist_name"), _value(form, "new_watchlist_name"))
    elif path == "/watchlist-delete":
        result = store.delete(_value(form, "watchlist_name"))
    summaries = [_watchlist_operation_summary(result)] if result is not None else []
    return RenderResult(
        "自选股组合",
        summaries=summaries,
        tables=[TableBlock("Watchlists", _watchlists_frame(store))],
        extra_html=_watchlist_operation_feedback(result),
    )


def _watchlist_operation_summary(result) -> dict[str, object]:
    duplicates = result.duplicates or []
    invalid = result.invalid_symbols or []
    return {
        "watchlist_name": result.name,
        "status": result.status,
        "filtered_count": len(result.symbols),
        "duplicate_count": len(duplicates),
        "invalid_count": len(invalid),
        "invalid_symbols": ", ".join(invalid),
    }


def _watchlist_operation_feedback(result) -> str:
    if result is None or not result.message:
        return ""
    class_name = "warning" if result.invalid_symbols else "info"
    return f'<p class="muted {class_name}">{html.escape(result.message)}</p>'


def handle_thermostat_backtest(form: dict[str, str]) -> RenderResult:
    symbols = [normalize_symbol(symbol) for symbol in _require_symbols(form)]
    start = _value(form, "start")
    end = _value(form, "end", _today_yyyymmdd())
    if not start or not end:
        raise ValueError("恒温器回测需要开始日期和结束日期。")
    result = backtest_thermostat_strategy(
        service=_service(form),
        symbols=symbols,
        start_date=start,
        end_date=end,
        initial_cash=_float(form, "cash", 100000.0),
    )
    return RenderResult(
        title="恒温器回测诊断",
        summaries=[_request_summary({**form, "end": end}, ["symbols", "start", "end", "cash", "source", "refresh"])],
        tables=[
            TableBlock("Summary", result.summary),
            TableBlock("Regime Performance", result.regime_performance),
            TableBlock("Diagnostics", result.diagnostics),
        ],
    )


def handle_turtle_backtest(form: dict[str, str]) -> RenderResult:
    service = _service(form)
    universe = _resolve_backtest_universe(form)
    if _checked(form, "exclude_chinext"):
        universe = _exclude_chinext_from_universe(universe)
    universe = _enrich_universe_names(service, universe)
    symbols = universe["symbols"]
    if not symbols:
        if _checked(form, "exclude_chinext"):
            raise ValueError("剔除创业板后回测股票池为空；请补充非创业板股票，或取消“剔除创业板”。")
        raise ValueError("海龟回测需要股票池：可以手动输入，或选择龙虎榜前30/前50。")
    start = _value(form, "start")
    end = _value(form, "end", _today_yyyymmdd())
    if not start or not end:
        raise ValueError("回测需要开始日期和结束日期。")
    result = backtest_turtle_system(
        service=service,
        symbols=symbols,
        start_date=start,
        end_date=end,
        initial_cash=_float(form, "cash", 100000.0),
        config=_turtle_config(form),
        refresh=_checked(form, "refresh"),
        skip_errors=True,
    )
    benchmark = _benchmark_summary(service, start, end)
    summary = _augment_backtest_summary(result.summary, benchmark)
    candidate_diff = _backtest_candidate_difference(universe["pool"], result.trades)
    yearly = _period_returns(result.equity, "Y")
    monthly = _period_returns(result.equity, "M")
    monthly_matrix = _monthly_return_matrix(monthly)
    trade_quality = _trade_quality(result.trades)
    holding_distribution = _holding_distribution(result.trades)
    summary_form = dict(form)
    summary_form["end"] = end
    summary_form.setdefault("pool_mode", _value(form, "pool_mode", "manual"))
    return RenderResult(
        title="海龟回测",
        summaries=[
            _request_summary(
                summary_form,
                [
                    "pool_mode",
                    "symbols",
                    "lhb_start",
                    "lhb_end",
                    "exclude_chinext",
                    "start",
                    "end",
                    "cash",
                    "risk_pct",
                    "s1_entry",
                    "s1_exit",
                    "s2_entry",
                    "s2_exit",
                    "source",
                    "refresh",
                ],
            )
        ],
        tables=[
            TableBlock("Summary", summary),
            TableBlock("Backtest Pool", universe["pool"]),
            TableBlock("Backtest Candidate Difference", candidate_diff),
            TableBlock("Yearly Returns", yearly),
            TableBlock("Monthly Returns", monthly),
            TableBlock("Monthly Return Matrix", monthly_matrix),
            TableBlock("Trade Quality", trade_quality),
            TableBlock("Holding Distribution", holding_distribution),
            TableBlock("Drawdowns", result.drawdowns),
            TableBlock("Symbol PnL", result.symbol_pnl),
            TableBlock("Trades", result.trades.tail(100)),
            TableBlock("Equity", result.equity.tail(120)),
            TableBlock("Errors", result.errors),
        ],
    )


def handle_portfolio_init(form: dict[str, str]) -> RenderResult:
    store = ManualPortfolioStore(_value(form, "path", DEFAULT_USER_PATH))
    portfolio = store.initialize(
        principal=_float(form, "principal", 5000.0),
        cash=_optional_float(form, "cash"),
        commission_rate=_float(form, "commission_rate", 0.0003),
        min_commission=_float(form, "min_commission", 5.0),
        stamp_tax_rate=_float(form, "stamp_tax_rate", 0.001),
    )
    return RenderResult("账户已初始化", summaries=[portfolio.summary()])


def handle_portfolio_buy(form: dict[str, str]) -> RenderResult:
    store = ManualPortfolioStore(_value(form, "path", DEFAULT_USER_PATH))
    symbol = _value(form, "symbol")
    portfolio = store.buy(
        symbol=symbol,
        name=_lookup_stock_name(symbol, form),
        price=_float(form, "price", 0.0),
        shares=_int(form, "shares", 0),
        target_sell_price=_optional_float(form, "target_sell_price"),
        strategy=_optional(form, "strategy_meta") or "",
        system=_optional(form, "system") or "",
        entry_reason=_optional(form, "entry_reason") or "",
        signal_date=_optional(form, "signal_date"),
        execution_date=_optional(form, "execution_date"),
        note=_optional(form, "note") or "",
    )
    return RenderResult(
        "买入已记录",
        summaries=[portfolio.summary()],
        tables=_portfolio_tables(portfolio),
    )


def handle_portfolio_sell(form: dict[str, str]) -> RenderResult:
    store = ManualPortfolioStore(_value(form, "path", DEFAULT_USER_PATH))
    portfolio = store.sell(
        symbol=_value(form, "symbol"),
        price=_float(form, "price", 0.0),
        shares=_int(form, "shares", 0),
        strategy=_optional(form, "strategy_meta") or "",
        system=_optional(form, "system") or "",
        exit_reason=_optional(form, "exit_reason") or "",
        signal_date=_optional(form, "signal_date"),
        execution_date=_optional(form, "execution_date"),
        note=_optional(form, "note") or "",
    )
    return RenderResult(
        "卖出已记录",
        summaries=[portfolio.summary(_marks(form))],
        tables=_portfolio_tables(portfolio, _marks(form)),
    )


def handle_portfolio_adjust_cost(form: dict[str, str]) -> RenderResult:
    store = ManualPortfolioStore(_value(form, "path", DEFAULT_USER_PATH))
    portfolio = store.adjust_cost(
        symbol=_value(form, "symbol"),
        avg_cost=_float(form, "avg_cost", 0.0),
        note=_optional(form, "note") or "",
    )
    return RenderResult(
        "成本已调整",
        summaries=[portfolio.summary(_marks(form))],
        tables=_portfolio_tables(portfolio, _marks(form)),
    )


def handle_portfolio_summary(form: dict[str, str]) -> RenderResult:
    store = ManualPortfolioStore(_value(form, "path", DEFAULT_USER_PATH))
    portfolio = store.load()
    marks = _marks(form)
    if _checked(form, "refresh_valuation"):
        marks.update(_quote_marks(_service(form), portfolio.positions["symbol"].dropna().astype(str).tolist()))
    return RenderResult(
        "账户概览",
        summaries=[portfolio.summary(marks)],
        tables=_portfolio_tables(portfolio, marks),
    )


def render_page(
    page: str = "thermostat",
    result: RenderResult | None = None,
    error: str | None = None,
    form: dict[str, str] | None = None,
) -> str:
    form = form or {}
    page = page if page in PAGES else "thermostat"
    page_body = {
        "thermostat": render_thermostat_section(form),
        "backtest": render_thermostat_backtest_section(form),
        "portfolio": render_portfolio_section(form),
    }[page]
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{APP_NAME}</title>
  <style>{CSS}</style>
</head>
<body>
  <header>
    <div>
      <h1>{APP_NAME}</h1>
      <p>恒温器策略、回测诊断和手动账户工作台</p>
    </div>
    <div class="status">本地运行 · {html.escape(datetime.now().strftime("%Y-%m-%d %H:%M"))}</div>
  </header>
  <nav>
    {nav_link("thermostat", "恒温器策略", page)}
    {nav_link("backtest", "回测诊断", page)}
    {nav_link("portfolio", "账户", page)}
  </nav>
  <main>
    {render_message(result, error)}
    <div class="workbench-page">
      {page_body}
    </div>
  </main>
  {render_source_refresh_script(page)}
</body>
</html>"""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{APP_NAME}</title>
  <style>{CSS}</style>
</head>
<body>
  <header>
    <div>
      <h1>{APP_NAME}</h1>
      <p>本地海龟系统、回测和手动账户工作台</p>
    </div>
    <div class="status">本地运行 · {html.escape(datetime.now().strftime("%Y-%m-%d %H:%M"))}</div>
  </header>
  <nav>
    {nav_link("turtle", "海龟系统", page)}
    {nav_link("backtest", "回测", page)}
    {nav_link("portfolio", "账户", page)}
  </nav>
  <main>
    {render_message(result, error)}
    {page_body}
  </main>
</body>
</html>"""


def nav_link(target: str, label: str, current: str) -> str:
    active = ' class="active"' if target == current else ""
    return f'<a href="/{target}"{active}>{html.escape(label)}</a>'


def render_source_refresh_script(page: str) -> str:
    if page != "thermostat":
        return ""
    return """
  <script>
  function refreshSourceFields(select) {
    const form = select.form;
    const params = new URLSearchParams(new FormData(form));
    window.location.href = "/thermostat?" + params.toString();
  }
  </script>
  """


def render_thermostat_section(form: dict[str, str]) -> str:
    display_form = _thermostat_display_form(form)
    stock_source = _value(display_form, "stock_pool_source", "manual")
    form_action = "/thermostat-job" if stock_source in {"lhb", "ths_lhb"} else "/thermostat"
    submit_label = "运行恒温器策略"
    return f"""
    <section id="thermostat" class="workspace-section">
      <div class="page-head">
        <h2>恒温器策略</h2>
        <p class="status">页面状态：选择股票池、策略日期和账户资金后运行。工作区按当前选择动态显示字段。</p>
      </div>
      <form method="post" action="{form_action}">
        <div class="panel-block">
          <h3>工作区：股票池来源</h3>
          {stock_pool_fields(display_form)}
        </div>
        <div class="panel-block">
          <h3>工作区：策略日期和资金</h3>
          <div class="grid">
            {strategy_date_fields(display_form)}
            {input_text("account_path", "账户路径", DEFAULT_USER_PATH, display_form)}
          </div>
          {account_cash_status(display_form)}
          {simulated_cash_fields(display_form)}
        </div>
        <details class="advanced-settings">
          <summary>高级设置</summary>
          <p class="muted">数据与执行设置默认收起，展开后可调整数据源、刷新和执行计划。</p>
          <div class="grid">
            {source_fields(display_form)}
            {execution_fields(display_form)}
          </div>
          {checkbox("execution_plan", "生成手工执行计划", display_form, checked=True)}
        </details>
        {checkbox("exclude_star", "剔除科创板", display_form)}
        <button type="submit">{html.escape(submit_label)}</button>
      </form>
      <p class="muted">股票池来源支持手动输入、自选股组合、市场范围和龙虎榜。当前来源：{html.escape(OPTION_LABELS["stock_pool_source"].get(stock_source, stock_source))}</p>
    </section>"""


def strategy_date_fields(form: dict[str, str]) -> str:
    current = _value(form, "strategy_date_range", "3m")
    actual_start, actual_end = _strategy_range_dates(form)
    fields = select("strategy_date_range", ("1m", "3m", "half_year", "1y", "custom"), "3m", "策略日期范围", form)
    fields += f'<p class="muted">实际使用日期范围：{html.escape(actual_start)} 至 {html.escape(actual_end)}</p>'
    if current == "custom":
        fields += input_text("start", "策略开始日期", "", form)
        fields += input_text("end", "策略结束日期", _today_yyyymmdd(), form)
    return fields


def account_cash_status(form: dict[str, str]) -> str:
    if _checked(form, "use_simulated_cash"):
        return '<p class="muted">使用模拟资金进行临时策略测算，不会改变账户现金、持仓或交易流水。</p>'
    path = _value(form, "account_path", DEFAULT_USER_PATH)
    portfolio = _load_portfolio(path)
    if portfolio is None:
        return '<p class="message-inline error">账户未初始化，请先到账户页初始化账户。</p>'
    return f'<p class="message-inline">账户现金（只读）：<strong>{portfolio.cash:.2f}</strong></p>'


def simulated_cash_fields(form: dict[str, str]) -> str:
    checked = _checked(form, "use_simulated_cash")
    fields = checkbox("use_simulated_cash", "使用模拟资金", form)
    if checked:
        fields += '<p class="muted">模拟资金仅用于临时策略测算，不会改变账户。</p>'
        fields += input_number("cash", "模拟资金", "5000", form)
    return fields


def _strategy_range_dates(form: dict[str, str]) -> tuple[str, str]:
    range_key = _value(form, "strategy_date_range", "3m")
    if range_key == "custom":
        end = _value(form, "end", _today_yyyymmdd())
        start = _optional(form, "start") or (pd.to_datetime(end) - pd.Timedelta(days=90)).strftime("%Y%m%d")
        return start, end
    end = _value(form, "end", _today_yyyymmdd())
    offsets = {"1m": 30, "3m": 90, "half_year": 182, "1y": 365}
    start = (pd.to_datetime(end) - pd.Timedelta(days=offsets.get(range_key, 90))).strftime("%Y%m%d")
    return start, end


def render_thermostat_backtest_section(form: dict[str, str]) -> str:
    today = _today_yyyymmdd()
    return f"""
    <section id="backtest" class="workspace-section">
      <div class="page-head">
        <h2>恒温器回测诊断</h2>
        <p class="status">页面状态：工作区用于输入回测股票池和日期，计算语义保持不变。</p>
      </div>
      <form method="post" action="/thermostat-backtest">
        <h3>工作区：回测输入</h3>
        <div class="grid">
          {input_text("symbols", "股票池", "", form)}
          {input_text("start", "开始日期", "", form)}
          {input_text("end", "结束日期", today, form)}
          {input_number("cash", "初始资金", "100000", form)}
          {source_fields(form)}
        </div>
        {checkbox("refresh", "强制刷新历史数据", form)}
        <button type="submit">运行恒温器回测</button>
      </form>
    </section>"""


def render_turtle_section(form: dict[str, str]) -> str:
    today = _today_yyyymmdd()
    return f"""
    <section id="turtle">
      <h2>完整海龟系统</h2>
      <form method="post" action="/turtle">
        <div class="grid">
          {select("pool_mode", ("manual", "lhb_top30", "lhb_top50"), "manual", "股票池模式", form)}
          {input_text("lhb_start", "龙虎榜开始日期", "", form)}
          {input_text("lhb_end", "龙虎榜结束日期", "", form)}
          {input_text("account_path", "账户路径", DEFAULT_USER_PATH, form)}
          {input_text("symbols", "股票池", "", form)}
          {input_text("start", "开始日期", "", form)}
          {input_text("end", "结束/As of", today, form)}
          {turtle_fields(form)}
          {execution_fields(form)}
          {source_fields(form)}
        </div>
        {checkbox("sync_holdings", "同步账户持仓", form, checked=True)}
        {checkbox("exclude_chinext", "剔除创业板", form)}
        {checkbox("execution_plan", "生成手工执行计划", form, checked=True)}
        <button type="submit">运行海龟系统</button>
      </form>
    </section>"""


def render_backtest_section(form: dict[str, str]) -> str:
    today = _today_yyyymmdd()
    return f"""
    <section id="backtest">
      <h2>海龟回测诊断</h2>
      <form method="post" action="/turtle-backtest">
        <div class="grid">
          {select("pool_mode", ("manual", "lhb_top30", "lhb_top50"), "manual", "股票池模式", form)}
          {input_text("lhb_start", "龙虎榜开始日期", "", form)}
          {input_text("lhb_end", "龙虎榜结束日期", "", form)}
          {input_text("symbols", "股票池", "", form)}
          {input_text("start", "开始日期", "", form)}
          {input_text("end", "结束日期", today, form)}
          {input_number("cash", "初始资金", "100000", form)}
          {turtle_fields(form)}
          {source_fields(form)}
        </div>
        {checkbox("exclude_chinext", "剔除创业板", form)}
        <button type="submit">运行回测</button>
      </form>
    </section>"""


def render_portfolio_section(form: dict[str, str]) -> str:
    portfolio = _load_portfolio(_value(form, "path", DEFAULT_USER_PATH))
    return f"""
    <section id="portfolio" class="workspace-section">
      <div class="page-head">
        <h2>账户</h2>
        <p class="status">页面状态：账户概览默认展示，低频操作放入功能操作区。工作区按任务分组。</p>
      </div>
      {render_account_overview(form, portfolio)}
      {render_holdings_and_trades_summary(portfolio)}
      <section class="function-tabs">
        <h2>功能操作区</h2>
        <div class="tab-labels">
          <span>自选组合</span><span>账户设置</span><span>持仓与估值</span><span>买入 / 卖出</span><span>成本调整</span><span>交易记录</span>
        </div>
        <details open>
          <summary>自选组合</summary>
          {render_watchlist_management(form)}
        </details>
        <details>
          <summary>账户设置</summary>
          {render_account_initializer(form)}
        </details>
        <details>
          <summary>持仓与估值</summary>
          {render_valuation_refresher(form)}
        </details>
        <details>
          <summary>买入 / 卖出</summary>
          <div class="columns">
            <form method="post" action="/portfolio-buy">
              <h3>买入记录</h3>
              {portfolio_path(form)}
              {trade_fields(side="buy", form=form)}
              <button type="submit">记录买入</button>
            </form>
            <form method="post" action="/portfolio-sell">
              <h3>卖出记录</h3>
              {portfolio_path(form)}
              {trade_fields(side="sell", form=form)}
              <button type="submit">记录卖出</button>
            </form>
          </div>
        </details>
        <details>
          <summary>成本调整</summary>
          <p class="muted">这是低频操作，会修改持仓成本记录。</p>
          <form method="post" action="/portfolio-adjust-cost">
            {portfolio_path(form)}
            <div class="grid">
              {input_text("symbol", "股票代码", "", form)}
              {input_number("avg_cost", "正确平均成本", "", form)}
              {input_text("note", "备注", "", form)}
            </div>
            <button type="submit">调整成本</button>
          </form>
        </details>
        <details>
          <summary>交易记录</summary>
          {render_table("Trades", _trades_view(portfolio.trades if portfolio is not None else pd.DataFrame()))}
        </details>
      </section>
    </section>
    """


def render_message(result: RenderResult | None, error: str | None) -> str:
    if error:
        return f'<section class="message error"><strong>错误</strong><p>{html.escape(_translate_text(error))}</p></section>'
    if result is None:
        return ""
    parts = [f'<section class="message"><h2>{html.escape(_display_title(result.title))}</h2>']
    for summary in result.summaries:
        parts.append(render_summary(summary))
    for table in result.tables:
        parts.append(render_table(table.title, table.frame))
    if result.extra_html:
        parts.append(result.extra_html)
    parts.append("</section>")
    return "\n".join(parts)


def render_summary(values: dict[str, object]) -> str:
    rows = []
    for key, value in values.items():
        display = _display_value(key, value)
        rows.append(f"<dt>{html.escape(_display_label(str(key)))}</dt><dd>{html.escape(display)}</dd>")
    return f'<dl class="summary">{"".join(rows)}</dl>'


def render_table(title: str, frame: pd.DataFrame) -> str:
    if frame is None or frame.empty:
        return f"<h3>{html.escape(_display_title(title))}</h3><p class=\"muted\">暂无数据。</p>"
    data = frame.copy()
    if len(data) > 200:
        data = data.tail(200)
    data = data.replace({pd.NA: ""}).fillna("")
    data = _localize_frame(data)
    table = data.to_html(index=False, escape=True, classes="data-table", border=0)
    return (
        f"<h3>{html.escape(_display_title(title))} <span>{len(frame)} 行</span></h3>"
        f'<div class="table-wrap">{table}</div>'
    )


def render_account_overview(form: dict[str, str], portfolio) -> str:
    if portfolio is None:
        return """
        <section class="account-overview">
          <h2>账户概览</h2>
          <p class="message-inline error">账户未初始化，请先在账户设置中初始化账户。</p>
          <a class="secondary-action" href="#account-settings">初始化账户</a>
        </section>
        """
    summary = portfolio.summary(_marks(form))
    cards = [
        ("本金", summary.get("principal", 0.0)),
        ("现金", summary.get("cash", 0.0)),
        ("持仓市值", summary.get("position_value", 0.0)),
        ("总资产", summary.get("total_asset", 0.0)),
        ("总收益", summary.get("total_pnl", 0.0)),
        ("总收益率", summary.get("total_return", 0.0)),
        ("已实现盈亏", summary.get("realized_pnl", 0.0)),
        ("浮动盈亏", summary.get("unrealized_pnl", 0.0)),
        ("持仓数量", len(portfolio.positions)),
        ("胜率", summary.get("win_rate", 0.0)),
        ("盈亏比", summary.get("profit_loss_ratio", 0.0)),
        ("最大回撤", 0.0),
        ("佣金率", summary.get("commission_rate", 0.0)),
        ("印花税率", summary.get("stamp_tax_rate", 0.0)),
    ]
    content = "".join(
        f'<div class="overview-card"><span>{html.escape(label)}</span><strong>{html.escape(_format_metric(value))}</strong></div>'
        for label, value in cards
    )
    return f'<section class="account-overview"><h2>账户概览</h2><div class="overview-grid">{content}</div></section>'


def render_holdings_and_trades_summary(portfolio) -> str:
    if portfolio is None:
        return """
        <section class="account-summaries">
          <h2>当前持仓</h2><p class="muted">暂无持仓。</p>
          <h2>交易流水</h2><p class="muted">暂无交易流水。</p>
        </section>
        """
    positions = _positions_view(portfolio.positions)
    recent_trades = _trades_view(portfolio.trades.tail(5))
    positions_html = render_table("Positions", positions) if positions is not None and not positions.empty else '<h3>当前持仓</h3><p class="muted">暂无持仓。</p>'
    trades_html = render_table("Trades", recent_trades) if recent_trades is not None and not recent_trades.empty else '<h3>交易流水</h3><p class="muted">暂无交易流水。</p>'
    return f"""
    <section class="account-summaries">
      {positions_html}
      <a class="secondary-action" href="#all-holdings">查看全部持仓</a>
      {trades_html}
      <a class="secondary-action" href="#all-trades">查看全部交易流水</a>
    </section>
    """


def render_account_initializer(form: dict[str, str]) -> str:
    return f"""
    <form id="account-settings" method="post" action="/portfolio-init">
      <h3>初始化账户</h3>
      <div class="grid">
        {portfolio_path(form)}
        {input_number("principal", "本金", "5000", form)}
        {input_number("commission_rate", "佣金率", "0.0003", form)}
        {input_number("min_commission", "最低佣金", "5", form)}
        {input_number("stamp_tax_rate", "印花税率", "0.001", form)}
      </div>
      <p class="muted">初始化后会更新账户概览。</p>
      <button type="submit">初始化账户</button>
    </form>
    """


def render_valuation_refresher(form: dict[str, str]) -> str:
    return f"""
    <form method="post" action="/portfolio-summary">
      <input type="hidden" name="refresh_valuation" value="1">
      <h3>刷新行情 / 更新估值</h3>
      <div class="grid">
        {portfolio_path(form)}
        {input_text("marks", "标记价格", "", form)}
        {source_fields(form)}
      </div>
      <button type="submit">刷新估值</button>
    </form>
    """


def _format_metric(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _display_title(title: str) -> str:
    if title in TITLE_LABELS:
        return TITLE_LABELS[title]
    if title.startswith("Strategy:"):
        strategy = title.split(":", 1)[1].strip()
        return f"策略运行：{_display_value('strategy', strategy)}"
    if _contains_cjk(title):
        return title
    return f"未翻译字段：{title}"


def _display_label(key: str) -> str:
    if key in COLUMN_LABELS:
        return COLUMN_LABELS[key]
    if key.isdigit():
        return f"{int(key)}月"
    if _contains_cjk(key):
        return key
    return f"未翻译字段：{key}"


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _display_value(key: str, value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    if key in INTEGER_DISPLAY_COLUMNS:
        try:
            return str(int(float(value)))
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, float):
        return f"{value:.6f}"
    if isinstance(value, bool):
        return "是" if value else "否"
    text = str(value)
    if key in OPTION_LABELS and text in OPTION_LABELS[key]:
        return OPTION_LABELS[key][text]
    if key == "source" and text in OPTION_LABELS["pool_mode"]:
        return OPTION_LABELS["pool_mode"][text]
    if key == "reason":
        return _translate_text(text)
    return text


def _localize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    for column in list(data.columns):
        data[column] = data[column].map(lambda value, key=column: _display_value(key, value))
    return data.rename(columns={column: _display_label(str(column)) for column in data.columns})


def _translate_text(text: str) -> str:
    replacements = [
        ("inside turtle holding rules", "未触发退出或加仓条件"),
        ("history unavailable:", "历史数据不可用："),
        ("2N stop:", "2N止损："),
        ("channel exit:", "通道退出："),
        ("0.5N add:", "0.5N加仓："),
        ("close", "收盘价"),
        ("stop", "止损价"),
        ("next add", "下次加仓价"),
        ("day low", "日低点"),
        ("broke", "突破"),
        ("day high", "日高点"),
        ("requires at least one symbol", "至少需要一个股票代码"),
        ("Enter at least one symbol.", "请输入至少一个股票代码。"),
        ("Invalid mark:", "标记价格格式无效："),
        ("no historical rows returned", "没有返回历史数据"),
        ("missing price", "缺少最新价"),
        ("missing previous close", "缺少昨收价"),
        ("buy signal but cash is insufficient for one lot or requested turtle unit", "出现买入信号，但现金不足以买入一手或策略建议单元"),
        ("buy signal but price is at limit-up", "出现买入信号，但当前价格已涨停"),
        ("wait for order book fill, otherwise buy next day only below", "可排队等待成交；否则次日只在不高于该价格时买入："),
        ("or switch to alternative", "或切换到备选标的"),
        ("buy signal but suggested shares exceed", "出现买入信号，但建议股数超过"),
        ("of quoted volume", "的盘口成交量限制"),
        ("buy signal is executable under current quote", "当前报价下买入信号可执行"),
    ]
    translated = text
    for old, new in replacements:
        translated = translated.replace(old, new)
    return translated


def input_text(
    name: str,
    label: str,
    value: str = "",
    form: dict[str, str] | None = None,
) -> str:
    value = _field_value(form, name, value)
    return f'<label>{html.escape(label)}<input name="{name}" value="{html.escape(value)}"></label>'


def input_number(
    name: str,
    label: str,
    value: str = "",
    form: dict[str, str] | None = None,
) -> str:
    value = _field_value(form, name, value)
    return f'<label>{html.escape(label)}<input type="number" step="any" name="{name}" value="{html.escape(value)}"></label>'


def select(
    name: str,
    values: tuple[str, ...],
    default: str,
    label: str,
    form: dict[str, str] | None = None,
    attrs: str = "",
) -> str:
    current = _field_value(form, name, default)
    options = []
    for value in values:
        selected = " selected" if value == current else ""
        display = OPTION_LABELS.get(name, {}).get(value, value)
        options.append(f'<option value="{value}"{selected}>{html.escape(display)}</option>')
    attr_text = f" {attrs.strip()}" if attrs.strip() else ""
    return f'<label>{html.escape(label)}<select name="{name}"{attr_text}>{"".join(options)}</select></label>'


def checkbox(
    name: str,
    label: str,
    form: dict[str, str] | None = None,
    checked: bool = False,
) -> str:
    current = checked if form is None or not form else name in form
    marker = " checked" if current else ""
    return f'<label class="check"><input type="checkbox" name="{name}"{marker}> {html.escape(label)}</label>'


def stock_pool_fields(form: dict[str, str] | None = None) -> str:
    form = form or {}
    source = _value(form, "stock_pool_source", "manual")
    fields = select("stock_pool_source", ("manual", "watchlist", "market_range", "lhb"), "manual", "股票池来源", form)
    fields = select(
        "stock_pool_source",
        ("manual", "watchlist", "market_range", "lhb"),
        "manual",
        "股票池来源",
        form,
        attrs='data-source-selector="stock_pool_source" onchange="refreshSourceFields(this)"',
    )
    if source == "watchlist":
        fields += _watchlist_select(form)
    elif source == "market_range":
        fields += market_range_fields(form)
    elif source in {"lhb", "ths_lhb"}:
        fields += lhb_source_fields(form)
    else:
        fields += manual_stock_pool_editor(form)
    return fields


def manual_stock_pool_editor(form: dict[str, str]) -> str:
    raw = _field_value(form, "symbols", "")
    pool = parse_manual_pool(raw) if raw.strip() else None
    recognized = pool.summary.filtered_count if pool is not None else 0
    warnings: list[str] = []
    if pool is not None:
        warnings.extend(pool.warnings)
        if pool.duplicates:
            warnings.append(f"重复代码：{', '.join(pool.duplicates)}")
    return f"""
    <details class="drawer manual-stock-pool-editor">
      <summary>编辑手动股票池</summary>
      <label>手动股票池<textarea name="symbols">{html.escape(raw)}</textarea></label>
      <p class="muted">支持逗号、空格、换行分隔。已识别股票数量：{recognized}</p>
      <p class="muted">{html.escape("；".join(warnings))}</p>
      <label class="check"><input type="radio" name="manual_pool_mode" value="once" checked> 仅本次使用</label>
      <p class="muted">保存为自选组合请到账户页的自选组合管理，或使用账户里的统一保存逻辑。</p>
    </details>
    """


def market_range_fields(form: dict[str, str]) -> str:
    selected = set(_value(form, "market_range", "all_a").split(","))
    options = ("all_a", "sh", "sz", "chinext", "star", "bj")
    checks = []
    for value in options:
        checked = " checked" if value in selected else ""
        label = OPTION_LABELS["market_range"][value]
        checks.append(f'<label class="check"><input type="checkbox" name="market_range" value="{value}"{checked}> {html.escape(label)}</label>')
    summary = "、".join(OPTION_LABELS["market_range"].get(value, value) for value in options if value in selected)
    return f'<div class="checkbox-group"><p class="muted">市场范围：{html.escape(summary or "未选择市场范围")}</p>{"".join(checks)}<p class="muted">大范围股票池可能耗时较长。</p></div>'


def lhb_source_fields(form: dict[str, str]) -> str:
    fields = select("lhb_range", ("1w", "1m", "3m", "half_year", "1y", "custom"), "1w", "龙虎榜时间范围", form)
    fields += select("lhb_confirmed_top", ("20", "30", "50"), "30", "运行候选数量", form)
    if _value(form, "lhb_range", "1w") == "custom":
        fields += input_text("lhb_start", "龙虎榜开始日期", "", form)
        fields += input_text("lhb_end", "龙虎榜结束日期", "", form)
    fields += '<p class="muted">结果会显示真实数据来源、时间范围、股票数量、错误或警告。</p>'
    return fields


def _watchlist_select(form: dict[str, str] | None = None) -> str:
    form = form or {}
    store = WatchlistStore(_value(form, "account_path", _value(form, "path", DEFAULT_USER_PATH)))
    values = tuple(item.name for item in store.list())
    if not values:
        return '<p class="muted">暂无自选组合，请到账户页创建</p>'
    current = _field_value(form, "watchlist_name", values[0])
    options = []
    for value in values:
        selected = " selected" if value == current else ""
        item = store.get(value)
        count = item.count if item is not None else 0
        options.append(f'<option value="{html.escape(value)}"{selected}>{html.escape(value)}（{count}只）</option>')
    return f'<label>自选股组合<select name="watchlist_name">{"".join(options)}</select></label>'


def render_watchlist_management(form: dict[str, str]) -> str:
    path = _value(form, "path", DEFAULT_USER_PATH)
    store = WatchlistStore(path)
    watchlists = _watchlists_frame(store)
    return f"""
      <section id="watchlists">
        <h2>自选股组合</h2>
        <p class="muted">自选股不是持仓；删除自选股不会影响账户现金、持仓或交易流水。</p>
        {render_table("Watchlists", watchlists) if not watchlists.empty else '<p class="muted">暂无自选组合</p>'}
        <div class="columns">
          <form method="post" action="/watchlist-create">
            <h3>创建组合</h3>
            {portfolio_path(form)}
            {input_text("watchlist_name", "组合名称", "", form)}
            <button type="submit">创建组合</button>
          </form>
          <form method="post" action="/watchlist-add-symbol">
            <h3>添加股票</h3>
            {portfolio_path(form)}
            {_watchlist_select(form)}
            {input_text("symbol", "股票代码", "", form)}
            <button type="submit">添加股票</button>
          </form>
          <form method="post" action="/watchlist-remove-symbol">
            <h3>删除股票</h3>
            {portfolio_path(form)}
            {_watchlist_select(form)}
            {input_text("symbol", "股票代码", "", form)}
            <button type="submit">删除股票</button>
          </form>
          <form method="post" action="/watchlist-rename">
            <h3>重命名组合</h3>
            {portfolio_path(form)}
            {_watchlist_select(form)}
            {input_text("new_watchlist_name", "新组合名称", "", form)}
            <button type="submit">重命名组合</button>
          </form>
          <form method="post" action="/watchlist-delete">
            <h3>删除组合</h3>
            {portfolio_path(form)}
            {_watchlist_select(form)}
            <button type="submit">删除组合</button>
          </form>
        </div>
      </section>
    """


def source_fields(form: dict[str, str] | None = None) -> str:
    return (
        select("source", ("", "baostock", "akshare", "joinquant"), "", "历史源", form)
        + select("stock_source", ("", "akshare", "baostock", "joinquant"), "", "股票列表源", form)
        + select("realtime_source", ("sina", "akshare"), "sina", "实时源", form)
        + checkbox("refresh", "强制刷新", form)
    )


def turtle_fields(form: dict[str, str] | None = None) -> str:
    return (
        input_number("risk_pct", "单元风险", "0.01", form)
        + input_number("s1_entry", "S1 入场", "20", form)
        + input_number("s1_exit", "S1 退出", "10", form)
        + input_number("s2_entry", "S2 入场", "55", form)
        + input_number("s2_exit", "S2 退出", "20", form)
        + input_number("atr_period", "ATR 周期", "20", form)
        + input_number("max_units", "最多单元", "4", form)
        + input_number("slippage_rate", "滑点", "0", form)
    )


def execution_fields(form: dict[str, str] | None = None) -> str:
    return input_number("next_day_premium", "次日溢价上限", "0.02", form) + input_number(
        "volume_limit_pct", "成交量限制", "0.10", form
    )


def portfolio_path(form: dict[str, str] | None = None) -> str:
    return input_text("path", "账户路径", DEFAULT_USER_PATH, form)


def trade_fields(side: str, form: dict[str, str] | None = None) -> str:
    reason = "entry_reason" if side == "buy" else "exit_reason"
    form = dict(form or {})
    form.setdefault("strategy_meta", "thermostat")
    form.setdefault("system", "trend_following")
    return (
        input_text("symbol", "股票代码", "", form)
        + input_number("price", "成交价", "", form)
        + input_number("shares", "股数", "", form)
        + (
            input_number("target_sell_price", "目标卖出价", "", form)
            if side == "buy"
            else ""
        )
        + '<details><summary>高级信息</summary><div class="grid">'
        + input_text("strategy_meta", "策略", "turtle_system", form)
        + input_text("system", "系统", "S1", form)
        + input_text(reason, "原因", "", form)
        + input_text("signal_date", "信号日", "", form)
        + input_text("execution_date", "执行日", "", form)
        + input_text("note", "备注", "", form)
        + "</div></details>"
    )


def _clear_trade_form(form: dict[str, str]) -> dict[str, str]:
    display = {"path": _value(form, "path", DEFAULT_USER_PATH)}
    for key in ["source", "stock_source", "realtime_source"]:
        value = _optional(form, key)
        if value is not None:
            display[key] = value
    return display


def _turtle_summary(form: dict[str, str], start: str, end: str, cash: float, portfolio) -> dict[str, object]:
    keys = [
        "pool_mode",
        "symbols",
        "lhb_start",
        "lhb_end",
        "account_path",
        "sync_holdings",
        "exclude_chinext",
        "source",
        "refresh",
    ]
    summary = _request_summary(form, keys)
    summary["start"] = start
    summary["end"] = end
    if portfolio is not None:
        summary["account_cash"] = cash
    else:
        summary["cash"] = cash
    for key in ["risk_pct", "s1_entry", "s1_exit", "s2_entry", "s2_exit"]:
        value = _optional(form, key)
        if value is not None:
            summary[key] = value
    return summary


def _resolve_turtle_universe(form: dict[str, str]) -> dict[str, object]:
    universe = _resolve_pool_universe(form, include_portfolio=_sync_holdings(form), cash_default=5000.0)
    return universe


def _resolve_backtest_universe(form: dict[str, str]) -> dict[str, object]:
    return _resolve_pool_universe(form, include_portfolio=False, cash_default=100000.0)


def _resolve_pool_universe(
    form: dict[str, str],
    include_portfolio: bool,
    cash_default: float,
) -> dict[str, object]:
    pool_mode = _value(form, "pool_mode", "manual")
    rows: list[dict[str, object]] = []
    lhb = pd.DataFrame(columns=["code", "name", "net_buy", "rank"])
    if pool_mode == "manual":
        for rank, symbol in enumerate(_symbols(form), start=1):
            normalized = normalize_symbol(symbol)
            rows.append({"symbol": normalized, "code": symbol_code(normalized), "name": "", "source": "manual", "rank": rank})
    elif pool_mode in {"lhb_top30", "lhb_top50"}:
        start = _value(form, "lhb_start")
        end = _value(form, "lhb_end")
        if not start or not end:
            raise ValueError("龙虎榜股票池需要填写龙虎榜开始日期和结束日期。")
        top = 30 if pool_mode == "lhb_top30" else 50
        lhb, _ranked = build_lhb_candidates(start, end, top)
        for row in lhb.itertuples(index=False):
            normalized = normalize_symbol(str(row.code))
            rows.append(
                {
                    "symbol": normalized,
                    "code": symbol_code(normalized),
                    "name": getattr(row, "name", ""),
                    "source": pool_mode,
                    "rank": getattr(row, "rank", None),
                    "net_buy": getattr(row, "net_buy", None),
                }
            )
    else:
        raise ValueError(f"未知的海龟股票池模式：{pool_mode}")

    portfolio = _load_portfolio(_value(form, "account_path", DEFAULT_USER_PATH)) if include_portfolio else None
    if portfolio is not None and not portfolio.positions.empty:
        existing = {str(row["symbol"]) for row in rows}
        for position in portfolio.positions.itertuples(index=False):
            symbol = normalize_symbol(str(position.symbol))
            if symbol not in existing:
                rows.append({"symbol": symbol, "code": symbol_code(symbol), "name": str(getattr(position, "name", "") or ""), "source": "portfolio_holding", "rank": None})
                existing.add(symbol)
    pool = pd.DataFrame(rows, columns=["symbol", "code", "name", "source", "rank", "net_buy"])
    if not pool.empty:
        pool = pool.drop_duplicates("symbol", keep="first").reset_index(drop=True)
    symbols = pool["symbol"].tolist() if not pool.empty else []
    cash = portfolio.cash if portfolio is not None else _float(form, "cash", cash_default)
    return {"symbols": symbols, "pool": pool, "lhb": lhb, "portfolio": portfolio, "cash": cash}


def _enrich_universe_names(service: MarketDataService, universe: dict[str, object]) -> dict[str, object]:
    pool = universe["pool"]
    if not isinstance(pool, pd.DataFrame) or pool.empty or "symbol" not in pool:
        return universe
    pool = pool.copy()
    missing = pool["name"].isna() | (pool["name"].astype(str).str.strip() == "")
    symbols = pool.loc[missing, "symbol"].dropna().astype(str).tolist()
    if not symbols:
        updated = dict(universe)
        updated["pool"] = pool
        return updated
    names = _stock_names(service, symbols)
    if names:
        pool.loc[missing, "name"] = pool.loc[missing, "symbol"].map(names).fillna(pool.loc[missing, "name"])
    updated = dict(universe)
    updated["pool"] = pool
    return updated


def _stock_names(service: MarketDataService, symbols: list[str]) -> dict[str, str]:
    normalized = [normalize_symbol(symbol) for symbol in symbols]
    names: dict[str, str] = {}
    for fetch in (
        lambda: service.get_realtime_quotes(normalized),
        lambda: service.get_market_snapshot(normalized),
    ):
        try:
            frame = fetch()
            names.update(_names_from_frame(frame))
        except Exception:
            pass
    missing = [symbol for symbol in normalized if symbol not in names]
    if missing:
        try:
            wanted = set(missing)
            for item in service.get_stock_symbols(refresh=False):
                if item.symbol in wanted and item.name:
                    names[item.symbol] = item.name
        except Exception:
            pass
    return names


def _names_from_frame(frame: pd.DataFrame) -> dict[str, str]:
    if frame is None or frame.empty or "symbol" not in frame or "name" not in frame:
        return {}
    result: dict[str, str] = {}
    for row in frame[["symbol", "name"]].to_dict("records"):
        name = str(row.get("name", "")).strip()
        if name and name.lower() != "nan":
            result[normalize_symbol(str(row.get("symbol", "")))] = name
    return result


def _exclude_chinext_from_universe(universe: dict[str, object]) -> dict[str, object]:
    pool = universe["pool"]
    if isinstance(pool, pd.DataFrame) and not pool.empty:
        pool = pool[~pool["code"].astype(str).str.startswith(("300", "301"))].reset_index(drop=True)
    lhb = universe["lhb"]
    if isinstance(lhb, pd.DataFrame) and not lhb.empty and "code" in lhb:
        lhb = lhb[~lhb["code"].astype(str).str.startswith(("300", "301"))].reset_index(drop=True)
    filtered = dict(universe)
    filtered["pool"] = pool
    filtered["lhb"] = lhb
    filtered["symbols"] = pool["symbol"].tolist() if isinstance(pool, pd.DataFrame) and not pool.empty else []
    return filtered


def _sync_holdings(form: dict[str, str]) -> bool:
    return form.get("sync_holdings", "on") != "off"


def _load_portfolio(path: str):
    try:
        return ManualPortfolioStore(path).load()
    except FileNotFoundError:
        return None


def _holding_advice(service: MarketDataService, portfolio, start_date: str, end_date: str, config: TurtleConfig, refresh: bool) -> pd.DataFrame:
    columns = ["symbol", "code", "name", "system", "shares", "avg_cost", "close", "n", "stop_price", "exit_price", "next_add_price", "action", "reason"]
    if portfolio is None or portfolio.positions.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    warmup_start = (pd.to_datetime(start_date) - pd.Timedelta(days=120)).strftime("%Y%m%d")
    for position in portfolio.positions.itertuples(index=False):
        symbol = normalize_symbol(str(position.symbol))
        system = str(getattr(position, "system", "") or "S1").upper()
        if system not in {"S1", "S2"}:
            system = "S1"
        avg_cost = float(getattr(position, "avg_cost", 0.0) or 0.0)
        base = {"symbol": symbol, "code": symbol_code(symbol), "name": str(getattr(position, "name", "") or ""), "system": system, "shares": int(getattr(position, "shares", 0) or 0), "avg_cost": avg_cost}
        try:
            history = _prepare_history(service.get_history(symbol, start_date=warmup_start, end_date=end_date, refresh=refresh, indicators=True))
            if history.empty:
                raise ValueError("没有返回历史数据")
            close = float(history.iloc[-1]["close"])
            n_value = _atr(history, config.atr_period)
            exit_price = _exit_price(history, system, config)
            stop_price = avg_cost - config.stop_atr * n_value if _is_number(n_value) else float("nan")
            next_add_price = avg_cost + config.add_unit_atr * n_value if _is_number(n_value) else float("nan")
            action = "hold"
            reason = "inside turtle holding rules"
            if _is_number(stop_price) and close <= stop_price:
                action = "sell"
                reason = f"2N stop: close {close:.2f} <= stop {stop_price:.2f}"
            elif _is_number(exit_price) and close < exit_price:
                action = "sell"
                window = config.s1_exit if system == "S1" else config.s2_exit
                reason = f"{system} channel exit: close {close:.2f} < {window}-day low {exit_price:.2f}"
            elif _is_number(next_add_price) and close >= next_add_price and int(config.max_units) > 1:
                action = "add"
                reason = f"0.5N add: close {close:.2f} >= next add {next_add_price:.2f}"
            rows.append({**base, "close": close, "n": n_value, "stop_price": stop_price, "exit_price": exit_price, "next_add_price": next_add_price, "action": action, "reason": reason})
        except Exception as exc:
            rows.append({**base, "close": pd.NA, "n": pd.NA, "stop_price": pd.NA, "exit_price": pd.NA, "next_add_price": pd.NA, "action": "hold", "reason": f"history unavailable: {exc}"})
    return pd.DataFrame(rows, columns=columns)


def _candidate_evaluation(
    service: MarketDataService,
    symbols: list[str],
    pool: pd.DataFrame,
    start_date: str,
    end_date: str,
    cash: float,
    config: TurtleConfig,
    refresh: bool,
    signals: pd.DataFrame,
) -> pd.DataFrame:
    columns = [
        "symbol",
        "code",
        "name",
        "date",
        "close",
        "n",
        "s1_breakout_price",
        "s2_breakout_price",
        "evaluation_action",
        "system",
        "score",
        "suggested_shares",
        "reason",
    ]
    if not symbols:
        return pd.DataFrame(columns=columns)
    pool_names = _pool_name_map(pool)
    signal_map = {
        str(row.symbol): row
        for row in signals.itertuples(index=False)
    } if signals is not None and not signals.empty else {}
    rows: list[dict[str, object]] = []
    warmup_start = (pd.to_datetime(start_date) - pd.Timedelta(days=120)).strftime("%Y%m%d")
    for symbol in symbols:
        normalized = normalize_symbol(symbol)
        code = symbol_code(normalized)
        signal = signal_map.get(normalized)
        if signal is not None:
            rows.append(
                {
                    "symbol": normalized,
                    "code": code,
                    "name": getattr(signal, "name", "") or pool_names.get(normalized, ""),
                    "date": getattr(signal, "date", ""),
                    "close": getattr(signal, "price", pd.NA),
                    "n": getattr(signal, "n", pd.NA),
                    "s1_breakout_price": pd.NA,
                    "s2_breakout_price": pd.NA,
                    "evaluation_action": "buy",
                    "system": getattr(signal, "system", ""),
                    "score": getattr(signal, "score", pd.NA),
                    "suggested_shares": getattr(signal, "suggested_shares", pd.NA),
                    "reason": getattr(signal, "reason", ""),
                }
            )
            continue
        try:
            history = _prepare_history(
                service.get_history(
                    normalized,
                    start_date=warmup_start,
                    end_date=end_date,
                    refresh=refresh,
                    indicators=True,
                )
            )
            if history.empty:
                raise ValueError("没有返回历史数据")
            close = float(history.iloc[-1]["close"])
            n_value = _atr(history, config.atr_period)
            s1_high = _breakout_high(history, config.s1_entry)
            s2_high = _breakout_high(history, config.s2_entry)
            unit = _unit_shares(cash, n_value, config)
            reason = _no_signal_reason(close, s1_high, s2_high, config)
            rows.append(
                {
                    "symbol": normalized,
                    "code": code,
                    "name": pool_names.get(normalized, ""),
                    "date": history.iloc[-1]["date"],
                    "close": close,
                    "n": n_value,
                    "s1_breakout_price": s1_high,
                    "s2_breakout_price": s2_high,
                    "evaluation_action": "no_signal",
                    "system": "",
                    "score": 0.0,
                    "suggested_shares": unit,
                    "reason": reason,
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "symbol": normalized,
                    "code": code,
                    "name": pool_names.get(normalized, ""),
                    "date": "",
                    "close": pd.NA,
                    "n": pd.NA,
                    "s1_breakout_price": pd.NA,
                    "s2_breakout_price": pd.NA,
                    "evaluation_action": "missing_history",
                    "system": "",
                    "score": 0.0,
                    "suggested_shares": 0,
                    "reason": f"历史数据不可用：{exc}",
                }
            )
    return pd.DataFrame(rows, columns=columns)


def _pool_name_map(pool: pd.DataFrame) -> dict[str, str]:
    if pool is None or pool.empty or "symbol" not in pool or "name" not in pool:
        return {}
    result: dict[str, str] = {}
    for row in pool[["symbol", "name"]].to_dict("records"):
        name = str(row.get("name", "")).strip()
        if name and name.lower() != "nan":
            result[normalize_symbol(str(row.get("symbol", "")))] = name
    return result


def _breakout_high(frame: pd.DataFrame, window: int) -> float:
    if len(frame) < window + 1:
        return float("nan")
    return float(frame["high"].iloc[-window - 1 : -1].max())


def _unit_shares(equity: float, n_value: float, config: TurtleConfig) -> int:
    if not _is_number(equity) or not _is_number(n_value) or n_value <= 0:
        return 0
    raw = float(equity) * config.risk_pct / float(n_value)
    return max(int(raw // config.lot_size) * config.lot_size, 0)


def _no_signal_reason(close: float, s1_high: float, s2_high: float, config: TurtleConfig) -> str:
    parts: list[str] = []
    if _is_number(s1_high):
        parts.append(f"收盘价 {close:.2f} 未突破 S1 {config.s1_entry}日高点 {s1_high:.2f}")
    else:
        parts.append(f"S1 {config.s1_entry}日突破历史数据不足")
    if _is_number(s2_high):
        parts.append(f"未突破 S2 {config.s2_entry}日高点 {s2_high:.2f}")
    else:
        parts.append(f"S2 {config.s2_entry}日突破历史数据不足")
    return "未触发买入：" + "；".join(parts)


def _prepare_history(history: pd.DataFrame) -> pd.DataFrame:
    frame = history.copy()
    if "date" in frame:
        frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    for column in ["open", "high", "low", "close", "volume"]:
        if column not in frame:
            frame[column] = pd.NA
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["date", "open", "high", "low", "close"]).sort_values("date").reset_index(drop=True)


def _atr(frame: pd.DataFrame, period: int) -> float:
    if len(frame) < period + 1:
        return float("nan")
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return float(tr.tail(period).mean())


def _exit_price(frame: pd.DataFrame, system: str, config: TurtleConfig) -> float:
    window = config.s1_exit if system == "S1" else config.s2_exit
    if len(frame) < window + 1:
        return float("nan")
    return float(frame["low"].iloc[-window - 1 : -1].min())


def _is_number(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _empty_turtle_result(start_date: str, end_date: str, cash: float) -> TurtleSystemResult:
    return TurtleSystemResult(
        summary=pd.DataFrame(columns=TURTLE_SUMMARY_COLUMNS),
        equity=pd.DataFrame(columns=TURTLE_EQUITY_COLUMNS),
        trades=pd.DataFrame(columns=TURTLE_TRADE_COLUMNS),
        positions=pd.DataFrame(columns=TURTLE_POSITION_COLUMNS),
        drawdowns=pd.DataFrame(columns=["start_date", "trough_date", "end_date", "max_drawdown"]),
        symbol_pnl=pd.DataFrame(columns=["symbol", "code", "name", "realized_pnl", "trades"]),
        signals=pd.DataFrame(columns=TURTLE_SIGNAL_COLUMNS),
        errors=pd.DataFrame(columns=["symbol", "name", "error"]),
    )


def _benchmark_summary(service: MarketDataService, start_date: str, end_date: str) -> dict[str, object]:
    try:
        frame = _prepare_history(
            service.get_index_history(
                "000001",
                start_date=start_date,
                end_date=end_date,
            ).rename(columns={"index_code": "symbol"})
        )
        if frame.empty:
            return {"benchmark_symbol": "000001.SH", "benchmark_return": pd.NA, "benchmark_error": "没有返回指数历史数据"}
        start_close = float(frame.iloc[0]["close"])
        end_close = float(frame.iloc[-1]["close"])
        benchmark_return = end_close / start_close - 1 if start_close else pd.NA
        return {"benchmark_symbol": "000001.SH", "benchmark_return": benchmark_return}
    except Exception as exc:
        return {"benchmark_symbol": "000001.SH", "benchmark_return": pd.NA, "benchmark_error": str(exc)}


def _augment_backtest_summary(summary: pd.DataFrame, benchmark: dict[str, object]) -> pd.DataFrame:
    if summary is None or summary.empty:
        summary = pd.DataFrame([{"strategy": "turtle_system"}])
    data = summary.copy()
    benchmark_return = benchmark.get("benchmark_return", pd.NA)
    data["benchmark_symbol"] = benchmark.get("benchmark_symbol", "000001.SH")
    data["benchmark_return"] = benchmark_return
    total_return = pd.to_numeric(data.get("total_return"), errors="coerce")
    if pd.notna(benchmark_return):
        data["excess_return"] = total_return - float(benchmark_return)
    else:
        data["excess_return"] = pd.NA
    if "benchmark_error" in benchmark:
        data["benchmark_error"] = benchmark["benchmark_error"]
    if "trade_count" in data:
        data["trade_note"] = data["trade_count"].map(lambda value: "未触发交易" if _safe_int(value) == 0 else "")
    return data


def _period_returns(equity: pd.DataFrame, frequency: str) -> pd.DataFrame:
    columns = ["period", "start_value", "end_value", "return", "max_drawdown", "position_utilization"]
    if equity is None or equity.empty:
        return pd.DataFrame(columns=columns)
    data = equity.copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    data["total_value"] = pd.to_numeric(data["total_value"], errors="coerce")
    data["drawdown"] = pd.to_numeric(data.get("drawdown"), errors="coerce")
    data["position_value"] = pd.to_numeric(data.get("position_value"), errors="coerce").fillna(0.0)
    data = data.dropna(subset=["date", "total_value"])
    if data.empty:
        return pd.DataFrame(columns=columns)
    data["period"] = data["date"].dt.to_period(frequency).astype(str)
    rows: list[dict[str, object]] = []
    for period, group in data.groupby("period", sort=True):
        start_value = float(group.iloc[0]["total_value"])
        end_value = float(group.iloc[-1]["total_value"])
        rows.append(
            {
                "period": period,
                "start_value": start_value,
                "end_value": end_value,
                "return": end_value / start_value - 1 if start_value else 0.0,
                "max_drawdown": float(group["drawdown"].min()) if group["drawdown"].notna().any() else 0.0,
                "position_utilization": float((group["position_value"] > 0).mean()),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _monthly_return_matrix(monthly: pd.DataFrame) -> pd.DataFrame:
    if monthly is None or monthly.empty or "period" not in monthly or "return" not in monthly:
        return pd.DataFrame(columns=["year"])
    data = monthly.copy()
    period = pd.PeriodIndex(data["period"].astype(str), freq="M")
    data["year"] = period.year
    data["month"] = period.month
    pivot = data.pivot(index="year", columns="month", values="return").reset_index()
    pivot.columns = [str(column) for column in pivot.columns]
    return pivot


def _trade_quality(trades: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "trade_count",
        "buy_count",
        "sell_count",
        "profit_count",
        "loss_count",
        "zero_count",
        "win_rate",
        "avg_profit",
        "avg_loss",
        "profit_loss_ratio",
        "expectancy",
    ]
    if trades is None or trades.empty:
        return pd.DataFrame([{column: 0 for column in columns}], columns=columns)
    sells = trades[trades["action"] == "sell"].copy()
    pnl = (
        pd.to_numeric(sells["realized_pnl"], errors="coerce").dropna()
        if not sells.empty and "realized_pnl" in sells
        else pd.Series(dtype=float)
    )
    profits = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    zeros = pnl[pnl == 0]
    avg_profit = float(profits.mean()) if len(profits) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    closed = len(profits) + len(losses) + len(zeros)
    return pd.DataFrame(
        [
            {
                "trade_count": len(trades),
                "buy_count": int((trades["action"].isin(["buy", "add"])).sum()) if "action" in trades else 0,
                "sell_count": len(sells),
                "profit_count": len(profits),
                "loss_count": len(losses),
                "zero_count": len(zeros),
                "win_rate": len(profits) / closed if closed else 0.0,
                "avg_profit": avg_profit,
                "avg_loss": avg_loss,
                "profit_loss_ratio": abs(avg_profit / avg_loss) if avg_loss else 0.0,
                "expectancy": float(pnl.mean()) if len(pnl) else 0.0,
            }
        ],
        columns=columns,
    )


def _holding_distribution(trades: pd.DataFrame) -> pd.DataFrame:
    columns = ["bucket", "trade_count", "average_holding_days"]
    days = _closed_holding_days(trades)
    if not days:
        return pd.DataFrame(columns=columns)
    buckets = [
        ("0-5天", lambda value: value <= 5),
        ("6-20天", lambda value: 6 <= value <= 20),
        ("21-60天", lambda value: 21 <= value <= 60),
        ("60天以上", lambda value: value > 60),
    ]
    rows = []
    for label, matcher in buckets:
        matched = [value for value in days if matcher(value)]
        rows.append(
            {
                "bucket": label,
                "trade_count": len(matched),
                "average_holding_days": sum(matched) / len(matched) if matched else 0.0,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _closed_holding_days(trades: pd.DataFrame) -> list[int]:
    if trades is None or trades.empty:
        return []
    entries: dict[str, pd.Timestamp] = {}
    days: list[int] = []
    for row in trades.itertuples(index=False):
        action = str(getattr(row, "action", ""))
        symbol = str(getattr(row, "symbol", ""))
        date = pd.to_datetime(getattr(row, "date", None), errors="coerce")
        if pd.isna(date):
            continue
        if action in {"buy", "add"} and symbol not in entries:
            entries[symbol] = date
        elif action == "sell" and symbol in entries:
            days.append(max((date - entries.pop(symbol)).days, 0))
    return days


def _backtest_candidate_difference(pool: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    columns = ["symbol", "code", "name", "source", "rank", "net_buy", "traded", "buy_count", "sell_count", "realized_pnl", "reason"]
    if pool is None or pool.empty:
        return pd.DataFrame(columns=columns)
    data = pool.copy()
    if trades is None or trades.empty:
        data["traded"] = "no"
        data["buy_count"] = 0
        data["sell_count"] = 0
        data["realized_pnl"] = 0.0
        data["reason"] = "未触发任何成交"
        return data.reindex(columns=columns)
    trades = trades.copy()
    grouped = trades.groupby("symbol", as_index=False).agg(
        buy_count=("action", lambda values: int(values.isin(["buy", "add"]).sum())),
        sell_count=("action", lambda values: int((values == "sell").sum())),
        realized_pnl=("realized_pnl", lambda values: float(pd.to_numeric(values, errors="coerce").fillna(0.0).sum())),
    )
    data = data.merge(grouped, how="left", on="symbol")
    data["buy_count"] = data["buy_count"].fillna(0).astype(int)
    data["sell_count"] = data["sell_count"].fillna(0).astype(int)
    data["realized_pnl"] = data["realized_pnl"].fillna(0.0)
    data["traded"] = data["buy_count"].map(lambda value: "yes" if value else "no")
    data["reason"] = data["buy_count"].map(lambda value: "已触发成交" if value else "未触发买入或资金不足")
    return data.reindex(columns=columns)


def _safe_int(value: object) -> int:
    try:
        if value is None or pd.isna(value):
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _quote_marks(service: MarketDataService, symbols: list[str]) -> dict[str, float]:
    if not symbols:
        return {}
    quotes = service.get_realtime_quotes(symbols)
    if quotes is None or quotes.empty or "symbol" not in quotes or "price" not in quotes:
        return {}
    marks: dict[str, float] = {}
    for row in quotes.itertuples(index=False):
        price = getattr(row, "price", None)
        if _is_number(price):
            marks[normalize_symbol(str(row.symbol))] = float(price)
    return marks


def _portfolio_tables(portfolio, marks: dict[str, float] | None = None) -> list[TableBlock]:
    return [
        TableBlock("Positions", _positions_view(portfolio.positions, marks)),
        TableBlock("Trades", _trades_view(portfolio.trades.tail(50))),
    ]


def _positions_view(positions: pd.DataFrame, marks: dict[str, float] | None = None) -> pd.DataFrame:
    if positions is None or positions.empty:
        return positions
    marks = {normalize_symbol(k): float(v) for k, v in (marks or {}).items()}
    data = positions.copy()
    data["mark_price"] = data.apply(lambda row: marks.get(row["symbol"], row["avg_cost"]), axis=1)
    data["market_value"] = pd.to_numeric(data["shares"], errors="coerce").fillna(0) * pd.to_numeric(data["mark_price"], errors="coerce").fillna(0)
    data["unrealized_pnl"] = data["market_value"] - pd.to_numeric(data["shares"], errors="coerce").fillna(0) * pd.to_numeric(data["avg_cost"], errors="coerce").fillna(0)
    return _prioritize_columns(
        data,
        [
            "symbol",
            "code",
            "name",
            "shares",
            "avg_cost",
            "mark_price",
            "market_value",
            "unrealized_pnl",
            "strategy",
            "system",
            "target_sell_price",
            "entry_reason",
            "signal_date",
            "execution_date",
        ],
    )


def _trades_view(trades: pd.DataFrame) -> pd.DataFrame:
    return _prioritize_columns(
        trades,
        [
            "timestamp",
            "symbol",
            "code",
            "name",
            "side",
            "price",
            "shares",
            "fees",
            "tax",
            "cash_after",
            "realized_pnl",
            "strategy",
            "system",
            "note",
        ],
    )


def _prioritize_columns(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    ordered = [column for column in columns if column in frame.columns]
    ordered.extend(column for column in frame.columns if column not in ordered)
    return frame[ordered]


def _service(form: dict[str, str]) -> MarketDataService:
    config = DataSourceConfig(
        history_source=_optional(form, "source"),
        stock_source=_optional(form, "stock_source"),
        realtime_source=_optional(form, "realtime_source"),
    )
    if not any([config.history_source, config.stock_source, config.realtime_source]):
        return MarketDataService()
    return MarketDataService(data_source_config=config)


def _lookup_stock_name(symbol: str, form: dict[str, str]) -> str:
    normalized = normalize_symbol(symbol)
    service = _service(form)
    try:
        quotes = service.get_realtime_quotes([normalized])
        name = _first_name(quotes)
        if name:
            return name
    except Exception:
        pass
    try:
        snapshot = service.get_market_snapshot([normalized])
        name = _first_name(snapshot)
        if name:
            return name
    except Exception:
        pass
    try:
        for item in service.get_stock_symbols(refresh=False):
            if item.symbol == normalized and item.name:
                return item.name
    except Exception:
        pass
    return ""


def _first_name(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty or "name" not in frame:
        return ""
    for value in frame["name"].dropna().astype(str):
        if value.strip():
            return value.strip()
    return ""


def _turtle_config(form: dict[str, str]) -> TurtleConfig:
    return TurtleConfig(
        s1_entry=_int(form, "s1_entry", 20),
        s1_exit=_int(form, "s1_exit", 10),
        s2_entry=_int(form, "s2_entry", 55),
        s2_exit=_int(form, "s2_exit", 20),
        atr_period=_int(form, "atr_period", 20),
        risk_pct=_float(form, "risk_pct", 0.01),
        max_units=_int(form, "max_units", 4),
        slippage_rate=_float(form, "slippage_rate", 0.0),
    )


def _symbols(form: dict[str, str]) -> list[str]:
    raw = _optional(form, "symbols") or ""
    return [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]


def _require_symbols(form: dict[str, str]) -> list[str]:
    symbols = _symbols(form)
    if not symbols:
        raise ValueError("请输入至少一个股票代码。")
    return symbols


def _marks(form: dict[str, str]) -> dict[str, float]:
    raw = _optional(form, "marks") or ""
    result: dict[str, float] = {}
    for item in raw.replace("\n", ",").split(","):
        if not item.strip():
            continue
        if "=" not in item:
            raise ValueError(f"标记价格格式无效：{item}")
        symbol, price = item.split("=", 1)
        result[symbol.strip()] = float(price.strip())
    return result


def _checked(form: dict[str, str], key: str) -> bool:
    return key in form


def _page_for_path(path: str) -> str:
    if path in {
        "/thermostat",
        "/portfolio-buy",
        "/portfolio-sell",
        "/portfolio-adjust-cost",
        "/portfolio-init",
        "/portfolio-summary",
        "/watchlist-create",
        "/watchlist-add-symbol",
        "/watchlist-remove-symbol",
        "/watchlist-rename",
        "/watchlist-delete",
    }:
        return "portfolio" if path.startswith("/portfolio") else "thermostat"
    if path == "/thermostat-backtest":
        return "backtest"
    return "thermostat"


def _query_form(query: str) -> dict[str, str]:
    values = parse_qs(query, keep_blank_values=True)
    return {key: ",".join(value) if key == "market_range" else value[-1] for key, value in values.items()}


def _field_value(form: dict[str, str] | None, key: str, default: str) -> str:
    if form is None:
        return default
    return form.get(key, default)


def _request_summary(form: dict[str, str], keys: list[str]) -> dict[str, object]:
    values: dict[str, object] = {}
    for key in keys:
        if key == "refresh":
            values[key] = "yes" if key in form else "no"
            continue
        value = _optional(form, key)
        if value is not None:
            values[key] = value
    return values


def _resolve_thermostat_stock_pool(form: dict[str, str], service: MarketDataService):
    source = _value(form, "stock_pool_source", "manual")
    account_path = _value(form, "account_path", _value(form, "path", DEFAULT_USER_PATH))
    exclude_star = _checked(form, "exclude_star")
    if source == "watchlist":
        return resolve_watchlist_pool(WatchlistStore(account_path), _value(form, "watchlist_name"), exclude_star=exclude_star)
    if source == "market_range":
        try:
            stocks = service.get_stock_symbols(refresh=_checked(form, "refresh"))
        except Exception as exc:
            return _stock_pool_error("market_range", "市场范围", f"市场范围股票列表获取失败：{exc}")
        source_detail = _stock_source_detail(service)
        return resolve_market_range_pool(stocks, _value(form, "market_range", "all_a"), source_detail=source_detail, updated_at=_today_yyyymmdd(), exclude_star=exclude_star)
    if source in {"lhb", "ths_lhb"}:
        confirmed = _optional(form, "confirmed_lhb_symbols")
        if confirmed:
            top = _int(form, "lhb_confirmed_top", 30)
            symbols = ",".join([item.strip() for item in confirmed.split(",") if item.strip()][:top])
            return parse_manual_pool(symbols, name="龙虎榜确认候选池", exclude_star=exclude_star)
        top = _int(form, "lhb_confirmed_top", 30)
        range_key = _value(form, "lhb_range", "1w")
        try:
            start, end = lhb_range_dates(range_key, as_of=_value(form, "end", _today_yyyymmdd()), start_date=_value(form, "lhb_start"), end_date=_value(form, "lhb_end"))
        except Exception as exc:
            return _stock_pool_error("lhb", "龙虎榜", str(exc))
        requested_source = "ths" if source == "ths_lhb" else "eastmoney"
        return resolve_lhb_pool(
            lambda start_date, end_date: build_lhb_candidates(start_date, end_date, top)[0],
            start_date=start,
            end_date=end,
            requested_source=requested_source,
            actual_source="东方财富龙虎榜",
            exclude_star=exclude_star,
        )
    return parse_manual_pool(_value(form, "symbols"), exclude_star=exclude_star)


def _stock_pool_error(source: str, name: str, message: str):
    from stock_picker.pools import StockPoolResult, StockPoolSummary

    summary = StockPoolSummary(source=source, name=name, original_count=0, deduped_count=0, filtered_count=0, removed_count=0)
    return StockPoolResult([], summary, errors=[message])


def _stock_source_detail(service: MarketDataService) -> str:
    result = getattr(service, "last_source_results", {}).get("stock")
    if result is None:
        return "现有股票列表"
    return result.source


def _stock_pool_error_result(pool) -> RenderResult:
    return RenderResult(
        "股票池错误",
        summaries=[_pool_summary_dict(pool)],
        tables=[TableBlock("Stock Pool Summary", _pool_summary_frame(pool))],
    )


def _pool_summary_frame(pool) -> pd.DataFrame:
    return pd.DataFrame([_pool_summary_dict(pool)])


def _pool_summary_dict(pool) -> dict[str, object]:
    summary = pool.summary
    return {
        "stock_pool_source": summary.source,
        "name": summary.name,
        "time_range": summary.time_range,
        "source_detail": summary.source_detail,
        "original_count": summary.original_count,
        "deduped_count": summary.deduped_count,
        "filtered_count": summary.filtered_count,
        "removed_count": summary.removed_count,
        "warnings": "；".join(pool.warnings),
        "errors": "；".join(pool.errors),
    }


def _watchlists_frame(store: WatchlistStore) -> pd.DataFrame:
    rows = [
        {
            "name": item.name,
            "symbols": ",".join(item.symbols),
            "filtered_count": len([symbol for symbol in item.symbols if is_supported_stock_symbol(symbol)]),
            "invalid_symbols": ", ".join(_watchlist_invalid_symbols(item.symbols)),
            "warnings": _watchlist_warning(item.symbols),
            "updated_at": item.updated_at,
        }
        for item in store.list()
    ]
    return pd.DataFrame(rows, columns=["name", "symbols", "filtered_count", "invalid_symbols", "warnings", "updated_at"])


def _watchlist_invalid_symbols(symbols: list[str]) -> list[str]:
    return [symbol for symbol in symbols if not is_supported_stock_symbol(symbol)]


def _watchlist_warning(symbols: list[str]) -> str:
    invalid = _watchlist_invalid_symbols(symbols)
    if not invalid:
        return ""
    return f"存在异常代码，请删除后重新添加：{', '.join(invalid)}"


def _thermostat_display_form(form: dict[str, str]) -> dict[str, str]:
    display = dict(form)
    path = _value(display, "account_path", DEFAULT_USER_PATH)
    if not _optional(display, "symbols"):
        last = WatchlistStore(path).load_last_manual_input()
        if last:
            display["symbols"] = last
    return display


def _today_yyyymmdd() -> str:
    return datetime.now().strftime("%Y%m%d")


def _optional(form: dict[str, str], key: str) -> str | None:
    value = form.get(key, "").strip()
    return value or None


def _value(form: dict[str, str], key: str, default: str = "") -> str:
    value = form.get(key, "").strip()
    return value or default


def _int(form: dict[str, str], key: str, default: int) -> int:
    value = _optional(form, key)
    return int(value) if value is not None else default


def _float(form: dict[str, str], key: str, default: float) -> float:
    value = _optional(form, key)
    return float(value) if value is not None else default


def _optional_float(form: dict[str, str], key: str) -> float | None:
    value = _optional(form, key)
    return float(value) if value is not None else None


class TableBlock:
    def __init__(self, title: str, frame: pd.DataFrame) -> None:
        self.title = title
        self.frame = frame


class RenderResult:
    def __init__(
        self,
        title: str,
        tables: list[TableBlock] | None = None,
        summaries: list[dict[str, object]] | None = None,
        extra_html: str = "",
    ) -> None:
        self.title = title
        self.tables = tables or []
        self.summaries = summaries or []
        self.extra_html = extra_html


CSS = """
:root {
  color-scheme: light;
  --line: #d8dee7;
  --text: #1f2937;
  --muted: #607086;
  --bg: #f6f8fb;
  --panel: #ffffff;
  --accent: #0f766e;
  --danger: #b42318;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  overflow-x: hidden;
  background: var(--bg);
  color: var(--text);
  font-family: "Segoe UI", Arial, sans-serif;
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 28px;
  border-bottom: 1px solid var(--line);
  background: var(--panel);
}
h1, h2, h3, p { margin-top: 0; }
h1 { font-size: 24px; margin-bottom: 4px; }
h2 { font-size: 18px; }
h3 { font-size: 15px; margin: 16px 0 8px; }
header p, .muted, .status { color: var(--muted); }
nav {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  gap: 18px;
  padding: 10px 28px;
  border-bottom: 1px solid var(--line);
  background: #eef3f7;
}
nav a { color: var(--text); text-decoration: none; font-weight: 600; }
nav a.active {
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 6px;
}
main {
  max-width: 1280px;
  margin: 0 auto;
  padding: 18px 28px 40px;
}
section, .message {
  max-width: 100%;
  margin-bottom: 18px;
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
}
.workbench-page {
  display: grid;
  gap: 18px;
}
.workspace-section,
.account-overview,
.account-summaries,
.function-tabs {
  width: 100%;
}
.page-head {
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--line);
}
.panel-block {
  margin-bottom: 16px;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fbfcfe;
}
.overview-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
}
.overview-card {
  min-height: 74px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fbfcfe;
}
.overview-card span {
  display: block;
  color: var(--muted);
  font-size: 12px;
}
.overview-card strong {
  display: block;
  margin-top: 8px;
  font-size: 16px;
}
.tab-labels {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.tab-labels span,
.secondary-action {
  display: inline-flex;
  align-items: center;
  min-height: 30px;
  padding: 4px 10px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #f2f5f8;
  color: var(--text);
  text-decoration: none;
  font-size: 13px;
}
.message-inline {
  margin: 8px 0;
  padding: 10px 12px;
  border-left: 4px solid var(--accent);
  background: #f2f8f7;
}
.message-inline.error {
  border-left-color: var(--danger);
  background: #fff4f2;
}
details {
  margin: 10px 0;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
}
summary {
  cursor: pointer;
  font-weight: 700;
}
textarea {
  width: 100%;
  min-height: 120px;
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  font: inherit;
}
.message { border-left: 4px solid var(--accent); }
.message.error { border-left-color: var(--danger); }
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 12px;
}
.columns {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 16px;
  margin-top: 12px;
}
label {
  display: flex;
  flex-direction: column;
  gap: 5px;
  color: var(--muted);
  font-size: 13px;
}
label.check {
  display: inline-flex;
  flex-direction: row;
  align-items: center;
  margin: 12px 16px 0 0;
}
input, select, textarea {
  width: 100%;
  min-height: 36px;
  padding: 7px 9px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
  color: var(--text);
  font: inherit;
}
button {
  margin-top: 14px;
  min-height: 38px;
  padding: 0 14px;
  border: 0;
  border-radius: 6px;
  background: var(--accent);
  color: #fff;
  font-weight: 700;
  cursor: pointer;
}
.summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 8px 16px;
  margin: 8px 0 16px;
}
.summary dt { color: var(--muted); font-size: 12px; }
.summary dd { margin: 0; font-weight: 700; }
.table-wrap {
  max-width: 100%;
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 6px;
}
.data-table {
  width: 100%;
  min-width: max-content;
  border-collapse: collapse;
  font-size: 13px;
}
.data-table th, .data-table td {
  padding: 7px 8px;
  border-bottom: 1px solid var(--line);
  text-align: left;
  vertical-align: top;
  white-space: nowrap;
}
.data-table th {
  background: #f2f5f8;
}
h3 span { color: var(--muted); font-weight: 400; }
@media (max-width: 720px) {
  header, nav { padding-left: 16px; padding-right: 16px; }
  main { padding: 14px 16px 32px; }
  .columns { grid-template-columns: 1fr; }
}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local Stock Picker web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), WebAppHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"Stock Picker local web app running at {url}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
