from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest


def _load_measure_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "measure_mlx_failed_ranges.py"
    )
    spec = importlib.util.spec_from_file_location(
        "measure_mlx_failed_ranges", script_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMeasureMlxFailedRanges:
    def test_run_measurement_marks_fail_when_any_call_errors(self, monkeypatch):
        measure = _load_measure_module()

        monkeypatch.setattr(measure, "_make_silent_wav", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            measure,
            "_time_one_call",
            lambda *args, **kwargs: (0.1, 1, False),
        )

        stats = measure.run_measurement(
            n_files=2,
            duration_sec=0.01,
            python_exe="python",
        )

        assert stats["errors"] == 2
        assert stats["within_30s"] is False

    def test_time_one_call_cleans_temp_output_dir(self, monkeypatch, tmp_path):
        measure = _load_measure_module()
        created_dir: Path | None = None

        class _Completed:
            returncode = 0

        def _fake_run(cmd, **kwargs):
            nonlocal created_dir
            output_dir = Path(cmd[cmd.index("--output-dir") + 1])
            created_dir = output_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.txt").write_text("ok", encoding="utf-8")
            return _Completed()

        monkeypatch.setattr(measure.subprocess, "run", _fake_run)

        wav_path = tmp_path / "sample.wav"
        wav_path.write_bytes(b"wav")

        elapsed, returncode, timed_out = measure._time_one_call(
            str(wav_path),
            "python",
            None,
            timeout_s=1.0,
        )

        assert elapsed >= 0
        assert returncode == 0
        assert timed_out is False
        assert created_dir is not None
        assert not created_dir.exists()

    def test_time_one_call_records_timeout_as_failure(self, monkeypatch, tmp_path):
        measure = _load_measure_module()

        def _fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="mlx", timeout=kwargs["timeout"])

        monkeypatch.setattr(measure.subprocess, "run", _fake_run)

        wav_path = tmp_path / "sample.wav"
        wav_path.write_bytes(b"wav")

        elapsed, returncode, timed_out = measure._time_one_call(
            str(wav_path),
            "python",
            None,
            timeout_s=1.0,
        )

        assert elapsed >= 0
        assert returncode == 124
        assert timed_out is True

    def test_validate_args_rejects_non_positive_values(self):
        measure = _load_measure_module()

        parser = measure.argparse.ArgumentParser()
        with pytest.raises(SystemExit):
            measure._validate_args(
                parser,
                n_files=0,
                duration_sec=0.5,
                timeout_s=60.0,
            )
        with pytest.raises(SystemExit):
            measure._validate_args(
                parser,
                n_files=1,
                duration_sec=0.0,
                timeout_s=60.0,
            )
        with pytest.raises(SystemExit):
            measure._validate_args(
                parser,
                n_files=1,
                duration_sec=0.5,
                timeout_s=0.0,
            )
