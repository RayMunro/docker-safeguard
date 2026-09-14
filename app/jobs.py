"""Minimal in-process background job runner with live progress, used for
backups and restores. No external broker needed for a single-instance app."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable


class JobCancelled(Exception):
    """Raised to unwind a running job when cancellation was requested."""


@dataclass
class Job:
    id: str
    kind: str  # backup | restore
    container_name: str
    status: str = "running"  # running | success | failed | cancelled
    percent: int = 0
    log_lines: list[str] = field(default_factory=list)
    error: str | None = None
    result: dict | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _cancel_event: threading.Event = field(default_factory=threading.Event)

    def log(self, line: str) -> None:
        with self._lock:
            self.log_lines.append(line)
            if len(self.log_lines) > 500:
                self.log_lines = self.log_lines[-500:]

    def set_percent(self, pct: int) -> None:
        with self._lock:
            self.percent = max(0, min(100, pct))

    def request_cancel(self) -> None:
        self._cancel_event.set()
        self.log("cancellation requested…")

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def finish(self, status: str, error: str | None = None, result: dict | None = None) -> None:
        with self._lock:
            self.status = status
            self.error = error
            self.result = result
            self.finished_at = datetime.now(timezone.utc)
            self.percent = 100 if status == "success" else self.percent

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "container_name": self.container_name,
                "status": self.status,
                "percent": self.percent,
                "log_lines": list(self.log_lines[-100:]),
                "error": self.error,
                "result": self.result,
            }


_JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()


def create_job(kind: str, container_name: str) -> Job:
    job = Job(id=uuid.uuid4().hex[:12], kind=kind, container_name=container_name)
    with _JOBS_LOCK:
        _JOBS[job.id] = job
    return job


def get_job(job_id: str) -> Job | None:
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def cancel_job(job_id: str) -> bool:
    job = get_job(job_id)
    if job and job.status == "running":
        job.request_cancel()
        return True
    return False


def run_in_background(fn: Callable[[Job], None], job: Job) -> None:
    def _run():
        try:
            fn(job)
            if job.status == "running":
                job.finish("success")
        except JobCancelled:
            job.log("cancelled")
            job.finish("cancelled")
            _record_outcome(job, "cancelled")
        except Exception as exc:  # noqa: BLE001
            job.log(f"ERROR: {exc}")
            job.finish("failed", error=str(exc))
            _record_outcome(job, "failed")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


def _record_outcome(job: Job, status: str) -> None:
    # A successful backup/restore records its own detailed BackupJob row;
    # a failed or cancelled one otherwise vanishes once the in-memory Job
    # is gone (e.g. on container restart), leaving no trace on the Logs page.
    try:
        from .db import session_scope
        from .models import BackupJob

        with session_scope() as session:
            session.add(
                BackupJob(
                    job_uuid=job.id,
                    container_name=job.container_name,
                    kind=job.kind,
                    finished_at=job.finished_at,
                    status=status,
                    message=job.error,
                )
            )
            session.commit()
    except Exception:  # noqa: BLE001
        pass
