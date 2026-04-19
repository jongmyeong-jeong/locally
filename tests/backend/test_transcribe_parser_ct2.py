"""Tests for app/transcribe_parser_ct2.py: faster-whisper generator wrapping."""
from __future__ import annotations

from app import transcribe_parser_ct2 as p


class _FakeSeg:
    """Mimics faster_whisper.Segment (namedtuple-ish with .start/.end/.text)."""

    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text


class TestWrapGenerator:
    def test_yields_normalized_shape_per_segment(self):
        segs = [
            _FakeSeg(0.0, 10.0, "hello"),
            _FakeSeg(10.0, 20.0, "world"),
        ]
        out = list(p.wrap_generator(segs, audio_duration_sec=30.0))
        assert len(out) == 2
        progress_0, seg_0 = out[0]
        assert set(progress_0.keys()) == {"percent", "segment_count", "elapsed_sec"}
        assert progress_0["percent"] == 10.0 / 30.0
        assert progress_0["segment_count"] == 1
        assert seg_0 == {"start": 0.0, "end": 10.0, "text": "hello"}

        progress_1, seg_1 = out[1]
        assert progress_1["segment_count"] == 2
        assert progress_1["percent"] == 20.0 / 30.0
        assert seg_1["text"] == "world"

    def test_caps_percent_at_one(self):
        segs = [_FakeSeg(0.0, 60.0, "overshoot")]
        out = list(p.wrap_generator(segs, audio_duration_sec=30.0))
        progress, _ = out[0]
        assert progress["percent"] == 1.0

    def test_zero_duration_yields_zero_percent(self):
        segs = [_FakeSeg(0.0, 10.0, "x")]
        out = list(p.wrap_generator(segs, audio_duration_sec=0.0))
        progress, _ = out[0]
        assert progress["percent"] == 0.0

    def test_dict_segments_supported(self):
        segs = [{"start": 0.0, "end": 5.0, "text": "from dict"}]
        out = list(p.wrap_generator(segs, audio_duration_sec=10.0))
        _, seg = out[0]
        assert seg["text"] == "from dict"

    def test_strips_text_whitespace(self):
        segs = [_FakeSeg(0.0, 1.0, "   padded   ")]
        out = list(p.wrap_generator(segs, audio_duration_sec=1.0))
        _, seg = out[0]
        assert seg["text"] == "padded"

    def test_empty_iterator_yields_nothing(self):
        assert list(p.wrap_generator([], audio_duration_sec=10.0)) == []

    def test_segment_count_is_one_based(self):
        segs = [_FakeSeg(0.0, 1.0, "a"), _FakeSeg(1.0, 2.0, "b"), _FakeSeg(2.0, 3.0, "c")]
        out = list(p.wrap_generator(segs, audio_duration_sec=10.0))
        assert [o[0]["segment_count"] for o in out] == [1, 2, 3]
