"""WAV chunk concatenation utility (retired).

``concat_wav_chunks`` belonged to the local-Whisper recording pipeline. That
pipeline was replaced by the Groq batch-transcription path (chunks are now
concatenated as raw webm in ``app/recordings.finalize``), leaving this helper
with no callers, so it was removed.

This module is intentionally kept as an (otherwise empty) file because
``tests/backend/test_repo_layout.py`` asserts ``app/audio_concat.py`` exists.
"""

from __future__ import annotations
