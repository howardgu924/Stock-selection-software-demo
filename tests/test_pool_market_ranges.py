from __future__ import annotations

from stock_picker.data.models import StockInfo
from stock_picker.pools import resolve_market_range_pool


STOCKS = [
    StockInfo.from_code_name("688001", "科创一号"),
    StockInfo.from_code_name("600519", "贵州茅台"),
    StockInfo.from_code_name("000001", "平安银行"),
    StockInfo.from_code_name("300001", "创业样本"),
]


def test_market_range_pool_filters_supported_ranges() -> None:
    star = resolve_market_range_pool(STOCKS, "star", source_detail="fake-list", updated_at="2026-06-29")
    sh = resolve_market_range_pool(STOCKS, "sh", source_detail="fake-list", updated_at="2026-06-29")
    sz = resolve_market_range_pool(STOCKS, "sz", source_detail="fake-list", updated_at="2026-06-29")
    all_a = resolve_market_range_pool(STOCKS, "all_a", source_detail="fake-list", updated_at="2026-06-29")

    assert star.symbols == ["688001.SH"]
    assert sh.symbols == ["688001.SH", "600519.SH"]
    assert sz.symbols == ["000001.SZ", "300001.SZ"]
    assert all_a.symbols == ["688001.SH", "600519.SH", "000001.SZ", "300001.SZ"]
    assert all_a.summary.source == "market_range"
    assert all_a.summary.name == "沪深 A 股"
    assert all_a.summary.source_detail == "fake-list 2026-06-29"


def test_market_range_pool_combines_with_star_exclusion_and_empty_stop() -> None:
    all_a = resolve_market_range_pool(STOCKS, "all_a", exclude_star=True)
    star = resolve_market_range_pool(STOCKS, "star", exclude_star=True)

    assert "688001.SH" not in all_a.symbols
    assert all_a.summary.removed_count == 1
    assert star.symbols == []
    assert star.should_stop
    assert "剔除科创板后股票池为空" in star.errors[0]


def test_market_range_pool_errors_when_range_is_empty() -> None:
    result = resolve_market_range_pool([], "all_a")

    assert result.symbols == []
    assert result.should_stop
    assert "市场范围股票列表为空" in result.errors[0]
