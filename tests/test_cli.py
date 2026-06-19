from pathlib import Path

from log_csv_gather.cli import build_parser
from log_csv_gather.cli import main


def test_parser_accepts_doctor_command() -> None:
    args = build_parser().parse_args(["doctor", "--config", "config.yaml"])

    assert args.command == "doctor"


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
