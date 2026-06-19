from __future__ import annotations

from log_csv_gather.config import AppConfig
from log_csv_gather.state import StateRepository


def render_status(config: AppConfig, repo: StateRepository) -> str:
    uploads = repo.count_by_status("uploads")
    downloads = repo.count_by_status("downloads")
    return "\n".join(
        [
            f"pc_id: {config.pc_id}",
            f"role: {config.role}",
            f"state_db: {repo.db_path}",
            f"uploads: {_format_counts(uploads)}",
            f"downloads: {_format_counts(downloads)}",
        ]
    )


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
