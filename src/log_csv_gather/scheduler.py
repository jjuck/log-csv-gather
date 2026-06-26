from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from log_csv_gather.config import AppConfig

MAX_INTERVAL_MINUTES = 1439


class SchedulerError(RuntimeError):
    """Raised when a Windows scheduled task operation fails."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class TaskCommand:
    executable: Path
    arguments: list[str]

    def as_task_run(self) -> str:
        return " ".join([_quote_cmd_part(str(self.executable)), *[_quote_cmd_part(arg) for arg in self.arguments]])


@dataclass(frozen=True)
class ScheduledTaskStatus:
    supported: bool
    task_name: str
    configured_enabled: bool
    configured_interval_minutes: int
    registered: bool
    enabled: bool | None = None
    state: str | None = None
    interval_minutes: int | None = None
    command: str | None = None
    last_run_time: str | None = None
    next_run_time: str | None = None
    last_task_result: int | str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "supported": self.supported,
            "task_name": self.task_name,
            "configured_enabled": self.configured_enabled,
            "configured_interval_minutes": self.configured_interval_minutes,
            "registered": self.registered,
            "enabled": self.enabled,
            "state": self.state,
            "interval_minutes": self.interval_minutes,
            "command": self.command,
            "last_run_time": self.last_run_time,
            "next_run_time": self.next_run_time,
            "last_task_result": self.last_task_result,
            "error": self.error,
        }


Runner = Callable[[Sequence[str]], CommandResult]


def default_task_name(config: AppConfig) -> str:
    if config.scheduler.task_name:
        return config.scheduler.task_name
    return f"LogCsvGather-{config.pc_id}-{config.role}"


def build_task_command(config: AppConfig, config_path: str | Path, app_dir: str | Path | None = None) -> TaskCommand:
    root = Path(app_dir) if app_dir is not None else Path.cwd()
    run_bat = root / "run.bat"
    action = "upload-once" if config.role == "uploader" else "download-once"
    resolved_config_path = str(Path(config_path).resolve())
    if run_bat.exists():
        return TaskCommand(executable=run_bat.resolve(), arguments=[action, resolved_config_path])

    cli_command = "upload" if config.role == "uploader" else "download"
    return TaskCommand(
        executable=Path(sys.executable).resolve(),
        arguments=["-m", "log_csv_gather", cli_command, "--config", resolved_config_path],
    )


class WindowsTaskScheduler:
    def __init__(
        self,
        config: AppConfig,
        *,
        config_path: str | Path,
        app_dir: str | Path | None = None,
        runner: Runner | None = None,
        is_windows: bool | None = None,
    ) -> None:
        self.config = config
        self.config_path = Path(config_path)
        self.app_dir = Path(app_dir) if app_dir is not None else Path.cwd()
        self.runner = runner or _default_runner
        self.is_windows = (os.name == "nt") if is_windows is None else is_windows

    def status(self) -> ScheduledTaskStatus:
        if not self.is_windows:
            return self._base_status(
                registered=False,
                supported=False,
                error="Windows Task Scheduler is only available on Windows.",
            )
        result = self.runner(_powershell_args(_query_script(default_task_name(self.config))))
        if result.returncode != 0:
            return self._base_status(registered=False, error=_command_error(result))
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            return self._base_status(registered=False, error=f"failed to parse scheduler status: {exc}")
        if not payload.get("Registered"):
            return self._base_status(registered=False)

        command = " ".join(
            part
            for part in [str(payload.get("Command") or "").strip(), str(payload.get("Arguments") or "").strip()]
            if part
        )
        return self._base_status(
            registered=True,
            enabled=_bool_or_none(payload.get("Enabled")),
            state=_optional_str(payload.get("State")),
            interval_minutes=_parse_interval_minutes(payload.get("RepetitionInterval")),
            command=command or None,
            last_run_time=_optional_str(payload.get("LastRunTime")),
            next_run_time=_optional_str(payload.get("NextRunTime")),
            last_task_result=payload.get("LastTaskResult"),
        )

    def register_or_update(
        self,
        *,
        interval_minutes: int | None = None,
        enabled: bool | None = None,
    ) -> ScheduledTaskStatus:
        if not self.is_windows:
            raise SchedulerError("Windows Task Scheduler is only available on Windows.")
        interval = interval_minutes if interval_minutes is not None else self.config.scheduler.interval_minutes
        _validate_interval(interval)
        should_enable = self.config.scheduler.enabled if enabled is None else enabled
        command = build_task_command(self.config, self.config_path, self.app_dir).as_task_run()
        result = self.runner(
            [
                "schtasks.exe",
                "/Create",
                "/TN",
                default_task_name(self.config),
                "/SC",
                "MINUTE",
                "/MO",
                str(interval),
                "/TR",
                command,
                "/ST",
                "00:00",
                "/F",
            ]
        )
        self._raise_on_error(result, "failed to register scheduled task")
        if not should_enable:
            self._change_enabled(False)
        return self.status()

    def unregister(self) -> ScheduledTaskStatus:
        if not self.is_windows:
            raise SchedulerError("Windows Task Scheduler is only available on Windows.")
        current = self.status()
        if not current.registered:
            return current
        result = self.runner(["schtasks.exe", "/Delete", "/TN", default_task_name(self.config), "/F"])
        self._raise_on_error(result, "failed to unregister scheduled task")
        return self.status()

    def set_enabled(self, enabled: bool) -> ScheduledTaskStatus:
        if not self.is_windows:
            raise SchedulerError("Windows Task Scheduler is only available on Windows.")
        self._change_enabled(enabled)
        return self.status()

    def _change_enabled(self, enabled: bool) -> None:
        flag = "/Enable" if enabled else "/Disable"
        result = self.runner(["schtasks.exe", "/Change", "/TN", default_task_name(self.config), flag])
        self._raise_on_error(result, f"failed to {'enable' if enabled else 'disable'} scheduled task")

    def _base_status(self, **overrides: object) -> ScheduledTaskStatus:
        values = {
            "supported": True,
            "task_name": default_task_name(self.config),
            "configured_enabled": self.config.scheduler.enabled,
            "configured_interval_minutes": self.config.scheduler.interval_minutes,
            "registered": False,
        }
        values.update(overrides)
        return ScheduledTaskStatus(**values)

    @staticmethod
    def _raise_on_error(result: CommandResult, message: str) -> None:
        if result.returncode != 0:
            raise SchedulerError(f"{message}: {_command_error(result)}")


def _default_runner(argv: Sequence[str]) -> CommandResult:
    completed = subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _powershell_args(script: str) -> list[str]:
    return [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        "[Console]::OutputEncoding = [Text.UTF8Encoding]::new(); " + script,
    ]


def _query_script(task_name: str) -> str:
    name = _ps_literal(task_name)
    return f"""
