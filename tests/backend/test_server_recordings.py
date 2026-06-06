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
    """Clear module-level finalize-task registry between tests."""
    server_mod._FINALIZE_TASKS.clear()
    yield
    server_mod._FINALIZE_TASKS.clear()


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
# Batch transcribe helpers (used by multiple test classes below)
# ---------------------------------------------------------------------------


def _make_batch_result(text: str, *, partial: bool = False, all_fail: bool = False):
    """Build a minimal BatchResult for monkeypatching."""
    from app.batch_transcribe import BatchResult, PieceResult

    if all_fail:
        pieces = [
            PieceResult(
                seq=0, start_ms=0, end_ms=30_000,
                ok=False, text=None, segments=[], error_type="server_error",
            )
        ]
        return BatchResult(pieces=pieces, failed_ranges=[{"start_ms": 0, "end_ms": 30_000}])

    if partial:
        pieces = [
            PieceResult(
                seq=0, start_ms=0, end_ms=15_000,
                ok=True, text=text, segments=[], error_type=None,
            ),
            PieceResult(
                seq=1, start_ms=15_000, end_ms=30_000,
                ok=False, text=None, segments=[], error_type="server_error",
            ),
        ]
        return BatchResult(
            pieces=pieces,
            failed_ranges=[{"start_ms": 15_000, "end_ms": 30_000}],
        )

    pieces = [
        PieceResult(
            seq=0, start_ms=0, end_ms=30_000,
            ok=True, text=text, segments=[{"start_ms": 0, "end_ms": 1000, "text": text}],
            error_type=None,
        )
    ]
    return BatchResult(pieces=pieces, failed_ranges=[])


def _patch_batch_transcribe_success(monkeypatch, text: str):
    result = _make_batch_result(text)
    monkeypatch.setattr(
        "app.server.batch_transcribe.run_batch_transcription",
        lambda *_a, **_kw: result,
    )
    return result


def _patch_batch_transcribe_partial(monkeypatch, text: str):
    result = _make_batch_result(text, partial=True)
    monkeypatch.setattr(
        "app.server.batch_transcribe.run_batch_transcription",
        lambda *_a, **_kw: result,
    )
    return result


def _patch_batch_transcribe_all_fail(monkeypatch):
    result = _make_batch_result("", all_fail=True)
    monkeypatch.setattr(
        "app.server.batch_transcribe.run_batch_transcription",
        lambda *_a, **_kw: result,
    )
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestChunkFinalize:
    def test_chunk_finalize_3_chunks(self, client, monkeypatch):
        _patch_batch_transcribe_success(monkeypatch, "transcribed text")

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
        """Chunk endpoint uses streaming path without calling UploadFile.read()."""
        app = create_app()
        with TestClient(app):  # runs lifespan
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
# BA: finalize_twice_second_returns_404
# ---------------------------------------------------------------------------


class TestFinalizeIdempotent:
    def test_finalize_twice_second_returns_404(self, client, monkeypatch):
        """Second finalize call with the same session_id returns 404."""
        _patch_batch_transcribe_success(monkeypatch, "hello")

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
# AC1: N chunk uploads → batch_transcribe not called during chunk uploads
# ---------------------------------------------------------------------------


class TestChunksDoNotCallBatch:
    def test_chunk_uploads_do_not_invoke_batch_transcribe(self, client, monkeypatch):
        """AC1: run_batch_transcription called 0 times during chunk uploads."""
        calls: list = []

        def _spy(*_a, **_kw):
            calls.append(1)
            return _make_batch_result("text")

        monkeypatch.setattr("app.server.batch_transcribe.run_batch_transcription", _spy)

        sid = client.post("/api/recordings", json={"title": "noop"}).json()["id"]
        for seq in range(3):
            r = _upload_chunk(client, sid, seq, b"\x00" * 64)
            assert r.status_code == 200

        assert calls == [], f"batch_transcribe called {len(calls)} times during chunk upload"


# ---------------------------------------------------------------------------
# Batch transcription finalize tests
# ---------------------------------------------------------------------------


