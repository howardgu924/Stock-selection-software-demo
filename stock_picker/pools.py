from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd

from stock_picker.data.models import StockInfo, is_supported_stock_symbol, normalize_symbol, split_symbol_tokens, symbol_code
from stock_picker.user.watchlist import WatchlistStore


LARGE_POOL_WARNING = "大范围股票池可能耗时较长"


@dataclass(frozen=True)
class StockPoolSummary:
    source: str
    name: str
    original_count: int
    deduped_count: int
    filtered_count: int
    removed_count: int
    time_range: str = ""
    source_detail: str = ""


@dataclass(frozen=True)
class StockPoolResult:
    symbols: list[str]
    summary: StockPoolSummary
    duplicates: list[str] = field(default_factory=list)
    invalid_symbols: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def should_stop(self) -> bool:
        return bool(self.errors)


def parse_manual_pool(raw: str, *, name: str = "手动输入", exclude_star: bool = False) -> StockPoolResult:
    tokens = split_symbol_tokens(raw)
    if not tokens:
        summary = StockPoolSummary("manual", name, 0, 0, 0, 0)
        return StockPoolResult([], summary, errors=["手动输入为空，请输入股票代码或选择其他股票池。"])
    return _build_result(tokens, source="manual", name=name, exclude_star=exclude_star)


def resolve_watchlist_pool(store: WatchlistStore, name: str, *, exclude_star: bool = False) -> StockPoolResult:
    watchlist = store.get(name)
    if watchlist is None:
        summary = StockPoolSummary("watchlist", name, 0, 0, 0, 0)
        return StockPoolResult([], summary, errors=[f"自选股组合不存在：{name}"])
    if not watchlist.symbols:
        summary = StockPoolSummary("watchlist", name, 0, 0, 0, 0)
        return StockPoolResult([], summary, errors=[f"自选股组合为空：{name}"])
    return _build_result(watchlist.symbols, source="watchlist", name=name, exclude_star=exclude_star)


def resolve_market_range_pool(
    stocks: list[StockInfo],
    market_range: str,
    *,
    source_detail: str = "",
    updated_at: str = "",
    exclude_star: bool = False,
) -> StockPoolResult:
    range_names = {
        "star": "科创板",
        "sh": "沪市",
        "sz": "深市",
        "chinext": "创业板",
        "bj": "北交所",
        "all_a": "沪深 A 股",
    }
    ranges = _split_ranges(market_range)
    filtered_infos = [
        item
        for item in stocks
        if any(_in_market_range(item.symbol, one_range) for one_range in ranges)
    ]
    detail = " ".join(item for item in [source_detail, updated_at] if item).strip()
    display_name = "、".join(range_names.get(item, item) for item in ranges) if ranges else "市场范围"
    if not filtered_infos:
        summary = StockPoolSummary("market_range", display_name, 0, 0, 0, 0, source_detail=detail)
        return StockPoolResult([], summary, errors=["市场范围股票列表为空，请选择其他范围或刷新股票列表。"])
    return _build_result(
        [item.symbol for item in filtered_infos],
        source="market_range",
        name=display_name,
        exclude_star=exclude_star,
        source_detail=detail,
    )


