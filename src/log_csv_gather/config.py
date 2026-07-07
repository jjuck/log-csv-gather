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
    "SMIC_Test data": "SMIC",
}

VALID_ROLES = {"uploader", "downloader"}


class ConfigError(ValueError):
    """Raised when a config file is missing required values."""


@dataclass(frozen=True)
class WebConfig:
    host: str = "127.0.0.1"
    preferred_port: int = 8765
    open_browser: bool = True
    browser_open_throttle_seconds: int = 3

    def validate(self) -> None:
        if self.host != "127.0.0.1":
            raise ConfigError("web.host must be 127.0.0.1 for the local-only dashboard")
        if not 1 <= self.preferred_port <= 65535:
            raise ConfigError("web.preferred_port must be between 1 and 65535")
        if self.browser_open_throttle_seconds < 0:
            raise ConfigError("web.browser_open_throttle_seconds must be 0 or greater")


@dataclass(frozen=True)
class SchedulerConfig:
    enabled: bool = True
    interval_minutes: int = 60
    task_name: str | None = None

    def validate(self) -> None:
        if self.interval_minutes <= 0:
            raise ConfigError("scheduler.interval_minutes must be greater than 0")


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
    drive_timeout_seconds: int = 60
    drive_num_retries: int = 3
    log_type_mappings: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_LOG_TYPE_MAPPINGS))
    ignore_dirs: list[str] = field(default_factory=lambda: ["fail"])
    include_groups: list[str] = field(default_factory=list)
    include_log_types: list[str] = field(default_factory=list)
    include_machines: list[str] = field(default_factory=list)
    credentials_file: Path | None = None
    token_file: Path | None = None
    service_account_file: Path | None = None
    web: WebConfig = field(default_factory=WebConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)

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
        if self.drive_timeout_seconds <= 0:
            raise ConfigError("drive_timeout_seconds must be greater than 0")
        if self.drive_num_retries < 0:
            raise ConfigError("drive_num_retries must be 0 or greater")
        if self.role == "uploader":
            if not self.source_root:
                raise ConfigError("source_root is required for uploader")
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
        self.web.validate()
        self.scheduler.validate()


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).expanduser().resolve(strict=False)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")
    return _build_config(raw, config_path)


def update_scheduler_settings(
    path: str | Path,
    *,
    enabled: bool | None = None,
    interval_minutes: int | None = None,
    task_name: str | None = None,
) -> AppConfig:
    config_path = Path(path).expanduser().resolve(strict=False)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")

    scheduler_section = dict(_section(raw.get("scheduler"), "scheduler"))
    if enabled is not None:
        scheduler_section["enabled"] = bool(enabled)
    if interval_minutes is not None:
        scheduler_section["interval_minutes"] = int(interval_minutes)
    if task_name is not None:
        scheduler_section["task_name"] = task_name if task_name else None
    raw["scheduler"] = scheduler_section

    updated = _build_config(raw, config_path)
    temp_path = config_path.with_name(f"{config_path.name}.tmp")
    temp_path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    temp_path.replace(config_path)
    return updated


def _build_config(raw: dict[str, Any], config_path: Path) -> AppConfig:
    base_dir = config_path.parent
    web_section = _section(raw.get("web"), "web")
    scheduler_section = _section(raw.get("scheduler"), "scheduler")

    config = AppConfig(
        role=_required_str(raw, "role"),
        pc_id=_required_str(raw, "pc_id"),
        drive_root_folder_id=_required_str(raw, "drive_root_folder_id"),
        state_dir=_path(raw.get("state_dir"), "state_dir", base_dir),
        source_root=_optional_path(raw.get("source_root"), base_dir),
        download_root=_optional_path(raw.get("download_root"), base_dir),
        group_name=_optional_str(raw.get("group_name")),
        machine_id=_optional_str(raw.get("machine_id")),
        file_stable_minutes=int(raw.get("file_stable_minutes", 5)),
        progress_every=int(raw.get("progress_every", 10)),
        drive_timeout_seconds=int(raw.get("drive_timeout_seconds", 60)),
        drive_num_retries=int(raw.get("drive_num_retries", 3)),
        log_type_mappings=_log_type_mappings(raw.get("log_type_mappings")),
        ignore_dirs=_string_list(raw.get("ignore_dirs")) or ["fail"],
        include_groups=_string_list(raw.get("include_groups")),
        include_log_types=_string_list(raw.get("include_log_types")),
        include_machines=_string_list(raw.get("include_machines")),
        credentials_file=_optional_path(raw.get("credentials_file"), base_dir),
        token_file=_optional_path(raw.get("token_file"), base_dir),
        service_account_file=_optional_path(raw.get("service_account_file"), base_dir),
        web=WebConfig(
            host=str(web_section.get("host", "127.0.0.1")),
            preferred_port=int(web_section.get("preferred_port", 8765)),
            open_browser=bool(web_section.get("open_browser", True)),
            browser_open_throttle_seconds=int(web_section.get("browser_open_throttle_seconds", 3)),
        ),
        scheduler=SchedulerConfig(
            enabled=bool(scheduler_section.get("enabled", True)),
            interval_minutes=int(scheduler_section.get("interval_minutes", 60)),
            task_name=_optional_str(scheduler_section.get("task_name")),
        ),
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


def _path(value: Any, key: str, base_dir: Path) -> Path:
    if value is None or str(value).strip() == "":
        raise ConfigError(f"{key} is required")
    return _resolve_path(str(value), base_dir)


def _optional_path(value: Any, base_dir: Path) -> Path | None:
    if value is None or str(value).strip() == "":
        return None
    return _resolve_path(str(value), base_dir)


def _resolve_path(value: str, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve(strict=False)


def _section(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a mapping")
    return value


def _string_dict(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError("log_type_mappings must be a mapping")
    return {str(key): str(item) for key, item in value.items()}


def _log_type_mappings(value: Any) -> dict[str, str]:
    mappings = dict(DEFAULT_LOG_TYPE_MAPPINGS)
    mappings.update(_string_dict(value))
    return mappings


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError("list config values must be arrays")
    return [str(item) for item in value]