class TestFinalizeSingleSuccess:
    def test_complete_payload_and_md(self, client, monkeypatch):
        """Single success: complete payload has partialFailure=False, .md content matches."""
        from app import paths

        _patch_batch_transcribe_success(monkeypatch, "hello world")

        sid = client.post("/api/recordings", json={"title": "success-test"}).json()["id"]
        chunk_resp = _upload_chunk(client, sid, 0, b"\x00" * 128)
        note_id = chunk_resp.json()["noteId"]

        r = client.post(f"/api/recordings/{sid}/finalize", json={"durationSec": 5.0})
        assert r.status_code == 200, r.text
        events = _parse_sse(r.text)
        complete_events = [e for e in events if e.get("event") == "complete"]
        assert complete_events, f"No complete event; events={events}"
        body = complete_events[0]["data"]

        assert body["partialFailure"] is False
        assert body["failedRanges"] == []
        assert body["noteId"] == note_id
        assert Path(body["audioPath"]).exists()

        transcript_path = Path(body["transcriptPath"])
        assert transcript_path.exists()
        content = transcript_path.read_text(encoding="utf-8")
        assert "hello world" in content

        # Sidecar file should exist (seq=0 has segments).
        sidecar_files = list(paths.audio_dir().glob(f"segments-{note_id}-*.json"))
        assert sidecar_files, "No sidecar JSON written"

        # Chunk row should be status='success'.
        from app import db as _db
        from app import recording_chunks as _rc
        with _db.open_db() as conn:
            chunks = _rc.get_chunks(conn, note_id)
        assert len(chunks) == 1
        assert chunks[0]["status"] == "success"

    def test_note_status_transcribed(self, client, monkeypatch):
        """DB note status = 'transcribed' after successful finalize."""
        _patch_batch_transcribe_success(monkeypatch, "text")

        sid = client.post("/api/recordings", json={}).json()["id"]
        chunk_resp = _upload_chunk(client, sid, 0, b"\x00" * 128)
        note_id = chunk_resp.json()["noteId"]

        r = client.post(f"/api/recordings/{sid}/finalize", json={"durationSec": 5.0})
        assert r.status_code == 200
        events = _parse_sse(r.text)
        assert any(e.get("event") == "complete" for e in events)

        note = client.get(f"/api/notes/{note_id}").json()
        assert note["status"] == "transcribed"


class TestFinalizePartialFailure:
    def test_partial_failure_payload(self, client, monkeypatch):
        """Partial failure: complete event with partialFailure=True and failedRanges."""
        _patch_batch_transcribe_partial(monkeypatch, "good part")

        sid = client.post("/api/recordings", json={"title": "partial"}).json()["id"]
        _upload_chunk(client, sid, 0, b"\x00" * 128)

        r = client.post(f"/api/recordings/{sid}/finalize", json={"durationSec": 30.0})
        assert r.status_code == 200
        events = _parse_sse(r.text)
        complete_events = [e for e in events if e.get("event") == "complete"]
        assert complete_events, f"No complete event; events={events}"
        body = complete_events[0]["data"]

        assert body["partialFailure"] is True
        assert len(body["failedRanges"]) == 1
        assert body["failedRanges"][0] == {"start_ms": 15_000, "end_ms": 30_000}

    def test_partial_failure_md_contains_marker(self, client, monkeypatch):
        """Partial failure: .md contains [전사 실패 구간] marker."""
        _patch_batch_transcribe_partial(monkeypatch, "good part")

        sid = client.post("/api/recordings", json={"title": "partial-md"}).json()["id"]
        _upload_chunk(client, sid, 0, b"\x00" * 128)

        r = client.post(f"/api/recordings/{sid}/finalize", json={"durationSec": 30.0})
        assert r.status_code == 200
        events = _parse_sse(r.text)
        complete_events = [e for e in events if e.get("event") == "complete"]
        assert complete_events
        body = complete_events[0]["data"]

        transcript_path = Path(body["transcriptPath"])
        content = transcript_path.read_text(encoding="utf-8")
        assert "전사 실패 구간" in content
        assert "good part" in content

    def test_partial_failure_chunk_rows(self, client, monkeypatch):
        """Partial failure: success chunk has status='success', failed chunk status='failed'."""
        _patch_batch_transcribe_partial(monkeypatch, "good part")

        sid = client.post("/api/recordings", json={}).json()["id"]
        chunk_resp = _upload_chunk(client, sid, 0, b"\x00" * 128)
        note_id = chunk_resp.json()["noteId"]

        r = client.post(f"/api/recordings/{sid}/finalize", json={"durationSec": 30.0})
        assert r.status_code == 200
        events = _parse_sse(r.text)
        assert any(e.get("event") == "complete" for e in events)

        from app import db as _db
        from app import recording_chunks as _rc
        with _db.open_db() as conn:
            chunks = _rc.get_chunks(conn, note_id)

        assert len(chunks) == 2
        statuses = {c["seq"]: c["status"] for c in chunks}
        assert statuses[0] == "success"
        assert statuses[1] == "failed"

    def test_partial_failure_download_failed_ranges(self, client, monkeypatch):
        """Download endpoint derives failedRanges from chunk rows with status='failed'."""
        _patch_batch_transcribe_partial(monkeypatch, "good part")

        sid = client.post("/api/recordings", json={}).json()["id"]
        chunk_resp = _upload_chunk(client, sid, 0, b"\x00" * 128)
        note_id = chunk_resp.json()["noteId"]

        r = client.post(f"/api/recordings/{sid}/finalize", json={"durationSec": 30.0})
        assert r.status_code == 200
        events = _parse_sse(r.text)
        assert any(e.get("event") == "complete" for e in events)

        # Download endpoint reads failed ranges from DB chunk rows.
        r2 = client.get(f"/api/notes/{note_id}/download")
        assert r2.status_code == 200
        content = r2.text
        assert "전사 실패 구간" in content


