import logging
from pathlib import Path

from log_csv_gather.logging_utils import configure_logging


def test_console_logging_is_warning_and_file_logging_is_info(tmp_path: Path) -> None:
    configure_logging(tmp_path / "state")

    root = logging.getLogger()
    levels_by_type = {type(handler).__name__: handler.level for handler in root.handlers}

    assert levels_by_type["FileHandler"] == logging.INFO
    assert levels_by_type["StreamHandler"] == logging.WARNING


def test_google_oauth_info_logs_are_not_written_to_app_log(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    configure_logging(state_dir)

    logging.getLogger("google_auth_oauthlib.flow").info("Please visit this URL with auth details")
    logging.getLogger("log_csv_gather.test").info("normal application event")
    for handler in logging.getLogger().handlers:
        handler.flush()

    log_text = (state_dir / "logs" / "app.log").read_text(encoding="utf-8")
    assert "normal application event" in log_text
    assert "Please visit this URL" not in log_text


def test_korean_paths_are_written_to_app_log_as_utf8(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    configure_logging(state_dir)

    logging.getLogger("log_csv_gather.test").info(r"업로드 완료 C:\검사자료\성능검사기_1\260401_PAS.csv")
    for handler in logging.getLogger().handlers:
        handler.flush()

    log_text = (state_dir / "logs" / "app.log").read_text(encoding="utf-8")
    assert "업로드 완료" in log_text
    assert "성능검사기_1" in log_text
