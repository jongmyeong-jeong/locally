"""Real-time chunk boundary detector (numpy-only).

Accumulates PCM frames and emits chunk boundaries suitable for incremental
transcription. Designed for single-threaded, per-session use.

References:
  - ``app/vad.py`` — batch VAD; SAMPLE_RATE / FRAME_LEN / HOP_LEN constants
  - ``.omc/plans/realtime-pretranscribe-plan.md`` §Step 3
"""
from __future__ import annotations

import numpy as np

from app.vad import FRAME_LEN, HOP_LEN, SAMPLE_RATE, THRESHOLD_FLOOR

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_SAMPLES_PER_MS: int = SAMPLE_RATE // 1000  # 16 samples / ms @ 16 kHz


def _ms_to_samples(ms: int) -> int:
    return ms * _SAMPLES_PER_MS


def _samples_to_ms(samples: int) -> int:
    return samples // _SAMPLES_PER_MS


def _is_silent_frame(frame: np.ndarray) -> bool:
    """Return True when the RMS of *frame* is below THRESHOLD_FLOOR."""
    rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
    return rms < THRESHOLD_FLOOR


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------

class ChunkBoundaryDetector:
    """Accumulate PCM frames and emit chunk boundaries.

    A boundary is emitted when either:
      - silence >= SILENCE_MS is detected AND accumulated speech >= MIN_CHUNK_MS
      - accumulated duration >= MAX_CHUNK_MS regardless of silence

    Caller pushes PCM via feed(); completed chunks returned as (start_ms, end_ms) pairs.
    """

    MIN_CHUNK_MS: int = 5_000
    MAX_CHUNK_MS: int = 30_000
    SILENCE_MS: int = 700  # midpoint of 500–1000 ms spec range

    def __init__(self) -> None:
        # PCM buffer for the current (not-yet-emitted) chunk.
        self._buffer: np.ndarray = np.empty(0, dtype=np.float32)
        # Absolute ms offset of the start of the current chunk relative to
        # the session start.
        self._chunk_start_ms: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def feed(self, pcm_chunk: np.ndarray) -> list[tuple[int, int]]:
        """Append PCM samples (float32 mono @ 16 kHz).

        Returns a list of completed (start_ms, end_ms) boundaries (may be empty).
        Multiple boundaries can be returned when the fed audio spans several
        silence or max-duration events.
        """
        self._buffer = np.concatenate((self._buffer, pcm_chunk.astype(np.float32)))
        boundaries: list[tuple[int, int]] = []

        while True:
            boundary = self._try_emit()
            if boundary is None:
                break
            boundaries.append(boundary)

        return boundaries

    def flush(self) -> tuple[int, int] | None:
        """Force-emit remaining accumulated audio as a final boundary.

        Returns None when there is no accumulated audio.
        """
        if self._buffer.size == 0:
            return None
        end_ms = self._chunk_start_ms + _samples_to_ms(self._buffer.size)
        result = (self._chunk_start_ms, end_ms)
        self._buffer = np.empty(0, dtype=np.float32)
        self._chunk_start_ms = end_ms
        return result

    @property
    def accumulated_ms(self) -> int:
        """Duration (ms) of audio currently held in the internal buffer."""
        return _samples_to_ms(self._buffer.size)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _try_emit(self) -> tuple[int, int] | None:
        """Check buffer for an emit condition; return boundary or None."""
        total_ms = self.accumulated_ms

        # --- max-duration trigger ---
        if total_ms >= self.MAX_CHUNK_MS:
            return self._emit_at_ms(self.MAX_CHUNK_MS)

        # --- silence trigger (only when we have enough speech) ---
        if total_ms >= self.MIN_CHUNK_MS:
            end_of_speech_offset = self._find_silence_trigger()
            if end_of_speech_offset is not None:
                return self._emit_at_ms(end_of_speech_offset)

        return None

    def _emit_at_ms(self, offset_ms: int) -> tuple[int, int]:
        """Emit a boundary at *offset_ms* from chunk start; trim buffer."""
        cut = min(_ms_to_samples(offset_ms), self._buffer.size)
        start_ms = self._chunk_start_ms
        end_ms = start_ms + _samples_to_ms(cut)
        # Keep everything after the cut point for the next chunk.
        self._buffer = self._buffer[cut:]
        self._chunk_start_ms = end_ms
        return (start_ms, end_ms)

    def _find_silence_trigger(self) -> int | None:
        """Return end-of-speech offset (ms) if a qualifying silence run exists.

        Scans frame-by-frame using FRAME_LEN / HOP_LEN windows from app.vad.
        Returns the offset (in ms from chunk start) of the last voiced frame
        before the qualifying silence, or None if no such point exists.
        """
        buf = self._buffer
        if buf.size < FRAME_LEN:
            return None

        silence_needed = _ms_to_samples(self.SILENCE_MS)
        min_chunk_samples = _ms_to_samples(self.MIN_CHUNK_MS)

        # Compute frame count; frames use HOP_LEN stride.
        n_frames = 1 + (buf.size - FRAME_LEN) // HOP_LEN

        # Build boolean silence mask per frame.
        silent: list[bool] = []
        for i in range(n_frames):
            start = i * HOP_LEN
            frame = buf[start : start + FRAME_LEN]
            silent.append(_is_silent_frame(frame))

        # Walk frames looking for a silence run >= silence_needed samples.
        # Each frame starts at i*HOP_LEN; a contiguous silence run [j, k)
        # spans samples [j*HOP_LEN, k*HOP_LEN + FRAME_LEN).
        # We report end-of-speech = start of the silence run.

        i = 0
        while i < n_frames:
            if not silent[i]:
                i += 1
                continue
            # Found start of a silence run at frame i.
            j = i
            while j < n_frames and silent[j]:
                j += 1
            # Silence run covers frames [i, j).
            silence_start_sample = i * HOP_LEN
            silence_end_sample = (j - 1) * HOP_LEN + FRAME_LEN  # inclusive end of last silent frame
            silence_span = silence_end_sample - silence_start_sample

            if silence_span >= silence_needed and silence_start_sample >= min_chunk_samples:
                # End-of-speech is at the start of this silence run.
                return _samples_to_ms(silence_start_sample)

            i = j  # skip past this silence run

        return None
