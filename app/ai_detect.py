"""Detect locally available AI CLI binaries (claude / codex).

Tiebreaker (N2): when both are on PATH, 'claude' wins by default.
Env override: LOCALLY_AI ∈ {'claude', 'codex', 'none'}.
"""
from __future__ import annotations

import os
import shutil

_CANDIDATES = ("claude", "codex")


def detect_ai_cli() -> dict | None:
    """Return {'name', 'path'} for the chosen AI CLI, or None."""
    env = os.environ.get("LOCALLY_AI", "").strip().lower()
    if env == "none":
        return None
    if env in _CANDIDATES:
        path = shutil.which(env)
        if path:
            return {"name": env, "path": path}
        # If env-requested CLI is not found, fall through to default probe.
    for name in _CANDIDATES:
        path = shutil.which(name)
        if path:
            return {"name": name, "path": path}
    return None


def availability() -> dict[str, bool]:
    """{'claude': bool, 'codex': bool} — independent of env override."""
    return {name: shutil.which(name) is not None for name in _CANDIDATES}
