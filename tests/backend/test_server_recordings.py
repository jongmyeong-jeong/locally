"""AC-7 tests for chunk upload / finalize / duplicate / gap / too-short."""
from __future__ import annotations

import io
import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import Headers, UploadFile

from app import recordings
from app.server import create_app


@pytest.fixture(autouse=True)
def _reset_sessions():
    recordings._SESSIONS.clear()
    yield
    recordings._SESSIONS.clear()


@pytest.fixture
def client(tmp_home):  # noqa: ARG001
    app = create_app()
    with TestClient(app) as c:
        yield c


def _upload_chunk(client, session_id: str, seq: int, payload: bytes):
    return client.post(
        f"/api/recordings/{session_id}/chunk",
        data={"seq": str(seq)},
        files={"chunk": ("chunk.webm", io.BytesIO(payload), "application/octet-stream")},
    )


class TestChunkFinalize:
    def test_chunk_finalize_3_chunks(self, client):
        start = client.post("/api/recordings", json={"title": "demo"})
        assert start.status_code == 201
        sid = start.json()["id"]

        for seq in range(3):
            r = _upload_chunk(client, sid, seq, b"\x00" * 128)
            assert r.status_code == 200, r.text
            assert r.json()["documentId"]

        # Finalize with a 30s duration (>1s floor).
        r = client.post(
            f"/api/recordings/{sid}/finalize",
            json={"durationSec": 30.0},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["documentId"]
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


class TestGapAtFinalize:
    def test_gap_at_finalize_returns_400_with_missing_list(self, client):
        sid = client.post("/api/recordings", json={}).json()["id"]
        _upload_chunk(client, sid, 0, b"\x00" * 32)
        _upload_chunk(client, sid, 2, b"\x00" * 32)
        r = client.post(
            f"/api/recordings/{sid}/finalize", json={"durationSec": 30.0}
        )
        assert r.status_code == 400
        body = r.json()
        assert body["error"] == "missing chunks"
        assert body["missing"] == [1]


class TestShortRecording:
    def test_finalize_under_1s_rejects(self, client):
        sid = client.post("/api/recordings", json={}).json()["id"]
        _upload_chunk(client, sid, 0, b"\x00" * 32)
        r = client.post(
            f"/api/recordings/{sid}/finalize", json={"durationSec": 0.5}
        )
        assert r.status_code == 400
        body = r.json()
        assert body["error"] == "recording too short"


class TestSeq0CreatesDocument:
    def test_seq0_creates_recording_document(self, client):
        """N1: seq=0 upload immediately creates a Document row (status='recording')."""
        sid = client.post("/api/recordings", json={"title": "demo"}).json()["id"]
        _upload_chunk(client, sid, 0, b"\x00" * 32)

        # The session now has a document_id; fetch it via list.
        docs = client.get("/api/documents").json()
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


class TestStreamingChunkAppend:
    def test_streaming_append_avoids_buffering_whole_upload(self, tmp_home):
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
        assert body["documentId"]
