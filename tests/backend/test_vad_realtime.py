"""Tests for app.vad_realtime.ChunkBoundaryDetector.

Pure numpy — no ffmpeg, no audio files, no external fixtures.
"""
from __future__ import annotations

import numpy as np
from app.vad import SAMPLE_RATE
from app.vad_realtime import ChunkBoundaryDetector


# ---------------------------------------------------------------------------
# PCM helpers
# ---------------------------------------------------------------------------

def pcm_silence(seconds: float) -> np.ndarray:
    """Return float32 mono silence at SAMPLE_RATE."""
    n = int(seconds * SAMPLE_RATE)
    return np.zeros(n, dtype=np.float32)


def pcm_speech(seconds: float, freq_hz: float = 440.0, amp: float = 0.3) -> np.ndarray:
    """Return float32 mono sine wave at SAMPLE_RATE.

    amp=0.3 → RMS ≈ 0.212, which is ~42x THRESHOLD_FLOOR (0.005).
    This gives stable 'speech' detection above the silence threshold.
    """
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    return (amp * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestChunkBoundaryDetector:
    """Unit tests for ChunkBoundaryDetector."""

    def test_short_silence_does_not_emit_boundary(self) -> None:
        """Two 2 s speech bursts separated by 0.2 s silence — total 4.2 s.

        Total duration < MIN_CHUNK_MS (5 s) and silence < SILENCE_MS (700 ms).
        Expect no boundaries emitted and accumulated_ms ≈ 4200.
        """
        audio = np.concatenate([
            pcm_speech(2.0),
            pcm_silence(0.2),
            pcm_speech(2.0),
        ])
        det = ChunkBoundaryDetector()
        boundaries = det.feed(audio)

        assert boundaries == [], f"Expected no boundaries, got {boundaries}"
        assert abs(det.accumulated_ms - 4200) < 50, (
            f"accumulated_ms={det.accumulated_ms}, expected ~4200"
        )

    def test_max_duration_forces_split(self) -> None:
        """31 s of continuous speech — expect at least one boundary at MAX_CHUNK_MS=30000 ms."""
        audio = pcm_speech(31.0)
        det = ChunkBoundaryDetector()
        boundaries = det.feed(audio)

        assert len(boundaries) >= 1, "Expected at least one boundary for 31 s of speech"
        first_start, first_end, _pcm = boundaries[0]
        assert first_start == 0, f"First boundary start should be 0, got {first_start}"
        assert abs(first_end - 30_000) < 50, (
            f"First boundary end should be ~30000 ms, got {first_end}"
        )
        # ~1 s of residual should remain buffered for the next chunk
        assert det.accumulated_ms > 500, (
            f"Expected residual audio after split, got {det.accumulated_ms} ms"
        )

    def test_silence_boundary_at_correct_timestamp(self) -> None:
        """6 s speech + 0.8 s silence + 4 s speech = 10.8 s total.

        Silence (0.8 s = 800 ms) >= SILENCE_MS (700 ms) and speech before
        silence >= MIN_CHUNK_MS (5000 ms = 5 s … 6 s qualifies).
        Expect a boundary emitted near 6000 ms (end of first speech run).
        After the boundary, ~4 s of the second speech run remains buffered.
        """
        audio = np.concatenate([
            pcm_speech(6.0),
            pcm_silence(0.8),
            pcm_speech(4.0),
        ])
        det = ChunkBoundaryDetector()
        boundaries = det.feed(audio)

        assert len(boundaries) >= 1, f"Expected at least one boundary, got {boundaries}"
        start_ms, end_ms, _pcm = boundaries[0]
        assert start_ms == 0, f"Boundary should start at 0, got {start_ms}"
        # Tolerance ±200 ms to absorb frame-quantization (HOP_LEN=400 samples = 25 ms)
        assert abs(end_ms - 6_000) < 200, (
            f"Boundary end should be near 6000 ms, got {end_ms}"
        )
        # Second speech burst (~4 s) should remain buffered
        assert det.accumulated_ms > 3_500, (
            f"Expected ~4 s residual, got {det.accumulated_ms} ms"
        )

    def test_flush_emits_remaining_audio(self) -> None:
        """7 s speech with no silence — no boundary emitted on feed().

        flush() must return (0, ~7000, pcm) so the tail can be transcribed.
        A second flush() must return None.
        """
        audio = pcm_speech(7.0)
        det = ChunkBoundaryDetector()
        boundaries = det.feed(audio)

        assert boundaries == [], f"Expected no boundaries before flush, got {boundaries}"

        result = det.flush()
        assert result is not None, "flush() should return a boundary for non-empty buffer"
        start_ms, end_ms, pcm = result
        assert start_ms == 0, f"flush start should be 0, got {start_ms}"
        assert abs(end_ms - 7_000) < 50, (
            f"flush end should be ~7000 ms, got {end_ms}"
        )
        assert pcm.size == audio.size, (
            f"flush pcm should carry the whole tail: {pcm.size} != {audio.size}"
        )

        # Second flush on empty buffer must return None
        second = det.flush()
        assert second is None, f"Second flush() should return None, got {second}"

    def test_flush_on_empty_detector_returns_none(self) -> None:
        """flush() on a freshly created (empty) detector must return None."""
        det = ChunkBoundaryDetector()
        result = det.flush()
        assert result is None, f"flush() on empty detector should be None, got {result}"

    def test_flush_sub_word_tail_returns_none(self) -> None:
        """Tails shorter than MIN_FLUSH_MS (300 ms) are noise, not speech.

        A 0.12 s loud tail (e.g. decoder padding artifacts) must be discarded —
        Whisper hallucinates full sentences on such snippets.
        """
        det = ChunkBoundaryDetector()
        det.feed(pcm_speech(0.12))

        result = det.flush()
        assert result is None, f"Sub-word tail should flush to None, got {result}"
        assert det.accumulated_ms == 0, "flush() must clear the buffer"

    def test_flush_all_silent_tail_returns_none(self) -> None:
        """A buffered tail with no speech frames is discarded, not emitted.

        Transcribing pure silence invites Whisper hallucinations, so flush()
        must return None (and still clear the buffer).
        """
        det = ChunkBoundaryDetector()
        boundaries = det.feed(pcm_silence(3.0))
        assert boundaries == [], f"Silence must not emit boundaries, got {boundaries}"

        result = det.flush()
        assert result is None, f"Silent tail should flush to None, got {result}"
        assert det.accumulated_ms == 0, "flush() must clear the buffer"

    def test_flush_after_boundary_returns_only_residual(self) -> None:
        """Tail after an emitted boundary flushes with continuous timestamps.

        6 s speech + 0.8 s silence + 2 s speech: feed() emits the first chunk
        (~6 s), flush() must return the residual starting where it ended.
        """
        audio = np.concatenate([
            pcm_speech(6.0),
            pcm_silence(0.8),
            pcm_speech(2.0),
        ])
        det = ChunkBoundaryDetector()
        boundaries = det.feed(audio)
        assert len(boundaries) == 1, f"Expected one boundary, got {boundaries}"
        _, first_end_ms, _ = boundaries[0]

        result = det.flush()
        assert result is not None, "Residual speech tail should be emitted"
        start_ms, end_ms, pcm = result
        assert start_ms == first_end_ms, (
            f"flush must continue at {first_end_ms} ms, got {start_ms}"
        )
        assert end_ms > start_ms and pcm.size > 0
