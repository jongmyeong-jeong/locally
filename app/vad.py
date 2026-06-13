"""Voice-activity-detection constants (energy-based, 16 kHz mono).

Only the frame/threshold CONSTANTS below remain in use: ``app/batch_transcribe.py``
imports ``FRAME_LEN``, ``HOP_LEN`` and ``THRESHOLD_FLOOR`` to refine silence-
boundary cuts when splitting long audio for Groq upload.

The ``detect_speech_timestamps`` function and its numpy helpers belonged to the
local mlx-whisper transcription path, which was removed when transcription moved
to the Groq batch API. They had no remaining production caller and were deleted;
the constants are kept so the split-boundary search stays consistent with the
original VAD parameters.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Public constants (values match the original VAD spec exactly).
# ---------------------------------------------------------------------------
SAMPLE_RATE = 16_000
FRAME_LEN = 800              # 50 ms @ 16 kHz
HOP_LEN = 400                # 25 ms @ 16 kHz
MIN_SILENCE_FRAMES = 24      # fill silences shorter than ~600 ms
MIN_SPEECH_FRAMES = 12       # drop speech runs shorter than ~300 ms
PAD_MS = 200                 # symmetric padding per interval
THRESHOLD_FLOOR = 0.005
SKIP_IF_VOICE_RATIO_GT = 0.9
