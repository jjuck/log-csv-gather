from __future__ import annotations

import json
import os
import socket
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import urlopen

from log_csv_gather.config import AppConfig

HealthCheck = Callable[["ServerInfo"], dict[str, Any] | None]


@dataclass(frozen=True)
class ServerInfo:
    pid: int
    host: str
    port: int
    url: str
    started_at: str
    config_path: str
    pc_id: str
    role: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ServerInfo":
        return cls(
            pid=int(data["pid"]),
            host=str(data["host"]),
            port=int(data["port"]),
            url=str(data["url"]),
            started_at=str(data["started_at"]),
            config_path=str(data["config_path"]),
            pc_id=str(data["pc_id"]),
            role=str(data["role"]),
        )


def runtime_web_dir(config: AppConfig) -> Path:
    return config.state_dir / "web"


def server_json_path(config: AppConfig) -> Path:
    return runtime_web_dir(config) / "server.json"


def browser_opened_json_path(config: AppConfig) -> Path:
    return runtime_web_dir(config) / "browser-opened.json"


def build_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def select_available_port(host: str, preferred_port: int, max_attempts: int = 20) -> int:
    for port in range(preferred_port, preferred_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"no available local port starting at {preferred_port}")


def write_server_info(config: AppConfig, port: int, config_path: Path) -> ServerInfo:
    info = ServerInfo(
        pid=os.getpid(),
        host=config.web.host,
        port=port,
        url=build_url(config.web.host, port),
        started_at=datetime.now(timezone.utc).isoformat(),
        config_path=str(config_path),
        pc_id=config.pc_id,
        role=config.role,
    )
    path = server_json_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return info


def load_server_info(config: AppConfig) -> ServerInfo | None:
    path = server_json_path(config)
    if not path.exists():
        return None
    try:
        return ServerInfo.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return None


def find_existing_server(config: AppConfig, health_check: HealthCheck | None = None) -> ServerInfo | None:
    info = load_server_info(config)
    if info is None:
        return None
    check = health_check or request_health
    payload = check(info)
    if payload and payload.get("ok") is True and payload.get("pc_id") == config.pc_id and payload.get("role") == config.role:
        return info
    server_json_path(config).unlink(missing_ok=True)
    return None


def request_health(info: ServerInfo, timeout_seconds: float = 1.0) -> dict[str, Any] | None:
    try:
        with urlopen(f"{info.url}/api/health", timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8")
    except (OSError, URLError):
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def record_browser_open(config: AppConfig, url: str, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    path = browser_opened_json_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            previous_url = previous.get("url")
            previous_time = datetime.fromisoformat(str(previous.get("last_opened_at")))
            elapsed = (now - previous_time).total_seconds()
            if previous_url == url and elapsed < config.web.browser_open_throttle_seconds:
                return False
        except (ValueError, TypeError, json.JSONDecodeError):
            pass
    path.write_text(
        json.dumps({"url": url, "last_opened_at": now.isoformat()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return True
