"""Transactional append-only SQLite store for Phase 5 runs."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from .adaptive_v13_schema import RUN_SCHEMA, SCHEMA_VERSION
from .phase5_models import Phase5Error


def canonical_json(value: Any) -> str:
    def default(item: Any) -> Any:
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        if isinstance(item, Path):
            return str(item)
        if isinstance(item, Enum):
            return item.value
        if is_dataclass(item):
            return asdict(item)
        raise TypeError(f"unsupported_json_type:{type(item).__name__}")
    return json.dumps(value, default=default, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


class RunStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _migrate(self) -> None:
        with self._connect() as connection:
            connection.executescript(RUN_SCHEMA)
            row = connection.execute(
                "SELECT version FROM adaptive_v13_schema_version WHERE component='runs'"
            ).fetchone()
            if row is not None and int(row["version"]) > SCHEMA_VERSION:
                raise Phase5Error("SCHEMA_VERSION_MISMATCH")
            checkpoint_columns = {
                item[1] for item in connection.execute(
                    "PRAGMA table_info(adaptive_v13_run_checkpoints)"
                )
            }
            for name in ("trade_date","event_time","next_event_id"):
                if name not in checkpoint_columns:
                    connection.execute(
                        f"""ALTER TABLE adaptive_v13_run_checkpoints
                        ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"""
                    )
            connection.execute(
                """INSERT INTO adaptive_v13_schema_version(component,version) VALUES('runs',?)
                ON CONFLICT(component) DO UPDATE SET version=excluded.version""",
                (SCHEMA_VERSION,),
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_run(self, run_id: str, fingerprint: str, config: Any, data_snapshot_id: str, created_at: str) -> None:
        """Reject the pre-V1.3.14 non-atomic run creation path."""
        raise Phase5Error("INVALID_CONFIG", "snapshot_bundle_required")

    def create_run_bundle(
        self, run_id: str, fingerprint: str, config: Any, *,
        account_snapshot: Any, universe_snapshot: Any, data_snapshot: Any,
        created_at: str,
    ) -> None:
        account_json, universe_json, data_json = (
            canonical_json(account_snapshot), canonical_json(universe_snapshot), canonical_json(data_snapshot)
        )
        account_id = str(_field(account_snapshot,"account_snapshot_id"))
        universe_id = str(_field(universe_snapshot,"universe_snapshot_id"))
        data_id = str(_field(data_snapshot,"data_snapshot_id"))
        account_hash = stable_hash(json.loads(account_json))
        universe_hash = stable_hash(json.loads(universe_json))
        data_hash = stable_hash(json.loads(data_json))
        with self.transaction() as connection:
            _reuse_or_insert_snapshot(
                connection,"adaptive_v13_account_snapshots","account_snapshot_id",
                account_id,account_json,account_hash,created_at,
            )
            _reuse_or_insert_snapshot(
                connection,"adaptive_v13_universe_snapshots","universe_snapshot_id",
                universe_id,universe_json,universe_hash,created_at,
            )
            price_basis = str(_field(data_snapshot,"price_basis_id",""))
            _reuse_or_insert_snapshot(
                connection,"adaptive_v13_data_snapshots","data_snapshot_id",
                data_id,data_json,data_hash,created_at,price_basis=price_basis,
            )
            partition_hashes = _field(data_snapshot,"partition_hashes",())
            coverage = _field(data_snapshot,"required_trade_dates",())
            link_rows = [
                (data_id,partition_id,content_hash,canonical_json(coverage))
                for partition_id,content_hash in partition_hashes
            ]
            connection.executemany(
                """INSERT OR IGNORE INTO adaptive_v13_data_snapshot_partition_links
                (data_snapshot_id,partition_id,content_hash,coverage_json) VALUES(?,?,?,?)""",
                link_rows,
            )
            for expected in link_rows:
                actual = connection.execute(
                    """SELECT data_snapshot_id,partition_id,content_hash,coverage_json
                    FROM adaptive_v13_data_snapshot_partition_links
                    WHERE data_snapshot_id=? AND partition_id=?""",
                    expected[:2],
                ).fetchone()
                if actual is None or tuple(actual) != expected:
                    raise Phase5Error(
                        "RUN_FINGERPRINT_MISMATCH", "snapshot_partition_link_mismatch"
                    )
            connection.execute(
                """INSERT INTO adaptive_v13_runs
                (run_id,run_fingerprint,status,config_json,data_snapshot_id,created_at,updated_at)
                VALUES(?,?,'CREATED',?,?,?,?)""",
                (run_id,fingerprint,canonical_json(config),data_id,created_at,created_at),
            )
            account_cash = _field(account_snapshot, "cash", "0")
            account_positions = _field(account_snapshot, "positions", ())
            account_controls = _field(account_snapshot, "exit_controls", ())
            initial_state = {
                "cash": str(account_cash),
                "positions": dict(account_positions or ()),
                "pending_sells": {},
                "exit_controls": dict(account_controls or ()),
                "cooldowns": {},
                "fill_requests": (),
            }
            connection.execute(
                """INSERT INTO adaptive_v13_run_checkpoints
                (run_id,event_id,sequence_number,trade_date,event_time,next_event_id,
                 state_json,state_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (run_id,"__INITIAL__",-1,"","","",canonical_json(initial_state),stable_hash(initial_state),created_at),
            )
            self._append_audit(
                connection,run_id=run_id,event_id="__INITIAL__",event_type="CREATE_RUN",
                component="run_store",action="create_run",status="COMPLETED",
                input_value={"config":config},output_value={"fingerprint":fingerprint},
                reason_code="",message="run bundle created",source_ids=(account_id,universe_id,data_id),
            )

    def update_run_status(self, run_id: str, status: str, *, reason: str = "") -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE adaptive_v13_runs SET status=?,failure_reason=?,updated_at=? WHERE run_id=?",
                (status, reason, datetime.now().astimezone().isoformat(), run_id),
            )
            if cursor.rowcount != 1:
                raise Phase5Error("INVALID_CONFIG", "unknown_run")

    def record_run_failure(self, run_id: str, reason: str, message: str) -> None:
        with self.transaction() as connection:
            self._append_audit(
                connection,run_id=run_id,event_id="__RUN_FAILURE__",
                event_type="RUN",component="run_orchestrator",action="execute",
                status="FAILED",input_value={},output_value={},
                reason_code=reason,message=message,
            )

    def import_cache_audits(self, run_id: str, rows) -> None:
        if not rows:
            return
        with self.transaction() as connection:
            for row in rows:
                self._append_audit(
                    connection,run_id=run_id,event_id=f"__CACHE__:{row['audit_id']}",
                    event_type="CACHE_PREPARATION",component="market_cache",
                    action=row["action"],status=row["status"],
                    input_value={"input_hash":row["input_hash"]},
                    output_value={"output_hash":row["output_hash"]},
                    reason_code=row["reason_code"],message="cache preparation audit",
                    source_ids=(
                        row["preparation_id"],row["data_snapshot_id"],row["source"],
                        row["source_version"],
                    ),symbol=row["symbol"],
                )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM adaptive_v13_runs WHERE run_id=?", (run_id,)).fetchone()
        return None if row is None else dict(row)

    def list_runs(self) -> tuple[dict[str, Any], ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM adaptive_v13_runs ORDER BY created_at DESC,run_id").fetchall()
        return tuple(dict(row) for row in rows)

    def completed_event_ids(self, run_id: str) -> frozenset[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_id FROM adaptive_v13_run_events WHERE run_id=? AND status='COMPLETED'",
                (run_id,),
            ).fetchall()
        return frozenset(row[0] for row in rows)

    def process_event(self, run_id: str, event: Any, handler) -> Any:
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT status FROM adaptive_v13_run_events WHERE event_id=?", (event.event_id,)
            ).fetchone()
            if existing is not None:
                if existing["status"] == "COMPLETED":
                    return None
                raise Phase5Error("DUPLICATE_EVENT")
            now = datetime.now().astimezone().isoformat()
            connection.execute(
                """INSERT INTO adaptive_v13_run_events
                (event_id,run_id,trade_date,event_time,event_type,sequence_number,status,payload_json,created_at)
                VALUES(?,?,?,?,?,?,'PROCESSING','{}',?)""",
                (event.event_id, run_id, event.trade_date.isoformat(), event.event_time, event.event_type, event.sequence_number, now),
            )
            result, state = handler(connection, event)
            state_json = canonical_json(state)
            connection.execute(
                """INSERT INTO adaptive_v13_run_checkpoints
                (run_id,event_id,sequence_number,trade_date,event_time,next_event_id,
                 state_json,state_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (run_id,event.event_id,event.sequence_number,event.trade_date.isoformat(),
                 event.event_time,"",state_json,stable_hash(state),now),
            )
            connection.execute(
                "UPDATE adaptive_v13_run_events SET status='COMPLETED',payload_json=? WHERE event_id=?",
                (canonical_json(result), event.event_id),
            )
            return result

    def last_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM adaptive_v13_run_checkpoints WHERE run_id=?
                ORDER BY sequence_number DESC LIMIT 1""", (run_id,)
            ).fetchone()
        return None if row is None else dict(row)

    def rows(self, table: str, run_id: str) -> tuple[dict[str, Any], ...]:
        allowed = {
            "adaptive_v13_run_events", "adaptive_v13_decisions", "adaptive_v13_exit_intents",
            "adaptive_v13_fill_requests", "adaptive_v13_fills", "adaptive_v13_ledger_events",
            "adaptive_v13_position_state_versions", "adaptive_v13_exit_control_state_versions",
            "adaptive_v13_pending_sell_versions", "adaptive_v13_cooldown_records",
            "adaptive_v13_daily_account_snapshots", "adaptive_v13_audit_events",
        }
        if table not in allowed:
            raise ValueError("unsupported_table")
        with self._connect() as connection:
            result = connection.execute(f"SELECT * FROM {table} WHERE run_id=? ORDER BY rowid", (run_id,)).fetchall()
        return tuple(dict(row) for row in result)

    def load_snapshot_bundle(self, run_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            run = connection.execute("SELECT * FROM adaptive_v13_runs WHERE run_id=?",(run_id,)).fetchone()
            if run is None:
                raise Phase5Error("INVALID_CONFIG","run_not_found")
            config = json.loads(run["config_json"])
            result = {"run":dict(run),"config":config}
            for key,table,id_key in (
                ("account","adaptive_v13_account_snapshots","account_snapshot_id"),
                ("universe","adaptive_v13_universe_snapshots","universe_snapshot_id"),
                ("data","adaptive_v13_data_snapshots","data_snapshot_id"),
            ):
                identifier = config[id_key]
                row = connection.execute(f"SELECT * FROM {table} WHERE {id_key}=?",(identifier,)).fetchone()
                if row is None:
                    raise Phase5Error("DATA_NOT_READY",f"{key}_snapshot_missing")
                result[key] = dict(row)
            links = connection.execute(
                "SELECT * FROM adaptive_v13_data_snapshot_partition_links WHERE data_snapshot_id=? ORDER BY partition_id",
                (config["data_snapshot_id"],),
            ).fetchall()
            result["partition_links"] = tuple(dict(row) for row in links)
        return result

    def append_pending_sell_version(self, connection, run_id: str, state: Any) -> None:
        symbol = str(state.symbol)
        encoded = canonical_json(state)
        latest = connection.execute(
            """SELECT state_json FROM adaptive_v13_pending_sell_versions
            WHERE run_id=? AND symbol=? ORDER BY version DESC LIMIT 1""",
            (run_id, symbol),
        ).fetchone()
        if latest is not None and latest["state_json"] == encoded:
            return
        version = self._next_version(connection,"adaptive_v13_pending_sell_versions",run_id,symbol)
        attempt = "" if state.last_processed_attempt is None else stable_hash(state.last_processed_attempt)
        connection.execute(
            """INSERT INTO adaptive_v13_pending_sell_versions
            (pending_event_id,run_id,symbol,version,attempt_identity,state_json) VALUES(?,?,?,?,?,?)""",
            (stable_hash(("pending",run_id,symbol,version)),run_id,symbol,version,attempt or None,encoded),
        )

    def append_exit_control_state_version(self, connection, run_id: str, state: Any) -> None:
        symbol = str(state.symbol)
        encoded = canonical_json(state)
        latest = connection.execute(
            """SELECT state_json FROM adaptive_v13_exit_control_state_versions
            WHERE run_id=? AND symbol=? ORDER BY version DESC LIMIT 1""",
            (run_id, symbol),
        ).fetchone()
        if latest is not None and latest["state_json"] == encoded:
            return
        version = self._next_version(connection,"adaptive_v13_exit_control_state_versions",run_id,symbol)
        connection.execute(
            """INSERT INTO adaptive_v13_exit_control_state_versions
            (state_event_id,run_id,symbol,version,evaluation_date,episode_id,state_json)
            VALUES(?,?,?,?,?,?,?)""",
            (stable_hash(("control",run_id,symbol,version)),run_id,symbol,version,
             None,None,encoded),
        )

    def append_cooldown_record(self, connection, run_id: str, state: Any) -> None:
        connection.execute(
            """INSERT OR IGNORE INTO adaptive_v13_cooldown_records
            (cooldown_event_id,run_id,symbol,exit_trade_date,state_json) VALUES(?,?,?,?,?)""",
            (stable_hash(("cooldown",run_id,state.symbol,state.exit_trade_date)),run_id,state.symbol,
             str(state.exit_trade_date),canonical_json(state)),
        )

    def latest_state_rows(self, table: str, run_id: str) -> tuple[dict[str,Any],...]:
        if table not in {"adaptive_v13_pending_sell_versions","adaptive_v13_exit_control_state_versions"}:
            raise ValueError("unsupported_state_table")
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT t.* FROM {table} t JOIN
                (SELECT symbol,MAX(version) version FROM {table} WHERE run_id=? GROUP BY symbol) latest
                ON t.symbol=latest.symbol AND t.version=latest.version WHERE t.run_id=? ORDER BY t.symbol""",
                (run_id,run_id),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def latest_position_rows(self, run_id: str) -> tuple[dict[str, Any], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT p.* FROM adaptive_v13_position_state_versions p JOIN
                (SELECT symbol,MAX(version) version
                 FROM adaptive_v13_position_state_versions WHERE run_id=? GROUP BY symbol) latest
                ON p.symbol=latest.symbol AND p.version=latest.version
                WHERE p.run_id=? ORDER BY p.symbol""",
                (run_id, run_id),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def unfinished_fill_request_rows(self, run_id: str) -> tuple[dict[str, Any], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT r.* FROM adaptive_v13_fill_requests r
                LEFT JOIN adaptive_v13_fills f ON f.fill_request_id=r.fill_request_id
                WHERE r.run_id=? AND f.fill_id IS NULL
                ORDER BY r.fill_request_id""",
                (run_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def latest_cash(self, run_id: str, fallback: Any) -> Decimal:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT cash_after FROM adaptive_v13_ledger_events
                WHERE run_id=? ORDER BY rowid DESC LIMIT 1""", (run_id,)
            ).fetchone()
        return Decimal(str(fallback if row is None else row["cash_after"]))

    def load_active_cooldowns(self, run_id: str, as_of_date: str) -> tuple[dict[str,Any],...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM adaptive_v13_cooldown_records WHERE run_id=? ORDER BY symbol,exit_trade_date",
                (run_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows if json.loads(row["state_json"]).get("reentry_allowed_date","") > as_of_date)

    def append_audit_event(self, connection, **kwargs) -> None:
        self._append_audit(connection,**kwargs)

    def _append_audit(
        self, connection, *, run_id: str, event_id: str, event_type: str,
        component: str, action: str, status: str, input_value: Any, output_value: Any,
        reason_code: str, message: str, source_ids=(), symbol: str = "",
    ) -> None:
        now = datetime.now().astimezone().isoformat()
        audit_id = stable_hash(("audit",run_id,event_id,component,action,status,symbol,stable_hash(output_value)))
        connection.execute(
            """INSERT OR IGNORE INTO adaptive_v13_audit_events
            (audit_id,run_id,event_id,event_at,event_type,symbol,component,action,status,
             reason_code,message,input_hash,output_hash,source_ids_json,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (audit_id,run_id,event_id,now,event_type,symbol,component,action,status,reason_code,
             message,stable_hash(input_value),stable_hash(output_value),canonical_json(tuple(source_ids)),now),
        )

    @staticmethod
    def _next_version(connection, table: str, run_id: str, symbol: str) -> int:
        return int(connection.execute(
            f"SELECT COALESCE(MAX(version),0)+1 FROM {table} WHERE run_id=? AND symbol=?",
            (run_id,symbol),
        ).fetchone()[0])


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value,dict):
        return value.get(name,default)
    return getattr(value,name,default)


def _reuse_or_insert_snapshot(
    connection: sqlite3.Connection, table: str, id_column: str,
    snapshot_id: str, content_json: str, content_hash: str, created_at: str,
    *, price_basis: str | None = None,
) -> None:
    allowed = {
        "adaptive_v13_account_snapshots": "account_snapshot_id",
        "adaptive_v13_universe_snapshots": "universe_snapshot_id",
        "adaptive_v13_data_snapshots": "data_snapshot_id",
    }
    if allowed.get(table) != id_column:
        raise Phase5Error("INVALID_CONFIG", "unsupported_snapshot_table")
    if price_basis is None:
        connection.execute(
            f"INSERT OR IGNORE INTO {table} VALUES(?,?,?,?)",
            (snapshot_id,content_json,content_hash,created_at),
        )
        row = connection.execute(
            f"SELECT content_json,content_hash FROM {table} WHERE {id_column}=?",
            (snapshot_id,),
        ).fetchone()
    else:
        connection.execute(
            f"INSERT OR IGNORE INTO {table} VALUES(?,?,?,?,?)",
            (snapshot_id,content_json,content_hash,price_basis,created_at),
        )
        row = connection.execute(
            f"""SELECT content_json,content_hash,price_basis_id
            FROM {table} WHERE {id_column}=?""",
            (snapshot_id,),
        ).fetchone()
    if row is None:
        raise Phase5Error("RUN_FINGERPRINT_MISMATCH", "snapshot_identity_conflict")
    existing_identity = _snapshot_identity_hash(row["content_json"], row["content_hash"])
    supplied_identity = _snapshot_identity_hash(content_json, content_hash)
    if existing_identity != supplied_identity:
        raise Phase5Error("RUN_FINGERPRINT_MISMATCH", "snapshot_content_mismatch")
    if price_basis is not None and row["price_basis_id"] != price_basis:
        raise Phase5Error("PRICE_BASIS_MISMATCH", "snapshot_price_basis_mismatch")


def _snapshot_identity_hash(content_json: str, fallback: str) -> str:
    try:
        parsed = json.loads(content_json)
    except (TypeError, ValueError):
        return fallback
    return str(parsed.get("snapshot_hash") or fallback)
