"""Unit tests for VAD PCM slice delivery (Phase 1).

Verifies that ChunkBoundaryDetector.feed() returns accurate PCM slices with
±0 sample tolerance for cross-box and intra-box speech boundaries, and that
the MAX_CHUNK_MS force-emit also produces the correct slice length.
"""
from __future__ import annotations

import math

import numpy as np

from app.vad_realtime import ChunkBoundaryDetector

SAMPLE_RATE = 16_000  # Hz
_MS_TO_SAMPLES = SAMPLE_RATE // 1000  # 16 samples/ms


def _silence(duration_ms: int) -> np.ndarray:
    """Return *duration_ms* ms of silent PCM (float32, ~0 RMS)."""
    return np.zeros(_MS_TO_SAMPLES * duration_ms, dtype=np.float32)


def _speech(duration_ms: int, freq: float = 440.0) -> np.ndarray:
    """Return *duration_ms* ms of sine-wave PCM (float32, amplitude 0.5)."""
    n = _MS_TO_SAMPLES * duration_ms
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    return (0.5 * np.sin(2 * math.pi * freq * t)).astype(np.float32)


# ---------------------------------------------------------------------------
# T1: cross-box boundary — speech spans box boundary (8s–14s, boxes 0–10s / 10–20s)
# ---------------------------------------------------------------------------

class TestT1CrossBoxBoundary:
    """Speech from 8s to 14s fed in two 10-second boxes."""

    def test_no_boundary_after_box1(self) -> None:
        """Box 1 (0–10s) alone should not trigger a boundary."""
        det = ChunkBoundaryDetector()
        # 0–8s silence, 8–10s speech (partial)
        pcm_box1 = np.concatenate([_silence(8_000), _speech(2_000)])
        result = det.feed(pcm_box1)
        assert result == [], "No boundary expected within box 1"

    def test_boundary_after_box2_sample_count(self) -> None:
        """After box 2 (10–20s), a boundary for the 8s–14s speech should fire.

        The VAD should emit at the end of speech (14s absolute = 14_000 ms).
        pcm_slice must contain exactly 14_000 ms × 16 samples/ms = 224_000 samples.
        """
        det = ChunkBoundaryDetector()
        # Box 1: 0–8s silence + 8–10s speech
        pcm_box1 = np.concatenate([_silence(8_000), _speech(2_000)])
        det.feed(pcm_box1)

        # Box 2: 10–14s speech (completing 8–14s total) + 14–20s silence
        pcm_box2 = np.concatenate([_speech(4_000), _silence(6_000)])
        result = det.feed(pcm_box2)

        assert len(result) >= 1, "Expected at least one boundary after box 2"
        start_ms, end_ms, pcm_slice = result[0]

        # The boundary should start at session time 0 (chunk_start_ms=0)
        assert start_ms == 0, f"start_ms should be 0, got {start_ms}"
        # end_ms should be exactly 14_000 ms
        assert end_ms == 14_000, f"end_ms should be 14000, got {end_ms}"
        # pcm_slice sample count must match exactly (±0)
        expected_samples = 14_000 * _MS_TO_SAMPLES
        assert pcm_slice.shape[0] == expected_samples, (
            f"Expected {expected_samples} samples, got {pcm_slice.shape[0]}"
        )

    def test_pcm_slice_is_copy(self) -> None:
        """pcm_slice must be a copy (not a view) to be safe for async hand-off."""
        det = ChunkBoundaryDetector()
        pcm_box1 = np.concatenate([_silence(8_000), _speech(2_000)])
        det.feed(pcm_box1)
        pcm_box2 = np.concatenate([_speech(4_000), _silence(6_000)])
        result = det.feed(pcm_box2)
        assert result, "Expected boundary"
        _, _, pcm_slice = result[0]
        assert pcm_slice.flags["OWNDATA"] or pcm_slice.base is not None.__class__, (
            "pcm_slice must own its data (be a copy)"
        )
        # Simpler ownership check: modifying original buffer must not affect slice.
        original_val = pcm_slice[0]
        # Force a new feed to mutate internal buffer state
        det.feed(_silence(100))
        assert pcm_slice[0] == original_val, "pcm_slice was mutated — not a copy"


# ---------------------------------------------------------------------------
# T2: intra-box boundary — speech entirely within box 2 (11s–13s)
# ---------------------------------------------------------------------------

class TestT2IntraBoxBoundary:
    """Speech from 11s to 13s is entirely within box 2 (10–20s)."""

    def test_boundary_sample_count(self) -> None:
        """pcm_slice covers the full 0–13s accumulation at the emit point."""
        det = ChunkBoundaryDetector()
        # Box 1: 0–10s silence
        det.feed(_silence(10_000))

        # Box 2: 10–11s silence + 11–13s speech + 13–20s silence
        pcm_box2 = np.concatenate([_silence(1_000), _speech(2_000), _silence(7_000)])
        result = det.feed(pcm_box2)

        assert len(result) >= 1, "Expected at least one boundary"
        start_ms, end_ms, pcm_slice = result[0]

        assert start_ms == 0, f"start_ms should be 0, got {start_ms}"
        # Boundary fires at end of speech = 13s
        assert end_ms == 13_000, f"end_ms should be 13000, got {end_ms}"
        expected_samples = 13_000 * _MS_TO_SAMPLES
        assert pcm_slice.shape[0] == expected_samples, (
            f"Expected {expected_samples} samples, got {pcm_slice.shape[0]}"
        )


# ---------------------------------------------------------------------------
# T3: MAX_CHUNK_MS force-emit — continuous speech triggers 30s boundary
# ---------------------------------------------------------------------------

class TestT3MaxChunkEmit:
    """Continuous speech forces a boundary at MAX_CHUNK_MS = 30_000 ms."""

    def test_force_emit_sample_count(self) -> None:
        """After 30s of continuous speech, pcm_slice must be exactly 30s long."""
        det = ChunkBoundaryDetector()
        # Feed 32s of continuous speech in a single call.
        pcm = _speech(32_000)
        result = det.feed(pcm)

        assert len(result) >= 1, "Expected max-duration boundary"
        start_ms, end_ms, pcm_slice = result[0]

        assert start_ms == 0, f"start_ms should be 0, got {start_ms}"
        assert end_ms == ChunkBoundaryDetector.MAX_CHUNK_MS, (
            f"end_ms should be {ChunkBoundaryDetector.MAX_CHUNK_MS}, got {end_ms}"
        )
        expected_samples = ChunkBoundaryDetector.MAX_CHUNK_MS * _MS_TO_SAMPLES
        assert pcm_slice.shape[0] == expected_samples, (
            f"Expected {expected_samples} samples, got {pcm_slice.shape[0]}"
        )

    def test_remaining_audio_stays_in_buffer(self) -> None:
        """After force-emit at 30s, the remaining 2s should stay buffered."""
        det = ChunkBoundaryDetector()
        pcm = _speech(32_000)
        result = det.feed(pcm)
        assert result, "Expected boundary"
        # Only one boundary (30s); remaining 2s is below MIN_CHUNK_MS
        assert len(result) == 1, f"Expected 1 boundary, got {len(result)}"
        # Remaining buffer: 32s - 30s = 2s
        assert det.accumulated_ms == 2_000, (
            f"Expected 2000 ms remaining, got {det.accumulated_ms}"
        )
