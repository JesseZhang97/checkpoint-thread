#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import datetime as dt
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 2
DATABASE_NAME = "checkpoint-thread.sqlite3"
HOOK_SPAN_TTL_HOURS = 24


class ControlPlaneError(Exception):
    def __init__(self, code: str, **details: Any) -> None:
        super().__init__(code)
        self.code = code
        self.details = details


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def database_path(root: Path) -> Path:
    return root / DATABASE_NAME


def validate_payload(ledger_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    version = payload.get("version")
    if version != SCHEMA_VERSION:
        raise ControlPlaneError(
            "unsupported_ledger_version",
            version=version,
            supported=SCHEMA_VERSION,
        )
    if not isinstance(payload.get("repos"), dict):
        raise ControlPlaneError(
            "control_plane_state_invalid",
            detail="ledger repos must be an object",
            solution="restore_the_control_plane_from_a_known_good_backup",
        )
    if payload.get("ledger_id") != ledger_id:
        raise ControlPlaneError(
            "control_plane_state_invalid",
            ledger_id=ledger_id,
            detail="ledger id does not match its control-plane key",
            solution="restore_the_control_plane_from_a_known_good_backup",
        )
    return payload


def decode_state(ledger_id: str, serialized: str) -> dict[str, Any]:
    try:
        payload = json.loads(serialized)
    except (TypeError, json.JSONDecodeError) as error:
        raise ControlPlaneError(
            "control_plane_state_invalid",
            ledger_id=ledger_id,
            detail=str(error),
            solution="restore_the_control_plane_from_a_known_good_backup",
        ) from error
    if not isinstance(payload, dict):
        raise ControlPlaneError(
            "control_plane_state_invalid",
            ledger_id=ledger_id,
            detail="ledger state must be a JSON object",
            solution="restore_the_control_plane_from_a_known_good_backup",
        )
    return payload


class ControlPlane:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.path = database_path(self.root)

    @contextlib.contextmanager
    def connect(self, *, create: bool) -> Iterator[sqlite3.Connection | None]:
        if not create and not self.path.exists():
            yield None
            return
        connection: sqlite3.Connection | None = None
        try:
            if create:
                self.root.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA foreign_keys = ON")
            if create:
                self._initialize(connection)
            else:
                self._validate_schema(connection)
            yield connection
        except sqlite3.Error as error:
            raise ControlPlaneError(
                "control_plane_unreadable",
                path=str(self.path),
                detail=str(error),
                solution="restore_or_replace_the_control_plane_after_inspection",
            ) from error
        except OSError as error:
            raise ControlPlaneError(
                "control_plane_path_unavailable",
                path=str(self.path),
                detail=str(error),
            ) from error
        finally:
            if connection is not None:
                connection.close()

    def _validate_schema(self, connection: sqlite3.Connection) -> None:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            raise ControlPlaneError(
                "unsupported_control_plane_version",
                path=str(self.path),
                version=version,
                supported=SCHEMA_VERSION,
            )

    def _initialize(self, connection: sqlite3.Connection) -> None:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in {0, SCHEMA_VERSION}:
            raise ControlPlaneError(
                "unsupported_control_plane_version",
                path=str(self.path),
                version=version,
                supported=SCHEMA_VERSION,
            )
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS ledgers (
                ledger_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                ledger_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                repo_id TEXT,
                branch TEXT,
                head TEXT,
                state_oid TEXT,
                status TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                FOREIGN KEY (ledger_id) REFERENCES ledgers(ledger_id)
            );

            CREATE INDEX IF NOT EXISTS events_ledger_sequence
                ON events(ledger_id, sequence);

            CREATE TABLE IF NOT EXISTS hook_spans (
                ledger_id TEXT NOT NULL,
                span_id TEXT NOT NULL,
                repo_id TEXT NOT NULL,
                branch TEXT NOT NULL,
                before_state_oid TEXT NOT NULL,
                started_at TEXT NOT NULL,
                PRIMARY KEY (ledger_id, span_id)
            );

            CREATE TABLE IF NOT EXISTS operations (
                ledger_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                command TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                receipt_json TEXT,
                PRIMARY KEY (ledger_id, operation_id)
            );
            """
        )
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()

    def load(self, ledger_id: str, *, required: bool) -> dict[str, Any] | None:
        with self.connect(create=False) as connection:
            if connection is not None:
                row = connection.execute(
                    "SELECT state_json FROM ledgers WHERE ledger_id = ?", (ledger_id,)
                ).fetchone()
                if row is not None:
                    return validate_payload(
                        ledger_id, decode_state(ledger_id, row["state_json"])
                    )

        if required:
            raise ControlPlaneError("ledger_not_found", ledger_id=ledger_id)
        return None

    def save(
        self,
        ledger_id: str,
        ledger: dict[str, Any],
        *,
        event: dict[str, Any] | None = None,
    ) -> str | None:
        ledger = validate_payload(ledger_id, ledger)
        timestamp = now_iso()
        ledger["updated_at"] = timestamp
        created_at = ledger.get("created_at") or timestamp
        serialized = json.dumps(
            ledger, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        event_id: str | None = None
        with self.connect(create=True) as connection:
            assert connection is not None
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO ledgers(ledger_id, state_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(ledger_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (ledger_id, serialized, created_at, timestamp),
            )
            if event is not None:
                event_id = event.get("event_id") or str(uuid.uuid4())
                detail = event.get("detail") or {}
                connection.execute(
                    """
                    INSERT INTO events(
                        event_id, ledger_id, operation, occurred_at, repo_id,
                        branch, head, state_oid, status, detail_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        ledger_id,
                        event.get("operation", "state_saved"),
                        event.get("occurred_at", timestamp),
                        event.get("repo_id"),
                        event.get("branch"),
                        event.get("head"),
                        event.get("state_oid"),
                        event.get("status", "completed"),
                        json.dumps(detail, ensure_ascii=False, sort_keys=True),
                    ),
                )
            connection.commit()
        return event_id

    def start_span(
        self,
        *,
        ledger_id: str,
        span_id: str,
        repo_id: str,
        branch: str,
        before_state_oid: str,
    ) -> None:
        timestamp = now_iso()
        cutoff = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=HOOK_SPAN_TTL_HOURS)
        ).isoformat()
        with self.connect(create=True) as connection:
            assert connection is not None
            connection.execute("DELETE FROM hook_spans WHERE started_at < ?", (cutoff,))
            connection.execute(
                """
                INSERT INTO hook_spans(
                    ledger_id, span_id, repo_id, branch,
                    before_state_oid, started_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(ledger_id, span_id) DO UPDATE SET
                    repo_id = excluded.repo_id,
                    branch = excluded.branch,
                    before_state_oid = excluded.before_state_oid,
                    started_at = excluded.started_at
                """,
                (
                    ledger_id,
                    span_id,
                    repo_id,
                    branch,
                    before_state_oid,
                    timestamp,
                ),
            )
            connection.commit()

    def get_span(self, *, ledger_id: str, span_id: str) -> dict[str, Any] | None:
        with self.connect(create=False) as connection:
            if connection is None:
                return None
            row = connection.execute(
                """
                SELECT ledger_id, span_id, repo_id, branch,
                       before_state_oid, started_at
                FROM hook_spans
                WHERE ledger_id = ? AND span_id = ?
                """,
                (ledger_id, span_id),
            ).fetchone()
            return dict(row) if row is not None else None

    def delete_span(self, *, ledger_id: str, span_id: str) -> None:
        with self.connect(create=False) as connection:
            if connection is None:
                return
            connection.execute(
                "DELETE FROM hook_spans WHERE ledger_id = ? AND span_id = ?",
                (ledger_id, span_id),
            )
            connection.commit()

    def event_count(self, ledger_id: str) -> int:
        with self.connect(create=False) as connection:
            if connection is None:
                return 0
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM events WHERE ledger_id = ?", (ledger_id,)
                ).fetchone()[0]
            )

    def integrity_check(self) -> str:
        with self.connect(create=False) as connection:
            if connection is None:
                return "missing"
            row = connection.execute("PRAGMA integrity_check").fetchone()
            return str(row[0])

    def events(self, ledger_id: str) -> list[dict[str, Any]]:
        with self.connect(create=False) as connection:
            if connection is None:
                return []
            rows = connection.execute(
                "SELECT * FROM events WHERE ledger_id = ? ORDER BY sequence",
                (ledger_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def start_operation(
        self, *, ledger_id: str, operation_id: str, command: str
    ) -> dict[str, Any] | None:
        with self.connect(create=True) as connection:
            assert connection is not None
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, receipt_json FROM operations
                WHERE ledger_id = ? AND operation_id = ?
                """,
                (ledger_id, operation_id),
            ).fetchone()
            if row is not None:
                connection.rollback()
                if row["status"] == "completed" and row["receipt_json"]:
                    return json.loads(row["receipt_json"])
                raise ControlPlaneError(
                    "operation_incomplete",
                    ledger_id=ledger_id,
                    operation_id=operation_id,
                    status=row["status"],
                    solution="run_inspect_check_before_retry",
                )
            connection.execute(
                """
                INSERT INTO operations(
                    ledger_id, operation_id, command, status, started_at
                ) VALUES (?, ?, ?, 'started', ?)
                """,
                (ledger_id, operation_id, command, now_iso()),
            )
            connection.commit()
        return None

    def complete_operation(
        self,
        *,
        ledger_id: str,
        operation_id: str,
        receipt: dict[str, Any],
    ) -> None:
        with self.connect(create=True) as connection:
            assert connection is not None
            connection.execute(
                """
                UPDATE operations SET status = 'completed', completed_at = ?,
                    receipt_json = ?
                WHERE ledger_id = ? AND operation_id = ?
                """,
                (
                    now_iso(),
                    json.dumps(receipt, ensure_ascii=False, sort_keys=True),
                    ledger_id,
                    operation_id,
                ),
            )
            connection.commit()

    def incomplete_operations(self, ledger_id: str) -> list[dict[str, Any]]:
        with self.connect(create=False) as connection:
            if connection is None:
                return []
            rows = connection.execute(
                """
                SELECT operation_id, command, status, started_at
                FROM operations
                WHERE ledger_id = ? AND status != 'completed'
                ORDER BY started_at
                """,
                (ledger_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def inventory(self) -> dict[str, Any]:
        with self.connect(create=False) as connection:
            if connection is None:
                return {
                    "ledgers": {},
                    "hook_spans": [],
                    "incomplete_operations": [],
                }
            ledger_rows = connection.execute(
                "SELECT ledger_id, state_json FROM ledgers ORDER BY ledger_id"
            ).fetchall()
            spans = connection.execute(
                "SELECT * FROM hook_spans ORDER BY started_at"
            ).fetchall()
            incomplete = connection.execute(
                """
                SELECT ledger_id, operation_id, command, status, started_at
                FROM operations WHERE status != 'completed'
                ORDER BY started_at
                """
            ).fetchall()
            return {
                "ledgers": {
                    row["ledger_id"]: validate_payload(
                        row["ledger_id"],
                        decode_state(row["ledger_id"], row["state_json"]),
                    )
                    for row in ledger_rows
                },
                "hook_spans": [dict(row) for row in spans],
                "incomplete_operations": [dict(row) for row in incomplete],
            }
