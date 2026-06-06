"""Per-session async transcription queue with 60-second batch accumulation.

One SessionTranscribeQueue instance per live recording session drives a single
asyncio worker task that processes ChunkJob items sequentially.  Module-level
registry (_QUEUES) maps session_id → queue instance.

Architecture change (groq migration):
  - VAD chunk WAV paths are accumulated in a per-session buffer.
  - When cumulative duration >= 60 s, the buffer is flushed: chunks are
    concatenated via audio_concat.concat_wav_chunks() and the resulting
    batch WAV is sent to groq_client.transcribe_audio().
  - On drain() (recording end), any remaining < 60 s buffer is flushed as one
    final batch.
  - Each completed batch creates ONE recording_chunks row (start_ms = first
    VAD chunk's start_ms, end_ms = last VAD chunk's end_ms).
  - Retry policy: GroqNetworkError → retry same batch up to 5 times at 60 s
    fixed intervals.  429/5xx → immediate SSE error, mark failed, continue.

State machine (batch level):
  queued → transcribing → success
  queued → transcribing → (network retry x5) → failed
  queued → transcribing → failed  (rate_limit / server_error)
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from app import audio_concat, db, groq_client, recording_chunks
from app.groq_client import (
    GroqApiKeyMissing,
    GroqClientError,
    GroqNetworkError,
    GroqRateLimitError,
    GroqServerError,
)

logger = logging.getLogger(__name__)

# Sentinel pushed by stop() to signal the worker to exit.
_SENTINEL = None

# Max retries for GroqNetworkError.
_MAX_NETWORK_RETRIES = 5
# Fixed sleep between network retries (seconds).
_NETWORK_RETRY_SLEEP = 60
# Minimum accumulated duration before flushing a batch (milliseconds).
_BATCH_THRESHOLD_MS = 60_000


@dataclass
class ChunkJob:
    chunk_id: int  # recording_chunks.id (already inserted with status='queued')
    note_id: str
    seq: int
    start_ms: int
    end_ms: int
    audio_path: str  # path to the per-chunk WAV temp file


@dataclass
class _PendingChunk:
    """Internal: one accumulated VAD chunk not yet sent to groq."""

    chunk_id: int
    seq: int
    start_ms: int
    end_ms: int
    audio_path: str


@dataclass
class _BatchJob:
    """A collection of VAD chunks that will be concatenated and transcribed together."""

    chunks: list[_PendingChunk] = field(default_factory=list)
    # Per-batch retry overrides.  None means use the module-level constants.
    # drain() sets these to inject finalize-path values (e.g. 5s × 5).
    retry_sleep_sec: int | None = None
    max_retries: int | None = None

    @property
    def start_ms(self) -> int:
        return self.chunks[0].start_ms

    @property
    def end_ms(self) -> int:
        return self.chunks[-1].end_ms

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def seq(self) -> int:
        """Batch seq == seq of the first VAD chunk in the batch."""
        return self.chunks[0].seq


class SessionTranscribeQueue:
    """One instance per live recording session.  Single async worker."""

    def __init__(
        self,
        session_id: str,
        note_id: str,
        prompt: str | None,
    ) -> None:
        self._session_id = session_id
        self._note_id = note_id
        self._prompt = prompt

        # Internal queue receives _BatchJob items (pre-assembled by push())
        # or _SENTINEL.
        self._queue: asyncio.Queue[_BatchJob | None] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

        # Drain tracking: _active_jobs counts batches not yet in a terminal state.
        # Incremented when a batch is dispatched to the queue; decremented on
        # success or final failure.
        self._active_jobs: int = 0
        self._idle_event: asyncio.Event = asyncio.Event()
        self._idle_event.set()  # starts idle (0 active jobs)

        # Lock protecting shared mutable state: _pending_chunks,
        # _accumulated_duration_ms, _active_jobs, and _idle_event.
        self._state_lock: asyncio.Lock = asyncio.Lock()

        self._failed_ranges: list[dict] = []

        # Accumulator state: pending VAD chunks not yet batched.
        self._pending_chunks: list[_PendingChunk] = []
        self._accumulated_duration_ms: int = 0

        # One-way latch: set to True when the live worker exhausts all network
        # retries.  Subsequent push() calls are silently discarded.  drain()
        # ignores this flag so finalize can still flush any buffered chunks.
        self._live_failed: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_note_id(self, note_id: str) -> None:
        """Late-bind the real note id once the seq=0 chunk has created the note.

        The queue is constructed before the note exists (create_recording passes
        session_id as a placeholder), so batch rows would otherwise be keyed by
        session_id and never found by finalize/download (which query by note_id).
        """
        self._note_id = note_id

    async def start(self) -> None:
        """Idempotent — start the background worker task if not already running."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())

    async def push(self, job: ChunkJob) -> None:
        """Accumulate a VAD chunk.  Dispatches a batch when threshold is reached.

        If _live_failed is True (network retries exhausted), the job is
        silently discarded — no new transcription work is enqueued.
        """
        if self._live_failed:
            return
        duration_ms = job.end_ms - job.start_ms
        pending = _PendingChunk(
            chunk_id=job.chunk_id,
            seq=job.seq,
            start_ms=job.start_ms,
            end_ms=job.end_ms,
            audio_path=job.audio_path,
        )
        batch_to_dispatch = None
        async with self._state_lock:
            self._pending_chunks.append(pending)
            self._accumulated_duration_ms += duration_ms

            if self._accumulated_duration_ms >= _BATCH_THRESHOLD_MS:
                # Pre-collect the batch under lock; enqueue outside lock to
                # avoid holding the lock across await queue.put.
                batch_to_dispatch = self._flush_pending_locked()

        if batch_to_dispatch is not None:
            await self._queue.put(batch_to_dispatch)

    async def drain(
        self,
        retry_sleep_sec: int = 60,
        max_retries: int = 5,
    ) -> None:
        """Flush remaining pending chunks, then block until all batches are done.

        Called on recording end.  Flushes any < 60 s remainder as a final batch,
        then waits until _active_jobs == 0.

        Args:
            retry_sleep_sec: Seconds to sleep between GroqNetworkError retries.
                Defaults to 60 (the live-worker policy).  Pass a smaller value
                (e.g. 5) for the finalize path.
            max_retries: Maximum number of GroqNetworkError retries per batch.
                Defaults to 5.
        """
        batch_to_dispatch = None
        async with self._state_lock:
            if self._pending_chunks:
                batch_to_dispatch = self._flush_pending_locked()
        if batch_to_dispatch is not None:
            batch_to_dispatch.retry_sleep_sec = retry_sleep_sec
            batch_to_dispatch.max_retries = max_retries
            await self._queue.put(batch_to_dispatch)
        await self._idle_event.wait()

    async def stop(self) -> None:
        """Signal worker to exit; await the worker task."""
        await self._queue.put(_SENTINEL)
        if self._worker_task is not None:
            await self._worker_task

    @property
    def prompt(self) -> str | None:
        """Whisper conditioning prompt passed at queue creation time."""
        return self._prompt

    @property
    def failed_ranges(self) -> list[dict]:
        """List of {start_ms, end_ms} for batches that ended in 'failed'."""
        return list(self._failed_ranges)

    # ------------------------------------------------------------------
    # Accumulator flush
    # ------------------------------------------------------------------

    def _flush_pending_locked(self) -> _BatchJob:
        """Build and return a _BatchJob from pending chunks, updating shared state.

        MUST be called with self._state_lock held.  Does not enqueue; caller is
        responsible for await self._queue.put(batch) after releasing the lock.
        """
        batch = _BatchJob(chunks=list(self._pending_chunks))
        self._pending_chunks.clear()
        self._accumulated_duration_ms = 0
        self._active_jobs += 1
        self._idle_event.clear()
        return batch

    # ------------------------------------------------------------------
    # Internal worker
    # ------------------------------------------------------------------

    async def _worker_loop(self) -> None:
        """Pull batch jobs from the queue and process them one at a time."""
        while True:
            batch = await self._queue.get()
            if batch is _SENTINEL:
                self._queue.task_done()
                break
            try:
                await self._process_batch(batch)
            except Exception:
                logger.exception(
                    "Unexpected error processing batch seq=%d session=%s",
                    batch.seq,
                    self._session_id,
                )
                await self._mark_batch_failed(batch, error_type="unexpected_error")
            finally:
                self._queue.task_done()

    @staticmethod
    def _safe_bulk_update_status(conn, chunk_ids: list[int], status: str) -> None:
        """bulk_update_status that swallows exceptions (best-effort).

        Used to keep VAD-chunk row statuses in sync with batch status.  A
        failure here must not abort the batch — the batch row itself is the
        authoritative status carrier.
        """
        try:
            recording_chunks.bulk_update_status(conn, chunk_ids, status)
        except Exception:  # noqa: BLE001
            pass

    async def _process_batch(self, batch: _BatchJob) -> None:
        """Concatenate WAVs, call groq, handle retries and errors."""
        chunk_ids = [c.chunk_id for c in batch.chunks]
        wav_paths = [Path(c.audio_path) for c in batch.chunks]
        # The batch's first member chunk row doubles as its text/status carrier.
        # Inserting a separate batch row under the real note id would violate
        # UNIQUE(note_id, seq) — that member row already owns the same seq.
        batch_chunk_id: int | None = (
            batch.chunks[0].chunk_id if batch.chunks else None
        )
        # Declared early so the try/finally safety net can clean it up on any
        # unexpected exception that bypasses the per-error _cleanup() calls (M6).
        batch_wav: Path | None = None

        try:
            # 1. Flip member VAD chunk rows to 'transcribing'.
            with closing(db.open_db()) as conn:
                recording_chunks.bulk_update_status(conn, chunk_ids, "transcribing")

            # 3. Concatenate WAV files into a temp file.
            try:
                batch_wav = await self._concat_wavs(wav_paths, batch)
            except ValueError as exc:
                # Format drift — treat as a non-retriable client error.
                logger.error(
                    "WAV concat failed for batch seq=%d session=%s: %s",
                    batch.seq,
                    self._session_id,
                    exc,
                )
                await self._push_groq_error_event(
                    "concat_error",
                    {"batch_seq": batch.seq, "detail": str(exc)},
                )
                await self._mark_batch_failed(
                    batch,
                    batch_chunk_id=batch_chunk_id,
                    error_type="concat_error",
                )
                return

            # 4. Call groq with retry policy from the batch (set by drain() for
            #    the finalize path) or fall back to module-level constants (live
            #    worker path).  This avoids a shared-state race between concurrent
            #    batches.
            retry_sleep_sec = batch.retry_sleep_sec if batch.retry_sleep_sec is not None else _NETWORK_RETRY_SLEEP
            max_retries = batch.max_retries if batch.max_retries is not None else _MAX_NETWORK_RETRIES
            result = None
            for attempt in range(1, max_retries + 1):
                try:
                    result = await asyncio.to_thread(
                        groq_client.transcribe_audio,
                        batch_wav,
                        prompt=self._prompt,
                    )
                    break  # success

                except GroqNetworkError as exc:
                    logger.warning(
                        "GroqNetworkError batch seq=%d attempt=%d/%d session=%s: %s",
                        batch.seq,
                        attempt,
                        max_retries,
                        self._session_id,
                        exc,
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(retry_sleep_sec)
                    else:
                        # All retries exhausted — latch the session so no
                        # further pushes are accepted, then fail via the shared
                        # path (SSE notify + cleanup + DB mark).
                        logger.error(
                            "Batch seq=%d failed after %d retries (network) session=%s",
                            batch.seq, max_retries, self._session_id,
                        )
                        self._live_failed = True
                        await self._fail_batch(
                            batch, batch_wav, batch_chunk_id,
                            error_type="network_failed_max_retries",
                            details={"batch_seq": batch.seq, "session_id": self._session_id},
                        )
                        return

                except GroqRateLimitError as exc:
                    logger.warning(
                        "GroqRateLimitError batch seq=%d session=%s: %s",
                        batch.seq, self._session_id, exc,
                    )
                    await self._fail_batch(
                        batch, batch_wav, batch_chunk_id,
                        error_type="rate_limit",
                        details={"batch_seq": batch.seq},
                    )
                    return

                except GroqServerError as exc:
                    logger.error(
                        "GroqServerError batch seq=%d session=%s: %s",
                        batch.seq, self._session_id, exc,
                    )
                    await self._fail_batch(
                        batch, batch_wav, batch_chunk_id,
                        error_type="server_error",
                        details={"batch_seq": batch.seq, "detail": str(exc)},
                    )
                    return

                except (GroqApiKeyMissing, GroqClientError) as exc:
                    error_type = (
                        "api_key_missing"
                        if isinstance(exc, GroqApiKeyMissing)
                        else "client_error"
                    )
                    logger.error(
                        "%s batch seq=%d session=%s: %s",
                        type(exc).__name__, batch.seq, self._session_id, exc,
                    )
                    await self._fail_batch(
                        batch, batch_wav, batch_chunk_id,
                        error_type=error_type,
                        details={"batch_seq": batch.seq, "detail": str(exc)},
                    )
                    return

            # 5. Success path.
            assert result is not None
            text = result["text"]
            segments = result["segments"]

            # Add global offset (batch.start_ms converted to seconds) to each segment.
            offset_sec = batch.start_ms / 1000.0
            adjusted_segments = [
                {
                    "start": seg["start"] + offset_sec,
                    "end": seg["end"] + offset_sec,
                    "text": seg["text"],
                }
                for seg in segments
            ]

            with closing(db.open_db()) as conn:
                recording_chunks.update_chunk_status(conn, batch_chunk_id, "success", text)
                self._safe_bulk_update_status(conn, chunk_ids, "success")

            # Clean up batch temp WAV.
            _cleanup(batch_wav)
            batch_wav = None

            await self._decrement_active()

            # Build per-segment timing payload from adjusted_segments.
            payload_segments = [
                {
                    "start_ms": int(round(seg["start"] * 1000)),
                    "end_ms": int(round(seg["end"] * 1000)),
                    "text": seg["text"],
                }
                for seg in adjusted_segments
            ]

            cb = _on_chunk_transcribed
            if cb is not None:
                try:
                    await cb(
                        self._session_id,
                        batch.seq,
                        batch.start_ms,
                        batch.end_ms,
                        text,
                        segments=payload_segments,
                        note_id=self._note_id,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "chunk_transcribed callback error session=%s batch_seq=%d",
                        self._session_id,
                        batch.seq,
                    )

            logger.info(
                "batch_transcribed session=%s seq=%d start_ms=%d end_ms=%d segments=%d",
                self._session_id,
                batch.seq,
                batch.start_ms,
                batch.end_ms,
                len(adjusted_segments),
            )

        finally:
            # Safety net: clean up the temp WAV on any unexpected exception that
            # bypassed the per-error _cleanup() calls above (M6).
            # batch_wav is set to None by every normal error/success path so this
            # is a no-op in those cases.
            _cleanup(batch_wav)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _concat_wavs(self, wav_paths: list[Path], batch: _BatchJob) -> Path:
        """Concatenate WAV files in a thread; return path to the batch temp WAV."""
        fd, tmp_path_str = tempfile.mkstemp(
            prefix=f"lonta_batch_{self._session_id}_{batch.seq}_",
            suffix=".wav",
        )
        import os

        os.close(fd)
        output_path = Path(tmp_path_str)
        await asyncio.to_thread(audio_concat.concat_wav_chunks, wav_paths, output_path)
        return output_path

    async def _push_groq_error_event(self, error_type: str, details: dict) -> None:
        cb = _on_groq_error
        if cb is None:
            return
        try:
            await cb(self._session_id, error_type, details)
        except Exception:  # noqa: BLE001
            logger.warning(
                "groq_error callback error session=%s error_type=%s",
                self._session_id,
                error_type,
            )

    async def _fail_batch(
        self,
        batch: _BatchJob,
        batch_wav: Path | None,
        batch_chunk_id: int | None,
        *,
        error_type: str,
        details: dict,
    ) -> None:
        """Common non-retriable-error path: SSE notify → cleanup temp WAV →
        mark batch as failed in DB.  Mutates nothing the caller still uses
        (caller must drop its local batch_wav reference after calling).
        """
        await self._push_groq_error_event(error_type, details)
        _cleanup(batch_wav)
        await self._mark_batch_failed(
            batch, batch_chunk_id=batch_chunk_id, error_type=error_type,
        )

    async def _mark_batch_failed(
        self,
        batch: _BatchJob,
        *,
        batch_chunk_id: int | None = None,
        error_type: str = "unknown",
    ) -> None:
        """Persist 'failed' status, record in failed_ranges, decrement counter."""
        if batch_chunk_id is None and batch.chunks:
            # Anchor row — same fallback the success path uses for text.
            batch_chunk_id = batch.chunks[0].chunk_id
        if batch_chunk_id is not None:
            try:
                with closing(db.open_db()) as conn:
                    recording_chunks.update_chunk_status(conn, batch_chunk_id, "failed")
                    self._safe_bulk_update_status(
                        conn, [c.chunk_id for c in batch.chunks], "failed"
                    )
            except Exception:
                logger.exception(
                    "Could not mark batch chunk %d as failed", batch_chunk_id
                )

        self._failed_ranges.append(
            {"start_ms": batch.start_ms, "end_ms": batch.end_ms}
        )
        await self._decrement_active()

    async def _decrement_active(self) -> None:
        async with self._state_lock:
            self._active_jobs -= 1
            if self._active_jobs == 0:
                self._idle_event.set()


def _cleanup(path: Path | None) -> None:
    """Best-effort delete of a temp file."""
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Module-level chunk_transcribed callback
# ---------------------------------------------------------------------------
# server.py registers a coroutine callback here so transcribe_queue can push
# chunk_transcribed events without creating a circular import.
# Current signature (5-arg legacy):
#   async (session_id: str, seq: int, start_ms: int, end_ms: int, text: str) -> None
# Extended signature (after G1.3):
#   async (session_id: str, seq: int, start_ms: int, end_ms: int, text: str,
#          *, segments: list[dict] | None = None, note_id: str | None = None) -> None
# Each segment dict: {"start_ms": int, "end_ms": int, "text": str}
# note_id is the queue's late-bound note id — callers need it because the
# recording session may already be gone when finalize-time batches complete.
_on_chunk_transcribed = None


def register_chunk_transcribed_callback(cb) -> None:
    """Register a coroutine callback invoked after each successful batch transcription.

    The callback is invoked with positional args (session_id, seq, start_ms,
    end_ms, text) and the keyword arg ``segments`` (list of dicts with keys
    start_ms, end_ms, text).  Registrants that do not yet accept ``segments``
    will receive a TypeError-triggered fallback call without that kwarg.
    """
    global _on_chunk_transcribed
    _on_chunk_transcribed = cb


# ---------------------------------------------------------------------------
# Module-level groq error callback
# ---------------------------------------------------------------------------
# server.py registers a coroutine callback here (in G1.3) so transcribe_queue
# can push SSE groq_error events without a circular import.
# Signature: async (session_id: str, error_type: str, details: dict) -> None
#   error_type: "rate_limit" | "server_error" | "network_failed_max_retries"
#               | "api_key_missing" | "client_error" | "concat_error"
#               | "unexpected_error"
_on_groq_error = None


def register_groq_error_callback(cb) -> None:
    """Register a coroutine callback invoked on non-retriable groq errors."""
    global _on_groq_error
    _on_groq_error = cb


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
    prompt: str | None = None,
) -> SessionTranscribeQueue:
    """Create and register a queue for session_id; raises if already exists."""
    lock = _get_lock()
    async with lock:
        if session_id in _QUEUES:
            raise ValueError(
                f"Session queue already exists for session_id={session_id!r}"
            )
        q = SessionTranscribeQueue(session_id, note_id, prompt)
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
