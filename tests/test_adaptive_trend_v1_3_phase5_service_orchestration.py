from datetime import date, datetime
from decimal import Decimal
import sqlite3

import pandas as pd
import pytest

from stock_picker.strategies.adaptive_trend_v1_3 import (
    AccountProfile, AccountSnapshot, CoreStrategyDependencies, DataSnapshot,
    DateRangeSpec, MarketCache, NetworkAccessPolicy,
    PartitionRequest, Phase5Error, Phase5Service, ResolvedDateRange, RunConfig,
    RunMode, RunStore, RuntimeHooks, UniverseSnapshot, UniverseSpec, create_run, execute_run,
    resume_run, run_fingerprint,
)
from stock_picker.strategies.adaptive_trend_v1_3.rule_snapshot_service import (
    select_fee_rule_snapshot, select_trading_rule_snapshot,
)


def resolved():
    days=(date(2025,1,2),)
    warm=tuple(item.date() for item in pd.bdate_range(end="2025-01-01",periods=320))
    return ResolvedDateRange(date(2025,1,2),date(2025,1,2),date(2025,1,2),date(2025,1,2),warm[0],days,warm)


def config(tmp_path,network=NetworkAccessPolicy.FORBID):
    return RunConfig(
        RunMode.BACKTEST,"V1.3.13","a","u","d",resolved(),320,"RAW_UNADJUSTED_V1",
        network,str(tmp_path),"EMPTY","2025-01-01T00:00:00+08:00","hash",
    )


def snapshots():
    return {
        "account":AccountSnapshot("a","p",RunMode.BACKTEST,Decimal("1000"),(),"fees","CNY","2025-01-01T00:00:00+08:00","ah"),
        "universe":UniverseSnapshot("u",(),(),(),("000300.SH","000905.SH","000852.SH"),(),"uh","2025-01-01T00:00:00+08:00"),
        "data":DataSnapshot("d",(),"RAW_UNADJUSTED_V1","2025-01-01T00:00:00+08:00","dh",(),("2025-01-02",),("rule",),("fee",),"READY",(("partition","daily_bar","k","provider","1","1d"),)),
    }


def initial_state():
    return {"cash":"1000","positions":{},"pending_sells":{},"exit_controls":{},"cooldowns":{},"fill_requests":()}


def dependencies():
    dates=pd.bdate_range(end="2025-01-01",periods=220)
    history=pd.DataFrame({
        "date":dates,"open":"10","high":"11","low":"9","close":[str(10+i/100) for i in range(220)]
    })
    indexes={symbol:history for symbol in ("000300.SH","000852.SH","399006.SZ")}
    def data_1000(state,event):
        return {
            "market_overlay":{"index_histories":indexes,"as_of":"2025-01-01"},
            "opportunity_score":{"histories":{},"benchmark_histories":indexes,"as_of":"2025-01-01"},
            "divergence":(),"risk_overlay":(),"execution_gate":(),"t1_risk":(),
            "position_sizing":(),
            "portfolio_allocator":{
                "candidates":(),"existing_holdings":(),"portfolio_equity":Decimal("1000"),
                "effective_exposure_cap":Decimal("1"),"evaluation_as_of":"2025-01-02 10:00+08:00",
            },
        }
    return CoreStrategyDependencies(
        decision_1000_data=data_1000,bar_close_data=lambda state,event:{},
        decision_1430_data=lambda state,event:{
            "holdings":(),"replacement_candidates":(),"portfolio_equity":Decimal("1000"),
            "existing_exposure":Decimal("0"),"effective_exposure_cap":Decimal("1"),
            "market_allows_new":True,"emergency_normal":True,"no_new_slots":False,
        },
        session_close_data=lambda state,event:{"holdings":{}},
        minute_bars=lambda request,event:pd.DataFrame(),
        trading_rule=lambda symbol,event:None,fee_rule=lambda event:None,
    )


