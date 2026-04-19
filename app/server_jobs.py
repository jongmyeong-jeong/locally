"""Async job registry for the server.

Tracks in-flight SSE jobs (transcribe / summarize / models download) so the
HTTP layer can:
  - list them   (GET  /api/jobs)
  - cancel them (POST /api/documents/{id}/cancel) — A2: kill subprocess
  - proxy their events from a second client (GET /api/documents/{id}/events)

Keyed by document_id for document-bound jobs (transcribe, summarize).
Download jobs use the model id as their key.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Literal

JobType = Literal["transcribe", "summarize", "download"]
JobStatus = Literal["running", "completed", "error", "cancelled"]


@dataclass
class JobHandle:
    job_id: str
    document_id: str
    job_type: JobType
    status: JobStatus = "running"
    started_at: float = field(default_factory=time.monotonic)
    task: asyncio.Task | None = None
    subprocess: asyncio.subprocess.Process | None = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)


_JOBS: dict[str, JobHandle] = {}
_LOCK = asyncio.Lock()


async def register(document_id: str, job_type: JobType) -> JobHandle:
    """Register a new job keyed by document_id. Replaces any prior entry."""
    async with _LOCK:
        handle = JobHandle(
            job_id=document_id,
            document_id=document_id,
            job_type=job_type,
        )
        _JOBS[document_id] = handle
        return handle


async def attach_task(document_id: str, task: asyncio.Task) -> None:
    async with _LOCK:
        handle = _JOBS.get(document_id)
        if handle is not None:
            handle.task = task


async def attach_subprocess(
    document_id: str, proc: asyncio.subprocess.Process
) -> None:
    """Stash the subprocess so cancel() can kill it (A2)."""
    async with _LOCK:
        handle = _JOBS.get(document_id)
        if handle is not None:
            handle.subprocess = proc


async def set_status(document_id: str, status: JobStatus) -> None:
    async with _LOCK:
        handle = _JOBS.get(document_id)
        if handle is not None:
            handle.status = status


async def cancel(document_id: str) -> bool:
    """Cancel a running job. Returns True if a job was found.

    Behavior (A2):
      - Set the cancel_event (SSE emitters watch this).
      - If a subprocess is attached, kill it immediately.
      - Mark status='cancelled'.
    """
    async with _LOCK:
        handle = _JOBS.get(document_id)
        if handle is None:
            return False
        handle.cancel_event.set()
        proc = handle.subprocess
        handle.status = "cancelled"

    if proc is not None and proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    return True


async def get(document_id: str) -> JobHandle | None:
    async with _LOCK:
        return _JOBS.get(document_id)


async def unregister(document_id: str) -> None:
    async with _LOCK:
        _JOBS.pop(document_id, None)


async def list_jobs() -> list[dict]:
    async with _LOCK:
        return [
            {
                "jobId": h.job_id,
                "type": h.job_type,
                "documentId": h.document_id,
                "status": h.status,
            }
            for h in _JOBS.values()
        ]


def reset_for_testing() -> None:
    """Clear registry (tests only — not async to keep conftest simple)."""
    _JOBS.clear()
