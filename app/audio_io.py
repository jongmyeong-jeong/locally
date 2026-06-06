"""FFmpeg-based PCM loader for the audio pipeline.

Decodes arbitrary audio files to 16 kHz mono float32 PCM via the bundled
(or system) ffmpeg binary.  Used by the VAD live-recording path in server.py.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

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


# ---------------------------------------------------------------------------
# Codec probe (cached at module level — runs once per process)
# ---------------------------------------------------------------------------

_PROBED_ENCODERS: Optional[frozenset[str]] = None


def probe_audio_encoders() -> frozenset[str]:
    """Return the set of available ffmpeg audio encoder names.

    Runs ``ffmpeg -hide_banner -encoders`` once and caches the result for the
    process lifetime.  Looks for 'libopus', 'flac', 'libmp3lame' specifically,
    but returns all audio encoder names so callers can check any codec.
    """
    global _PROBED_ENCODERS
    if _PROBED_ENCODERS is not None:
        return _PROBED_ENCODERS

    ffmpeg = _resolve_ffmpeg()
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        capture_output=True,
        check=False,
        timeout=30,
    )
    encoders: set[str] = set()
    # Output format per line (audio section):  " A..... encoder_name  ..."
    # A in position 1 indicates audio encoder.
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if len(stripped) < 8:
            continue
        # Columns: flags(6 chars) name description
        # Flag column 0 = type: A=audio, V=video, S=subtitle
        if stripped[0] == "A":
            parts = stripped.split()
            if len(parts) >= 2:
                encoders.add(parts[1])

    _PROBED_ENCODERS = frozenset(encoders)
    return _PROBED_ENCODERS


# ---------------------------------------------------------------------------
# Re-encode for upload
# ---------------------------------------------------------------------------

def reencode_for_upload(src: Path, dest_dir: Path) -> Path:
    """Re-encode *src* to 16 kHz mono for Groq upload; returns the output Path.

    Codec selection (probed once at first call):
      1. libopus  → output ``.ogg`` (24 kbps VBR)  — ~11 MB/h
      2. flac     → output ``.flac``               — ~30–45 MB/h fallback

    Raises
    ------
    AudioIOError
        If neither libopus nor flac is available, or if ffmpeg exits non-zero.
    """
    ffmpeg = _resolve_ffmpeg()
    encoders = probe_audio_encoders()

    if "libopus" in encoders:
        suffix = ".ogg"
        codec_args = ["-c:a", "libopus", "-b:a", "24k", "-vbr", "on", "-f", "ogg"]
    elif "flac" in encoders:
        suffix = ".flac"
        codec_args = ["-c:a", "flac", "-f", "flac"]
    else:
        raise AudioIOError(
            "ffmpeg has neither libopus nor flac encoder available. "
            "Cannot re-encode audio for upload."
        )

    dest = dest_dir / (src.stem + suffix)
    cmd = [
        ffmpeg,
        "-nostdin",
        "-v", "error",
        "-i", str(src),
        "-ar", "16000",
        "-ac", "1",
        "-map", "0:a",
        *codec_args,
        "-y",
        str(dest),
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
            f"ffmpeg reencode failed (code {result.returncode}): {stderr_text}"
        )
    return dest


# ---------------------------------------------------------------------------
# Windowed PCM decode (for silence-boundary search)
# ---------------------------------------------------------------------------

def load_pcm_16k_mono_window(
    audio_path: str,
    *,
    start_sec: float,
    duration_sec: float,
) -> "np.ndarray":
    """Decode a time window of *audio_path* to 16 kHz mono float32 PCM.

    Uses ``-ss {start_sec}`` BEFORE ``-i`` (fast seek) and ``-t {duration_sec}``
    after to limit output length.  Follows the same subprocess pattern as
    :func:`load_pcm_16k_mono`.

    Returns a float32 1-D array (may be shorter than requested near EOF).
    """
    ffmpeg = _resolve_ffmpeg()
    cmd = [
        ffmpeg,
        "-nostdin",
        "-v", "error",
        "-ss", str(start_sec),   # seek BEFORE -i for fast seek
        "-i", audio_path,
        "-t", str(duration_sec),
        "-ac", "1",
        "-ar", "16000",
        "-f", "f32le",
        "-acodec", "pcm_f32le",
        "-",
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
            f"ffmpeg windowed decode failed (code {result.returncode}): {stderr_text}"
        )
    pcm = np.frombuffer(result.stdout, dtype=np.float32)
    return pcm
