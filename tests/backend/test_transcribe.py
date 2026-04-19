"""Integration tests for app/transcribe.py (locally-ad2).

Covers Acceptance Criteria:
  A1a — mlx_whisper subprocess stdout streams progress as lines arrive
         (not batched at end).
  A1b — subprocess argv includes `-u`, `-m mlx_whisper.cli`, and
         `--clip-timestamps <arg>`, with env `PYTHONUNBUFFERED=1` and
         Popen `bufsize=1`. Two variants:
           A1b-1: VAD returns intervals -> clip_arg = "s1,e1,..."
           A1b-2: VAD returns []        -> clip_arg = "0"
  A2   — faster-whisper (_run_ct2) emits monotonic non-decreasing percents
         on a Linux-forced branch, final percent == 1.0.
  A5   — `vad_filter=True` is passed to `model.transcribe(**kwargs)`.
  I1   — `app.audio_io.load_pcm_16k_mono` is invoked on mlx path.
  I2   — Implicit in A1a: if the subprocess were block-buffered, the
         cadence assertion below would fail. A positive A1a is sufficient
         per plan §3; no negative test is needed.

All tests use fakes/monkeypatches — no real mlx_whisper, no real ffmpeg,
no real faster_whisper. stdlib + pytest + numpy only.
"""
from __future__ import annotations

import re
import sys
import time
import types
from pathlib import Path

import numpy as np
import pytest

