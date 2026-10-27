"""In-memory job store for long-running scans and fixes.

Long operations run as asyncio tasks so they never block the event loop. Each job
keeps an ordered log and a small event queue that the SSE endpoint drains, so the
dashboard can show real progress rather than a simulated animation.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal

from .models import JobState, LogEntry

logger = logging.getLogger("ghost_maintainer.jobs")

JobKind = Literal["scan", "fix", "review"]

MAX_LOG_ENTRIES = 500
JOB_RETENTION_SECONDS = 60 * 60


@dataclass
class Job:
    """One unit of background work."""

    id: str
    kind: JobKind
    state: JobState = JobState.QUEUED
    progress: int = 0
    error: str | None = None
    logs: list[LogEntry] = field(default_factory=list)
    result: Any | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    subscribers: list[asyncio.Queue] = field(default_factory=list)

    def log(self, message: str, step: str = "", level: str = "info") -> None:
        entry = LogEntry(message=message, step=step, level=level)  # type: ignore[arg-type]
        self.logs.append(entry)
        if len(self.logs) > MAX_LOG_ENTRIES:
            del self.logs[: len(self.logs) - MAX_LOG_ENTRIES]
        self.updated_at = datetime.now(timezone.utc)
        event = {"type": "log", "entry": entry.model_dump(mode="json"), "state": self.state.value}
        for queue in list(self.subscribers):
            queue.put_nowait(event)

    def publish_state(self) -> None:
        event = {
            "type": "state",
            "state": self.state.value,
            "progress": self.progress,
            "error": self.error,
        }
        for queue in list(self.subscribers):
            queue.put_nowait(event)


class JobManager:
    """Creates, tracks and expires jobs."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self.store: dict[str, Any] = {}
        """Artifacts that outlive a job, such as a scan's working copy."""

    def create(self, kind: JobKind, **meta: Any) -> Job:
        job = Job(id=uuid.uuid4().hex[:16], kind=kind, meta=meta)
        self._jobs[job.id] = job
        self._expire_old()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def require(self, job_id: str, kind: JobKind | None = None) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if kind is not None and job.kind != kind:
            raise KeyError(job_id)
        return job

    def subscribe(self, job: Job) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        job.subscribers.append(queue)
        return queue

    def unsubscribe(self, job: Job, queue: asyncio.Queue) -> None:
        if queue in job.subscribers:
            job.subscribers.remove(queue)

    def start(
        self,
        job: Job,
        worker: Callable[[Job], Awaitable[Any]],
    ) -> asyncio.Task:
        """Run a worker in the background and record its outcome."""

        async def runner() -> None:
            job.state = JobState.RUNNING
            job.publish_state()
            try:
                job.result = await worker(job)
                job.state = JobState.SUCCEEDED
                job.progress = 100
            except Exception as exc:  # noqa: BLE001 - every failure is reported to the UI
                logger.exception("Job %s failed", job.id)
                job.error = str(exc) or exc.__class__.__name__
                job.state = JobState.FAILED
                job.log(job.error, "error", "error")
            finally:
                job.updated_at = datetime.now(timezone.utc)
                job.publish_state()
                job.finished.set()

        task = asyncio.create_task(runner(), name=f"{job.kind}-{job.id}")
        return task

    def _expire_old(self) -> None:
        cutoff = datetime.now(timezone.utc).timestamp() - JOB_RETENTION_SECONDS
        stale = [
            job_id
            for job_id, job in self._jobs.items()
            if job.created_at.timestamp() < cutoff and job.finished.is_set()
        ]
        for job_id in stale:
            self._jobs.pop(job_id, None)


manager = JobManager()