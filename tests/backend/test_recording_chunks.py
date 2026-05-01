"""Tests for app.recording_chunks CRUD and all_chunks_done semantics."""
from __future__ import annotations

import asyncio

import pytest

from app import db, recording_chunks, transcribe, transcribe_queue


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_home):  # noqa: ARG001 — tmp_home redirects Path.home() to tmp_path
    """Open an in-process DB (in tmp_home so it doesn't touch the real ~/.locally)."""
    c = db.open_db()
    yield c
    c.close()


@pytest.fixture
def note_id(conn):
    """Insert a throwaway note row and clean up recording_chunks after."""
    doc = db.create_note(conn, title="test-doc")
    did = doc["id"]
    yield did
    conn.execute("DELETE FROM recording_chunks WHERE note_id = ?", (did,))
    conn.execute("DELETE FROM notes WHERE id = ?", (did,))
    conn.commit()


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestInsertChunk:
    def test_insert_chunk_creates_queued_row(self, conn, note_id):
        """insert_chunk → status='queued', retry_count=0, text=None."""
        chunk_id = recording_chunks.insert_chunk(conn, note_id, seq=0, start_ms=0, end_ms=5000)
        rows = recording_chunks.get_chunks(conn, note_id)
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == chunk_id
        assert row["status"] == "queued"
        assert row["retry_count"] == 0
        assert row["text"] is None


class TestUpdateChunkStatus:
    def test_update_chunk_status_transitions_to_success(self, conn, note_id):
        """update_chunk_status → 'success' with text='hello'."""
        chunk_id = recording_chunks.insert_chunk(conn, note_id, seq=0, start_ms=0, end_ms=3000)
        recording_chunks.update_chunk_status(conn, chunk_id, "success", "hello")
        rows = recording_chunks.get_chunks(conn, note_id)
        assert rows[0]["status"] == "success"
        assert rows[0]["text"] == "hello"

    def test_update_chunk_status_to_failed(self, conn, note_id):
        """update_chunk_status → 'failed' → get_failed_chunks returns the row."""
        chunk_id = recording_chunks.insert_chunk(conn, note_id, seq=0, start_ms=0, end_ms=3000)
        recording_chunks.update_chunk_status(conn, chunk_id, "failed")
        failed = recording_chunks.get_failed_chunks(conn, note_id)
        assert len(failed) == 1
        assert failed[0]["id"] == chunk_id
        assert failed[0]["status"] == "failed"


class TestAllChunksDone:
    def test_all_chunks_done_only_when_all_success(self, conn, note_id):
        """all_chunks_done → True only when all rows are 'success'; False for any non-success."""
        ids = [
            recording_chunks.insert_chunk(conn, note_id, seq=i, start_ms=i * 5000, end_ms=(i + 1) * 5000)
            for i in range(3)
        ]
        # Initially all queued → False.
        assert recording_chunks.all_chunks_done(conn, note_id) is False

        # One success, two queued → False.
        recording_chunks.update_chunk_status(conn, ids[0], "success", "a")
        assert recording_chunks.all_chunks_done(conn, note_id) is False

        # Two success, one failed → False.
        recording_chunks.update_chunk_status(conn, ids[1], "success", "b")
        recording_chunks.update_chunk_status(conn, ids[2], "failed")
        assert recording_chunks.all_chunks_done(conn, note_id) is False

        # Bring failed back to success (simulate recovery).
        recording_chunks.update_chunk_status(conn, ids[2], "success", "c")
        assert recording_chunks.all_chunks_done(conn, note_id) is True

    def test_all_chunks_done_returns_false_for_zero_chunks(self, conn, note_id):
        """all_chunks_done returns False when no rows exist (empty doc)."""
        assert recording_chunks.all_chunks_done(conn, note_id) is False


class TestGetChunksOrdering:
    def test_get_chunks_orders_by_seq(self, conn, note_id):
        """get_chunks returns rows ordered by seq regardless of insertion order."""
        recording_chunks.insert_chunk(conn, note_id, seq=2, start_ms=10000, end_ms=15000)
        recording_chunks.insert_chunk(conn, note_id, seq=0, start_ms=0, end_ms=5000)
        recording_chunks.insert_chunk(conn, note_id, seq=1, start_ms=5000, end_ms=10000)
        rows = recording_chunks.get_chunks(conn, note_id)
        assert [r["seq"] for r in rows] == [0, 1, 2]


class TestInvalidStatus:
    def test_invalid_status_raises(self, conn, note_id):
        """update_chunk_status with an unrecognised status raises ValueError."""
        chunk_id = recording_chunks.insert_chunk(conn, note_id, seq=0, start_ms=0, end_ms=1000)
        with pytest.raises(ValueError, match="bogus"):
            recording_chunks.update_chunk_status(conn, chunk_id, "bogus")


class TestSessionTranscribeQueueRetrySemantics:
    def test_session_transcribe_queue_failed_after_two_attempts(self, tmp_home, monkeypatch):
        """Step 5 retry semantics: first chunk fails twice → 'failed'; second succeeds.

        Monkeypatches transcribe.run to raise TranscriptionError for the first
        chunk's audio path and return ('ok', []) for any other path.
        """
        # We need a real DB doc + two chunks.
        conn = db.open_db()
        doc = db.create_note(conn, title="retry-test")
        did = doc["id"]

        # Insert two chunks manually so we know their IDs.
        id0 = recording_chunks.insert_chunk(conn, did, seq=0, start_ms=0, end_ms=5000)
        id1 = recording_chunks.insert_chunk(conn, did, seq=1, start_ms=5000, end_ms=10000)
        conn.close()

        # Create fake audio files (transcribe.run checks Path.exists()).
        import tempfile
        from pathlib import Path

        tmp_dir = Path(tempfile.mkdtemp())
        audio0 = tmp_dir / "chunk0.webm"
        audio1 = tmp_dir / "chunk1.webm"
        audio0.write_bytes(b"\x00" * 16)
        audio1.write_bytes(b"\x00" * 16)

        fail_path = str(audio0)

        def fake_run(audio_path, *, model_dir=None, prompt=None, profile="file", progress_cb=None):
            if audio_path == fail_path:
                raise transcribe.TranscriptionError("simulated failure")
            return ("ok text", [])

        monkeypatch.setattr(transcribe, "run", fake_run)

        async def _run():
            q = transcribe_queue.SessionTranscribeQueue(
                session_id=did,
                note_id=did,
                model_dir=None,
                glossary_prompt=None,
            )
            await q.start()
            await q.push(transcribe_queue.ChunkJob(
                chunk_id=id0, note_id=did, seq=0,
                start_ms=0, end_ms=5000, audio_path=str(audio0),
            ))
            await q.push(transcribe_queue.ChunkJob(
                chunk_id=id1, note_id=did, seq=1,
                start_ms=5000, end_ms=10000, audio_path=str(audio1),
            ))
            await q.drain()
            await q.stop()
            return q

        q = asyncio.run(_run())

        # First chunk should be failed after two attempts (retry_count=1 → terminal).
        assert len(q.failed_ranges) == 1
        assert q.failed_ranges[0]["seq"] == 0

        # Second chunk should be 'success'.
        conn2 = db.open_db()
        rows = recording_chunks.get_chunks(conn2, did)
        conn2.close()
        chunk_by_seq = {r["seq"]: r for r in rows}
        assert chunk_by_seq[0]["status"] == "failed"
        assert chunk_by_seq[1]["status"] == "success"
        assert chunk_by_seq[1]["text"] == "ok text"

        # Cleanup.
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
