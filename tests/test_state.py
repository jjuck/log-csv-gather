from pathlib import Path

from log_csv_gather.state import ActionResult, DownloadRecord, StateRepository, UploadRecord, reset_state_database


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


def test_action_result_is_persisted_by_action_name(tmp_path: Path) -> None:
    repo = StateRepository(tmp_path / "state.sqlite")

    repo.upsert_action_result(
        ActionResult(
            action="upload-once",
            status="succeeded",
            tone="green",
            message="uploaded all files",
            payload={"processed_count": 2, "success_count": 2},
            started_at="2026-06-25T01:00:00+00:00",
            ended_at="2026-06-25T01:00:05+00:00",
        )
    )
    repo.upsert_action_result(
        ActionResult(
            action="upload-once",
            status="succeeded",
            tone="yellow",
            message="retryable failures remain",
            payload={"processed_count": 2, "failed_count": 1},
            started_at="2026-06-25T02:00:00+00:00",
            ended_at="2026-06-25T02:00:07+00:00",
            error="network timeout",
        )
    )

    result = repo.get_action_result("upload-once")

    assert result is not None
    assert result.action == "upload-once"
    assert result.tone == "yellow"
    assert result.payload["failed_count"] == 1
    assert result.error == "network timeout"
    assert [item.action for item in repo.list_action_results()] == ["upload-once"]


def test_reset_state_database_backs_up_existing_db_and_recreates_empty_state(tmp_path: Path) -> None:
    db_path = tmp_path / "state.sqlite"
    repo = StateRepository(db_path)
    upload = UploadRecord(
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
    repo.upsert_upload(upload)
    repo.upsert_action_result(
        ActionResult(
            action="upload-once",
            status="succeeded",
            tone="green",
            message="uploaded",
            payload={"success_count": 1},
        )
    )

    result = reset_state_database(db_path)

    assert result["state_db"] == str(db_path)
    assert result["reset"] is True
    assert result["backup_path"] is not None
    backup_repo = StateRepository(Path(str(result["backup_path"])))
    assert backup_repo.count_by_status("uploads") == {"uploaded": 1}
    reset_repo = StateRepository(db_path)
    assert reset_repo.count_by_status("uploads") == {}
    assert reset_repo.count_by_status("downloads") == {}
    assert reset_repo.list_action_results() == []
