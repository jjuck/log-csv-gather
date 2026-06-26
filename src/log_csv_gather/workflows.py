from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from log_csv_gather.config import AppConfig, DEFAULT_LOG_TYPE_MAPPINGS
from log_csv_gather.drive import DriveAdapter, DriveFile
from log_csv_gather.hash_utils import md5_file
from log_csv_gather.scanner import scan_upload_candidates
from log_csv_gather.state import DownloadRecord, StateRepository, UploadRecord

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, Any]], None]

PERMANENT_DRIVE_ERROR_MARKERS = (
    "accessNotConfigured",
    "storageQuotaExceeded",
    "insufficientFilePermissions",
    "invalid_grant",
    "notFound",
    "File not found",
    "Google OAuth credentials file not found",
)


@dataclass(frozen=True)
class RunSummary:
    processed_count: int = 0
    success_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    conflict_count: int = 0
    last_error: str | None = None

    def as_status(self, config: AppConfig, role: str, now: datetime) -> dict[str, Any]:
        data: dict[str, Any] = {
            "pc_id": config.pc_id,
            "role": role,
            "last_run_at": now.isoformat(),
            "last_success_at": now.isoformat() if self.failed_count == 0 else None,
            "processed_count": self.processed_count,
            "success_count": self.success_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "conflict_count": self.conflict_count,
            "last_error": self.last_error,
        }
        if config.group_name:
            data["group_name"] = config.group_name
        if config.machine_id:
            data["machine_id"] = config.machine_id
        return data

    def add_success(self) -> "RunSummary":
        return _copy_summary(self, processed_count=self.processed_count + 1, success_count=self.success_count + 1)

    def add_skip(self) -> "RunSummary":
        return _copy_summary(self, skipped_count=self.skipped_count + 1)

    def add_failure(self, error: str) -> "RunSummary":
        return _copy_summary(
            self,
            processed_count=self.processed_count + 1,
            failed_count=self.failed_count + 1,
            last_error=error,
        )

    def add_conflict(self, error: str) -> "RunSummary":
        return _copy_summary(
            self,
            processed_count=self.processed_count + 1,
            conflict_count=self.conflict_count + 1,
            last_error=error,
        )

    def replace_failure_with_success(self) -> "RunSummary":
        failed_count = max(0, self.failed_count - 1)
        return _copy_summary(
            self,
            success_count=self.success_count + 1,
            failed_count=failed_count,
            last_error=None if failed_count == 0 else self.last_error,
        )

    def replace_failure_with_conflict(self, error: str) -> "RunSummary":
        return _copy_summary(
            self,
            failed_count=max(0, self.failed_count - 1),
            conflict_count=self.conflict_count + 1,
            last_error=error,
        )

    def keep_retry_failure(self, error: str) -> "RunSummary":
        return _copy_summary(self, last_error=error)


@dataclass(frozen=True)
class DoctorResult:
    checks: dict[str, str]
    last_error: str | None = None
    details: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return all(value in {"ok", "warning"} for value in self.checks.values())

    def as_status(self, config: AppConfig, now: datetime) -> dict[str, Any]:
        data: dict[str, Any] = {
            "pc_id": config.pc_id,
            "role": config.role,
            "last_run_at": now.isoformat(),
            "ok": self.ok,
            "checks": self.checks,
            "last_error": self.last_error,
        }
        if self.details:
            data["details"] = self.details
        return data

    def as_line(self) -> str:
        checks = " ".join(f"{name}={value}" for name, value in self.checks.items())
        if self.last_error:
            return f"doctor: {checks}\nerror: {self.last_error}"
        return f"doctor: {checks}"


@dataclass(frozen=True)
class _UploadRetryItem:
    candidate: Any
    content_hash: str


@dataclass(frozen=True)
class _DownloadRetryItem:
    drive_file: DriveFile
    parsed: dict[str, str]
    local_path: Path


