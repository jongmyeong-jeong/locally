"""End-to-end flow tests: upload → transcribe → summarize.

Uses TestClient + mocked transcribe.run / summarize.run_ai so no real
model inference runs. Validates the full HTTP/SSE pipeline.
"""
from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

from app import server_jobs
from app.server import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_sse(body: bytes) -> list[dict]:
    """Parse raw SSE bytes into list of {event, data} dicts."""
    events: list[dict] = []
    current: dict = {}
    for raw_line in body.decode("utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("event:"):
            current["event"] = line[len("event:"):].strip()
        elif line.startswith("data:"):
            current["data"] = json.loads(line[len("data:"):].strip())
        elif line == "" and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return [e for e in events if "event" in e]


def _upload_audio(client: TestClient, audio_bytes: bytes, title: str = "e2e test") -> str:
    r = client.post(
        "/api/documents",
        data={"title": title},
        files={"file": ("test.m4a", io.BytesIO(audio_bytes), "audio/mp4")},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_jobs():
    server_jobs._JOBS.clear()
    yield
    server_jobs._JOBS.clear()


@pytest.fixture
def client(tmp_home):  # noqa: ARG001
    return TestClient(create_app())


@pytest.fixture
def mock_transcribe(monkeypatch):
    """Replace transcribe.run with a fast stub returning fixed text."""
    import app.transcribe as t

    def _fake_run(audio_path, *, model_dir=None, prompt=None, progress_cb=None):
        if progress_cb:
            progress_cb({"percent": 0.5, "segment_count": 1, "elapsed_sec": 0.1})
            progress_cb({"percent": 1.0, "segment_count": 2, "elapsed_sec": 0.2})
        return "안녕하세요 테스트입니다.", [
            {"start": 0.0, "end": 1.5, "text": "안녕하세요"},
            {"start": 1.5, "end": 3.0, "text": "테스트입니다."},
        ]

    monkeypatch.setattr(t, "run", _fake_run)


# ---------------------------------------------------------------------------
# Tests: Upload
# ---------------------------------------------------------------------------


class TestUpload:
    def test_upload_m4a_creates_document(self, client, tmp_home):
        doc_id = _upload_audio(client, b"\x00" * 256)
        r = client.get(f"/api/documents/{doc_id}")
        assert r.status_code == 200
        doc = r.json()
        assert doc["id"] == doc_id
        assert doc["audioPath"] is not None

    def test_upload_sets_title(self, client, tmp_home):
        doc_id = _upload_audio(client, b"\x00" * 128, title="회의록")
        doc = client.get(f"/api/documents/{doc_id}").json()
        assert doc["title"] == "회의록"

    def test_upload_unsupported_ext_returns_415(self, client, tmp_home):
        r = client.post(
            "/api/documents",
            files={"file": ("bad.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
        )
        assert r.status_code == 415

    def test_upload_without_file_creates_doc(self, client, tmp_home):
        r = client.post("/api/documents", json={"title": "no audio"})
        assert r.status_code == 201
        assert r.json()["audioPath"] is None


# ---------------------------------------------------------------------------
# Tests: Transcribe SSE
# ---------------------------------------------------------------------------


class TestTranscribeFlow:
    def test_transcribe_emits_progress_and_complete(self, client, tmp_home, mock_transcribe):
        doc_id = _upload_audio(client, b"\x00" * 256)

        with client.stream("POST", f"/api/documents/{doc_id}/transcribe") as resp:
            assert resp.status_code == 200
            body = resp.read()

        events = _parse_sse(body)
        names = [e["event"] for e in events]
        assert "progress" in names
        assert "complete" in names

    def test_transcribe_complete_has_transcript_path(self, client, tmp_home, mock_transcribe):
        doc_id = _upload_audio(client, b"\x00" * 256)

        with client.stream("POST", f"/api/documents/{doc_id}/transcribe") as resp:
            body = resp.read()

        events = _parse_sse(body)
        complete = next(e for e in events if e["event"] == "complete")
        assert "transcriptPath" in complete["data"]

    def test_transcript_readable_after_transcribe(self, client, tmp_home, mock_transcribe):
        doc_id = _upload_audio(client, b"\x00" * 256)

        with client.stream("POST", f"/api/documents/{doc_id}/transcribe") as resp:
            resp.read()

        r = client.get(f"/api/documents/{doc_id}/transcript")
        assert r.status_code == 200
        assert "안녕하세요" in r.json()["content"]

    def test_transcribe_without_audio_returns_400(self, client, tmp_home):
        r = client.post("/api/documents", json={"title": "no audio"})
        doc_id = r.json()["id"]
        r2 = client.post(f"/api/documents/{doc_id}/transcribe")
        assert r2.status_code == 400

    def test_transcribe_updates_document_status(self, client, tmp_home, mock_transcribe):
        doc_id = _upload_audio(client, b"\x00" * 256)

        with client.stream("POST", f"/api/documents/{doc_id}/transcribe") as resp:
            resp.read()

        doc = client.get(f"/api/documents/{doc_id}").json()
        assert doc["status"] == "transcribed"


# ---------------------------------------------------------------------------
# Tests: Summarize SSE (no-AI path)
# ---------------------------------------------------------------------------


class TestSummarizeFlow:
    def test_summarize_no_ai_emits_prompt_ready(self, client, tmp_home, mock_transcribe):
        doc_id = _upload_audio(client, b"\x00" * 256)
        with client.stream("POST", f"/api/documents/{doc_id}/transcribe") as resp:
            resp.read()

        with client.stream(
            "POST",
            f"/api/documents/{doc_id}/summarize",
            json={"ai": "none"},
        ) as resp:
            assert resp.status_code == 200
            body = resp.read()

        events = _parse_sse(body)
        names = [e["event"] for e in events]
        assert "prompt_ready" in names

    def test_summarize_prompt_ready_contains_transcript(self, client, tmp_home, mock_transcribe):
        doc_id = _upload_audio(client, b"\x00" * 256)
        with client.stream("POST", f"/api/documents/{doc_id}/transcribe") as resp:
            resp.read()

        with client.stream(
            "POST",
            f"/api/documents/{doc_id}/summarize",
            json={"ai": "none"},
        ) as resp:
            body = resp.read()

        events = _parse_sse(body)
        prompt_ready = next(e for e in events if e["event"] == "prompt_ready")
        assert "안녕하세요" in prompt_ready["data"]["transcript"]

    def test_summarize_without_transcript_returns_400(self, client, tmp_home):
        r = client.post("/api/documents", json={"title": "no transcript"})
        doc_id = r.json()["id"]
        r2 = client.post(
            f"/api/documents/{doc_id}/summarize",
            json={"ai": "none"},
        )
        assert r2.status_code == 400
