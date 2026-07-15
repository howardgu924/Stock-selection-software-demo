from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from stock_picker.strategies.adaptive_trend_v1_3.phase4_models import (
    ClassificationMetadata,
    Phase4Status,
    T1RiskObservation,
)
from stock_picker.strategies.adaptive_trend_v1_3.t1_tail_risk import (
    build_t1_risk_observation,
    calculate_t1_risk,
    linear_quantile_95,
)


def _bar(ts: str, **overrides) -> dict[str, object]:
    stamp = pd.Timestamp(ts)
    row = {
        "symbol": "600001.SH",
        "trade_date": stamp.strftime("%Y-%m-%d"),
        "bar_start": stamp,
        "open": "10",
        "high": "10",
        "low": "10",
        "close": "10",
        "volume": "1",
        "trade_status": "normal",
        "limit_status": "normal",
    }
    row.update(overrides)
    return row


def _obs(
    day: date,
    loss: Decimal,
    censored: bool = False,
    *,
    symbol: str = "600001.SH",
    instrument_type: str = "SECURITY",
) -> T1RiskObservation:
    completion = day + timedelta(days=1)
    bar_start = pd.Timestamp(f"{completion.isoformat()} 09:30", tz="Asia/Shanghai")
    return T1RiskObservation(
        instrument_type=instrument_type,
        symbol=symbol,
        sample_entry_date=day,
        completion_trade_date=completion,
        completion_bar_start=bar_start,
        known_at=(
            pd.Timestamp(f"{completion.isoformat()} 15:00", tz="Asia/Shanghai")
            if censored
            else bar_start + pd.Timedelta(minutes=5)
        ),
        entry_price=Decimal("10"),
        first_sellable_price=Decimal("10") * (Decimal("1") - loss),
        t1_return=-loss,
        t1_loss=loss,
        censored=censored,
    )


def test_valid_entry_and_next_day_0930_sellable() -> None:
    bars = pd.DataFrame(
        [_bar("2025-01-02 10:05"), _bar("2025-01-03 09:30", open="9", high="9", low="9", close="9")]
    )
    result = build_t1_risk_observation(
        "600001", "2025-01-02", bars, ["2025-01-02", "2025-01-03"]
    )

    assert result.status == "VALID"
    assert result.first_sellable_price == Decimal("9")
    assert result.t1_return == Decimal("-0.1")
    assert result.t1_loss == Decimal("0.1")
    assert result.censored is False


@pytest.mark.parametrize(
    "entry", [_bar("2025-01-02 10:05", limit_status="limit_up"), _bar("2025-01-02 10:05", trade_status="suspended")]
)
def test_limit_up_or_suspended_entry_does_not_form_observation(entry) -> None:
    result = build_t1_risk_observation(
        "600001", "2025-01-02", pd.DataFrame([entry]), ["2025-01-02", "2025-01-03"]
    )
    assert result.status == "INVALID_OBSERVATION"
    assert result.failure_reason == "entry_not_fillable"


def test_limit_down_sell_search_continues_and_crosses_lunch() -> None:
    bars = pd.DataFrame(
        [
            _bar("2025-01-02 10:05"),
            _bar("2025-01-03 11:25", limit_status="limit_down"),
            _bar("2025-01-03 13:00", open="9.5", high="9.5", low="9.5", close="9.5"),
        ]
    )
    result = build_t1_risk_observation(
        "600001", "2025-01-02", bars, ["2025-01-02", "2025-01-03"]
    )
    assert result.completion_trade_date == date(2025, 1, 3)
    assert result.first_sellable_price == Decimal("9.5")


def test_sell_search_crosses_to_later_trading_day() -> None:
    bars = pd.DataFrame(
        [
            _bar("2025-01-02 10:05"),
            _bar("2025-01-03 14:55", limit_status="limit_down"),
            _bar("2025-01-06 09:30", open="9", high="9", low="9", close="9"),
        ]
    )
    result = build_t1_risk_observation(
        "600001", "2025-01-02", bars,
        ["2025-01-02", "2025-01-03", "2025-01-06"],
    )
    assert result.status == "VALID"
    assert result.completion_trade_date == date(2025, 1, 6)


