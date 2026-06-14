"""Background job management for async solve operations.

A Job wraps one run_solve() call in a daemon thread.  The caller can cancel
the job at any time; the pipeline checks the cancel_event at each stage
boundary and raises SolveCancelledError if it is set.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class Job:
    id: str
    session_id: str
    status: JobStatus = JobStatus.PENDING
    error: str | None = None
    error_code: str | None = None
    _cancel_event: threading.Event = field(
        default_factory=threading.Event, repr=False, compare=False
    )

    def cancel(self) -> None:
        self._cancel_event.set()

    @property
    def cancel_event(self) -> threading.Event:
        return self._cancel_event


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(self, session_id: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], session_id=session_id)
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job:
        if job_id not in self._jobs:
            raise KeyError(job_id)
        return self._jobs[job_id]


job_store = JobStore()
