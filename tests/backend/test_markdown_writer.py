"""Tests for app/markdown_writer.py (Step 6.4)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta


from app.markdown_writer import write_transcript_md


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dt(year=2024, month=1, day=15, hour=9, minute=30, second=0, tz_offset_h=9):
    tz = timezone(timedelta(hours=tz_offset_h))
    return datetime(year, month, day, hour, minute, second, tzinfo=tz)


def _write_and_read(tmp_path, title, recorded_at, segments, failed_ranges) -> str:
    out = tmp_path / "transcript.md"
    write_transcript_md(title, recorded_at, segments, failed_ranges, out)
    return out.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBasicStructure:
    def test_title_header(self, tmp_path):
        content = _write_and_read(tmp_path, "My Meeting", _dt(), [], [])
        assert content.startswith("# My Meeting\n")

    def test_recorded_at_line(self, tmp_path):
        dt = _dt(2024, 3, 5, 14, 0, 0, tz_offset_h=9)
        content = _write_and_read(tmp_path, "T", dt, [], [])
        assert "녹음 일시: 2024-03-05T14:00:00+09:00" in content

    def test_separator_present(self, tmp_path):
        content = _write_and_read(tmp_path, "T", _dt(), [], [])
        assert "---" in content


class TestSegmentRendering:
    def test_sorted_segments_chronological(self, tmp_path):
        segments = [
            {"start_ms": 5000, "end_ms": 8000, "text": "second"},
            {"start_ms": 0, "end_ms": 4000, "text": "first"},
        ]
        content = _write_and_read(tmp_path, "T", _dt(), segments, [])
        idx_first = content.index("first")
        idx_second = content.index("second")
        assert idx_first < idx_second

    def test_hms_format(self, tmp_path):
        segments = [{"start_ms": 3661_000, "end_ms": 3662_000, "text": "late"}]
        content = _write_and_read(tmp_path, "T", _dt(), segments, [])
        # 3661s = 1h 1m 1s
        assert "[01:01:01–01:01:02] late" in content

    def test_hms_format_zero_seconds(self, tmp_path):
        segments = [{"start_ms": 0, "end_ms": 1000, "text": "start"}]
        content = _write_and_read(tmp_path, "T", _dt(), segments, [])
        assert "[00:00:00–00:00:01] start" in content

    def test_hms_uniform_format_always(self, tmp_path):
        """All timestamps use HH:MM:SS — no MM:SS shorthand."""
        segments = [
            {"start_ms": 0, "end_ms": 30_000, "text": "a"},
            {"start_ms": 90_000, "end_ms": 120_000, "text": "b"},
        ]
        content = _write_and_read(tmp_path, "T", _dt(), segments, [])
        assert "[00:00:00–00:00:30] a" in content
        assert "[00:01:30–00:02:00] b" in content


class TestFailedRanges:
    def test_failed_marker_text(self, tmp_path):
        failed = [{"start_ms": 0, "end_ms": 60_000}]
        content = _write_and_read(tmp_path, "T", _dt(), [], failed)
        assert "[전사 실패 구간]" in content

    def test_failed_marker_uses_en_dash(self, tmp_path):
        """The timestamp separator in failed markers must be EN-DASH (U+2013), not ASCII hyphen."""
        failed = [{"start_ms": 0, "end_ms": 60_000}]
        content = _write_and_read(tmp_path, "T", _dt(), [], failed)
        # Find the failed line and verify it contains '–' (U+2013)
        failed_line = next(line for line in content.splitlines() if "[전사 실패 구간]" in line)
        assert "–" in failed_line, f"EN-DASH not found in: {failed_line!r}"
        # Must NOT be an ASCII hyphen between the timestamps
        # The line format is [HH:MM:SS–HH:MM:SS] [전사 실패 구간]
        assert "–" in failed_line

    def test_failed_range_timestamp(self, tmp_path):
        failed = [{"start_ms": 0, "end_ms": 60_000}]
        content = _write_and_read(tmp_path, "T", _dt(), [], failed)
        assert "[00:00:00–00:01:00] [전사 실패 구간]" in content

    def test_empty_segments_non_empty_failed(self, tmp_path):
        failed = [
            {"start_ms": 0, "end_ms": 30_000},
            {"start_ms": 30_000, "end_ms": 60_000},
        ]
        content = _write_and_read(tmp_path, "T", _dt(), [], failed)
        assert content.count("[전사 실패 구간]") == 2
        # Header lines still present
        assert "# T" in content
        assert "녹음 일시:" in content


class TestMixedSorting:
    def test_failed_and_segments_sorted_together(self, tmp_path):
        segments = [{"start_ms": 70_000, "end_ms": 90_000, "text": "after failure"}]
        failed = [{"start_ms": 0, "end_ms": 60_000}]
        content = _write_and_read(tmp_path, "T", _dt(), segments, failed)

        idx_failed = content.index("[전사 실패 구간]")
        idx_after = content.index("after failure")
        assert idx_failed < idx_after

    def test_out_of_order_segments_sorted(self, tmp_path):
        segments = [
            {"start_ms": 120_000, "end_ms": 150_000, "text": "C"},
            {"start_ms": 0, "end_ms": 30_000, "text": "A"},
            {"start_ms": 60_000, "end_ms": 90_000, "text": "B"},
        ]
        content = _write_and_read(tmp_path, "T", _dt(), segments, [])
        idx_a = content.index("] A")
        idx_b = content.index("] B")
        idx_c = content.index("] C")
        assert idx_a < idx_b < idx_c


class TestSegmentTimestampsUseEndash:
    def test_segment_line_uses_en_dash(self, tmp_path):
        segments = [{"start_ms": 0, "end_ms": 5000, "text": "hello"}]
        content = _write_and_read(tmp_path, "T", _dt(), segments, [])
        seg_line = next(line for line in content.splitlines() if "hello" in line)
        assert "–" in seg_line
