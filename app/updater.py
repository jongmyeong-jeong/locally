"""locally start 실행 시 원격 저장소 변경을 감지하고 자동 업데이트."""
from __future__ import annotations

import subprocess
from pathlib import Path

_SOURCE_DIR = Path.home() / ".locally" / "source"


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def check_and_apply() -> bool:
    """업데이트가 있으면 적용하고 True 반환. 최신이거나 실패하면 False."""
    if not (_SOURCE_DIR / ".git").exists():
        return False  # 직접 빌드(dev) 환경은 건너뜀

    try:
        _run(["git", "-C", str(_SOURCE_DIR), "fetch", "--quiet"], timeout=5)
        result = _run(
            ["git", "-C", str(_SOURCE_DIR), "rev-list", "HEAD..@{u}", "--count"],
            timeout=5,
        )
        if int(result.stdout.strip() or "0") == 0:
            return False

        _run(["git", "-C", str(_SOURCE_DIR), "pull", "--ff-only"], timeout=30)
        _run(["uv", "tool", "install", "--reinstall", str(_SOURCE_DIR)], timeout=120)
        return True
    except Exception:
        return False
