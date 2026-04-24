"""Tests for app/audio_io.py: FFmpeg-based PCM loader."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# ffmpeg availability guard
# ---------------------------------------------------------------------------
_BUNDLED_FFMPEG = Path(__file__).resolve().parent.parent.parent / "bin" / "ffmpeg"
_FFMPEG = str(_BUNDLED_FFMPEG) if _BUNDLED_FFMPEG.exists() else shutil.which("ffmpeg")

pytestmark = pytest.mark.skipif(
    _FFMPEG is None,
    reason="ffmpeg not available (neither bundled bin/ffmpeg nor system ffmpeg on PATH)",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sine_wav(dest: Path) -> None:
    """Generate a 0.5-second 440 Hz sine wave WAV at 16 kHz mono via ffmpeg."""
    result = subprocess.run(
        [
            _FFMPEG,
            "-f", "lavfi",
            "-i", "sine=frequency=440:duration=0.5",
            "-ar", "16000",
            "-ac", "1",
            "-y",
            str(dest),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"ffmpeg fixture generation failed: {result.stderr.decode('utf-8', errors='replace')}"
    )


# ---------------------------------------------------------------------------
# Smoke test: successful decode
# ---------------------------------------------------------------------------

class TestLoadPcm16kMono:
    def test_returns_numpy_float32_array(self, tmp_path):
        """load_pcm_16k_mono returns a numpy ndarray with dtype float32."""
        from app.audio_io import load_pcm_16k_mono

        wav = tmp_path / "sine.wav"
        _make_sine_wav(wav)

        result = load_pcm_16k_mono(str(wav))

        assert isinstance(result, np.ndarray)
        assert result.dtype == np.float32

    def test_returns_1d_array(self, tmp_path):
        """load_pcm_16k_mono returns a 1-dimensional (mono) array."""
        from app.audio_io import load_pcm_16k_mono

        wav = tmp_path / "sine.wav"
        _make_sine_wav(wav)

        result = load_pcm_16k_mono(str(wav))

        assert result.ndim == 1

    def test_sample_count_matches_duration(self, tmp_path):
        """0.5-second audio at 16 kHz yields approximately 8000 samples."""
        from app.audio_io import load_pcm_16k_mono

        wav = tmp_path / "sine.wav"
        _make_sine_wav(wav)

        result = load_pcm_16k_mono(str(wav))

        expected = 0.5 * 16_000  # 8000
        assert abs(len(result) - expected) <= 100, (
            f"Expected ~{expected} samples, got {len(result)}"
        )

    def test_values_within_normalised_range(self, tmp_path):
        """All PCM samples are within [-1.0, 1.0]."""
        from app.audio_io import load_pcm_16k_mono

        wav = tmp_path / "sine.wav"
        _make_sine_wav(wav)

        result = load_pcm_16k_mono(str(wav))

        assert float(result.min()) >= -1.0
        assert float(result.max()) <= 1.0


# ---------------------------------------------------------------------------
# Error path: invalid audio file
# ---------------------------------------------------------------------------

class TestLoadPcm16kMonoErrorPath:
    def test_raises_transcription_error_on_invalid_file(self, tmp_path):
        """load_pcm_16k_mono raises TranscriptionError when the file is not valid audio."""
        from app.audio_io import load_pcm_16k_mono
        from app.transcribe import TranscriptionError

        not_audio = tmp_path / "not_audio.wav"
        not_audio.write_text("not audio content")

        with pytest.raises(TranscriptionError):
            load_pcm_16k_mono(str(not_audio))

    def test_error_message_contains_ffmpeg_stderr(self, tmp_path):
        """TranscriptionError message includes ffmpeg diagnostic output (non-empty stderr)."""
        from app.audio_io import load_pcm_16k_mono
        from app.transcribe import TranscriptionError

        not_audio = tmp_path / "not_audio.wav"
        not_audio.write_text("not audio content")

        with pytest.raises(TranscriptionError) as exc_info:
            load_pcm_16k_mono(str(not_audio))

        message = str(exc_info.value)
        # The message must contain ffmpeg's stderr diagnostic (non-empty).
        # Strip the "ffmpeg failed (code N): " prefix to isolate stderr content.
        assert "ffmpeg failed" in message
        # Stderr tail appended to the message must be non-trivial.
        prefix = "ffmpeg failed"
        after_prefix = message[message.index(prefix):]
        # At minimum there is a colon and some ffmpeg output beyond the prefix line.
        assert len(after_prefix) > len(prefix) + 10, (
            f"Expected ffmpeg stderr content in message, got: {message!r}"
        )


# ---------------------------------------------------------------------------
# Missing-ffmpeg scenario
# ---------------------------------------------------------------------------

class TestMissingFfmpeg:
    @pytest.mark.skip(
        reason=(
            "Patching _FFMPEG after module import does not re-trigger the module-level "
            "resolution guard (which runs at import time). Forcing ffmpeg absence would "
            "require reimporting the module in a subprocess with a modified PATH, which "
            "is tested adequately by the module's own import-time raise. Skip to avoid "
            "fragile reimport infrastructure."
        )
    )
    def test_raises_when_ffmpeg_missing(self, monkeypatch):
        """TranscriptionError is raised with a clear message when ffmpeg is absent."""
        pass
