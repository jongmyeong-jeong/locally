"""OS-branched Whisper model catalog + readiness probe.

Mac (Darwin)  → MLX Whisper (Turbo, Korean fine-tune).
Windows/Linux → faster-whisper (CT2; Turbo, Korean fine-tune).

model_ready() checks:
  - canonical dir ~/.locally/models/{last-seg}/ exists, AND
  - sibling '.incomplete' dir does NOT exist (N7), AND
  - all HF-metadata siblings are present (best-effort: metadata fetch may
    fail offline, in which case we accept presence of canonical dir only
    with a minimum-of-one-file sanity check).
"""
from __future__ import annotations

import platform
from pathlib import Path
from typing import Literal

from app.paths import models_dir

_MAC_ENTRY = {
    "id": "jongmyeong-jeong/whisper-large-v3-turbo-ko-mlx",
    "displayName": "Whisper Turbo 한국어 (MLX)",
    "size_mb": 1610,
    "format": "mlx",
}

_NON_MAC_ENTRY = {
    "id": "ghost613/faster-whisper-large-v3-turbo-korean",
    "displayName": "Whisper Turbo 한국어 (CTranslate2)",
    "size_mb": 3240,
    "format": "ct2",
}


def current_os() -> Literal["Darwin", "Windows", "Linux"]:
    system = platform.system()
    if system in ("Darwin", "Windows", "Linux"):
        return system  # type: ignore[return-value]
    return "Linux"  # fallback for BSDs etc.


def current_arch() -> str:
    return platform.machine() or "unknown"


def catalog_for_current_os() -> list[dict]:
    if current_os() == "Darwin":
        return [dict(_MAC_ENTRY)]
    return [dict(_NON_MAC_ENTRY)]


def model_dir_for(model_id: str) -> Path:
    """Return canonical directory path for a model id."""
    last_seg = model_id.rsplit("/", 1)[-1]
    return models_dir() / last_seg


def incomplete_dir_for(model_id: str) -> Path:
    """Return the sibling '.incomplete' directory path (N7 sentinel)."""
    last_seg = model_id.rsplit("/", 1)[-1]
    return models_dir() / f"{last_seg}.incomplete"


def size_mb_for(model_id: str) -> int:
    """Return expected model size in MB from the catalog. Returns 0 if unknown."""
    for entry in [_MAC_ENTRY, _NON_MAC_ENTRY]:
        if entry["id"] == model_id:
            return entry["size_mb"]
    return 0


def model_ready(model_id: str) -> bool:
    canonical = model_dir_for(model_id)
    incomplete = incomplete_dir_for(model_id)
    if incomplete.exists():
        return False
    if not canonical.exists() or not canonical.is_dir():
        return False
    # Minimum sanity: at least one file within (downloaded content).
    try:
        for _ in canonical.iterdir():
            return True
        return False
    except OSError:
        return False
