from __future__ import annotations

import threading
import time

import pytest

from log_csv_gather.web.jobs import JobAlreadyRunning, JobManager


def test_job_manager_runs_job_and_records_structured_progress_and_latest_first_feed() -> None:
    manager = JobManager()

    def action(progress):
        progress({"phase": "processing", "current": 1, "total": 2, "message": "first file"})
        progress({"phase": "processing", "current": 2, "total": 2, "message": "second file", "feed": True})
        return {"summary": "ok"}

    job = manager.start("doctor", action)
    finished = manager.wait(job.id, timeout=2)

    assert finished.status == "succeeded"
    assert finished.progress[-1] == {
        "phase": "processing",
        "current": 2,
        "total": 2,
        "message": "second file",
        "feed": True,
    }
    assert finished.latest_progress == finished.progress[-1]
    assert finished.result == {"summary": "ok"}
    assert [event["message"] for event in manager.feed(limit=10)] == [
        "succeeded doctor",
        "second file",
        "started doctor",
        "queued doctor",
    ]


def test_job_manager_normalizes_string_progress_without_adding_feed_noise() -> None:
    manager = JobManager()

    def action(progress):
        progress("plain message")
        return {"summary": "ok"}

    job = manager.start("auth", action)
    finished = manager.wait(job.id, timeout=2)

    assert finished.progress == [
        {"phase": "running", "message": "plain message", "current": None, "total": None}
    ]
    assert [event["message"] for event in manager.feed(limit=10)] == [
        "succeeded auth",
        "started auth",
        "queued auth",
    ]


def test_job_manager_rejects_duplicate_running_action() -> None:
    manager = JobManager()
    release = threading.Event()

    def slow_action(progress):
        progress("waiting")
        release.wait(timeout=2)
        return {"summary": "done"}

    first = manager.start("upload-once", slow_action)
    deadline = time.time() + 2
    while manager.get(first.id).status != "running" and time.time() < deadline:
        time.sleep(0.01)

    with pytest.raises(JobAlreadyRunning) as exc:
        manager.start("upload-once", lambda progress: {"summary": "second"})

    release.set()
    manager.wait(first.id, timeout=2)
    assert exc.value.existing_job_id == first.id