def test_fifth_day_last_valid_close_is_censored() -> None:
    calendar = [f"2025-01-0{day}" for day in range(2, 8)]
    bars = pd.DataFrame(
        [
            _bar("2025-01-02 10:05"),
            _bar("2025-01-07 14:50", open="8", high="8", low="8", close="8", limit_status="limit_down"),
        ]
    )
    result = build_t1_risk_observation("600001", "2025-01-02", bars, calendar)
    assert result.status == "VALID"
    assert result.censored is True
    assert result.first_sellable_price == Decimal("8")
    assert result.completion_trade_date == date(2025, 1, 7)


def test_fifth_day_without_valid_close_is_invalid_observation() -> None:
    calendar = [f"2025-01-0{day}" for day in range(2, 8)]
    result = build_t1_risk_observation(
        "600001", "2025-01-02", pd.DataFrame([_bar("2025-01-02 10:05")]), calendar
    )
    assert result.status == "INVALID_OBSERVATION"
    assert result.failure_reason == "missing_fifth_day_valid_close"


def test_linear_quantile_fixed_example_and_censored_count() -> None:
    start = date(2024, 1, 1)
    observations = [
        _obs(start + timedelta(days=index), Decimal(index) / Decimal("1000"), index < 3)
        for index in range(120)
    ]
    quantile, count, censored = linear_quantile_95(observations, date(2025, 1, 1))
    assert quantile == Decimal("0.11305")
    assert count == 120
    assert censored == 3


def test_recent_252_truncation_and_evaluation_date_exclusion() -> None:
    start = date(2024, 1, 1)
    observations = [
        _obs(start + timedelta(days=index), Decimal(index) / Decimal("1000"))
        for index in range(300)
    ]
    observations.append(_obs(date(2026, 1, 1), Decimal("1")))
    _, count, _ = linear_quantile_95(observations, date(2025, 12, 31))
    assert count == 252


def test_120_minimum_and_full_fallback_order() -> None:
    start = date(2024, 1, 1)
    small = [_obs(start + timedelta(days=i), Decimal("0.01")) for i in range(119)]
    enough = [_obs(start + timedelta(days=i), Decimal("0.02")) for i in range(120)]
    metadata = ClassificationMetadata("Bank", "2024-01-01", "2024-01-02", "sw", "v1")
    result = calculate_t1_risk(
        evaluation_as_of="2025-01-01",
        entry_atr="1",
        entry_price="100",
        security_observations=small,
        industry_observations=enough,
        board_observations=enough,
        index_observations={"CSI300": enough, "CSI1000": (), "CHINEXT": ()},
        industry_metadata=metadata,
        board_metadata=metadata,
    )
    assert result.status == Phase4Status.VALID
    assert result.source_level == "INDUSTRY"
    assert result.normal_risk_pct == Decimal("0.02")
    assert result.effective_risk_pct == Decimal("0.02")

    security = calculate_t1_risk(
        evaluation_as_of="2025-01-01", entry_atr="1", entry_price="100",
        security_observations=enough, industry_observations=enough,
        industry_metadata=metadata,
    )
    assert security.source_level == "SECURITY"


def test_invalid_metadata_skips_layer_and_all_insufficient_blocks() -> None:
    start = date(2024, 1, 1)
    enough = [_obs(start + timedelta(days=i), Decimal("0.03")) for i in range(120)]
    invalid = ClassificationMetadata("Bank", "2026-01-01", "2024-01-02", "sw", "v1")
    board = ClassificationMetadata("Main", "2024-01-01", "2024-01-02", "x", "v1")
    fallback = calculate_t1_risk(
        evaluation_as_of="2025-01-01", entry_atr="1", entry_price="100",
        security_observations=(), industry_observations=enough,
        board_observations=enough,
        index_observations={"CSI300": enough, "CSI1000": (), "CHINEXT": ()},
        industry_metadata=invalid, board_metadata=board,
    )
    blocked = calculate_t1_risk(
        evaluation_as_of="2025-01-01", entry_atr="1", entry_price="100",
        security_observations=(), index_observations=(),
    )
    assert fallback.source_level == "BOARD"
    assert blocked.status == Phase4Status.BLOCK_NEW
    assert blocked.failure_reason == "insufficient_t1_risk_samples"


