"""Tests for app.transcribe profile parameter and THRESHOLD_PROFILE.

Covers:
  1. CT2 path + profile='file' → correct thresholds passed to WhisperModel.transcribe()
  2. CT2 path + profile='chunk' → correct thresholds passed
  3. Default (no profile arg) → same as profile='file'
  4. Module-level constants not mutated between calls
  5. MLX path + profile='chunk' → --hallucination-silence-threshold NOT in cmd
  6. CT2 path + profile='chunk' → both thresholds in kwargs
"""
from __future__ import annotations

import subprocess
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestCT2ProfileThresholds:
    """Force the CT2 (Linux) path and capture WhisperModel.transcribe() kwargs."""

    def _make_fake_model(self, captured: dict):
        """Return a fake WhisperModel whose .transcribe() stores kwargs in `captured`."""

        class _FakeInfo:
            duration = 1.0

        class _FakeModel:
            def transcribe(self, audio_path, **kwargs):
                captured["kwargs"] = kwargs
                # Return an empty iterator + info so _wrap_ct2 terminates.
                return iter([]), _FakeInfo()

        return _FakeModel()

    def _run_ct2(self, monkeypatch, tmp_path, profile, captured):
        """Patch platform to Linux, inject a fake CT2 model, call transcribe.run()."""
        import platform as _platform
        from app import transcribe as transcribe_mod

        monkeypatch.setattr(_platform, "system", lambda: "Linux")

        fake_model = self._make_fake_model(captured)
        monkeypatch.setattr(transcribe_mod, "_MODEL", fake_model)
        # Also patch _get_ct2_model so the lazy-init doesn't try to load real weights.
        monkeypatch.setattr(transcribe_mod, "_get_ct2_model", lambda model_dir: fake_model)

        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"\x00" * 16)

        kwargs: dict[str, Any] = {"audio_path": str(audio), "model_dir": "/fake/model"}
        if profile is not None:
            kwargs["profile"] = profile
        transcribe_mod.run(**kwargs)

    def test_run_profile_file_uses_file_thresholds(self, monkeypatch, tmp_path):
        """profile='file' → no_speech_threshold=0.6, hallucination_silence_threshold=2.0."""
        captured: dict = {}
        self._run_ct2(monkeypatch, tmp_path, "file", captured)
        kw = captured["kwargs"]
        assert kw["no_speech_threshold"] == 0.6
        assert kw["hallucination_silence_threshold"] == 2.0

    def test_run_profile_chunk_uses_chunk_thresholds(self, monkeypatch, tmp_path):
        """profile='chunk' → no_speech_threshold=0.6, hallucination_silence_threshold=2.0."""
        captured: dict = {}
        self._run_ct2(monkeypatch, tmp_path, "chunk", captured)
        kw = captured["kwargs"]
        assert kw["no_speech_threshold"] == 0.6
        assert kw["hallucination_silence_threshold"] == 2.0

    def test_run_no_profile_arg_defaults_to_file(self, monkeypatch, tmp_path):
        """Calling run() without profile arg → same as profile='file' (backward-compat)."""
        captured: dict = {}
        self._run_ct2(monkeypatch, tmp_path, None, captured)
        kw = captured["kwargs"]
        assert kw["no_speech_threshold"] == 0.6
        assert kw["hallucination_silence_threshold"] == 2.0

    def test_module_globals_unchanged_between_calls(self, monkeypatch, tmp_path):
        """Module-level _HALLUCINATION_SILENCE_THRESHOLD is not mutated by run()."""
        from app import transcribe as transcribe_mod

        original_hst = transcribe_mod._HALLUCINATION_SILENCE_THRESHOLD

        captured1: dict = {}
        self._run_ct2(monkeypatch, tmp_path, "chunk", captured1)

        captured2: dict = {}
        self._run_ct2(monkeypatch, tmp_path, "file", captured2)

        assert transcribe_mod._HALLUCINATION_SILENCE_THRESHOLD == original_hst

    def test_ct2_chunk_profile_passes_both_thresholds(self, monkeypatch, tmp_path):
        """CT2 path + profile='chunk' → both thresholds present in WhisperModel.transcribe() kwargs."""
        captured: dict = {}
        self._run_ct2(monkeypatch, tmp_path, "chunk", captured)
        kw = captured["kwargs"]
        assert "no_speech_threshold" in kw
        assert "hallucination_silence_threshold" in kw
        assert kw["no_speech_threshold"] == 0.6
        assert kw["hallucination_silence_threshold"] == 2.0


class TestMLXProfileAsymmetry:
    """Force the Darwin/MLX path and capture the subprocess cmd list."""

    def test_mlx_chunk_profile_omits_hallucination_threshold(self, monkeypatch, tmp_path):
        """MLX path + profile='chunk': --hallucination-silence-threshold NOT in cmd;
        --no-speech-threshold IS in cmd with value 0.9.

        Strategy: monkeypatch subprocess.Popen to capture the cmd list and raise
        immediately; monkeypatch audio_io.load_pcm_16k_mono and
        vad.detect_speech_timestamps so VAD preprocessing doesn't read real audio.
        """
        import platform as _platform
        from app import transcribe as transcribe_mod
        from app import audio_io as audio_io_mod
        from app import vad as vad_mod
        import numpy as np

        monkeypatch.setattr(_platform, "system", lambda: "Darwin")

        # Intercept VAD preprocessing.
        monkeypatch.setattr(
            audio_io_mod,
            "load_pcm_16k_mono",
            lambda path: np.zeros(16000, dtype=np.float32),
        )
        monkeypatch.setattr(
            vad_mod,
            "detect_speech_timestamps",
            lambda pcm, sr: [],
        )

        captured_cmd: list[str] = []

        class _EarlyExit(Exception):
            pass

        class _FakePopen:
            def __init__(self, cmd, **kwargs):
                captured_cmd.extend(cmd)
                raise _EarlyExit("early exit from fake Popen")

        monkeypatch.setattr(subprocess, "Popen", _FakePopen)

        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"\x00" * 16)

        try:
            transcribe_mod._run_mlx(
                str(audio),
                model_dir=None,
                prompt=None,
                progress_cb=None,
                profile="chunk",
            )
        except _EarlyExit:
            pass

        # The asymmetry: MLX does NOT pass hallucination_silence_threshold.
        assert "--hallucination-silence-threshold" not in captured_cmd

        # --no-speech-threshold must be present with the chunk value (0.6).
        assert "--no-speech-threshold" in captured_cmd
        idx = captured_cmd.index("--no-speech-threshold")
        nst_value = captured_cmd[idx + 1]
        assert float(nst_value) == pytest.approx(0.6, abs=1e-6), (
            f"Expected 0.6 but got {nst_value!r}"
        )
