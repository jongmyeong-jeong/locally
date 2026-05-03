"""Tests for app/prompt.py load() and groq_client prompt injection (Step 6.6)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app import prompt as prompt_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    yield tmp_path


@pytest.fixture
def prompt_file(tmp_path) -> Path:
    """Return the path where prompt.json should live (data dir created)."""
    data_dir = tmp_path / ".lonta" / "data"
    data_dir.mkdir(parents=True)
    return data_dir / "prompt.json"


# ---------------------------------------------------------------------------
# Tests: prompt.load()
# ---------------------------------------------------------------------------


class TestPromptLoad:
    def test_new_format_returns_string(self, prompt_file):
        prompt_file.write_text(json.dumps({"prompt": "hello world"}), encoding="utf-8")
        result = prompt_mod.load(prompt_file)
        assert result == "hello world"

    def test_backward_compat_array_format(self, prompt_file):
        prompt_file.write_text(json.dumps(["term1", "term2", "term3"]), encoding="utf-8")
        result = prompt_mod.load(prompt_file)
        assert result == "term1, term2, term3"

    def test_returns_none_when_file_absent(self, tmp_path):
        missing = tmp_path / "no_such.json"
        assert prompt_mod.load(missing) is None

    def test_returns_none_when_empty_string_value(self, prompt_file):
        prompt_file.write_text(json.dumps({"prompt": ""}), encoding="utf-8")
        assert prompt_mod.load(prompt_file) is None

    def test_returns_none_when_whitespace_only(self, prompt_file):
        prompt_file.write_text(json.dumps({"prompt": "   "}), encoding="utf-8")
        assert prompt_mod.load(prompt_file) is None

    def test_returns_none_on_malformed_json(self, prompt_file):
        prompt_file.write_text("not-valid-json", encoding="utf-8")
        assert prompt_mod.load(prompt_file) is None

    def test_returns_none_on_empty_file(self, prompt_file):
        prompt_file.write_text("", encoding="utf-8")
        assert prompt_mod.load(prompt_file) is None

    def test_truncates_over_600_chars(self, prompt_file):
        long_str = "a " * 350  # 700 chars
        prompt_file.write_text(json.dumps({"prompt": long_str}), encoding="utf-8")
        result = prompt_mod.load(prompt_file)
        assert result is not None
        assert len(result) <= 600

    def test_truncation_logs_warning(self, prompt_file, caplog):
        import logging
        long_str = "word " * 200  # 1000 chars
        prompt_file.write_text(json.dumps({"prompt": long_str}), encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="app.prompt"):
            prompt_mod.load(prompt_file)
        assert any("prompt_too_long" in r.message or "prompt_too_long" in str(r.getMessage()) for r in caplog.records)

    def test_array_empty_strings_filtered(self, prompt_file):
        prompt_file.write_text(json.dumps(["", "  ", "valid"]), encoding="utf-8")
        result = prompt_mod.load(prompt_file)
        assert result == "valid"

    def test_array_all_empty_returns_none(self, prompt_file):
        prompt_file.write_text(json.dumps(["", "  "]), encoding="utf-8")
        result = prompt_mod.load(prompt_file)
        assert result is None


# ---------------------------------------------------------------------------
# Tests: groq_client prompt injection
# ---------------------------------------------------------------------------


def _make_wav(path: Path) -> None:
    import wave
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00" * 3200)


class TestGroqClientPromptInjection:
    """Verify that transcribe_audio passes/omits prompt based on prompt.json."""

    @pytest.fixture
    def audio_wav(self, tmp_path):
        p = tmp_path / "sample.wav"
        _make_wav(p)
        return p

    def _fake_resp(self):
        from types import SimpleNamespace
        return SimpleNamespace(
            text="hello",
            segments=[SimpleNamespace(start=0.0, end=1.0, text="hello")],
        )

    def test_prompt_passed_when_file_exists(self, audio_wav, monkeypatch, prompt_file):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        prompt_file.write_text(json.dumps({"prompt": "domain terms"}), encoding="utf-8")

        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.return_value = self._fake_resp()

        with patch("app.groq_client._groq_module.Groq", return_value=mock_client):
            from app.groq_client import transcribe_audio
            transcribe_audio(audio_wav)

        call_kwargs = mock_client.audio.transcriptions.create.call_args[1]
        assert call_kwargs.get("prompt") == "domain terms"

    def test_prompt_omitted_when_file_absent(self, audio_wav, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        # No prompt.json — home is tmp_path with no data dir

        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.return_value = self._fake_resp()

        with patch("app.groq_client._groq_module.Groq", return_value=mock_client):
            from app.groq_client import transcribe_audio
            transcribe_audio(audio_wav)

        call_kwargs = mock_client.audio.transcriptions.create.call_args[1]
        assert "prompt" not in call_kwargs
