from __future__ import annotations

import argparse
import sys

from log_csv_gather.config import ConfigError, load_config
from log_csv_gather.drive import GoogleDriveAdapter
from log_csv_gather.logging_utils import configure_logging
from log_csv_gather.state import StateRepository
from log_csv_gather.status import render_status
from log_csv_gather.workflows import run_doctor, run_download, run_upload


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        configure_logging(config.state_dir)
        repo = StateRepository(config.state_dir / "state.sqlite")
        if args.command == "status":
            print(render_status(config, repo))
            return 0
        drive = GoogleDriveAdapter.from_config(config)
        if args.command == "auth":
            print("Google Drive authentication succeeded.")
            return 0
        if args.command == "doctor":
            result = run_doctor(config, drive)
            print(result.as_line())
            return 0 if result.ok else 2
        if args.command == "upload":
            summary = run_upload(config, drive, repo, progress_callback=print, progress_every=config.progress_every)
            print(_summary_line("upload", summary))
            return 0 if summary.failed_count == 0 and summary.conflict_count == 0 else 2
        if args.command == "download":
            summary = run_download(config, drive, repo, progress_callback=print, progress_every=config.progress_every)
            print(_summary_line("download", summary))
            return 0 if summary.failed_count == 0 and summary.conflict_count == 0 else 2
        parser.error(f"unknown command: {args.command}")
    except (ConfigError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="log-csv-gather")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ["auth", "upload", "download", "status", "doctor"]:
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--config", required=True, help="Path to YAML config file")
    return parser


def _summary_line(name: str, summary: object) -> str:
    return (
        f"{name}: processed={summary.processed_count} success={summary.success_count} "
        f"skipped={summary.skipped_count} failed={summary.failed_count} "
        f"conflict={summary.conflict_count}"
    )
