"""Decimal cash ledger coupled to fill and position state in one transaction."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import sqlite3

from .event_clock import deterministic_id
from .phase3_models import FillResult, FillSide, FillStatus
from .phase5_models import LedgerEvent, Phase5Error
from .run_store import canonical_json, stable_hash


def apply_fill_to_ledger(
    connection: sqlite3.Connection, *, run_id: str, fill_id: str,
    fill: FillResult, current_cash: Decimal,
) -> LedgerEvent:
    if fill.status != FillStatus.FILLED:
        raise Phase5Error("LEDGER_CONFLICT", "fill_not_filled")
    if not isinstance(current_cash, Decimal) or not current_cash.is_finite() or current_cash < 0:
        raise Phase5Error("LEDGER_CONFLICT", "invalid_cash")
    delta = -fill.cash_required if fill.side == FillSide.BUY else fill.net_proceeds
    after = current_cash + delta
    if after < 0:
        raise Phase5Error("LEDGER_CONFLICT", "negative_cash")
    event_id = deterministic_id("ledger", run_id, fill_id)
    created_at = datetime.now().astimezone().isoformat()
    try:
        connection.execute(
            """INSERT INTO adaptive_v13_ledger_events
            (ledger_event_id,run_id,fill_id,cash_delta,cash_after,created_at) VALUES(?,?,?,?,?,?)""",
            (event_id,run_id,fill_id,str(delta),str(after),created_at),
        )
        action="BUY_CASH_DEBIT" if fill.side == FillSide.BUY else "SELL_CASH_CREDIT"
        output={
            "fill_id":fill_id,"ledger_event_id":event_id,"cash_before":current_cash,
            "cash_delta":delta,"cash_after":after,"fee_total":fill.total_fees,
        }
        audit_id=stable_hash(("audit",run_id,event_id,"account_ledger",action))
        connection.execute(
            """INSERT INTO adaptive_v13_audit_events
            (audit_id,run_id,event_id,event_at,event_type,symbol,component,action,status,
             reason_code,message,input_hash,output_hash,source_ids_json,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (audit_id,run_id,event_id,created_at,"LEDGER",fill.symbol,"account_ledger",
             action,"COMPLETED","","cash ledger event persisted",
             stable_hash({"fill_id":fill_id}),stable_hash(output),
             canonical_json((fill_id,event_id)),created_at),
        )
    except sqlite3.IntegrityError as exc:
        raise Phase5Error("DUPLICATE_FILL") from exc
    return LedgerEvent(event_id,run_id,fill_id,delta,after,created_at)


def rebuild_cash(initial_cash: Decimal, rows: tuple[dict[str, object], ...]) -> Decimal:
    cash = initial_cash
    seen: set[str] = set()
    for row in rows:
        fill_id = str(row["fill_id"])
        if fill_id in seen:
            raise Phase5Error("LEDGER_CONFLICT", "duplicate_fill_in_ledger")
        seen.add(fill_id)
        cash += Decimal(str(row["cash_delta"]))
        if cash != Decimal(str(row["cash_after"])):
            raise Phase5Error("LEDGER_CONFLICT", "cash_chain_mismatch")
    return cash
