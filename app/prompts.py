"""Prompt preset persistence and seed.

Source file: ~/.locally/workspace/prompts.json
Schema: list of {"id": int, "name": str, "template": str}
Array order = display order. No is_default flag, no order field.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from app.paths import prompts_path


# 기존 SUMMARY_PROMPT_TEMPLATE의 사용자-facing 변수명 정규화 버전.
# 다음 3개 리터럴만 치환:
#   '# {제목}' → '# {title}'
#   '{transcript text}' → '{transcript}'
#   '{glossary terms comma-separated}' → '{glossary}'
# 그 외 자연어 중괄호 토큰({내용}, {담당자} 등)은 모두 그대로 보존.
SEED_TEMPLATE = """다음 전사 내용을 한국어 회의록으로 정리해주세요. 아래 형식을 반드시 따라주세요:

# {title}

## 일시
{전사에서 추론 가능하면 날짜/시간, 아니면 '알 수 없음'}

## 참석자
{전사에서 언급된 인물들 bullet 리스트, 없으면 '알 수 없음'}

## 주요 논의사항
### 1. {주제명}
- {내용}

### 2. {주제명}
- {내용}

## 결정사항
- {결정된 내용}

## 액션 아이템
- [ ] @{담당자} - {할 일} (~{마감})

---
다음 용어는 반드시 이 정확한 철자로 작성하세요 (글로사리):
{glossary}

---
전사:
{transcript}
"""


def load(path: Path | None = None) -> list[dict]:
    """Read prompts.json; [] if missing or malformed.

    Filters items lacking required fields (id, name, template).
    """
    target = path or prompts_path()
    if not target.exists():
        return []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    cleaned: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if "id" not in item or "name" not in item or "template" not in item:
            continue
        try:
            prompt_id = int(item["id"])
        except (TypeError, ValueError):
            continue
        cleaned.append({
            "id": prompt_id,
            "name": str(item["name"]),
            "template": str(item["template"]),
        })
    return cleaned


def save(presets: list[dict], path: Path | None = None) -> None:
    """Atomic write of presets list (tmp → os.replace)."""
    target = path or prompts_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(presets, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(target.parent),
        prefix=".prompts-",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp.write(payload)
        tmp_name = tmp.name
    os.replace(tmp_name, target)


def next_id(presets: list[dict]) -> int:
    """Returns max(id) + 1; deleted IDs are NOT reused. Empty list → 1."""
    return max((p["id"] for p in presets), default=0) + 1


def ensure_seed(path: Path | None = None) -> None:
    """If no presets exist, seed one '회의록' preset with SEED_TEMPLATE."""
    presets = load(path)
    if not presets:
        save([{"id": 1, "name": "회의록", "template": SEED_TEMPLATE}], path)
