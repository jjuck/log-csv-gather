from pathlib import Path

import pytest

from log_csv_gather.config import ConfigError, load_config


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
    assert config.log_type_mappings["PAS Test data"] == "PAS"
    assert config.log_type_mappings["HM-3203-011 Test data"] == "3203"
    assert config.log_type_mappings["HM-3903-011 Test data"] == "3903"
    assert config.log_type_mappings["LITE Test data"] == "LITE"


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
