from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from log_csv_gather.config import AppConfig
from log_csv_gather.drive import GoogleDriveAdapter
from log_csv_gather.state import ActionResult, StateRepository
from log_csv_gather.status import render_status
from log_csv_gather.web.jobs import Job
from log_csv_gather.web.jobs import ProgressCallback
from log_csv_gather.workflows import (
    DoctorResult,
    RunSummary,
    run_doctor,
    run_download,
    run_download_dry_run,
    run_upload,
    run_upload_dry_run,
)

ActionRunner = Callable[[str, ProgressCallback], dict[str, Any]]

COMMON_ACTIONS = {"auth", "doctor"}
UPLOADER_ACTIONS = {"upload-dry-run", "upload-once"}
DOWNLOADER_ACTIONS = {"download-dry-run", "download-once"}


def allowed_actions_for_role(role: str) -> set[str]:
    if role == "uploader":
        return COMMON_ACTIONS | UPLOADER_ACTIONS
    if role == "downloader":
        return COMMON_ACTIONS | DOWNLOADER_ACTIONS
    return set(COMMON_ACTIONS)


class DefaultActionRunner:
    def __init__(self, config: AppConfig, config_path: Path) -> None:
        self.config = config
        self.config_path = Path(config_path)

    def __call__(self, action: str, progress: ProgressCallback) -> dict[str, Any]:
        repo = StateRepository(self.config.state_dir / "state.sqlite")
        if action == "auth":
            progress({"phase": "auth", "current": 1, "total": 2, "message": "Google Drive authentication started.", "feed": True})
            GoogleDriveAdapter.from_config(self.config)
            progress({"phase": "auth", "current": 2, "total": 2, "message": "Google Drive authentication succeeded.", "feed": True})
            return {"message": "Google Drive authentication succeeded."}
        if action == "doctor":
            progress({"phase": "doctor", "current": 1, "total": 3, "message": "Doctor started.", "feed": True})
            drive = GoogleDriveAdapter.from_config(self.config)
            result = run_doctor(self.config, drive)
            progress({"phase": "doctor", "current": 3, "total": 3, "message": result.as_line(), "feed": True})
            return _doctor_result(result)
        if action == "upload-dry-run":
            return _summary_result(run_upload_dry_run(self.config, repo, progress_callback=progress, progress_every=1))
        if action == "upload-once":
            drive = GoogleDriveAdapter.from_config(self.config)
            return _summary_result(run_upload(self.config, drive, repo, progress_callback=progress, progress_every=1))
        if action == "download-dry-run":
            drive = GoogleDriveAdapter.from_config(self.config)
            return _summary_result(run_download_dry_run(self.config, drive, repo, progress_callback=progress, progress_every=1))
        if action == "download-once":
            drive = GoogleDriveAdapter.from_config(self.config)
            return _summary_result(run_download(self.config, drive, repo, progress_callback=progress, progress_every=1))
        raise ValueError(f"unknown action: {action}")


def _summary_result(summary: RunSummary) -> dict[str, Any]:
    return asdict(summary)


def _doctor_result(result: DoctorResult) -> dict[str, Any]:
    payload = {
        "ok": result.ok,
        "checks": result.checks,
        "last_error": result.last_error,
        "line": result.as_line(),
    }
    if result.details:
        payload["details"] = result.details
    return payload


def local_status_payload(config: AppConfig, details: bool = False) -> dict[str, Any]:
    repo = StateRepository(config.state_dir / "state.sqlite")
    payload: dict[str, Any] = {
        "pc_id": config.pc_id,
        "role": config.role,
        "state_db": str(repo.db_path),
        "uploads": repo.count_by_status("uploads"),
        "downloads": repo.count_by_status("downloads"),
        "actions": {record.action: _action_result_to_dict(record) for record in repo.list_action_results()},
        "text": render_status(config, repo, details=details),
    }
    if details:
        payload["upload_conflicts"] = [_upload_record_to_dict(record) for record in repo.list_by_status("uploads", "conflict")]
        payload["download_conflicts"] = [
            _download_record_to_dict(record) for record in repo.list_by_status("downloads", "conflict")
        ]
        payload["upload_failed"] = [_upload_record_to_dict(record) for record in repo.list_by_status("uploads", "failed")]
        payload["download_failed"] = [_download_record_to_dict(record) for record in repo.list_by_status("downloads", "failed")]
    return payload


def tail_log_lines(config: AppConfig, lines: int = 100) -> list[str]:
    safe_lines = max(1, min(lines, 1000))
    log_path = config.state_dir / "logs" / "app.log"
    if not log_path.exists():
        return []
    return log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-safe_lines:]


def record_action_result(config: AppConfig, job: Job) -> None:
    repo = StateRepository(config.state_dir / "state.sqlite")
    payload = dict(job.result or {})
    error = job.error or _result_error(payload)
    repo.upsert_action_result(
        ActionResult(
            action=job.action,
            status=job.status,
            tone=_action_tone(job.status, payload),
            message=_action_message(job.action, job.status, payload, error),
            payload=payload,
            started_at=job.started_at,
            ended_at=job.ended_at,
            error=error,
        )
    )


def _action_tone(status: str, payload: dict[str, Any]) -> str:
    if status != "succeeded":
        return "red"
    if payload.get("ok") is False:
        return "red"
    checks = payload.get("checks")
    if isinstance(checks, dict) and any(value == "warning" for value in checks.values()):
        return "yellow"
    if int(payload.get("conflict_count") or 0) > 0:
        return "amber"
    if int(payload.get("failed_count") or 0) > 0:
        return "yellow"
    return "green"


def _action_message(action: str, status: str, payload: dict[str, Any], error: str | None) -> str:
    if error:
        return error
    if payload.get("line"):
        return str(payload["line"])
    if payload.get("message"):
        return str(payload["message"])
    if "processed_count" in payload:
        return (
            f"{action}: processed={payload.get('processed_count', 0)} "
            f"success={payload.get('success_count', 0)} failed={payload.get('failed_count', 0)} "
            f"conflict={payload.get('conflict_count', 0)}"
        )
    return f"{action} {status}"


def _result_error(payload: dict[str, Any]) -> str | None:
    value = payload.get("last_error")
    return str(value) if value else None


def _action_result_to_dict(record: ActionResult) -> dict[str, Any]:
    return {
        "action": record.action,
        "status": record.status,
        "tone": record.tone,
        "message": record.message,
        "payload": record.payload,
        "started_at": record.started_at,
        "ended_at": record.ended_at,
        "error": record.error,
    }


def _upload_record_to_dict(record: Any) -> dict[str, Any]:
    return {
        "source_path": record.source_path,
        "drive_file_id": record.drive_file_id,
        "drive_path": record.drive_path,
        "group_name": record.group_name,
        "log_type": record.log_type,
        "machine_id": record.machine_id,
        "target_date_yymmdd": record.target_date_yymmdd,
        "status": record.status,
        "attempt_count": record.attempt_count,
        "last_error": record.last_error,
    }


def _download_record_to_dict(record: Any) -> dict[str, Any]:
    return {
        "drive_file_id": record.drive_file_id,
        "drive_path": record.drive_path,
        "local_path": record.local_path,
        "group_name": record.group_name,
        "log_type": record.log_type,
        "machine_id": record.machine_id,
        "target_date_yymmdd": record.target_date_yymmdd,
        "status": record.status,
        "attempt_count": record.attempt_count,
        "last_error": record.last_error,
    }
