"""Versioned immutable SQLite market cache and complete-partition fallback."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterable, Sequence

import pandas as pd

from .adaptive_v13_schema import CACHE_SCHEMA, SCHEMA_VERSION
from .minute_contract import LEGAL_BAR_START_TIMES, validate_minute_bars
from .phase5_models import CachePartition, DataSnapshot, PartitionStatus, Phase5Error
from .run_store import canonical_json, stable_hash

RAW_PRICE_BASIS = "RAW_UNADJUSTED_V1"


class MarketCache:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(CACHE_SCHEMA)
            row = connection.execute(
                "SELECT version FROM adaptive_v13_schema_version WHERE component='cache'"
            ).fetchone()
            if row is not None and int(row[0]) > SCHEMA_VERSION:
                raise Phase5Error("SCHEMA_VERSION_MISMATCH")
            columns = {
                item[1] for item in connection.execute(
                    "PRAGMA table_info(adaptive_v13_cache_partitions)"
                )
            }
            additions = {
                "normalized_symbol": "TEXT NOT NULL DEFAULT ''",
                "frequency": "TEXT NOT NULL DEFAULT ''",
                "coverage_start_date": "TEXT NOT NULL DEFAULT ''",
                "coverage_end_date": "TEXT NOT NULL DEFAULT ''",
                "covered_trade_dates_json": "TEXT NOT NULL DEFAULT '[]'",
                "expected_trade_dates_json": "TEXT NOT NULL DEFAULT '[]'",
                "partition_version": "INTEGER NOT NULL DEFAULT 1",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE adaptive_v13_cache_partitions ADD COLUMN {name} {declaration}"
                    )
            snapshot_columns = {
                item[1] for item in connection.execute(
                    "PRAGMA table_info(adaptive_v13_immutable_data_snapshots)"
                )
            }
            if "snapshot_json" not in snapshot_columns:
                connection.execute(
                    """ALTER TABLE adaptive_v13_immutable_data_snapshots
                    ADD COLUMN snapshot_json TEXT NOT NULL DEFAULT '{}'"""
                )
            connection.execute(
                """INSERT INTO adaptive_v13_schema_version(component,version) VALUES('cache',?)
                ON CONFLICT(component) DO UPDATE SET version=excluded.version""",
                (SCHEMA_VERSION,),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def store_partition(
        self, dataset_type: str, logical_key: str, rows: Iterable[dict[str, Any]],
        *, source: str, source_version: str, price_basis_id: str = RAW_PRICE_BASIS,
        status: PartitionStatus | str = PartitionStatus.COMPLETE, reasons: Sequence[str] = (),
        normalized_symbol: str = "", frequency: str = "",
        expected_trade_dates: Iterable[object] = (),
    ) -> CachePartition:
        normalized = sorted((_canonical_row(row) for row in rows), key=canonical_json)
        expected_dates = tuple(sorted({_date_text(value) for value in expected_trade_dates if _date_text(value)}))
        covered_dates = tuple(sorted({_row_date(row) for row in normalized if _row_date(row)}))
        if expected_dates:
            validation_status, validation_reasons = validate_coverage(
                dataset_type, pd.DataFrame(normalized), expected_dates
            )
            status = validation_status
            reasons = (*reasons, *validation_reasons)
        content_hash = stable_hash(normalized)
        partition_id = sha256(canonical_json({
            "dataset_type": dataset_type, "logical_key": logical_key,
            "source": source, "source_version": source_version,
            "price_basis_id": price_basis_id, "rows": normalized,
        }).encode("utf-8")).hexdigest()
        parsed_status = PartitionStatus(status)
        created_at = datetime.now().astimezone().isoformat()
        with self._connect() as connection:
            previous = connection.execute(
                """SELECT partition_id FROM adaptive_v13_cache_partitions
                WHERE logical_key=? ORDER BY created_at DESC,partition_id DESC LIMIT 1""",
                (logical_key,),
            ).fetchone()
            supersedes = "" if previous is None or previous[0] == partition_id else previous[0]
            version = 1 if previous is None else connection.execute(
                "SELECT COALESCE(MAX(partition_version),0)+1 FROM adaptive_v13_cache_partitions WHERE logical_key=?",
                (logical_key,),
            ).fetchone()[0]
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT OR IGNORE INTO adaptive_v13_cache_partitions
                (partition_id,dataset_type,logical_key,status,source,source_version,price_basis_id,
                 row_count,content_sha256,supersedes,reasons_json,created_at,normalized_symbol,
                 frequency,coverage_start_date,coverage_end_date,covered_trade_dates_json,
                 expected_trade_dates_json,partition_version)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (partition_id,dataset_type,logical_key,parsed_status.value,source,source_version,
                 price_basis_id,len(normalized),content_hash,supersedes,canonical_json(tuple(reasons)),created_at,
                 normalized_symbol,frequency,covered_dates[0] if covered_dates else "",
                 covered_dates[-1] if covered_dates else "",canonical_json(covered_dates),
                 canonical_json(expected_dates),version),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO adaptive_v13_cache_rows(partition_id,row_number,row_json) VALUES(?,?,?)",
                [(partition_id,index,canonical_json(row)) for index,row in enumerate(normalized)],
            )
            connection.commit()
        return CachePartition(partition_id,dataset_type,logical_key,parsed_status,source,source_version,
                              price_basis_id,len(normalized),content_hash,supersedes,tuple(reasons),
                              normalized_symbol,frequency,covered_dates[0] if covered_dates else "",
                              covered_dates[-1] if covered_dates else "",covered_dates,expected_dates,version)

    def latest_complete(self, logical_key: str) -> CachePartition | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM adaptive_v13_cache_partitions
                WHERE logical_key=? AND status='COMPLETE'
                ORDER BY created_at DESC,partition_id DESC LIMIT 1""", (logical_key,)
            ).fetchone()
        return None if row is None else _partition(row)

    def coverage(
        self, logical_key: str, requested_trade_dates: Iterable[object]
    ) -> tuple[tuple[CachePartition, ...], tuple[str, ...]]:
        requested = tuple(sorted({_date_text(value) for value in requested_trade_dates if _date_text(value)}))
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM adaptive_v13_cache_partitions
                WHERE logical_key=? AND status='COMPLETE'
                ORDER BY partition_version,partition_id""", (logical_key,)
            ).fetchall()
        partitions = tuple(_partition(row) for row in rows)
        covered = set()
        for partition in partitions:
            covered.update(partition.covered_trade_dates)
        missing = tuple(day for day in requested if day not in covered)
        return partitions, missing

    def load_rows(self, partition_id: str) -> tuple[dict[str, Any], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT row_json FROM adaptive_v13_cache_rows WHERE partition_id=? ORDER BY row_number",
                (partition_id,),
            ).fetchall()
        return tuple(json.loads(row[0]) for row in rows)

    def create_snapshot(
        self, partition_ids: Iterable[str], *, price_basis_id: str = RAW_PRICE_BASIS,
        required_trade_dates: Iterable[object] = (), rule_snapshot_ids: Iterable[str] = (),
        fee_snapshot_ids: Iterable[str] = (),
        preparation_id: str = "",
    ) -> DataSnapshot:
        ids = tuple(sorted(set(partition_ids)))
        if not ids:
            raise Phase5Error("DATA_NOT_READY", "empty_snapshot")
        with self._connect() as connection:
            marks = ",".join("?" for _ in ids)
            rows = connection.execute(
                f"""SELECT partition_id,status,price_basis_id,content_sha256,
                covered_trade_dates_json,dataset_type,logical_key,source,source_version,
                normalized_symbol,frequency,expected_trade_dates_json
                FROM adaptive_v13_cache_partitions
                WHERE partition_id IN ({marks})""", ids
            ).fetchall()
            if len(rows) != len(ids) or any(row["status"] != "COMPLETE" for row in rows):
                raise Phase5Error("DATA_NOT_READY")
            if any(row["price_basis_id"] != price_basis_id for row in rows):
                raise Phase5Error("PRICE_BASIS_MISMATCH")
            required = tuple(sorted({_date_text(value) for value in required_trade_dates if _date_text(value)}))
            covered = set()
            for row in rows:
                covered.update(json.loads(row["covered_trade_dates_json"]))
            if required and not set(required).issubset(covered):
                raise Phase5Error("DATA_NOT_READY", "snapshot_coverage_incomplete")
            hashes = tuple(sorted((row["partition_id"],row["content_sha256"]) for row in rows))
            metadata = tuple(sorted(
                (
                    row["partition_id"], row["dataset_type"], row["logical_key"],
                    row["source"], row["source_version"], row["frequency"],
                    row["normalized_symbol"], row["covered_trade_dates_json"],
                    row["expected_trade_dates_json"],
                )
                for row in rows
            ))
            rules = tuple(sorted(set(rule_snapshot_ids))) or tuple(sorted(
                row["partition_id"] for row in rows if row["dataset_type"] == "trading_rule_snapshot"
            ))
            fees = tuple(sorted(set(fee_snapshot_ids))) or tuple(sorted(
                row["partition_id"] for row in rows if row["dataset_type"] == "fee_rule_snapshot"
            ))
            snapshot_hash = stable_hash({
                "partition_hashes": hashes, "price_basis_id": price_basis_id,
                "required_trade_dates": required, "rule_snapshot_ids": rules,
                "fee_snapshot_ids": fees, "readiness_status": "READY",
                "partition_metadata": metadata,
                "preparation_id": preparation_id,
            })
            snapshot_id = f"data_{snapshot_hash}"
            created_at = datetime.now().astimezone().isoformat()
            snapshot = DataSnapshot(
                snapshot_id,ids,price_basis_id,created_at,snapshot_hash,hashes,required,
                rules,fees,"READY",metadata,
                preparation_id,
            )
            connection.execute(
                """INSERT OR IGNORE INTO adaptive_v13_immutable_data_snapshots
                (data_snapshot_id,snapshot_hash,price_basis_id,created_at,snapshot_json)
                VALUES(?,?,?,?,?)""",
                (snapshot_id,snapshot_hash,price_basis_id,created_at,canonical_json(snapshot)),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO adaptive_v13_snapshot_partition_links(data_snapshot_id,partition_id) VALUES(?,?)",
                [(snapshot_id,item) for item in ids],
            )
        return snapshot

    def verify_snapshot(self, snapshot: DataSnapshot) -> None:
        expected_snapshot_hash = stable_hash({
            "partition_hashes": snapshot.partition_hashes,
            "price_basis_id": snapshot.price_basis_id,
            "required_trade_dates": snapshot.required_trade_dates,
            "rule_snapshot_ids": snapshot.rule_snapshot_ids,
            "fee_snapshot_ids": snapshot.fee_snapshot_ids,
            "readiness_status": snapshot.readiness_status,
            "partition_metadata": snapshot.partition_metadata,
            "preparation_id": snapshot.preparation_id,
        })
        if expected_snapshot_hash != snapshot.snapshot_hash:
            raise Phase5Error("RUN_FINGERPRINT_MISMATCH", "snapshot_hash_mismatch")
        if not set((*snapshot.rule_snapshot_ids, *snapshot.fee_snapshot_ids)).issubset(
            snapshot.partition_ids
        ):
            raise Phase5Error("DATA_NOT_READY", "rule_or_fee_snapshot_missing")
        covered: set[str] = set()
        with self._connect() as connection:
            for partition_id, expected_hash in snapshot.partition_hashes:
                row = connection.execute(
                    """SELECT content_sha256,status,price_basis_id,covered_trade_dates_json,
                    dataset_type,logical_key,source,source_version,frequency,
                    normalized_symbol,expected_trade_dates_json
                    FROM adaptive_v13_cache_partitions WHERE partition_id=?""", (partition_id,)
                ).fetchone()
                if row is None or row["status"] != "COMPLETE":
                    raise Phase5Error("DATA_NOT_READY", "snapshot_partition_missing")
                if row["content_sha256"] != expected_hash:
                    raise Phase5Error("RUN_FINGERPRINT_MISMATCH", "partition_hash_mismatch")
                if row["price_basis_id"] != snapshot.price_basis_id:
                    raise Phase5Error("PRICE_BASIS_MISMATCH")
                covered.update(json.loads(row["covered_trade_dates_json"]))
                actual_metadata = (
                    partition_id,row["dataset_type"],row["logical_key"],row["source"],
                    row["source_version"],row["frequency"],row["normalized_symbol"],
                    row["covered_trade_dates_json"],row["expected_trade_dates_json"],
                )
                if snapshot.partition_metadata and actual_metadata not in snapshot.partition_metadata:
                    raise Phase5Error("RUN_FINGERPRINT_MISMATCH", "partition_metadata_mismatch")
            linked = set(self.snapshot_partition_ids(snapshot.data_snapshot_id))
            if linked != set(snapshot.partition_ids):
                raise Phase5Error("DATA_NOT_READY", "snapshot_link_missing")
        if not set(snapshot.required_trade_dates).issubset(covered):
            raise Phase5Error("DATA_NOT_READY", "snapshot_coverage_incomplete")

    def snapshot_partition_ids(self, data_snapshot_id: str) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT partition_id FROM adaptive_v13_snapshot_partition_links WHERE data_snapshot_id=? ORDER BY partition_id",
                (data_snapshot_id,),
            ).fetchall()
        return tuple(row[0] for row in rows)

    def load_snapshot(self, data_snapshot_id: str) -> DataSnapshot:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM adaptive_v13_immutable_data_snapshots WHERE data_snapshot_id=?",
                (data_snapshot_id,),
            ).fetchone()
            if row is None:
                raise Phase5Error("DATA_NOT_READY","data_snapshot_missing")
            persisted = json.loads(row["snapshot_json"])
            if persisted:
                return DataSnapshot(
                    data_snapshot_id=persisted["data_snapshot_id"],
                    partition_ids=tuple(persisted["partition_ids"]),
                    price_basis_id=persisted["price_basis_id"],
                    created_at=persisted["created_at"],
                    snapshot_hash=persisted["snapshot_hash"],
                    partition_hashes=tuple(tuple(item) for item in persisted["partition_hashes"]),
                    required_trade_dates=tuple(persisted["required_trade_dates"]),
                    rule_snapshot_ids=tuple(persisted["rule_snapshot_ids"]),
                    fee_snapshot_ids=tuple(persisted["fee_snapshot_ids"]),
                    readiness_status=persisted["readiness_status"],
                    partition_metadata=tuple(tuple(item) for item in persisted["partition_metadata"]),
                    preparation_id=persisted.get("preparation_id",""),
                )
            ids = self.snapshot_partition_ids(data_snapshot_id)
            marks = ",".join("?" for _ in ids)
            partitions = connection.execute(
                f"""SELECT partition_id,content_sha256,covered_trade_dates_json,
                dataset_type,logical_key,source,source_version,frequency,
                normalized_symbol,expected_trade_dates_json
                FROM adaptive_v13_cache_partitions WHERE partition_id IN ({marks})""",ids
            ).fetchall() if ids else ()
        hashes = tuple(sorted((item["partition_id"],item["content_sha256"]) for item in partitions))
        covered = tuple(sorted({day for item in partitions for day in json.loads(item["covered_trade_dates_json"])}))
        metadata = tuple(sorted(
            (
                item["partition_id"], item["dataset_type"], item["logical_key"],
                item["source"], item["source_version"], item["frequency"],
                item["normalized_symbol"],item["covered_trade_dates_json"],
                item["expected_trade_dates_json"],
            )
            for item in partitions
        ))
        return DataSnapshot(
            data_snapshot_id,ids,row["price_basis_id"],row["created_at"],row["snapshot_hash"],
            hashes,covered,(),(),"READY",metadata,
            "",
        )

    def append_audit(
        self, *, preparation_id: str, action: str, status: str,
        logical_key: str = "", symbol: str = "", dataset_type: str = "",
        source: str = "", source_version: str = "", reason_code: str = "",
        covered_dates=(), missing_dates=(), input_value=None, output_value=None,
        data_snapshot_id: str = "",
    ) -> None:
        created_at=datetime.now().astimezone().isoformat()
        payload=(
            preparation_id,data_snapshot_id,logical_key,symbol,dataset_type,source,
            source_version,action,status,reason_code,
            stable_hash(tuple(sorted(map(str,covered_dates)))),
            stable_hash(tuple(sorted(map(str,missing_dates)))),
            stable_hash(input_value),stable_hash(output_value),created_at,
        )
        audit_id=stable_hash(("cache_audit",*payload))
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO adaptive_v13_market_cache_audit
                (audit_id,preparation_id,data_snapshot_id,logical_key,symbol,dataset_type,
                 source,source_version,action,status,reason_code,covered_dates_hash,
                 missing_dates_hash,input_hash,output_hash,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (audit_id,*payload),
            )

    def audit_rows(self, preparation_id: str) -> tuple[dict[str,Any],...]:
        with self._connect() as connection:
            rows=connection.execute(
                """SELECT * FROM adaptive_v13_market_cache_audit
                WHERE preparation_id=? ORDER BY created_at,audit_id""",(preparation_id,)
            ).fetchall()
        return tuple(dict(row) for row in rows)


def validate_partition(dataset_type: str, rows: pd.DataFrame, *, suspended: bool = False) -> tuple[PartitionStatus, tuple[str, ...]]:
    if dataset_type == "minute_5m_bar":
        if rows.empty:
            return (PartitionStatus.COMPLETE, ()) if suspended else (PartitionStatus.PARTIAL, ("unknown_empty_minute_partition",))
        result = validate_minute_bars(rows)
        if result.status != "VALID":
            return PartitionStatus.INVALID, result.invalid_reasons
        count = len(result.bars)
        return (PartitionStatus.COMPLETE, ()) if count == len(LEGAL_BAR_START_TIMES) else (PartitionStatus.PARTIAL, (f"expected_48_bars:actual_{count}",))
    if dataset_type in {"daily_bar", "benchmark_daily_bar"}:
        if len(rows) != 1:
            return PartitionStatus.PARTIAL, (f"expected_1_row:actual_{len(rows)}",)
        required = ("open","high","low","close")
        values = [_decimal(rows.iloc[0].get(name)) for name in required]
        if any(value is None or value <= 0 for value in values):
            return PartitionStatus.INVALID, ("invalid_ohlc",)
        open_,high,low,close = values
        if high < max(open_,close,low) or low > min(open_,close,high):
            return PartitionStatus.INVALID, ("invalid_ohlc",)
        return PartitionStatus.COMPLETE, ()
    return (PartitionStatus.COMPLETE, ()) if not rows.empty else (PartitionStatus.PARTIAL, ("empty_partition",))


def validate_coverage(
    dataset_type: str, rows: pd.DataFrame, expected_trade_dates: Iterable[object],
    *, suspended_trade_dates: Iterable[object] = (),
) -> tuple[PartitionStatus, tuple[str, ...]]:
    expected = tuple(sorted({_date_text(value) for value in expected_trade_dates if _date_text(value)}))
    suspended = {_date_text(value) for value in suspended_trade_dates if _date_text(value)}
    reasons: list[str] = []
    actual_dates = {_row_date(row) for row in rows.to_dict("records")}
    for day in expected:
        day_rows = rows[
            rows.apply(lambda row: _row_date(row.to_dict()) == day, axis=1)
        ] if not rows.empty else rows
        if dataset_type == "minute_5m_bar":
            if day_rows.empty and day in suspended:
                continue
            status, day_reasons = validate_partition(dataset_type,day_rows,suspended=False)
        elif dataset_type in {"daily_bar","benchmark_daily_bar"}:
            status, day_reasons = validate_partition(dataset_type,day_rows)
        else:
            status, day_reasons = ((PartitionStatus.COMPLETE,()) if not day_rows.empty else (PartitionStatus.PARTIAL,("missing_trade_date",)))
        if status != PartitionStatus.COMPLETE:
            reasons.extend(f"{day}:{reason}" for reason in day_reasons)
    if reasons:
        return (PartitionStatus.INVALID if any("invalid" in reason or "conflicting" in reason for reason in reasons) else PartitionStatus.PARTIAL, tuple(sorted(set(reasons))))
    return PartitionStatus.COMPLETE, ()


def fetch_complete_partition(
    providers: Sequence[tuple[str, str, Callable[[], pd.DataFrame]]],
    dataset_type: str,
    *, suspended: bool = False, expected_trade_dates: Iterable[object] = (),
) -> tuple[pd.DataFrame, str, str, tuple[str, ...]]:
    failures: list[str] = []
    for source, version, fetch in providers:
        try:
            frame = fetch().copy(deep=True)
            if tuple(expected_trade_dates):
                status, reasons = validate_coverage(dataset_type, frame, expected_trade_dates)
            else:
                status, reasons = validate_partition(dataset_type, frame, suspended=suspended)
        except Exception as exc:
            failures.append(f"{source}:provider_error:{type(exc).__name__}")
            continue
        if status == PartitionStatus.COMPLETE:
            return frame, source, version, tuple(failures)
        failures.append(f"{source}:{status.value}:{'|'.join(reasons)}")
    raise Phase5Error("PROVIDER_FAILED", ";".join(failures))


def _canonical_row(row: dict[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in sorted(dict(row).items())}


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _partition(row: sqlite3.Row) -> CachePartition:
    return CachePartition(
        partition_id=row["partition_id"], dataset_type=row["dataset_type"], logical_key=row["logical_key"],
        status=PartitionStatus(row["status"]), source=row["source"], source_version=row["source_version"],
        price_basis_id=row["price_basis_id"], row_count=row["row_count"], content_sha256=row["content_sha256"],
        supersedes=row["supersedes"], reasons=tuple(json.loads(row["reasons_json"])),
        normalized_symbol=row["normalized_symbol"],frequency=row["frequency"],
        coverage_start_date=row["coverage_start_date"],coverage_end_date=row["coverage_end_date"],
        covered_trade_dates=tuple(json.loads(row["covered_trade_dates_json"])),
        expected_trade_dates=tuple(json.loads(row["expected_trade_dates_json"])),
        partition_version=row["partition_version"],
    )


def _row_date(row: dict[str, Any]) -> str:
    return _date_text(row.get("trade_date",row.get("date","")))


def _date_text(value: object) -> str:
    try:
        parsed = pd.Timestamp(value)
        return "" if pd.isna(parsed) else parsed.date().isoformat()
    except (TypeError,ValueError,OverflowError):
        return ""
