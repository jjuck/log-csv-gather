from __future__ import annotations

import itertools
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

ProgressEvent = dict[str, Any]
ProgressInput = str | ProgressEvent
ProgressCallback = Callable[[ProgressInput], None]
JobCallable = Callable[[ProgressCallback], dict[str, Any]]
JobCompletionCallback = Callable[["Job"], None]


class JobAlreadyRunning(RuntimeError):
    def __init__(self, action: str, existing_job_id: str) -> None:
        super().__init__(f"{action} is already running as {existing_job_id}")
        self.action = action
        self.existing_job_id = existing_job_id


@dataclass
class Job:
    id: str
    action: str
    status: str = "queued"
    progress: list[ProgressEvent] = field(default_factory=list)
    latest_progress: ProgressEvent | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: _now())
    started_at: str | None = None
    ended_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action,
            "status": self.status,
            "progress": list(self.progress),
            "latest_progress": dict(self.latest_progress) if self.latest_progress else None,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


class JobManager:
    def __init__(
        self,
        max_workers: int = 2,
        feed_limit: int = 200,
        completion_callback: JobCompletionCallback | None = None,
    ) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._counter = itertools.count(1)
        self._feed_counter = itertools.count(1)
        self._jobs: dict[str, Job] = {}
        self._futures = {}
        self._feed: list[dict[str, Any]] = []
        self._feed_limit = feed_limit
        self._lock = threading.RLock()
        self._completion_callback = completion_callback

    def set_completion_callback(self, callback: JobCompletionCallback | None) -> None:
        with self._lock:
            self._completion_callback = callback

    def start(self, action: str, callback: JobCallable) -> Job:
        with self._lock:
            existing = self._find_active_by_action(action)
            if existing:
                raise JobAlreadyRunning(action, existing.id)
            job = Job(id=f"job-{next(self._counter)}", action=action)
            self._jobs[job.id] = job
            self._append_event_locked(job, f"queued {action}", "info")
            future = self._executor.submit(self._run_job, job.id, callback)
            self._futures[job.id] = future
            return job

    def get(self, job_id: str) -> Job:
        with self._lock:
            return self._jobs[job_id]

    def get_or_none(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def wait(self, job_id: str, timeout: float | None = None) -> Job:
        future = self._futures[job_id]
        future.result(timeout=timeout)
        return self.get(job_id)

    def feed(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self._feed[-limit:]))

    def has_active_jobs(self) -> bool:
        with self._lock:
            return any(job.status in {"queued", "running"} for job in self._jobs.values())

    def _run_job(self, job_id: str, callback: JobCallable) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.started_at = _now()
            self._append_event_locked(job, f"started {job.action}", "info")

        def progress(event: ProgressInput) -> None:
            with self._lock:
                current = self._jobs[job_id]
                normalized = _normalize_progress(event)
                current.progress.append(normalized)
                current.latest_progress = normalized
                if normalized.get("feed"):
                    self._append_event_locked(current, str(normalized.get("message") or ""), "info")

        try:
            result = callback(progress)
        except Exception as exc:  # pragma: no cover - safety net
            with self._lock:
                job = self._jobs[job_id]
                job.status = "failed"
                job.error = str(exc)
                job.ended_at = _now()
                self._append_event_locked(job, f"failed {job.action}: {exc}", "error")
                self._run_completion_callback(job)
            return

        with self._lock:
            job = self._jobs[job_id]
            job.status = "succeeded"
            job.result = result
            job.ended_at = _now()
            self._append_event_locked(job, f"succeeded {job.action}", "info")
            self._run_completion_callback(job)

    def _find_active_by_action(self, action: str) -> Job | None:
        for job in self._jobs.values():
            if job.action == action and job.status in {"queued", "running"}:
                return job
        return None

    def _append_event_locked(self, job: Job, message: str, level: str) -> None:
        self._feed.append(
            {
                "id": next(self._feed_counter),
                "at": _now(),
                "job_id": job.id,
                "action": job.action,
                "level": level,
                "message": message,
            }
        )
        if len(self._feed) > self._feed_limit:
            del self._feed[: len(self._feed) - self._feed_limit]

    def _run_completion_callback(self, job: Job) -> None:
        callback = self._completion_callback
        if callback is None:
            return
        try:
            callback(job)
        except Exception:
            self._append_event_locked(job, f"failed to record action result for {job.action}", "error")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_progress(event: ProgressInput) -> ProgressEvent:
    if isinstance(event, dict):
        normalized = dict(event)
        normalized.setdefault("phase", "running")
        normalized.setdefault("message", "")
        normalized.setdefault("current", None)
        normalized.setdefault("total", None)
        return normalized
    return {
        "phase": "running",
        "message": str(event),
        "current": None,
        "total": None,
    }
