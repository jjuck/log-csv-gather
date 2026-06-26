from __future__ import annotations

import json
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

from log_csv_gather.config import AppConfig
def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        role="downloader",
        pc_id="management-pc-01",
        drive_root_folder_id="drive-root-id",
        state_dir=tmp_path / "state",
        download_root=tmp_path / "downloads",
    )


def test_select_available_port_skips_occupied_preferred_port(tmp_path: Path) -> None:
    from log_csv_gather.web.runtime import select_available_port

    config = _config(tmp_path)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((config.web.host, 0))
        occupied_port = sock.getsockname()[1]

        selected = select_available_port(config.web.host, occupied_port, max_attempts=2)

    assert selected == occupied_port + 1


def test_server_info_is_written_under_state_web_runtime(tmp_path: Path) -> None:
    from log_csv_gather.web.runtime import server_json_path, write_server_info

    config = _config(tmp_path)

    info = write_server_info(config, port=8765, config_path=tmp_path / "config.yaml")

    payload = json.loads(server_json_path(config).read_text(encoding="utf-8"))
    assert info.url == "http://127.0.0.1:8765"
    assert payload["pc_id"] == "management-pc-01"
    assert payload["role"] == "downloader"
    assert payload["port"] == 8765


def test_find_existing_server_returns_live_matching_server(tmp_path: Path) -> None:
    from log_csv_gather.web.runtime import find_existing_server, write_server_info

    config = _config(tmp_path)
    info = write_server_info(config, port=8765, config_path=tmp_path / "config.yaml")

    existing = find_existing_server(config, health_check=lambda item: {"ok": True, "pc_id": item.pc_id, "role": item.role})

    assert existing == info


def test_find_existing_server_ignores_stale_runtime_file(tmp_path: Path) -> None:
    from log_csv_gather.web.runtime import find_existing_server, server_json_path, write_server_info

    config = _config(tmp_path)
    write_server_info(config, port=8765, config_path=tmp_path / "config.yaml")

    existing = find_existing_server(config, health_check=lambda item: None)

    assert existing is None
    assert not server_json_path(config).exists()


def test_browser_open_is_throttled_per_url(tmp_path: Path) -> None:
    from log_csv_gather.web.runtime import record_browser_open

    config = _config(tmp_path)
    now = datetime(2026, 6, 19, 10, 0, tzinfo=timezone.utc)
    url = "http://127.0.0.1:8765"

    first = record_browser_open(config, url, now=now)
    second = record_browser_open(config, url, now=now + timedelta(seconds=1))
    third = record_browser_open(config, url, now=now + timedelta(seconds=4))

    assert first is True
    assert second is False
    assert third is True


def test_server_info_round_trips() -> None:
    from log_csv_gather.web.runtime import ServerInfo

    info = ServerInfo(
        pid=123,
        host="127.0.0.1",
        port=8765,
        url="http://127.0.0.1:8765",
        started_at="2026-06-19T10:00:00+09:00",
        config_path="config.yaml",
        pc_id="pc-1",
        role="uploader",
    )

    assert ServerInfo.from_dict(info.to_dict()) == info
