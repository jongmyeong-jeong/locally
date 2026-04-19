"""Serialize/deserialize transcript segments to/from the spec format.

Spec format:
    [Xs → Ys]
    text

    [Xs → Ys]
    text
"""
from __future__ import annotations

import re

_TIMESTAMP_RE = re.compile(
    r"^\[(\d+(?:\.\d+)?)s\s*→\s*(\d+(?:\.\d+)?)s\]$"
)


def format_transcript_markdown(segments: list[dict]) -> str:
    """Serialize segment list to spec format string."""
    if not segments:
        return ""
    parts = []
    for seg in segments:
        start = float(seg["start"])
        end = float(seg["end"])
        text = seg["text"].strip()
        parts.append(f"[{start:.1f}s → {end:.1f}s]\n{text}")
    return "\n\n".join(parts)


def parse_transcript_markdown(text: str) -> list[dict]:
    """Parse spec format string back to segment list.

    Returns [] for empty input or legacy plain-markdown (no timestamp lines).
    """
    if not text or not text.strip():
        return []

    segments = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = _TIMESTAMP_RE.match(lines[i].strip())
        if m:
            start = float(m.group(1))
            end = float(m.group(2))
            body: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip():
                body.append(lines[i].strip())
                i += 1
            segments.append({"start": start, "end": end, "text": "\n".join(body)})
        else:
            i += 1
    return segments