def lhb_range_dates(
    range_key: str,
    *,
    as_of: str | None = None,
    start_date: str = "",
    end_date: str = "",
    strict: bool = True,
) -> tuple[str, str]:
    if range_key == "custom":
        if start_date and end_date and start_date <= end_date:
            return start_date, end_date
        if strict:
            raise ValueError("自定义龙虎榜时间范围无效：开始日期不能晚于结束日期。")
        return "", ""
    end = _parse_date(as_of or datetime.now().strftime("%Y%m%d"))
    offsets = {
        "1w": 6,
        "1m": 30,
        "3m": 90,
        "half_year": 182,
        "1y": 365,
    }
    if range_key not in offsets:
        if strict:
            raise ValueError(f"不支持的龙虎榜时间范围：{range_key}")
        return "", ""
    start = end - timedelta(days=offsets[range_key])
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def resolve_lhb_pool(
    fetcher: Callable[[str, str], pd.DataFrame],
    *,
    start_date: str,
    end_date: str,
    requested_source: str = "eastmoney",
    actual_source: str = "东方财富龙虎榜",
    exclude_star: bool = False,
) -> StockPoolResult:
    try:
        frame = fetcher(start_date, end_date)
    except Exception as exc:
        summary = StockPoolSummary("lhb", "龙虎榜", 0, 0, 0, 0, time_range=f"{start_date}-{end_date}", source_detail=actual_source)
        return StockPoolResult([], summary, errors=[f"龙虎榜数据抓取失败：{exc}"])
    if frame is None or frame.empty:
        summary = StockPoolSummary("lhb", "龙虎榜", 0, 0, 0, 0, time_range=f"{start_date}-{end_date}", source_detail=actual_source)
        return StockPoolResult([], summary, errors=["龙虎榜数据为空，请更换时间范围或选择其他股票池。"])
    ranked = _rank_lhb_frame(frame)
    result = _build_result(
        ranked["code"].astype(str).tolist(),
        source="lhb",
        name="龙虎榜",
        exclude_star=exclude_star,
        time_range=f"{start_date}-{end_date}",
        source_detail=actual_source,
    )
    warnings = [f"龙虎榜原始记录 {len(frame)} 条，去重后 {len(result.symbols)} 只股票。", *result.warnings]
    if requested_source == "ths" and actual_source != "同花顺龙虎榜":
        warnings.insert(0, f"同花顺龙虎榜不可用，实际使用数据来源：{actual_source}。")
    return StockPoolResult(
        result.symbols,
        result.summary,
        duplicates=result.duplicates,
        invalid_symbols=result.invalid_symbols,
        warnings=warnings,
        errors=result.errors,
    )


def _build_result(
    raw_symbols: list[str],
    *,
    source: str,
    name: str,
    exclude_star: bool = False,
    time_range: str = "",
    source_detail: str = "",
) -> StockPoolResult:
    symbols: list[str] = []
    duplicates: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for raw in raw_symbols:
        if not _is_valid_stock_code(raw):
            invalid.append(raw)
            continue
        normalized = normalize_symbol(raw)
        if normalized in seen:
            duplicates.append(normalized)
            continue
        seen.add(normalized)
        symbols.append(normalized)

    filtered = [symbol for symbol in symbols if not (exclude_star and _is_star_market(symbol))]
    removed_count = len(symbols) - len(filtered)
    warnings: list[str] = []
    errors: list[str] = []
    if invalid:
        warnings.append(f"以下股票代码无法识别，已排除：{', '.join(invalid)}")
    if len(filtered) > 500:
        warnings.append(LARGE_POOL_WARNING)
    if exclude_star and symbols and not filtered:
        errors.append("剔除科创板后股票池为空，请取消过滤或选择其他股票池。")

    summary = StockPoolSummary(
        source=source,
        name=name,
        original_count=len(raw_symbols),
        deduped_count=len(symbols),
        filtered_count=len(filtered),
        removed_count=removed_count,
        time_range=time_range,
        source_detail=source_detail,
    )
    return StockPoolResult(filtered, summary, duplicates=duplicates, invalid_symbols=invalid, warnings=warnings, errors=errors)


def _is_valid_stock_code(value: str) -> bool:
    return is_supported_stock_symbol(value)


def _is_star_market(symbol: str) -> bool:
    return symbol_code(symbol).startswith("688")


def _in_market_range(symbol: str, market_range: str) -> bool:
    normalized = normalize_symbol(symbol)
    code = symbol_code(normalized)
    if market_range == "star":
        return _is_star_market(normalized)
    if market_range == "sh":
        return normalized.endswith(".SH")
    if market_range == "sz":
        return normalized.endswith(".SZ")
    if market_range == "chinext":
        return code.startswith("300") and normalized.endswith(".SZ")
    if market_range == "bj":
        return normalized.endswith(".BJ")
    if market_range == "all_a":
        return normalized.endswith((".SH", ".SZ"))
    return False


def _split_ranges(value: str) -> list[str]:
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d")


def _rank_lhb_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"code", "name", "net_buy"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"龙虎榜数据缺少字段：{', '.join(sorted(missing))}")
    ranked = (
        frame.assign(
            code=frame["code"].astype(str).str.zfill(6),
            net_buy=pd.to_numeric(frame["net_buy"], errors="coerce").fillna(0.0),
        )
        .groupby(["code", "name"], as_index=False)["net_buy"]
        .sum()
        .sort_values(["net_buy", "code"], ascending=[False, True])
        .reset_index(drop=True)
    )
    return ranked
