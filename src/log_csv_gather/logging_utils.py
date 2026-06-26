from __future__ import annotations

import logging
import sys
from pathlib import Path


NOISY_EXTERNAL_LOGGERS = (
    "google_auth_oauthlib",
    "google_auth_oauthlib.flow",
    "googleapiclient.discovery_cache",
)


def configure_logging(state_dir: Path) -> None:
    log_dir = state_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_dir / "app.log", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    stream_handler = logging.StreamHandler(sys.__stderr__)
    stream_handler.setLevel(logging.WARNING)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[file_handler, stream_handler],
        force=True,
    )
    for logger_name in NOISY_EXTERNAL_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
