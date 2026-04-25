"""Tests for glossary injection into file-upload transcribe route (locally-4ue).

Verifies that POST /api/notes/{note_id}/transcribe passes glossary terms
as the `prompt` argument to transcribe.run, and passes None when glossary is empty.
"""
from __future__ import annotations


from fastapi.testclient import TestClient

from app import glossary as glossary_mod
from app import paths
from app import transcribe as transcribe_mod
from app.server import create_app


FAKE_SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": "테스트"},
]
FAKE_TEXT = "테스트"


def _fake_run_capture(calls: list):
    """Return a fake transcribe.run that records call kwargs."""
    def _fake(audio_path, *, model_dir=None, prompt=None, progress_cb=None, **kwargs):
        calls.append({"audio_path": audio_path, "prompt": prompt})
        return FAKE_TEXT, FAKE_SEGMENTS
    return _fake


def _create_doc_with_audio(client, tmp_home) -> str:
    audio_path = tmp_home / "test.m4a"
    audio_path.write_bytes(b"\x00" * 128)
    r = client.post("/api/notes", json={"title": "테스트", "audioPath": str(audio_path)})
    assert r.status_code == 201
    return r.json()["id"]


class TestTranscribeGlossaryInjection:
    def test_glossary_terms_passed_as_prompt(
        self, tmp_home, mock_platform, monkeypatch
    ):
        """glossary에 단어가 있으면 ', '.join한 값이 prompt로 전달된다."""
        mock_platform("Darwin", "arm64")

        # Write glossary terms (no explicit path — uses patched Path.home() via tmp_home).
        glossary_mod.save(["전사", "요약", "AI"])

        calls: list = []
        monkeypatch.setattr(transcribe_mod, "run", _fake_run_capture(calls))

        app = create_app()
        with TestClient(app) as c:
            note_id = _create_doc_with_audio(c, tmp_home)
            r = c.post(f"/api/notes/{note_id}/transcribe")
            assert r.status_code == 200

        assert len(calls) == 1
        assert calls[0]["prompt"] == "전사, 요약, AI"

    def test_empty_glossary_passes_none_prompt(
        self, tmp_home, mock_platform, monkeypatch
    ):
        """glossary가 비어 있으면 prompt=None으로 전달된다."""
        mock_platform("Darwin", "arm64")

        # Ensure no glossary file exists (tmp_home is fresh).
        calls: list = []
        monkeypatch.setattr(transcribe_mod, "run", _fake_run_capture(calls))

        app = create_app()
        with TestClient(app) as c:
            note_id = _create_doc_with_audio(c, tmp_home)
            r = c.post(f"/api/notes/{note_id}/transcribe")
            assert r.status_code == 200

        assert len(calls) == 1
        assert calls[0]["prompt"] is None

    def test_malformed_glossary_falls_back_to_none_prompt(
        self, tmp_home, mock_platform, monkeypatch
    ):
        """malformed glossary.json이어도 전사는 계속되고 prompt=None으로 fallback된다."""
        mock_platform("Darwin", "arm64")

        glossary_path = paths.glossary_path()
        glossary_path.parent.mkdir(parents=True, exist_ok=True)
        glossary_path.write_text("{not-json", encoding="utf-8")

        calls: list = []
        monkeypatch.setattr(transcribe_mod, "run", _fake_run_capture(calls))

        app = create_app()
        with TestClient(app) as c:
            note_id = _create_doc_with_audio(c, tmp_home)
            r = c.post(f"/api/notes/{note_id}/transcribe")
            assert r.status_code == 200

        assert len(calls) == 1
        assert calls[0]["prompt"] is None
