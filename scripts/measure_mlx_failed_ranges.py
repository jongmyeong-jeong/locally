#!/usr/bin/env python3
"""Measure MLX subprocess startup + transcription latency for 10 synthetic WAV files.

Hypothesis: per-range independent mlx_whisper subprocess calls for 10 failed
ranges complete within 30 seconds total (avg < 3 s/call, max < 10 s/call).

Usage:
    .venv/bin/python scripts/measure_mlx_failed_ranges.py [--model-dir PATH]

Output:
    Prints per-call timing table and summary.
    Writes .omc/research/mlx-subprocess-delay.md with findings.
"""
from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Synthetic WAV generation (pure Python, no extra deps)
# ---------------------------------------------------------------------------

def _make_silent_wav(path: Path, duration_sec: float = 0.5, sample_rate: int = 16000) -> None:
    """Write a minimal PCM WAV file containing silence."""
    num_samples = int(sample_rate * duration_sec)
    num_channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = num_samples * block_align
    # WAV header: RIFF chunk (44 bytes) + PCM silence
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,              # subchunk1 size
        1,               # PCM format
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    with open(path, "wb") as f:
        f.write(header)
        f.write(b"\x00" * data_size)


# ---------------------------------------------------------------------------
# Core measurement logic
# ---------------------------------------------------------------------------

def _resolve_python_exe(
    project_root: Path | None = None,
    current_exe: str | None = None,
) -> str:
    """Prefer the project venv interpreter when present."""
    root = project_root or Path(__file__).resolve().parent.parent
    active = Path(current_exe or sys.executable)
    venv_python = root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return str(active)


def _validate_args(
    parser: argparse.ArgumentParser,
    *,
    n_files: int,
    duration_sec: float,
    timeout_s: float,
) -> None:
    if n_files < 1:
        parser.error("--n-files must be >= 1")
    if duration_sec <= 0:
        parser.error("--duration must be > 0")
    if timeout_s <= 0:
        parser.error("--timeout must be > 0")


def _time_one_call(
    wav_path: str,
    python_exe: str,
    model_dir: str | None,
    *,
    timeout_s: float,
) -> tuple[float, int, bool]:
    """Invoke mlx_whisper on one WAV; return (elapsed_sec, returncode, timed_out)."""
    timed_out = False
    with tempfile.TemporaryDirectory(prefix="mlx_measure_") as output_dir:
        cmd = [
            python_exe,
            "-u",
            "-m",
            "mlx_whisper.cli",
            wav_path,
            "--output-format", "txt",
            "--output-dir", output_dir,
            "--condition-on-previous-text", "False",
            "--no-speech-threshold", "0.85",
            "--logprob-threshold", "-0.8",
            "--compression-ratio-threshold", "2.0",
            "--temperature", "0",
            "--clip-timestamps", "0",
        ]
        if model_dir:
            cmd += ["--model", model_dir]

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                timeout=timeout_s,
                check=False,
            )
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            returncode = 124
        elapsed = time.monotonic() - t0
        return elapsed, returncode, timed_out


