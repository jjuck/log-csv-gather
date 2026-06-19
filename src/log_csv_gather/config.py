from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


DEFAULT_LOG_TYPE_MAPPINGS = {
    "PAS Test data": "PAS",
    "HM-3203-011 Test data": "3203",
    "HM-3903-011 Test data": "3903",
    "LITE Test data": "LITE",
}

VALID_ROLES = {"uploader", "downloader"}


class ConfigError(ValueError):
    """Raised when a config file is missing required values."""


@dataclass(frozen=True)
class AppConfig:
    role: str
    pc_id: str
    drive_root_folder_id: str
    state_dir: Path
    source_root: Path | None = None
    download_root: Path | None = None
    group_name: str | None = None
    machine_id: str | None = None
    file_stable_minutes: int = 5
    progress_every: int = 10
    log_type_mappings: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_LOG_TYPE_MAPPINGS))
    ignore_dirs: list[str] = field(default_factory=lambda: ["fail"])
    include_groups: list[str] = field(default_factory=list)
    include_log_types: list[str] = field(default_factory=list)
    include_machines: list[str] = field(default_factory=list)
    credentials_file: Path | None = None
    token_file: Path | None = None
    service_account_file: Path | None = None

    def validate(self) -> None:
        if self.role not in VALID_ROLES:
            raise ConfigError(f"role must be one of {sorted(VALID_ROLES)}")
        if not self.pc_id:
            raise ConfigError("pc_id is required")
        if not self.drive_root_folder_id:
            raise ConfigError("drive_root_folder_id is required")
        if self.file_stable_minutes < 0:
            raise ConfigError("file_stable_minutes must be 0 or greater")
        if self.progress_every < 0:
            raise ConfigError("progress_every must be 0 or greater")
        if self.role == "uploader":
            if not self.source_root:
                raise ConfigError("source_root is required for uploader")
            if not self.source_root.exists():
                raise ConfigError(f"source_root does not exist: {self.source_root}")
            if not self.group_name:
                raise ConfigError("group_name is required for uploader")
            if not self.machine_id:
                raise ConfigError("machine_id is required for uploader")
            if not self.log_type_mappings:
                raise ConfigError("log_type_mappings must not be empty for uploader")
        if self.role == "downloader" and not self.download_root:
            raise ConfigError("download_root is required for downloader")
        if self.service_account_file and not self.service_account_file.exists():
            raise ConfigError(f"service_account_file does not exist: {self.service_account_file}")


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")

    config = AppConfig(
        role=_required_str(raw, "role"),
        pc_id=_required_str(raw, "pc_id"),
        drive_root_folder_id=_required_str(raw, "drive_root_folder_id"),
        state_dir=_path(raw.get("state_dir"), "state_dir"),
        source_root=_optional_path(raw.get("source_root")),
        download_root=_optional_path(raw.get("download_root")),
        group_name=_optional_str(raw.get("group_name")),
        machine_id=_optional_str(raw.get("machine_id")),
        file_stable_minutes=int(raw.get("file_stable_minutes", 5)),
        progress_every=int(raw.get("progress_every", 10)),
        log_type_mappings=_string_dict(raw.get("log_type_mappings")) or dict(DEFAULT_LOG_TYPE_MAPPINGS),
        ignore_dirs=_string_list(raw.get("ignore_dirs")) or ["fail"],
        include_groups=_string_list(raw.get("include_groups")),
        include_log_types=_string_list(raw.get("include_log_types")),
        include_machines=_string_list(raw.get("include_machines")),
        credentials_file=_optional_path(raw.get("credentials_file")),
        token_file=_optional_path(raw.get("token_file")),
        service_account_file=_optional_path(raw.get("service_account_file")),
    )
    config.validate()
    return config


def _required_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if value is None or str(value).strip() == "":
        raise ConfigError(f"{key} is required")
    return str(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _path(value: Any, key: str) -> Path:
    if value is None or str(value).strip() == "":
        raise ConfigError(f"{key} is required")
    return Path(str(value))


def _optional_path(value: Any) -> Path | None:
    if value is None or str(value).strip() == "":
        return None
    return Path(str(value))


def _string_dict(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError("log_type_mappings must be a mapping")
    return {str(key): str(item) for key, item in value.items()}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError("list config values must be arrays")
    return [str(item) for item in value]
