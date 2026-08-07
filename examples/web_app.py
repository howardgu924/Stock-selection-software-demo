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
)
from stock_picker.reporting.t1_thermostat_report import (
    build_t1_thermostat_report,
    default_t1_thermostat_report_filename,
    export_t1_thermostat_excel,
)
from stock_picker.user import ManualPortfolioStore, WatchlistStore
from stock_picker.strategies.adaptive_trend_v1_3.phase6_controller import Phase6Controller
from stock_picker.strategies.adaptive_trend_v1_3.phase6_app_factory import (
    create_phase6_application,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase6_models import ErrorVM
from stock_picker.strategies.adaptive_trend_v1_3.phase6_web import (
    PHASE6_PAGES,
    Phase6WebState,
    handle_phase6_action,
    phase6_nav,
    refresh_snapshot_state,
    render_phase6_page,
    update_selection,
)
from examples.list_lhb_candidates import build_lhb_candidates


DEFAULT_PORT = 8765
DEFAULT_USER_PATH = "data/user/default"
REPORT_DIR = Path("data/reports")
LAST_FORM: dict[str, str] = {}
JOBS: dict[str, "ThermostatJob"] = {}
JOBS_LOCK = threading.Lock()
PAGES = {"thermostat", "backtest", "portfolio", *PHASE6_PAGES}
LEGACY_GET_PATHS = {
    "/thermostat", "/backtest", "/portfolio", "/job", "/thermostat-report",
}
LEGACY_POST_PATHS = {
    "/thermostat", "/thermostat-lhb-preview", "/thermostat-job",
    "/thermostat-backtest", "/thermostat-backtest-job",
    "/portfolio-init", "/portfolio-buy", "/portfolio-sell",
    "/portfolio-adjust-cost", "/portfolio-summary",
    "/watchlist-save-manual", "/watchlist-create", "/watchlist-add-symbol",
    "/watchlist-remove-symbol", "/watchlist-rename", "/watchlist-delete",
}
PHASE6_CONTROLLER: Phase6Controller | None = None
PHASE6_STARTUP_ERROR: ErrorVM | None = None
PHASE6_STATE = Phase6WebState()
PHASE6_STATES: dict[str, Phase6WebState] = {}
PHASE6_STATES_LOCK = threading.Lock()


def configure_phase6(controller: Phase6Controller) -> None:
    """Attach a production Phase 5 service composition to the local web UI."""
    global PHASE6_CONTROLLER
    PHASE6_CONTROLLER = controller
APP_NAME = "选股工作台"

TITLE_LABELS = {
    "Strategy": "策略运行",
    "Results": "策略结果",
    "Signals": "信号",
    "Errors": "错误",
    "Final Pool": "最终股票池",
    "LHB Ranking": "龙虎榜排名",
    "Holding Advice": "持仓建议",
    "New Buy Signals": "新买入信号",
    "Execution Plan": "手工执行计划",
    "Candidate Evaluation": "候选评估",
    "Summary": "摘要",
    "Market Overview": "市场概览",
    "New Buy Candidates": "新买候选",
    "Grid Advice": "网格建议",
    "Trend Advice": "趋势建议",
    "Trigger Plan": "Trigger Plan",
    "LHB Top 20": "龙虎榜前 20 名",
    "LHB Top 30": "龙虎榜前 30 名",
    "LHB Top 50": "龙虎榜前 50 名",
    "Regime Performance": "市场状态表现",
    "Diagnostics": "诊断明细",
    "LHB Candidate Preview": "龙虎榜候选预览",
    "Thermostat Job Started": "恒温器任务已开始",
    "Trades": "交易流水",
    "Daily Portfolio": "每日账户",
    "Daily Evaluation Detail": "每日评估明细",
    "Symbol Performance": "个股表现",
    "Data Quality": "数据质量",
    "Parameters": "参数来源",
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
    "run_backtest": "正在运行恒温器回测",
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
    "updated_at": "更新时间",
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
        "actual_shares": "实际股数",
        "actual_lhb_range": "实际龙虎榜日期范围",
        "average_after_switch_return": "切换后平均收益",
        "available_shares": "可用股数",
        "available_shares_after": "操作后可用股数",
        "backtest_type": "回测类型",
        "cash_ratio": "现金比例",
        "cash_before": "操作前现金",
        "cash_end": "期末现金",
        "cash_start": "期初现金",
        "candidate_count": "候选数量",
        "entry_price": "入场价",
        "evidence": "判断依据",
        "execution_price": "成交价格",
        "execution_status": "执行状态",
        "execution_time": "执行时间点",
        "failure_reason": "失败原因",
        "grid_invalid_count": "网格失效次数",
        "grid_lower": "网格下沿",
        "grid_max_layers": "最大网格层数",
        "grid_mid": "网格中枢",
        "grid_stop_condition": "网格停止条件",
        "grid_unit_pct": "单格仓位比例",
        "grid_upper": "网格上沿",
        "job_id": "任务编号",
        "gross_amount": "成交总额",
        "intended_shares": "计划股数",
        "message": "进度说明",
        "net_amount": "净额",
        "node": "当前节点",
        "note": "备注",
        "order_status": "订单状态",
        "parameter_name": "参数",
        "parameter_source": "参数来源",
        "parameter_value": "参数值",
        "period_count": "周期数",
        "position_after": "操作后持仓",
        "position_before": "操作前持仓",
        "position_value_end": "期末持仓市值",
        "position_value_start": "期初持仓市值",
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
        "time_point": "时间点",
        "top_options": "可选排名范围",
        "total_commission": "总佣金",
        "total_shares": "总股数",
        "total_slippage_cost": "总滑点成本",
        "total_value_end": "期末总资产",
        "total_value_start": "期初总资产",
        "trade_id": "交易编号",
        "trade_reason": "交易原因",
        "user_overridden": "用户覆盖",
        "warning": "警告",
        "trend_stop_count": "趋势止损次数",
    }
)

