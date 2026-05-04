"""AC-7 tests for chunk upload / finalize / duplicate / gap / too-short."""
from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import Headers, UploadFile

from app import recordings
from app import server as server_mod
from app.server import create_app


# ---------------------------------------------------------------------------
# SSE parsing helper
# ---------------------------------------------------------------------------


def _parse_sse(text: str) -> list[dict]:
    """Parse SSE response text into a list of event dicts with 'event'/'data'."""
    events: list[dict] = []
    current: dict = {}
    for raw in text.splitlines():
        if raw.startswith(":"):
            continue
        if raw == "":
            if current:
                events.append(current)
                current = {}
            continue
        if raw.startswith("event: "):
            current["event"] = raw[len("event: "):]
        elif raw.startswith("data: "):
            try:
                current["data"] = json.loads(raw[len("data: "):])
            except json.JSONDecodeError:
                current["data"] = raw[len("data: "):]
    if current:
        events.append(current)
    return events


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_sessions():
    recordings._SESSIONS.clear()
    yield
    recordings._SESSIONS.clear()


@pytest.fixture(autouse=True)
def _reset_live_state():
    """Clear module-level VAD/queue state between tests."""
    server_mod._VAD_DETECTORS.clear()
    server_mod._VAD_LOCKS.clear()
    server_mod._CHUNK_SEQ.clear()
    yield
    server_mod._VAD_DETECTORS.clear()
    server_mod._VAD_LOCKS.clear()
    server_mod._CHUNK_SEQ.clear()


