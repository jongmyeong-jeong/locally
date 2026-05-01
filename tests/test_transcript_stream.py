"""Unit tests for the transcript-stream SSE channel (Phase 2).

Verifies:
- chunk_transcribed events are pushed to registered SSE queues.
- stream_end + sentinel are emitted by finalize before queue removal.
- SSE queue is absent after _remove_transcript_queue.
- _push_chunk_transcribed silently skips when no queue is registered.
"""
from __future__ import annotations

import json

import pytest

# ---------------------------------------------------------------------------
# Helpers — import server-level primitives directly
# ---------------------------------------------------------------------------
from app.server import (
    _push_chunk_transcribed,
    _register_transcript_queue,
    _remove_transcript_queue,
    _TRANSCRIPT_SSE_QUEUES,
    _sse_event,
)


# ---------------------------------------------------------------------------
# Fixture: isolated queue per test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_sse_queues():
    """Ensure _TRANSCRIPT_SSE_QUEUES is empty before and after each test."""
    _TRANSCRIPT_SSE_QUEUES.clear()
    yield
    _TRANSCRIPT_SSE_QUEUES.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_chunk_transcribed_no_queue_silently_skips():
    """No queue registered → push does nothing (no exception)."""
    # Must not raise.
    await _push_chunk_transcribed("session-x", seq=0, start_ms=0, end_ms=1000, text="hello")


@pytest.mark.asyncio
async def test_push_chunk_transcribed_delivers_correct_payload():
    """chunk_transcribed event has the expected JSON payload."""
    session_id = "sess-1"
    q = _register_transcript_queue(session_id)

    await _push_chunk_transcribed(session_id, seq=3, start_ms=1000, end_ms=2000, text="안녕하세요")

    assert not q.empty()
    raw = q.get_nowait()
    assert raw is not None

    # Parse SSE wire format: "event: chunk_transcribed\ndata: {...}\n\n"
    text = raw.decode("utf-8")
    lines = [ln for ln in text.splitlines() if ln]
    event_line = next(ln for ln in lines if ln.startswith("event:"))
    data_line = next(ln for ln in lines if ln.startswith("data:"))

    assert event_line == "event: chunk_transcribed"
    payload = json.loads(data_line[len("data: "):])
    assert payload == {"seq": 3, "startMs": 1000, "endMs": 2000, "text": "안녕하세요"}


@pytest.mark.asyncio
async def test_multiple_chunks_arrive_in_order():
    """Multiple pushes arrive in FIFO order."""
    session_id = "sess-2"
    q = _register_transcript_queue(session_id)

    for i in range(5):
        await _push_chunk_transcribed(session_id, seq=i, start_ms=i * 1000, end_ms=(i + 1) * 1000, text=f"text{i}")

    seqs = []
    while not q.empty():
        raw = q.get_nowait()
        assert raw is not None
        text = raw.decode("utf-8")
        data_line = next(ln for ln in text.splitlines() if ln.startswith("data:"))
        payload = json.loads(data_line[len("data: "):])
        seqs.append(payload["seq"])

    assert seqs == list(range(5))


@pytest.mark.asyncio
async def test_stream_end_and_sentinel_emitted_before_queue_removal():
    """stream_end event followed by None sentinel should be in the queue
    before _remove_transcript_queue is called (simulates finalize behavior)."""
    session_id = "sess-3"
    q = _register_transcript_queue(session_id)

    # Simulate what finalize's finally block does.
    _tsq = _TRANSCRIPT_SSE_QUEUES.get(session_id)
    assert _tsq is not None
    await _tsq.put(_sse_event("stream_end", {"reason": "finalized"}))
    await _tsq.put(None)
    _remove_transcript_queue(session_id)

    # Queue should have been removed from the registry.
    assert session_id not in _TRANSCRIPT_SSE_QUEUES

    # But the events are still readable from the local reference `q`.
    stream_end_raw = q.get_nowait()
    assert stream_end_raw is not None
    text = stream_end_raw.decode("utf-8")
    data_line = next(ln for ln in text.splitlines() if ln.startswith("data:"))
    payload = json.loads(data_line[len("data: "):])
    assert payload == {"reason": "finalized"}
    event_line = next(ln for ln in text.splitlines() if ln.startswith("event:"))
    assert event_line == "event: stream_end"

    sentinel = q.get_nowait()
    assert sentinel is None


@pytest.mark.asyncio
async def test_remove_transcript_queue_idempotent():
    """Calling _remove_transcript_queue twice does not raise."""
    session_id = "sess-4"
    _register_transcript_queue(session_id)
    _remove_transcript_queue(session_id)
    _remove_transcript_queue(session_id)  # second call must not raise
    assert session_id not in _TRANSCRIPT_SSE_QUEUES


@pytest.mark.asyncio
async def test_transcribe_queue_callback_integration():
    """SessionTranscribeQueue invokes the registered callback on successful transcription."""
    from unittest.mock import patch
    import app.transcribe_queue as tq

    session_id = "sess-cb"
    _register_transcript_queue(session_id)

    captured: list[dict] = []

    async def _fake_cb(sid, seq, start_ms, end_ms, text):
        captured.append({"sid": sid, "seq": seq, "startMs": start_ms, "endMs": end_ms, "text": text})

    original_cb = tq._on_chunk_transcribed
    tq.register_chunk_transcribed_callback(_fake_cb)
    try:
        # Build a queue instance with a mock transcribe.run and db.
        with (
            patch("app.transcribe_queue.db") as mock_db,
            patch("app.transcribe_queue.recording_chunks"),
            patch("app.transcribe_queue.transcribe") as mock_tr,
        ):
            mock_db.open_db.return_value.__enter__ = lambda s: s
            mock_db.open_db.return_value.__exit__ = lambda s, *a: None
            mock_db.open_db.return_value.close = lambda: None

            # Simulate db.open_db() returning a context-manager-like conn.
            conn_mock = mock_db.open_db.return_value
            conn_mock.execute.return_value.fetchone.return_value = {"retry_count": 0}

            mock_tr.run.return_value = ("안녕하세요", [])
            mock_tr.TranscriptionError = Exception

            queue_obj = tq.SessionTranscribeQueue(session_id, session_id, None, None)
            await queue_obj.start()

            from app.transcribe_queue import ChunkJob
            job = ChunkJob(
                chunk_id=1,
                note_id=session_id,
                seq=7,
                start_ms=3000,
                end_ms=4000,
                audio_path="/tmp/fake.wav",
            )

            with patch("pathlib.Path.unlink"):
                await queue_obj.push(job)
                await queue_obj.drain()

        assert len(captured) == 1
        assert captured[0] == {
            "sid": session_id, "seq": 7, "startMs": 3000, "endMs": 4000, "text": "안녕하세요"
        }
    finally:
        tq.register_chunk_transcribed_callback(original_cb)