def run_upload(
    config: AppConfig,
    drive: DriveAdapter,
    repo: StateRepository,
    now: datetime | None = None,
    progress_callback: ProgressCallback | None = None,
    progress_every: int | None = None,
    retry_wait_seconds: int = 10,
) -> RunSummary:
    now = now or datetime.now(timezone.utc)
    summary = RunSummary()
    try:
        candidates = scan_upload_candidates(config, now=now)
    except Exception as exc:
        error = str(exc)
        logger.exception("upload scan failed")
        summary = summary.add_failure(error)
        _safe_upsert_json(drive, f"status/uploaders/{config.pc_id}.json", summary.as_status(config, "uploader", now))
        return summary
    total = len(candidates)
    progress_every = config.progress_every if progress_every is None else progress_every
    retry_items: list[_UploadRetryItem] = []
    logger.info("upload scan found %s candidate(s)", total)
    for index, candidate in enumerate(candidates, start=1):
        content_hash = md5_file(candidate.source_path)
        if not repo.should_process_upload(candidate.drive_path, candidate.source_size, content_hash):
            summary = summary.add_skip()
            logger.info("upload skipped %s", candidate.drive_path)
            _emit_progress("upload", index, total, summary, progress_callback, progress_every)
            continue
        try:
            existing = drive.find_file(candidate.drive_path)
            if existing:
                if _same_drive_content(existing, candidate.source_size, content_hash):
                    repo.upsert_upload(_upload_record(candidate, existing, content_hash, "uploaded", now))
                    summary = summary.add_success()
                    logger.info("upload already present %s", candidate.drive_path)
                else:
                    error = f"Drive conflict at {candidate.drive_path}"
                    repo.upsert_upload(_upload_record(candidate, existing, content_hash, "conflict", now, error))
                    summary = summary.add_conflict(error)
                    logger.warning(error)
                _emit_progress("upload", index, total, summary, progress_callback, progress_every)
                continue
            uploaded = drive.upload_file(candidate.source_path, candidate.drive_path)
            repo.upsert_upload(_upload_record(candidate, uploaded, content_hash, "uploaded", now))
            summary = summary.add_success()
            logger.info("upload completed %s", candidate.drive_path)
        except Exception as exc:  # pragma: no cover - exercised by integration failures
            error = str(exc)
            repo.upsert_upload(_upload_record(candidate, None, content_hash, "failed", now, error))
            summary = summary.add_failure(error)
            _emit_progress("upload", index, total, summary, progress_callback, progress_every)
            if is_permanent_drive_error(exc):
                logger.exception("upload failed for %s", candidate.source_path)
                logger.error("stopping upload after permanent Drive error: %s", error)
                break
            _log_retryable_transfer_failure("upload", candidate.source_path, exc)
            retry_items.append(_UploadRetryItem(candidate, content_hash))
            continue
        _emit_progress("upload", index, total, summary, progress_callback, progress_every)
    if retry_items:
        _emit_retry_wait("upload", len(retry_items), summary, progress_callback, retry_wait_seconds)
        if retry_wait_seconds > 0:
            time.sleep(retry_wait_seconds)
        for retry_index, retry_item in enumerate(retry_items, start=1):
            summary = _retry_upload_item(config, drive, repo, retry_item, summary, now)
            _emit_progress("upload retry", retry_index, len(retry_items), summary, progress_callback, 1)
    _safe_upsert_json(drive, f"status/uploaders/{config.pc_id}.json", summary.as_status(config, "uploader", now))
    return summary


def run_upload_dry_run(
    config: AppConfig,
    repo: StateRepository,
    now: datetime | None = None,
    progress_callback: ProgressCallback | None = None,
    progress_every: int | None = None,
) -> RunSummary:
    now = now or datetime.now(timezone.utc)
    summary = RunSummary()
    candidates = scan_upload_candidates(config, now=now)
    total = len(candidates)
    progress_every = config.progress_every if progress_every is None else progress_every
    logger.info("upload dry-run scan found %s candidate(s)", total)
    for index, candidate in enumerate(candidates, start=1):
        content_hash = md5_file(candidate.source_path)
        if repo.should_process_upload(candidate.drive_path, candidate.source_size, content_hash):
            summary = _copy_summary(summary, processed_count=summary.processed_count + 1)
            logger.info("upload dry-run would process %s", candidate.drive_path)
        else:
            summary = summary.add_skip()
            logger.info("upload dry-run skipped %s", candidate.drive_path)
        _emit_progress("upload dry-run", index, total, summary, progress_callback, progress_every)
    return summary


