"""Wrap faster-whisper's (segment, info) generator into shared progress shape.

faster-whisper yields Segment objects with .start, .end, .text attributes.
We normalize to the same TranscribeProgress dict as MLX.
"""
from __future__ import annotations

import time
from typing import Iterable, Iterator


def wrap_generator(
    segments_iter: Iterable,
    *,
    audio_duration_sec: float,
) -> Iterator[tuple[dict, dict]]:
    """Yield (TranscribeProgress, {'start','end','text'}) per segment.

    percent = min(segment.end / audio_duration_sec, 1.0).
    """
    started = time.monotonic()
    count = 0
    for seg in segments_iter:
        count += 1
        # faster-whisper Segment is a namedtuple-like; support both attr and dict access.
        start = getattr(seg, "start", None)
        end = getattr(seg, "end", None)
        text = getattr(seg, "text", None)
        if start is None and isinstance(seg, dict):
            start = seg.get("start")
            end = seg.get("end")
            text = seg.get("text")
        if audio_duration_sec and audio_duration_sec > 0:
            percent = min(float(end) / audio_duration_sec, 1.0)
        else:
            percent = 0.0
        progress = {
            "percent": percent,
            "segment_count": count,
            "elapsed_sec": time.monotonic() - started,
        }
        segment_dict = {
            "start": float(start) if start is not None else 0.0,
            "end": float(end) if end is not None else 0.0,
            "text": (text or "").strip(),
        }
        yield progress, segment_dict
