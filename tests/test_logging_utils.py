import logging
from pathlib import Path

from log_csv_gather.logging_utils import configure_logging


def test_console_logging_is_warning_and_file_logging_is_info(tmp_path: Path) -> None:
    configure_logging(tmp_path / "state")

    root = logging.getLogger()
    levels_by_type = {type(handler).__name__: handler.level for handler in root.handlers}

    assert levels_by_type["FileHandler"] == logging.INFO
    assert levels_by_type["StreamHandler"] == logging.WARNING
