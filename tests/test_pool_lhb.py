from __future__ import annotations

import pandas as pd

from stock_picker.pools import lhb_range_dates, resolve_lhb_pool


def test_lhb_range_dates_supports_named_ranges_and_custom_range() -> None:
    assert lhb_range_dates("1w", as_of="20260629") == ("20260623", "20260629")
    assert lhb_range_dates("1m", as_of="20260629") == ("20260530", "20260629")
    assert lhb_range_dates("3m", as_of="20260629") == ("20260331", "20260629")
    assert lhb_range_dates("half_year", as_of="20260629") == ("20251229", "20260629")
    assert lhb_range_dates("1y", as_of="20260629") == ("20250629", "20260629")
    assert lhb_range_dates("custom", start_date="20260601", end_date="20260610") == ("20260601", "20260610")


def test_lhb_range_dates_rejects_invalid_custom_range() -> None:
    result = lhb_range_dates("custom", start_date="20260610", end_date="20260601", strict=False)

    assert result == ("", "")


def test_resolve_lhb_pool_deduplicates_and_preserves_source_summary() -> None:
    frame = pd.DataFrame(
        [
            {"code": "600001", "name": "A", "net_buy": 20},
            {"code": "600001", "name": "A", "net_buy": 30},
            {"code": "000001", "name": "B", "net_buy": 10},
        ]
    )

    result = resolve_lhb_pool(lambda start, end: frame, start_date="20260601", end_date="20260610")

    assert result.symbols == ["600001.SH", "000001.SZ"]
    assert result.summary.source == "lhb"
    assert result.summary.time_range == "20260601-20260610"
    assert "东方财富龙虎榜" in result.summary.source_detail
    assert any("原始记录 3 条" in warning for warning in result.warnings)


def test_resolve_lhb_pool_reports_empty_and_fetch_failure() -> None:
    empty = resolve_lhb_pool(lambda start, end: pd.DataFrame(), start_date="20260601", end_date="20260610")

    def fail(start: str, end: str) -> pd.DataFrame:
        raise RuntimeError("network blocked")

    failed = resolve_lhb_pool(fail, start_date="20260601", end_date="20260610")

    assert empty.should_stop
    assert "龙虎榜数据为空" in empty.errors[0]
    assert failed.should_stop
    assert "network blocked" in failed.errors[0]


def test_resolve_lhb_pool_discloses_unavailable_ths_source() -> None:
    frame = pd.DataFrame([{"code": "600001", "name": "A", "net_buy": 20}])

    result = resolve_lhb_pool(
        lambda start, end: frame,
        start_date="20260601",
        end_date="20260610",
        requested_source="ths",
        actual_source="东方财富龙虎榜",
    )

    assert "同花顺龙虎榜不可用" in result.warnings[0]
    assert result.summary.source_detail == "东方财富龙虎榜"
