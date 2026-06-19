from pathlib import Path

from log_csv_gather.state import DownloadRecord, StateRepository, UploadRecord


def test_upload_state_skips_uploaded_retries_failed_and_preserves_conflict(tmp_path: Path) -> None:
    repo = StateRepository(tmp_path / "state.sqlite")

    uploaded = UploadRecord(
        source_path="E:/PAS Test data/20260401/20260401_总数据.csv",
        drive_file_id="drive-1",
        drive_path="logs/Array_MIC/PAS/성능검사기_1/260401/260401_PAS.csv",
        group_name="Array_MIC",
        log_type="PAS",
        machine_id="성능검사기_1",
        source_date_yyyymmdd="20260401",
        target_date_yymmdd="260401",
        source_size=3,
        source_mtime=100.0,
        content_hash="md5-a",
        status="uploaded",
    )
    repo.upsert_upload(uploaded)
    assert repo.should_process_upload(uploaded.drive_path, uploaded.source_size, uploaded.content_hash) is False

    repo.upsert_upload(uploaded.with_status("failed", last_error="network"))
    assert repo.should_process_upload(uploaded.drive_path, uploaded.source_size, uploaded.content_hash) is True

    repo.upsert_upload(uploaded.with_status("conflict", last_error="different content"))
    assert repo.should_process_upload(uploaded.drive_path, uploaded.source_size, uploaded.content_hash) is False


def test_download_state_skips_downloaded_retries_failed_and_preserves_conflict(tmp_path: Path) -> None:
    repo = StateRepository(tmp_path / "state.sqlite")

    downloaded = DownloadRecord(
        drive_file_id="drive-1",
        drive_path="logs/Array_MIC/PAS/성능검사기_1/260401/260401_PAS.csv",
        local_path=str(tmp_path / "Array_MIC" / "PAS" / "성능검사기_1" / "260401" / "260401_PAS.csv"),
        group_name="Array_MIC",
        log_type="PAS",
        machine_id="성능검사기_1",
        target_date_yymmdd="260401",
        drive_size=3,
        drive_mtime="2026-06-18T10:00:00Z",
        content_hash="md5-a",
        status="downloaded",
    )
    repo.upsert_download(downloaded)
    assert repo.should_process_download(downloaded.drive_file_id, downloaded.drive_size, downloaded.content_hash) is False

    repo.upsert_download(downloaded.with_status("failed", last_error="network"))
    assert repo.should_process_download(downloaded.drive_file_id, downloaded.drive_size, downloaded.content_hash) is True

    repo.upsert_download(downloaded.with_status("conflict", last_error="different content"))
    assert repo.should_process_download(downloaded.drive_file_id, downloaded.drive_size, downloaded.content_hash) is False
