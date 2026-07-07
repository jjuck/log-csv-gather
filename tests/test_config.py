from pathlib import Path

import pytest

from log_csv_gather.config import ConfigError, load_config, update_scheduler_settings


def test_loads_uploader_config_with_default_mappings(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"""
role: uploader
pc_id: field-pc-01
group_name: Array_MIC
machine_id: 성능검사기_1
drive_root_folder_id: drive-root-id
source_root: "{source_root.as_posix()}"
state_dir: "{(tmp_path / "state").as_posix()}"
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.role == "uploader"
    assert config.file_stable_minutes == 5
    assert config.progress_every == 10
    assert config.drive_timeout_seconds == 60
    assert config.drive_num_retries == 3
    assert config.web.host == "127.0.0.1"
    assert config.web.preferred_port == 8765
    assert config.web.open_browser is True
    assert config.web.browser_open_throttle_seconds == 3
    assert config.scheduler.enabled is True
    assert config.scheduler.interval_minutes == 60
    assert config.scheduler.task_name is None
    assert config.log_type_mappings["PAS Test data"] == "PAS"
    assert config.log_type_mappings["HM-3203-011 Test data"] == "3203"
    assert config.log_type_mappings["HM-3903-011 Test data"] == "3903"
    assert config.log_type_mappings["LITE Test data"] == "LITE"
    assert config.log_type_mappings["SMIC_Test data"] == "SMIC"


def test_rejects_downloader_without_download_root(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"""
role: downloader
pc_id: management-pc-01
drive_root_folder_id: drive-root-id
state_dir: "{(tmp_path / "state").as_posix()}"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="download_root"):
        load_config(config_file)


def test_loads_service_account_file_for_unattended_drive_access(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    key_file = tmp_path / "service-account.json"
    key_file.write_text("{}", encoding="utf-8")
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"""
role: uploader
pc_id: field-pc-01
group_name: Array_MIC
machine_id: 성능검사기_1
drive_root_folder_id: drive-root-id
source_root: "{source_root.as_posix()}"
state_dir: "{(tmp_path / "state").as_posix()}"
service_account_file: "{key_file.as_posix()}"
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.service_account_file == key_file


def test_rejects_negative_progress_every(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"""
role: uploader
pc_id: field-pc-01
group_name: Array_MIC
machine_id: machine-1
drive_root_folder_id: drive-root-id
source_root: "{source_root.as_posix()}"
state_dir: "{(tmp_path / "state").as_posix()}"
progress_every: -1
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="progress_every"):
        load_config(config_file)


def test_loads_drive_timeout_and_retry_settings(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"""
role: uploader
pc_id: field-pc-01
group_name: Array_MIC
machine_id: machine-1
drive_root_folder_id: drive-root-id
source_root: "{source_root.as_posix()}"
state_dir: "{(tmp_path / "state").as_posix()}"
drive_timeout_seconds: 15
drive_num_retries: 4
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.drive_timeout_seconds == 15
    assert config.drive_num_retries == 4


def test_loads_web_and_scheduler_sections(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"""
role: uploader
pc_id: field-pc-01
group_name: Array_MIC
machine_id: machine-1
drive_root_folder_id: drive-root-id
source_root: "{source_root.as_posix()}"
state_dir: "{(tmp_path / "state").as_posix()}"
web:
  host: "127.0.0.1"
  preferred_port: 8777
  open_browser: false
  browser_open_throttle_seconds: 9
scheduler:
  enabled: false
  interval_minutes: 30
  task_name: CustomTask
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.web.preferred_port == 8777
    assert config.web.open_browser is False
    assert config.web.browser_open_throttle_seconds == 9
    assert config.scheduler.enabled is False
    assert config.scheduler.interval_minutes == 30
    assert config.scheduler.task_name == "CustomTask"


def test_relative_paths_are_resolved_from_config_directory(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    source_root = config_dir / "source"
    source_root.mkdir()
    secrets_dir = config_dir / "secrets"
    secrets_dir.mkdir()
    credentials_file = secrets_dir / "oauth-client.json"
    credentials_file.write_text("{}", encoding="utf-8")
    config_file = config_dir / "config.yaml"
    config_file.write_text(
        """
role: uploader
pc_id: field-pc-01
group_name: Array_MIC
machine_id: 성능검사기_1
drive_root_folder_id: drive-root-id
source_root: "source"
state_dir: "state/uploader"
credentials_file: "secrets/oauth-client.json"
token_file: "state/uploader/token.json"
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.source_root == source_root
    assert config.state_dir == config_dir / "state" / "uploader"
    assert config.credentials_file == credentials_file
    assert config.token_file == config_dir / "state" / "uploader" / "token.json"


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("drive_timeout_seconds", 0, "drive_timeout_seconds"),
        ("drive_num_retries", -1, "drive_num_retries"),
    ],
)
def test_rejects_invalid_drive_timeout_and_retry_settings(
    tmp_path: Path,
    key: str,
    value: int,
    message: str,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"""
role: uploader
pc_id: field-pc-01
group_name: Array_MIC
machine_id: machine-1
drive_root_folder_id: drive-root-id
source_root: "{source_root.as_posix()}"
state_dir: "{(tmp_path / "state").as_posix()}"
{key}: {value}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=message):
        load_config(config_file)


@pytest.mark.parametrize(
    ("section", "key", "value", "message"),
    [
        ("web", "host", "0.0.0.0", "web.host"),
        ("web", "preferred_port", 0, "web.preferred_port"),
        ("web", "browser_open_throttle_seconds", -1, "web.browser_open_throttle_seconds"),
        ("scheduler", "interval_minutes", 0, "scheduler.interval_minutes"),
    ],
)
def test_rejects_invalid_web_and_scheduler_settings(
    tmp_path: Path,
    section: str,
    key: str,
    value: object,
    message: str,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    rendered = f'"{value}"' if isinstance(value, str) else value
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"""
role: uploader
pc_id: field-pc-01
group_name: Array_MIC
machine_id: machine-1
drive_root_folder_id: drive-root-id
source_root: "{source_root.as_posix()}"
state_dir: "{(tmp_path / "state").as_posix()}"
{section}:
  {key}: {rendered}
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=message):
        load_config(config_file)


def test_update_scheduler_settings_persists_yaml(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"""
role: uploader
pc_id: field-pc-01
group_name: Array_MIC
machine_id: 성능검사기_1
drive_root_folder_id: drive-root-id
source_root: "{source_root.as_posix()}"
state_dir: "{(tmp_path / "state").as_posix()}"
scheduler:
  enabled: true
  interval_minutes: 60
  task_name:
""",
        encoding="utf-8",
    )

    updated = update_scheduler_settings(
        config_file,
        enabled=False,
        interval_minutes=30,
        task_name="CustomLogTask",
    )
    reloaded = load_config(config_file)

    assert updated.scheduler.enabled is False
    assert updated.scheduler.interval_minutes == 30
    assert updated.scheduler.task_name == "CustomLogTask"
    assert reloaded.scheduler == updated.scheduler
    assert "성능검사기_1" in config_file.read_text(encoding="utf-8")
