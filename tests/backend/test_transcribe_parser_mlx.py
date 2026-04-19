"""Tests for app/transcribe_parser_mlx.py: stderr → TranscribeProgress."""
from __future__ import annotations

from app import transcribe_parser_mlx as p


class TestParseDuration:
    def test_parses_duration_line(self):
        assert p.parse_duration("[DURATION] 123.456") == 123.456

    def test_ignores_non_duration_line(self):
        assert p.parse_duration("[00:01.234 --> 00:05.678] hello") is None

    def test_integer_duration(self):
        assert p.parse_duration("[DURATION] 600") == 600.0


class TestParseSegmentEnd:
    def test_parses_end_time(self):
        assert p.parse_segment_end("[00:01.234 --> 00:05.678] hello") == 5.678

    def test_parses_minutes(self):
        assert p.parse_segment_end("[00:00.000 --> 01:05.200] x") == 65.2

    def test_returns_none_for_duration_line(self):
        assert p.parse_segment_end("[DURATION] 10") is None

    def test_returns_none_for_plain_text(self):
        assert p.parse_segment_end("hello world") is None


class TestParseSegmentLine:
    def test_parses_full_segment(self):
        got = p.parse_segment_line("[00:01.000 --> 00:05.500] hello world")
        assert got == {"start": 1.0, "end": 5.5, "text": "hello world"}

    def test_strips_whitespace_around_text(self):
        got = p.parse_segment_line("[00:00.000 --> 00:01.000]   padded   ")
        assert got == {"start": 0.0, "end": 1.0, "text": "padded"}


class TestParseProgressLine:
    def test_valid_segment_line_produces_progress(self):
        out = p.parse_progress_line(
            "[00:00.000 --> 00:30.000] hello",
            audio_duration_sec=60.0,
            segment_count=1,
        )
        assert out is not None
        assert out["percent"] == pytest_approx(0.5)
        assert out["segment_count"] == 1
        assert "elapsed_sec" in out

    def test_vad_skip_marker_returns_none(self):
        """VAD skip markers like '[00:00.000 --> 00:00.000]' or [INFO] lines."""
        out = p.parse_progress_line(
            "[INFO] VAD skipped a silent region",
            audio_duration_sec=60.0,
        )
        assert out is None

    def test_percent_capped_at_one(self):
        out = p.parse_progress_line(
            "[00:00.000 --> 05:00.000] x",
            audio_duration_sec=60.0,
        )
        assert out["percent"] == 1.0

    def test_missing_duration_returns_none(self):
        out = p.parse_progress_line(
            "[00:00.000 --> 00:01.000] x",
            audio_duration_sec=0.0,
        )
        assert out is None

    def test_non_segment_line_returns_none(self):
        assert (
            p.parse_progress_line(
                "random log line", audio_duration_sec=30.0
            )
            is None
        )


class TestCalcProgress:
    def test_fraction(self):
        assert p.calc_progress(30.0, 60.0) == 0.5

    def test_caps_at_one(self):
        assert p.calc_progress(120.0, 60.0) == 1.0

    def test_zero_duration_returns_none(self):
        assert p.calc_progress(10.0, 0.0) is None

    def test_negative_duration_returns_none(self):
        assert p.calc_progress(10.0, -1.0) is None


# Tiny float-compare helper (avoids bringing in pytest.approx importer).
def pytest_approx(val, tol=1e-6):
    class _A:
        def __eq__(self, other):
            return abs(other - val) < tol

        def __repr__(self):
            return f"~{val}"

    return _A()
