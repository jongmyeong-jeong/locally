"""Tests for the live recording flow: POST /api/recordings 503/409, finalize SSE,
and regression guard for the file-upload path.

Cases:
  1. POST /api/recordings with no model → 503 {"error": "model_not_installed"}
  2. Two concurrent POST /api/recordings → second returns 409 {"error": "concurrent_recording"}
  3. Full happy-path: start → finalize with mocked transcription → 'complete' SSE event,
     document status='transcribed'.
  4. File-upload regression: POST /api/documents + POST .../transcribe → 'transcribed'
     via the old (non-live) path.
"""
from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

from app import db as db_mod
from app import recordings
from app import server as server_mod
from app import transcribe as transcribe_mod
from app.server import create_app


# ---------------------------------------------------------------------------
# SSE parsing helper (borrowed from test_server_recordings.py)
# ---------------------------------------------------------------------------


def _parse_sse(text: str) -> list[dict]:
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
# Autouse fixtures (mirror test_server_recordings.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_sessions():
    recordings._SESSIONS.clear()
    yield
    recordings._SESSIONS.clear()


@pytest.fixture(autouse=True)
def _reset_live_state():
    server_mod._VAD_DETECTORS.clear()
    server_mod._VAD_LOCKS.clear()
    server_mod._CHUNK_SEQ.clear()
    yield
    server_mod._VAD_DETECTORS.clear()
    server_mod._VAD_LOCKS.clear()
    server_mod._CHUNK_SEQ.clear()


# ---------------------------------------------------------------------------
# Case 1 — 503 when no model installed
# ---------------------------------------------------------------------------


class TestCreateRecording503:
    def test_post_recordings_503_when_no_model(self, tmp_home, monkeypatch):
        """_any_model_ready() returns False → POST /api/recordings → 503."""
        monkeypatch.setattr(server_mod, "_any_model_ready", lambda: False)
        app = create_app()
        with TestClient(app) as c:
            r = c.post("/api/recordings", json={})
        assert r.status_code == 503
        assert r.json()["error"] == "model_not_installed"


# ---------------------------------------------------------------------------
# Case 2 — 409 when concurrent recording
# ---------------------------------------------------------------------------


class TestCreateRecording409:
    def test_post_recordings_409_when_concurrent(self, tmp_home, monkeypatch):
        """Second POST /api/recordings while first is active → 409."""
        monkeypatch.setattr(server_mod, "_any_model_ready", lambda: True)
        app = create_app()
        with TestClient(app) as c:
            r1 = c.post("/api/recordings", json={})
            assert r1.status_code == 201
            r2 = c.post("/api/recordings", json={})
            assert r2.status_code == 409
            assert r2.json()["error"] == "concurrent_recording"


# ---------------------------------------------------------------------------
# Case 3 — Happy-path finalize: shape + complete event + status='transcribed'
#
# Compromise: rather than exercising VAD-boundary pre-transcription (which
# requires real PCM decoding), we skip the chunk upload phase and call
# finalize directly on a fresh session.  The finalize path handles "no
# chunks queued" gracefully (transcript_text = ""), writes the transcript
# file, and emits the 'complete' event.  This verifies the SSE contract and
# DB state without depending on ffmpeg/VAD in CI.
# ---------------------------------------------------------------------------


