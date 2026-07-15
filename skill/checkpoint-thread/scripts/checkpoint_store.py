#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import re
import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 2
DATABASE_NAME = "checkpoint-thread.sqlite3"


class ControlPlaneError(Exception):
    def __init__(self, code: str, **details: Any) -> None:
        super().__init__(code)
        self.code = code
        self.details = details


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def safe_component(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._/-]+", "-", value).strip("./-")
    sanitized = sanitized.replace("..", "-").replace("@{", "-")
    if not sanitized:
        raise ControlPlaneError("invalid_ledger_id", value=value)
    return sanitized


def projection_path(root: Path, ledger_id: str) -> Path:
    return root / safe_component(ledger_id) / "ledger.json"


def database_path(root: Path) -> Path:
    return root / DATABASE_NAME


def migrate_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    version = payload.get("version", 1)
    if version not in {1, SCHEMA_VERSION}:
        raise ControlPlaneError(
            "unsupported_ledger_version",
            version=version,
            supported=[1, SCHEMA_VERSION],
        )
    migrated = version != SCHEMA_VERSION
    payload["version"] = SCHEMA_VERSION
    payload.setdefault("state", "active")
    payload.setdefault("repos", {})
    return payload, migrated


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

            CREATE TABLE IF NOT EXISTS branch_claims (
                repo_id TEXT NOT NULL,
                branch TEXT NOT NULL,
                ledger_id TEXT NOT NULL,
                repo_root TEXT NOT NULL,
                claimed_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (repo_id, branch)
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

    def load(
        self, ledger_id: str, *, required: bool
    ) -> tuple[dict[str, Any] | None, bool]:
        with self.connect(create=False) as connection:
            if connection is not None:
                row = connection.execute(
                    "SELECT state_json FROM ledgers WHERE ledger_id = ?", (ledger_id,)
                ).fetchone()
                if row is not None:
                    payload = decode_state(ledger_id, row["state_json"])
                    migrated_payload, migrated = migrate_payload(payload)
                    if migrated:
                        self.save(
                            ledger_id,
                            migrated_payload,
                            event={"operation": "migrate", "status": "completed"},
                        )
                    return migrated_payload, migrated

        path = projection_path(self.root, ledger_id)
        if not path.exists():
            if required:
                raise ControlPlaneError("ledger_not_found", ledger_id=ledger_id)
            return None, False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ControlPlaneError(
                "ledger_unreadable", path=str(path), detail=str(error)
            ) from error
        if not isinstance(payload, dict):
            raise ControlPlaneError(
                "ledger_unreadable",
                path=str(path),
                detail="ledger projection must be a JSON object",
            )
        migrated_payload, _ = migrate_payload(payload)
        self.save(
            ledger_id,
            migrated_payload,
            event={
                "operation": "migrate_v1_projection",
                "status": "completed",
                "detail": {"source": str(path)},
            },
        )
        return migrated_payload, True

    def save(
        self,
        ledger_id: str,
        ledger: dict[str, Any],
        *,
        event: dict[str, Any] | None = None,
    ) -> str | None:
        ledger, _ = migrate_payload(ledger)
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
        self.write_projection(ledger_id, ledger)
        return event_id

    def write_projection(self, ledger_id: str, ledger: dict[str, Any]) -> None:
        path = projection_path(self.root, ledger_id)
        temporary: str | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(
                prefix="ledger-", suffix=".tmp", dir=path.parent
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    ledger,
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as error:
            raise ControlPlaneError(
                "projection_write_failed",
                path=str(path),
                canonical_state_saved=self.path.exists(),
                detail=str(error),
                solution="run_doctor_repair_projections",
            ) from error
        finally:
            if temporary and os.path.exists(temporary):
                with contextlib.suppress(OSError):
                    os.unlink(temporary)

    def acquire_claim(
        self,
        *,
        ledger_id: str,
        repo_id: str,
        branch: str,
        repo_root: str,
    ) -> dict[str, Any]:
        timestamp = now_iso()
        with self.connect(create=True) as connection:
            assert connection is not None
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT ledger_id, repo_root, claimed_at, updated_at
                FROM branch_claims WHERE repo_id = ? AND branch = ?
                """,
                (repo_id, branch),
            ).fetchone()
            if row is not None and row["ledger_id"] != ledger_id:
                connection.rollback()
                raise ControlPlaneError(
                    "branch_claimed",
                    repo_id=repo_id,
                    branch=branch,
                    owner_ledger_id=row["ledger_id"],
                    owner_repo_root=row["repo_root"],
                    claimed_at=row["claimed_at"],
                    solution="park_or_ship_owner_then_retry",
                )
            action = "retained" if row is not None else "acquired"
            connection.execute(
                """
                INSERT INTO branch_claims(
                    repo_id, branch, ledger_id, repo_root, claimed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_id, branch) DO UPDATE SET
                    repo_root = excluded.repo_root,
                    updated_at = excluded.updated_at
                """,
                (repo_id, branch, ledger_id, repo_root, timestamp, timestamp),
            )
            connection.commit()
        return {
            "action": action,
            "repo_id": repo_id,
            "branch": branch,
            "ledger_id": ledger_id,
            "claimed_at": row["claimed_at"] if row is not None else timestamp,
        }

    def release_claim(self, *, ledger_id: str, repo_id: str, branch: str) -> bool:
        with self.connect(create=False) as connection:
            if connection is None:
                return False
            cursor = connection.execute(
                """
                DELETE FROM branch_claims
                WHERE repo_id = ? AND branch = ? AND ledger_id = ?
                """,
                (repo_id, branch, ledger_id),
            )
            connection.commit()
            return cursor.rowcount > 0

    def claim(self, *, repo_id: str, branch: str) -> dict[str, Any] | None:
        with self.connect(create=False) as connection:
            if connection is None:
                return None
            row = connection.execute(
                "SELECT * FROM branch_claims WHERE repo_id = ? AND branch = ?",
                (repo_id, branch),
            ).fetchone()
            return dict(row) if row is not None else None

    def event_count(self, ledger_id: str) -> int:
        with self.connect(create=False) as connection:
            if connection is None:
                return 0
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM events WHERE ledger_id = ?", (ledger_id,)
                ).fetchone()[0]
            )

    def events(self, ledger_id: str) -> list[dict[str, Any]]:
        with self.connect(create=False) as connection:
            if connection is None:
                return []
            rows = connection.execute(
                "SELECT * FROM events WHERE ledger_id = ? ORDER BY sequence",
                (ledger_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def projection_matches(self, ledger_id: str, ledger: dict[str, Any]) -> bool:
        path = projection_path(self.root, ledger_id)
        if not path.exists():
            return False
        try:
            projection = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return projection == ledger

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
                return {"ledgers": {}, "claims": [], "incomplete_operations": []}
            ledger_rows = connection.execute(
                "SELECT ledger_id, state_json FROM ledgers ORDER BY ledger_id"
            ).fetchall()
            claims = connection.execute(
                "SELECT * FROM branch_claims ORDER BY repo_id, branch"
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
                    row["ledger_id"]: decode_state(row["ledger_id"], row["state_json"])
                    for row in ledger_rows
                },
                "claims": [dict(row) for row in claims],
                "incomplete_operations": [dict(row) for row in incomplete],
            }
