"""Filesystem path helpers + slug/basename utilities.

Layout (platform-agnostic via pathlib.Path.home()):
  ~/.locally/                 ← locally_home()
    ├── workspace/            ← workspace_root()
    │   ├── documents/
    │   │   ├── transcripts/
    │   │   └── summaries/
    │   ├── audio/
    │   └── glossary.json
    ├── db.sqlite
    ├── models/
    ├── logs/
    └── runtime.json

Contract notes:
  - slugify (M7): NFC-normalize, keep Korean/English/digits, replace whitespace
    with '-', drop other punctuation, truncate on code-point boundary to
    max_len; ALWAYS returns non-empty string (empty/all-rejected → 'untitled').
  - audio_basename (M7) trusts slugify (no extra fallback).
  - runtime.json stores pid/port/started_at. No flock (single-shell practice).
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

_SLUG_FALLBACK = "untitled"
# Keep Hangul syllables + Jamo + ASCII letters/digits + hyphen.
_SLUG_ALLOWED = re.compile(
    r"[^A-Za-z0-9\-"
    r"\uAC00-\uD7A3"  # Hangul syllables
    r"\u1100-\u11FF"  # Hangul Jamo
    r"\u3130-\u318F"  # Hangul compatibility Jamo
    r"]+"
)
_WHITESPACE = re.compile(r"\s+")


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def workspace_root() -> Path:
    """~/.locally/workspace/ (created if absent)."""
    return _ensure(Path.home() / ".locally" / "workspace")


def locally_home() -> Path:
    """~/.locally/ (created if absent)."""
    return _ensure(Path.home() / ".locally")


def documents_dir() -> Path:
    return _ensure(workspace_root() / "documents")


def transcripts_dir() -> Path:
    return _ensure(documents_dir() / "transcripts")


def summaries_dir() -> Path:
    return _ensure(documents_dir() / "summaries")


def audio_dir() -> Path:
    return _ensure(workspace_root() / "audio")


def logs_dir() -> Path:
    return _ensure(locally_home() / "logs")


def models_dir() -> Path:
    return _ensure(locally_home() / "models")


def glossary_path() -> Path:
    return workspace_root() / "glossary.json"


def prompts_path() -> Path:
    return workspace_root() / "prompts.json"


def db_path() -> Path:
    return locally_home() / "db.sqlite"


def runtime_json_path() -> Path:
    return locally_home() / "runtime.json"


def setup_json_path() -> Path:
    return locally_home() / "setup.json"


def settings_json_path() -> Path:
    return locally_home() / "settings.json"


def slugify(text: str, *, max_len: int = 50) -> str:
    """Normalize + sanitize a title to a filesystem-safe slug.

    Rules (M7):
    - NFC normalize first so composed/decomposed Hangul compare equally.
    - Collapse any run of whitespace to a single '-'.
    - Drop characters outside [A-Za-z0-9-] and Hangul ranges.
    - Collapse multiple '-' into one; strip leading/trailing '-'.
    - Truncate on code-point boundary to max_len; then strip '-' again.
    - Empty or all-rejected input → literal 'untitled'.
    """
    if text is None:
        return _SLUG_FALLBACK
    s = unicodedata.normalize("NFC", str(text))
    s = _WHITESPACE.sub("-", s)
    s = _SLUG_ALLOWED.sub("", s)
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        return _SLUG_FALLBACK
    # Code-point boundary truncation (post-NFC).
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    if not s:
        return _SLUG_FALLBACK
    return s


def audio_basename(title: str | None, now: datetime, *, doc_id: str | None = None) -> str:
    """Return '{YYYY-MM-DD}-{slug}[-{doc_id[:8]}]' (no extension).

    slugify is always non-empty so no extra fallback is needed here.
    doc_id suffix guarantees uniqueness across multiple transcriptions of the same title.
    """
    date_str = now.strftime("%Y-%m-%d")
    slug = slugify(title if title is not None else "")
    base = f"{date_str}-{slug}"
    if doc_id:
        base = f"{base}-{doc_id[:8]}"
    return base


def write_runtime(pid: int, port: int, started_at: float) -> None:
    path = runtime_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pid": int(pid), "port": int(port), "started_at": float(started_at)}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def read_runtime() -> dict | None:
    path = runtime_json_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def clear_runtime() -> None:
    path = runtime_json_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
