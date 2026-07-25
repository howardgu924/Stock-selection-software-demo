from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from stock_picker.strategies.adaptive_trend_v1_3 import (
    MarketCache, PartitionStatus, Phase5Error, create_data_snapshot,
    fetch_complete_partition, validate_partition, verify_data_snapshot,
)


def daily(close="10"):
    return pd.DataFrame([{"symbol":"600001.SH","date":"2025-01-02","open":close,"high":"11","low":"9","close":close}])


def minute(count=48):
    times = list(pd.date_range("2025-01-02 09:30",periods=24,freq="5min")) + list(pd.date_range("2025-01-02 13:00",periods=24,freq="5min"))
    return pd.DataFrame([
        {"symbol":"600001","trade_date":"2025-01-02","bar_start":time,"open":10,"high":11,"low":9,"close":10,"volume":0,"trade_status":"normal","limit_status":"normal"}
        for time in times[:count]
    ])


@pytest.mark.parametrize("count,status",[(48,"COMPLETE"),(47,"PARTIAL"),(1,"PARTIAL"),(0,"PARTIAL")])
def test_minute_completeness(count,status):
    actual,_ = validate_partition("minute_5m_bar",minute(count))
    assert actual.value == status


def test_suspended_zero_minute_partition_is_complete():
    assert validate_partition("minute_5m_bar",pd.DataFrame(),suspended=True)[0] == PartitionStatus.COMPLETE


@pytest.mark.parametrize(
    "change",
    [
        {"open":"0"},{"high":"8"},{"low":"12"},{"close":"NaN"},{"open":"Infinity"},
    ],
)
def test_invalid_daily_ohlc(change):
    frame=daily(); frame.loc[0,list(change)[0]]=list(change.values())[0]
    assert validate_partition("daily_bar",frame)[0] == PartitionStatus.INVALID


def test_partition_versions_do_not_overwrite(tmp_path):
    cache=MarketCache(tmp_path/"cache.sqlite3")
    first=cache.store_partition("daily_bar","k",daily("10").to_dict("records"),source="p",source_version="1")
    second=cache.store_partition("daily_bar","k",daily("10.5").to_dict("records"),source="p",source_version="2")
    assert first.partition_id != second.partition_id
    assert second.supersedes == first.partition_id
    assert cache.load_rows(first.partition_id)[0]["close"] == "10"


def test_identical_partition_is_deduplicated(tmp_path):
    cache=MarketCache(tmp_path/"cache.sqlite3")
    first=cache.store_partition("daily_bar","k",daily().to_dict("records"),source="p",source_version="1")
    second=cache.store_partition("daily_bar","k",daily().to_dict("records"),source="p",source_version="1")
    assert first.partition_id == second.partition_id


def test_snapshot_is_immutable_after_new_partition(tmp_path):
    cache=MarketCache(tmp_path/"cache.sqlite3")
    first=cache.store_partition("daily_bar","k",daily().to_dict("records"),source="p",source_version="1")
    snapshot=create_data_snapshot(cache,[first.partition_id])
    cache.store_partition("daily_bar","k",daily("10.5").to_dict("records"),source="p",source_version="2")
    verify_data_snapshot(cache,snapshot)
    assert cache.snapshot_partition_ids(snapshot.data_snapshot_id)==(first.partition_id,)


@pytest.mark.parametrize("basis",["qfq","hfq","QFQ","HFQ"])
def test_current_adjustment_modes_forbidden(tmp_path,basis):
    cache=MarketCache(tmp_path/"cache.sqlite3")
    part=cache.store_partition("daily_bar","k",daily().to_dict("records"),source="p",source_version="1")
    with pytest.raises(Phase5Error) as caught:
        create_data_snapshot(cache,[part.partition_id],price_basis_id=basis)
    assert caught.value.code=="PRICE_BASIS_MISMATCH"


def test_provider_fallback_discards_incomplete_partition():
    frame,source,version,failures=fetch_complete_partition(
        (("bad","1",lambda:minute(47)),("good","2",lambda:minute(48))),"minute_5m_bar"
    )
    assert len(frame)==48 and source=="good" and failures[0].startswith("bad:PARTIAL")


def test_all_provider_failures_are_stable():
    with pytest.raises(Phase5Error) as caught:
        fetch_complete_partition((("a","1",lambda:minute(1)),("b","1",lambda:(_ for _ in ()).throw(RuntimeError()))),"minute_5m_bar")
    assert caught.value.code=="PROVIDER_FAILED"
