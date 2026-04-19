"""Tests for app/transcript_format.py: format/parse round-trip."""
from __future__ import annotations

import pytest

from app import transcript_format as tf


class TestFormatTranscriptMarkdown:
    def test_empty_list_returns_empty_string(self):
        assert tf.format_transcript_markdown([]) == ""

    def test_single_segment(self):
        segs = [{"start": 0.0, "end": 1.5, "text": "hello"}]
        result = tf.format_transcript_markdown(segs)
        assert result == "[0.0s → 1.5s]\nhello"

    def test_multiple_segments_separated_by_blank_line(self):
        segs = [
            {"start": 0.0, "end": 1.0, "text": "first"},
            {"start": 1.0, "end": 2.5, "text": "second"},
        ]
        result = tf.format_transcript_markdown(segs)
        assert result == "[0.0s → 1.0s]\nfirst\n\n[1.0s → 2.5s]\nsecond"

    def test_one_decimal_precision_fixed(self):
        segs = [{"start": 0.0, "end": 1.25, "text": "x"}]
        result = tf.format_transcript_markdown(segs)
        # 1.25 rounded to 1dp is 1.2; end value appears as "→ 1.2s]"
        assert "1.2s]" in result
        assert "." in result.split("→")[1].split("s]")[0]

    def test_integer_timestamps_formatted_with_decimal(self):
        segs = [{"start": 0, "end": 10, "text": "x"}]
        result = tf.format_transcript_markdown(segs)
        assert result == "[0.0s → 10.0s]\nx"

    def test_uses_unicode_arrow_not_ascii(self):
        segs = [{"start": 0.0, "end": 1.0, "text": "x"}]
        result = tf.format_transcript_markdown(segs)
        assert "→" in result
        assert "->" not in result


class TestParseTranscriptMarkdown:
    def test_empty_string_returns_empty_list(self):
        assert tf.parse_transcript_markdown("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert tf.parse_transcript_markdown("   \n\n  ") == []

    def test_legacy_plain_markdown_returns_empty_list(self):
        assert tf.parse_transcript_markdown("# Title\n\nSome text here.") == []

    def test_single_segment(self):
        text = "[0.0s → 1.5s]\nhello"
        result = tf.parse_transcript_markdown(text)
        assert result == [{"start": 0.0, "end": 1.5, "text": "hello"}]

    def test_multiple_segments(self):
        text = "[0.0s → 1.0s]\nfirst\n\n[1.0s → 2.5s]\nsecond"
        result = tf.parse_transcript_markdown(text)
        assert len(result) == 2
        assert result[0] == {"start": 0.0, "end": 1.0, "text": "first"}
        assert result[1] == {"start": 1.0, "end": 2.5, "text": "second"}

    def test_ascii_arrow_not_matched(self):
        text = "[0.0s -> 1.0s]\nhello"
        assert tf.parse_transcript_markdown(text) == []

    def test_unicode_arrow_matched(self):
        text = "[0.0s → 1.0s]\nhello"
        result = tf.parse_transcript_markdown(text)
        assert len(result) == 1


class TestRoundTrip:
    def test_empty_round_trip(self):
        assert tf.parse_transcript_markdown(tf.format_transcript_markdown([])) == []

    def test_single_segment_round_trip(self):
        segs = [{"start": 0.0, "end": 1.5, "text": "hello world"}]
        assert tf.parse_transcript_markdown(tf.format_transcript_markdown(segs)) == segs

    def test_multiple_segments_round_trip(self):
        segs = [
            {"start": 0.0, "end": 5.3, "text": "첫 번째 세그먼트"},
            {"start": 5.3, "end": 12.7, "text": "두 번째 세그먼트"},
            {"start": 12.7, "end": 20.0, "text": "세 번째 세그먼트"},
        ]
        result = tf.parse_transcript_markdown(tf.format_transcript_markdown(segs))
        assert result == segs

    def test_precision_normalised_after_round_trip(self):
        segs = [{"start": 1.25, "end": 2.75, "text": "x"}]
        formatted = tf.format_transcript_markdown(segs)
        parsed = tf.parse_transcript_markdown(formatted)
        # After round-trip, values are rounded to 1dp
        assert parsed[0]["start"] == round(1.25, 1)
        assert parsed[0]["end"] == round(2.75, 1)