class TestFinalizeAllFail:
    def test_all_fail_emits_error_event(self, client, monkeypatch):
        """All-fail: SSE error event with error='transcription_failed'."""
        _patch_batch_transcribe_all_fail(monkeypatch)

        sid = client.post("/api/recordings", json={}).json()["id"]
        _upload_chunk(client, sid, 0, b"\x00" * 128)

        r = client.post(f"/api/recordings/{sid}/finalize", json={"durationSec": 5.0})
        assert r.status_code == 200
        events = _parse_sse(r.text)
        error_events = [e for e in events if e.get("event") == "error"]
        assert error_events, f"No error event; events={events}"
        assert error_events[0]["data"]["error"] == "transcription_failed"

    def test_all_fail_note_status(self, client, monkeypatch):
        """All-fail: DB note status = 'transcription_failed'; no .md written."""
        from app import paths
        _patch_batch_transcribe_all_fail(monkeypatch)

        sid = client.post("/api/recordings", json={}).json()["id"]
        chunk_resp = _upload_chunk(client, sid, 0, b"\x00" * 128)
        note_id = chunk_resp.json()["noteId"]

        r = client.post(f"/api/recordings/{sid}/finalize", json={"durationSec": 5.0})
        assert r.status_code == 200

        note = client.get(f"/api/notes/{note_id}").json()
        assert note["status"] == "transcription_failed"

        md_files = list(paths.transcripts_dir().glob("*.md"))
        assert md_files == [], f"Unexpected .md files: {md_files}"


# ---------------------------------------------------------------------------
# Lifespan: startup recovery of 'finalizing' notes
# ---------------------------------------------------------------------------


class TestLifespanStartupRecovery:
    def test_startup_recovers_finalizing_note(self, tmp_home, monkeypatch):
        """Startup recovery: note with status='finalizing' is flipped to 'transcription_failed'."""
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        from app import db as _db

        # Pre-seed a 'finalizing' note directly in the DB.
        with _db.open_db() as conn:
            note = _db.create_note(conn, title="stuck", status="finalizing")
            note_id = note["id"]

        # Starting the app via TestClient triggers lifespan startup.
        app = create_app()
        with TestClient(app):
            pass

        with _db.open_db() as conn:
            recovered = _db.get_note(conn, note_id)
        assert recovered["status"] == "transcription_failed", (
            f"Expected 'transcription_failed', got {recovered['status']!r}"
        )
