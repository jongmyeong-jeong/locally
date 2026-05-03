"""Tests for groq_client observability logging (Step 6.7).

Verifies that transcribe_audio emits a logger.info("groq_transcribe", extra={...})
with the required fields: audio_path, lang, has_prompt, duration_ms.
"""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_wav(path: Path) -> None:
    import wave
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00" * 3200)


def _fake_resp(text: str = "test"):
    return SimpleNamespace(
        text=text,
        segments=[SimpleNamespace(start=0.0, end=1.0, text=text)],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    yield tmp_path


@pytest.fixture
def audio_wav(tmp_path):
    p = tmp_path / "sample.wav"
    _make_wav(p)
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGroqTranscribeLogging:
    def _run_transcribe(self, audio_wav, monkeypatch, prompt=None):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.return_value = _fake_resp()

        with patch("app.groq_client._groq_module.Groq", return_value=mock_client):
            from app.groq_client import transcribe_audio
            transcribe_audio(audio_wav, prompt=prompt)

    def test_groq_transcribe_event_emitted(self, audio_wav, monkeypatch, caplog):
        with caplog.at_level(logging.INFO, logger="app.groq_client"):
            self._run_transcribe(audio_wav, monkeypatch)

        messages = [r.getMessage() for r in caplog.records]
        assert "groq_transcribe" in messages, f"Expected 'groq_transcribe' in log messages: {messages}"

    def test_log_has_audio_path(self, audio_wav, monkeypatch, caplog):
        with caplog.at_level(logging.INFO, logger="app.groq_client"):
            self._run_transcribe(audio_wav, monkeypatch)

        record = next(r for r in caplog.records if r.getMessage() == "groq_transcribe")
        assert hasattr(record, "audio_path")
        assert str(audio_wav) in str(record.audio_path)

    def test_log_has_lang(self, audio_wav, monkeypatch, caplog):
        monkeypatch.delenv("LOCALLY_LANG", raising=False)
        with caplog.at_level(logging.INFO, logger="app.groq_client"):
            self._run_transcribe(audio_wav, monkeypatch)

        record = next(r for r in caplog.records if r.getMessage() == "groq_transcribe")
        assert hasattr(record, "lang")
        assert record.lang == "ko"

    def test_log_has_has_prompt_false_when_no_prompt(self, audio_wav, monkeypatch, caplog):
        with caplog.at_level(logging.INFO, logger="app.groq_client"):
            self._run_transcribe(audio_wav, monkeypatch, prompt=None)

        record = next(r for r in caplog.records if r.getMessage() == "groq_transcribe")
        assert hasattr(record, "has_prompt")
        assert record.has_prompt is False

    def test_log_has_has_prompt_true_when_prompt_given(self, audio_wav, monkeypatch, caplog):
        with caplog.at_level(logging.INFO, logger="app.groq_client"):
            self._run_transcribe(audio_wav, monkeypatch, prompt="my vocab")

        record = next(r for r in caplog.records if r.getMessage() == "groq_transcribe")
        assert hasattr(record, "has_prompt")
        assert record.has_prompt is True

    def test_log_has_duration_ms(self, audio_wav, monkeypatch, caplog):
        with caplog.at_level(logging.INFO, logger="app.groq_client"):
            self._run_transcribe(audio_wav, monkeypatch)

        record = next(r for r in caplog.records if r.getMessage() == "groq_transcribe")
        assert hasattr(record, "duration_ms")
        assert isinstance(record.duration_ms, int)
        assert record.duration_ms >= 0

    def test_log_has_audio_sec_for_wav(self, audio_wav, monkeypatch, caplog):
        """For valid WAV files, audio_sec should be present in the log record."""
        with caplog.at_level(logging.INFO, logger="app.groq_client"):
            self._run_transcribe(audio_wav, monkeypatch)

        record = next(r for r in caplog.records if r.getMessage() == "groq_transcribe")
        # audio_sec is optional but should be present for a valid WAV
        assert hasattr(record, "audio_sec")
        assert isinstance(record.audio_sec, float)

    def test_log_lang_reflects_env(self, audio_wav, monkeypatch, caplog):
        monkeypatch.setenv("LOCALLY_LANG", "en")
        with caplog.at_level(logging.INFO, logger="app.groq_client"):
            self._run_transcribe(audio_wav, monkeypatch)

        record = next(r for r in caplog.records if r.getMessage() == "groq_transcribe")
        assert record.lang == "en"
