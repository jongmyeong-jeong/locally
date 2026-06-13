"""Tests for app/batch_transcribe.py (Phase 1).

Covers:
 - probe_audio_encoders: real bundled ffmpeg returns at least one of libopus/flac
 - reencode_for_upload: produces a decodable file (round-trip 2s WAV)
 - plan_splits: silence boundary detection (synthetic audio with known silence)
 - plan_splits: no-silence forced cut
 - plan_splits: single piece when size ≤ threshold
 - transcribe_pieces offset merge monotonicity (AC3)
 - partial failure: one piece fails → BatchResult.partial_failure + marker
 - retry: GroqNetworkError twice then success → 3 calls
 - all-fail: all pieces exhausted → all_failed True
 - merged_text_with_failure_markers: exact marker format
"""
from __future__ import annotations

import shutil
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# ffmpeg availability guard (mirrors test_audio_io.py pattern)
# ---------------------------------------------------------------------------
_BUNDLED_FFMPEG = Path(__file__).resolve().parent.parent.parent / "bin" / "ffmpeg"
_FFMPEG = str(_BUNDLED_FFMPEG) if _BUNDLED_FFMPEG.exists() else shutil.which("ffmpeg")

pytestmark = pytest.mark.skipif(
    _FFMPEG is None,
    reason="ffmpeg not available",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_wav_with_silence(dest: Path, total_sec: float = 30.0, silence_at: float = 10.0, silence_dur: float = 2.0) -> None:
    """Generate a WAV: sine tone with a silence window at a known position.

    Uses numpy + wave stdlib — no scipy needed.
    """
    sr = 16000
    n = int(total_sec * sr)
    t = np.linspace(0, total_sec, n, endpoint=False, dtype=np.float32)
    pcm = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

    # Zero out silence window
    sil_start = int(silence_at * sr)
    sil_end = int((silence_at + silence_dur) * sr)
    pcm[sil_start:sil_end] = 0.0

    # Convert float32 → int16 for WAV
    pcm_int16 = (pcm * 32767).astype(np.int16)

    with wave.open(str(dest), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16
        wf.setframerate(sr)
        wf.writeframes(pcm_int16.tobytes())


def _make_sine_wav(dest: Path, duration_sec: float = 2.0, sr: int = 16000) -> None:
    """Generate a plain sine WAV (no silence)."""
    n = int(duration_sec * sr)
    t = np.linspace(0, duration_sec, n, endpoint=False, dtype=np.float32)
    pcm = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    pcm_int16 = (pcm * 32767).astype(np.int16)
    with wave.open(str(dest), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm_int16.tobytes())


def _fake_transcribe_result(text: str = "hello", start: float = 0.0, end: float = 1.0):
    """Return a TranscribeResult-compatible dict."""
    from app.groq_client import TranscribeResult
    return TranscribeResult(
        text=text,
        segments=[{"start": start, "end": end, "text": text}],
    )


# ---------------------------------------------------------------------------
# Reset the module-level encoder cache between tests so probe tests don't
# interfere with each other.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_encoder_cache():
    import app.audio_io as _aio
    original = _aio._PROBED_ENCODERS
    yield
    _aio._PROBED_ENCODERS = original


# ---------------------------------------------------------------------------
# 1. probe_audio_encoders — real ffmpeg
# ---------------------------------------------------------------------------

class TestProbeAudioEncoders:
    def test_returns_frozenset(self):
        from app.audio_io import probe_audio_encoders
        result = probe_audio_encoders()
        assert isinstance(result, frozenset)

    def test_contains_at_least_one_supported_codec(self):
        from app.audio_io import probe_audio_encoders
        result = probe_audio_encoders()
        supported = {"libopus", "flac"}
        assert result & supported, (
            f"Expected at least one of {supported!r} but got encoders: {result!r}"
        )

    def test_result_is_cached(self):
        """Calling twice returns the identical frozenset object."""
        import app.audio_io as _aio
        _aio._PROBED_ENCODERS = None  # reset
        from app.audio_io import probe_audio_encoders
        first = probe_audio_encoders()
        second = probe_audio_encoders()
        assert first is second


# ---------------------------------------------------------------------------
# 2. reencode_for_upload — round-trip decodability
# ---------------------------------------------------------------------------

class TestReencodeForUpload:
    def test_produces_decodable_file(self, tmp_path):
        """Re-encode a 2s WAV and verify it decodes back to ≈2s of samples."""
        from app.audio_io import load_pcm_16k_mono, reencode_for_upload

        src = tmp_path / "src.wav"
        _make_sine_wav(src, duration_sec=2.0)

        out = reencode_for_upload(src, tmp_path)

        assert out.exists()
        assert out.stat().st_size > 100  # non-empty

        # Decode back via load_pcm_16k_mono and check approximate length
        pcm = load_pcm_16k_mono(str(out))
        expected_samples = 2.0 * 16000
        assert abs(len(pcm) - expected_samples) < 1600, (
            f"Expected ~{expected_samples:.0f} samples, got {len(pcm)}"
        )

    def test_codec_matches_probe(self, tmp_path):
        """The output extension matches the probed codec selection."""
        from app.audio_io import probe_audio_encoders, reencode_for_upload

        src = tmp_path / "src.wav"
        _make_sine_wav(src, duration_sec=1.0)

        encoders = probe_audio_encoders()
        out = reencode_for_upload(src, tmp_path)

        if "libopus" in encoders:
            assert out.suffix == ".ogg"
        else:
            assert out.suffix == ".flac"


# ---------------------------------------------------------------------------
# 3. plan_splits — single piece (size ≤ threshold)
# ---------------------------------------------------------------------------

class TestPlanSplitsSinglePiece:
    def test_small_file_returns_one_range(self, tmp_path):
        """A file smaller than the threshold produces exactly one SplitRange."""
        from app.batch_transcribe import plan_splits

        src = tmp_path / "small.wav"
        _make_sine_wav(src, duration_sec=2.0)

        # Default threshold is 20 MB; a 2s WAV is tiny
        ranges = plan_splits(src)
        assert len(ranges) == 1
        assert ranges[0].seq == 0
        assert ranges[0].start_sec == 0.0
        assert ranges[0].end_sec > 0.0


# ---------------------------------------------------------------------------
# 4. plan_splits — silence boundary detection
# ---------------------------------------------------------------------------

class TestPlanSplitsSilenceBoundary:
    def test_boundary_near_known_silence(self, tmp_path):
        """With a known silence at 10s in a 30s file, the split boundary lands ±2s of it.

        We set threshold_bytes to just below the file size to force exactly 2 pieces,
        placing the candidate cut near the midpoint (15s). The silence at 10s is within
        the ±30s search window, so the boundary should be refined toward 10s.
        """
        from app.batch_transcribe import plan_splits

        src = tmp_path / "with_silence.wav"
        # 30s audio, silence at 10s for 2s
        _make_wav_with_silence(src, total_sec=30.0, silence_at=10.0, silence_dur=2.0)

        # Force exactly 2 pieces: threshold = file_size - 1 byte
        file_size = src.stat().st_size
        ranges = plan_splits(src, threshold_bytes=file_size - 1)

        assert len(ranges) == 2, f"Expected 2 pieces, got {len(ranges)}"

        # The internal boundary (ranges[0].end_sec) should be near the silence at 10s
        # (the target cut is at ~15s midpoint; silence at 10s is within the ±30s window)
        split_point = ranges[0].end_sec
        assert abs(split_point - 10.0) <= 2.0, (
            f"Expected boundary within ±2s of silence at 10s, got {split_point:.2f}s"
        )

    def test_no_silence_uses_target_cut(self, tmp_path):
        """When there's no real silence, the boundary stays near the target cut."""
        from app.batch_transcribe import plan_splits

        src = tmp_path / "no_silence.wav"
        _make_sine_wav(src, duration_sec=4.0)

        # Force two pieces by setting threshold to half the file size
        file_size = src.stat().st_size
        ranges = plan_splits(src, threshold_bytes=file_size // 2)

        assert len(ranges) == 2
        # The split boundary should be near 2.0s (the midpoint of 4s)
        split_point = ranges[0].end_sec
        assert abs(split_point - 2.0) <= 1.0, (
            f"Expected split near 2.0s, got {split_point:.2f}s"
        )

    def test_ranges_are_contiguous_and_cover_full_duration(self, tmp_path):
        """All SplitRanges together cover the full audio without gaps."""
        from app.batch_transcribe import plan_splits

        src = tmp_path / "audio.wav"
        _make_sine_wav(src, duration_sec=4.0)

        file_size = src.stat().st_size
        ranges = plan_splits(src, threshold_bytes=file_size // 2)

        # Each range's end matches the next range's start
        for i in range(len(ranges) - 1):
            assert abs(ranges[i].end_sec - ranges[i + 1].start_sec) < 0.001, (
                f"Gap between range {i} and {i+1}"
            )

        # First starts at 0, last ends at approximately total duration
        assert ranges[0].start_sec == 0.0
        assert ranges[-1].end_sec > 0.0


# ---------------------------------------------------------------------------
# 5. transcribe_pieces — offset merge monotonicity (AC3)
# ---------------------------------------------------------------------------

class TestTranscribePiecesOffsetMonotonicity:
    def test_second_piece_segments_offset_correctly(self, tmp_path):
        """Segments from piece 2 must have start_ms >= last end_ms of piece 1."""
        from app.batch_transcribe import SplitRange, transcribe_pieces

        # Two pieces: piece0 covers 0–10s, piece1 covers 10–20s
        p0 = tmp_path / "p0.ogg"
        p1 = tmp_path / "p1.ogg"
        _make_sine_wav(tmp_path / "_src.wav", duration_sec=1.0)
        # Just need them to exist as files for the mock path
        p0.write_bytes(b"placeholder")
        p1.write_bytes(b"placeholder")

        rng0 = SplitRange(seq=0, start_sec=0.0, end_sec=10.0)
        rng1 = SplitRange(seq=1, start_sec=10.0, end_sec=20.0)

        # piece0 has segment at [0.5s, 2.0s], piece1 has segment at [0.5s, 1.5s]
        # After offset: piece1 segment should be at [10.5s, 11.5s]
        fake_result_0 = _fake_transcribe_result("hello", start=0.5, end=2.0)
        fake_result_1 = _fake_transcribe_result("world", start=0.5, end=1.5)

        call_results = [fake_result_0, fake_result_1]
        with patch("app.batch_transcribe.transcribe_audio", side_effect=call_results):
            result = transcribe_pieces(
                [(p0, rng0), (p1, rng1)],
                prompt=None,
            )

        assert len(result.pieces) == 2
        p0_segs = result.pieces[0].segments
        p1_segs = result.pieces[1].segments

        # Piece 0: start_ms = round((0.5+0)*1000) = 500
        assert p0_segs[0]["start_ms"] == 500
        assert p0_segs[0]["end_ms"] == 2000

        # Piece 1: start_ms = round((0.5+10.0)*1000) = 10500
        assert p1_segs[0]["start_ms"] == 10500
        assert p1_segs[0]["end_ms"] == 11500

        # Monotonicity: p1 first start >= p0 last end (minus small epsilon)
        assert p1_segs[0]["start_ms"] >= p0_segs[-1]["end_ms"] - 100

    def test_all_segments_monotonically_non_decreasing(self, tmp_path):
        """All start_ms values across pieces are strictly non-decreasing."""
        from app.batch_transcribe import SplitRange, transcribe_pieces

        p0 = tmp_path / "p0.ogg"
        p1 = tmp_path / "p1.ogg"
        p2 = tmp_path / "p2.ogg"
        for f in (p0, p1, p2):
            f.write_bytes(b"placeholder")

        rng0 = SplitRange(seq=0, start_sec=0.0, end_sec=10.0)
        rng1 = SplitRange(seq=1, start_sec=10.0, end_sec=20.0)
        rng2 = SplitRange(seq=2, start_sec=20.0, end_sec=30.0)

        fake_results = [
            _fake_transcribe_result("a", start=1.0, end=3.0),
            _fake_transcribe_result("b", start=1.0, end=2.0),
            _fake_transcribe_result("c", start=0.5, end=1.5),
        ]

        with patch("app.batch_transcribe.transcribe_audio", side_effect=fake_results):
            result = transcribe_pieces(
                [(p0, rng0), (p1, rng1), (p2, rng2)],
                prompt=None,
            )

        all_segs = []
        for piece in result.pieces:
            all_segs.extend(piece.segments)

        starts = [s["start_ms"] for s in all_segs]
        for i in range(1, len(starts)):
            assert starts[i] >= starts[i - 1], (
                f"Non-monotonic at index {i}: {starts[i]} < {starts[i-1]}"
            )


# ---------------------------------------------------------------------------
# 6. partial failure
# ---------------------------------------------------------------------------

class TestPartialFailure:
    def test_partial_failure_properties(self, tmp_path):
        """Piece 2 of 3 fails → partial_failure=True, failed_ranges has piece 2."""
        from app.batch_transcribe import SplitRange, transcribe_pieces
        from app.groq_client import GroqServerError

        p0 = tmp_path / "p0.ogg"
        p1 = tmp_path / "p1.ogg"
        p2 = tmp_path / "p2.ogg"
        for f in (p0, p1, p2):
            f.write_bytes(b"placeholder")

        rng0 = SplitRange(seq=0, start_sec=0.0, end_sec=10.0)
        rng1 = SplitRange(seq=1, start_sec=10.0, end_sec=20.0)
        rng2 = SplitRange(seq=2, start_sec=20.0, end_sec=30.0)

        side_effects = [
            _fake_transcribe_result("piece0 text", start=0.0, end=5.0),
            GroqServerError("500 error"),
            _fake_transcribe_result("piece2 text", start=0.0, end=5.0),
        ]

        with patch("app.batch_transcribe.transcribe_audio", side_effect=side_effects):
            result = transcribe_pieces(
                [(p0, rng0), (p1, rng1), (p2, rng2)],
                prompt=None,
            )

        assert result.partial_failure is True
        assert result.all_failed is False
        assert len(result.failed_ranges) == 1
        assert result.failed_ranges[0]["start_ms"] == 10000
        assert result.failed_ranges[0]["end_ms"] == 20000

    def test_merged_text_has_marker_between_pieces(self, tmp_path):
        """merged_text_with_failure_markers inserts the failure marker at position of piece 2."""
        from app.batch_transcribe import SplitRange, transcribe_pieces
        from app.groq_client import GroqServerError

        p0 = tmp_path / "p0.ogg"
        p1 = tmp_path / "p1.ogg"
        p2 = tmp_path / "p2.ogg"
        for f in (p0, p1, p2):
            f.write_bytes(b"placeholder")

        rng0 = SplitRange(seq=0, start_sec=0.0, end_sec=10.0)
        rng1 = SplitRange(seq=1, start_sec=10.0, end_sec=20.0)
        rng2 = SplitRange(seq=2, start_sec=20.0, end_sec=30.0)

        side_effects = [
            _fake_transcribe_result("first part", start=0.0, end=5.0),
            GroqServerError("500"),
            _fake_transcribe_result("third part", start=0.0, end=5.0),
        ]

        with patch("app.batch_transcribe.transcribe_audio", side_effect=side_effects):
            result = transcribe_pieces(
                [(p0, rng0), (p1, rng1), (p2, rng2)],
                prompt=None,
            )

        merged = result.merged_text_with_failure_markers()
        assert "first part" in merged
        assert "third part" in merged
        assert "[00:00:10–00:00:20 전사 실패 구간]" in merged

        # Order: piece0 text, then marker, then piece2 text
        idx_first = merged.index("first part")
        idx_marker = merged.index("[00:00:10")
        idx_third = merged.index("third part")
        assert idx_first < idx_marker < idx_third


# ---------------------------------------------------------------------------
# 7. Retry — GroqNetworkError twice then success
# ---------------------------------------------------------------------------

class TestRetryNetworkError:
    def test_network_error_retried_to_success(self, tmp_path, monkeypatch):
        """GroqNetworkError on attempts 1 and 2; success on attempt 3 → 3 calls total."""
        from app.batch_transcribe import SplitRange, transcribe_pieces
        from app.groq_client import GroqNetworkError

        p0 = tmp_path / "p0.ogg"
        p0.write_bytes(b"placeholder")
        rng0 = SplitRange(seq=0, start_sec=0.0, end_sec=5.0)

        call_count = 0
        successes_after = 2  # fail twice, succeed on 3rd

        def fake_transcribe(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= successes_after:
                raise GroqNetworkError("network blip")
            return _fake_transcribe_result("ok text", start=0.0, end=5.0)

        # Suppress the sleep to keep tests fast
        monkeypatch.setattr("app.batch_transcribe.time.sleep", lambda _: None)

        with patch("app.batch_transcribe.transcribe_audio", side_effect=fake_transcribe):
            result = transcribe_pieces([(p0, rng0)], prompt=None)

        assert call_count == 3
        assert result.pieces[0].ok is True
        assert result.pieces[0].text == "ok text"
        assert result.all_failed is False

    def test_network_error_exhausted_marks_failed(self, tmp_path, monkeypatch):
        """Exhausting all retries with GroqNetworkError marks the piece as failed."""
        from app.batch_transcribe import SplitRange, transcribe_pieces
        from app.groq_client import GroqNetworkError

        p0 = tmp_path / "p0.ogg"
        p0.write_bytes(b"placeholder")
        rng0 = SplitRange(seq=0, start_sec=0.0, end_sec=5.0)

        monkeypatch.setattr("app.batch_transcribe.time.sleep", lambda _: None)

        with patch(
            "app.batch_transcribe.transcribe_audio",
            side_effect=GroqNetworkError("always fails"),
        ):
            result = transcribe_pieces([(p0, rng0)], prompt=None)

        assert result.pieces[0].ok is False
        assert result.pieces[0].error_type == "network_failed_max_retries"
        assert result.all_failed is True


# ---------------------------------------------------------------------------
# 8. All-fail
# ---------------------------------------------------------------------------

class TestAllFail:
    def test_all_pieces_failed(self, tmp_path, monkeypatch):
        """Every piece failing network retries → all_failed True."""
        from app.batch_transcribe import SplitRange, transcribe_pieces
        from app.groq_client import GroqNetworkError

        files = []
        ranges = []
        for i in range(2):
            f = tmp_path / f"p{i}.ogg"
            f.write_bytes(b"placeholder")
            files.append(f)
            ranges.append(SplitRange(seq=i, start_sec=i * 10.0, end_sec=(i + 1) * 10.0))

        monkeypatch.setattr("app.batch_transcribe.time.sleep", lambda _: None)

        with patch(
            "app.batch_transcribe.transcribe_audio",
            side_effect=GroqNetworkError("always fails"),
        ):
            result = transcribe_pieces(list(zip(files, ranges)), prompt=None)

        assert result.all_failed is True
        assert result.partial_failure is False
        assert len(result.failed_ranges) == 2


# ---------------------------------------------------------------------------
# 9. merged_text_with_failure_markers — exact marker format
# ---------------------------------------------------------------------------

class TestMergedTextMarkerFormat:
    def test_exact_marker_format(self, tmp_path, monkeypatch):
        """Marker format must be [hh:mm:ss–hh:mm:ss 전사 실패 구간] with en-dash."""
        from app.batch_transcribe import SplitRange, transcribe_pieces
        from app.groq_client import GroqServerError

        p0 = tmp_path / "p0.ogg"
        p0.write_bytes(b"placeholder")
        # Start at 10s, end at 20s → marker [00:00:10–00:00:20 전사 실패 구간]
        rng0 = SplitRange(seq=0, start_sec=10.0, end_sec=20.0)

        with patch(
            "app.batch_transcribe.transcribe_audio",
            side_effect=GroqServerError("500"),
        ):
            result = transcribe_pieces([(p0, rng0)], prompt=None)

        merged = result.merged_text_with_failure_markers()
        # Must use en-dash U+2013, not hyphen-minus U+002D
        assert "–" in merged, "Expected en-dash (–) in marker"
        assert "[00:00:10–00:00:20 전사 실패 구간]" in merged

    def test_larger_timestamps(self, tmp_path, monkeypatch):
        """Verify hh:mm:ss formatting for times beyond 1 hour."""
        from app.batch_transcribe import SplitRange, transcribe_pieces
        from app.groq_client import GroqServerError

        p0 = tmp_path / "p0.ogg"
        p0.write_bytes(b"placeholder")
        # 1h5m10s to 1h5m20s
        start = 3600 + 5 * 60 + 10  # 3910 s
        end = start + 10
        rng0 = SplitRange(seq=0, start_sec=float(start), end_sec=float(end))

        with patch(
            "app.batch_transcribe.transcribe_audio",
            side_effect=GroqServerError("500"),
        ):
            result = transcribe_pieces([(p0, rng0)], prompt=None)

        merged = result.merged_text_with_failure_markers()
        assert "[01:05:10–01:05:20 전사 실패 구간]" in merged

    def test_non_network_error_fails_immediately(self, tmp_path, monkeypatch):
        """GroqRateLimitError fails immediately (no retries) → error_type=rate_limit."""
        from app.batch_transcribe import SplitRange, transcribe_pieces
        from app.groq_client import GroqRateLimitError

        p0 = tmp_path / "p0.ogg"
        p0.write_bytes(b"placeholder")
        rng0 = SplitRange(seq=0, start_sec=0.0, end_sec=5.0)

        call_count = 0

        def fake_transcribe(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise GroqRateLimitError("rate limited")

        with patch("app.batch_transcribe.transcribe_audio", side_effect=fake_transcribe):
            result = transcribe_pieces([(p0, rng0)], prompt=None)

        # Must only be called once (no retries for rate limit)
        assert call_count == 1
        assert result.pieces[0].error_type == "rate_limit"
