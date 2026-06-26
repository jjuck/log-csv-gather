from pathlib import Path

from log_csv_gather.cli import build_parser
from log_csv_gather.cli import progress_line
from log_csv_gather.cli import main
from log_csv_gather.state import StateRepository, UploadRecord


def test_parser_accepts_doctor_command() -> None:
    args = build_parser().parse_args(["doctor", "--config", "config.yaml"])

    assert args.command == "doctor"


def test_parser_accepts_upload_dry_run() -> None:
    args = build_parser().parse_args(["upload", "--config", "config.yaml", "--dry-run"])

    assert args.command == "upload"
    assert args.dry_run is True


def test_parser_accepts_web_command_with_browser_flags() -> None:
    args = build_parser().parse_args(["web", "--config", "config.yaml", "--no-browser"])

    assert args.command == "web"
    assert args.no_browser is True


def test_progress_line_formats_structured_progress_for_console() -> None:
    line = progress_line(
        {
            "phase": "upload",
            "current": 1,
            "total": 2,
            "success": 1,
            "skipped": 0,
            "failed": 0,
            "conflict": 0,
            "message": "upload: 1/2 processed, success=1 skipped=0 failed=0 conflict=0",
        }
    )

    assert line == "upload: 1/2 processed, success=1 skipped=0 failed=0 conflict=0"


def test_status_command_prints_local_state_counts(tmp_path: Path, capsys) -> None:
    config_file = tmp_path / "downloader.yaml"
    config_file.write_text(
        f"""
role: downloader
pc_id: management-pc-01
drive_root_folder_id: drive-root-id
download_root: "{(tmp_path / "downloads").as_posix()}"
state_dir: "{(tmp_path / "state").as_posix()}"
""",
        encoding="utf-8",
    )

    exit_code = main(["status", "--config", str(config_file)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "pc_id: management-pc-01" in output
    assert "downloads:" in output


def test_status_details_prints_conflict_paths(tmp_path: Path, capsys) -> None:
    config_file = tmp_path / "uploader.yaml"
    state_dir = tmp_path / "state"
    source_root = tmp_path / "source"
    source_root.mkdir()
    config_file.write_text(
        f"""
role: uploader
pc_id: field-pc-01
drive_root_folder_id: drive-root-id
source_root: "{source_root.as_posix()}"
state_dir: "{state_dir.as_posix()}"
group_name: Array_MIC
machine_id: machine-1
""",
        encoding="utf-8",
    )
    repo = StateRepository(state_dir / "state.sqlite")
    repo.upsert_upload(
        UploadRecord(
            source_path="E:/PAS Test data/20260401/20260401_summary.csv",
            drive_file_id="drive-1",
            drive_path="logs/Array_MIC/PAS/machine-1/260401/260401_PAS.csv",
            group_name="Array_MIC",
            log_type="PAS",
            machine_id="machine-1",
            source_date_yyyymmdd="20260401",
            target_date_yymmdd="260401",
            source_size=10,
            source_mtime=1.0,
            content_hash="hash",
            status="conflict",
            last_error="different content",
        )
    )

    exit_code = main(["status", "--config", str(config_file), "--details"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "upload_conflicts:" in output
    assert "logs/Array_MIC/PAS/machine-1/260401/260401_PAS.csv" in output
    assert "different content" in output
