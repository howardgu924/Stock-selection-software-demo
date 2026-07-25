from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from stock_picker.strategies.adaptive_trend_v1_3 import (
    Phase5Error, select_session_close_mark,
)
from stock_picker.strategies.adaptive_trend_v1_3.run_recovery import (
    _recovery_as_of_trade_date,
)


DAY = date(2025, 1, 8)
BASIS = "RAW_UNADJUSTED_V1"
PARTITIONS = ("minute-p",)


def bar(at, price, **changes):
    item = {
        "symbol":"600001.SH","trade_date":DAY,
        "bar_start":pd.Timestamp(f"{DAY} {at}",tz="Asia/Shanghai"),
        "open":price,"high":price,"low":price,"close":price,
        "volume":"1","trade_status":"normal","price_basis_id":BASIS,
        "source_partition_id":"minute-p",
    }
    item.update(changes)
    return item


def select(rows, *, status="normal", previous=None):
    return select_session_close_mark(
        symbol="600001",trade_date=DAY,bars=pd.DataFrame(rows),
        previous_valid_mark=previous,session_status=status,
        expected_price_basis_id=BASIS,allowed_partition_ids=PARTITIONS,
    )


def previous(**changes):
    value = {
        "symbol":"600001.SH","trade_date":"2025-01-07","mark_price":"9.8",
        "price_basis_id":BASIS,"source_partition_id":"minute-p",
    }
    value.update(changes)
    return value


def test_close_mark_prefers_valid_1455():
    result=select([bar("14:50","10"),bar("14:55","11")])
    assert (result.status,result.mark_price,result.mark_bar_start)==(
        "VALID",Decimal("11"),"14:55",
    )


def test_close_mark_falls_back_to_latest_valid_intraday_bar():
    result=select([
        bar("14:55","11",high="10"),
        bar("14:45","9"),
        bar("14:50","10"),
    ])
    assert (result.mark_price,result.mark_bar_start)==(Decimal("10"),"14:50")


def test_close_mark_is_order_independent():
    forward=select([bar("14:45","9"),bar("14:50","10")])
    reverse=select([bar("14:50","10"),bar("14:45","9")])
    assert forward == reverse


def test_suspended_session_may_use_earlier_authoritative_mark():
    result=select([],status="suspended",previous=previous())
    assert result.status=="VALID"
    assert result.used_previous_mark is True
    assert result.mark_price==Decimal("9.8")


@pytest.mark.parametrize("status",("normal","unknown",""))
def test_non_suspended_session_cannot_use_previous_mark(status):
    result=select([],status=status,previous=previous())
    assert (result.status,result.failure_reason)==("INVALID","MISSING_MARK_PRICE")


@pytest.mark.parametrize("changes",(
    {"price_basis_id":"QFQ"},
    {"trade_date":"2025-01-08"},
    {"source_partition_id":"other"},
))
def test_invalid_previous_mark_is_rejected(changes):
    result=select([],status="suspended",previous=previous(**changes))
    assert result.status=="INVALID"


@pytest.mark.parametrize("changes",(
    {"trade_date":"2025-01-07"},
    {"price_basis_id":"QFQ"},
    {"source_partition_id":"other"},
    {"close":"NaN"},
))
def test_invalid_current_bar_is_not_a_mark(changes):
    result=select([bar("14:55","11",**changes)])
    assert result.status=="INVALID"


class Config:
    class Range:
        trading_dates=(date(2025,1,2),date(2025,1,3),date(2025,1,6),date(2025,1,7))
    date_range=Range()


@pytest.mark.parametrize(("checkpoint","calendar","expected"),(
    ({"trade_date":"2025-01-03"},(),date(2025,1,3)),
    ({"trade_date":"2025-01-03"},(date(2025,1,10),),date(2025,1,3)),
    ({"trade_date":""},(date(2024,12,31),date(2025,1,1)),date(2025,1,1)),
    ({"trade_date":""},(date(2025,1,2),),date(2025,1,2)),
))
def test_recovery_as_of_uses_checkpoint_not_run_end(checkpoint,calendar,expected):
    assert _recovery_as_of_trade_date(checkpoint,Config(),calendar)==expected


def test_invalid_bar_frame_type_degrades_deterministically():
    result=select_session_close_mark(
        symbol="600001",trade_date=DAY,bars=None,previous_valid_mark=None,
        session_status="normal",expected_price_basis_id=BASIS,
        allowed_partition_ids=PARTITIONS,
    )
    assert result.status=="INVALID"
    assert result.failure_reason=="MISSING_MARK_PRICE"
