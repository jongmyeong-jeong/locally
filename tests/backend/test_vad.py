"""Tests for app/vad.py — energy-based VAD (A3, A3b, A4 + defensive cases).

Synthesises all PCM fixtures with numpy; no binary audio files committed.
All fixtures use np.float32 at 16 kHz to match load_pcm_16k_mono output.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.vad import (
    FRAME_LEN,
    HOP_LEN,
    MIN_SILENCE_FRAMES,
    MIN_SPEECH_FRAMES,
    PAD_MS,
    SAMPLE_RATE,
    SKIP_IF_VOICE_RATIO_GT,
    THRESHOLD_FLOOR,
    detect_speech_timestamps,
)

SR = 16_000  # convenience alias


# ---------------------------------------------------------------------------
# Synthesis helpers
# ---------------------------------------------------------------------------

def _silence(duration_sec: float) -> np.ndarray:
    """Return float32 zeros for the requested duration."""
    n = int(duration_sec * SR)
    return np.zeros(n, dtype=np.float32)


def _tone(duration_sec: float, freq: float = 440.0, amplitude: float = 0.2) -> np.ndarray:
    """Return float32 sine wave; amplitude 0.2 gives RMS ~0.14, well above THRESHOLD_FLOOR."""
    n = int(duration_sec * SR)
    t = np.arange(n, dtype=np.float32) / SR
    return (amplitude * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)


# ---------------------------------------------------------------------------
# A3 — Basic detection
# Structure: 1s silence + 2s tone + 0.5s silence + 1s tone = 4.5s total
# The 0.5s gap is SHORTER than MIN_SILENCE_FRAMES * HOP_LEN / SR
#   = 24 * 400 / 16000 = 0.6s threshold, so it may merge.
# The plan's A3 fixture uses a gap > MIN_SILENCE threshold to get 2 intervals.
# Plan §3 specifies gap 0.8s > PAD_MS to expect 2 intervals.
# Using: 1s silence + 2s tone + 0.8s silence + 1.5s tone
# Gap 0.8s > MIN_SILENCE_FRAMES*HOP_LEN/SR=0.6s so silence is NOT filled → 2 intervals.
# ---------------------------------------------------------------------------

class TestA3BasicDetection:
    """A3 — detect_speech_timestamps returns intervals covering speech regions."""

    @pytest.fixture
    def pcm_speech_gap_speech(self) -> np.ndarray:
        # 1s silence | 2s tone | 0.8s silence | 1.5s tone
        # Gap 0.8s > MIN_SILENCE threshold (~0.6s) → expect 2 intervals
        return np.concatenate([
            _silence(1.0),
            _tone(2.0, freq=440.0),
            _silence(0.8),
            _tone(1.5, freq=220.0),
        ])

    def test_returns_nonempty_list(self, pcm_speech_gap_speech):
        result = detect_speech_timestamps(pcm_speech_gap_speech, SR)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_returns_list_of_float_tuples(self, pcm_speech_gap_speech):
        result = detect_speech_timestamps(pcm_speech_gap_speech, SR)
        for item in result:
            assert isinstance(item, tuple), f"Expected tuple, got {type(item)}"
            assert len(item) == 2, f"Expected 2-tuple, got {len(item)}-tuple"
            s, e = item
            assert isinstance(s, float), f"start is not float: {type(s)}"
            assert isinstance(e, float), f"end is not float: {type(e)}"

    def test_intervals_are_monotonically_non_overlapping(self, pcm_speech_gap_speech):
        result = detect_speech_timestamps(pcm_speech_gap_speech, SR)
        for i in range(len(result) - 1):
            _, e_cur = result[i]
            s_next, _ = result[i + 1]
            assert e_cur <= s_next, (
                f"Overlapping intervals at index {i}: end={e_cur} > next_start={s_next}"
            )

    def test_intervals_have_positive_duration(self, pcm_speech_gap_speech):
        result = detect_speech_timestamps(pcm_speech_gap_speech, SR)
        for s, e in result:
            assert e > s, f"Non-positive interval: ({s}, {e})"

    def test_at_least_one_interval_overlaps_first_speech_region(self, pcm_speech_gap_speech):
        # First speech region: [1.0, 3.0] (with PAD_MS=200ms tolerance)
        pad = PAD_MS / 1000.0
        result = detect_speech_timestamps(pcm_speech_gap_speech, SR)
        first_speech_start = 1.0
        first_speech_end = 3.0
        overlaps = [
            (s, e) for s, e in result
            if s < first_speech_end + pad and e > first_speech_start - pad
        ]
        assert len(overlaps) >= 1, (
            f"No interval overlaps first speech region "
            f"[{first_speech_start}, {first_speech_end}] "
            f"(±{pad}s). Got: {result}"
        )

    def test_at_least_one_interval_overlaps_second_speech_region(self, pcm_speech_gap_speech):
        # Second speech region: [3.8, 5.3] (1s+2s+0.8s=3.8s start, +1.5s=5.3s end)
        pad = PAD_MS / 1000.0
        result = detect_speech_timestamps(pcm_speech_gap_speech, SR)
        second_speech_start = 3.8
        second_speech_end = 5.3
        overlaps = [
            (s, e) for s, e in result
            if s < second_speech_end + pad and e > second_speech_start - pad
        ]
        assert len(overlaps) >= 1, (
            f"No interval overlaps second speech region "
            f"[{second_speech_start}, {second_speech_end}] "
            f"(±{pad}s). Got: {result}"
        )

    def test_two_intervals_for_long_gap(self, pcm_speech_gap_speech):
        # The 0.8s silence gap > MIN_SILENCE_FRAMES * HOP_LEN / SR = 24 * 400 / 16000 = 0.6s
        # So silence is NOT filled → two separate speech intervals expected
        result = detect_speech_timestamps(pcm_speech_gap_speech, SR)
        assert len(result) == 2, (
            f"Expected 2 intervals (gap exceeds MIN_SILENCE threshold), got {len(result)}: {result}"
        )


# ---------------------------------------------------------------------------
# A3b — Padding + merge
# Structure: 1s tone + 0.2s silence + 1s tone = 2.2s
# Gap 0.2s < 2 × PAD_MS (0.4s) → after padding the two intervals overlap → merge to 1
# ---------------------------------------------------------------------------

class TestA3bPaddingMerge:
    """A3b — two close speech bursts merge into one after PAD_MS padding."""

    @pytest.fixture
    def pcm_close_speech(self) -> np.ndarray:
        # Plan §3 A3b fixture: 1s silence | 0.5s tone | 0.2s silence | 0.8s tone | 0.5s silence
        # The flanking silence keeps the adaptive threshold anchored at THRESHOLD_FLOOR (0.005),
        # so tone frames are correctly classified as speech.
        # Gap 0.2s < 2 × PAD_MS (0.4s) → after padding the two intervals overlap → merge to 1.
        # Without flanking silence the 15th-percentile RMS equals the tone RMS, driving
        # threshold = toneRMS × 3 ≈ 0.42, which silences all frames → []. (verified in diagnosis)
        return np.concatenate([
            _silence(1.0),
            _tone(0.5, freq=440.0),
            _silence(0.2),
            _tone(0.8, freq=220.0),
            _silence(0.5),
        ])

    def test_returns_exactly_one_interval(self, pcm_close_speech):
        result = detect_speech_timestamps(pcm_close_speech, SR)
        assert len(result) == 1, (
            f"Expected 1 merged interval (gap 0.2s < 2*PAD_MS=0.4s), "
            f"got {len(result)}: {result}"
        )

    def test_merged_interval_covers_full_speech(self, pcm_close_speech):
        result = detect_speech_timestamps(pcm_close_speech, SR)
        assert len(result) == 1
        s, e = result[0]
        # Audio is 3.0s (1s silence + 0.5s tone + 0.2s silence + 0.8s tone + 0.5s silence).
        # Speech spans [1.0, 1.5] and [1.7, 2.5]. After PAD_MS=200ms padding, the padded
        # intervals are [0.8, 1.7] and [1.5, 2.7] which overlap and merge to [0.8, 2.7].
        # The start must be ≤ (first_speech_start - pad) + 1 frame tolerance (~0.05s).
        # The end must be ≥ (last_speech_end + pad) - 1 frame tolerance.
        duration = 3.0
        pad = PAD_MS / 1000.0
        first_speech_start = 1.0
        last_speech_end = 2.5  # 1.0 + 0.5 + 0.2 + 0.8
        frame_tol = 0.05  # ~2 hop lengths of tolerance
        assert s <= first_speech_start - pad + frame_tol, (
            f"Merged interval start {s:.3f} should be near or before "
            f"{first_speech_start - pad:.3f} (first speech onset minus pad)"
        )
        assert e >= last_speech_end + pad - frame_tol, (
            f"Merged interval end {e:.3f} should be near or after "
            f"{last_speech_end + pad:.3f} (last speech end plus pad)"
        )
        # Bounds must be clamped within audio duration
        assert s >= 0.0
        assert e <= duration + 1e-6

    def test_merged_interval_does_not_exceed_audio_bounds(self, pcm_close_speech):
        duration = len(pcm_close_speech) / SR
        result = detect_speech_timestamps(pcm_close_speech, SR)
        for s, e in result:
            assert s >= 0.0, f"Interval start {s} is negative"
            assert e <= duration + 1e-6, (
                f"Interval end {e:.4f} exceeds audio duration {duration:.4f}"
            )


# ---------------------------------------------------------------------------
# A4 — VAD skip on full speech (speech_ratio > SKIP_IF_VOICE_RATIO_GT)
# Structure: 10s continuous sine wave → speech_ratio ≈ 1.0 → returns []
# ---------------------------------------------------------------------------

class TestA4SkipOnFullSpeech:
    """A4 — detect_speech_timestamps returns [] when speech_ratio > 0.9."""

    @pytest.fixture
    def pcm_continuous_speech(self) -> np.ndarray:
        # 10s continuous tone → effectively 100% speech
        return _tone(10.0, freq=440.0, amplitude=0.2)

    def test_returns_empty_list(self, pcm_continuous_speech):
        result = detect_speech_timestamps(pcm_continuous_speech, SR)
        assert result == [], (
            f"Expected [] when speech_ratio > {SKIP_IF_VOICE_RATIO_GT}, got {result}"
        )

    def test_return_type_is_list(self, pcm_continuous_speech):
        result = detect_speech_timestamps(pcm_continuous_speech, SR)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Defensive tests — boundary and edge cases
# ---------------------------------------------------------------------------

class TestDefensiveCases:
    """Boundary conditions: too-short pcm, all-zeros pcm."""

    def test_too_short_pcm_returns_empty(self):
        # pcm shorter than FRAME_LEN (800 samples) → cannot form even one frame
        short_pcm = np.zeros(FRAME_LEN - 1, dtype=np.float32)
        result = detect_speech_timestamps(short_pcm, SR)
        assert result == [], (
            f"Expected [] for pcm shorter than FRAME_LEN ({FRAME_LEN}), got {result}"
        )

    def test_exactly_frame_len_silence_returns_empty(self):
        # Exactly FRAME_LEN samples but all silence → no speech
        pcm = np.zeros(FRAME_LEN, dtype=np.float32)
        result = detect_speech_timestamps(pcm, SR)
        assert result == [], f"Expected [] for all-zero pcm of length FRAME_LEN, got {result}"

    def test_all_zeros_pcm_returns_empty(self):
        # 5 seconds of silence → no speech detected
        silent_pcm = np.zeros(5 * SR, dtype=np.float32)
        result = detect_speech_timestamps(silent_pcm, SR)
        assert result == [], f"Expected [] for all-zero pcm, got {result}"

    def test_rejects_wrong_sample_rate(self):
        pcm = np.zeros(SR, dtype=np.float32)
        with pytest.raises(AssertionError):
            detect_speech_timestamps(pcm, 8000)

    def test_rejects_non_float32_dtype(self):
        pcm = np.zeros(SR, dtype=np.float64)
        with pytest.raises(AssertionError):
            detect_speech_timestamps(pcm, SR)

    def test_rejects_2d_array(self):
        pcm = np.zeros((2, SR), dtype=np.float32)
        with pytest.raises(AssertionError):
            detect_speech_timestamps(pcm, SR)


# ---------------------------------------------------------------------------
# Constants sanity — pin spec parameters so future tweaks are noticed
# ---------------------------------------------------------------------------

class TestConstantsSanity:
    """Pin the spec-mandated VAD parameters; a drift here is a spec violation."""

    def test_frame_len(self):
        assert FRAME_LEN == 800, f"FRAME_LEN must be 800 (50ms @ 16kHz), got {FRAME_LEN}"

    def test_hop_len(self):
        assert HOP_LEN == 400, f"HOP_LEN must be 400 (25ms @ 16kHz), got {HOP_LEN}"

    def test_sample_rate(self):
        assert SAMPLE_RATE == 16_000, f"SAMPLE_RATE must be 16000, got {SAMPLE_RATE}"

    def test_min_silence_frames(self):
        assert MIN_SILENCE_FRAMES == 24, (
            f"MIN_SILENCE_FRAMES must be 24 (~600ms), got {MIN_SILENCE_FRAMES}"
        )

    def test_min_speech_frames(self):
        assert MIN_SPEECH_FRAMES == 12, (
            f"MIN_SPEECH_FRAMES must be 12 (~300ms), got {MIN_SPEECH_FRAMES}"
        )

    def test_pad_ms(self):
        assert PAD_MS == 200, f"PAD_MS must be 200ms, got {PAD_MS}"

    def test_threshold_floor(self):
        assert THRESHOLD_FLOOR == 0.005, (
            f"THRESHOLD_FLOOR must be 0.005, got {THRESHOLD_FLOOR}"
        )

    def test_skip_if_voice_ratio_gt(self):
        assert SKIP_IF_VOICE_RATIO_GT == 0.9, (
            f"SKIP_IF_VOICE_RATIO_GT must be 0.9, got {SKIP_IF_VOICE_RATIO_GT}"
        )

    def test_min_silence_threshold_in_seconds(self):
        # Derived: 24 frames × 400 samples/frame / 16000 Hz = 0.6s
        silence_threshold_sec = MIN_SILENCE_FRAMES * HOP_LEN / SAMPLE_RATE
        assert abs(silence_threshold_sec - 0.6) < 1e-6, (
            f"MIN_SILENCE threshold must be 0.6s, got {silence_threshold_sec}"
        )

    def test_pad_in_seconds(self):
        assert PAD_MS / 1000.0 == 0.2