class TestFinalizeLiveHappyPath:
    def test_finalize_live_happy_path(self, tmp_home, monkeypatch):
        """Start session → finalize → 'complete' SSE event → document status='transcribed'.

        Simplification: no chunk uploads (no VAD boundaries → empty transcript).
        The finalize path still emits progress + complete events and sets
        status='transcribed'. This is the minimal verifiable contract without
        requiring a working audio pipeline in CI.
        """
        monkeypatch.setattr(server_mod, "_any_model_ready", lambda: True)

        # Mock transcribe.run so even if it's somehow called, it doesn't need a model.
        monkeypatch.setattr(transcribe_mod, "run", lambda *a, **kw: ("mocked text", []))

        app = create_app()
        with TestClient(app) as c:
            # 1. Start recording session.
            r_start = c.post("/api/recordings", json={"title": "live test"})
            assert r_start.status_code == 201
            sid = r_start.json()["id"]

            # 2. Upload one chunk so a document row is created (seq=0 creates the doc).
            chunk_resp = c.post(
                f"/api/recordings/{sid}/chunk",
                data={"seq": "0"},
                files={"chunk": ("chunk.webm", io.BytesIO(b"\x1a\x45\xdf\xa3" + b"\x00" * 124), "application/octet-stream")},
            )
            assert chunk_resp.status_code == 200
            doc_id = chunk_resp.json()["documentId"]
            assert doc_id  # document was created on seq=0

            # 3. Finalize — returns SSE stream.
            r_fin = c.post(
                f"/api/recordings/{sid}/finalize",
                json={"durationSec": 30.0},
            )
            assert r_fin.status_code == 200

            # 4. Parse SSE events.
            events = _parse_sse(r_fin.text)
            event_names = [e.get("event") for e in events]

            # Must contain at least one progress event and a complete event.
            assert "progress" in event_names, f"No progress event; events={event_names}"
            complete_events = [e for e in events if e.get("event") == "complete"]
            assert complete_events, f"No 'complete' event; events={event_names}"

            body = complete_events[0]["data"]
            # complete event must carry the document id.
            assert body.get("documentId") == doc_id

            # 5. Verify DB status.
            with db_mod.open_db() as conn:
                doc = db_mod.get_document(conn, doc_id)
            assert doc is not None
            assert doc["status"] == "transcribed", (
                f"Expected 'transcribed', got {doc['status']!r}"
            )

            # 6. The status sequence must NOT contain 'pending' (live path skips it).
            # We can't observe intermediate states post-hoc; the final state being
            # 'transcribed' (not 'pending') is the meaningful assertion.
            assert doc["status"] != "pending"


# ---------------------------------------------------------------------------
# Case 4 — File-upload path regression
# ---------------------------------------------------------------------------


class TestFileUploadRegression:
    def test_file_upload_path_regression(self, tmp_home, mock_platform, monkeypatch):
        """POST /api/documents + POST .../transcribe → pending → transcribing → transcribed.

        Confirms the non-live transcription path is unaffected by live changes.
        """
        mock_platform("Darwin", "arm64")

        FAKE_SEGMENTS = [{"start": 0.0, "end": 2.0, "text": "hello world"}]
        FAKE_TEXT = "hello world"

        def _fake_run(audio_path, *, model_dir=None, prompt=None, progress_cb=None, **_kw):
            if progress_cb:
                progress_cb({"percent": 1.0, "segment_count": 1, "elapsed_sec": 0.1})
            return FAKE_TEXT, FAKE_SEGMENTS

        monkeypatch.setattr(transcribe_mod, "run", _fake_run)

        audio = tmp_home / "test.m4a"
        audio.write_bytes(b"\x00" * 128)

        app = create_app()
        with TestClient(app) as c:
            # Create document via old file-upload path.
            r_doc = c.post(
                "/api/documents",
                json={"title": "file upload test", "audioPath": str(audio)},
            )
            assert r_doc.status_code == 201
            doc_id = r_doc.json()["id"]

            # Trigger transcription SSE.
            r_tx = c.post(f"/api/documents/{doc_id}/transcribe")
            assert r_tx.status_code == 200

            events = _parse_sse(r_tx.text)
            complete_events = [e for e in events if e.get("event") == "complete"]
            assert complete_events, f"No 'complete' event; events={events}"

            # DB status must be 'transcribed'.
            with db_mod.open_db() as conn:
                doc = db_mod.get_document(conn, doc_id)
            assert doc is not None
            assert doc["status"] == "transcribed"
            assert doc["transcriptPath"] is not None
