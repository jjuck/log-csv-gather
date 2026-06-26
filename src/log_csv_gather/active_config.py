from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from log_csv_gather.config import AppConfig, ConfigError, load_config

ACTIVE_ROLES = {"uploader", "downloader"}
UNSAFE_PATH_CHARS = set('/\\:*?"<>|')
PLACEHOLDER_DRIVE_IDS = {
    "replace-with-google-drive-folder-id",
    "google-drive-folder-id",
    "drive-root-id",
}


class ActiveConfigError(ValueError):
    """Raised when active config selection cannot be changed."""


@dataclass(frozen=True)
class ActiveConfigSelection:
    config: AppConfig
    config_path: Path
    active_path: Path
    role: str

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "config_path": str(self.config_path),
            "active_path": str(self.active_path),
            "active_exists": self.active_path.exists(),
        }


class ActiveConfigManager:
    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path).expanduser().resolve(strict=False)
        self.configs_dir = self.config_path.parent

    @property
    def active_path(self) -> Path:
        return self.configs_dir / "active.yaml"

    def production_path(self, role: str) -> Path:
        self._validate_role(role)
        return self.configs_dir / f"production.{role}.yaml"

    def status(self, config: AppConfig, config_path: str | Path | None = None) -> dict[str, object]:
        current_path = Path(config_path).expanduser().resolve(strict=False) if config_path else self.config_path
        available_roles = [role for role in sorted(ACTIVE_ROLES) if self.production_path(role).exists()]
        return {
            "role": config.role,
            "pc_id": config.pc_id,
            "drive_root_folder_id": config.drive_root_folder_id,
            "machine_id": config.machine_id,
            "source_root": str(config.source_root) if config.source_root else None,
            "download_root": str(config.download_root) if config.download_root else None,
            "config_path": str(current_path),
            "configs_dir": str(self.configs_dir),
            "active_path": str(self.active_path),
            "active_exists": self.active_path.exists(),
            "available_roles": available_roles,
            "setup_required": (not self.active_path.exists()) or self._setup_required(config),
        }

    def switch_role(self, role: str) -> ActiveConfigSelection:
        self._validate_role(role)
        source = self.production_path(role)
        if not source.exists():
            raise ActiveConfigError(f"production config not found for role {role}: {source}")

        self.active_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.active_path.with_name(f"{self.active_path.name}.tmp")
        shutil.copyfile(source, temp_path)
        temp_path.replace(self.active_path)

        config = load_config(self.active_path)
        if config.role != role:
            raise ActiveConfigError(f"selected config role is {config.role}, expected {role}")
        return ActiveConfigSelection(config=config, config_path=self.active_path, active_path=self.active_path, role=role)

    def reset(self) -> dict[str, object]:
        existed = self.active_path.exists()
        if existed:
            self.active_path.unlink()
        return {
            "active_path": str(self.active_path),
            "active_exists": False,
            "deleted": existed,
        }

    def save_setup(self, values: dict[str, Any]) -> ActiveConfigSelection:
        role = str(values.get("role") or "").strip()
        self._validate_role(role)

        raw = self._base_raw_for_role(role)
        pc_id = _sanitize_segment(values.get("pc_id"), "pc_id")
        drive_root_folder_id = _required_text(values.get("drive_root_folder_id"), "drive_root_folder_id")

        raw["role"] = role
        raw["pc_id"] = pc_id
        raw["drive_root_folder_id"] = drive_root_folder_id

        if role == "uploader":
            raw["group_name"] = str(raw.get("group_name") or "Array_MIC")
            raw["machine_id"] = _sanitize_segment(values.get("machine_id") or "성능검사기_1", "machine_id")
            raw["source_root"] = _required_text(values.get("source_root"), "source_root")
            raw.pop("download_root", None)
        else:
            raw["download_root"] = _required_text(values.get("download_root"), "download_root")
            raw.pop("source_root", None)
            raw.pop("machine_id", None)
            raw.pop("group_name", None)

        self.active_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.active_path.with_name(f"{self.active_path.name}.tmp")
        temp_path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        temp_path.replace(self.active_path)

        config = load_config(self.active_path)
        if config.role != role:
            raise ActiveConfigError(f"saved config role is {config.role}, expected {role}")
        return ActiveConfigSelection(config=config, config_path=self.active_path, active_path=self.active_path, role=role)

    @staticmethod
    def _validate_role(role: str) -> None:
        if role not in ACTIVE_ROLES:
            raise ConfigError(f"role must be one of {sorted(ACTIVE_ROLES)}")

    def _base_raw_for_role(self, role: str) -> dict[str, Any]:
        for path in [self.active_path, self.production_path(role), self.config_path]:
            if not path.exists():
                continue
            raw = _read_yaml_mapping(path)
            if str(raw.get("role") or role) == role:
                return dict(raw)
        source = self.production_path(role)
        raise ActiveConfigError(f"production config not found for role {role}: {source}")

    @staticmethod
    def _setup_required(config: AppConfig) -> bool:
        if config.drive_root_folder_id in PLACEHOLDER_DRIVE_IDS:
            return True
        if not config.pc_id:
            return True
        if config.role == "uploader":
            return not config.source_root or not config.machine_id
        if config.role == "downloader":
            return not config.download_root
        return True


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ActiveConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ActiveConfigError(f"config root must be a mapping: {path}")
    return raw


def _required_text(value: Any, name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise ActiveConfigError(f"{name} is required")
    return text


def _sanitize_segment(value: Any, name: str) -> str:
    text = _required_text(value, name)
    if any(char in UNSAFE_PATH_CHARS for char in text):
        raise ActiveConfigError(f"{name} contains unsafe path characters")
    return re.sub(r"\s+", "_", text)
