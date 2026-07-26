"""Persistent idempotency records for Phase 6 run and resume submissions."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Iterator

from .phase5_models import Phase5Error


@dataclass(frozen=True)
class SubmissionClaim:
    operation_token: str
    submission_fingerprint: str
    run_id: str
    state: str
    owned: bool


class Phase6IdempotencyStore:
    """Small append-preserving store colocated with, but separate from, RunStore."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS adaptive_v13_phase6_submissions (
                    operation_token TEXT PRIMARY KEY,
                    submission_fingerprint TEXT NOT NULL,
                    run_id TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_adaptive_v13_phase6_submission_fingerprint
                ON adaptive_v13_phase6_submissions(submission_fingerprint, state);
                CREATE UNIQUE INDEX IF NOT EXISTS uq_adaptive_v13_phase6_active_fingerprint
                ON adaptive_v13_phase6_submissions(submission_fingerprint)
                WHERE state='ACTIVE';
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
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

    def claim(self, operation_token: str, submission_fingerprint: str) -> SubmissionClaim:
        token = _token(operation_token)
        fingerprint = _fingerprint(submission_fingerprint)
        now = datetime.now().astimezone().isoformat()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM adaptive_v13_phase6_submissions WHERE operation_token=?",
                (token,),
            ).fetchone()
            if existing is not None:
                if existing["submission_fingerprint"] != fingerprint:
                    raise Phase5Error("RUN_FINGERPRINT_MISMATCH", "operation_token_reused")
                if existing["state"] == "RELEASED" and not existing["run_id"]:
                    connection.execute(
                        """UPDATE adaptive_v13_phase6_submissions
                        SET state='ACTIVE',updated_at=? WHERE operation_token=?""",
                        (now, token),
                    )
                    return SubmissionClaim(token, fingerprint, "", "ACTIVE", True)
                return _claim(existing, False)
            active = connection.execute(
                """SELECT * FROM adaptive_v13_phase6_submissions
                WHERE submission_fingerprint=? AND state='ACTIVE'
                ORDER BY created_at, operation_token LIMIT 1""",
                (fingerprint,),
            ).fetchone()
            if active is not None:
                connection.execute(
                    """INSERT INTO adaptive_v13_phase6_submissions
                    (operation_token,submission_fingerprint,run_id,state,created_at,updated_at)
                    VALUES(?,?,?,'ALIAS',?,?)""",
                    (token,fingerprint,str(active["run_id"]),now,now),
                )
                alias = connection.execute(
                    "SELECT * FROM adaptive_v13_phase6_submissions WHERE operation_token=?",
                    (token,),
                ).fetchone()
                return _claim(alias, False)
            connection.execute(
                """INSERT INTO adaptive_v13_phase6_submissions
                (operation_token,submission_fingerprint,run_id,state,created_at,updated_at)
                VALUES(?,?,?,'ACTIVE',?,?)""",
                (token, fingerprint, "", now, now),
            )
        return SubmissionClaim(token, fingerprint, "", "ACTIVE", True)

    def bind_run(self, operation_token: str, run_id: str) -> None:
        now = datetime.now().astimezone().isoformat()
        with self._transaction() as connection:
            owner = connection.execute(
                """SELECT submission_fingerprint FROM adaptive_v13_phase6_submissions
                WHERE operation_token=? AND state='ACTIVE'""",
                (_token(operation_token),),
            ).fetchone()
            if owner is None:
                raise Phase5Error("DUPLICATE_EVENT", "submission_claim_missing")
            connection.execute(
                """UPDATE adaptive_v13_phase6_submissions SET run_id=?,updated_at=?
                WHERE submission_fingerprint=? AND state IN ('ACTIVE','ALIAS')""",
                (str(run_id),now,str(owner["submission_fingerprint"])),
            )

    def finish(self, operation_token: str, state: str) -> None:
        final = str(state).upper()
        if final not in {"COMPLETED", "RECOVERABLE_FAILED", "RELEASED"}:
            raise ValueError("invalid_submission_state")
        now = datetime.now().astimezone().isoformat()
        with self._transaction() as connection:
            owner = connection.execute(
                """SELECT submission_fingerprint FROM adaptive_v13_phase6_submissions
                WHERE operation_token=?""",
                (_token(operation_token),),
            ).fetchone()
            if owner is None:
                return
            connection.execute(
                """UPDATE adaptive_v13_phase6_submissions SET state=?,updated_at=?
                WHERE submission_fingerprint=? AND state IN ('ACTIVE','ALIAS')""",
                (final,now,str(owner["submission_fingerprint"])),
            )

    def get(self, operation_token: str) -> SubmissionClaim | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM adaptive_v13_phase6_submissions WHERE operation_token=?",
                (_token(operation_token),),
            ).fetchone()
        return None if row is None else _claim(row, False)


def _token(value: str) -> str:
    token = str(value).strip()
    if not token or len(token) > 200:
        raise Phase5Error("INVALID_CONFIG", "invalid_operation_token")
    return token


def _fingerprint(value: str) -> str:
    fingerprint = str(value).strip().lower()
    if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
        raise Phase5Error("INVALID_CONFIG", "invalid_submission_fingerprint")
    return fingerprint


def _claim(row: sqlite3.Row, owned: bool) -> SubmissionClaim:
    return SubmissionClaim(
        str(row["operation_token"]),
        str(row["submission_fingerprint"]),
        str(row["run_id"]),
        str(row["state"]),
        owned,
    )
