from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from log_csv_gather.config import AppConfig
from log_csv_gather.state import ActionResult, StateRepository, UploadRecord
from log_csv_gather.web.app import create_app
from log_csv_gather.web.jobs import JobManager


class FakeActionRunner:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, action: str, progress):
        self.calls.append(action)
        progress({"phase": "processing", "current": 1, "total": 1, "message": f"{action} progress", "feed": True})
        return {"action": action, "processed_count": 1, "success_count": 1}


def _config(tmp_path: Path, role: str = "uploader") -> AppConfig:
    source_root = tmp_path / "source"
    source_root.mkdir(exist_ok=True)
    kwargs = {
        "role": role,
        "pc_id": "field-pc-01" if role == "uploader" else "management-pc-01",
        "drive_root_folder_id": "drive-root-id",
        "state_dir": tmp_path / "state",
    }
    if role == "uploader":
        kwargs.update(source_root=source_root, group_name="Array_MIC", machine_id="machine-1")
    else:
        kwargs.update(download_root=tmp_path / "downloads")
    return AppConfig(**kwargs)


def test_action_endpoint_creates_job_and_exposes_feed(tmp_path: Path) -> None:
    runner = FakeActionRunner()
    manager = JobManager()
    app = create_app(
        _config(tmp_path),
        config_path=tmp_path / "config.yaml",
        port=8765,
        job_manager=manager,
        action_runner=runner,
    )
    client = TestClient(app)

    response = client.post("/api/actions/upload-dry-run")

    assert response.status_code == 202
    job_id = response.json()["job_id"]
    job = manager.wait(job_id, timeout=2)
    assert job.status == "succeeded"
    assert runner.calls == ["upload-dry-run"]

    job_response = client.get(f"/api/jobs/{job_id}")
    feed_response = client.get("/api/feed")

    assert job_response.json()["latest_progress"] == {
        "phase": "processing",
        "current": 1,
        "total": 1,
        "message": "upload-dry-run progress",
        "feed": True,
    }
    assert feed_response.json()["events"][0]["message"] == "succeeded upload-dry-run"

    status_response = client.get("/api/status")
    action_result = status_response.json()["actions"]["upload-dry-run"]
    assert action_result["status"] == "succeeded"
    assert action_result["tone"] == "green"
    assert action_result["payload"]["success_count"] == 1


def test_action_endpoint_persists_partial_failure_as_yellow(tmp_path: Path) -> None:
    class PartialFailureRunner:
        def __call__(self, action: str, progress):
            progress({"phase": "processing", "current": 1, "total": 2, "message": "one failed"})
            return {"processed_count": 2, "success_count": 1, "failed_count": 1, "conflict_count": 0, "last_error": "timeout"}

    app = create_app(
        _config(tmp_path),
        config_path=tmp_path / "config.yaml",
        port=8765,
        job_manager=JobManager(),
        action_runner=PartialFailureRunner(),
    )
    client = TestClient(app)

    response = client.post("/api/actions/upload-once")
    job_id = response.json()["job_id"]
    app.state.job_manager.wait(job_id, timeout=2)

    payload = client.get("/api/status").json()

    assert payload["actions"]["upload-once"]["tone"] == "yellow"
    assert payload["actions"]["upload-once"]["error"] == "timeout"


def test_action_endpoint_rejects_action_for_wrong_role(tmp_path: Path) -> None:
    app = create_app(
        _config(tmp_path, role="uploader"),
        config_path=tmp_path / "config.yaml",
        port=8765,
        job_manager=JobManager(),
        action_runner=FakeActionRunner(),
    )
    client = TestClient(app)

    response = client.post("/api/actions/download-once")

    assert response.status_code == 403
    assert "not allowed" in response.json()["detail"]


def test_status_api_returns_counts_and_conflict_details(tmp_path: Path) -> None:
    config = _config(tmp_path)
    repo = StateRepository(config.state_dir / "state.sqlite")
    repo.upsert_upload(
        UploadRecord(
            source_path="E:/PAS/20260401/20260401_summary.csv",
            drive_file_id="drive-1",
            drive_path="logs/Array_MIC/PAS/machine-1/260401/260401_PAS.csv",
            group_name="Array_MIC",
            log_type="PAS",
            machine_id="machine-1",
            source_date_yyyymmdd="20260401",
            target_date_yymmdd="260401",
            source_size=10,
            source_mtime=1.0,
            content_hash="hash",
            status="conflict",
            last_error="different content",
        )
    )
    app = create_app(config, config_path=tmp_path / "config.yaml", port=8765)
    client = TestClient(app)

    response = client.get("/api/status?details=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["uploads"] == {"conflict": 1}
    assert payload["downloads"] == {}
    assert payload["upload_conflicts"][0]["drive_path"] == "logs/Array_MIC/PAS/machine-1/260401/260401_PAS.csv"
    assert payload["upload_conflicts"][0]["last_error"] == "different content"


def test_local_state_reset_api_clears_sqlite_without_touching_config_or_token(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config_path = tmp_path / "config.yaml"
    token_path = config.state_dir / "token.json"
    token_path.parent.mkdir(parents=True)
    token_path.write_text("token", encoding="utf-8")
    config_path.write_text("role: uploader\n", encoding="utf-8")
    repo = StateRepository(config.state_dir / "state.sqlite")
    repo.upsert_upload(
        UploadRecord(
            source_path="E:/PAS/20260401/20260401_summary.csv",
            drive_file_id="drive-1",
            drive_path="logs/Array_MIC/PAS/machine-1/260401/260401_PAS.csv",
            group_name="Array_MIC",
            log_type="PAS",
            machine_id="machine-1",
            source_date_yyyymmdd="20260401",
            target_date_yymmdd="260401",
            source_size=10,
            source_mtime=1.0,
            content_hash="hash",
            status="uploaded",
        )
    )
    repo.upsert_action_result(
        ActionResult(
            action="upload-once",
            status="succeeded",
            tone="green",
            message="uploaded",
            payload={"success_count": 1},
        )
    )
    app = create_app(config, config_path=config_path, port=8765)
    client = TestClient(app)

    response = client.post("/api/state/reset")

    assert response.status_code == 200
    payload = response.json()
    assert payload["reset"] is True
    assert payload["backup_path"]
    assert token_path.read_text(encoding="utf-8") == "token"
    assert config_path.read_text(encoding="utf-8") == "role: uploader\n"
    status = client.get("/api/status?details=true").json()
    assert status["uploads"] == {}
    assert status["downloads"] == {}
    assert status["actions"] == {}


def test_log_tail_api_returns_recent_app_log_lines(tmp_path: Path) -> None:
    config = _config(tmp_path)
    log_file = config.state_dir / "logs" / "app.log"
    log_file.parent.mkdir(parents=True)
    log_file.write_text("one\ntwo\nthree\n", encoding="utf-8")
    app = create_app(config, config_path=tmp_path / "config.yaml", port=8765)
    client = TestClient(app)

    response = client.get("/api/logs/tail?lines=2")

    assert response.status_code == 200
    assert response.json()["lines"] == ["two", "three"]
