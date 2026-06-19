from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from log_csv_gather.config import AppConfig

DATE_RE = re.compile(r"^\d{8}$")
SUMMARY_MARKER = "总数据"


@dataclass(frozen=True)
class UploadCandidate:
    source_path: Path
    drive_path: str
    group_name: str
    log_type: str
    machine_id: str
    source_date_yyyymmdd: str
    target_date_yymmdd: str
    source_size: int
    source_mtime: float


def yyyymmdd_to_yymmdd(value: str) -> str:
    if not DATE_RE.match(value):
        raise ValueError(f"expected YYYYMMDD date folder, got {value!r}")
    return value[2:]


def scan_upload_candidates(config: AppConfig, now: datetime | None = None) -> list[UploadCandidate]:
    config.validate()
    if config.role != "uploader":
        raise ValueError("scan_upload_candidates requires uploader config")
    if config.source_root is None or config.group_name is None or config.machine_id is None:
        raise ValueError("uploader config is incomplete")

    now = now or datetime.now(timezone.utc)
    stable_before = now.timestamp() - (config.file_stable_minutes * 60)
    ignored = set(config.ignore_dirs)
    candidates: list[UploadCandidate] = []

    for source_folder, log_type in sorted(config.log_type_mappings.items()):
        if source_folder in ignored:
            continue
        root = config.source_root / source_folder
        if not root.is_dir():
            continue
        for date_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            if not DATE_RE.match(date_dir.name):
                continue
            target_date = yyyymmdd_to_yymmdd(date_dir.name)
            for csv_file in sorted(date_dir.glob("*.csv")):
                if SUMMARY_MARKER not in csv_file.name:
                    continue
                stat = csv_file.stat()
                if stat.st_mtime > stable_before:
                    continue
                drive_path = "/".join(
                    [
                        "logs",
                        config.group_name,
                        log_type,
                        config.machine_id,
                        target_date,
                        f"{target_date}_{log_type}.csv",
                    ]
                )
                candidates.append(
                    UploadCandidate(
                        source_path=csv_file,
                        drive_path=drive_path,
                        group_name=config.group_name,
                        log_type=log_type,
                        machine_id=config.machine_id,
                        source_date_yyyymmdd=date_dir.name,
                        target_date_yymmdd=target_date,
                        source_size=stat.st_size,
                        source_mtime=stat.st_mtime,
                    )
                )
    return candidates
