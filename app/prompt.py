"""Prompt injection persistence.

Source file: ~/.locally/workspace/prompt.json (UTF-8 JSON object).
Format: {"prompt": "..."} — a single string injected into Groq transcription calls.

Backward compatibility: if the file contains a JSON array of strings (old glossary.json
format), the terms are joined with ", " and treated as the prompt value. The file is
NOT rewritten on read — write() always uses the new format.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from app.paths import prompt_path

logger = logging.getLogger(__name__)

_PROMPT_SOFT_CAP = 600


def load(path: Path | None = None) -> str | None:
    """Read prompt.json; None if missing, malformed, or empty.

    Supports old glossary.json array-of-strings format for backward compatibility:
    terms are joined with ", " and returned as a single string.
    """
    target = path or prompt_path()
    if not target.exists():
        return None
    try:
        content = target.read_text(encoding="utf-8")
        parsed = json.loads(content)
    except (OSError, json.JSONDecodeError):
        return None

    # New format: {"prompt": "..."}
    if isinstance(parsed, dict):
        value = parsed.get("prompt")
        if not isinstance(value, str) or not value.strip():
            return None
        prompt_str = value.strip()
    elif isinstance(parsed, list):
        # Old format: ["term1", "term2", ...] (backward compat — read-only)
        terms = [str(t).strip() for t in parsed if isinstance(t, str) and str(t).strip()]
        if not terms:
            return None
        prompt_str = ", ".join(terms)
    else:
        return None

    if len(prompt_str) > _PROMPT_SOFT_CAP:
        logger.warning("prompt_too_long", extra={"length": len(prompt_str)})
        # Truncate at word boundary within the cap.
        truncated = prompt_str[:_PROMPT_SOFT_CAP]
        last_space = truncated.rfind(" ")
        prompt_str = truncated[:last_space] if last_space != -1 else truncated

    return prompt_str


def save(prompt: str, path: Path | None = None) -> None:
    """Atomic write of prompt string in new {"prompt": "..."} format.

    ensure_ascii=False preserves Korean characters as-is.
    """
    target = path or prompt_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"prompt": prompt.strip()}, ensure_ascii=False)
    # Atomic write via temp file + replace.
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(target.parent),
        prefix=".prompt-",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp.write(payload)
        tmp_name = tmp.name
    os.replace(tmp_name, target)
