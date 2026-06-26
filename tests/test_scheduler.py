from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from log_csv_gather.config import AppConfig
from log_csv_gather.scheduler import CommandResult, WindowsTaskScheduler, build_task_command, default_task_name


class FakeSchedulerRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.registered = False
        self.enabled = False
        self.interval_minutes: int | None = None
        self.command = ""
        self.arguments = ""

    def __call__(self, argv: Sequence[str]) -> CommandResult:
        self.calls.append(list(argv))
        if argv[0].lower().startswith("powershell"):
            return CommandResult(0, json.dumps(self._query_payload()), "")
        if "/Create" in argv:
            self.registered = True
            self.enabled = True
            self.interval_minutes = int(argv[argv.index("/MO") + 1])
            task_run = argv[argv.index("/TR") + 1]
            self.command, _, self.arguments = task_run.partition(" ")
            self.command = self.command.strip('"')
            return CommandResult(0, "created", "")
        if "/Delete" in argv:
            self.registered = False
            self.enabled = False
            return CommandResult(0, "deleted", "")
        if "/Disable" in argv:
            self.enabled = False
            return CommandResult(0, "disabled", "")
        if "/Enable" in argv:
            self.enabled = True
            return CommandResult(0, "enabled", "")
        return CommandResult(0, "ok", "")

    def _query_payload(self) -> dict[str, object]:
        if not self.registered:
            return {"Registered": False}
        return {
            "Registered": True,
            "TaskName": "LogCsvGather-field-pc-01-uploader",
            "State": "Ready" if self.enabled else "Disabled",
            "Enabled": self.enabled,
            "LastRunTime": None,
            "NextRunTime": "2026-06-19T11:00:00+09:00",
            "LastTaskResult": 0,
            "Command": self.command,
            "Arguments": self.arguments,
            "WorkingDirectory": None,
            "RepetitionInterval": f"PT{self.interval_minutes}M",
        }


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


def test_default_task_name_is_stable_per_pc_and_role(tmp_path: Path) -> None:
    assert default_task_name(_config(tmp_path)) == "LogCsvGather-field-pc-01-uploader"


def test_build_task_command_prefers_portable_run_bat(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "run.bat").write_text("@echo off\n", encoding="utf-8")
    config_path = tmp_path / "configs" / "production.uploader.yaml"
    config_path.parent.mkdir()
    config_path.write_text("role: uploader\n", encoding="utf-8")

    command = build_task_command(_config(tmp_path), config_path=config_path, app_dir=app_dir)

    assert command.executable == app_dir / "run.bat"
    assert command.arguments == ["upload-once", str(config_path)]
    assert command.as_task_run() == f'"{app_dir / "run.bat"}" upload-once "{config_path}"'


def test_register_update_disable_and_unregister_task(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "run.bat").write_text("@echo off\n", encoding="utf-8")
    runner = FakeSchedulerRunner()
    scheduler = WindowsTaskScheduler(
        _config(tmp_path),
        config_path=tmp_path / "config.yaml",
        app_dir=app_dir,
        runner=runner,
        is_windows=True,
    )

    registered = scheduler.register_or_update(interval_minutes=60, enabled=False)
    enabled = scheduler.set_enabled(True)
    removed = scheduler.unregister()

    create_call = next(call for call in runner.calls if "/Create" in call)
    assert create_call[create_call.index("/SC") + 1] == "MINUTE"
    assert create_call[create_call.index("/MO") + 1] == "60"
    assert '"upload-once"' not in create_call[create_call.index("/TR") + 1]
    assert "upload-once" in create_call[create_call.index("/TR") + 1]
    assert registered.registered is True
    assert registered.enabled is False
    assert registered.interval_minutes == 60
    assert enabled.enabled is True
    assert removed.registered is False


def test_status_parses_timespan_repetition_interval(tmp_path: Path) -> None:
    runner = FakeSchedulerRunner()
    runner.registered = True
    runner.enabled = True
    runner.interval_minutes = 90
    runner.command = r"C:\app\run.bat"
    runner.arguments = r"upload-once C:\app\config.yaml"
    scheduler = WindowsTaskScheduler(
        _config(tmp_path),
        config_path=tmp_path / "config.yaml",
        runner=runner,
        is_windows=True,
    )

    status = scheduler.status()

    assert status.registered is True
    assert status.enabled is True
    assert status.interval_minutes == 90
    assert status.command == r"C:\app\run.bat upload-once C:\app\config.yaml"
