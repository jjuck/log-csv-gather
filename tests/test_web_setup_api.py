from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from log_csv_gather.config import AppConfig, load_config
from log_csv_gather.scheduler import ScheduledTaskStatus
from log_csv_gather.web.app import create_app


class FakeSchedulerService:
    def __init__(self, config: AppConfig, registered: bool = False) -> None:
        self.config = config
        self.registered = registered
        self.unregister_called = False

    def status(self) -> ScheduledTaskStatus:
        return ScheduledTaskStatus(
            supported=True,
            task_name=f"LogCsvGather-{self.config.pc_id}-{self.config.role}",
            configured_enabled=self.config.scheduler.enabled,
            configured_interval_minutes=self.config.scheduler.interval_minutes,
            registered=self.registered,
            enabled=True if self.registered else None,
            interval_minutes=self.config.scheduler.interval_minutes if self.registered else None,
            state="Ready" if self.registered else None,
            command="run.bat upload-once configs\\active.yaml" if self.registered else None,
        )

    def unregister(self) -> ScheduledTaskStatus:
        self.unregister_called = True
        self.registered = False
        return self.status()


def _write_role_configs(tmp_path: Path) -> Path:
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    source_root = tmp_path / "source"
    source_root.mkdir()
    (configs_dir / "production.uploader.yaml").write_text(
        f"""
role: uploader
pc_id: field-pc-01
drive_root_folder_id: replace-with-google-drive-folder-id
source_root: "{source_root.as_posix()}"
state_dir: "../runtime/uploader"
group_name: Array_MIC
machine_id: machine-1
credentials_file: "../secrets/oauth-client.json"
token_file: "../runtime/uploader/token.json"
""",
        encoding="utf-8",
    )
    (configs_dir / "production.downloader.yaml").write_text(
        """
role: downloader
pc_id: management-pc-01
drive_root_folder_id: replace-with-google-drive-folder-id
download_root: "../runtime/downloads"
state_dir: "../runtime/downloader"
credentials_file: "../secrets/oauth-client.json"
token_file: "../runtime/downloader/token.json"
""",
        encoding="utf-8",
    )
    active_path = configs_dir / "active.yaml"
    active_path.write_text((configs_dir / "production.uploader.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    return active_path


def test_setup_api_saves_active_config_and_unregisters_existing_scheduler(tmp_path: Path) -> None:
    active_path = _write_role_configs(tmp_path)
    selected_root = tmp_path / "field-root"
    selected_root.mkdir()
    (selected_root / "PAS Test data").mkdir()
    (selected_root / "LITE Test data").mkdir()
    config = load_config(active_path)
    fake_scheduler = FakeSchedulerService(config, registered=True)
    app = create_app(
        config,
        config_path=active_path,
        port=8765,
        scheduler_service_factory=lambda _: fake_scheduler,
    )
    client = TestClient(app)

    response = client.post(
        "/api/config/setup",
        json={
            "role": "uploader",
            "pc_id": "현장 PC 01",
            "drive_root_folder_id": "drive-root-real",
            "machine_id": "성능검사기 1",
            "source_root": str(selected_root),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["role"] == "uploader"
    assert payload["pc_id"] == "현장_PC_01"
    assert payload["machine_id"] == "성능검사기_1"
    assert payload["scheduler_unregistered"] is True
    assert payload["doctor_verification_required"] is True
    assert payload["path_validation"]["status"] == "warning"
    assert payload["path_validation"]["found_count"] == 2
    assert fake_scheduler.unregister_called is True
    assert app.state.config.pc_id == "현장_PC_01"
    reloaded = load_config(active_path)
    assert reloaded.drive_root_folder_id == "drive-root-real"
    assert reloaded.source_root == selected_root
    assert reloaded.credentials_file == (active_path.parent / ".." / "secrets" / "oauth-client.json").resolve(strict=False)


def test_validate_path_api_reports_uploader_folder_coverage(tmp_path: Path) -> None:
    active_path = _write_role_configs(tmp_path)
    root = tmp_path / "partial-root"
    root.mkdir()
    (root / "PAS Test data").mkdir()
    app = create_app(load_config(active_path), config_path=active_path, port=8765)
    client = TestClient(app)

    response = client.post("/api/local/validate-path", json={"role": "uploader", "path": str(root)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "warning"
    assert payload["found_count"] == 1
    assert "PAS Test data" in payload["found"]
    assert "LITE Test data" in payload["missing"]
    assert "SMIC_Test data" in payload["missing"]


def test_folder_browser_lists_folders_without_files(tmp_path: Path) -> None:
    active_path = _write_role_configs(tmp_path)
    (tmp_path / "visible-folder").mkdir()
    (tmp_path / "plain-file.txt").write_text("hidden from folder browser", encoding="utf-8")
    app = create_app(load_config(active_path), config_path=active_path, port=8765)
    client = TestClient(app)

    response = client.get("/api/local/folders", params={"path": str(tmp_path)})

    assert response.status_code == 200
    payload = response.json()
    names = {item["name"] for item in payload["folders"]}
    assert "visible-folder" in names
    assert "plain-file.txt" not in names


def test_setup_api_rejects_path_unsafe_names(tmp_path: Path) -> None:
    active_path = _write_role_configs(tmp_path)
    app = create_app(load_config(active_path), config_path=active_path, port=8765)
    client = TestClient(app)

    response = client.post(
        "/api/config/setup",
        json={
            "role": "uploader",
            "pc_id": "bad/name",
            "drive_root_folder_id": "drive-root-real",
            "machine_id": "machine-1",
            "source_root": str(tmp_path / "source"),
        },
    )

    assert response.status_code == 400
    assert "unsafe" in response.json()["detail"]
