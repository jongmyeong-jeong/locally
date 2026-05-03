"""Tests for app/transcribe_queue.py — 60s batch accumulation (Step 6.3).

Mocks: groq_client.transcribe_audio, audio_concat.concat_wav_chunks, db, recording_chunks.
No real groq calls or file I/O.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.groq_client import GroqNetworkError
from app.transcribe_queue import (
    ChunkJob,
    SessionTranscribeQueue,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_result(text: str = "hello") -> dict:
    return {"text": text, "segments": [{"start": 0.0, "end": 1.0, "text": text}]}


def _make_wav(path: Path) -> None:
    import wave
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00" * 3200)


def _make_chunk_job(
    chunk_id: int,
    seq: int,
    start_ms: int,
    duration_ms: int,
    audio_path: str = "/tmp/fake.wav",
    note_id: str = "note-1",
) -> ChunkJob:
    return ChunkJob(
        chunk_id=chunk_id,
        note_id=note_id,
        seq=seq,
        start_ms=start_ms,
        end_ms=start_ms + duration_ms,
        audio_path=audio_path,
    )


# ---------------------------------------------------------------------------
# Base fixture — patches shared across all batching tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_transcribe():
    """Patch groq_client.transcribe_audio to return a deterministic result."""
    with patch("app.transcribe_queue.groq_client.transcribe_audio") as m:
        m.return_value = _fake_result()
        yield m


@pytest.fixture
def mock_concat(tmp_path):
    """Patch audio_concat.concat_wav_chunks to create a real (tiny) WAV at dest."""
    def _fake_concat(paths, dest):
        _make_wav(dest)

    with patch("app.transcribe_queue.audio_concat.concat_wav_chunks", side_effect=_fake_concat):
        yield


@pytest.fixture
def mock_db():
    """Patch db.open_db and recording_chunks ops to no-ops."""
    with patch("app.transcribe_queue.db.open_db") as mock_open, \
         patch("app.transcribe_queue.recording_chunks.insert_chunk", return_value=99) as mock_insert, \
         patch("app.transcribe_queue.recording_chunks.update_chunk_status") as mock_update:
        mock_open.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        yield {"open": mock_open, "insert": mock_insert, "update": mock_update}


# ---------------------------------------------------------------------------
# Helper to build a queue with mocked callbacks
# ---------------------------------------------------------------------------


async def _make_queue(session_id: str = "sess-1") -> tuple[SessionTranscribeQueue, list]:
    """Return (queue, fired_callbacks). Queue is started."""
    fired: list[tuple] = []

    async def _cb(session_id, seq, start_ms, end_ms, text, *, segments=None):
        fired.append((session_id, seq, start_ms, end_ms, text))

    q = SessionTranscribeQueue(session_id, "note-1", prompt=None)
    # Register the callback directly on the module level
    import app.transcribe_queue as tq_mod
    tq_mod._on_chunk_transcribed = _cb
    await q.start()
    return q, fired


# ---------------------------------------------------------------------------
# Tests: batch accumulation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_batch_below_threshold(mock_transcribe, mock_concat, mock_db):
    """50 000ms of chunks don't trigger a flush (threshold is 60 000ms)."""
    q, fired = await _make_queue("sess-accum-1")
    try:
        # 5 chunks × 10 000ms = 50 000ms < 60 000ms
        for i in range(5):
            job = _make_chunk_job(i, i, start_ms=i * 10_000, duration_ms=10_000)
            await q.push(job)

        # Give event loop a tick — no batch should have been dispatched.
        await asyncio.sleep(0)
        assert mock_transcribe.call_count == 0
        assert fired == []
    finally:
        await q.stop()
        import app.transcribe_queue as tq_mod
        tq_mod._on_chunk_transcribed = None


@pytest.mark.asyncio
async def test_one_batch_at_threshold(mock_transcribe, mock_concat, mock_db):
    """6 chunks × 10 000ms = 60 000ms == threshold → 1 batch flushed."""
    q, fired = await _make_queue("sess-accum-2")
    try:
        for i in range(6):
            job = _make_chunk_job(i, i, start_ms=i * 10_000, duration_ms=10_000)
            await q.push(job)

        # Drain residual (empty buffer after the flush)
        await q.drain()
        assert mock_transcribe.call_count == 1
        assert len(fired) == 1
        # Verify batch-level start/end
        _, seq, start_ms, end_ms, _ = fired[0]
        assert start_ms == 0
        assert end_ms == 60_000
    finally:
        await q.stop()
        import app.transcribe_queue as tq_mod
        tq_mod._on_chunk_transcribed = None


