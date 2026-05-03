"""WAV chunk concatenation utility.

Concatenates multiple 16kHz mono int16 WAV files into a single output file.
Each input chunk is validated before concatenation to catch any format drift.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import scipy.io.wavfile as wavfile


def concat_wav_chunks(wav_paths: list[Path], output_path: Path) -> None:
    """Concatenate a list of WAV chunks into a single WAV file.

    All input files **must** be 16 kHz, mono, 16-bit PCM (int16).
    A :class:`ValueError` is raised immediately if any file deviates from
    these parameters (Architect fix A2).

    Args:
        wav_paths: Ordered list of WAV file paths to concatenate.
        output_path: Destination WAV file path (created/overwritten).

    Raises:
        ValueError: If any chunk has a sample rate other than 16000,
            a dtype other than int16, or more than one channel.
    """
    if not wav_paths:
        raise ValueError("no chunks to concatenate")

    chunks: list[np.ndarray] = []

    for path in wav_paths:
        sr, data = wavfile.read(str(path))

        if sr != 16000:
            raise ValueError(
                f"Expected sample rate 16000 Hz, got {sr} Hz: {path}"
            )
        if data.dtype != np.int16:
            raise ValueError(
                f"Expected dtype int16, got {data.dtype}: {path}"
            )
        if data.ndim != 1:
            raise ValueError(
                f"Expected mono (1-channel) audio, got shape {data.shape}: {path}"
            )

        chunks.append(data)

    concatenated = np.concatenate(chunks)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(output_path), 16000, concatenated)
