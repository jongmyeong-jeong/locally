#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["huggingface_hub"]
# ///
"""
Hugging Face Hub에서 mlx-whisper 모델을 다운로드한다.
stdout으로 JSON 진행률을 출력하고, stderr로 로그를 남긴다.

사용법: python3 download_model.py <target-dir>
"""
import sys
import json
import signal
from pathlib import Path

REPO_ID = "jongmyeong-jeong/whisper-large-v3-turbo-ko-mlx"
REQUIRED_FILES = ["config.json", "weights.safetensors"]


def _handle_sigterm(signum, frame):
    print("[INFO] SIGTERM received, aborting download", file=sys.stderr)
    sys.exit(1)


signal.signal(signal.SIGTERM, _handle_sigterm)


_last_reported_pct = -1


def report_progress(percent, downloaded="", total=""):
    """stdout으로 JSON 진행률을 출력한다. 같은 percent는 중복 출력하지 않는다."""
    global _last_reported_pct
    if percent == _last_reported_pct:
        return
    _last_reported_pct = percent
    print(
        json.dumps({"percent": percent, "downloaded": downloaded, "total": total}),
        flush=True,
    )


def _fmt_bytes(n):
    """바이트 수를 사람이 읽기 쉬운 문자열로 변환한다."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def download(target_dir: str):
    try:
        from huggingface_hub import hf_hub_download
        from tqdm.auto import tqdm as base_tqdm
    except ImportError:
        print("[ERROR] huggingface_hub가 설치되지 않았습니다.", file=sys.stderr)
        print("[ERROR] pip install huggingface_hub 를 실행하세요.", file=sys.stderr)
        sys.exit(1)

    model_dir = Path(target_dir) / "whisper-ko-turbo-mlx"
    model_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] 모델 다운로드 시작: {REPO_ID}", file=sys.stderr)
    print(f"[INFO] 저장 경로: {model_dir}", file=sys.stderr)

    total_files = len(REQUIRED_FILES)

    class ProgressTqdm(base_tqdm):
        """hf_hub_download의 tqdm_class로 전달하여 바이트 단위 진행률을 JSON으로 보고한다."""

        _file_index = 0

        def update(self, n=1):
            super().update(n)
            if self.total and self.total > 0:
                file_frac = self.n / self.total
                overall = (ProgressTqdm._file_index + file_frac) / total_files
                report_progress(
                    round(overall, 2),
                    _fmt_bytes(self.n),
                    _fmt_bytes(self.total),
                )

    for i, filename in enumerate(REQUIRED_FILES):
        ProgressTqdm._file_index = i
        print(f"[INFO] 다운로드 중: {filename} ({i + 1}/{total_files})", file=sys.stderr)
        report_progress(i / total_files, f"{i}/{total_files} files", f"{total_files} files")

        hf_hub_download(
            repo_id=REPO_ID,
            filename=filename,
            local_dir=str(model_dir),
            tqdm_class=ProgressTqdm,
        )

    report_progress(1.0, f"{total_files}/{total_files} files", f"{total_files} files")
    print("[OK] 모델 다운로드 완료", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python3 download_model.py <target-dir>", file=sys.stderr)
        sys.exit(1)

    download(sys.argv[1])
