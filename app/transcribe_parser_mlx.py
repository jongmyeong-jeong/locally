"""Parse mlx-whisper stderr lines into normalized progress payloads.

Lines of interest (verbatim from the mlx script):
  [DURATION] 123.456
  [00:01.234 --> 00:05.678] hello world

Shared TranscribeProgress shape (A3):
  {'percent': float in [0,1], 'segment_count': int, 'elapsed_sec': float}
"""
from __future__ import annotations

import re

_DURATION_RE = re.compile(r"^\[DURATION\]\s+([\d.]+)")
_SEGMENT_RE = re.compile(
    r"^\[\d{2}:\d{2}\.\d{3}\s*-->\s*(\d{2}):(\d{2})\.(\d{3})\]"
)


def parse_duration(line: str) -> float | None:
    """Return total audio duration in seconds, or None."""
    m = _DURATION_RE.match(line)
    return float(m.group(1)) if m else None


def parse_segment_end(line: str) -> float | None:
    """Return the end-time in seconds of a segment line, or None."""
    m = _SEGMENT_RE.match(line)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / 1000


def parse_segment_line(line: str) -> dict | None:
    """Parse '[mm:ss.sss --> mm:ss.sss] text' → {start, end, text}."""
    m = re.match(
        r"^\[(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{2}):(\d{2})\.(\d{3})\]\s*(.*)$",
        line,
    )
    if not m:
        return None
    start = int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / 1000
    end = int(m.group(4)) * 60 + int(m.group(5)) + int(m.group(6)) / 1000
    return {"start": start, "end": end, "text": m.group(7).strip()}


def calc_progress(end_sec: float, total_duration: float | None) -> float | None:
    """Return fraction in [0,1] or None if total_duration is missing/invalid."""
    if not total_duration or total_duration <= 0:
        return None
    return min(end_sec / total_duration, 1.0)


def parse_progress_line(
    line: str,
    *,
    audio_duration_sec: float,
    segment_count: int = 0,
    elapsed_sec: float = 0.0,
) -> dict | None:
    """Parse one stderr line; return TranscribeProgress or None.

    Non-segment lines (e.g., [DURATION], [INFO]) return None.
    """
    end = parse_segment_end(line)
    if end is None:
        return None
    percent = calc_progress(end, audio_duration_sec)
    if percent is None:
        return None
    return {
        "percent": percent,
        "segment_count": segment_count,
        "elapsed_sec": elapsed_sec,
    }
