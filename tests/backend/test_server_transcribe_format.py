"""Tests for server.py transcribe formatter wiring (locally-2f5).

Verifies that when transcribe_mod.run returns segments,
the .md file and GET /transcript use format_transcript_markdown output.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import transcribe as transcribe_mod
from app.server import create_app
from app.transcript_format import format_transcript_markdown

FAKE_SEGMENTS = [
    {"start": 0.0, "end": 3.5, "text": "안녕하세요"},
    {"start": 3.5, "end": 7.2, "text": "오늘 회의 시작합니다"},
]
FAKE_TEXT = "안녕하세요 오늘 회의 시작합니다"
EXPECTED_CONTENT = format_transcript_markdown(FAKE_SEGMENTS)


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


def _create_doc_and_transcribe(client, tmp_home, monkeypatch, fake_fn):
    monkeypatch.setattr(transcribe_mod, "run", fake_fn)
    audio_path = tmp_home / "test.m4a"
    audio_path.write_bytes(b"\x00" * 128)
    r = client.post("/api/documents", json={"title": "테스트", "audioPath": str(audio_path)})
    assert r.status_code == 201
    doc_id = r.json()["id"]
    r = client.post(f"/api/documents/{doc_id}/transcribe")
    assert r.status_code == 200
    return doc_id, r


class TestTranscribeFormatterWiring:
    def test_md_file_uses_spec_format_when_segments_present(
        self, tmp_home, mock_platform, monkeypatch
    ):
        mock_platform("Darwin", "arm64")

        def _fake(audio_path, *, model_dir=None, prompt=None, progress_cb=None):
            if progress_cb:
                progress_cb({"percent": 1.0, "segment_count": 2, "elapsed_sec": 0.5})
            return FAKE_TEXT, FAKE_SEGMENTS

        app = create_app()
        with TestClient(app) as c:
            doc_id, r = _create_doc_and_transcribe(c, tmp_home, monkeypatch, _fake)
            events = _parse_sse(r.text)
            complete = next(e for e in events if e.get("event") == "complete")
            md = Path(complete["data"]["transcriptPath"]).read_text(encoding="utf-8")
            assert md == EXPECTED_CONTENT
            assert "[0.0s → 3.5s]" in md
            assert "안녕하세요" in md

    def test_get_transcript_returns_formatted_content(
        self, tmp_home, mock_platform, monkeypatch
    ):
        mock_platform("Darwin", "arm64")

        def _fake(audio_path, *, model_dir=None, prompt=None, progress_cb=None):
            return FAKE_TEXT, FAKE_SEGMENTS

        app = create_app()
        with TestClient(app) as c:
            doc_id, _ = _create_doc_and_transcribe(c, tmp_home, monkeypatch, _fake)
            r = c.get(f"/api/documents/{doc_id}/transcript")
            assert r.status_code == 200
            body = r.json()
            assert body["content"] == EXPECTED_CONTENT
            assert body["segments"] == []

    def test_empty_segments_fallback_to_plain_text(
        self, tmp_home, mock_platform, monkeypatch
    ):
        mock_platform("Darwin", "arm64")

        def _fake(audio_path, *, model_dir=None, prompt=None, progress_cb=None):
            return FAKE_TEXT, []

        app = create_app()
        with TestClient(app) as c:
            doc_id, _ = _create_doc_and_transcribe(c, tmp_home, monkeypatch, _fake)
            r = c.get(f"/api/documents/{doc_id}/transcript")
            assert r.status_code == 200
            assert r.json()["content"] == FAKE_TEXT
