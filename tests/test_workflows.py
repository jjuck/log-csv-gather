import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from log_csv_gather.config import AppConfig
from log_csv_gather.scanner import SUMMARY_MARKER
from log_csv_gather.state import StateRepository
from log_csv_gather import workflows
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


def test_upload_creates_smic_drive_path_when_smic_source_folder_exists(tmp_path: Path) -> None:
    now = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
    source_root = tmp_path / "source"
    _write_summary_file(source_root, "SMIC_Test data", "20260401", b"smic summary", now - timedelta(minutes=10))
    config = AppConfig(
        role="uploader",
        pc_id="field-pc-01",
        drive_root_folder_id="drive-root-id",
        state_dir=tmp_path / "state",
        source_root=source_root,
        group_name="Array_MIC",
        machine_id="machine-1",
    )
    drive = FakeDriveAdapter()

    summary = run_upload(config, drive, StateRepository(tmp_path / "state" / "state.sqlite"), now=now)

    assert summary.success_count == 1
    assert "logs/Array_MIC/SMIC/machine-1/260401/260401_SMIC.csv" in drive.files_by_path


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


def test_upload_retries_retryable_failures_once_at_end(tmp_path: Path) -> None:
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

    class OneTimeoutDrive(FakeDriveAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.upload_calls_by_path: dict[str, int] = {}

        def upload_file(self, local_path: Path, drive_path: str):
            self.upload_calls_by_path[drive_path] = self.upload_calls_by_path.get(drive_path, 0) + 1
            if drive_path.endswith("260401_PAS.csv") and self.upload_calls_by_path[drive_path] == 1:
                raise TimeoutError("The read operation timed out")
            return super().upload_file(local_path, drive_path)

    drive = OneTimeoutDrive()
    repo = StateRepository(tmp_path / "state" / "state.sqlite")
    progress: list[dict] = []

    summary = run_upload(
        config,
        drive,
        repo,
        now=now,
        progress_callback=progress.append,
        progress_every=1,
        retry_wait_seconds=0,
    )

    failed_then_retried = "logs/Array_MIC/PAS/machine-1/260401/260401_PAS.csv"
    assert drive.upload_calls_by_path[failed_then_retried] == 2
    assert summary.success_count == 2
    assert summary.failed_count == 0
    assert summary.last_error is None
    assert repo.get_upload_by_drive_path(failed_then_retried).status == "uploaded"
    assert any(item["phase"] == "upload retry wait" for item in progress)
    assert any(item["phase"] == "upload retry" and item["current"] == 1 for item in progress)


def test_upload_retryable_failure_logs_without_traceback(tmp_path: Path, caplog) -> None:
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

    class OneTimeoutDrive(FakeDriveAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.upload_calls = 0

        def upload_file(self, local_path: Path, drive_path: str):
            self.upload_calls += 1
            if self.upload_calls == 1:
                raise TimeoutError("The read operation timed out")
            return super().upload_file(local_path, drive_path)

    caplog.set_level(logging.INFO, logger="log_csv_gather.workflows")

    run_upload(
        config,
        OneTimeoutDrive(),
        StateRepository(tmp_path / "state" / "state.sqlite"),
        now=now,
        retry_wait_seconds=0,
    )

    retryable_records = [
        record
        for record in caplog.records
        if record.name == "log_csv_gather.workflows" and "queued for retry" in record.getMessage()
    ]
    assert len(retryable_records) == 1
    assert retryable_records[0].levelno == logging.WARNING
    assert retryable_records[0].exc_info is None
    assert "Traceback" not in caplog.text


def test_upload_missing_source_root_records_structural_failure(tmp_path: Path) -> None:
    config = AppConfig(
        role="uploader",
        pc_id="field-pc-01",
        drive_root_folder_id="drive-root-id",
        state_dir=tmp_path / "state",
        source_root=tmp_path / "missing-source",
        group_name="Array_MIC",
        machine_id="machine-1",
    )
    drive = FakeDriveAdapter()

    summary = run_upload(config, drive, StateRepository(tmp_path / "state" / "state.sqlite"))

    assert summary.failed_count == 1
    assert summary.last_error and "source_root" in summary.last_error
    assert drive.json_by_path["status/uploaders/field-pc-01.json"]["failed_count"] == 1


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
    progress: list[dict] = []

    run_upload(
        config,
        FakeDriveAdapter(),
        StateRepository(tmp_path / "state" / "state.sqlite"),
        now=now,
        progress_callback=progress.append,
        progress_every=1,
    )

    assert progress == [
        {
            "phase": "upload",
            "current": 1,
            "total": 2,
            "success": 1,
            "skipped": 0,
            "failed": 0,
            "conflict": 0,
            "message": "upload: 1/2 processed, success=1 skipped=0 failed=0 conflict=0",
        },
        {
            "phase": "upload",
            "current": 2,
            "total": 2,
            "success": 2,
            "skipped": 0,
            "failed": 0,
            "conflict": 0,
            "message": "upload: 2/2 processed, success=2 skipped=0 failed=0 conflict=0",
            "feed": True,
        },
    ]


def test_upload_dry_run_reports_candidates_without_writing_drive_or_state(tmp_path: Path) -> None:
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
    drive = FakeDriveAdapter()
    repo = StateRepository(tmp_path / "state" / "state.sqlite")

    summary = workflows.run_upload_dry_run(config, repo, now=now)

    assert summary.processed_count == 1
    assert summary.success_count == 0
    assert drive.files_by_path == {}
    assert repo.count_by_status("uploads") == {}


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


def test_download_retries_retryable_failures_once_at_end(tmp_path: Path) -> None:
    config = AppConfig(
        role="downloader",
        pc_id="management-pc-01",
        drive_root_folder_id="drive-root-id",
        state_dir=tmp_path / "state",
        download_root=tmp_path / "downloads",
    )

    class OneTimeoutDrive(FakeDriveAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.download_calls_by_id: dict[str, int] = {}

        def download_file(self, drive_file_id: str, local_path: Path):
            self.download_calls_by_id[drive_file_id] = self.download_calls_by_id.get(drive_file_id, 0) + 1
            if self.download_calls_by_id[drive_file_id] == 1:
                raise TimeoutError("The read operation timed out")
            return super().download_file(drive_file_id, local_path)

    drive = OneTimeoutDrive()
    drive_file = drive.put_file("logs/Array_MIC/PAS/machine-1/260401/260401_PAS.csv", b"pas summary")
    repo = StateRepository(tmp_path / "state" / "state.sqlite")
    progress: list[dict] = []

    summary = run_download(
        config,
        drive,
        repo,
        progress_callback=progress.append,
        progress_every=1,
        retry_wait_seconds=0,
    )

    assert drive.download_calls_by_id[drive_file.id] == 2
    assert summary.success_count == 1
    assert summary.failed_count == 0
    assert summary.last_error is None
    assert repo.get_download_by_drive_file_id(drive_file.id).status == "downloaded"
    assert any(item["phase"] == "download retry wait" for item in progress)
    assert any(item["phase"] == "download retry" and item["current"] == 1 for item in progress)


def test_download_retryable_failure_logs_without_traceback(tmp_path: Path, caplog) -> None:
    config = AppConfig(
        role="downloader",
        pc_id="management-pc-01",
        drive_root_folder_id="drive-root-id",
        state_dir=tmp_path / "state",
        download_root=tmp_path / "downloads",
    )

    class OneTimeoutDrive(FakeDriveAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.download_calls = 0

        def download_file(self, drive_file_id: str, local_path: Path):
            self.download_calls += 1
            if self.download_calls == 1:
                raise TimeoutError("The read operation timed out")
            return super().download_file(drive_file_id, local_path)

    drive = OneTimeoutDrive()
    drive.put_file("logs/Array_MIC/PAS/machine-1/260401/260401_PAS.csv", b"pas summary")
    caplog.set_level(logging.INFO, logger="log_csv_gather.workflows")

    run_download(
        config,
        drive,
        StateRepository(tmp_path / "state" / "state.sqlite"),
        retry_wait_seconds=0,
    )

    retryable_records = [
        record
        for record in caplog.records
        if record.name == "log_csv_gather.workflows" and "queued for retry" in record.getMessage()
    ]
    assert len(retryable_records) == 1
    assert retryable_records[0].levelno == logging.WARNING
    assert retryable_records[0].exc_info is None
    assert "Traceback" not in caplog.text


def test_download_root_file_records_structural_failure_before_drive_listing(tmp_path: Path) -> None:
    download_root = tmp_path / "download-root"
    download_root.write_text("not a folder", encoding="utf-8")
    config = AppConfig(
        role="downloader",
        pc_id="management-pc-01",
        drive_root_folder_id="drive-root-id",
        state_dir=tmp_path / "state",
        download_root=download_root,
    )

    class ListingShouldNotRunDrive(FakeDriveAdapter):
        def list_files(self, prefix: str = "logs/"):
            raise AssertionError("Drive listing should not run when local root is structurally invalid")

    drive = ListingShouldNotRunDrive()

    summary = run_download(config, drive, StateRepository(tmp_path / "state" / "state.sqlite"))

    assert summary.failed_count == 1
    assert summary.last_error and "download_root" in summary.last_error
    assert drive.json_by_path["status/downloaders/management-pc-01.json"]["failed_count"] == 1


def test_download_dry_run_reports_remote_candidates_without_writing_files_or_state(tmp_path: Path) -> None:
    config = AppConfig(
        role="downloader",
        pc_id="management-pc-01",
        drive_root_folder_id="drive-root-id",
        state_dir=tmp_path / "state",
        download_root=tmp_path / "downloads",
    )
    drive = FakeDriveAdapter()
    drive.put_file("logs/Array_MIC/PAS/machine-1/260401/260401_PAS.csv", b"pas summary")
    repo = StateRepository(tmp_path / "state" / "state.sqlite")

    summary = workflows.run_download_dry_run(config, drive, repo)

    assert summary.processed_count == 1
    assert summary.success_count == 0
    assert not (tmp_path / "downloads").exists()
    assert repo.count_by_status("downloads") == {}


def test_doctor_checks_drive_root_local_download_root_and_writes_probe_status(tmp_path: Path) -> None:
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
        "download_root": "ok",
        "drive_root": "ok",
        "write_status": "ok",
    }
    assert drive.json_by_path["status/doctor/management-pc-01.json"]["ok"] is True


def test_doctor_warns_when_some_uploader_source_folders_are_missing(tmp_path: Path) -> None:
    from log_csv_gather.workflows import run_doctor

    source_root = tmp_path / "source"
    (source_root / "PAS Test data").mkdir(parents=True)
    config = AppConfig(
        role="uploader",
        pc_id="field-pc-01",
        drive_root_folder_id="drive-root-id",
        state_dir=tmp_path / "state",
        source_root=source_root,
        group_name="Array_MIC",
        machine_id="machine-1",
    )
    drive = FakeDriveAdapter()

    result = run_doctor(config, drive, now=datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc))

    assert result.ok is True
    assert result.checks["source_root"] == "ok"
    assert result.checks["source_folders"] == "warning"
    assert result.details["source_folders"]["found_count"] == 1
    assert "LITE Test data" in result.details["source_folders"]["missing"]


def test_doctor_fails_when_downloader_root_is_a_file(tmp_path: Path) -> None:
    from log_csv_gather.workflows import run_doctor

    download_root = tmp_path / "download-target"
    download_root.write_text("not a folder", encoding="utf-8")
    config = AppConfig(
        role="downloader",
        pc_id="management-pc-01",
        drive_root_folder_id="drive-root-id",
        state_dir=tmp_path / "state",
        download_root=download_root,
    )

    result = run_doctor(config, FakeDriveAdapter(), now=datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc))

    assert result.ok is False
    assert result.checks["download_root"] == "failed"
