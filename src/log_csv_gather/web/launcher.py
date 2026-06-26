from __future__ import annotations

import argparse
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import uvicorn

from log_csv_gather.config import AppConfig, load_config
from log_csv_gather.logging_utils import configure_logging
from log_csv_gather.web.app import create_app
from log_csv_gather.web.runtime import (
    ServerInfo,
    find_existing_server,
    record_browser_open,
    select_available_port,
    write_server_info,
)

BrowserOpener = Callable[[str], object]


@dataclass(frozen=True)
class LaunchResult:
    url: str
    reused_existing: bool
    browser_opened: bool


def open_browser_if_enabled(config: AppConfig, url: str, opener: BrowserOpener = webbrowser.open) -> bool:
    if not config.web.open_browser:
        return False
    if not record_browser_open(config, url):
        return False
    opener(url)
    return True


def reuse_existing_server(
    config: AppConfig,
    opener: BrowserOpener = webbrowser.open,
) -> LaunchResult | None:
    existing = find_existing_server(config)
    if existing is None:
        return None
    browser_opened = open_browser_if_enabled(config, existing.url, opener=opener)
    return LaunchResult(existing.url, reused_existing=True, browser_opened=browser_opened)


def start_server(config: AppConfig, config_path: Path, open_browser: bool | None = None) -> ServerInfo:
    port = select_available_port(config.web.host, config.web.preferred_port)
    info = write_server_info(config, port=port, config_path=config_path)
    if open_browser is not False:
        open_browser_if_enabled(config, info.url)
    app = create_app(config, config_path=config_path, port=port)
    uvicorn.run(app, host=config.web.host, port=port, log_level="info")
    return info


def launch(config_path: str | Path, no_browser: bool = False) -> LaunchResult | None:
    path = Path(config_path)
    config = load_config(path)
    if no_browser:
        config = _with_browser_disabled(config)
    configure_logging(config.state_dir)
    existing = reuse_existing_server(config)
    if existing:
        print(f"Using existing local dashboard: {existing.url}")
        return existing
    info = start_server(config, path, open_browser=not no_browser)
    return LaunchResult(info.url, reused_existing=False, browser_opened=not no_browser)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="log-csv-gather-web")
    parser.add_argument("--config", required=True, help="Path to YAML config file")
    parser.add_argument("--no-browser", action="store_true", help="Start without opening a browser")
    args = parser.parse_args(argv)
    launch(args.config, no_browser=args.no_browser)
    return 0


def _with_browser_disabled(config: AppConfig) -> AppConfig:
    from dataclasses import replace

    return replace(config, web=replace(config.web, open_browser=False))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
