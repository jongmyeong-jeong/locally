"""Energy-based Voice Activity Detection (numpy-only).

Implements ``detect_speech_timestamps`` per the reference spec:
  - copybara-note: ``docs/spec/03-transcription.md`` §82-100 (authoritative parameters)
  - locally plan:  ``.omc/plans/consensus-transcription-progress-vad.md`` §2.3, §2.9

Used by the mlx-whisper path in ``app/transcribe.py`` to build a
``--clip-timestamps`` argument. On "no speech" / "skip" conditions the
function returns an empty list; the caller is expected to fall back to the
single-token ``"0"`` form (mlx_whisper treats the trailing lone start as
implicit EOF).

Pure numpy. No scipy, torch, silero, librosa. No new dependencies.
"""
from __future__ import annotations

import os
import sys

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

# ---------------------------------------------------------------------------
# Public constants (exposed for transparency / testability).
# Values match copybara-note §82-96 exactly.
# ---------------------------------------------------------------------------
SAMPLE_RATE = 16_000
FRAME_LEN = 800              # 50 ms @ 16 kHz
HOP_LEN = 400                # 25 ms @ 16 kHz
MIN_SILENCE_FRAMES = 24      # fill silences shorter than ~600 ms
MIN_SPEECH_FRAMES = 12       # drop speech runs shorter than ~300 ms
PAD_MS = 200                 # symmetric padding per interval
THRESHOLD_FLOOR = 0.005
SKIP_IF_VOICE_RATIO_GT = 0.9


def _flip_short_runs(mask: np.ndarray, value: bool, min_len: int) -> np.ndarray:
    """Flip runs of ``value`` shorter than ``min_len`` to ``not value``.

    Operates in place on a copy and returns the new array.
    """
    if mask.size == 0 or min_len <= 1:
        return mask.copy()
    out = mask.copy()
    n = out.size
    i = 0
    while i < n:
        if bool(out[i]) == value:
            j = i
            while j < n and bool(out[j]) == value:
                j += 1
            if (j - i) < min_len:
                out[i:j] = not value
            i = j
        else:
            i += 1
    return out


def _mask_to_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Return list of (start_frame, end_frame_exclusive) for True runs."""
    if mask.size == 0:
        return []
    # Pad with False on either end to find edges via diff.
    padded = np.concatenate(([False], mask.astype(bool), [False]))
    diffs = np.diff(padded.astype(np.int8))
    starts = np.flatnonzero(diffs == 1)
    ends = np.flatnonzero(diffs == -1)
    return [(int(s), int(e)) for s, e in zip(starts, ends)]


def _pad_and_merge(
    intervals: list[tuple[float, float]],
    pad_sec: float,
    duration_sec: float,
) -> list[tuple[float, float]]:
    """Pad each (s, e) by pad_sec symmetrically, clamp, then merge overlaps."""
    if not intervals:
        return []
    padded: list[tuple[float, float]] = []
    for s, e in intervals:
        ps = max(0.0, s - pad_sec)
        pe = min(duration_sec, e + pad_sec)
        if pe > ps:
            padded.append((ps, pe))
    if not padded:
        return []
    padded.sort()
    merged: list[tuple[float, float]] = [padded[0]]
    for s, e in padded[1:]:
        last_s, last_e = merged[-1]
        if s <= last_e:
            merged[-1] = (last_s, max(last_e, e))
        else:
            merged.append((s, e))
    return merged


def detect_speech_timestamps(
    pcm: "np.ndarray",
    sample_rate: int = SAMPLE_RATE,
) -> list[tuple[float, float]]:
    """Return list of (start_sec, end_sec) speech intervals.

    Empty list = no speech detected (caller should fall back to full clip).
    Intervals are non-overlapping, monotonically increasing, padded, and merged.

    The input ``pcm`` must be float32 mono at ``sample_rate`` (default 16 kHz).
    """
    assert sample_rate == SAMPLE_RATE, (
        f"vad expects {SAMPLE_RATE} Hz, got {sample_rate}"
    )
    assert isinstance(pcm, np.ndarray), "pcm must be a numpy array"
    assert pcm.dtype == np.float32, f"pcm must be float32, got {pcm.dtype}"
    assert pcm.ndim == 1, f"pcm must be mono (1-D), got shape {pcm.shape}"

    duration_sec = float(pcm.size) / float(sample_rate)

    # Not enough samples for a single frame → empty.
    if pcm.size < FRAME_LEN:
        _debug_log(intervals=0, speech_ratio=0.0, threshold=0.0)
        return []

    # 1. Frame via sliding window with HOP_LEN stride.
    windows = sliding_window_view(pcm, window_shape=FRAME_LEN)[::HOP_LEN]
    # Expected frame count: 1 + (len(pcm) - FRAME_LEN) // HOP_LEN.
    # 2. RMS per frame.
    rms = np.sqrt(np.mean(windows.astype(np.float32) ** 2, axis=1))

    # 3. Threshold.
    threshold = float(max(np.percentile(rms, 15) * 3, THRESHOLD_FLOOR))

    # 4. Voiced mask.
    voiced_mask = rms >= threshold

    # 5. Skip trigger (too-loud / mostly-speech → let caller use full clip).
    if voiced_mask.size == 0:
        _debug_log(intervals=0, speech_ratio=0.0, threshold=threshold)
        return []
    speech_ratio = float(voiced_mask.mean())
    if speech_ratio > SKIP_IF_VOICE_RATIO_GT:
        _debug_log(
            intervals=0, speech_ratio=speech_ratio, threshold=threshold
        )
        return []

    # 6. Fill short silences: runs of False shorter than MIN_SILENCE_FRAMES → True.
    mask = _flip_short_runs(voiced_mask, value=False, min_len=MIN_SILENCE_FRAMES)
    # 7. Remove short speech: runs of True shorter than MIN_SPEECH_FRAMES → False.
    mask = _flip_short_runs(mask, value=True, min_len=MIN_SPEECH_FRAMES)

    # 8. Convert frame runs to seconds using HOP-based conversion
    #    (consistent with mlx). A frame run [i, j) maps to time range
    #    [i * HOP_LEN / SR, j * HOP_LEN / SR).
    runs = _mask_to_runs(mask)
    raw_intervals: list[tuple[float, float]] = []
    for run_start, run_end in runs:
        t_start = (run_start * HOP_LEN) / sample_rate
        t_end = (run_end * HOP_LEN) / sample_rate
        t_start = max(0.0, min(t_start, duration_sec))
        t_end = max(0.0, min(t_end, duration_sec))
        if t_end > t_start:
            raw_intervals.append((t_start, t_end))

    # 9. Pad, clamp, merge.
    pad_sec = PAD_MS / 1000.0
    merged = _pad_and_merge(raw_intervals, pad_sec, duration_sec)

    _debug_log(
        intervals=len(merged),
        speech_ratio=speech_ratio,
        threshold=threshold,
    )
    return merged


def _debug_log(*, intervals: int, speech_ratio: float, threshold: float) -> None:
    """Emit one line to stderr when LOCALLY_DEBUG=1 (§2.9 observability stub)."""
    if os.environ.get("LOCALLY_DEBUG") != "1":
        return
    print(
        f"[vad] intervals={intervals} "
        f"speech_ratio={speech_ratio:.2f} "
        f"threshold={threshold:.4f}",
        file=sys.stderr,
        flush=True,
    )
