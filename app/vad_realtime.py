"""Real-time chunk boundary detector (numpy-only).

Accumulates PCM frames and emits chunk boundaries suitable for incremental
transcription. Designed for single-threaded, per-session use.

References:
  - ``app/vad.py`` — batch VAD; SAMPLE_RATE / FRAME_LEN / HOP_LEN constants
  - ``.omc/plans/realtime-pretranscribe-plan.md`` §Step 3
"""
from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from app.vad import FRAME_LEN, HOP_LEN, SAMPLE_RATE, THRESHOLD_FLOOR

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_SAMPLES_PER_MS: int = SAMPLE_RATE // 1000  # 16 samples / ms @ 16 kHz


def _ms_to_samples(ms: int) -> int:
    return ms * _SAMPLES_PER_MS


def _samples_to_ms(samples: int) -> int:
    return samples // _SAMPLES_PER_MS


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
    # flush() tails shorter than this carry no transcribable speech — feeding
    # them to Whisper-family models invites hallucinated text.
    MIN_FLUSH_MS: int = 300

    def __init__(self) -> None:
        # PCM buffer for the current (not-yet-emitted) chunk.
        self._buffer: np.ndarray = np.empty(0, dtype=np.float32)
        # Absolute ms offset of the start of the current chunk relative to
        # the session start.
        self._chunk_start_ms: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def feed(self, pcm_chunk: np.ndarray) -> list[tuple[int, int, np.ndarray]]:
        """Append PCM samples (float32 mono @ 16 kHz).

        Returns a list of completed (start_ms, end_ms, pcm_slice) boundaries
        (may be empty).  Multiple boundaries can be returned when the fed audio
        spans several silence or max-duration events.  *pcm_slice* is a copy of
        the emitted PCM samples suitable for direct WAV export.
        """
        self._buffer = np.concatenate((self._buffer, pcm_chunk.astype(np.float32)))
        boundaries: list[tuple[int, int, np.ndarray]] = []

        while True:
            boundary = self._try_emit()
            if boundary is None:
                break
            boundaries.append(boundary)

        return boundaries

    def flush(self) -> tuple[int, int, np.ndarray] | None:
        """Force-emit remaining accumulated audio as a final boundary.

        Returns ``(start_ms, end_ms, pcm_slice)`` matching feed() boundaries so
        the caller can transcribe the tail that never met an emit condition
        (e.g. a short recording, or speech right before stop).  Returns None
        when the buffer is empty, shorter than MIN_FLUSH_MS, or contains no
        speech frames — transcribing silent or sub-word tails only invites
        hallucinated text.  The buffer is cleared in every case.
        """
        if self._buffer.size == 0:
            return None
        start_ms = self._chunk_start_ms
        end_ms = start_ms + _samples_to_ms(self._buffer.size)
        pcm_slice = self._buffer
        self._buffer = np.empty(0, dtype=np.float32)
        self._chunk_start_ms = end_ms
        if end_ms - start_ms < self.MIN_FLUSH_MS:
            return None
        if not self._has_speech(pcm_slice):
            return None
        return (start_ms, end_ms, pcm_slice)

    @property
    def accumulated_ms(self) -> int:
        """Duration (ms) of audio currently held in the internal buffer."""
        return _samples_to_ms(self._buffer.size)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _try_emit(self) -> tuple[int, int, np.ndarray] | None:
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

    def _emit_at_ms(self, offset_ms: int) -> tuple[int, int, np.ndarray]:
        """Emit a boundary at *offset_ms* from chunk start; trim buffer.

        Returns ``(start_ms, end_ms, pcm_slice)`` where *pcm_slice* is a copy
        of the emitted samples (captured before the buffer is trimmed).
        """
        cut = min(_ms_to_samples(offset_ms), self._buffer.size)
        start_ms = self._chunk_start_ms
        end_ms = start_ms + _samples_to_ms(cut)
        # Capture slice BEFORE trimming the buffer.
        pcm_slice = self._buffer[:cut].copy()
        # Keep everything after the cut point for the next chunk.
        self._buffer = self._buffer[cut:]
        self._chunk_start_ms = end_ms
        return (start_ms, end_ms, pcm_slice)

    @staticmethod
    def _has_speech(buf: np.ndarray) -> bool:
        """True when any RMS frame is at or above the silence floor.

        Sub-frame buffers (< FRAME_LEN samples ≈ one RMS window) are treated
        as silence — too short to contain usable speech.
        """
        if buf.size < FRAME_LEN:
            return False
        windows = sliding_window_view(buf, window_shape=FRAME_LEN)[::HOP_LEN]
        rms = np.sqrt(np.mean(windows.astype(np.float32) ** 2, axis=1))
        return bool((rms >= THRESHOLD_FLOOR).any())

    def _find_silence_trigger(self) -> int | None:
        """Return end-of-speech offset (ms) if a qualifying silence run exists.

        Vectorized via sliding_window_view + run-length analysis on the silence
        mask.  Equivalent to the previous frame-by-frame scan, O(N) numpy ops.
        """
        buf = self._buffer
        if buf.size < FRAME_LEN:
            return None

        silence_needed = _ms_to_samples(self.SILENCE_MS)
        min_chunk_samples = _ms_to_samples(self.MIN_CHUNK_MS)

        windows = sliding_window_view(buf, window_shape=FRAME_LEN)[::HOP_LEN]
        rms = np.sqrt(np.mean(windows.astype(np.float32) ** 2, axis=1))
        silent = rms < THRESHOLD_FLOOR
        if not silent.any():
            return None

        # Find run boundaries on the boolean mask via a single diff pass.
        padded = np.concatenate(([False], silent, [False]))
        diffs = np.diff(padded.astype(np.int8))
        run_starts = np.flatnonzero(diffs == 1)
        run_ends = np.flatnonzero(diffs == -1)  # exclusive end (frame index)

        for start_frame, end_frame in zip(run_starts, run_ends):
            silence_start_sample = int(start_frame) * HOP_LEN
            silence_end_sample = (int(end_frame) - 1) * HOP_LEN + FRAME_LEN
            silence_span = silence_end_sample - silence_start_sample
            if silence_span >= silence_needed and silence_start_sample >= min_chunk_samples:
                return _samples_to_ms(silence_start_sample)
        return None
