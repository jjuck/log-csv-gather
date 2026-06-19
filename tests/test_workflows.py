import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from log_csv_gather.config import AppConfig
from log_csv_gather.scanner import SUMMARY_MARKER
from log_csv_gather.state import StateRepository
from log_csv_gather.workflows import run_download, run_upload

from tests.fakes import FakeDriveAdapter


def _write_file(path: Path, content: bytes, mtime: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    ts = mtime.timestamp()
    os.utime(path, (ts, ts))


def _write_summary_file(root: Path, source_folder: str, date: str, content: bytes, mtime: datetime) -> None:
    _write_file(root / source_folder / date / f"{date}_{SUMMARY_MARKER}.csv", content, mtime)


def test_upload_creates_expected_drive_path_and_repeated_run_skips_duplicate(tmp_path: Path) -> None:
    now = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
    _write_file(
        tmp_path / "source" / "PAS Test data" / "20260401" / "20260401_总数据.csv",
        b"pas summary",
        now - timedelta(minutes=10),
    )
    config = AppConfig(
        role="uploader",
        pc_id="field-pc-01",
        drive_root_folder_id="drive-root-id",
        state_dir=tmp_path / "state",
        source_root=tmp_path / "source",
        group_name="Array_MIC",
        machine_id="성능검사기_1",
    )
    drive = FakeDriveAdapter()
    repo = StateRepository(tmp_path / "state" / "state.sqlite")

    first = run_upload(config, drive, repo, now=now)
    second = run_upload(config, drive, repo, now=now)

    expected_path = "logs/Array_MIC/PAS/성능검사기_1/260401/260401_PAS.csv"
    assert first.success_count == 1
    assert second.skipped_count == 1
    assert sorted(drive.files_by_path) == [
        expected_path,
        "status/uploaders/field-pc-01.json",
    ]
    assert drive.json_by_path["status/uploaders/field-pc-01.json"]["last_error"] is None


def test_upload_existing_different_drive_content_records_conflict(tmp_path: Path) -> None:
    now = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
    _write_file(
        tmp_path / "source" / "PAS Test data" / "20260401" / "20260401_总数据.csv",
        b"new local content",
        now - timedelta(minutes=10),
    )
    config = AppConfig(
        role="uploader",
        pc_id="field-pc-01",
        drive_root_folder_id="drive-root-id",
        state_dir=tmp_path / "state",
        source_root=tmp_path / "source",
        group_name="Array_MIC",
        machine_id="성능검사기_1",
    )
    drive = FakeDriveAdapter()
    drive.put_file("logs/Array_MIC/PAS/성능검사기_1/260401/260401_PAS.csv", b"old drive content")
    repo = StateRepository(tmp_path / "state" / "state.sqlite")

    summary = run_upload(config, drive, repo, now=now)

    assert summary.conflict_count == 1
    assert repo.get_upload_by_drive_path("logs/Array_MIC/PAS/성능검사기_1/260401/260401_PAS.csv").status == "conflict"


def test_upload_stops_after_permanent_drive_error(tmp_path: Path) -> None:
    now = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
    source_root = tmp_path / "source"
    _write_summary_file(source_root, "PAS Test data", "20260401", b"first", now - timedelta(minutes=10))
    _write_summary_file(source_root, "PAS Test data", "20260402", b"second", now - timedelta(minutes=10))
    config = AppConfig(
        role="uploader",
        pc_id="field-pc-01",
        drive_root_folder_id="drive-root-id",
        state_dir=tmp_path / "state",
        source_root=source_root,
        group_name="Array_MIC",
        machine_id="machine-1",
    )

    class QuotaFailingDrive(FakeDriveAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.upload_calls = 0

        def upload_file(self, local_path: Path, drive_path: str):
            self.upload_calls += 1
            raise RuntimeError("storageQuotaExceeded: account cannot write to Drive")

    drive = QuotaFailingDrive()
    repo = StateRepository(tmp_path / "state" / "state.sqlite")

    summary = run_upload(config, drive, repo, now=now)

    assert drive.upload_calls == 1
    assert summary.failed_count == 1
    assert summary.last_error and "storageQuotaExceeded" in summary.last_error
    assert repo.get_upload_by_drive_path("logs/Array_MIC/PAS/machine-1/260401/260401_PAS.csv").status == "failed"
    assert repo.get_upload_by_drive_path("logs/Array_MIC/PAS/machine-1/260402/260402_PAS.csv") is None


def test_upload_ignores_status_json_failure_after_success(tmp_path: Path) -> None:
    now = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
    source_root = tmp_path / "source"
    _write_summary_file(source_root, "PAS Test data", "20260401", b"first", now - timedelta(minutes=10))
    config = AppConfig(
        role="uploader",
        pc_id="field-pc-01",
        drive_root_folder_id="drive-root-id",
        state_dir=tmp_path / "state",
        source_root=source_root,
        group_name="Array_MIC",
        machine_id="machine-1",
    )

    class StatusFailingDrive(FakeDriveAdapter):
        def upsert_json(self, drive_path: str, data: dict):
            raise RuntimeError("status write failed")

    summary = run_upload(config, StatusFailingDrive(), StateRepository(tmp_path / "state" / "state.sqlite"), now=now)

    assert summary.success_count == 1
    assert summary.failed_count == 0
    assert summary.last_error is None


def test_upload_reports_progress_every_n_candidates(tmp_path: Path) -> None:
    now = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
    source_root = tmp_path / "source"
    _write_summary_file(source_root, "PAS Test data", "20260401", b"first", now - timedelta(minutes=10))
    _write_summary_file(source_root, "PAS Test data", "20260402", b"second", now - timedelta(minutes=10))
    config = AppConfig(
        role="uploader",
        pc_id="field-pc-01",
        drive_root_folder_id="drive-root-id",
        state_dir=tmp_path / "state",
        source_root=source_root,
        group_name="Array_MIC",
        machine_id="machine-1",
    )
    progress: list[str] = []

    run_upload(
        config,
        FakeDriveAdapter(),
        StateRepository(tmp_path / "state" / "state.sqlite"),
        now=now,
        progress_callback=progress.append,
        progress_every=1,
    )

    assert progress == [
        "upload: 1/2 processed, success=1 skipped=0 failed=0 conflict=0",
        "upload: 2/2 processed, success=2 skipped=0 failed=0 conflict=0",
    ]


def test_download_writes_expected_local_path_and_repeated_run_skips_duplicate(tmp_path: Path) -> None:
    config = AppConfig(
        role="downloader",
        pc_id="management-pc-01",
        drive_root_folder_id="drive-root-id",
        state_dir=tmp_path / "state",
        download_root=tmp_path / "downloads",
    )
    drive = FakeDriveAdapter()
    drive.put_file("logs/Array_MIC/PAS/성능검사기_1/260401/260401_PAS.csv", b"pas summary")
    repo = StateRepository(tmp_path / "state" / "state.sqlite")

    first = run_download(config, drive, repo)
    second = run_download(config, drive, repo)

    local_file = tmp_path / "downloads" / "Array_MIC" / "PAS" / "성능검사기_1" / "260401" / "260401_PAS.csv"
    assert first.success_count == 1
    assert second.skipped_count == 1
    assert local_file.read_bytes() == b"pas summary"
    assert drive.json_by_path["status/downloaders/management-pc-01.json"]["last_error"] is None


def test_download_existing_different_local_content_records_conflict(tmp_path: Path) -> None:
    config = AppConfig(
        role="downloader",
        pc_id="management-pc-01",
        drive_root_folder_id="drive-root-id",
        state_dir=tmp_path / "state",
        download_root=tmp_path / "downloads",
    )
    drive = FakeDriveAdapter()
    drive_file = drive.put_file("logs/Array_MIC/PAS/성능검사기_1/260401/260401_PAS.csv", b"drive content")
    local_file = tmp_path / "downloads" / "Array_MIC" / "PAS" / "성능검사기_1" / "260401" / "260401_PAS.csv"
    local_file.parent.mkdir(parents=True)
    local_file.write_bytes(b"local content")
    repo = StateRepository(tmp_path / "state" / "state.sqlite")

    summary = run_download(config, drive, repo)

    assert summary.conflict_count == 1
    assert repo.get_download_by_drive_file_id(drive_file.id).status == "conflict"


def test_doctor_checks_drive_root_and_writes_probe_status(tmp_path: Path) -> None:
    from log_csv_gather.workflows import run_doctor

    config = AppConfig(
        role="downloader",
        pc_id="management-pc-01",
        drive_root_folder_id="drive-root-id",
        state_dir=tmp_path / "state",
        download_root=tmp_path / "downloads",
    )
    drive = FakeDriveAdapter()

    result = run_doctor(config, drive, now=datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc))

    assert result.ok is True
    assert result.checks == {
        "config": "ok",
        "auth": "ok",
        "drive_root": "ok",
        "write_status": "ok",
    }
    assert drive.json_by_path["status/doctor/management-pc-01.json"]["ok"] is True