@pytest.mark.asyncio
async def test_two_batches_at_120s(mock_transcribe, mock_concat, mock_db):
    """12 chunks × 10 000ms = 120 000ms → 2 batches total."""
    q, fired = await _make_queue("sess-accum-3")
    try:
        for i in range(12):
            job = _make_chunk_job(i, i, start_ms=i * 10_000, duration_ms=10_000)
            await q.push(job)

        await q.drain()
        assert mock_transcribe.call_count == 2
        assert len(fired) == 2
    finally:
        await q.stop()
        import app.transcribe_queue as tq_mod
        tq_mod._on_chunk_transcribed = None


@pytest.mark.asyncio
async def test_drain_flushes_residual(mock_transcribe, mock_concat, mock_db):
    """7 chunks: first 6 flush as batch 1, 7th flushed by drain() as batch 2."""
    q, fired = await _make_queue("sess-drain-1")
    try:
        for i in range(7):
            job = _make_chunk_job(i, i, start_ms=i * 10_000, duration_ms=10_000)
            await q.push(job)

        await q.drain()
        assert mock_transcribe.call_count == 2
        assert len(fired) == 2
    finally:
        await q.stop()
        import app.transcribe_queue as tq_mod
        tq_mod._on_chunk_transcribed = None


@pytest.mark.asyncio
async def test_drain_residual_only(mock_transcribe, mock_concat, mock_db):
    """3 chunks < 60s → no flush during push; drain flushes 1 final batch."""
    q, fired = await _make_queue("sess-drain-2")
    try:
        for i in range(3):
            job = _make_chunk_job(i, i, start_ms=i * 10_000, duration_ms=10_000)
            await q.push(job)

        await q.drain()
        assert mock_transcribe.call_count == 1
        assert len(fired) == 1
    finally:
        await q.stop()
        import app.transcribe_queue as tq_mod
        tq_mod._on_chunk_transcribed = None


@pytest.mark.asyncio
async def test_callback_receives_batch_start_end_ms(mock_transcribe, mock_concat, mock_db):
    """chunk_transcribed callback is called once per batch with correct start/end ms."""
    q, fired = await _make_queue("sess-cb-1")
    try:
        # 6 chunks from 5000ms to 5000 + 5 * 10000 = 55000ms — total range 50s
        # Actually: 6 chunks × 10000ms = 60000ms starting at 5000ms
        for i in range(6):
            job = _make_chunk_job(i, i, start_ms=5000 + i * 10_000, duration_ms=10_000)
            await q.push(job)

        await q.drain()
        assert len(fired) == 1
        _, _, start_ms, end_ms, _ = fired[0]
        assert start_ms == 5000
        assert end_ms == 5000 + 6 * 10_000
    finally:
        await q.stop()
        import app.transcribe_queue as tq_mod
        tq_mod._on_chunk_transcribed = None


# ---------------------------------------------------------------------------
# Tests: retry policy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_4_failures_then_success(mock_concat, mock_db):
    """4 consecutive GroqNetworkErrors then success → 5 total attempts."""
    call_count = {"n": 0}

    def _side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 5:
            raise GroqNetworkError("network down")
        return _fake_result()

    fired = []

    async def _cb(session_id, seq, start_ms, end_ms, text, *, segments=None):
        fired.append((start_ms, end_ms))

    import app.transcribe_queue as tq_mod
    tq_mod._on_chunk_transcribed = _cb

    with patch("app.transcribe_queue.groq_client.transcribe_audio", side_effect=_side_effect), \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        q = SessionTranscribeQueue("sess-retry-1", "note-1", prompt=None)
        await q.start()

        try:
            for i in range(6):
                job = _make_chunk_job(i, i, start_ms=i * 10_000, duration_ms=10_000)
                await q.push(job)
            await q.drain()
        finally:
            await q.stop()
            tq_mod._on_chunk_transcribed = None

    assert call_count["n"] == 5
    # sleep called 4 times (between attempts 1-2, 2-3, 3-4, 4-5)
    assert mock_sleep.call_count == 4
    # Eventually succeeded — callback fired once
    assert len(fired) == 1


@pytest.mark.asyncio
async def test_retry_exhausted_5_failures(mock_concat, mock_db):
    """5 consecutive GroqNetworkErrors → batch marked failed; failed_ranges populated."""
    import app.transcribe_queue as tq_mod
    tq_mod._on_chunk_transcribed = None

    with patch(
        "app.transcribe_queue.groq_client.transcribe_audio",
        side_effect=GroqNetworkError("persistent error"),
    ), patch("asyncio.sleep", new_callable=AsyncMock):
        q = SessionTranscribeQueue("sess-retry-fail-1", "note-1", prompt=None)
        await q.start()

        try:
            for i in range(6):
                job = _make_chunk_job(i, i, start_ms=i * 10_000, duration_ms=10_000)
                await q.push(job)
            await q.drain()
        finally:
            await q.stop()

    assert len(q.failed_ranges) == 1
    fr = q.failed_ranges[0]
    assert fr["start_ms"] == 0
    assert fr["end_ms"] == 60_000
