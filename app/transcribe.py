"""OS-branched transcription dispatch with a lazy model singleton (N6).

Darwin  → mlx_whisper (stderr parsed via transcribe_parser_mlx).
Windows/Linux → faster_whisper (generator wrapped via transcribe_parser_ct2).

Both paths normalize progress payloads to TranscribeProgress (A3).
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Literal, Optional

from app.transcribe_parser_ct2 import wrap_generator as _wrap_ct2
from app.transcribe_parser_mlx import parse_segment_line

_MODEL = None
_MODEL_LOCK = threading.Lock()

# Decoder rejection thresholds — DO NOT tighten without a multi-file
# evaluation on Korean speech. These are deliberately at Whisper defaults.
#
# The trap: when transcripts contain noise/hallucinations, the obvious move is
# to tighten these (raise no_speech, raise logprob toward 0, lower compression
# ratio). It silences some noise but its bigger cost is dropping quiet or
# short legitimate utterances — extremely common in Korean conversational
# speech (single-syllable backchannels, trailing endings, brief affirmations).
# This trade has been measured and was not worth it.
#
# The remaining failure mode at defaults is greedy repetition loops. Those
# are already handled by (a) temperature fallback inside the decoder
# (compression-ratio check trips → retry with rising randomness) and (b)
# `hallucination_silence_threshold` below. Tightening these constants does
# NOT improve loop suppression; it only removes real speech.
#
# If you must change a value here, validate against ≥3 Korean recordings of
# different content types (noisy live, clean monologue, multi-speaker
# discussion) and check both hallucination count AND total speech duration
# captured — not just hallucination count.
_LOGPROB_THRESHOLD = -1.0
_COMPRESSION_RATIO_THRESHOLD = 2.4
_HALLUCINATION_SILENCE_THRESHOLD = 2.0  # CT2 only — MLX CLI does not accept this flag

THRESHOLD_PROFILE: dict[str, dict[str, float]] = {
    "file": {"no_speech_threshold": 0.6, "hallucination_silence_threshold": 2.0},
    "chunk": {"no_speech_threshold": 0.6, "hallucination_silence_threshold": 2.0},
}


class TranscriptionError(Exception):
    """Raised when transcription fails for any reason."""


def _get_ct2_model(model_dir: str):
    """Lazily instantiate a faster-whisper WhisperModel once per process."""
    global _MODEL
    with _MODEL_LOCK:
        if _MODEL is None:
            from faster_whisper import WhisperModel  # type: ignore

            _MODEL = WhisperModel(
                model_dir,
                device="cpu",
                compute_type="int8",
            )
        return _MODEL


def _get_audio_duration(audio_path: str) -> float | None:
    """Return audio duration in seconds via ffprobe, or None if unavailable."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", audio_path],
            capture_output=True, text=True, timeout=10,
        )
        info = json.loads(result.stdout)
        return float(info["format"]["duration"])
    except Exception:
        return None


def _run_mlx(
    audio_path: str,
    *,
    model_dir: str | None,
    prompt: str | None,
    progress_cb: Optional[Callable[[dict], None]],
    profile: Literal["file", "chunk"] = "file",
) -> tuple[str, list[dict]]:
    """Invoke mlx_whisper as a subprocess; parse stdout lines for segments and progress.

    subprocess invoked via `sys.executable -u -m mlx_whisper.cli` + `PYTHONUNBUFFERED=1`
    + `bufsize=1` to ensure per-segment stdout events arrive in real time (no
    block-buffered end-burst).
    """
    _tmp_dir = tempfile.mkdtemp(prefix="locally_mlx_")
    cmd = [
        sys.executable,
        "-u",                 # unbuffered Python
        "-m",
        "mlx_whisper.cli",    # module entrypoint (not the bin/mlx_whisper console script)
        audio_path,
        "--output-format",
        "txt",
        "--output-dir",
        _tmp_dir,
    ]
    if model_dir:
        cmd += ["--model", model_dir]
    if prompt:
        cmd += ["--initial-prompt", prompt]

    # --- Phase-2: VAD preprocessing (mlx-path only) ---
    # Load PCM once via bundled ffmpeg, detect speech intervals, and convert to
    # mlx_whisper's --clip-timestamps comma-separated pairs format.
    # If VAD returns [] (too short, silent, or speech_ratio > 0.9), pass "0"
    # alone so mlx_whisper treats the trailing odd-count token as EOF and
    # decodes the full audio. See mlx_whisper/transcribe.py:189-196 and
    # plan §2.5.
    from app import audio_io as _audio_io
    from app import vad as _vad

    audio_duration: float | None = None
    try:
        _pcm = _audio_io.load_pcm_16k_mono(audio_path)
        audio_duration = len(_pcm) / float(_vad.SAMPLE_RATE)
        _intervals = _vad.detect_speech_timestamps(_pcm, _vad.SAMPLE_RATE)
    except TranscriptionError:
        # Surface ffmpeg errors directly.
        raise
    except Exception:
        # VAD internal failure should NOT abort transcription.
        _intervals = []

    if audio_duration is None:
        audio_duration = _get_audio_duration(audio_path)

    if _intervals:
        # Flatten to "s1,e1,s2,e2,..." string.
        _clip_arg = ",".join(f"{t:.3f}" for pair in _intervals for t in pair)
    else:
        _clip_arg = "0"  # single-token → mlx treats as start with implicit EOF

    cmd += ["--clip-timestamps", _clip_arg]

    # --language ko is REQUIRED. Do NOT remove or change to auto-detect.
    # Whisper's auto-detect runs on the opening seconds, which in real-world
    # recordings are often background music, breath, or near silence —
    # conditions where the language head drifts to English and emits phantom
    # phrases like "Thank you for watching" or "Thanks for listening". This
    # product transcribes Korean only, so forcing the language is correct AND
    # safer than letting the model guess.
    #
    # --hallucination-silence-threshold is intentionally NOT passed. The flag
    # exists in the underlying API but breaks mlx-whisper CLI's stdout JSON
    # parsing (the lines we depend on for per-segment streaming). The CT2
    # path applies it via kwargs, where it works correctly.
    #
    # Temperature is intentionally left unset so mlx CLI uses its default
    # fallback ladder (0.0 → 0.2 → 0.4 → 0.6 → 0.8 → 1.0). Do NOT pin
    # `--temperature 0` here: it makes decoding deterministic-greedy, which
    # produces infinite repetition loops on hard inputs (e.g. a 1-second
    # segment containing the same sentence repeated 16 times). The fallback
    # ladder is the decoder's built-in escape — when its compression-ratio
    # check trips on a repetitive output, it retries with higher randomness
    # and breaks the loop. Removing the fallback re-introduces the loops.
    _thresholds = THRESHOLD_PROFILE[profile]
    cmd += [
        "--language", "ko",
        "--condition-on-previous-text", "False",
        "--no-speech-threshold", str(_thresholds["no_speech_threshold"]),
        "--logprob-threshold", str(_LOGPROB_THRESHOLD),
        "--compression-ratio-threshold", str(_COMPRESSION_RATIO_THRESHOLD),
    ]

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,          # line-buffered
            env=env,
        )
    except OSError as exc:
        raise TranscriptionError(f"mlx_whisper launch failed: {exc}") from exc

    # Drain stderr in background thread to prevent pipe buffer deadlock.
    stderr_lines: list[str] = []

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line.rstrip("\n"))

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    # Parse stdout line-by-line: extract segments and report progress.
    segments: list[dict] = []
    started = time.monotonic()
    assert proc.stdout is not None
    for raw in proc.stdout:
        seg = parse_segment_line(raw.rstrip("\n"))
        if seg is None:
            continue
        # Guard: skip zero-length or empty-text segments (can arise from silent regions
        # when VAD is wired in later phases, but harmless to enforce now).
        if seg["end"] <= seg["start"] or not seg["text"]:
            continue
        segments.append(seg)
        if progress_cb:
            percent = min(seg["end"] / audio_duration, 1.0) if audio_duration else 0.0
            progress_cb({
                "percent": percent,
                "segment_count": len(segments),
                "elapsed_sec": time.monotonic() - started,
            })

    stderr_thread.join(timeout=5)
    proc.wait()
    shutil.rmtree(_tmp_dir, ignore_errors=True)

    if proc.returncode != 0:
        err_tail = "\n".join(stderr_lines[-20:])
        raise TranscriptionError(
            f"mlx_whisper exited with code {proc.returncode}: {err_tail}"
        )

    text = "\n".join(seg["text"] for seg in segments if seg["text"])
    return text, segments