def run_download(
    config: AppConfig,
    drive: DriveAdapter,
    repo: StateRepository,
    now: datetime | None = None,
    progress_callback: ProgressCallback | None = None,
    progress_every: int | None = None,
    retry_wait_seconds: int = 10,
) -> RunSummary:
    now = now or datetime.now(timezone.utc)
    summary = RunSummary()
    local_error = _download_root_error(config)
    if local_error:
        logger.error("download root check failed: %s", local_error)
        summary = summary.add_failure(local_error)
        _safe_upsert_json(drive, f"status/downloaders/{config.pc_id}.json", summary.as_status(config, "downloader", now))
        return summary
    try:
        drive_files = [
            drive_file
            for drive_file in drive.list_files("logs/")
            if (parsed := parse_drive_log_path(drive_file.path)) is not None and _matches_download_filters(config, parsed)
        ]
    except Exception as exc:  # pragma: no cover - exercised by integration failures
        error = str(exc)
        logger.exception("download listing failed")
        summary = summary.add_failure(error)
        _safe_upsert_json(drive, f"status/downloaders/{config.pc_id}.json", summary.as_status(config, "downloader", now))
        return summary

    total = len(drive_files)
    progress_every = config.progress_every if progress_every is None else progress_every
    retry_items: list[_DownloadRetryItem] = []
    logger.info("download scan found %s candidate(s)", total)
    for index, drive_file in enumerate(drive_files, start=1):
        parsed = parse_drive_log_path(drive_file.path)
        if parsed is None:
            continue
        if not repo.should_process_download(drive_file.id, drive_file.size, drive_file.md5_checksum):
            summary = summary.add_skip()
            logger.info("download skipped %s", drive_file.path)
            _emit_progress("download", index, total, summary, progress_callback, progress_every)
            continue
        local_path = _download_local_path(config, parsed)
        try:
            if local_path.exists():
                local_hash = md5_file(local_path)
                if local_path.stat().st_size == drive_file.size and local_hash == drive_file.md5_checksum:
                    repo.upsert_download(_download_record(drive_file, local_path, parsed, "downloaded", now))
                    summary = summary.add_success()
                    logger.info("download already present %s", local_path)
                else:
                    error = f"Local conflict at {local_path}"
                    repo.upsert_download(_download_record(drive_file, local_path, parsed, "conflict", now, error))
                    summary = summary.add_conflict(error)
                    logger.warning(error)
                _emit_progress("download", index, total, summary, progress_callback, progress_every)
                continue
            downloaded = drive.download_file(drive_file.id, local_path)
            repo.upsert_download(_download_record(drive_file, local_path, parsed, "downloaded", now, drive_file=downloaded))
            summary = summary.add_success()
            logger.info("download completed %s", local_path)
        except Exception as exc:  # pragma: no cover - exercised by integration failures
            error = str(exc)
            repo.upsert_download(_download_record(drive_file, local_path, parsed, "failed", now, error))
            summary = summary.add_failure(error)
            _emit_progress("download", index, total, summary, progress_callback, progress_every)
            if is_permanent_drive_error(exc):
                logger.exception("download failed for %s", drive_file.path)
                logger.error("stopping download after permanent Drive error: %s", error)
                break
            _log_retryable_transfer_failure("download", drive_file.path, exc)
            retry_items.append(_DownloadRetryItem(drive_file, parsed, local_path))
            continue
        _emit_progress("download", index, total, summary, progress_callback, progress_every)
    if retry_items:
        _emit_retry_wait("download", len(retry_items), summary, progress_callback, retry_wait_seconds)
        if retry_wait_seconds > 0:
            time.sleep(retry_wait_seconds)
        for retry_index, retry_item in enumerate(retry_items, start=1):
            summary = _retry_download_item(config, drive, repo, retry_item, summary, now)
            _emit_progress("download retry", retry_index, len(retry_items), summary, progress_callback, 1)
    _safe_upsert_json(drive, f"status/downloaders/{config.pc_id}.json", summary.as_status(config, "downloader", now))
    return summary