@pytest.fixture
def mock_groq_key(monkeypatch):
    """Set GROQ_API_KEY so POST /api/recordings passes the 503 guard."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key")


@pytest.fixture
def client(tmp_home, mock_groq_key):  # noqa: ARG001
    app = create_app()
    with TestClient(app) as c:
        yield c


def _upload_chunk(client, session_id: str, seq: int, payload: bytes):
    return client.post(
        f"/api/recordings/{session_id}/chunk",
        data={"seq": str(seq)},
        files={"chunk": ("chunk.webm", io.BytesIO(payload), "application/octet-stream")},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestChunkFinalize:
    def test_chunk_finalize_3_chunks(self, client):
        start = client.post("/api/recordings", json={"title": "demo"})
        assert start.status_code == 201
        sid = start.json()["id"]

        for seq in range(3):
            r = _upload_chunk(client, sid, seq, b"\x00" * 128)
            assert r.status_code == 200, r.text
            assert r.json()["noteId"]

        # Finalize with a 30s duration (>1s floor) — now returns SSE stream (200).
        r = client.post(
            f"/api/recordings/{sid}/finalize",
            json={"durationSec": 30.0},
        )
        assert r.status_code == 200, r.text
        events = _parse_sse(r.text)
        complete_events = [e for e in events if e.get("event") == "complete"]
        assert complete_events, f"No 'complete' SSE event found; events={events}"
        body = complete_events[0]["data"]
        assert body["noteId"]
        assert Path(body["audioPath"]).exists()


class TestDuplicateSeq:
    def test_duplicate_seq_returns_409(self, client):
        sid = client.post("/api/recordings", json={}).json()["id"]
        r1 = _upload_chunk(client, sid, 0, b"\x00" * 32)
        assert r1.status_code == 200
        r2 = _upload_chunk(client, sid, 0, b"\x00" * 32)
        assert r2.status_code == 409
        body = r2.json()
        assert body["error"] == "duplicate seq"
        assert body["seq"] == 0

    def test_duplicate_seq_does_not_append_bytes_twice(self, client):
        sid = client.post("/api/recordings", json={}).json()["id"]
        first = b"A" * 32
        second = b"B" * 32

        r1 = _upload_chunk(client, sid, 0, first)
        assert r1.status_code == 200

        tmp_path = recordings._session_tmp_path(sid)
        assert tmp_path.read_bytes() == first

        r2 = _upload_chunk(client, sid, 0, second)
        assert r2.status_code == 409
        assert tmp_path.read_bytes() == first

    def test_negative_seq_returns_400(self, client):
        sid = client.post("/api/recordings", json={}).json()["id"]
        r = _upload_chunk(client, sid, -1, b"\x00" * 32)
        assert r.status_code == 400
        assert r.json()["detail"] == "seq must be >= 0"


class TestGapAtFinalize:
    def test_gap_at_finalize_returns_400_with_missing_list(self, client):
        sid = client.post("/api/recordings", json={}).json()["id"]
        _upload_chunk(client, sid, 0, b"\x00" * 32)
        _upload_chunk(client, sid, 2, b"\x00" * 32)
        # Finalize now returns SSE 200 with an error event for gap errors.
        r = client.post(
            f"/api/recordings/{sid}/finalize", json={"durationSec": 30.0}
        )
        assert r.status_code == 200, r.text
        events = _parse_sse(r.text)
        error_events = [e for e in events if e.get("event") == "error"]
        assert error_events, f"No 'error' SSE event found; events={events}"
        data = error_events[0]["data"]
        assert data["error"] == "missing chunks"
        assert data["missing"] == [1]

        retry = client.post("/api/recordings", json={})
        assert retry.status_code == 201


class TestShortRecording:
    def test_finalize_under_1s_rejects(self, client):
        sid = client.post("/api/recordings", json={}).json()["id"]
        _upload_chunk(client, sid, 0, b"\x00" * 32)
        # Finalize now returns SSE 200 with an error event for too-short.
        r = client.post(
            f"/api/recordings/{sid}/finalize", json={"durationSec": 0.5}
        )
        assert r.status_code == 200, r.text
        events = _parse_sse(r.text)
        error_events = [e for e in events if e.get("event") == "error"]
        assert error_events, f"No 'error' SSE event found; events={events}"
        assert error_events[0]["data"]["error"] == "recording too short"

        retry = client.post("/api/recordings", json={})
        assert retry.status_code == 201

    def test_finalize_too_short_marks_note_transcription_failed(self, client):
        """AC6(A): duration < 1.0s finalize → DB status = transcription_failed."""
        # 1) Start a recording session.
        sid = client.post("/api/recordings", json={"title": "too short"}).json()["id"]
        # 2) Upload seq=0 so a note row is created (status='recording').
        chunk_resp = _upload_chunk(client, sid, 0, b"\x00" * 32)
        assert chunk_resp.status_code == 200
        note_id = chunk_resp.json()["noteId"]
        # Confirm note exists with status 'recording'.
        note_before = client.get(f"/api/notes/{note_id}").json()
        assert note_before["status"] == "recording"
        # 3) Finalize with durationSec=0.5 — SSE 200, error event.
        r = client.post(
            f"/api/recordings/{sid}/finalize", json={"durationSec": 0.5}
        )
        assert r.status_code == 200, r.text
        events = _parse_sse(r.text)
        error_events = [e for e in events if e.get("event") == "error"]
        assert error_events, f"No 'error' SSE event; events={events}"
        assert error_events[0]["data"]["error"] == "recording too short"
        # 4) Note status must be 'transcription_failed'.
        note_after = client.get(f"/api/notes/{note_id}").json()
        assert note_after["status"] == "transcription_failed"


class TestSeq0CreatesNote:
    def test_seq0_creates_recording_note(self, client):
        """N1: seq=0 upload immediately creates a Note row (status='recording')."""
        sid = client.post("/api/recordings", json={"title": "demo"}).json()["id"]
        _upload_chunk(client, sid, 0, b"\x00" * 32)

        # The session now has a note_id; fetch it via list.
        docs = client.get("/api/notes").json()
        demo = [d for d in docs if d["title"] == "demo"]
        assert len(demo) == 1
        assert demo[0]["status"] == "recording"


class TestUnknownSession:
    def test_chunk_unknown_session_404(self, client):
        r = _upload_chunk(client, "no-such", 0, b"\x00" * 16)
        assert r.status_code == 404

    def test_finalize_unknown_session_404(self, client):
        r = client.post("/api/recordings/no-such/finalize", json={})
        assert r.status_code == 404


class TestGroqKeyMissing:
    def test_create_recording_returns_503_when_no_key(self, tmp_home, monkeypatch):
        """503 when GROQ_API_KEY is not set."""
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        app = create_app()
        with TestClient(app) as c:
            r = c.post("/api/recordings", json={})
        assert r.status_code == 503
        assert r.json()["error"] == "groq_api_key_missing"


class TestConcurrentRecording:
    def test_create_recording_returns_409_when_session_active(
        self, tmp_home, monkeypatch
    ):
        """409 when another recording session is already active."""
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        app = create_app()
        with TestClient(app) as c:
            r1 = c.post("/api/recordings", json={})
            assert r1.status_code == 201
            r2 = c.post("/api/recordings", json={})
            assert r2.status_code == 409
            assert r2.json()["error"] == "concurrent_recording"


class TestStreamingChunkAppend:
    def test_streaming_append_avoids_buffering_whole_upload(self, tmp_home):
        """When no VAD detector is registered (session created directly, not via HTTP),
        the chunk endpoint falls back to the original streaming path without calling
        UploadFile.read()."""
        app = create_app()
        route = next(
            route
            for route in app.router.routes
            if getattr(route, "path", None) == "/api/recordings/{session_id}/chunk"
        )
        endpoint = route.endpoint
        session = recordings.start_session(title="stream")
        upload = UploadFile(
            file=io.BytesIO(b"x" * (1024 * 1024 + 17)),
            filename="chunk.webm",
            headers=Headers({"content-type": "application/octet-stream"}),
        )

        async def _forbidden_read(*_args, **_kwargs):
            raise AssertionError("server endpoint should not call UploadFile.read()")

        upload.read = _forbidden_read  # type: ignore[method-assign]

        body = asyncio.run(endpoint(session["id"], upload, 0))
        assert body["bytes_written"] == 1024 * 1024 + 17
        assert body["noteId"]


# ---------------------------------------------------------------------------
# BA1–BA3: skipTranscribe finalize path
# ---------------------------------------------------------------------------


class TestSkipTranscribeFinalize:
    """BA1: skipTranscribe=true → audio moved, .md not created, status=audio_only."""

    def test_skip_transcribe_audio_moved_md_absent(self, client, tmp_path):
        """BA1: webm is moved to audio_dir; no .md file is written."""
        from app import paths

        # Start session and upload one chunk so a note row exists.
        sid = client.post("/api/recordings", json={"title": "skip-test"}).json()["id"]
        _upload_chunk(client, sid, 0, b"\x00" * 128)

        r = client.post(
            f"/api/recordings/{sid}/finalize",
            json={"durationSec": 5.0, "skipTranscribe": True},
        )
        assert r.status_code == 200, r.text
        events = _parse_sse(r.text)
        complete_events = [e for e in events if e.get("event") == "complete"]
        assert complete_events, f"No complete event; events={events}"
        body = complete_events[0]["data"]

        # Audio file must exist in audio_dir.
        assert body["status"] == "audio_only"
        assert body["transcriptPath"] is None
        audio_path = Path(body["audioPath"])
        assert audio_path.exists(), f"audio file missing: {audio_path}"

        # No .md file should have been created.
        md_files = list(paths.transcripts_dir().glob("*.md"))
        assert md_files == [], f"Unexpected .md files: {md_files}"

    def test_skip_transcribe_db_status_audio_only(self, client):
        """BA2: DB note status = 'audio_only' after skipTranscribe finalize."""
        sid = client.post("/api/recordings", json={"title": "skip-db"}).json()["id"]
        chunk_resp = _upload_chunk(client, sid, 0, b"\x00" * 128)
        note_id = chunk_resp.json()["noteId"]

        r = client.post(
            f"/api/recordings/{sid}/finalize",
            json={"durationSec": 5.0, "skipTranscribe": True},
        )
        assert r.status_code == 200, r.text
        events = _parse_sse(r.text)
        assert any(e.get("event") == "complete" for e in events)

        note = client.get(f"/api/notes/{note_id}").json()
        assert note["status"] == "audio_only"

    def test_finalize_twice_second_returns_404(self, client):
        """BA3: Second finalize call with the same session_id returns 404."""
        sid = client.post("/api/recordings", json={"title": "idem"}).json()["id"]
        _upload_chunk(client, sid, 0, b"\x00" * 128)

        r1 = client.post(
            f"/api/recordings/{sid}/finalize",
            json={"durationSec": 5.0},
        )
        assert r1.status_code == 200

        # Second finalize — session is gone.
        r2 = client.post(
            f"/api/recordings/{sid}/finalize",
            json={"durationSec": 5.0},
        )
        assert r2.status_code == 404


# ---------------------------------------------------------------------------
# BA4 booster: drain() spy — finalize passes retry_sleep_sec=5, max_retries=5
# ---------------------------------------------------------------------------


class TestFinalizeCallsDrainArgs:
    """BA4-ext: finalize handler calls sq.drain(retry_sleep_sec=5, max_retries=5)."""

    def test_finalize_calls_drain_with_5sec_5retries(self, client, monkeypatch):
        """Spy on SessionTranscribeQueue.drain to verify finalize passes correct args."""
        from unittest.mock import AsyncMock, call

        import app.transcribe_queue as tq_mod

        drain_calls: list[dict] = []
        original_drain = tq_mod.SessionTranscribeQueue.drain

        async def _spy_drain(self, retry_sleep_sec=60, max_retries=5):
            drain_calls.append({"retry_sleep_sec": retry_sleep_sec, "max_retries": max_retries})
            # Call through so the queue actually drains (avoids hanging).
            await original_drain(self, retry_sleep_sec=retry_sleep_sec, max_retries=max_retries)

        monkeypatch.setattr(tq_mod.SessionTranscribeQueue, "drain", _spy_drain)

        sid = client.post("/api/recordings", json={"title": "drain-spy"}).json()["id"]
        _upload_chunk(client, sid, 0, b"\x00" * 128)

        r = client.post(
            f"/api/recordings/{sid}/finalize",
            json={"durationSec": 5.0},
        )
        assert r.status_code == 200, r.text
        events = _parse_sse(r.text)
        assert any(e.get("event") == "complete" for e in events)

        assert len(drain_calls) >= 1, "drain() was never called during finalize"
        # The finalize path must pass retry_sleep_sec=5, max_retries=5.
        assert drain_calls[0] == {"retry_sleep_sec": 5, "max_retries": 5}, (
            f"drain() called with wrong args: {drain_calls[0]}"
        )