def _run_ct2(
    audio_path: str,
    *,
    model_dir: str | None,
    prompt: str | None,
    progress_cb: Optional[Callable[[dict], None]],
    profile: Literal["file", "chunk"] = "file",
) -> tuple[str, list[dict]]:
    if model_dir is None:
        raise TranscriptionError("model_dir is required for faster-whisper path")
    model = _get_ct2_model(model_dir)
    # language="ko" is REQUIRED — see _run_mlx for rationale; same constraint.
    # Temperature is intentionally OMITTED so faster-whisper uses its default
    # fallback ladder (0.0..1.0 step 0.2) — do NOT pin temperature=0, it
    # re-introduces greedy repetition loops on hard inputs.
    # log_prob_threshold uses the underscore form (faster-whisper convention,
    # not the MLX --logprob-threshold hyphen form).
    _thresholds = THRESHOLD_PROFILE[profile]
    kwargs: dict = {
        "language": "ko",
        "beam_size": 5,
        "vad_filter": True,
        "condition_on_previous_text": False,
        "no_speech_threshold": _thresholds["no_speech_threshold"],
        "log_prob_threshold": _LOGPROB_THRESHOLD,
        "compression_ratio_threshold": _COMPRESSION_RATIO_THRESHOLD,
        "hallucination_silence_threshold": _thresholds["hallucination_silence_threshold"],
    }
    if prompt:
        kwargs["initial_prompt"] = prompt
    segments_iter, info = model.transcribe(audio_path, **kwargs)  # type: ignore
    duration = float(getattr(info, "duration", 0.0) or 0.0)
    text_parts: list[str] = []
    segments: list[dict] = []
    for progress, seg in _wrap_ct2(segments_iter, audio_duration_sec=duration):
        segments.append(seg)
        if seg["text"]:
            text_parts.append(seg["text"])
        if progress_cb:
            progress_cb(progress)
    return "\n".join(text_parts), segments


def run(
    audio_path: str,
    *,
    model_dir: str | None = None,
    prompt: str | None = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
    profile: Literal["file", "chunk"] = "file",
) -> tuple[str, list[dict]]:
    """OS-branched transcription.

    Returns (text, segments[]). segments entries: {start, end, text}.
    Raises TranscriptionError on failure.
    """
    if not Path(audio_path).exists():
        raise TranscriptionError(f"audio file not found: {audio_path}")
    if platform.system() == "Darwin":
        return _run_mlx(
            audio_path,
            model_dir=model_dir,
            prompt=prompt,
            progress_cb=progress_cb,
            profile=profile,
        )
    return _run_ct2(
        audio_path,
        model_dir=model_dir,
        prompt=prompt,
        progress_cb=progress_cb,
        profile=profile,
    )


def reset_model_singleton_for_testing() -> None:
    """Reset the module-level _MODEL singleton. Test-only helper."""
    global _MODEL
    with _MODEL_LOCK:
        _MODEL = None