def run_download_dry_run(
    config: AppConfig,
    drive: DriveAdapter,
    repo: StateRepository,
    now: datetime | None = None,
    progress_callback: ProgressCallback | None = None,
    progress_every: int | None = None,
) -> RunSummary:
    now = now or datetime.now(timezone.utc)
    summary = RunSummary()
    try:
        drive_files = [
            drive_file
            for drive_file in drive.list_files("logs/")
            if (parsed := parse_drive_log_path(drive_file.path)) is not None and _matches_download_filters(config, parsed)
        ]
    except Exception as exc:  # pragma: no cover - exercised by integration failures
        error = str(exc)
        logger.exception("download dry-run listing failed")
        return summary.add_failure(error)

    total = len(drive_files)
    progress_every = config.progress_every if progress_every is None else progress_every
    logger.info("download dry-run scan found %s candidate(s)", total)
    for index, drive_file in enumerate(drive_files, start=1):
        parsed = parse_drive_log_path(drive_file.path)
        if parsed is None:
            continue
        if not repo.should_process_download(drive_file.id, drive_file.size, drive_file.md5_checksum):
            summary = summary.add_skip()
            logger.info("download dry-run skipped %s", drive_file.path)
            _emit_progress("download dry-run", index, total, summary, progress_callback, progress_every)
            continue
        local_path = _download_local_path(config, parsed)
        if local_path.exists():
            local_hash = md5_file(local_path)
            if local_path.stat().st_size == drive_file.size and local_hash == drive_file.md5_checksum:
                summary = summary.add_skip()
                logger.info("download dry-run local file already present %s", local_path)
            else:
                error = f"Local conflict at {local_path}"
                summary = summary.add_conflict(error)
                logger.warning(error)
        else:
            summary = _copy_summary(summary, processed_count=summary.processed_count + 1)
            logger.info("download dry-run would process %s", drive_file.path)
        _emit_progress("download dry-run", index, total, summary, progress_callback, progress_every)
    return summary


def run_doctor(config: AppConfig, drive: DriveAdapter, now: datetime | None = None) -> DoctorResult:
    now = now or datetime.now(timezone.utc)
    checks: dict[str, str] = {"config": "ok", "auth": "ok"}
    details: dict[str, Any] = {}
    try:
        config.validate()
    except Exception as exc:
        return DoctorResult({"config": "failed"}, str(exc))

    local_checks, local_details = _doctor_local_checks(config)
    checks.update(local_checks)
    details.update(local_details)

    try:
        drive.list_files("")
        checks["drive_root"] = "ok"
    except Exception as exc:
        error = str(exc)
        checks["drive_root"] = "failed"
        checks["write_status"] = "skipped"
        return DoctorResult(checks, error, details or None)

    result = DoctorResult({**checks, "write_status": "ok"}, details=details or None)
    try:
        drive.upsert_json(f"status/doctor/{config.pc_id}.json", result.as_status(config, now))
    except Exception as exc:
        error = str(exc)
        logger.warning("doctor status write failed: %s", error)
        result = DoctorResult({**checks, "write_status": "failed"}, error, details or None)
    return result


def parse_drive_log_path(drive_path: str) -> dict[str, str] | None:
    parts = drive_path.split("/")
    if len(parts) != 6 or parts[0] != "logs":
        return None
    _, group_name, log_type, machine_id, target_date_yymmdd, filename = parts
    expected = f"{target_date_yymmdd}_{log_type}.csv"
    if filename != expected:
        return None
    return {
        "group_name": group_name,
        "log_type": log_type,
        "machine_id": machine_id,
        "target_date_yymmdd": target_date_yymmdd,
        "filename": filename,
    }


