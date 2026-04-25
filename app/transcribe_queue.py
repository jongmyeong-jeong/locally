"""Per-session async transcription queue.

One SessionTranscribeQueue instance per live recording session drives a single
asyncio worker task that processes ChunkJob items sequentially.  Module-level
registry (_QUEUES) maps session_id → queue instance.

State machine mirrored from recording_chunks:
  queued → transcribing → success
  queued → transcribing → retry  → transcribing → success
  queued → transcribing → retry  → transcribing → failed
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from app import db, recording_chunks, transcribe

logger = logging.getLogger(__name__)

# Sentinel pushed by stop() to signal the worker to exit.
_SENTINEL = None


@dataclass
class ChunkJob:
    chunk_id: int       # recording_chunks.id (already inserted with status='queued')
    note_id: str
    seq: int
    start_ms: int
    end_ms: int
    audio_path: str     # path to the per-chunk webm/pcm temp file


class SessionTranscribeQueue:
    """One instance per live recording session.  Single async worker."""

    def __init__(
        self,
        note_id: str,
        model_dir: str | None,
        glossary_prompt: str | None,
    ) -> None:
        self._note_id = note_id
        self._model_dir = model_dir
        self._glossary_prompt = glossary_prompt

        self._queue: asyncio.Queue[ChunkJob | None] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

        # Drain tracking: _active_jobs counts jobs not yet in a terminal state.
        # Incremented on push(); decremented only when status reaches 'success'
        # or 'failed' (not 'retry', which re-queues the job).
        self._active_jobs: int = 0
        self._idle_event: asyncio.Event = asyncio.Event()
        self._idle_event.set()  # starts idle (0 active jobs)

        self._failed_ranges: list[dict] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Idempotent — start the background worker task if not already running."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())

    async def push(self, job: ChunkJob) -> None:
        """Enqueue a chunk job.  Returns immediately."""
        self._active_jobs += 1
        self._idle_event.clear()
        await self._queue.put(job)

    async def drain(self) -> None:
        """Block until ALL jobs reach a terminal state (success or failed).

        Re-queued retry jobs also count — drain does NOT return the moment
        the queue is momentarily empty; it waits until _active_jobs == 0.
        """
        await self._idle_event.wait()

    async def stop(self) -> None:
        """Signal worker to exit; await the worker task."""
        await self._queue.put(_SENTINEL)
        if self._worker_task is not None:
            await self._worker_task

    @property
    def glossary_prompt(self) -> str | None:
        """Glossary prompt string passed at queue creation time."""
        return self._glossary_prompt

    @property
    def failed_ranges(self) -> list[dict]:
        """List of {seq, start_ms, end_ms} for chunks that ended in 'failed'."""
        return list(self._failed_ranges)

    # ------------------------------------------------------------------
    # Internal worker
    # ------------------------------------------------------------------

    async def _worker_loop(self) -> None:
        """Pull jobs from the queue and process them one at a time."""
        while True:
            job = await self._queue.get()
            if job is _SENTINEL:
                self._queue.task_done()
                break
            try:
                await self._process(job)
            except Exception:
                # Unexpected error — mark as failed so drain can unblock.
                logger.exception(
                    "Unexpected error processing chunk %d (seq=%d)",
                    job.chunk_id,
                    job.seq,
                )
                self._mark_terminal_failed(job)
            finally:
                self._queue.task_done()

    async def _process(self, job: ChunkJob) -> None:
        # 1. Mark as transcribing.
        conn = db.open_db()
        try:
            recording_chunks.update_chunk_status(conn, job.chunk_id, "transcribing")
        finally:
            conn.close()

        # 2. Run transcription in a thread (blocking call).
        text: str | None = None
        error: Exception | None = None
        try:
            text, _segments = await asyncio.to_thread(
                transcribe.run,
                job.audio_path,
                model_dir=self._model_dir,
                prompt=self._glossary_prompt,
                profile="chunk",
            )
        except transcribe.TranscriptionError as exc:
            error = exc
            logger.warning(
                "TranscriptionError for chunk %d (seq=%d): %s",
                job.chunk_id,
                job.seq,
                exc,
            )

        # 3a. Success (non-empty text).
        if error is None and text is not None and text.strip() != "":
            conn = db.open_db()
            try:
                recording_chunks.update_chunk_status(conn, job.chunk_id, "success", text)
            finally:
                conn.close()
            # Best-effort temp file cleanup.
            Path(job.audio_path).unlink(missing_ok=True)
            self._decrement_active()
            return

        # 3b. Failure (TranscriptionError or empty text) — check retry_count.
        conn = db.open_db()
        try:
            row = conn.execute(
                "SELECT retry_count FROM recording_chunks WHERE id = ?",
                (job.chunk_id,),
            ).fetchone()
            retry_count = row["retry_count"] if row else 0

            if retry_count < 1:
                # Increment retry_count and set status to 'retry', then re-enqueue.
                conn.execute(
                    "UPDATE recording_chunks "
                    "SET retry_count = retry_count + 1, status = 'retry', updated_at = datetime('now') "
                    "WHERE id = ?",
                    (job.chunk_id,),
                )
                conn.commit()
        finally:
            conn.close()

        if retry_count < 1:
            # Re-enqueue WITHOUT incrementing _active_jobs (job already counted).
            await self._queue.put(job)
            return

        # Final failure — retry_count >= 1, mark failed and keep temp file
        # (Step 6c re-transcription uses ffmpeg extraction from the session webm;
        # keeping the per-chunk file is harmless but not required — delete here
        # for cleanliness).
        self._mark_terminal_failed(job)
        Path(job.audio_path).unlink(missing_ok=True)

    def _mark_terminal_failed(self, job: ChunkJob) -> None:
        """Persist 'failed' status, append to failed_ranges, and decrement counter."""
        conn = db.open_db()
        try:
            recording_chunks.update_chunk_status(conn, job.chunk_id, "failed")
        except Exception:
            logger.exception("Could not mark chunk %d as failed", job.chunk_id)
        finally:
            conn.close()
        self._failed_ranges.append(
            {"seq": job.seq, "start_ms": job.start_ms, "end_ms": job.end_ms}
        )
        self._decrement_active()

    def _decrement_active(self) -> None:
        self._active_jobs -= 1
        if self._active_jobs == 0:
            self._idle_event.set()


# ---------------------------------------------------------------------------
# Module-level registry
# ---------------------------------------------------------------------------

_QUEUES: dict[str, SessionTranscribeQueue] = {}
_QUEUES_LOCK: asyncio.Lock  # initialised lazily below


def _get_lock() -> asyncio.Lock:
    """Return the module-level lock, creating it inside the running event loop."""
    global _QUEUES_LOCK
    try:
        return _QUEUES_LOCK
    except NameError:
        _QUEUES_LOCK = asyncio.Lock()
        return _QUEUES_LOCK


async def create_session_queue(
    session_id: str,
    note_id: str,
    model_dir: str | None,
    glossary_prompt: str | None,
) -> SessionTranscribeQueue:
    """Create and register a queue for session_id; raises if already exists."""
    lock = _get_lock()
    async with lock:
        if session_id in _QUEUES:
            raise ValueError(f"Session queue already exists for session_id={session_id!r}")
        q = SessionTranscribeQueue(note_id, model_dir, glossary_prompt)
        _QUEUES[session_id] = q
        return q


async def get_session_queue(session_id: str) -> SessionTranscribeQueue | None:
    """Return the queue for session_id, or None if not registered."""
    lock = _get_lock()
    async with lock:
        return _QUEUES.get(session_id)


async def remove_session_queue(session_id: str) -> None:
    """Stop and remove queue from registry.  Idempotent."""
    lock = _get_lock()
    async with lock:
        q = _QUEUES.pop(session_id, None)
    if q is not None:
        await q.stop()
