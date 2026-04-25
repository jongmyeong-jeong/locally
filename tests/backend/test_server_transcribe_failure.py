"""AC6(B/C) regression: transcribe SSE producer → transcription_failed on bad outcomes.

AC6(B): transcribe.run returns ("", []) (no segments) → status=transcription_failed.
AC6(C): transcribe.run raises RuntimeError → status=transcription_failed.

SSE parsing pattern borrowed from test_server_transcribe_format.py.
monkeypatch target: app.transcribe.run (same as that file).
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app import transcribe as transcribe_mod
from app.server import create_app


def _parse_sse(text: str) -> list[dict]:
    """Parse SSE response text into a list of event dicts with 'event' and 'data'."""
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


def _create_doc_with_audio(client, tmp_home, title="테스트"):
    """Create a note row with a fake audio file; return note_id."""
    audio_path = tmp_home / "test.m4a"
    audio_path.write_bytes(b"\x00" * 128)
    r = client.post(
        "/api/notes",
        json={"title": title, "audioPath": str(audio_path)},
    )
    assert r.status_code == 201
    return r.json()["id"]


class TestTranscribeNoSegments:
    def test_transcribe_with_no_segments_marks_transcription_failed(
        self, tmp_home, mock_platform, monkeypatch
    ):
        """AC6(B): transcribe.run returns empty segments → status=transcription_failed."""
        mock_platform("Darwin", "arm64")

        def _fake_no_segments(audio_path, *, model_dir=None, prompt=None, progress_cb=None):
            return ("", [])

        monkeypatch.setattr(transcribe_mod, "run", _fake_no_segments)

        app = create_app()
        with TestClient(app) as c:
            note_id = _create_doc_with_audio(c, tmp_home)

            r = c.post(f"/api/notes/{note_id}/transcribe")
            assert r.status_code == 200

            # Consume SSE — must contain an 'error' event.
            events = _parse_sse(r.text)
            error_events = [e for e in events if e.get("event") == "error"]
            assert len(error_events) >= 1

            # DB status must be transcription_failed after SSE is consumed.
            doc = c.get(f"/api/notes/{note_id}").json()
            assert doc["status"] == "transcription_failed"


class TestTranscribeException:
    def test_transcribe_exception_marks_transcription_failed(
        self, tmp_home, mock_platform, monkeypatch
    ):
        """AC6(C): transcribe.run raises RuntimeError → status=transcription_failed."""
        mock_platform("Darwin", "arm64")

        def _fake_raise(audio_path, *, model_dir=None, prompt=None, progress_cb=None):
            raise RuntimeError("boom")

        monkeypatch.setattr(transcribe_mod, "run", _fake_raise)

        app = create_app()
        with TestClient(app) as c:
            note_id = _create_doc_with_audio(c, tmp_home)

            r = c.post(f"/api/notes/{note_id}/transcribe")
            assert r.status_code == 200

            # SSE must contain an 'error' event with the exception message.
            events = _parse_sse(r.text)
            error_events = [e for e in events if e.get("event") == "error"]
            assert len(error_events) >= 1
            assert "boom" in str(error_events[0].get("data", ""))

            # DB status must be transcription_failed.
            doc = c.get(f"/api/notes/{note_id}").json()
            assert doc["status"] == "transcription_failed"