def profile(tmp_path):
    return AccountProfile(
        "p","100000","200000","f","CNY",UniverseSpec("MANUAL",manual_symbols=("600001",)),
        ("provider",),tmp_path/"data",tmp_path/"reports",
    )


def service(tmp_path,planner=None):
    days=[item.date() for item in pd.bdate_range("2022-01-03",periods=1300)]
    return Phase5Service(
        cache=MarketCache(tmp_path/"cache.sqlite3"),run_store=RunStore(tmp_path/"runs.sqlite3"),
        account_profiles={"p":profile(tmp_path)},trading_calendar=days,
        watchlist_loader=lambda _:["600001"],market_scope_loader=lambda _:["600002"],
        partition_planner=planner,
    )


def test_empty_run_executes_complete_clock_transactionally(tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3"); cfg=config(tmp_path)
    run=create_run(store,cfg,snapshots(),run_id="r")
    state=execute_run(store,run,cfg,initial_state(),trading_calendar=cfg.date_range.trading_dates,dependencies=dependencies())
    assert state["cash"]==Decimal("1000")
    assert store.get_run(run)["status"]=="COMPLETED"
    assert len(store.rows("adaptive_v13_run_events",run))==100
    assert len(store.completed_event_ids(run))==100


def test_decisions_and_daily_snapshot_are_persisted(tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3"); cfg=config(tmp_path)
    run=create_run(store,cfg,snapshots(),run_id="r")
    execute_run(store,run,cfg,initial_state(),dependencies=dependencies())
    assert {
        row["decision_type"] for row in store.rows("adaptive_v13_decisions",run)
    } == {"ENTRY", "EXIT_1430"}
    assert len(store.rows("adaptive_v13_daily_account_snapshots",run))==1


def test_run_requires_network_forbid(tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3"); cfg=config(tmp_path,NetworkAccessPolicy.ALLOW_CACHE_PREPARATION)
    run=create_run(store,cfg,snapshots(),run_id="r")
    with pytest.raises(Phase5Error) as caught:
        execute_run(store,run,cfg,initial_state(),dependencies=dependencies())
    assert caught.value.code=="INVALID_CONFIG"


def test_completed_resume_is_idempotent(tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3"); cfg=config(tmp_path)
    run=create_run(store,cfg,snapshots(),run_id="r")
    loaded=store.load_snapshot_bundle(run)
    assert loaded["account"]["account_snapshot_id"]=="a"
    assert loaded["universe"]["universe_snapshot_id"]=="u"
    assert loaded["data"]["data_snapshot_id"]=="d"


def test_resume_rejects_fingerprint_change(tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3"); cfg=config(tmp_path)
    with pytest.raises(Phase5Error) as caught:
        create_run(store,cfg,{"data":"a"},run_id="r")
    assert caught.value.code=="INVALID_CONFIG"


def test_service_prepares_complete_snapshot(tmp_path):
    def planner(universe,date_range,*_):
        dates=(*date_range.warmup_dates,*date_range.trading_dates)
        frame=pd.DataFrame([{"symbol":"600001","date":day,"open":"10","high":"11","low":"9","close":"10"} for day in dates])
        return [PartitionRequest("daily_bar","600001:2025",(("p","1",lambda:frame),),requested_trade_dates=dates)]
    svc=service(tmp_path,planner)
    report=svc.prepare_market_cache(
        UniverseSpec("MANUAL",manual_symbols=("600001",)),
        DateRangeSpec("CUSTOM",start_date="2026-01-01",end_date="2026-01-30"),"p","BACKTEST",
    )
    assert report.status=="READY"
    assert report.data_snapshot_id
    assert svc.validate_data_readiness(report)
    snapshot=svc.cache.load_snapshot(report.data_snapshot_id)
    actions={row["action"] for row in svc.cache.audit_rows(snapshot.preparation_id)}
    assert {
        "CACHE_LOOKUP","CACHE_MISS","MISSING_TRADING_DATES",
        "PROVIDER_ATTEMPT","PARTITION_VALIDATED",
        "DATA_SNAPSHOT_CREATED","DATA_READINESS_READY",
    } <= actions


def test_service_reports_provider_failure_without_snapshot(tmp_path):
    planner=lambda *_:[PartitionRequest("daily_bar","k",(("p","1",lambda:pd.DataFrame()),))]
    svc=service(tmp_path,planner)
    report=svc.prepare_market_cache(
        UniverseSpec("MANUAL",manual_symbols=("600001",)),
        DateRangeSpec("CUSTOM",start_date="2026-01-01",end_date="2026-01-30"),"p","BACKTEST",
    )
    assert report.status=="NOT_READY"
    assert report.failed_partitions==("k",)
    assert not report.data_snapshot_id
    with sqlite3.connect(svc.cache.db_path) as connection:
        preparation_id=connection.execute(
            "SELECT preparation_id FROM adaptive_v13_market_cache_audit ORDER BY created_at LIMIT 1"
        ).fetchone()[0]
    rows=svc.cache.audit_rows(preparation_id)
    assert "DATA_READINESS_FAILED" in {row["action"] for row in rows}


def test_partial_cache_gap_has_independent_audit(tmp_path):
    day1=date(2025,1,2); day2=date(2025,1,3)
    def planner(*_):
        frame=pd.DataFrame([
            {"symbol":"600001","date":day2,"open":"10","high":"11","low":"9","close":"10"}
        ])
        return [PartitionRequest(
            "daily_bar","partial-key",(("provider","2",lambda:frame),),
            requested_trade_dates=(day1,day2),
        )]
    svc=service(tmp_path,planner)
    svc.cache.store_partition(
        "daily_bar","partial-key",
        [{"symbol":"600001","date":day1,"open":"10","high":"11","low":"9","close":"10"}],
        source="cache",source_version="1",expected_trade_dates=(day1,),
    )
    report=svc.prepare_market_cache(
        UniverseSpec("MANUAL",manual_symbols=("600001",)),
        DateRangeSpec("CUSTOM",start_date="2026-01-01",end_date="2026-01-30"),
        "p","BACKTEST",
    )
    assert report.status=="NOT_READY"
    with sqlite3.connect(svc.cache.db_path) as connection:
        preparation_id=connection.execute(
            "SELECT preparation_id FROM adaptive_v13_market_cache_audit ORDER BY created_at LIMIT 1"
        ).fetchone()[0]
    actions={row["action"] for row in svc.cache.audit_rows(preparation_id)}
    assert {"CACHE_PARTIAL","MISSING_TRADING_DATES","PROVIDER_ATTEMPT"} <= actions


def test_provider_fallback_audit_records_rejection_and_final_source(tmp_path):
    times = list(pd.date_range("2025-01-02 09:30",periods=24,freq="5min")) + list(
        pd.date_range("2025-01-02 13:00",periods=24,freq="5min")
    )
    def frame(count):
        return pd.DataFrame([{
            "symbol":"600001","trade_date":"2025-01-02","bar_start":stamp,
            "open":"10","high":"11","low":"9","close":"10","volume":"1",
            "trade_status":"normal","limit_status":"normal",
        } for stamp in times[:count]])
    planner=lambda *_:[PartitionRequest(
        "minute_5m_bar","minute-key",
        (("first","1",lambda:frame(47)),("second","2",lambda:frame(48))),
        requested_trade_dates=(date(2025,1,2),),
    )]
    svc=service(tmp_path,planner)
    report=svc.prepare_market_cache(
        UniverseSpec("MANUAL",manual_symbols=("600001",)),
        DateRangeSpec("CUSTOM",start_date="2026-01-01",end_date="2026-01-30"),
        "p","BACKTEST",
    )
    assert report.status=="NOT_READY"
    with sqlite3.connect(svc.cache.db_path) as connection:
        preparation_id=connection.execute(
            "SELECT preparation_id FROM adaptive_v13_market_cache_audit ORDER BY created_at LIMIT 1"
        ).fetchone()[0]
    rows=svc.cache.audit_rows(preparation_id)
    attempts=[row["source"] for row in rows if row["action"]=="PROVIDER_ATTEMPT"]
    rejected=[row for row in rows if row["action"]=="PROVIDER_REJECTED"]
    fallback=[row for row in rows if row["action"]=="PROVIDER_FALLBACK"]
    assert attempts==["first","second"]
    assert rejected[0]["source"]=="first"
    assert fallback[0]["source"]=="second"


def test_cache_hit_does_not_call_provider_again(tmp_path):
    calls=[]
    def planner(universe,date_range,*_):
        dates=(*date_range.warmup_dates,*date_range.trading_dates)
        frame=pd.DataFrame([{"symbol":"600001","date":day,"open":"10","high":"11","low":"9","close":"10"} for day in dates])
        def fetch(): calls.append(1); return frame
        return [PartitionRequest("daily_bar","k",(("p","1",fetch),),requested_trade_dates=dates)]
    svc=service(tmp_path,planner)
    args=(UniverseSpec("MANUAL",manual_symbols=("600001",)),DateRangeSpec("CUSTOM",start_date="2026-01-01",end_date="2026-01-30"),"p","BACKTEST")
    svc.prepare_market_cache(*args); svc.prepare_market_cache(*args)
    assert calls==[1]


class RuleRecord:
    def __init__(self,symbol="600001",effective_date="2025-01-02",known_at="2025-01-02 09:00+08:00",version="1"):
        self.symbol=symbol; self.effective_date=effective_date; self.known_at=known_at; self.rule_version=version


@pytest.mark.parametrize(
    ("effective","known"),
    [("2025-01-01","2025-01-02 09:00+08:00"),("2025-01-03","2025-01-02 09:00+08:00"),("2025-01-02","2025-01-02 10:01+08:00")],
)
def test_invalid_historical_rule_timing_is_missing(effective,known):
    with pytest.raises(Phase5Error) as caught:
        select_trading_rule_snapshot([RuleRecord(effective_date=effective,known_at=known)],"600001","2025-01-02 10:00+08:00")
    assert caught.value.code=="RULE_SNAPSHOT_MISSING"


def test_latest_known_rule_selected():
    rows=[RuleRecord(known_at="2025-01-02 08:00+08:00",version="1"),RuleRecord(known_at="2025-01-02 09:00+08:00",version="2")]
    assert select_trading_rule_snapshot(rows,"600001","2025-01-02 10:00+08:00").rule_version=="2"


STABLE_CODES = (
    "INVALID_CONFIG","INVALID_UNIVERSE","INVALID_DATE_RANGE","INSUFFICIENT_WARMUP",
    "DATA_NOT_READY","PARTIAL_CACHE","INVALID_PARTITION","PROVIDER_FAILED",
    "RULE_SNAPSHOT_MISSING","FEE_SNAPSHOT_MISSING","PRICE_BASIS_MISMATCH",
    "LOOKAHEAD_ACCESS","DUPLICATE_EVENT","DUPLICATE_FILL","LEDGER_CONFLICT",
    "STATE_VERSION_CONFLICT","SCHEMA_VERSION_MISMATCH","RUN_FINGERPRINT_MISMATCH",
    "REPORT_WRITE_FAILED","MISSING_MARK_PRICE","UNEXPECTED_ENGINE_ERROR",
)


@pytest.mark.parametrize("code",STABLE_CODES)
def test_stable_error_code_round_trip(code):
    error=Phase5Error(code)
    assert error.code==code and str(error)==code


@pytest.mark.parametrize("table",["run_events","fill_requests","fills","ledger_events","position_state_versions","pending_sell_versions"])
def test_database_has_unique_constraints_for_replay(tmp_path,table):
    store=RunStore(tmp_path/"runs.sqlite3")
    with sqlite3.connect(store.db_path) as connection:
        indexes=list(connection.execute(f"PRAGMA index_list(adaptive_v13_{table})"))
    assert any(row[2] for row in indexes)
