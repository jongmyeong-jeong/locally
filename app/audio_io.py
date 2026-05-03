"""FFmpeg-based PCM loader for the audio pipeline.

Decodes arbitrary audio files to 16 kHz mono float32 PCM via the bundled
(or system) ffmpeg binary.  Used by the VAD live-recording path in server.py.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np


class AudioIOError(Exception):
    """Raised when ffmpeg is missing or exits with a non-zero status."""


# ---------------------------------------------------------------------------
# ffmpeg resolution (lazy — resolved on first call, not at import time)
# ---------------------------------------------------------------------------
# Primary: bundled binary at <project-root>/bin/ffmpeg
# Fallback: system ffmpeg on PATH (useful for development / CI without the bundle)
_BUNDLED_FFMPEG = Path(__file__).resolve().parent.parent / "bin" / "ffmpeg"


def _resolve_ffmpeg() -> str:
    """Locate ffmpeg binary; prefer bundled, fall back to PATH. Raises on miss."""
    if _BUNDLED_FFMPEG.exists():
        return str(_BUNDLED_FFMPEG)
    system = shutil.which("ffmpeg")
    if system is None:
        raise AudioIOError(
            "ffmpeg not found: expected bundled binary at "
            f"{_BUNDLED_FFMPEG} and no 'ffmpeg' on PATH. "
            "Install ffmpeg or ensure the bundled binary is present."
        )
    return system


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_pcm_16k_mono(audio_path: str) -> "np.ndarray":
    """Decode audio via bundled ffmpeg to 16 kHz mono float32 numpy array.

    Returns a float32 1-D array with values in [-1.0, 1.0].

    Raises
    ------
    AudioIOError
        On ffmpeg not found or non-zero exit.  The ffmpeg stderr tail (up to
        512 bytes) is included in the exception message for diagnosis.
    """
    ffmpeg = _resolve_ffmpeg()
    cmd = [
        ffmpeg,
        "-nostdin",          # prevent TTY hang when stdin is a pipe
        "-v", "error",       # suppress banner; errors still go to stderr
        "-i", audio_path,
        "-ac", "1",          # mono
        "-ar", "16000",      # 16 kHz
        "-f", "f32le",       # raw float32 little-endian PCM
        "-acodec", "pcm_f32le",
        "-",                 # write to stdout
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        check=False,
        timeout=120,
    )

    if result.returncode != 0:
        stderr_text = result.stderr.decode("utf-8", errors="replace")[-512:]
        raise AudioIOError(
            f"ffmpeg failed (code {result.returncode}): {stderr_text}"
        )

    pcm = np.frombuffer(result.stdout, dtype=np.float32)
    return pcm
