from datetime import date
from decimal import Decimal
import sqlite3

import pytest

from stock_picker.strategies.adaptive_trend_v1_3 import (
    FillResult, FillSide, FillStatus, ExecutionType, Phase5Error, RunStore,
    apply_fill_to_ledger, build_event_clock, rebuild_cash,
)


def fill(side=FillSide.BUY):
    buy=side==FillSide.BUY
    return FillResult(
        FillStatus.FILLED,side,ExecutionType.ENTRY_BUY if buy else ExecutionType.SOFT_EXIT,
        "600001.SH",100,100,"2025-01-02","2025-01-02T10:05:00+08:00",Decimal("10"),
        Decimal("1000"),Decimal("5"),Decimal("0") if buy else Decimal("1"),
        Decimal("0"),Decimal("0"),Decimal("5") if buy else Decimal("6"),
        Decimal("1005") if buy else Decimal("0"),Decimal("0") if buy else Decimal("994"),
        "",False,
    )


def make_run(store):
    config={
        "strategy_version":"v","report_directory":"x",
        "account_snapshot_id":"account","universe_snapshot_id":"universe",
        "data_snapshot_id":"data",
    }
    store.create_run_bundle(
        "r","fp",config,
        account_snapshot={"account_snapshot_id":"account","cash":"10000","positions":()},
        universe_snapshot={"universe_snapshot_id":"universe"},
        data_snapshot={"data_snapshot_id":"data","price_basis_id":"RAW_UNADJUSTED_V1","partition_hashes":()},
        created_at="2025-01-01T00:00:00+08:00",
    )