def _doctor_local_checks(config: AppConfig) -> tuple[dict[str, str], dict[str, Any]]:
    if config.role == "uploader":
        return _doctor_uploader_local_checks(config)
    if config.role == "downloader":
        return _doctor_downloader_local_checks(config)
    return {}, {}


def _doctor_uploader_local_checks(config: AppConfig) -> tuple[dict[str, str], dict[str, Any]]:
    checks: dict[str, str] = {}
    details: dict[str, Any] = {}
    source_root = config.source_root
    expected = list((config.log_type_mappings or DEFAULT_LOG_TYPE_MAPPINGS).keys())
    if source_root is None or not source_root.is_dir():
        checks["source_root"] = "failed"
        checks["source_folders"] = "failed"
        details["source_folders"] = {
            "expected": expected,
            "found": [],
            "missing": expected,
            "found_count": 0,
        }
        return checks, details

    found = [name for name in expected if (source_root / name).is_dir()]
    missing = [name for name in expected if name not in found]
    checks["source_root"] = "ok"
    checks["source_folders"] = "ok" if len(found) == len(expected) else "warning" if found else "failed"
    details["source_folders"] = {
        "expected": expected,
        "found": found,
        "missing": missing,
        "found_count": len(found),
    }
    return checks, details


def _doctor_downloader_local_checks(config: AppConfig) -> tuple[dict[str, str], dict[str, Any]]:
    if config.download_root is None:
        return {"download_root": "failed"}, {"download_root": {"message": "download_root is required"}}
    root = config.download_root
    if root.exists() and not root.is_dir():
        return {
            "download_root": "failed",
        }, {
            "download_root": {
                "path": str(root),
                "message": "download root exists but is not a folder",
            }
        }
    if root.exists():
        writable = _directory_is_writable(root)
        return {
            "download_root": "ok" if writable else "failed",
        }, {
            "download_root": {
                "path": str(root),
                "exists": True,
                "writable": writable,
            }
        }

    parent = _nearest_existing_parent(root)
    can_create = bool(parent and os.access(parent, os.W_OK))
    return {
        "download_root": "ok" if can_create else "failed",
    }, {
        "download_root": {
            "path": str(root),
            "exists": False,
            "can_create": can_create,
            "existing_parent": str(parent) if parent else None,
        }
    }


def _directory_is_writable(path: Path) -> bool:
    probe = path / ".log_csv_gather_doctor.tmp"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _same_drive_content(drive_file: DriveFile, source_size: int, content_hash: str) -> bool:
    return drive_file.size == source_size and drive_file.md5_checksum == content_hash


def _retry_upload_item(
    config: AppConfig,
    drive: DriveAdapter,
    repo: StateRepository,
    item: _UploadRetryItem,
    summary: RunSummary,
    now: datetime,
) -> RunSummary:
    candidate = item.candidate
    try:
        existing = drive.find_file(candidate.drive_path)
        if existing:
            if _same_drive_content(existing, candidate.source_size, item.content_hash):
                repo.upsert_upload(_upload_record(candidate, existing, item.content_hash, "uploaded", now))
                logger.info("upload retry already present %s", candidate.drive_path)
                return summary.replace_failure_with_success()
            error = f"Drive conflict at {candidate.drive_path}"
            repo.upsert_upload(_upload_record(candidate, existing, item.content_hash, "conflict", now, error))
            logger.warning(error)
            return summary.replace_failure_with_conflict(error)
        uploaded = drive.upload_file(candidate.source_path, candidate.drive_path)
        repo.upsert_upload(_upload_record(candidate, uploaded, item.content_hash, "uploaded", now))
        logger.info("upload retry completed %s", candidate.drive_path)
        return summary.replace_failure_with_success()
    except Exception as exc:  # pragma: no cover - exercised by integration failures
        error = str(exc)
        _log_final_transfer_failure("upload retry", candidate.source_path, exc)
        repo.upsert_upload(_upload_record(candidate, None, item.content_hash, "failed", now, error))
        return summary.keep_retry_failure(error)


