"""Glossary term persistence and prompt injection.

Source file: ~/.locally/workspace/glossary.json (UTF-8 JSON array of strings).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from app.paths import glossary_path


def _dedupe_preserve_order(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for t in terms:
        stripped = t.strip()
        if not stripped or stripped in seen:
            continue
        seen.add(stripped)
        result.append(stripped)
    return result


def load(path: Path | None = None) -> list[str]:
    """Read glossary.json; [] if missing or malformed."""
    target = path or glossary_path()
    if not target.exists():
        return []
    try:
        content = target.read_text(encoding="utf-8")
        parsed = json.loads(content)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(t) for t in parsed if isinstance(t, str)]


def save(terms: list[str], path: Path | None = None) -> None:
    """Atomic write of deduped, first-seen-order, stripped terms.

    ensure_ascii=False preserves Korean characters as-is.
    """
    target = path or glossary_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    cleaned = _dedupe_preserve_order(terms)
    payload = json.dumps(cleaned, ensure_ascii=False)
    # Atomic write via temp file + replace.
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(target.parent),
        prefix=".glossary-",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp.write(payload)
        tmp_name = tmp.name
    os.replace(tmp_name, target)


def inject_into_prompt(template: str, terms: list[str]) -> str:
    """Replace the literal placeholder '{glossary terms comma-separated}'."""
    joined = ", ".join(terms)
    return template.replace("{glossary terms comma-separated}", joined)