def test_legacy_non_atomic_create_run_is_rejected(tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3")
    with pytest.raises(Phase5Error) as caught:
        store.create_run("r","fp",{},"data","2025-01-01T00:00:00+08:00")
    assert caught.value.code=="INVALID_CONFIG"


def test_multiple_runs_reuse_identical_immutable_snapshots(tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3")

    def create(run_id, fingerprint, created_at):
        config={
            "strategy_version":"v","report_directory":"x",
            "account_snapshot_id":"account","universe_snapshot_id":"universe",
            "data_snapshot_id":"data",
        }
        store.create_run_bundle(
            run_id,fingerprint,config,
            account_snapshot={
                "account_snapshot_id":"account","snapshot_hash":"account-hash",
                "created_at":created_at,"cash":"10000","positions":(),
            },
            universe_snapshot={
                "universe_snapshot_id":"universe","snapshot_hash":"universe-hash",
                "created_at":created_at,
            },
            data_snapshot={
                "data_snapshot_id":"data","snapshot_hash":"data-hash",
                "created_at":created_at,"price_basis_id":"RAW_UNADJUSTED_V1",
                "partition_hashes":(),
            },
            created_at=created_at,
        )

    create("r1","fp1","2025-01-01T00:00:00+08:00")
    create("r2","fp2","2025-01-02T00:00:00+08:00")

    assert len(store.list_runs()) == 2
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM adaptive_v13_account_snapshots"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM adaptive_v13_universe_snapshots"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM adaptive_v13_data_snapshots"
        ).fetchone()[0] == 1


def test_reused_snapshot_id_rejects_changed_semantic_hash(tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3")
    make_run(store)
    config={
        "strategy_version":"v","report_directory":"x",
        "account_snapshot_id":"account","universe_snapshot_id":"universe",
        "data_snapshot_id":"data",
    }
    with pytest.raises(Phase5Error) as caught:
        store.create_run_bundle(
            "r2","fp2",config,
            account_snapshot={
                "account_snapshot_id":"account","snapshot_hash":"changed",
                "cash":"10000","positions":(),
            },
            universe_snapshot={"universe_snapshot_id":"universe"},
            data_snapshot={
                "data_snapshot_id":"data","price_basis_id":"RAW_UNADJUSTED_V1",
                "partition_hashes":(),
            },
            created_at="2025-01-02T00:00:00+08:00",
        )
    assert caught.value.code == "RUN_FINGERPRINT_MISMATCH"


def test_schema_migration_is_idempotent(tmp_path):
    RunStore(tmp_path/"runs.sqlite3")
    RunStore(tmp_path/"runs.sqlite3")


def test_schema_mismatch_rejected(tmp_path):
    path=tmp_path/"runs.sqlite3"; store=RunStore(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE adaptive_v13_schema_version SET version=999 WHERE component='runs'")
    with pytest.raises(Phase5Error) as caught:
        RunStore(path)
    assert caught.value.code=="SCHEMA_VERSION_MISMATCH"


def test_decimal_columns_are_text(tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3")
    with sqlite3.connect(store.db_path) as connection:
        columns={row[1]:row[2] for row in connection.execute("PRAGMA table_info(adaptive_v13_ledger_events)")}
    assert columns["cash_delta"]=="TEXT" and columns["cash_after"]=="TEXT"


@pytest.mark.parametrize(("side","expected"),[(FillSide.BUY,Decimal("8995")),(FillSide.SELL,Decimal("10994"))])
def test_ledger_applies_fill_once(side,expected,tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3"); make_run(store)
    with store.transaction() as connection:
        event=apply_fill_to_ledger(connection,run_id="r",fill_id="f",fill=fill(side),current_cash=Decimal("10000"))
    assert event.cash_after==expected
    assert rebuild_cash(Decimal("10000"),store.rows("adaptive_v13_ledger_events","r"))==expected


def test_duplicate_fill_ledger_is_blocked_by_database(tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3"); make_run(store)
    with store.transaction() as connection:
        apply_fill_to_ledger(connection,run_id="r",fill_id="f",fill=fill(),current_cash=Decimal("10000"))
    with pytest.raises(Phase5Error) as caught:
        with store.transaction() as connection:
            apply_fill_to_ledger(connection,run_id="r",fill_id="f",fill=fill(),current_cash=Decimal("10000"))
    assert caught.value.code=="DUPLICATE_FILL"
    audits=[
        row for row in store.rows("adaptive_v13_audit_events","r")
        if row["component"]=="account_ledger"
    ]
    assert len(audits)==1


@pytest.mark.parametrize(
    ("side","action"),
    ((FillSide.BUY,"BUY_CASH_DEBIT"),(FillSide.SELL,"SELL_CASH_CREDIT")),
)
def test_ledger_has_independent_exportable_audit(side,action,tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3"); make_run(store)
    with store.transaction() as connection:
        event=apply_fill_to_ledger(
            connection,run_id="r",fill_id="fill-1",fill=fill(side),
            current_cash=Decimal("10000"),
        )
    rows=store.rows("adaptive_v13_audit_events","r")
    audit=next(row for row in rows if row["component"]=="account_ledger")
    assert audit["action"]==action
    assert audit["status"]=="COMPLETED"
    assert set(__import__("json").loads(audit["source_ids_json"]))=={"fill-1",event.ledger_event_id}


def test_ledger_rollback_has_no_success_audit(tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3"); make_run(store)
    with pytest.raises(RuntimeError):
        with store.transaction() as connection:
            apply_fill_to_ledger(
                connection,run_id="r",fill_id="fill-1",fill=fill(),
                current_cash=Decimal("10000"),
            )
            raise RuntimeError("rollback")
    assert store.rows("adaptive_v13_ledger_events","r")==()
    assert not [
        row for row in store.rows("adaptive_v13_audit_events","r")
        if row["component"]=="account_ledger"
    ]


@pytest.mark.parametrize("cash",[Decimal("-1"),Decimal("NaN"),Decimal("Infinity")])
def test_invalid_cash_rejected(cash,tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3"); make_run(store)
    with pytest.raises(Phase5Error):
        with store.transaction() as connection:
            apply_fill_to_ledger(connection,run_id="r",fill_id="f",fill=fill(),current_cash=cash)


def test_event_transaction_rolls_back_everything(tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3"); make_run(store)
    audit_before=store.rows("adaptive_v13_audit_events","r")
    event=build_event_clock("r",(date(2025,1,2),))[0]
    def fail(connection,current):
        connection.execute("INSERT INTO adaptive_v13_audit_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           ("a","r",event.event_id,"x","x","","x","x","x","x","x","x","x","[]","x"))
        raise RuntimeError("boom")
    with pytest.raises(RuntimeError):
        store.process_event("r",event,fail)
    assert store.rows("adaptive_v13_run_events","r")==()
    assert store.rows("adaptive_v13_audit_events","r")==audit_before


def test_completed_event_is_idempotent(tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3"); make_run(store)
    event=build_event_clock("r",(date(2025,1,2),))[0]
    calls=[]
    def handler(connection,current):
        calls.append(1); return {"ok":True},{"cash":"1"}
    assert store.process_event("r",event,handler)=={"ok":True}
    assert store.process_event("r",event,handler) is None
    assert calls==[1]


def test_run_listing_and_status(tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3"); make_run(store)
    store.update_run_status("r","RUNNING")
    assert store.get_run("r")["status"]=="RUNNING"
    assert store.list_runs()[0]["run_id"]=="r"
