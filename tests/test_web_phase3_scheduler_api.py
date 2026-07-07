from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from log_csv_gather.config import AppConfig, SchedulerConfig, load_config
from log_csv_gather.scheduler import ScheduledTaskStatus
from log_csv_gather.web.app import create_app


class FakeSchedulerService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.registered = False
        self.enabled = config.scheduler.enabled

    def status(self) -> ScheduledTaskStatus:
        return ScheduledTaskStatus(
            supported=True,
            task_name=self.config.scheduler.task_name or f"LogCsvGather-{self.config.pc_id}-{self.config.role}",
            configured_enabled=self.config.scheduler.enabled,
            configured_interval_minutes=self.config.scheduler.interval_minutes,
            registered=self.registered,
            enabled=self.enabled if self.registered else None,
            interval_minutes=self.config.scheduler.interval_minutes if self.registered else None,
            state="Ready" if self.registered and self.enabled else "Disabled" if self.registered else None,
            command="run.bat upload-once config.yaml" if self.registered else None,
        )

    def register_or_update(self, interval_minutes: int | None = None, enabled: bool | None = None) -> ScheduledTaskStatus:
        self.registered = True
        if enabled is not None:
            self.enabled = enabled
        return self.status()

    def unregister(self) -> ScheduledTaskStatus:
        self.registered = False
        self.enabled = False
        return self.status()

    def set_enabled(self, enabled: bool) -> ScheduledTaskStatus:
        self.registered = True
        self.enabled = enabled
        return self.status()


def _write_config(tmp_path: Path, interval: int = 60, enabled: bool = True) -> Path:
    source_root = tmp_path / "source"
    source_root.mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
role: uploader
pc_id: field-pc-01
drive_root_folder_id: drive-root-id
source_root: "{source_root.as_posix()}"
state_dir: "{(tmp_path / "state").as_posix()}"
group_name: Array_MIC
machine_id: 성능검사기_1
scheduler:
  enabled: {str(enabled).lower()}
  interval_minutes: {interval}
  task_name:
""",
        encoding="utf-8",
    )
    return config_path


def _write_role_configs(tmp_path: Path) -> Path:
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    source_root = tmp_path / "source"
    source_root.mkdir()
    (configs_dir / "production.uploader.yaml").write_text(
        f"""
role: uploader
pc_id: field-pc-01
drive_root_folder_id: drive-root-id
source_root: "{source_root.as_posix()}"
state_dir: "../runtime/uploader"
group_name: Array_MIC
machine_id: machine-1
""",
        encoding="utf-8",
    )
    (configs_dir / "production.downloader.yaml").write_text(
        """
role: downloader
pc_id: management-pc-01
drive_root_folder_id: drive-root-id
download_root: "../runtime/downloads"
state_dir: "../runtime/downloader"
""",
        encoding="utf-8",
    )
    active_path = configs_dir / "active.yaml"
    active_path.write_text((configs_dir / "production.uploader.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    return active_path


def test_scheduler_status_api_returns_configured_task_context(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, interval=45)
    config = load_config(config_path)
    fake = FakeSchedulerService(config)
    app = create_app(
        config,
        config_path=config_path,
        port=8765,
        scheduler_service_factory=lambda _: fake,
    )
    client = TestClient(app)

    response = client.get("/api/scheduler")

    assert response.status_code == 200
    payload = response.json()
    assert payload["supported"] is True
    assert payload["registered"] is False
    assert payload["configured_interval_minutes"] == 45
    assert payload["task_name"] == "LogCsvGather-field-pc-01-uploader"


def test_scheduler_register_updates_config_and_task(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, interval=60, enabled=True)
    config = load_config(config_path)
    services: list[FakeSchedulerService] = []

    def factory(current_config: AppConfig) -> FakeSchedulerService:
        service = FakeSchedulerService(current_config)
        services.append(service)
        return service

    app = create_app(config, config_path=config_path, port=8765, scheduler_service_factory=factory)
    client = TestClient(app)

    response = client.post("/api/scheduler/register", json={"interval_minutes": 30, "enabled": False})

    assert response.status_code == 200
    payload = response.json()
    assert payload["registered"] is True
    assert payload["configured_interval_minutes"] == 30
    assert payload["configured_enabled"] is False
    assert load_config(config_path).scheduler == SchedulerConfig(enabled=False, interval_minutes=30, task_name=None)
    assert app.state.config.scheduler.interval_minutes == 30
    assert services[-1].config.scheduler.enabled is False


def test_scheduler_enable_disable_and_unregister_apis(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    config = load_config(config_path)
    fake = FakeSchedulerService(replace(config, scheduler=SchedulerConfig(enabled=True, interval_minutes=60)))
    app = create_app(
        config,
        config_path=config_path,
        port=8765,
        scheduler_service_factory=lambda _: fake,
    )
    client = TestClient(app)

    disabled = client.post("/api/scheduler/disable")
    enabled = client.post("/api/scheduler/enable")
    removed = client.post("/api/scheduler/unregister")

    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert enabled.json()["enabled"] is True
    assert removed.json()["registered"] is False


def test_active_config_api_switches_role_and_unregistered_existing_task(tmp_path: Path) -> None:
    active_path = _write_role_configs(tmp_path)
    config = load_config(active_path)
    fake = FakeSchedulerService(config)
    fake.registered = True
    app = create_app(
        config,
        config_path=active_path,
        port=8765,
        scheduler_service_factory=lambda _: fake,
    )
    client = TestClient(app)

    response = client.post("/api/config/role", json={"role": "downloader"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["role"] == "downloader"
    assert payload["active_exists"] is True
    assert payload["scheduler_unregistered"] is True
    assert fake.registered is False
    assert app.state.config.role == "downloader"
    assert app.state.config_path == active_path
    assert load_config(active_path).role == "downloader"


def test_active_config_reset_api_deletes_active_yaml(tmp_path: Path) -> None:
    active_path = _write_role_configs(tmp_path)
    config = load_config(active_path)
    fake = FakeSchedulerService(config)
    fake.registered = True
    app = create_app(
        config,
        config_path=active_path,
        port=8765,
        scheduler_service_factory=lambda _: fake,
    )
    client = TestClient(app)

    response = client.post("/api/config/active/reset")

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert response.json()["scheduler_unregistered"] is True
    assert response.json()["restart_recommended"] is True
    assert fake.registered is False
    assert not active_path.exists()
