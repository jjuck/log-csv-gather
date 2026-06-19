from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from log_csv_gather.config import AppConfig
from log_csv_gather.drive import DriveAdapter, DriveFile
from log_csv_gather.hash_utils import md5_file
from log_csv_gather.scanner import scan_upload_candidates
from log_csv_gather.state import DownloadRecord, StateRepository, UploadRecord

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], None]

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


@dataclass(frozen=True)
class DoctorResult:
    checks: dict[str, str]
    last_error: str | None = None

    @property
    def ok(self) -> bool:
        return all(value == "ok" for value in self.checks.values())

    def as_status(self, config: AppConfig, now: datetime) -> dict[str, Any]:
        return {
            "pc_id": config.pc_id,
            "role": config.role,
            "last_run_at": now.isoformat(),
            "ok": self.ok,
            "checks": self.checks,
            "last_error": self.last_error,
        }

    def as_line(self) -> str:
        checks = " ".join(f"{name}={value}" for name, value in self.checks.items())
        if self.last_error:
            return f"doctor: {checks}\nerror: {self.last_error}"
        return f"doctor: {checks}"


def run_upload(
    config: AppConfig,
    drive: DriveAdapter,
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
            logger.exception("upload failed for %s", candidate.source_path)
            repo.upsert_upload(_upload_record(candidate, None, content_hash, "failed", now, error))
            summary = summary.add_failure(error)
            _emit_progress("upload", index, total, summary, progress_callback, progress_every)
            if is_permanent_drive_error(exc):
                logger.error("stopping upload after permanent Drive error: %s", error)
                break
            continue
        _emit_progress("upload", index, total, summary, progress_callback, progress_every)
    _safe_upsert_json(drive, f"status/uploaders/{config.pc_id}.json", summary.as_status(config, "uploader", now))
    return summary


def run_download(
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
        logger.exception("download listing failed")
        summary = summary.add_failure(error)
        _safe_upsert_json(drive, f"status/downloaders/{config.pc_id}.json", summary.as_status(config, "downloader", now))
        return summary

    total = len(drive_files)
    progress_every = config.progress_every if progress_every is None else progress_every
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
            logger.exception("download failed for %s", drive_file.path)
            repo.upsert_download(_download_record(drive_file, local_path, parsed, "failed", now, error))
            summary = summary.add_failure(error)
            _emit_progress("download", index, total, summary, progress_callback, progress_every)
            if is_permanent_drive_error(exc):
                logger.error("stopping download after permanent Drive error: %s", error)
                break
            continue
        _emit_progress("download", index, total, summary, progress_callback, progress_every)
    _safe_upsert_json(drive, f"status/downloaders/{config.pc_id}.json", summary.as_status(config, "downloader", now))
    return summary


def run_doctor(config: AppConfig, drive: DriveAdapter, now: datetime | None = None) -> DoctorResult:
    now = now or datetime.now(timezone.utc)
    checks: dict[str, str] = {"config": "ok", "auth": "ok"}
    try:
        config.validate()
    except Exception as exc:
        return DoctorResult({"config": "failed"}, str(exc))

    try:
        drive.list_files("")
        checks["drive_root"] = "ok"
    except Exception as exc:
        error = str(exc)
        checks["drive_root"] = "failed"
        checks["write_status"] = "skipped"
        return DoctorResult(checks, error)

    result = DoctorResult({**checks, "write_status": "ok"})
    try:
        drive.upsert_json(f"status/doctor/{config.pc_id}.json", result.as_status(config, now))
    except Exception as exc:
        error = str(exc)
        logger.warning("doctor status write failed: %s", error)
        result = DoctorResult({**checks, "write_status": "failed"}, error)
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


def _same_drive_content(drive_file: DriveFile, source_size: int, content_hash: str) -> bool:
    return drive_file.size == source_size and drive_file.md5_checksum == content_hash


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
    progress_callback(message)


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