COLUMN_LABELS.update(
    {
        "stock_mode": "股票模式",
        "market_regime_normalized": "市场折扣档位",
        "market_position_discount": "仓位折扣",
        "boll_upper": "布林上轨",
        "boll_mid": "布林中轨",
        "boll_lower": "布林下轨",
        "atr20": "ATR20",
        "trend_buy_trigger": "趋势买入触发价",
        "trend_reduce_trigger": "趋势减仓触发价",
        "trend_exit_trigger": "趋势退出触发价",
        "trend_batches": "趋势分批",
        "grid_buy_levels": "网格买入层",
        "grid_sell_levels": "网格卖出层",
        "grid_total_max_position_pct": "网格总仓位上限",
        "target_position_pct": "目标仓位",
        "max_position_pct": "单股仓位上限",
        "available_shares": "可卖股数",
        "today_bought_shares": "今日买入股数",
        "total_shares": "总股数",
        "share_split_source": "股数拆分来源",
        "pending_sell_level": "待卖级别",
        "trigger_status": "触发状态",
        "filled_status": "成交状态",
        "failed_reason": "失败原因",
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
    "available_shares",
    "today_bought_shares",
    "total_shares",
    "grid_max_layers",
}

MONEY_DISPLAY_COLUMNS = {
    "account_cash",
    "avg_cost",
    "cash",
    "cash_after",
    "cash_before",
    "cash_end",
    "cash_start",
    "close",
    "end_value",
    "entry_price",
    "estimated_cost",
    "execution_price",
    "exit_price",
    "fees",
    "final_value",
    "gross_amount",
    "initial_cash",
    "last_entry_price",
    "limit_up_price",
    "mark_price",
    "market_value",
    "min_commission",
    "net_amount",
    "next_add_price",
    "next_day_max_price",
    "position_value",
    "position_value_end",
    "position_value_start",
    "prev_close",
    "price",
    "principal",
    "realized_pnl",
    "reference_price",
    "s1_breakout_price",
    "s2_breakout_price",
    "start_value",
    "stop_price",
    "suggested_price",
    "target_price",
    "target_sell_price",
    "tax",
    "total_asset",
    "total_commission",
    "total_pnl",
    "total_slippage_cost",
    "total_value",
    "total_value_end",
    "total_value_start",
    "unrealized_pnl",
}

PERCENT_DISPLAY_COLUMNS = {
    "annualized_return",
    "annualized_excess_return",
    "annualized_volatility",
    "benchmark_return",
    "cash_ratio",
    "daily_return",
    "drawdown",
    "excess_return",
    "limit_pct",
    "position_utilization",
    "realized_pnl_pct",
    "return",
    "risk_pct",
    "slippage_rate",
    "suggested_position_pct",
    "target_position_pct",
    "max_position_pct",
    "market_position_discount",
    "grid_total_max_position_pct",
    "total_return",
    "volume_limit_pct",
    "weight",
    "win_rate",
}

DEFAULT_HIDDEN_COLUMNS = {
    "signal_time",
    "order_status",
    "slippage_cost",
    "shares_after",
}

TABLE_HIDDEN_COLUMNS = {
    "Trades": DEFAULT_HIDDEN_COLUMNS | {"execution_status"},
    "交易流水": DEFAULT_HIDDEN_COLUMNS | {"execution_status"},
}

STOCK_LEVEL_TABLES = {
    "Trades",
    "Positions",
    "Symbol Performance",
    "Data Quality",
    "New Buy Candidates",
    "Holding Advice",
    "Grid Advice",
    "Trend Advice",
    "Backtest Pool",
    "Backtest Candidate Difference",
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
    "backtest_date_range": {
        "1m": "最近 1 个月",
        "3m": "最近 3 个月",
        "5m": "最近 5 个月",
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
        "thermostat": "恒温器",
        "trend_following": "趋势跟随",
        "grid": "网格策略",
        "manual": "手动记录",
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
    server_version = "StockPickerWeb/1.2.0"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._send_text("ok")
            return
        if path in LEGACY_GET_PATHS and not self._legacy_features_enabled():
            self._send_legacy_hidden()
            return
        if path == "/job":
            job_id = parse_qs(parsed.query).get("id", [""])[-1]
            self._send_json(job_status_payload(job_id))
            return
        if path == "/thermostat-report":
            job_id = parse_qs(parsed.query).get("id", [""])[-1]
            self._send_thermostat_report(job_id)
            return
        if path == "/adaptive-v13-report-file":
            query = parse_qs(parsed.query)
            run_id = query.get("run_id", [""])[-1]
            name = query.get("name", [""])[-1]
            if PHASE6_CONTROLLER is None:
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Phase 6 service unavailable")
                return
            try:
                self._send_phase6_report(
                    PHASE6_CONTROLLER.validate_report_file(run_id, name)
                )
            except Exception as exc:
                view = PHASE6_CONTROLLER.get_error_view(exc)
                self.send_error(HTTPStatus.BAD_REQUEST, f"{view.title} [{view.code}]")
            return
        if path == "/":
            self._send_page(render_page(
                page="adaptive-v13-overview", form={},
                phase6_state=self._phase6_state(),
                phase6_error=PHASE6_STARTUP_ERROR,
            ))
            return
        page = path.strip("/")
        if page not in PAGES:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        if page in PHASE6_PAGES:
            phase6_state = self._phase6_state()
            phase6_query = parse_qs(parsed.query)
            if page in {
                "adaptive-v13-cache","adaptive-v13-backtest","adaptive-v13-paper",
            } and ({"universe_kind","date_kind"} & set(phase6_query)):
                update_selection(
                    phase6_state,
                    {
                        key: ",".join(values) if key == "market_scopes" else values[-1]
                        for key,values in phase6_query.items() if values
                    },
                )
            if phase6_query.get("run_id"):
                phase6_state.run_id = phase6_query["run_id"][-1]
            if page == "adaptive-v13-runs":
                allowed = {
                    "mode","status","date_from","date_to","account","strategy_version",
                    "has_open_positions","degraded","page","page_size",
                }
                submitted_filters = {
                    key: values[-1] for key,values in phase6_query.items()
                    if key in allowed and values
                }
                if submitted_filters:
                    phase6_state.run_filters = submitted_filters
                    if not phase6_query.get("run_id"):
                        phase6_state.run_id = ""
            if PHASE6_CONTROLLER is not None and page in {
                "adaptive-v13-cache","adaptive-v13-backtest","adaptive-v13-paper",
            }:
                refresh_snapshot_state(
                    PHASE6_CONTROLLER,phase6_state,
                    "DAILY_PAPER" if page == "adaptive-v13-paper" else "BACKTEST",
                )
            self._send_page(render_page(
                page=page, form={}, phase6_state=phase6_state,
                phase6_error=PHASE6_STARTUP_ERROR,
            ))
            return
        query_form = _query_form(parsed.query)
        display_form = _display_form_for_page(page, {**LAST_FORM, **query_form} if query_form else LAST_FORM)
        result = None
        if page == "portfolio":
            try:
                result = handle_portfolio_summary({"path": display_form.get("path", DEFAULT_USER_PATH)})
            except Exception:
                result = None
        self._send_page(render_page(
            page=page, result=result, form=display_form,
            legacy_features_visible=True,
        ))

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        form: dict[str, str] = {}
        if path in LEGACY_POST_PATHS and not self._legacy_features_enabled():
            self._send_legacy_hidden()
            return
        try:
            form = self._read_form()
            display_form = form
            if path.startswith("/adaptive-v13-"):
                if PHASE6_CONTROLLER is None:
                    raise RuntimeError("Phase 6 服务尚未配置")
                phase6_state = self._phase6_state()
                page, message = handle_phase6_action(
                    path, form, PHASE6_CONTROLLER, phase6_state
                )
                self._send_page(
                    render_page(
                        page=page, result=RenderResult(message), form={},
                        phase6_state=phase6_state,
                    )
                )
                return
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
            elif path == "/thermostat-backtest-job":
                page = "backtest"
                result = handle_thermostat_backtest_job(form)
            elif path == "/portfolio-init":
                page = "portfolio"
                result = handle_portfolio_init(form)
            elif path == "/portfolio-buy":
                page = "portfolio"
                result = handle_portfolio_buy(form)
            elif path == "/portfolio-sell":
                page = "portfolio"
                result = handle_portfolio_sell(form)
            elif path == "/portfolio-adjust-cost":
                page = "portfolio"
                result = handle_portfolio_adjust_cost(form)
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
            display_form = _display_form_after_success(path, form)
            LAST_FORM.clear()
            LAST_FORM.update(display_form)
            self._send_page(render_page(
                page=page, result=result, form=display_form,
                legacy_features_visible=True,
            ))
        except Exception as exc:
            failed_page = _page_for_path(path)
            failed_state = (
                self._phase6_state() if failed_page in PHASE6_PAGES else None
            )
            if failed_page in PHASE6_PAGES:
                view = (
                    PHASE6_CONTROLLER.get_error_view(exc)
                    if PHASE6_CONTROLLER is not None
                    else _startup_error_view(exc)
                )
                self._send_page(render_page(
                    page=failed_page,form=form,phase6_state=failed_state,
                    phase6_error=view,
                ))
            else:
                self._send_page(render_page(
                    page=failed_page,error=str(exc),form=form,
                ))

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[web] {self.address_string()} - {fmt % args}", file=sys.stderr)

    def _legacy_features_enabled(self) -> bool:
        if PHASE6_CONTROLLER is None:
            return False
        try:
            return PHASE6_CONTROLLER.show_legacy_experimental(
                self._phase6_state().account_profile_id
            )
        except Exception:
            return False

    def _send_legacy_hidden(self) -> None:
        self._send_page(render_page(
            page="adaptive-v13-overview",
            result=RenderResult("旧版/实验功能当前已隐藏"),
            form={},
            phase6_state=self._phase6_state(),
            phase6_error=PHASE6_STARTUP_ERROR,
        ))

    def _read_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        values = parse_qs(raw, keep_blank_values=True)
        return {
            key: ",".join(value) if key in {"market_range","market_scopes"} else value[-1]
            for key, value in values.items()
        }

    def _send_page(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        if getattr(self, "_phase6_session_cookie", ""):
            self.send_header(
                "Set-Cookie",
                f"adaptive_v13_session={self._phase6_session_cookie}; Path=/; SameSite=Strict",
            )
        self.end_headers()
        self.wfile.write(encoded)

    def _phase6_state(self) -> Phase6WebState:
        cookie = self.headers.get("Cookie", "")
        session_id = ""
        for item in cookie.split(";"):
            key, separator, value = item.strip().partition("=")
            if separator and key == "adaptive_v13_session":
                session_id = value
                break
        if not session_id.replace("-", "").isalnum() or len(session_id) > 64:
            session_id = uuid.uuid4().hex
        with PHASE6_STATES_LOCK:
            state = PHASE6_STATES.setdefault(session_id, Phase6WebState())
        self._phase6_session_cookie = session_id
        return state

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

    def _send_phase6_report(self, path: Path) -> None:
        payload = path.read_bytes()
        content_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if path.suffix.lower() == ".xlsx"
            else ("application/x-ndjson" if path.suffix.lower() == ".jsonl" else "application/json")
        )
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_thermostat_report(self, job_id: str) -> None:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job is None:
                self.send_error(HTTPStatus.NOT_FOUND, "report job not found")
                return
            if job.status != "done":
                self.send_error(HTTPStatus.CONFLICT, "report is not ready")
                return
            report_path = Path(job.report_path) if job.report_path else None
            filename = job.report_filename or (report_path.name if report_path is not None else "")
        if report_path is None or not report_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "report file not found")
            return
        data = report_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


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
    trigger_plan = getattr(result, "trigger_plan", pd.DataFrame())
    tables = [
        *_thermostat_trigger_plan_tables(trigger_plan),
        TableBlock("错误/数据质量", result.errors),
    ]
    return RenderResult(
        title="恒温器策略",
        summaries=[
            _request_summary(
                {**form, "start": start, "end": end, "account_path": account_path},
                ["stock_pool_source", "symbols", "watchlist_name", "market_range", "lhb_range", "start", "end", "account_path", "refresh"],
            )
        ],
        tables=tables,
        extra_html=_thermostat_trigger_plan_detail(trigger_plan),
        metadata={"trigger_plan": trigger_plan, "errors": result.errors},
    )


def _thermostat_trigger_plan_tables(trigger_plan: pd.DataFrame) -> list[TableBlock]:
    frame = trigger_plan if trigger_plan is not None else pd.DataFrame()
    pending = _filter_nonempty(frame, "pending_sell_level")
    risk = frame
    if not frame.empty:
        failed = _nonempty_mask(frame, "failed_reason")
        risk_note = _nonempty_mask(frame, "risk_note")
        risk = frame[failed | risk_note].copy()
    return [
        TableBlock(
            "市场状态与仓位折扣",
            _select_columns(
                frame.drop_duplicates(subset=[column for column in ["date", "market_regime", "market_regime_normalized", "market_position_discount"] if column in frame])
                if not frame.empty
                else frame,
                ["date", "market_regime", "market_regime_normalized", "market_position_discount"],
            ),
        ),
        TableBlock(
            "个股模式摘要",
            _select_columns(
                frame,
                [
                    "symbol",
                    "name",
                    "stock_mode",
                    "target_position_pct",
                    "max_position_pct",
                    "total_shares",
                    "available_shares",
                    "today_bought_shares",
                    "pending_sell_level",
                ],
            ),
        ),
        TableBlock(
            "趋势触发计划",
            _select_columns(
                _filter_equals(frame, "stock_mode", "trend"),
                [
                    "symbol",
                    "name",
                    "reference_price",
                    "boll_upper",
                    "boll_mid",
                    "boll_lower",
                    "atr20",
                    "trend_buy_trigger",
                    "trend_reduce_trigger",
                    "trend_exit_trigger",
                    "trend_batches",
                    "max_position_pct",
                ],
            ),
        ),
        TableBlock(
            "网格触发计划",
            _select_columns(
                _filter_equals(frame, "stock_mode", "range"),
                [
                    "symbol",
                    "name",
                    "reference_price",
                    "grid_lower",
                    "grid_mid",
                    "grid_upper",
                    "grid_max_layers",
                    "grid_buy_levels",
                    "grid_sell_levels",
                    "grid_total_max_position_pct",
                    "max_position_pct",
                ],
            ),
        ),
        TableBlock(
            "待卖记录",
            _select_columns(
                pending,
                [
                    "symbol",
                    "name",
                    "stock_mode",
                    "available_shares",
                    "today_bought_shares",
                    "pending_sell_level",
                    "trigger_status",
                    "filled_status",
                ],
            ),
        ),
        TableBlock(
            "失败原因和风险提示",
            _select_columns(
                risk,
                [
                    "symbol",
                    "name",
                    "stock_mode",
                    "trigger_status",
                    "filled_status",
                    "failed_reason",
                    "risk_note",
                    "reason",
                ],
            ),
        ),
    ]


def _thermostat_trigger_plan_detail(trigger_plan: pd.DataFrame) -> str:
    if trigger_plan is None or trigger_plan.empty:
        return ""
    fields = ", ".join(str(column) for column in trigger_plan.columns)
    return (
        '<details class="result-section result-section-table trigger-plan-detail">'
        "<summary>展开查看详细字段</summary>"
        f'<p class="muted">原始字段：{html.escape(fields)}</p>'
        f'{render_table("Trigger Plan", trigger_plan, include_title=False)}'
        "</details>"
    )


def _select_columns(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    visible = [column for column in columns if column in frame.columns]
    return frame.loc[:, visible].copy()


def _filter_equals(frame: pd.DataFrame, column: str, value: str) -> pd.DataFrame:
    if frame is None or frame.empty or column not in frame:
        return pd.DataFrame()
    return frame[frame[column].fillna("").astype(str) == value].copy()


def _filter_nonempty(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    if frame is None or frame.empty or column not in frame:
        return pd.DataFrame()
    return frame[_nonempty_mask(frame, column)].copy()


def _nonempty_mask(frame: pd.DataFrame, column: str) -> pd.Series:
    if frame is None or frame.empty or column not in frame:
        return pd.Series(False, index=frame.index if frame is not None else None)
    return frame[column].fillna("").astype(str).str.strip().ne("")


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


def handle_thermostat_backtest_job(form: dict[str, str]) -> RenderResult:
    job = start_thermostat_backtest_job(form)
    return RenderResult(
        "恒温器回测任务已开始",
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
        self.report_path = ""
        self.report_filename = ""
        self.report_error = ""

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
            self.result_html = render_message(result, None) + _thermostat_report_entry(self)

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


def start_thermostat_backtest_job(form: dict[str, str]) -> ThermostatJob:
    job_id = uuid.uuid4().hex
    job = ThermostatJob(job_id, form)
    with JOBS_LOCK:
        JOBS[job_id] = job
    thread = threading.Thread(target=_run_thermostat_backtest_job, args=(job_id,), daemon=True)
    thread.start()
    return job


def _run_thermostat_job(job_id: str) -> None:
    job = JOBS[job_id]
    try:
        result = handle_thermostat(job.form, progress_callback=job.update)
        _prepare_t1_thermostat_report(job, result)
        job.complete(result)
    except Exception as exc:  # pragma: no cover - defensive live-web path
        job.fail(exc)


def _run_thermostat_backtest_job(job_id: str) -> None:
    job = JOBS[job_id]
    try:
        symbols = _symbols(job.form)
        total_symbols = max(len(symbols), 1)
        job.update({"stage": "prepare_backtest", "completed": 0, "total": total_symbols, "node": "准备回测参数"})
        job.update({"stage": "load_backtest_data", "completed": total_symbols, "total": total_symbols, "node": "加载回测数据"})
        job.update({"stage": "simulate_trades", "completed": 0, "total": total_symbols, "node": "模拟交易"})
        result = handle_thermostat_backtest(job.form)
        job.update({"stage": "prepare_result_tables", "completed": total_symbols, "total": total_symbols, "node": "整理回测结果表"})
        job.update({"stage": "prepare_report", "completed": total_symbols, "total": total_symbols, "node": "准备报告下载"})
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


def _prepare_t1_thermostat_report(job: ThermostatJob, result: RenderResult) -> None:
    try:
        trigger_plan = result.metadata.get("trigger_plan")
        errors = result.metadata.get("errors")
        report = build_t1_thermostat_report(
            trigger_plan if isinstance(trigger_plan, pd.DataFrame) else pd.DataFrame(),
            errors if isinstance(errors, pd.DataFrame) else pd.DataFrame(),
        )
        filename = default_t1_thermostat_report_filename(report)
        output = REPORT_DIR / filename
        export_t1_thermostat_excel(report, output)
        job.report_path = str(output)
        job.report_filename = filename
        job.report_error = ""
    except Exception as exc:  # pragma: no cover - defensive live-web path
        job.report_path = ""
        job.report_filename = ""
        job.report_error = str(exc)


def _thermostat_report_entry(job: ThermostatJob) -> str:
    if job.report_error:
        return (
            '<section class="result-section result-section-report">'
            '<h3>报告下载</h3>'
            f'<p class="message error">报告导出失败：{html.escape(job.report_error)}</p>'
            "</section>"
        )
    if job.report_path and job.report_filename:
        href = f"/thermostat-report?id={html.escape(job.job_id)}"
        return (
            '<section class="result-section result-section-report">'
            '<h3>报告下载</h3>'
            f'<a class="button" href="{href}">下载新版 T+1 恒温器报告</a>'
            "</section>"
        )
    return ""


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


def _first_record(frame: pd.DataFrame) -> dict[str, object]:
    if frame is None or frame.empty:
        return {}
    return frame.iloc[0].to_dict()


def handle_thermostat_backtest(form: dict[str, str]) -> RenderResult:
    source = _value(form, "stock_pool_source", "manual")
    if source not in {"manual", "watchlist", "market_range", "lhb"}:
        label = OPTION_LABELS["stock_pool_source"].get(source, source)
        return _stock_pool_error_result(_stock_pool_error(source, label, f"回测暂不支持股票池来源：{label}"))
    service = _service(form)
    pool = _resolve_thermostat_stock_pool(form, service)
    if pool.errors or not pool.symbols:
        return _stock_pool_error_result(pool)
    symbols = [normalize_symbol(symbol) for symbol in pool.symbols]
    start, end = _backtest_range_dates(form)
    if not start or not end:
        raise ValueError("恒温器回测需要开始日期和结束日期。")
    result = backtest_thermostat_strategy(
        service=service,
        symbols=symbols,
        start_date=start,
        end_date=end,
        initial_cash=_float(form, "cash", 100000.0),
    )
    return RenderResult(
        title="恒温器回测诊断",
        summaries=[
            _first_record(result.summary),
            _pool_summary_dict(pool),
            _request_summary(
                {**form, "start": start, "end": end},
                ["stock_pool_source", "watchlist_name", "market_range", "backtest_date_range", "start", "end", "cash", "source", "refresh"],
            ),
        ],
        tables=[
            TableBlock("Summary", result.summary),
            TableBlock("Regime Performance", result.regime_performance),
            TableBlock("Diagnostics", result.diagnostics),
            TableBlock("Daily Portfolio", result.daily_portfolio),
            TableBlock("Trades", result.trades),
            TableBlock("Positions", result.positions),
            TableBlock("Symbol Performance", result.symbol_performance),
            TableBlock("Data Quality", result.data_quality),
            TableBlock("Parameters", result.parameters),
        ],
        extra_html='<p class="muted">报告下载区：事件驱动详细报告可由后续下载入口导出为 Excel。</p>',
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
    page: str = "adaptive-v13-overview",
    result: RenderResult | None = None,
    error: str | None = None,
    form: dict[str, str] | None = None,
    phase6_state: Phase6WebState | None = None,
    phase6_error: ErrorVM | None = None,
    legacy_features_visible: bool | None = None,
) -> str:
    form = form or {}
    page = page if page in PAGES else "adaptive-v13-overview"
    if legacy_features_visible is None:
        legacy_features_visible = _legacy_features_enabled(
            phase6_state or PHASE6_STATE
        )
    if page in PHASE6_PAGES:
        page_body = render_phase6_page(
            page, PHASE6_CONTROLLER, phase6_state or PHASE6_STATE,
            message="" if result is None else result.title,
            error=phase6_error,
        )
    else:
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
      <p>自适应趋势 V1.3 数据、运行与账户工作台</p>
    </div>
    <div class="status">本地运行 · {html.escape(datetime.now().strftime("%Y-%m-%d %H:%M"))}</div>
  </header>
  <nav>
    {phase6_nav(page)}
    {legacy_nav(page) if legacy_features_visible else ""}
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
      <p>本地恒温器策略、回测和手动账户工作台</p>
    </div>
    <div class="status">本地运行 · {html.escape(datetime.now().strftime("%Y-%m-%d %H:%M"))}</div>
  </header>
  <nav>
    {nav_link("thermostat", "恒温器策略", page)}
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


def legacy_nav(current: str) -> str:
    links = "".join((
        nav_link("thermostat", "恒温器策略", current),
        nav_link("backtest", "旧回测诊断", current),
        nav_link("portfolio", "旧选股与账户", current),
    ))
    return (
        '<div class="nav-group legacy-nav">'
        '<span class="nav-title">旧版/实验功能</span>'
        f"{links}</div>"
    )


def _legacy_features_enabled(state: Phase6WebState) -> bool:
    if PHASE6_CONTROLLER is None:
        return False
    try:
        return PHASE6_CONTROLLER.show_legacy_experimental(
            state.account_profile_id
        )
    except Exception:
        return False


def render_source_refresh_script(page: str) -> str:
    if page not in {"thermostat", "backtest"}:
        return ""
    target = f"/{page}"
    return f"""
  <script>
  function refreshSourceFields(select) {{
    const form = select.form;
    const params = new URLSearchParams(new FormData(form));
    window.location.href = "{target}?" + params.toString();
  }}
  </script>
  """


def render_thermostat_section(form: dict[str, str]) -> str:
    display_form = _thermostat_display_form(form)
    stock_source = _value(display_form, "stock_pool_source", "manual")
    form_action = "/thermostat-job"
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
    fields = select(
        "strategy_date_range",
        ("1m", "3m", "half_year", "1y", "custom"),
        "3m",
        "策略日期范围",
        form,
        attrs='onchange="refreshSourceFields(this)"',
    )
    if current == "custom":
        fields += input_text("start", "策略开始日期", "", form)
        fields += input_text("end", "策略结束日期", _today_yyyymmdd(), form)
    else:
        fields += f'<p class="muted">实际使用日期范围：{html.escape(actual_start)} 至 {html.escape(actual_end)}</p>'
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


def _backtest_range_dates(form: dict[str, str]) -> tuple[str, str]:
    end = _value(form, "end", _today_yyyymmdd())
    if "backtest_date_range" not in form and _optional(form, "start"):
        return _value(form, "start"), end
    range_key = _value(form, "backtest_date_range", "3m")
    if range_key == "custom":
        start = _optional(form, "start") or (pd.to_datetime(end) - pd.Timedelta(days=90)).strftime("%Y%m%d")
        return start, end
    offsets = {"1m": 30, "3m": 90, "5m": 150, "half_year": 182, "1y": 365}
    start = (pd.to_datetime(end) - pd.Timedelta(days=offsets.get(range_key, 90))).strftime("%Y%m%d")
    return start, end


def render_thermostat_backtest_section(form: dict[str, str]) -> str:
    today = _today_yyyymmdd()
    actual_start, actual_end = _backtest_range_dates(form)
    date_fields = select(
        "backtest_date_range",
        ("1m", "3m", "5m", "half_year", "1y", "custom"),
        "3m",
        "回测日期范围",
        form,
        attrs='onchange="refreshSourceFields(this)"',
    )
    if _value(form, "backtest_date_range", "3m") == "custom":
        date_fields += input_text("start", "开始日期", "", form)
        date_fields += input_text("end", "结束日期", today, form)
    else:
        date_fields += f'<p class="muted">实际回测日期范围：{html.escape(actual_start)} 至 {html.escape(actual_end)}</p>'
    return f"""
    <section id="backtest" class="workspace-section">
      <div class="page-head">
        <h2>恒温器回测诊断</h2>
        <p class="status">页面状态：工作区用于正式事件驱动回测，旧简化回测仅作为明确标记的辅助诊断。</p>
      </div>
      <form method="post" action="/thermostat-backtest-job">
        <h3>数据缓存区</h3>
        <p class="muted">正式回测会校验本地缓存；缺少涨跌停、停牌或执行时间点状态时会给出数据质量提示。</p>
        <h3>回测参数区</h3>
        <h4>股票池来源</h4>
        {stock_pool_fields(form)}
        <h4>回测日期范围</h4>
        <div class="grid">
          {date_fields}
        </div>
        <div class="grid">
          {input_number("cash", "初始资金", "100000", form)}
          {source_fields(form)}
        </div>
        {checkbox("refresh", "强制刷新历史数据", form)}
        <button type="submit">运行恒温器回测</button>
        <h3>回测结果区</h3>
        <p class="muted">运行后展示摘要、每日资产、交易明细、持仓和数据质量。</p>
        <h3>报告下载区</h3>
        <p class="muted">运行后可导出详细 Excel 报告。</p>
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
    display_title = _display_title(result.title)
    is_backtest = "回测" in display_title
    parts = [f'<section class="message"><h2>{html.escape(display_title)}</h2>']
    for summary in result.summaries:
        summary_html = render_summary(summary)
        if is_backtest:
            parts.append(f'<section class="result-section result-section-summary"><h3>回测摘要</h3>{summary_html}</section>')
        else:
            parts.append(summary_html)
    for table in result.tables:
        if is_backtest:
            title = _display_title(table.title)
            row_count = 0 if table.frame is None else len(table.frame)
            body = render_table(table.title, table.frame, include_title=False)
            open_attr = " open" if table.title in {"Summary", "Trades", "Positions"} else ""
            parts.append(
                f'<details class="result-section result-section-table"{open_attr}>'
                f"<summary>{html.escape(title)} <span>{row_count} 行</span></summary>{body}</details>"
            )
        else:
            parts.append(render_table(table.title, table.frame))
    if result.extra_html:
        if is_backtest:
            parts.append(
                '<section class="result-section result-section-report">'
                '<h3>报告下载</h3>'
                f'<div class="report-entry report-entry-available">{result.extra_html}</div>'
                "</section>"
            )
        else:
            parts.append(result.extra_html)
    parts.append("</section>")
    return "\n".join(parts)


def render_summary(values: dict[str, object]) -> str:
    rows = []
    for key, value in values.items():
        display = _display_value(key, value)
        rows.append(f"<dt>{html.escape(_display_label(str(key)))}</dt><dd>{html.escape(display)}</dd>")
    return f'<dl class="summary">{"".join(rows)}</dl>'


def render_table(title: str, frame: pd.DataFrame, *, include_title: bool = True) -> str:
    display_title = _display_title(title)
    if frame is None or frame.empty:
        heading = f"<h3>{html.escape(display_title)}</h3>" if include_title else ""
        return f'{heading}<p class="muted">暂无数据。</p>'
    data = frame.copy()
    if len(data) > 200:
        data = data.tail(200)
    data = data.replace({pd.NA: ""}).fillna("")
    data = _localize_frame(data, title=title)
    if data.empty or not list(data.columns):
        heading = f"<h3>{html.escape(display_title)}</h3>" if include_title else ""
        return f'{heading}<p class="muted">暂无数据。</p>'
    table = data.to_html(index=False, escape=True, classes="data-table sticky-table", border=0)
    table = table.replace('class="dataframe data-table sticky-table"', 'class="data-table sticky-table"')
    wrap_class = "table-wrap table-wrap-scroll" if len(frame) >= 50 or len(data.columns) >= 8 else "table-wrap"
    heading = f"<h3>{html.escape(display_title)} <span>{len(frame)} 行</span></h3>" if include_title else ""
    return (
        f"{heading}"
        f'<div class="{wrap_class}">{table}</div>'
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
        return f"{value:.2f}"
    return str(value)


def _display_title(title: str) -> str:
    if title in TITLE_LABELS:
        return TITLE_LABELS[title]
    if title.startswith("Strategy:"):
        strategy = title.split(":", 1)[1].strip()
        return f"策略运行：{_display_value('strategy', strategy)}"
    if _contains_cjk(title):
        return title
    return "结果"


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
    if isinstance(value, bool):
        return "是" if value else "否"
    if key in INTEGER_DISPLAY_COLUMNS:
        try:
            return str(int(float(value)))
        except (TypeError, ValueError):
            return str(value)
    if key in MONEY_DISPLAY_COLUMNS:
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return str(value)
    if key in PERCENT_DISPLAY_COLUMNS:
        try:
            return f"{float(value):.2%}"
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, float):
        return f"{value:.6f}"
    text = str(value)
    if key in OPTION_LABELS and text in OPTION_LABELS[key]:
        return OPTION_LABELS[key][text]
    if key == "source" and text in OPTION_LABELS["pool_mode"]:
        return OPTION_LABELS["pool_mode"][text]
    if key in {"reason", "risk_note"}:
        return _translate_text(text)
    return text


def _localize_frame(frame: pd.DataFrame, *, title: str = "") -> pd.DataFrame:
    data = frame.copy()
    data = _with_stock_name_column(data, title)
    visible = [column for column in data.columns if _is_display_column_visible(str(column), title)]
    data = data[visible]
    for column in list(data.columns):
        data[column] = data[column].map(lambda value, key=column: _display_value(key, value))
    return data.rename(columns={column: _display_label(str(column)) for column in data.columns})


def _with_stock_name_column(frame: pd.DataFrame, title: str) -> pd.DataFrame:
    if title not in STOCK_LEVEL_TABLES or "name" in frame.columns:
        return frame
    symbol_column = "symbol" if "symbol" in frame.columns else "code" if "code" in frame.columns else ""
    if not symbol_column:
        return frame
    data = frame.copy()
    insert_at = list(data.columns).index(symbol_column) + 1
    data.insert(insert_at, "name", "未知")
    return data


def _is_display_column_visible(column: str, title: str) -> bool:
    hidden = TABLE_HIDDEN_COLUMNS.get(title, DEFAULT_HIDDEN_COLUMNS)
    if column in hidden:
        return False
    if column in COLUMN_LABELS:
        return True
    if column.isdigit() or _contains_cjk(column):
        return True
    return False


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
        ("insufficient cash:", "账户现金不足："),
        ("buy signal but cash is insufficient for one lot or requested turtle unit", "出现买入信号，但建议仓位金额不足以买入一手或策略建议单元"),
        ("现金不足以买入一手", "建议仓位金额不足以买入一手"),
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
        + input_text("strategy_meta", "策略", "thermostat", form)
        + input_text("system", "系统", "trend_following", form)
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


def _account_path_for_form(form: dict[str, str], *, page: str) -> str:
    if page == "thermostat":
        return _value(form, "account_path", _value(form, "path", DEFAULT_USER_PATH))
    return _value(form, "path", _value(form, "account_path", DEFAULT_USER_PATH))


def _copy_existing(form: dict[str, str], keys: list[str]) -> dict[str, str]:
    return {key: form[key] for key in keys if _optional(form, key) is not None}


def _display_form_for_page(page: str, form: dict[str, str]) -> dict[str, str]:
    if page == "thermostat":
        display = _copy_existing(
            form,
            [
                "stock_pool_source",
                "symbols",
                "watchlist_name",
                "market_range",
                "lhb_range",
                "lhb_confirmed_top",
                "lhb_start",
                "lhb_end",
                "strategy_date_range",
                "start",
                "end",
                "cash",
                "source",
                "stock_source",
                "realtime_source",
                "refresh",
                "execution_plan",
                "next_day_premium",
                "volume_limit_pct",
                "exclude_star",
                "use_simulated_cash",
            ],
        )
        display["account_path"] = _account_path_for_form(form, page="thermostat")
        return display
    if page == "portfolio":
        display = _copy_existing(form, ["source", "stock_source", "realtime_source", "refresh", "watchlist_name"])
        display["path"] = _account_path_for_form(form, page="portfolio")
        if _optional(form, "account_path") is not None:
            display["account_path"] = _value(form, "account_path")
        return display
    if page == "backtest":
        display = _copy_existing(
            form,
            [
                "stock_pool_source",
                "symbols",
                "watchlist_name",
                "market_range",
                "lhb_range",
                "lhb_confirmed_top",
                "lhb_start",
                "lhb_end",
                "backtest_date_range",
                "start",
                "end",
                "cash",
                "source",
                "stock_source",
                "realtime_source",
                "refresh",
            ],
        )
        if _optional(form, "account_path") is not None:
            display["account_path"] = _value(form, "account_path")
        return display
    return {}


def _display_form_after_success(path: str, form: dict[str, str]) -> dict[str, str]:
    if path == "/thermostat-job":
        return _display_form_for_page("thermostat", form)
    if path in {"/thermostat-backtest", "/thermostat-backtest-job"}:
        return _display_form_for_page("backtest", form)
    if path == "/portfolio-init":
        return {"path": _account_path_for_form(form, page="portfolio")}
    if path == "/portfolio-summary":
        return _display_form_for_page("portfolio", form)
    if path in {"/portfolio-buy", "/portfolio-sell", "/portfolio-adjust-cost"}:
        return _clear_trade_form(form)
    if path == "/watchlist-create":
        display = {"path": _account_path_for_form(form, page="portfolio")}
        if _optional(form, "account_path") is not None:
            display["account_path"] = _value(form, "account_path")
        return display
    if path in {"/watchlist-add-symbol", "/watchlist-remove-symbol", "/watchlist-rename"}:
        display = {"path": _account_path_for_form(form, page="portfolio")}
        if _optional(form, "account_path") is not None:
            display["account_path"] = _value(form, "account_path")
        if _optional(form, "watchlist_name") is not None:
            display["watchlist_name"] = _value(form, "watchlist_name")
        return display
    if path == "/watchlist-delete":
        display = {"path": _account_path_for_form(form, page="portfolio")}
        if _optional(form, "account_path") is not None:
            display["account_path"] = _value(form, "account_path")
        return display
    if path == "/watchlist-save-manual":
        return _display_form_for_page("thermostat", form)
    return dict(form)








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
        raise ValueError(f"未知的股票池模式：{pool_mode}")

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




def _is_number(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
















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
    phase6_paths = {
        "/adaptive-v13-preview":"adaptive-v13-cache",
        "/adaptive-v13-cache-prepare":"adaptive-v13-cache",
        "/adaptive-v13-backtest-run":"adaptive-v13-backtest",
        "/adaptive-v13-paper-run":"adaptive-v13-paper",
        "/adaptive-v13-resume":"adaptive-v13-runs",
        "/adaptive-v13-report":"adaptive-v13-runs",
        "/adaptive-v13-provider-test":"adaptive-v13-account",
        "/adaptive-v13-account-save":"adaptive-v13-account",
        "/adaptive-v13-legacy-settings":"adaptive-v13-account",
        "/adaptive-v13-watchlist":"adaptive-v13-account",
    }
    if path in phase6_paths:
        return phase6_paths[path]
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
    if path in {"/thermostat-backtest", "/thermostat-backtest-job"}:
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
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.title = title
        self.tables = tables or []
        self.summaries = summaries or []
        self.extra_html = extra_html
        self.metadata = metadata or {}


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
.table-wrap-scroll {
  max-height: 520px;
  overflow: auto;
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
  position: sticky;
  top: 0;
  z-index: 1;
  background: #f2f5f8;
}
h3 span { color: var(--muted); font-weight: 400; }
.nav-group { display:flex; align-items:center; gap:4px; flex-wrap:wrap; }
.nav-title { color:var(--muted); font-size:11px; text-transform:uppercase; margin-right:4px; }
.legacy-nav { margin-left:auto; padding-left:12px; border-left:1px solid var(--border); }
.adaptive-v13 { --ready:#197047; --partial:#9a6700; --failed:#b42318; }
.strategy-heading { display:flex; justify-content:space-between; align-items:end; margin-bottom:12px; }
.strategy-heading h2 { margin:2px 0 0; }
.eyebrow { color:var(--accent); font-size:12px; font-weight:700; }
.status-chip { border:1px solid var(--line); border-radius:999px; padding:4px 9px; font-weight:700; }
.status-ready,.status-completed { color:var(--ready); border-color:var(--ready); }
.status-partial,.status-running,.status-degraded { color:var(--partial); border-color:var(--partial); }
.status-invalid,.status-failed { color:var(--failed); border-color:var(--failed); }
.account-summary,.card-grid,.selector-grid,.form-grid { display:grid; gap:10px; }
.account-summary { grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); margin-bottom:14px; }
.account-summary div,.metric-card,.panel { background:#fff; border:1px solid var(--line); border-radius:8px; padding:12px; }
.account-summary span { display:block; color:var(--muted); font-size:11px; }
.card-grid { grid-template-columns:repeat(3,minmax(0,1fr)); margin-bottom:14px; }
.selector-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
.form-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
.span-2 { grid-column:span 2; }
.panel { margin-bottom:14px; }
.panel h3 { margin-top:0; }
.table-scroll { max-width:100%; overflow-x:auto; }
.table-scroll table { width:100%; border-collapse:collapse; font-size:13px; }
.table-scroll th,.table-scroll td { border-bottom:1px solid var(--line); padding:8px; text-align:left; }
.actions,.filter-row,.data-summary { display:flex; gap:10px; align-items:end; flex-wrap:wrap; }
.hint,.empty-state { color:var(--muted); }
.validation,.warning { color:var(--failed); }
button:disabled { opacity:.45; cursor:not-allowed; }
@media (max-width: 720px) {
  header, nav { padding-left: 16px; padding-right: 16px; }
  main { padding: 14px 16px 32px; }
  .columns { grid-template-columns: 1fr; }
  .card-grid,.selector-grid,.form-grid { grid-template-columns:1fr; }
}
"""


def _startup_error_view(error: BaseException) -> ErrorVM:
    return ErrorVM(
        title="Phase 6服务初始化失败",
        action="请检查项目数据目录、账户配置和Provider安装状态后重启。",
        code="INVALID_CONFIG",
        detail="",
        recoverable=False,
        correlation_id=uuid.uuid4().hex[:16],
    )


def initialize_phase6() -> Phase6Controller | None:
    """Compose Phase 6 before HTTP startup while preserving legacy pages on failure."""
    global PHASE6_STARTUP_ERROR
    try:
        controller = create_phase6_application(
            project_root=Path(__file__).resolve().parents[1],
        )
    except Exception as exc:
        PHASE6_STARTUP_ERROR = _startup_error_view(exc)
        return None
    configure_phase6(controller)
    PHASE6_STARTUP_ERROR = None
    return controller


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local Stock Picker web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    initialize_phase6()
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
