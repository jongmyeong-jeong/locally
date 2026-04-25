#!/usr/bin/env python3
"""Benchmark file-profile vs chunk-profile CER on a directory of audio files.

Usage:
    python scripts/benchmark_cer.py \\
        --audio-dir /path/to/audio \\
        --ref-dir /path/to/transcripts \\
        [--model-dir /path/to/model] \\
        [--output report.json]

For each audio file the script runs transcribe.run() with profile="file" and
profile="chunk", computes Character Error Rate (CER) against a reference .txt
in --ref-dir, and prints a per-file table plus aggregate summary.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".webm"}


# ---------------------------------------------------------------------------
# Levenshtein edit distance (pure Python, no new deps)
# ---------------------------------------------------------------------------

def _edit_distance(a: str, b: str) -> int:
    """Return the Levenshtein edit distance between strings a and b."""
    m, n = len(a), len(b)
    # Use two rows to keep memory O(n).
    prev = list(range(n + 1))
    curr = [0] * (n + 1)
    for i in range(1, m + 1):
        curr[0] = i
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1]
            else:
                curr[j] = 1 + min(prev[j - 1], prev[j], curr[j - 1])
        prev, curr = curr, [0] * (n + 1)
    return prev[n]


def _cer(hyp: str, ref: str) -> float:
    """Return Character Error Rate: edit_distance(hyp, ref) / len(ref)."""
    return _edit_distance(hyp, ref) / len(ref)


# ---------------------------------------------------------------------------
# Core benchmark logic
# ---------------------------------------------------------------------------

def _transcribe(audio_path: str, model_dir: Optional[str], profile: str) -> Optional[str]:
    """Run transcription; return text or None on error (error printed to stderr)."""
    from app import transcribe
    try:
        text, _ = transcribe.run(audio_path, model_dir=model_dir, profile=profile)  # type: ignore[arg-type]
        return text
    except Exception as exc:
        print(f"[ERROR] transcribe failed ({profile}) for {audio_path}: {exc}", file=sys.stderr)
        return None


def run_benchmark(
    audio_dir: str,
    ref_dir: str,
    model_dir: Optional[str] = None,
    output: Optional[str] = None,
) -> int:
    """Run the benchmark and return process exit code."""
    audio_path = Path(audio_dir)
    ref_path = Path(ref_dir)

    if not audio_path.exists():
        print(f"no audio files found in {audio_dir}", file=sys.stderr)
        return 2

    audio_files = sorted(
        p for p in audio_path.iterdir() if p.suffix.lower() in AUDIO_EXTENSIONS
    )
    if not audio_files:
        print(f"no audio files found in {audio_dir}", file=sys.stderr)
        return 2

    per_file: list[dict] = []

    for audio_file in audio_files:
        stem = audio_file.stem
        ref_file = ref_path / f"{stem}.txt"

        if not ref_file.exists():
            print(f"[WARN] reference not found, skipping: {ref_file}", file=sys.stderr)
            continue

        ref_text = ref_file.read_text(encoding="utf-8").strip()
        if not ref_text:
            print(f"[WARN] reference file is empty, skipping: {ref_file}", file=sys.stderr)
            continue

        text_file = _transcribe(str(audio_file), model_dir, "file")
        text_chunk = _transcribe(str(audio_file), model_dir, "chunk")

        cer_file: Optional[float] = None
        cer_chunk: Optional[float] = None
        error = False

        if text_file is None or text_chunk is None:
            error = True
        else:
            cer_file = _cer(text_file, ref_text)
            cer_chunk = _cer(text_chunk, ref_text)

        delta: Optional[float] = None
        if cer_file is not None and cer_chunk is not None:
            delta = cer_chunk - cer_file

        per_file.append({
            "filename": audio_file.name,
            "cer_file": cer_file,
            "cer_chunk": cer_chunk,
            "delta": delta,
            "error": error,
        })

    # --- print table ---
    col_w = max((len(r["filename"]) for r in per_file), default=20)
    header = f"{'filename':<{col_w}}  {'CER_file':>10}  {'CER_chunk':>10}  {'delta':>10}"
    print(header)
    print("-" * len(header))
    for row in per_file:
        cer_f = f"{row['cer_file']:.4f}" if row["cer_file"] is not None else "ERROR"
        cer_c = f"{row['cer_chunk']:.4f}" if row["cer_chunk"] is not None else "ERROR"
        dlt = f"{row['delta']:+.4f}" if row["delta"] is not None else "ERROR"
        print(f"{row['filename']:<{col_w}}  {cer_f:>10}  {cer_c:>10}  {dlt:>10}")

    # --- aggregate ---
    valid = [r for r in per_file if r["cer_file"] is not None and r["cer_chunk"] is not None]
    aggregate: dict = {}
    if valid:
        mean_cer_file = sum(r["cer_file"] for r in valid) / len(valid)  # type: ignore[operator]
        mean_cer_chunk = sum(r["cer_chunk"] for r in valid) / len(valid)  # type: ignore[operator]
        mean_delta = sum(r["delta"] for r in valid) / len(valid)  # type: ignore[operator]
        aggregate = {
            "mean_cer_file": mean_cer_file,
            "mean_cer_chunk": mean_cer_chunk,
            "mean_delta": mean_delta,
            "n": len(valid),
        }
        print()
        print(f"{'mean':<{col_w}}  {mean_cer_file:>10.4f}  {mean_cer_chunk:>10.4f}  {mean_delta:>+10.4f}  (n={len(valid)})")
    else:
        print("\nno valid results to aggregate")

    # --- optional JSON output ---
    if output:
        report = {"per_file": per_file, "aggregate": aggregate}
        Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nreport written to {output}")

    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark file-profile vs chunk-profile CER on audio files.",
    )
    parser.add_argument("--audio-dir", required=True, help="directory containing audio files")
    parser.add_argument("--ref-dir", required=True, help="directory containing reference .txt files")
    parser.add_argument("--model-dir", default=None, help="path to whisper model directory")
    parser.add_argument("--output", default=None, metavar="FILE", help="write JSON report to FILE")
    args = parser.parse_args()

    sys.exit(run_benchmark(
        audio_dir=args.audio_dir,
        ref_dir=args.ref_dir,
        model_dir=args.model_dir,
        output=args.output,
    ))


if __name__ == "__main__":
    main()