def _retry_download_item(
    config: AppConfig,
    drive: DriveAdapter,
    repo: StateRepository,
    item: _DownloadRetryItem,
    summary: RunSummary,
    now: datetime,
) -> RunSummary:
    try:
        if item.local_path.exists():
            local_hash = md5_file(item.local_path)
            if item.local_path.stat().st_size == item.drive_file.size and local_hash == item.drive_file.md5_checksum:
                repo.upsert_download(_download_record(item.drive_file, item.local_path, item.parsed, "downloaded", now))
                logger.info("download retry already present %s", item.local_path)
                return summary.replace_failure_with_success()
            error = f"Local conflict at {item.local_path}"
            repo.upsert_download(_download_record(item.drive_file, item.local_path, item.parsed, "conflict", now, error))
            logger.warning(error)
            return summary.replace_failure_with_conflict(error)
        downloaded = drive.download_file(item.drive_file.id, item.local_path)
        repo.upsert_download(_download_record(item.drive_file, item.local_path, item.parsed, "downloaded", now, drive_file=downloaded))
        logger.info("download retry completed %s", item.local_path)
        return summary.replace_failure_with_success()
    except Exception as exc:  # pragma: no cover - exercised by integration failures
        error = str(exc)
        _log_final_transfer_failure("download retry", item.drive_file.path, exc)
        repo.upsert_download(_download_record(item.drive_file, item.local_path, item.parsed, "failed", now, error))
        return summary.keep_retry_failure(error)


def _upload_record(
    candidate: Any,
    drive_file: DriveFile | None,
    content_hash: str,
    status: str,
    now: datetime,
    last_error: str | None = None,
) -> UploadRecord:
    return UploadRecord(
        source_path=str(candidate.source_path),
        drive_file_id=drive_file.id if drive_file else None,
        drive_path=candidate.drive_path,
        group_name=candidate.group_name,
        log_type=candidate.log_type,
        machine_id=candidate.machine_id,
        source_date_yyyymmdd=candidate.source_date_yyyymmdd,
        target_date_yymmdd=candidate.target_date_yymmdd,
        source_size=candidate.source_size,
        source_mtime=candidate.source_mtime,
        content_hash=content_hash,
        status=status,
        uploaded_at=now.isoformat() if status == "uploaded" else None,
        last_attempt_at=now.isoformat(),
        last_error=last_error,
    )


def _download_record(
    source_drive_file: DriveFile,
    local_path: Path,
    parsed: dict[str, str],
    status: str,
    now: datetime,
    last_error: str | None = None,
    drive_file: DriveFile | None = None,
) -> DownloadRecord:
    item = drive_file or source_drive_file
    return DownloadRecord(
        drive_file_id=source_drive_file.id,
        drive_path=source_drive_file.path,
        local_path=str(local_path),
        group_name=parsed["group_name"],
        log_type=parsed["log_type"],
        machine_id=parsed["machine_id"],
        target_date_yymmdd=parsed["target_date_yymmdd"],
        drive_size=item.size,
        drive_mtime=item.modified_time,
        content_hash=item.md5_checksum,
        status=status,
        downloaded_at=now.isoformat() if status == "downloaded" else None,
        last_attempt_at=now.isoformat(),
        last_error=last_error,
    )


def _matches_download_filters(config: AppConfig, parsed: dict[str, str]) -> bool:
    return (
        (not config.include_groups or parsed["group_name"] in config.include_groups)
        and (not config.include_log_types or parsed["log_type"] in config.include_log_types)
        and (not config.include_machines or parsed["machine_id"] in config.include_machines)
    )


def _download_local_path(config: AppConfig, parsed: dict[str, str]) -> Path:
    if config.download_root is None:
        raise ValueError("download_root is required")
    return (
        config.download_root
        / parsed["group_name"]
        / parsed["log_type"]
        / parsed["machine_id"]
        / parsed["target_date_yymmdd"]
        / parsed["filename"]
    )