def run_measurement(
    n_files: int = 10,
    duration_sec: float = 0.5,
    model_dir: str | None = None,
    python_exe: str | None = None,
    timeout_s: float = 60.0,
) -> dict:
    """Run n_files sequential subprocess calls; return timing stats."""
    if python_exe is None:
        python_exe = _resolve_python_exe()

    results: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="mlx_wavs_") as tmp:
        # Pre-generate all WAV files before timing.
        wav_paths: list[str] = []
        for i in range(n_files):
            p = Path(tmp) / f"synthetic_{i:02d}.wav"
            _make_silent_wav(p, duration_sec=duration_sec)
            wav_paths.append(str(p))

        print(f"Measuring {n_files} sequential mlx_whisper subprocess calls "
              f"(each: {duration_sec}s silent WAV)")
        print(f"Python: {python_exe}")
        print(f"Model:  {model_dir or '(default)'}")
        print(f"Timeout:{timeout_s}s / call")
        print()

        col_w = 20
        print(
            f"{'call':<6}  {'wav':<{col_w}}  {'elapsed_s':>10}  "
            f"{'returncode':>10}  {'timed_out':>10}"
        )
        print("-" * (6 + 2 + col_w + 2 + 10 + 2 + 10 + 2 + 10))

        wall_start = time.monotonic()
        for i, wav_path in enumerate(wav_paths):
            elapsed, rc, timed_out = _time_one_call(
                wav_path,
                python_exe,
                model_dir,
                timeout_s=timeout_s,
            )
            results.append(
                {
                    "index": i,
                    "wav": Path(wav_path).name,
                    "elapsed_s": elapsed,
                    "returncode": rc,
                    "timed_out": timed_out,
                }
            )
            print(
                f"{i:<6}  {Path(wav_path).name:<{col_w}}  {elapsed:>10.3f}  "
                f"{rc:>10}  {str(timed_out):>10}"
            )
        wall_total = time.monotonic() - wall_start

    times = [r["elapsed_s"] for r in results]
    mean_s = sum(times) / len(times)
    max_s = max(times)
    min_s = min(times)
    errors = sum(1 for r in results if r["returncode"] != 0)

    print()
    print(f"{'total_wall_s':<20}: {wall_total:.3f}")
    print(f"{'mean_s':<20}: {mean_s:.3f}")
    print(f"{'max_s':<20}: {max_s:.3f}")
    print(f"{'min_s':<20}: {min_s:.3f}")
    print(f"{'errors':<20}: {errors}")
    within_30s = wall_total <= 30.0 and errors == 0
    conclusion = "PASS (<=30 s, rc=0)" if within_30s else "FAIL (>30 s or errors)"
    print(f"{'conclusion':<20}: {conclusion}")

    return {
        "n_files": n_files,
        "duration_sec": duration_sec,
        "python_exe": python_exe,
        "model_dir": model_dir,
        "total_wall_s": wall_total,
        "mean_s": mean_s,
        "max_s": max_s,
        "min_s": min_s,
        "errors": errors,
        "within_30s": within_30s,
        "per_call": results,
    }


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def write_report(stats: dict, report_path: Path) -> None:
    """Write .omc/research/mlx-subprocess-delay.md with measurement results."""
    conclusion = "30초 이내 완료 (가설 성립)" if stats["within_30s"] else "30초 초과 (가설 기각 — 후속 최적화 필요)"
    lines = [
        "# MLX Subprocess Delay Measurement",
        "",
        "## 개요",
        "",
        "실패한 청크 범위 10개에 대해 `mlx_whisper` subprocess를 독립적으로 순차 호출할 때",
        "총 소요 시간이 30초 이내인지 검증.",
        "",
        "## 측정 환경",
        "",
        f"- Python: `{stats['python_exe']}`",
        f"- Model: `{stats['model_dir'] or '(default / mlx-community/whisper-large-v3-mlx)'}`",
        f"- 합성 WAV: 무음 {stats['duration_sec']}초 × {stats['n_files']}개",
        "- 측정 방법: 순차 subprocess.run, 각 호출의 wall-clock time 기록",
        "",
        "## 결과",
        "",
        "| 항목 | 값 |",
        "|------|-----|",
        f"| 총 소요 (wall) | **{stats['total_wall_s']:.3f}초** |",
        f"| 평균 지연 | {stats['mean_s']:.3f}초 |",
        f"| 최대 지연 | {stats['max_s']:.3f}초 |",
        f"| 최소 지연 | {stats['min_s']:.3f}초 |",
        f"| 오류 횟수 | {stats['errors']} |",
        f"| 30초 기준 | {'통과' if stats['within_30s'] else '초과'} |",
        "",
        "### 콜별 상세",
        "",
        "| call | wav | elapsed_s | rc | timed_out |",
        "|------|-----|-----------|----|-----------|",
    ]
    for r in stats["per_call"]:
        lines.append(
            f"| {r['index']} | {r['wav']} | {r['elapsed_s']:.3f} | "
            f"{r['returncode']} | {r['timed_out']} |"
        )

    lines += [
        "",
        "## 결론",
        "",
        f"**{conclusion}**",
        "",
    ]

    if stats["within_30s"]:
        lines += [
            "10개 실패 범위에 대한 독립 subprocess 재시도 전략은 허용 가능한 지연 범위 내에 있음.",
            "별도 최적화(프로세스 풀, 배치 병합 등) 없이 현행 구조를 유지해도 무방.",
        ]
    else:
        lines += [
            "총 지연이 30초를 초과함. 다음 후속 작업 필요:",
            "",
            "- [ ] subprocess 재사용(프로세스 풀) 도입 검토",
            "- [ ] 실패 범위 배치 병합 후 단일 subprocess 호출로 전환",
            "- [ ] 후속 이슈 생성 및 우선순위 배정",
        ]

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nReport written to {report_path}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure MLX subprocess startup latency for 10 synthetic WAV files.",
    )
    parser.add_argument(
        "--model-dir",
        default=None,
        help="Path to mlx_whisper model directory (default: mlx_whisper default model)",
    )
    parser.add_argument(
        "--n-files",
        type=int,
        default=10,
        help="Number of synthetic WAV files to measure (default: 10)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.5,
        help="Duration of each synthetic WAV in seconds (default: 0.5)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Per-call timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--report",
        default=None,
        metavar="FILE",
        help="Override report output path (default: .omc/research/mlx-subprocess-delay.md)",
    )
    args = parser.parse_args()
    _validate_args(
        parser,
        n_files=args.n_files,
        duration_sec=args.duration,
        timeout_s=args.timeout,
    )

    python_exe = _resolve_python_exe()

    stats = run_measurement(
        n_files=args.n_files,
        duration_sec=args.duration,
        model_dir=args.model_dir,
        python_exe=python_exe,
        timeout_s=args.timeout,
    )

    # Determine report path relative to project root (two levels up from scripts/).
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    if args.report:
        report_path = Path(args.report)
    else:
        report_path = project_root / ".omc" / "research" / "mlx-subprocess-delay.md"

    write_report(stats, report_path)
    sys.exit(0 if stats["within_30s"] else 1)


if __name__ == "__main__":
    main()
