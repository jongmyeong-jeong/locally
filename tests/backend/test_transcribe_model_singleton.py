"""Tests for app/transcribe.py: N6 _MODEL singleton (CT2 path)."""
from __future__ import annotations

import sys
import types

import pytest

from app import transcribe


@pytest.fixture(autouse=True)
def _reset_singleton():
    transcribe.reset_model_singleton_for_testing()
    yield
    transcribe.reset_model_singleton_for_testing()


def _install_fake_faster_whisper(monkeypatch):
    """Replace `faster_whisper` import with a stub that counts constructor calls."""
    constructor_calls = {"n": 0, "args": [], "kwargs": []}

    class _FakeModel:
        def __init__(self, *args, **kwargs):
            constructor_calls["n"] += 1
            constructor_calls["args"].append(args)
            constructor_calls["kwargs"].append(kwargs)

        def transcribe(self, audio_path, **kwargs):  # noqa: ARG002
            return iter([]), types.SimpleNamespace(duration=10.0)

    fake_mod = types.ModuleType("faster_whisper")
    fake_mod.WhisperModel = _FakeModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_mod)
    return constructor_calls


class TestModelSingleton:
    def test_model_constructor_called_once_across_3_serial_transcribes(
        self, monkeypatch, tmp_path
    ):
        """N6: the WhisperModel constructor runs exactly once per process."""
        counts = _install_fake_faster_whisper(monkeypatch)
        monkeypatch.setattr(transcribe.platform, "system", lambda: "Linux")

        audio = tmp_path / "a.wav"
        audio.write_bytes(b"\x00" * 16)
        model_dir = tmp_path / "model"
        model_dir.mkdir()

        for _ in range(3):
            text, segments = transcribe.run(
                str(audio), model_dir=str(model_dir)
            )
            assert isinstance(text, str)

        assert counts["n"] == 1, (
            f"expected WhisperModel to instantiate once; got {counts['n']}"
        )

    def test_reset_for_testing_forces_new_instance(self, monkeypatch, tmp_path):
        counts = _install_fake_faster_whisper(monkeypatch)
        monkeypatch.setattr(transcribe.platform, "system", lambda: "Linux")

        audio = tmp_path / "a.wav"
        audio.write_bytes(b"\x00" * 16)
        model_dir = tmp_path / "model"
        model_dir.mkdir()

        transcribe.run(str(audio), model_dir=str(model_dir))
        transcribe.reset_model_singleton_for_testing()
        transcribe.run(str(audio), model_dir=str(model_dir))
        assert counts["n"] == 2

    def test_missing_audio_raises(self, tmp_path):
        with pytest.raises(transcribe.TranscriptionError):
            transcribe.run(str(tmp_path / "does-not-exist.wav"))