def _download_root_error(config: AppConfig) -> str | None:
    if config.download_root is None:
        return "download_root is required"
    if config.download_root.exists() and not config.download_root.is_dir():
        return f"download_root exists but is not a directory: {config.download_root}"
    if config.download_root.exists():
        return None
    parent = _nearest_existing_parent(config.download_root)
    if parent is None:
        return f"download_root has no existing parent: {config.download_root}"
    if not os.access(parent, os.W_OK):
        return f"download_root parent is not writable: {parent}"
    return None


def _nearest_existing_parent(path: Path) -> Path | None:
    current = path
    while current != current.parent:
        current = current.parent
        if current.exists():
            return current if current.is_dir() else None
    return current if current.exists() and current.is_dir() else None


def _emit_retry_wait(
    name: str,
    retry_count: int,
    summary: RunSummary,
    progress_callback: ProgressCallback | None,
    retry_wait_seconds: int,
) -> None:
    if progress_callback is None:
        return
    message = (
        f"{name}: retrying {retry_count} failed item(s)"
        if retry_wait_seconds <= 0
        else f"{name}: retrying {retry_count} failed item(s) after {retry_wait_seconds}s"
    )
    progress_callback(
        {
            "phase": f"{name} retry wait",
            "current": 0,
            "total": retry_count,
            "success": summary.success_count,
            "skipped": summary.skipped_count,
            "failed": summary.failed_count,
            "conflict": summary.conflict_count,
            "message": message,
            "retry_wait_seconds": retry_wait_seconds,
            "feed": True,
        }
    )


def _log_retryable_transfer_failure(operation: str, path: str | Path, exc: Exception) -> None:
    logger.warning(
        "%s failed for %s; queued for retry: %s",
        operation,
        path,
        _exception_summary(exc),
    )


def _log_final_transfer_failure(operation: str, path: str | Path, exc: Exception) -> None:
    if is_permanent_drive_error(exc):
        logger.exception("%s failed for %s", operation, path)
        return
    logger.error("%s failed for %s: %s", operation, path, _exception_summary(exc))


def _exception_summary(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"
    return type(exc).__name__


def _emit_progress(
    name: str,
    index: int,
    total: int,
    summary: RunSummary,
    progress_callback: ProgressCallback | None,
    progress_every: int,
) -> None:
    if progress_callback is None or progress_every <= 0:
        return
    if index % progress_every != 0 and index != total:
        return
    message = (
        f"{name}: {index}/{total} processed, success={summary.success_count} "
        f"skipped={summary.skipped_count} failed={summary.failed_count} "
        f"conflict={summary.conflict_count}"
    )
    payload: dict[str, Any] = {
        "phase": name,
        "current": index,
        "total": total,
        "success": summary.success_count,
        "skipped": summary.skipped_count,
        "failed": summary.failed_count,
        "conflict": summary.conflict_count,
        "message": message,
    }
    if index == total or index % 10 == 0:
        payload["feed"] = True
    progress_callback(payload)


def _safe_upsert_json(drive: DriveAdapter, drive_path: str, data: dict[str, Any]) -> None:
    try:
        drive.upsert_json(drive_path, data)
        logger.info("status JSON updated %s", drive_path)
    except Exception as exc:  # pragma: no cover - exercised by integration failures
        logger.warning("status JSON update failed for %s: %s", drive_path, exc)


def is_permanent_drive_error(exc: Exception) -> bool:
    text = str(exc)
    return any(marker in text for marker in PERMANENT_DRIVE_ERROR_MARKERS)


def _copy_summary(summary: RunSummary, **changes: Any) -> RunSummary:
    values = {
        "processed_count": summary.processed_count,
        "success_count": summary.success_count,
        "skipped_count": summary.skipped_count,
        "failed_count": summary.failed_count,
        "conflict_count": summary.conflict_count,
        "last_error": summary.last_error,
    }
    values.update(changes)
    return RunSummary(**values)
