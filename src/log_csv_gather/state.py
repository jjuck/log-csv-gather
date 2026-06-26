from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class UploadRecord:
    source_path: str
    drive_file_id: str | None
    drive_path: str
    group_name: str
    log_type: str
    machine_id: str
    source_date_yyyymmdd: str
    target_date_yymmdd: str
    source_size: int
    source_mtime: float
    content_hash: str
    status: str
    attempt_count: int = 1
    uploaded_at: str | None = None
    last_attempt_at: str | None = None
    last_error: str | None = None

    def with_status(self, status: str, **changes: object) -> "UploadRecord":
        return replace(self, status=status, **changes)


@dataclass(frozen=True)
class DownloadRecord:
    drive_file_id: str
    drive_path: str
    local_path: str
    group_name: str
    log_type: str
    machine_id: str
    target_date_yymmdd: str
    drive_size: int
    drive_mtime: str
    content_hash: str
    status: str
    attempt_count: int = 1
    downloaded_at: str | None = None
    last_attempt_at: str | None = None
    last_error: str | None = None

    def with_status(self, status: str, **changes: object) -> "DownloadRecord":
        return replace(self, status=status, **changes)


@dataclass(frozen=True)
class ActionResult:
    action: str
    status: str
    tone: str
    message: str
    payload: dict[str, Any]
    started_at: str | None = None
    ended_at: str | None = None
    error: str | None = None


class StateRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def upsert_upload(self, record: UploadRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO uploads (
                  source_path, drive_file_id, drive_path, group_name, log_type, machine_id,
                  source_date_yyyymmdd, target_date_yymmdd, source_size, source_mtime,
                  content_hash, status, attempt_count, uploaded_at, last_attempt_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(drive_path) DO UPDATE SET
                  source_path=excluded.source_path,
                  drive_file_id=excluded.drive_file_id,
                  group_name=excluded.group_name,
                  log_type=excluded.log_type,
                  machine_id=excluded.machine_id,
                  source_date_yyyymmdd=excluded.source_date_yyyymmdd,
                  target_date_yymmdd=excluded.target_date_yymmdd,
                  source_size=excluded.source_size,
                  source_mtime=excluded.source_mtime,
                  content_hash=excluded.content_hash,
                  status=excluded.status,
                  attempt_count=uploads.attempt_count + 1,
                  uploaded_at=excluded.uploaded_at,
                  last_attempt_at=excluded.last_attempt_at,
                  last_error=excluded.last_error
                """,
                (
                    record.source_path,
                    record.drive_file_id,
                    record.drive_path,
                    record.group_name,
                    record.log_type,
                    record.machine_id,
                    record.source_date_yyyymmdd,
                    record.target_date_yymmdd,
                    record.source_size,
                    record.source_mtime,
                    record.content_hash,
                    record.status,
                    record.attempt_count,
                    record.uploaded_at,
                    record.last_attempt_at,
                    record.last_error,
                ),
            )

    def upsert_download(self, record: DownloadRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO downloads (
                  drive_file_id, drive_path, local_path, group_name, log_type, machine_id,
                  target_date_yymmdd, drive_size, drive_mtime, content_hash, status,
                  attempt_count, downloaded_at, last_attempt_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(drive_file_id) DO UPDATE SET
                  drive_path=excluded.drive_path,
                  local_path=excluded.local_path,
                  group_name=excluded.group_name,
                  log_type=excluded.log_type,
                  machine_id=excluded.machine_id,
                  target_date_yymmdd=excluded.target_date_yymmdd,
                  drive_size=excluded.drive_size,
                  drive_mtime=excluded.drive_mtime,
                  content_hash=excluded.content_hash,
                  status=excluded.status,
                  attempt_count=downloads.attempt_count + 1,
                  downloaded_at=excluded.downloaded_at,
                  last_attempt_at=excluded.last_attempt_at,
                  last_error=excluded.last_error
                """,
                (
                    record.drive_file_id,
                    record.drive_path,
                    record.local_path,
                    record.group_name,
                    record.log_type,
                    record.machine_id,
                    record.target_date_yymmdd,
                    record.drive_size,
                    record.drive_mtime,
                    record.content_hash,
                    record.status,
                    record.attempt_count,
                    record.downloaded_at,
                    record.last_attempt_at,
                    record.last_error,
                ),
            )

    def get_upload_by_drive_path(self, drive_path: str) -> UploadRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM uploads WHERE drive_path = ?", (drive_path,)).fetchone()
        return self._upload_from_row(row) if row else None

    def get_download_by_drive_file_id(self, drive_file_id: str) -> DownloadRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM downloads WHERE drive_file_id = ?", (drive_file_id,)).fetchone()
        return self._download_from_row(row) if row else None

    def should_process_upload(self, drive_path: str, source_size: int, content_hash: str) -> bool:
        record = self.get_upload_by_drive_path(drive_path)
        if record is None:
            return True
        if record.status in {"failed", "pending"}:
            return True
        if record.status == "uploaded":
            return not (record.source_size == source_size and record.content_hash == content_hash)
        return False

    def should_process_download(self, drive_file_id: str, drive_size: int, content_hash: str) -> bool:
        record = self.get_download_by_drive_file_id(drive_file_id)
        if record is None:
            return True
        if record.status in {"failed", "pending"}:
            return True
        if record.status == "downloaded":
            return not (record.drive_size == drive_size and record.content_hash == content_hash)
        return False

    def count_by_status(self, table: str) -> dict[str, int]:
        if table not in {"uploads", "downloads"}:
            raise ValueError("table must be uploads or downloads")
        with self._connect() as conn:
            rows = conn.execute(f"SELECT status, COUNT(*) AS count FROM {table} GROUP BY status").fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def list_by_status(self, table: str, status: str, limit: int = 20) -> list[UploadRecord] | list[DownloadRecord]:
        if table not in {"uploads", "downloads"}:
            raise ValueError("table must be uploads or downloads")
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM {table}
                WHERE status = ?
                ORDER BY last_attempt_at DESC, id DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        if table == "uploads":
            return [self._upload_from_row(row) for row in rows]
        return [self._download_from_row(row) for row in rows]

    def upsert_action_result(self, record: ActionResult) -> None:
        payload_json = json.dumps(record.payload, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO action_results (
                  action, status, tone, message, payload_json, started_at, ended_at, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(action) DO UPDATE SET
                  status=excluded.status,
                  tone=excluded.tone,
                  message=excluded.message,
                  payload_json=excluded.payload_json,
                  started_at=excluded.started_at,
                  ended_at=excluded.ended_at,
                  error=excluded.error
                """,
                (
                    record.action,
                    record.status,
                    record.tone,
                    record.message,
                    payload_json,
                    record.started_at,
                    record.ended_at,
                    record.error,
                ),
            )

    def get_action_result(self, action: str) -> ActionResult | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM action_results WHERE action = ?", (action,)).fetchone()
        return self._action_result_from_row(row) if row else None

    def list_action_results(self) -> list[ActionResult]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM action_results ORDER BY ended_at DESC, action ASC").fetchall()
        return [self._action_result_from_row(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS uploads (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  source_path TEXT NOT NULL,
                  drive_file_id TEXT,
                  drive_path TEXT NOT NULL UNIQUE,
                  group_name TEXT NOT NULL,
                  log_type TEXT NOT NULL,
                  machine_id TEXT NOT NULL,
                  source_date_yyyymmdd TEXT NOT NULL,
                  target_date_yymmdd TEXT NOT NULL,
                  source_size INTEGER NOT NULL,
                  source_mtime REAL NOT NULL,
                  content_hash TEXT NOT NULL,
                  status TEXT NOT NULL,
                  attempt_count INTEGER NOT NULL DEFAULT 1,
                  uploaded_at TEXT,
                  last_attempt_at TEXT,
                  last_error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS downloads (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  drive_file_id TEXT NOT NULL UNIQUE,
                  drive_path TEXT NOT NULL,
                  local_path TEXT NOT NULL,
                  group_name TEXT NOT NULL,
                  log_type TEXT NOT NULL,
                  machine_id TEXT NOT NULL,
                  target_date_yymmdd TEXT NOT NULL,
                  drive_size INTEGER NOT NULL,
                  drive_mtime TEXT NOT NULL,
                  content_hash TEXT NOT NULL,
                  status TEXT NOT NULL,
                  attempt_count INTEGER NOT NULL DEFAULT 1,
                  downloaded_at TEXT,
                  last_attempt_at TEXT,
                  last_error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS action_results (
                  action TEXT PRIMARY KEY,
                  status TEXT NOT NULL,
                  tone TEXT NOT NULL,
                  message TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  started_at TEXT,
                  ended_at TEXT,
                  error TEXT
                )
                """
            )

    @staticmethod
    def _upload_from_row(row: sqlite3.Row) -> UploadRecord:
        return UploadRecord(
            source_path=row["source_path"],
            drive_file_id=row["drive_file_id"],
            drive_path=row["drive_path"],
            group_name=row["group_name"],
            log_type=row["log_type"],
            machine_id=row["machine_id"],
            source_date_yyyymmdd=row["source_date_yyyymmdd"],
            target_date_yymmdd=row["target_date_yymmdd"],
            source_size=row["source_size"],
            source_mtime=row["source_mtime"],
            content_hash=row["content_hash"],
            status=row["status"],
            attempt_count=row["attempt_count"],
            uploaded_at=row["uploaded_at"],
            last_attempt_at=row["last_attempt_at"],
            last_error=row["last_error"],
        )

    @staticmethod
    def _download_from_row(row: sqlite3.Row) -> DownloadRecord:
        return DownloadRecord(
            drive_file_id=row["drive_file_id"],
            drive_path=row["drive_path"],
            local_path=row["local_path"],
            group_name=row["group_name"],
            log_type=row["log_type"],
            machine_id=row["machine_id"],
            target_date_yymmdd=row["target_date_yymmdd"],
            drive_size=row["drive_size"],
            drive_mtime=row["drive_mtime"],
            content_hash=row["content_hash"],
            status=row["status"],
            attempt_count=row["attempt_count"],
            downloaded_at=row["downloaded_at"],
            last_attempt_at=row["last_attempt_at"],
            last_error=row["last_error"],
        )

    @staticmethod
    def _action_result_from_row(row: sqlite3.Row) -> ActionResult:
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            payload = {}
        return ActionResult(
            action=row["action"],
            status=row["status"],
            tone=row["tone"],
            message=row["message"],
            payload=payload if isinstance(payload, dict) else {},
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            error=row["error"],
        )
