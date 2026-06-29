from __future__ import annotations

from stock_picker.pools import (
    LARGE_POOL_WARNING,
    parse_manual_pool,
    resolve_watchlist_pool,
)
from stock_picker.user import WatchlistStore


def test_parse_manual_pool_deduplicates_and_reports_invalid_symbols() -> None:
    result = parse_manual_pool("600519, 600519, abc, 000001")

    assert result.symbols == ["600519.SH", "000001.SZ"]
    assert result.summary.source == "manual"
    assert result.summary.original_count == 4
    assert result.summary.deduped_count == 2
    assert result.summary.filtered_count == 2
    assert result.summary.removed_count == 0
    assert result.duplicates == ["600519.SH"]
    assert result.invalid_symbols == ["abc"]
    assert any("abc" in warning for warning in result.warnings)


def test_parse_manual_pool_returns_error_for_empty_input() -> None:
    result = parse_manual_pool("  ")

    assert result.symbols == []
    assert result.errors == ["手动输入为空，请输入股票代码或选择其他股票池。"]
    assert result.should_stop


def test_parse_manual_pool_excludes_star_market_and_stops_when_empty() -> None:
    mixed = parse_manual_pool("688001,600519", exclude_star=True)
    empty = parse_manual_pool("688001,688002", exclude_star=True)

    assert mixed.symbols == ["600519.SH"]
    assert mixed.summary.filtered_count == 1
    assert mixed.summary.removed_count == 1
    assert empty.symbols == []
    assert empty.should_stop
    assert "剔除科创板后股票池为空" in empty.errors[0]


def test_large_pool_warning_starts_above_500_symbols() -> None:
    symbols_500 = ",".join(f"600{i:03d}" for i in range(500))
    symbols_501 = ",".join(f"600{i:03d}" for i in range(501))

    exact = parse_manual_pool(symbols_500)
    large = parse_manual_pool(symbols_501)

    assert LARGE_POOL_WARNING not in exact.warnings
    assert LARGE_POOL_WARNING in large.warnings


def test_resolve_watchlist_pool_uses_saved_group_without_portfolio_semantics(tmp_path) -> None:
    store = WatchlistStore(tmp_path / "user")
    store.create("高关注")
    store.add_symbols("高关注", ["600519", "000001"])

    result = resolve_watchlist_pool(store, "高关注")
    missing = resolve_watchlist_pool(store, "不存在")
    empty_store = WatchlistStore(tmp_path / "empty")
    empty_store.create("空组合")
    empty = resolve_watchlist_pool(empty_store, "空组合")

    assert result.symbols == ["600519.SH", "000001.SZ"]
    assert result.summary.source == "watchlist"
    assert result.summary.name == "高关注"
    assert missing.should_stop
    assert "不存在" in missing.errors[0]
    assert empty.should_stop
    assert "为空" in empty.errors[0]
