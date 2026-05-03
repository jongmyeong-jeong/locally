"""Markdown transcript writer.

Converts a flat list of transcription segments and failed ranges into a
human-readable .md file with chronological time-stamped lines.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def _ms_to_hms(ms: int) -> str:
    """Format milliseconds as HH:MM:SS."""
    total_s = ms // 1000
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def write_transcript_md(
    title: str,
    recorded_at: datetime,
    segments: list[dict],
    failed_ranges: list[dict],
    output_path: Path,
) -> None:
    """Write a timestamped Markdown transcript file.

    Args:
        title: Human-readable recording title.
        recorded_at: Timezone-aware datetime of the recording start.
        segments: Flat list of segment dicts, each with keys:
            - ``start_ms`` (int): segment start in milliseconds.
            - ``end_ms`` (int): segment end in milliseconds.
            - ``text`` (str): segment transcription text.
            Caller is responsible for flattening groq batch segments
            into this list before calling.
        failed_ranges: List of dicts with ``start_ms`` (int) and
            ``end_ms`` (int) for ranges that exhausted all retries.
        output_path: Destination ``.md`` file path (created/overwritten).
    """
    # Build a flat list of renderable items:
    # Each item is (start_ms, end_ms, text_or_None)
    # None text means 전사 실패.
    items: list[tuple[int, int, str | None]] = []

    for seg in segments:
        items.append((seg["start_ms"], seg["end_ms"], seg["text"].strip()))

    for fr in failed_ranges:
        items.append((fr["start_ms"], fr["end_ms"], None))

    # Chronological sort: primary key = start_ms, secondary = end_ms.
    items.sort(key=lambda x: (x[0], x[1]))

    # Format recorded_at in ISO 8601 with UTC offset.
    recorded_at_str = recorded_at.isoformat(timespec="seconds")

    lines: list[str] = [
        f"# {title}",
        "",
        f"녹음 일시: {recorded_at_str}",
        "",
        "---",
        "",
    ]

    for start_ms, end_ms, text in items:
        start_fmt = _ms_to_hms(start_ms)
        end_fmt = _ms_to_hms(end_ms)
        if text is not None:
            lines.append(f"[{start_fmt}–{end_fmt}] {text}")
        else:
            lines.append(f"[{start_fmt}–{end_fmt}] (전사 실패)")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
