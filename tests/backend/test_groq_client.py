"""Tests for app/groq_client.py (Step 6.2).

All tests mock the Groq SDK — no real API calls are made.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

import groq as _groq_module
from app.groq_client import (
    GroqApiKeyMissing,
    GroqClientError,
    GroqNetworkError,
    GroqRateLimitError,
    GroqServerError,
    transcribe_audio,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_response(status_code: int = 200) -> httpx.Response:
    """Build a minimal httpx.Response for Groq exception construction."""
    return httpx.Response(status_code, request=httpx.Request("POST", "https://api.groq.com"))


def _fake_verbose_json(text: str = "안녕하세요", segments=None):
    """Return a fake Groq transcription response namespace."""
    if segments is None:
        segments = [
            SimpleNamespace(start=0.0, end=1.5, text="안녕하세요"),
        ]
    return SimpleNamespace(text=text, segments=segments)


def _make_wav(path: Path) -> None:
    """Write a minimal valid WAV file (16kHz mono, 0.1s silence)."""
    import wave
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00" * 3200)


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
# 1. Missing API key
# ---------------------------------------------------------------------------


class TestMissingApiKey:
    def test_raises_when_env_unset(self, audio_wav, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        with pytest.raises(GroqApiKeyMissing):
            transcribe_audio(audio_wav)


# ---------------------------------------------------------------------------
# 2. Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_result_shape(self, audio_wav, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")

        fake_resp = _fake_verbose_json(
            text="안녕하세요",
            segments=[SimpleNamespace(start=0.0, end=1.5, text="안녕하세요")],
        )

        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.return_value = fake_resp

        with patch("app.groq_client._groq_module.Groq", return_value=mock_client):
            result = transcribe_audio(audio_wav)

        assert result["text"] == "안녕하세요"
        assert isinstance(result["segments"], list)
        assert len(result["segments"]) == 1
        seg = result["segments"][0]
        assert seg["start"] == 0.0
        assert seg["end"] == 1.5
        assert seg["text"] == "안녕하세요"

    def test_called_with_correct_model_and_format(self, audio_wav, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")

        fake_resp = _fake_verbose_json()
        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.return_value = fake_resp

        with patch("app.groq_client._groq_module.Groq", return_value=mock_client):
            transcribe_audio(audio_wav, language="ko")

        call_kwargs = mock_client.audio.transcriptions.create.call_args[1]
        assert call_kwargs["model"] == "whisper-large-v3-turbo"
        assert call_kwargs["response_format"] == "verbose_json"
        assert "segment" in call_kwargs["timestamp_granularities"]
        assert call_kwargs["language"] == "ko"


# ---------------------------------------------------------------------------
# 3. Error mapping
# ---------------------------------------------------------------------------


class TestErrorMapping:
    def _setup_client_raise(self, exc, monkeypatch, audio_wav):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.side_effect = exc

        with patch("app.groq_client._groq_module.Groq", return_value=mock_client):
            transcribe_audio(audio_wav)

    def test_rate_limit_429(self, audio_wav, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        resp = _make_fake_response(429)
        exc = _groq_module.RateLimitError("rate limit", response=resp, body=None)

        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.side_effect = exc

        with patch("app.groq_client._groq_module.Groq", return_value=mock_client):
            with pytest.raises(GroqRateLimitError):
                transcribe_audio(audio_wav)

    def test_server_error_5xx(self, audio_wav, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        resp = _make_fake_response(500)
        exc = _groq_module.APIStatusError("server error", response=resp, body=None)

        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.side_effect = exc

        with patch("app.groq_client._groq_module.Groq", return_value=mock_client):
            with pytest.raises(GroqServerError):
                transcribe_audio(audio_wav)

    def test_network_error(self, audio_wav, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        exc = httpx.ConnectError("connection refused")

        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.side_effect = exc

        with patch("app.groq_client._groq_module.Groq", return_value=mock_client):
            with pytest.raises(GroqNetworkError):
                transcribe_audio(audio_wav)

    def test_401_raises_api_key_missing(self, audio_wav, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "bad-key")
        resp = _make_fake_response(401)
        exc = _groq_module.APIStatusError("unauthorized", response=resp, body=None)
        # Patch status_code on the response used internally
        exc.status_code = 401

        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.side_effect = exc

        with patch("app.groq_client._groq_module.Groq", return_value=mock_client):
            with pytest.raises(GroqApiKeyMissing):
                transcribe_audio(audio_wav)

    def test_other_4xx_raises_client_error(self, audio_wav, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        resp = _make_fake_response(400)
        exc = _groq_module.APIStatusError("bad request", response=resp, body=None)
        exc.status_code = 400

        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.side_effect = exc

        with patch("app.groq_client._groq_module.Groq", return_value=mock_client):
            with pytest.raises(GroqClientError):
                transcribe_audio(audio_wav)

    def test_api_connection_error_raises_network_error(self, audio_wav, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        req = httpx.Request("POST", "https://api.groq.com")
        exc = _groq_module.APIConnectionError(request=req)

        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.side_effect = exc

        with patch("app.groq_client._groq_module.Groq", return_value=mock_client):
            with pytest.raises(GroqNetworkError):
                transcribe_audio(audio_wav)


# ---------------------------------------------------------------------------
# 4. Prompt loading from prompt.json
# ---------------------------------------------------------------------------


class TestPromptLoading:
    def test_prompt_loaded_from_file_when_not_provided(self, audio_wav, monkeypatch, tmp_path):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")

        # Write prompt.json inside the sandboxed home
        workspace = tmp_path / ".locally" / "workspace"
        workspace.mkdir(parents=True)
        (workspace / "prompt.json").write_text(
            json.dumps({"prompt": "Korean meeting vocabulary"}), encoding="utf-8"
        )

        fake_resp = _fake_verbose_json()
        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.return_value = fake_resp

        with patch("app.groq_client._groq_module.Groq", return_value=mock_client):
            transcribe_audio(audio_wav)

        call_kwargs = mock_client.audio.transcriptions.create.call_args[1]
        assert call_kwargs.get("prompt") == "Korean meeting vocabulary"

    def test_explicit_prompt_overrides_file(self, audio_wav, monkeypatch, tmp_path):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")

        workspace = tmp_path / ".locally" / "workspace"
        workspace.mkdir(parents=True)
        (workspace / "prompt.json").write_text(
            json.dumps({"prompt": "from file"}), encoding="utf-8"
        )

        fake_resp = _fake_verbose_json()
        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.return_value = fake_resp

        with patch("app.groq_client._groq_module.Groq", return_value=mock_client):
            transcribe_audio(audio_wav, prompt="explicit prompt")

        call_kwargs = mock_client.audio.transcriptions.create.call_args[1]
        assert call_kwargs.get("prompt") == "explicit prompt"

    def test_no_prompt_key_when_file_absent(self, audio_wav, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")

        fake_resp = _fake_verbose_json()
        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.return_value = fake_resp

        with patch("app.groq_client._groq_module.Groq", return_value=mock_client):
            transcribe_audio(audio_wav)

        call_kwargs = mock_client.audio.transcriptions.create.call_args[1]
        assert "prompt" not in call_kwargs


# ---------------------------------------------------------------------------
# 5. Language defaults
# ---------------------------------------------------------------------------


class TestLanguageDefaults:
    def test_default_language_is_ko(self, audio_wav, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        monkeypatch.delenv("LOCALLY_LANG", raising=False)

        fake_resp = _fake_verbose_json()
        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.return_value = fake_resp

        with patch("app.groq_client._groq_module.Groq", return_value=mock_client):
            transcribe_audio(audio_wav)

        call_kwargs = mock_client.audio.transcriptions.create.call_args[1]
        assert call_kwargs["language"] == "ko"

    def test_locally_lang_env_respected(self, audio_wav, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        monkeypatch.setenv("LOCALLY_LANG", "en")

        fake_resp = _fake_verbose_json()
        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.return_value = fake_resp

        with patch("app.groq_client._groq_module.Groq", return_value=mock_client):
            transcribe_audio(audio_wav)

        call_kwargs = mock_client.audio.transcriptions.create.call_args[1]
        assert call_kwargs["language"] == "en"

    def test_explicit_language_overrides_env(self, audio_wav, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        monkeypatch.setenv("LOCALLY_LANG", "ko")

        fake_resp = _fake_verbose_json()
        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.return_value = fake_resp

        with patch("app.groq_client._groq_module.Groq", return_value=mock_client):
            transcribe_audio(audio_wav, language="en")

        call_kwargs = mock_client.audio.transcriptions.create.call_args[1]
        assert call_kwargs["language"] == "en"