$taskName = {name}
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -eq $task) {{
  [pscustomobject]@{{ Registered = $false }} | ConvertTo-Json -Compress
  exit 0
}}
$info = Get-ScheduledTaskInfo -TaskName $taskName
$action = @($task.Actions)[0]
$trigger = @($task.Triggers)[0]
$interval = $null
if ($null -ne $trigger -and $null -ne $trigger.Repetition) {{
  $interval = [string]$trigger.Repetition.Interval
}}
function Format-Date($value) {{
  if ($null -eq $value -or $value -eq [datetime]::MinValue) {{ return $null }}
  return $value.ToString("o")
}}
[pscustomobject]@{{
  Registered = $true
  TaskName = $task.TaskName
  State = [string]$task.State
  Enabled = ([string]$task.State -ne "Disabled")
  LastRunTime = Format-Date $info.LastRunTime
  NextRunTime = Format-Date $info.NextRunTime
  LastTaskResult = $info.LastTaskResult
  Command = $action.Execute
  Arguments = $action.Arguments
  WorkingDirectory = $action.WorkingDirectory
  RepetitionInterval = $interval
}} | ConvertTo-Json -Compress
"""


def _validate_interval(interval_minutes: int) -> None:
    if interval_minutes < 1 or interval_minutes > MAX_INTERVAL_MINUTES:
        raise SchedulerError(f"interval_minutes must be between 1 and {MAX_INTERVAL_MINUTES}")


def _parse_interval_minutes(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("PT"):
        remaining = text[2:]
        total = 0
        number = ""
        for char in remaining:
            if char.isdigit():
                number += char
                continue
            if not number:
                return None
            amount = int(number)
            number = ""
            if char == "H":
                total += amount * 60
            elif char == "M":
                total += amount
            elif char == "S":
                total += 1 if amount else 0
            else:
                return None
        return total or None
    if ":" in text:
        day_part = "0"
        time_part = text
        if "." in text:
            day_part, time_part = text.split(".", 1)
        pieces = time_part.split(":")
        if len(pieces) != 3:
            return None
        days = int(day_part)
        hours, minutes, seconds = [int(float(piece)) for piece in pieces]
        return days * 24 * 60 + hours * 60 + minutes + (1 if seconds else 0)
    try:
        return int(text)
    except ValueError:
        return None


def _quote_cmd_part(value: str) -> str:
    escaped = value.replace('"', r'\"')
    if value.lower().endswith((".bat", ".cmd", ".exe")) or any(char.isspace() for char in value) or "\\" in value:
        return f'"{escaped}"'
    return escaped


def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _bool_or_none(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"true", "1", "yes"}


def _command_error(result: CommandResult) -> str:
    return (result.stderr or result.stdout or f"exit code {result.returncode}").strip()
