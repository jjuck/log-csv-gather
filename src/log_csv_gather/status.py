from __future__ import annotations

from log_csv_gather.config import AppConfig
from log_csv_gather.state import DownloadRecord, StateRepository, UploadRecord


def render_status(config: AppConfig, repo: StateRepository, details: bool = False) -> str:
    uploads = repo.count_by_status("uploads")
    downloads = repo.count_by_status("downloads")
    lines = [
        f"pc_id: {config.pc_id}",
        f"role: {config.role}",
        f"state_db: {repo.db_path}",
        f"uploads: {_format_counts(uploads)}",
        f"downloads: {_format_counts(downloads)}",
    ]
    if details:
        upload_conflicts = repo.list_by_status("uploads", "conflict")
        download_conflicts = repo.list_by_status("downloads", "conflict")
        lines.extend(
            [
                "upload_conflicts:",
                *_format_upload_records(upload_conflicts),
                "download_conflicts:",
                *_format_download_records(download_conflicts),
            ]
        )
    return "\n".join(lines)


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))


def _format_upload_records(records: list[UploadRecord] | list[DownloadRecord]) -> list[str]:
    if not records:
        return ["  none"]
    return [
        f"  - {record.drive_path} source={record.source_path} error={record.last_error or '-'}"
        for record in records
        if isinstance(record, UploadRecord)
    ]


def _format_download_records(records: list[UploadRecord] | list[DownloadRecord]) -> list[str]:
    if not records:
        return ["  none"]
    return [
        f"  - {record.drive_path} local={record.local_path} error={record.last_error or '-'}"
        for record in records
        if isinstance(record, DownloadRecord)
    ]
