from __future__ import annotations

from pathlib import Path

import pytest

from log_csv_gather.active_config import ActiveConfigError, ActiveConfigManager
from log_csv_gather.config import load_config


def _write_configs(tmp_path: Path) -> Path:
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
    return configs_dir


def test_switch_role_copies_selected_production_config_to_active(tmp_path: Path) -> None:
    configs_dir = _write_configs(tmp_path)
    manager = ActiveConfigManager(configs_dir / "production.uploader.yaml")

    selection = manager.switch_role("downloader")

    assert selection.config.role == "downloader"
    assert selection.config_path == configs_dir / "active.yaml"
    assert (configs_dir / "active.yaml").read_text(encoding="utf-8").startswith("\nrole: downloader")


def test_reset_deletes_active_config(tmp_path: Path) -> None:
    configs_dir = _write_configs(tmp_path)
    manager = ActiveConfigManager(configs_dir / "production.uploader.yaml")
    manager.switch_role("uploader")

    result = manager.reset()

    assert result["deleted"] is True
    assert not (configs_dir / "active.yaml").exists()


def test_status_requires_setup_when_active_yaml_is_missing(tmp_path: Path) -> None:
    configs_dir = _write_configs(tmp_path)
    uploader_path = configs_dir / "production.uploader.yaml"
    uploader_path.write_text(
        uploader_path.read_text(encoding="utf-8").replace("drive-root-id", "real-drive-root-id"),
        encoding="utf-8",
    )
    config = load_config(configs_dir / "production.uploader.yaml")
    manager = ActiveConfigManager(configs_dir / "production.uploader.yaml")

    status = manager.status(config, configs_dir / "production.uploader.yaml")

    assert status["active_exists"] is False
    assert status["setup_required"] is True


def test_switch_role_requires_matching_production_config(tmp_path: Path) -> None:
    configs_dir = _write_configs(tmp_path)
    (configs_dir / "production.downloader.yaml").unlink()
    manager = ActiveConfigManager(configs_dir / "production.uploader.yaml")

    with pytest.raises(ActiveConfigError, match="production config not found"):
        manager.switch_role("downloader")


def test_save_setup_writes_active_yaml_from_selected_template(tmp_path: Path) -> None:
    configs_dir = _write_configs(tmp_path)
    source_root = tmp_path / "field-root"
    source_root.mkdir()
    manager = ActiveConfigManager(configs_dir / "production.uploader.yaml")

    selection = manager.save_setup(
        {
            "role": "uploader",
            "pc_id": "현장 PC 01",
            "drive_root_folder_id": "drive-root-real",
            "machine_id": "성능검사기 1",
            "source_root": str(source_root),
        }
    )

    reloaded = load_config(configs_dir / "active.yaml")
    assert selection.config_path == configs_dir / "active.yaml"
    assert reloaded.role == "uploader"
    assert reloaded.pc_id == "현장_PC_01"
    assert reloaded.machine_id == "성능검사기_1"
    assert reloaded.drive_root_folder_id == "drive-root-real"
    assert reloaded.source_root == source_root
    assert reloaded.group_name == "Array_MIC"


def test_save_setup_rejects_path_unsafe_pc_name(tmp_path: Path) -> None:
    configs_dir = _write_configs(tmp_path)
    manager = ActiveConfigManager(configs_dir / "production.uploader.yaml")

    with pytest.raises(ActiveConfigError, match="unsafe"):
        manager.save_setup(
            {
                "role": "uploader",
                "pc_id": "bad/name",
                "drive_root_folder_id": "drive-root-real",
                "machine_id": "machine-1",
                "source_root": str(tmp_path / "source"),
            }
        )