def test_three_index_fallback_merges_sources_without_reweighting() -> None:
    start = date(2024, 1, 1)
    sources = {
        "CSI300": [_obs(start + timedelta(days=i), Decimal("0.01")) for i in range(40)],
        "CSI1000": [_obs(start + timedelta(days=40 + i), Decimal("0.02")) for i in range(40)],
        "CHINEXT": [_obs(start + timedelta(days=80 + i), Decimal("0.03")) for i in range(40)],
    }
    result = calculate_t1_risk(
        evaluation_as_of="2025-01-01", entry_atr="1", entry_price="100",
        security_observations=(), index_observations=sources,
    )
    assert result.status == Phase4Status.VALID
    assert result.source_level == "THREE_INDEX"
    assert result.sample_count == 120


def test_observation_known_after_evaluation_is_excluded() -> None:
    start = date(2024, 1, 1)
    observations = [
        replace(
            _obs(start + timedelta(days=i), Decimal("0.1")),
            completion_trade_date=date(2025, 1, 2),
            completion_bar_start=pd.Timestamp("2025-01-02 09:30", tz="Asia/Shanghai"),
            known_at=pd.Timestamp("2025-01-02 09:35", tz="Asia/Shanghai"),
        )
        for i in range(120)
    ]
    assert linear_quantile_95(observations, "2025-01-01 10:00+08:00") == (
        None,
        0,
        0,
    )


def test_date_only_evaluation_adapts_to_ten_am() -> None:
    start = date(2024, 1, 1)
    observations = [_obs(start + timedelta(days=i), Decimal("0.1")) for i in range(119)]
    observations.append(
        replace(
            _obs(date(2024, 12, 31), Decimal("0.1")),
            completion_trade_date=date(2025, 1, 1),
            completion_bar_start=pd.Timestamp("2025-01-01 09:30", tz="Asia/Shanghai"),
            known_at=pd.Timestamp("2025-01-01 09:35", tz="Asia/Shanghai"),
        )
    )
    assert linear_quantile_95(observations, "2025-01-01")[1] == 120
    assert linear_quantile_95(observations, "2025-01-01 09:30+08:00")[1] == 119


def test_identical_observation_duplicates_count_once() -> None:
    observation = _obs(date(2024, 1, 1), Decimal("0.1"))
    assert linear_quantile_95([observation] * 120, "2025-01-01")[1] == 1


def test_conflicting_observation_key_excluded_order_independently() -> None:
    first = _obs(date(2024, 1, 1), Decimal("0.1"))
    second = replace(first, t1_loss=Decimal("0.2"))
    for records in ([first, second], [second, first]):
        result = calculate_t1_risk(
            evaluation_as_of="2025-01-01",
            entry_atr="1",
            entry_price="100",
            security_observations=records,
        )
        assert result.status == Phase4Status.BLOCK_NEW
        assert result.observation_reasons == (
            "conflicting_t1_observation:SECURITY:600001.SH:2024-01-01",
        )


@pytest.mark.parametrize(
    "loss", [Decimal("-0.1"), Decimal("1.1"), Decimal("NaN"), Decimal("Infinity")]
)
def test_invalid_t1_loss_is_excluded_without_exception(loss: Decimal) -> None:
    observations = [
        _obs(date(2024, 1, 1) + timedelta(days=i), loss) for i in range(120)
    ]
    assert linear_quantile_95(observations, "2025-01-01") == (None, 0, 0)


def test_three_index_cross_source_duplicates_are_deduplicated() -> None:
    observations = [
        _obs(
            date(2024, 1, 1) + timedelta(days=i),
            Decimal("0.1"),
            symbol="000300.SH",
            instrument_type="INDEX",
        )
        for i in range(40)
    ]
    result = calculate_t1_risk(
        evaluation_as_of="2025-01-01",
        entry_atr="1",
        entry_price="100",
        security_observations=(),
        index_observations={key: list(reversed(observations)) for key in ("CSI300", "CSI1000", "CHINEXT")},
    )
    assert result.status == Phase4Status.BLOCK_NEW
    assert result.failure_reason == "insufficient_t1_risk_samples"


def test_recent_252_selection_is_input_order_independent() -> None:
    start = date(2023, 1, 1)
    observations = [
        _obs(start + timedelta(days=i), Decimal(i % 100) / Decimal("100"))
        for i in range(300)
    ]
    forward = linear_quantile_95(observations, "2025-01-01")
    backward = linear_quantile_95(list(reversed(observations)), "2025-01-01")
    assert forward == backward
    assert forward[1] == 252
