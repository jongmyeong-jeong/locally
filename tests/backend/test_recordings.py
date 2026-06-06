"""Tests for app/recordings.py: seq append + dup/gap/short + N1 seq0→Note."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import db as db_mod
from app import recordings


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    yield tmp_path


@pytest.fixture(autouse=True)
def _reset_sessions():
    recordings._SESSIONS.clear()
    yield
    recordings._SESSIONS.clear()


@pytest.fixture
def conn(tmp_path):
    c = db_mod.open_db(tmp_path / "test.sqlite")
    try:
        yield c
    finally:
        c.close()


class TestSeqAppend:
    def test_three_chunks_append_in_order(self, conn):
        sess = recordings.start_session(title="demo")
        sid = sess["id"]
        for seq in range(3):
            r = recordings.append_chunk(conn, sid, b"\x00" * 64, seq)
            assert r["bytes_written"] == 64

        # Finalize with duration_sec=30.0 (> 1s floor).
        out = recordings.finalize(conn, sid, duration_sec=30.0)
        assert out["noteId"] is not None
        assert Path(out["audioPath"]).exists()

    def test_out_of_order_chunks_finalize_when_no_gap(self, conn):
        sess = recordings.start_session(title="demo")
        sid = sess["id"]
        recordings.append_chunk(conn, sid, b"\x00" * 64, 1)
        recordings.append_chunk(conn, sid, b"\x00" * 64, 0)

        out = recordings.finalize(conn, sid, duration_sec=30.0)
        assert out["noteId"] is not None
        assert Path(out["audioPath"]).exists()


class TestAudioPathUniqueness:
    def test_same_title_recordings_keep_separate_audio_files(self, conn):
        """Regression: the audio basename must include the note id.

        Without the suffix two same-title recordings resolve to the same
        .webm and the second finalize overwrites the first recording's audio.
        """
        outs = []
        for payload in (b"\x01" * 64, b"\x02" * 64):
            sess = recordings.start_session(title="demo")
            sid = sess["id"]
            recordings.append_chunk(conn, sid, payload, 0)
            outs.append(recordings.finalize(conn, sid, duration_sec=30.0))

        first, second = outs
        assert first["audioPath"] != second["audioPath"]
        assert Path(first["audioPath"]).exists()
        assert Path(second["audioPath"]).exists()
        # The note id suffix is what guarantees uniqueness.
        assert first["noteId"][:8] in first["audioPath"]
        assert second["noteId"][:8] in second["audioPath"]
        # First recording's bytes survive the second finalize.
        assert Path(first["audioPath"]).read_bytes() == b"\x01" * 64


class TestDuplicateSeq:
    def test_duplicate_seq_raises_conflict(self, conn):
        sess = recordings.start_session(title="demo")
        sid = sess["id"]
        recordings.append_chunk(conn, sid, b"\x00" * 10, 0)
        with pytest.raises(recordings.ChunkSeqConflict) as exc:
            recordings.append_chunk(conn, sid, b"\x00" * 10, 0)
        assert exc.value.seq == 0

    def test_duplicate_seq_does_not_append_bytes_twice(self, conn):
        sess = recordings.start_session(title="demo")
        sid = sess["id"]
        first = b"A" * 10
        second = b"B" * 10

        recordings.append_chunk(conn, sid, first, 0)
        assert recordings._session_tmp_path(sid).read_bytes() == first

        with pytest.raises(recordings.ChunkSeqConflict):
            recordings.append_chunk(conn, sid, second, 0)

        assert recordings._session_tmp_path(sid).read_bytes() == first


class TestGapAtFinalize:
    def test_missing_seq_between_produces_gap_error(self, conn):
        sess = recordings.start_session()
        sid = sess["id"]
        recordings.append_chunk(conn, sid, b"\x00" * 10, 0)
        recordings.append_chunk(conn, sid, b"\x00" * 10, 2)
        with pytest.raises(recordings.ChunkGapError) as exc:
            recordings.finalize(conn, sid, duration_sec=30.0)
        assert exc.value.missing == [1]

    def test_no_chunks_finalize_reports_missing_zero(self, conn):
        sess = recordings.start_session()
        sid = sess["id"]
        with pytest.raises(recordings.ChunkGapError) as exc:
            recordings.finalize(conn, sid)
        assert exc.value.missing == [0]


class TestShortRecording:
    def test_duration_under_1s_raises_too_short(self, conn):
        sess = recordings.start_session()
        sid = sess["id"]
        recordings.append_chunk(conn, sid, b"\x00" * 10, 0)
        with pytest.raises(recordings.RecordingTooShortError) as exc:
            recordings.finalize(conn, sid, duration_sec=0.5)
        assert exc.value.min_sec == 1

    def test_close_session_clears_failed_finalize_state(self, conn):
        sess = recordings.start_session()
        sid = sess["id"]
        recordings.append_chunk(conn, sid, b"\x00" * 10, 0)

        with pytest.raises(recordings.RecordingTooShortError):
            recordings.finalize(conn, sid, duration_sec=0.5)

        assert recordings.get_active_session_count() == 1
        recordings.close_session(sid)
        assert recordings.get_session(sid) is None
        assert recordings.get_active_session_count() == 0


class TestTryStartSession:
    def test_returns_none_when_another_session_is_active(self):
        first = recordings.try_start_session(title="one")
        assert first is not None

        second = recordings.try_start_session(title="two")
        assert second is None


class TestSeq0CreatesNote:
    def test_seq0_creates_recording_status_note(self, conn):
        """N1: append_chunk(seq=0) writes a Note row with status='recording'."""
        sess = recordings.start_session(title="demo")
        sid = sess["id"]
        r = recordings.append_chunk(conn, sid, b"\x00" * 10, 0)
        note_id = r["noteId"]
        assert note_id is not None
        doc = db_mod.get_note(conn, note_id)
        assert doc is not None
        assert doc["status"] == "recording"
        assert doc["title"] == "demo"

    def test_non_zero_seq_does_not_create_note_when_missing_seq0(self, conn):
        """seq=1 before seq=0 must NOT create a note (it'll error at finalize)."""
        sess = recordings.start_session()
        sid = sess["id"]
        r = recordings.append_chunk(conn, sid, b"\x00" * 10, 1)
        assert r["noteId"] is None

    def test_finalize_updates_status_to_pending(self, conn):
        sess = recordings.start_session(title="x")
        sid = sess["id"]
        recordings.append_chunk(conn, sid, b"\x00" * 10, 0)
        recordings.append_chunk(conn, sid, b"\x00" * 10, 1)
        out = recordings.finalize(conn, sid, duration_sec=20.0)
        doc = db_mod.get_note(conn, out["noteId"])
        assert doc["status"] == "pending"


class TestUnknownSession:
    def test_append_chunk_unknown_session_raises(self, conn):
        with pytest.raises(KeyError):
            recordings.append_chunk(conn, "no-such", b"", 0)

    def test_finalize_unknown_session_raises(self, conn):
        with pytest.raises(KeyError):
            recordings.finalize(conn, "no-such")
