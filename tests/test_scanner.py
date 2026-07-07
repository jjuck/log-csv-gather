import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from log_csv_gather.config import AppConfig
from log_csv_gather.scanner import scan_upload_candidates


def _write_file(path: Path, content: bytes, mtime: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    ts = mtime.timestamp()
    os.utime(path, (ts, ts))


def test_scanner_selects_only_stable_summary_csvs_and_builds_drive_path(tmp_path: Path) -> None:
    now = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
    old = now - timedelta(minutes=10)
    recent = now - timedelta(minutes=1)

    _write_file(tmp_path / "PAS Test data" / "20260401" / "20260401_总数据.csv", b"pas", old)
    _write_file(tmp_path / "PAS Test data" / "20260401" / "20260401_MIC1数据.csv", b"mic1", old)
    _write_file(tmp_path / "PAS Test data" / "20260401" / "20260401_MIC2数据.csv", b"mic2", old)
    _write_file(tmp_path / "PAS Test data" / "bad-date" / "bad_总数据.csv", b"bad", old)
    _write_file(tmp_path / "Unknown Test data" / "20260401" / "20260401_总数据.csv", b"unknown", old)
    _write_file(tmp_path / "fail" / "20260401" / "20260401_总数据.csv", b"fail", old)
    _write_file(tmp_path / "LITE Test data" / "20260401" / "20260401_总数据.csv", b"recent", recent)
    _write_file(tmp_path / "SMIC_Test data" / "20260401" / "20260401_总数据.csv", b"smic", old)

    config = AppConfig(
        role="uploader",
        pc_id="field-pc-01",
        drive_root_folder_id="drive-root-id",
        state_dir=tmp_path / "state",
        source_root=tmp_path,
        group_name="Array_MIC",
        machine_id="성능검사기_1",
    )

    candidates = scan_upload_candidates(config, now=now)

    assert [candidate.drive_path for candidate in candidates] == [
        "logs/Array_MIC/PAS/성능검사기_1/260401/260401_PAS.csv",
        "logs/Array_MIC/SMIC/성능검사기_1/260401/260401_SMIC.csv",
    ]
    assert candidates[0].source_date_yyyymmdd == "20260401"
    assert candidates[0].target_date_yymmdd == "260401"
    assert candidates[0].log_type == "PAS"
