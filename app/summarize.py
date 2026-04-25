"""Build the summary prompt, run the AI CLI asynchronously, write outputs.

Async subprocess model (A2):
  - asyncio.create_subprocess_exec(ai_path, *args, ...).
  - `on_heartbeat` called at monotonic elapsed_s ∈ {10, 20, 30, ...} (B4).
  - `cancel_event`: if set, kill the subprocess and raise CancelledError.
  - Timeout: asyncio.wait_for(process.communicate(), timeout_s); on
    TimeoutError → kill, raise subprocess.TimeoutExpired.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Awaitable, Callable, Literal

from app.glossary import inject_into_prompt
from app.paths import transcripts_dir, summaries_dir

SUMMARY_PROMPT_TEMPLATE = """다음 전사 내용을 한국어 회의록으로 정리해주세요. 아래 형식을 반드시 따라주세요:

# {제목}

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
{glossary terms comma-separated}

---
전사:
{transcript text}
"""


def build_prompt(
    *,
    transcript: str,
    glossary_terms: list[str],
    title: str = "회의록",
    template: str | None = None,
) -> str:
    """Build the summary prompt by substituting variables.

    Two paths:
    - template=None (legacy): uses SUMMARY_PROMPT_TEMPLATE with internal literals
      ({제목}, {glossary terms comma-separated}, {transcript text}).
    - template=str (new): user-facing variable names ({title}, {glossary},
      {transcript}). All substitutions use str.replace() — never str.format() —
      because templates may contain natural-language brace tokens like {내용}.
    """
    if template is None:
        text = SUMMARY_PROMPT_TEMPLATE
        # Title placeholder: replace only the first occurrence on the h1 line.
        text = text.replace("# {제목}", f"# {title}", 1)
        text = inject_into_prompt(text, glossary_terms)
        text = text.replace("{transcript text}", transcript)
    else:
        text = template
        text = text.replace("{title}", title)
        text = text.replace("{glossary}", ", ".join(glossary_terms))
        text = text.replace("{transcript}", transcript)
    return text


async def run_ai(
    *,
    ai_name: Literal["claude", "codex"],
    ai_path: str,
    prompt: str,
    timeout_s: int = 300,
    on_heartbeat: Callable[[int], Awaitable[None]] | None = None,
    cancel_event: asyncio.Event | None = None,
    on_process_started: Callable[[asyncio.subprocess.Process], None] | None = None,
) -> tuple[str, asyncio.subprocess.Process]:
    """Run an AI CLI subprocess asynchronously with heartbeat + cancel.

    Returns (stdout_text, process) on success.
    Raises asyncio.CancelledError if cancel_event is set.
    Raises subprocess.TimeoutExpired on timeout.
    Raises RuntimeError if the AI CLI exits with a non-zero status.
    """
    if ai_name == "claude":
        args = [ai_path, "-p", prompt]
    elif ai_name == "codex":
        args = [ai_path, "exec", prompt]
    else:  # pragma: no cover — typing prevents this at call sites
        raise ValueError(f"Unsupported AI CLI name: {ai_name}")

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    if on_process_started is not None:
        on_process_started(proc)

    heartbeat_task: asyncio.Task | None = None
    cancel_watcher_task: asyncio.Task | None = None
    if on_heartbeat is not None or cancel_event is not None:
        heartbeat_task = asyncio.create_task(
            _heartbeat_loop(proc, on_heartbeat, cancel_event)
        )
    if cancel_event is not None:
        cancel_watcher_task = asyncio.create_task(
            _cancel_watcher(proc, cancel_event)
        )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s
        )
    except asyncio.TimeoutError as exc:
        _safe_kill(proc)
        await proc.wait()
        raise subprocess.TimeoutExpired(cmd=args, timeout=timeout_s) from exc
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
        if cancel_watcher_task is not None:
            cancel_watcher_task.cancel()

    if cancel_event is not None and cancel_event.is_set():
        raise asyncio.CancelledError("run_ai cancelled by cancel_event")

    if proc.returncode != 0:
        raise RuntimeError(
            f"AI CLI exited with code {proc.returncode}: "
            f"{(stderr_bytes or b'').decode('utf-8', errors='replace').strip()}"
        )

    return stdout_bytes.decode("utf-8", errors="replace"), proc


async def _heartbeat_loop(
    proc: asyncio.subprocess.Process,
    on_heartbeat: Callable[[int], Awaitable[None]] | None,
    cancel_event: asyncio.Event | None,
) -> None:
    """Emit heartbeats at monotonic integer seconds {10, 20, 30, ...} (B4)."""
    elapsed = 0
    while True:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            return
        elapsed += 10
        if proc.returncode is not None:
            return
        if cancel_event is not None and cancel_event.is_set():
            return
        if on_heartbeat is not None:
            try:
                await on_heartbeat(elapsed)
            except Exception:
                return


async def _cancel_watcher(
    proc: asyncio.subprocess.Process,
    cancel_event: asyncio.Event,
) -> None:
    """Kill the subprocess as soon as cancel_event is set."""
    try:
        await cancel_event.wait()
    except asyncio.CancelledError:
        return
    _safe_kill(proc)


def _safe_kill(proc: asyncio.subprocess.Process) -> None:
    try:
        if proc.returncode is None:
            proc.kill()
    except ProcessLookupError:
        pass


def write_outputs(
    *,
    doc_id: str,
    slug: str,
    summary_md: str | None,
    prompt_md: str,
    transcript_root: Path | None = None,
    summary_root: Path | None = None,
) -> dict:
    """Write {slug}.prompt.md to transcripts_dir and {slug}.summary.md to summaries_dir.

    Returns {'summary_path', 'prompt_path'}; 'summary_path' is None if
    summary_md is None.
    """
    t_root = transcript_root if transcript_root is not None else transcripts_dir()
    s_root = summary_root if summary_root is not None else summaries_dir()
    t_root.mkdir(parents=True, exist_ok=True)
    s_root.mkdir(parents=True, exist_ok=True)
    prompt_path = t_root / f"{slug}.prompt.md"
    _atomic_write(prompt_path, prompt_md)
    summary_path: Path | None = None
    if summary_md is not None:
        summary_path = s_root / f"{slug}.summary.md"
        _atomic_write(summary_path, summary_md)
    return {
        "summary_path": str(summary_path) if summary_path else None,
        "prompt_path": str(prompt_path),
    }


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.stem}-",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp.write(content)
        tmp_name = tmp.name
    os.replace(tmp_name, path)