from app import transcribe


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Keep _MODEL singleton clean between tests on the CT2 path."""
    transcribe.reset_model_singleton_for_testing()
    yield
    transcribe.reset_model_singleton_for_testing()


def _make_audio(tmp_path: Path) -> Path:
    """Touch a placeholder audio file so Path(...).exists() returns True."""
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"\x00" * 16)
    return audio


class _FakeProc:
    """Minimal subprocess.Popen stand-in.

    stdout_lines: iterable of strings (each one logical stdout line).
    sleep_between: optional seconds to sleep before each yielded line
                   (used to simulate streaming cadence).
    """

    def __init__(self, stdout_lines, *, sleep_between: float = 0.0):
        self._stdout_lines = list(stdout_lines)
        self._sleep_between = sleep_between
        self.stderr = iter([])  # drained by background thread
        self.returncode = 0
        self.stdout = self._gen_stdout()

    def _gen_stdout(self):
        for line in self._stdout_lines:
            if self._sleep_between:
                time.sleep(self._sleep_between)
            yield line

    def wait(self, timeout=None):  # noqa: ARG002
        self.returncode = 0
        return 0

    def kill(self):
        self.returncode = -9


def _stub_audio_and_vad(monkeypatch, *, intervals, duration: float = 60.0):
    """Stub out app.audio_io and app.vad so _run_mlx does not touch ffmpeg.

    `load_pcm_16k_mono` returns a short float32 numpy array.
    `detect_speech_timestamps` returns the given intervals list.
    `_get_audio_duration` returns a fixed duration so percent math works.
    """
    from app import audio_io as _audio_io
    from app import vad as _vad

    spy = {"load_called_with": []}

    def _fake_load(path):
        spy["load_called_with"].append(path)
        return np.zeros(16000, dtype=np.float32)

    def _fake_detect(pcm, sample_rate):  # noqa: ARG001
        return list(intervals)

    monkeypatch.setattr(_audio_io, "load_pcm_16k_mono", _fake_load)
    monkeypatch.setattr(_vad, "detect_speech_timestamps", _fake_detect)
    monkeypatch.setattr(transcribe, "_get_audio_duration", lambda _p: duration)
    # Force Darwin branch regardless of host OS.
    monkeypatch.setattr(transcribe.platform, "system", lambda: "Darwin")
    return spy


# ---------------------------------------------------------------------------
# A1a — streaming cadence via fake Popen with sleeping stdout generator
# ---------------------------------------------------------------------------


class TestMlxStreamingCadence:
    def test_progress_cb_fires_as_lines_stream_not_at_end(
        self, monkeypatch, tmp_path
    ):
        """If the subprocess were block-buffered (no -u / PYTHONUNBUFFERED=1),
        stdout would burst at end and the inter-call gap assertion below would
        fail. A1a implicitly validates I2.
        """
        audio = _make_audio(tmp_path)
        _stub_audio_and_vad(monkeypatch, intervals=[], duration=60.0)

        # Each stdout line is a valid mlx_whisper segment line.
        lines = [
            "[00:00.000 --> 00:15.000] alpha\n",
            "[00:15.000 --> 00:30.000] bravo\n",
            "[00:30.000 --> 00:45.000] charlie\n",
            "[00:45.000 --> 01:00.000] delta\n",
        ]

        def fake_popen(cmd, **kwargs):  # noqa: ARG001
            return _FakeProc(lines, sleep_between=0.15)

        monkeypatch.setattr(transcribe.subprocess, "Popen", fake_popen)

        events: list[tuple[float, dict]] = []

        def cb(payload):
            events.append((time.monotonic(), payload))

        transcribe.run(str(audio), progress_cb=cb)

        assert len(events) >= 2, f"expected streaming events, got {len(events)}"
        gaps = [events[i + 1][0] - events[i][0] for i in range(len(events) - 1)]
        assert max(gaps) >= 0.1, (
            f"max inter-call gap {max(gaps):.3f}s < 0.1s "
            f"(events look batched, not streamed)"
        )
        last = events[-1][1]
        assert last["percent"] == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# A1b / I1 / I2 — argv + env spy on subprocess.Popen
# ---------------------------------------------------------------------------


class _PopenSpy:
    """Capture args/kwargs. Return a minimal immediately-exiting fake proc."""

    def __init__(self):
        self.calls: list[tuple[list, dict]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), dict(kwargs)))
        return _FakeProc([], sleep_between=0.0)


class TestMlxPopenArgvAndEnv:
    def _invoke(self, monkeypatch, tmp_path, *, intervals):
        audio = _make_audio(tmp_path)
        _stub_audio_and_vad(monkeypatch, intervals=intervals, duration=60.0)
        spy = _PopenSpy()
        monkeypatch.setattr(transcribe.subprocess, "Popen", spy)
        transcribe.run(str(audio), progress_cb=None)
        assert len(spy.calls) == 1
        return spy.calls[0]

    def test_argv_env_and_bufsize_on_vad_intervals(self, monkeypatch, tmp_path):
        """A1b-1: VAD returns intervals -> clip-timestamps is 's1,e1,...'."""
        cmd, kwargs = self._invoke(
            monkeypatch, tmp_path, intervals=[(1.0, 5.5), (12.25, 30.0)]
        )

        assert sys.executable in cmd[0]
        assert "-u" in cmd
        assert "-m" in cmd
        assert "mlx_whisper.cli" in cmd
        assert "--clip-timestamps" in cmd

        clip_idx = cmd.index("--clip-timestamps")
        clip_arg = cmd[clip_idx + 1]
        assert clip_arg, "clip-timestamps argument is empty"
        # Expect something like "1.000,5.500,12.250,30.000" — all floats,
        # comma-separated, non-empty.
        assert re.fullmatch(r"\d+\.\d+(?:,\d+\.\d+)+", clip_arg), (
            f"clip_arg {clip_arg!r} is not a valid pair-list string"
        )

        env = kwargs.get("env") or {}
        assert env.get("PYTHONUNBUFFERED") == "1"
        assert kwargs.get("bufsize") == 1

    def test_clip_timestamps_zero_when_vad_empty(self, monkeypatch, tmp_path):
        """A1b-2: VAD returns [] -> clip-timestamps is exactly '0'."""
        cmd, _kwargs = self._invoke(monkeypatch, tmp_path, intervals=[])

        clip_idx = cmd.index("--clip-timestamps")
        assert cmd[clip_idx + 1] == "0"


# ---------------------------------------------------------------------------
# I1 — audio_io.load_pcm_16k_mono is called with audio_path on mlx path
# ---------------------------------------------------------------------------


class TestMlxAudioIoCalled:
    def test_load_pcm_invoked_with_audio_path(self, monkeypatch, tmp_path):
        audio = _make_audio(tmp_path)
        spy = _stub_audio_and_vad(monkeypatch, intervals=[], duration=60.0)
        monkeypatch.setattr(
            transcribe.subprocess, "Popen", lambda *a, **k: _FakeProc([])
        )

        transcribe.run(str(audio), progress_cb=None)

        assert spy["load_called_with"] == [str(audio)]


# ---------------------------------------------------------------------------
# A2 + A5 — faster-whisper monotonic events, vad_filter=True kwarg
# ---------------------------------------------------------------------------


def _install_fake_faster_whisper_with_segments(monkeypatch, segments, duration):
    """Stub faster_whisper.WhisperModel with fake segments + info.duration.

    Records the kwargs passed to transcribe() in `captured`.
    """
    captured: dict = {"kwargs": None}

    class _FakeSeg:
        def __init__(self, start, end, text):
            self.start = start
            self.end = end
            self.text = text

    class _FakeModel:
        def __init__(self, *args, **kwargs):  # noqa: ARG002
            pass

        def transcribe(self, audio_path, **kwargs):  # noqa: ARG002
            captured["kwargs"] = dict(kwargs)
            segs = [_FakeSeg(s, e, t) for (s, e, t) in segments]
            return iter(segs), types.SimpleNamespace(duration=duration)

    fake_mod = types.ModuleType("faster_whisper")
    fake_mod.WhisperModel = _FakeModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_mod)
    return captured


class TestCt2ProgressAndVadFlag:
    def test_monotonic_percents_and_final_is_one(self, monkeypatch, tmp_path):
        """A2: ≥2 events, percents non-decreasing, final==1.0."""
        monkeypatch.setattr(transcribe.platform, "system", lambda: "Linux")
        duration = 60.0
        segments = [
            (0.0, 15.0, "one"),
            (15.0, 30.0, "two"),
            (30.0, 45.0, "three"),
            (45.0, 60.0, "four"),
        ]
        _install_fake_faster_whisper_with_segments(
            monkeypatch, segments, duration
        )

        audio = _make_audio(tmp_path)
        model_dir = tmp_path / "model"
        model_dir.mkdir()

        events: list[dict] = []
        transcribe.run(
            str(audio),
            model_dir=str(model_dir),
            progress_cb=events.append,
        )

        assert len(events) >= 2
        percents = [e["percent"] for e in events]
        for i in range(len(percents) - 1):
            assert percents[i] <= percents[i + 1], (
                f"non-monotonic at {i}: {percents}"
            )
        assert percents[-1] == pytest.approx(1.0, abs=1e-6)

    def test_vad_filter_true_passed_to_transcribe(self, monkeypatch, tmp_path):
        """A5: vad_filter=True must appear in model.transcribe(**kwargs)."""
        monkeypatch.setattr(transcribe.platform, "system", lambda: "Linux")
        captured = _install_fake_faster_whisper_with_segments(
            monkeypatch, segments=[(0.0, 10.0, "x")], duration=10.0
        )

        audio = _make_audio(tmp_path)
        model_dir = tmp_path / "model"
        model_dir.mkdir()

        transcribe.run(str(audio), model_dir=str(model_dir), progress_cb=None)

        assert captured["kwargs"] is not None
        assert captured["kwargs"].get("vad_filter") is True
