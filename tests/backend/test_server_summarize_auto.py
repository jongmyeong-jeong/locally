"""AC-5 integration test: upload → transcribe → summarize auto path.

Mocks:
  - app.transcribe.run: returns a fake transcript that contains a glossary
    term (satisfies B1's OR-logic — "Notion" in transcript).
  - shutil.which("claude") via mock_shutil_which(["claude"]).
  - asyncio.create_subprocess_exec: returns a fake process whose stdout is
    a short markdown summary with 5 h2 headings.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import glossary as glossary_mod
from app.server import create_app


FAKE_SUMMARY_MD = """# 회의록

## 일시
2026-04-17

## 참석자
- 김개발자

## 주요 논의사항
### 1. Notion 연동
- API 스펙 검토

## 결정사항
- 다음 주까지 초안 확정

## 액션 아이템
- [ ] @김개발자 - 초안 작성 (~04-24)
"""


def _parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    current: dict = {}
    for raw in text.splitlines():
        if raw.startswith(":"):
            continue
        if raw == "":
            if current:
                events.append(current)
                current = {}
            continue
        if raw.startswith("event: "):
            current["event"] = raw[len("event: "):]
        elif raw.startswith("data: "):
            try:
                current["data"] = json.loads(raw[len("data: "):])
            except json.JSONDecodeError:
                current["data"] = raw[len("data: "):]
    if current:
        events.append(current)
    return events


class _FakeProc:
    """Mimics asyncio.subprocess.Process for a one-shot success with stdout."""

    def __init__(self, stdout_text: str):
        self.returncode = 0
        self._stdout = stdout_text.encode("utf-8")
        self.killed = False

    async def communicate(self):
        await asyncio.sleep(0)
        self.returncode = 0
        return self._stdout, b""

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode


class TestUploadTranscribeSummarize:
    def test_upload_transcribe_summarize(
        self, tmp_home, mock_platform, mock_shutil_which, monkeypatch
    ):
        """AC-5: doc → transcribe → summarize_auto happy path."""
        mock_platform("Darwin", "arm64")
        mock_shutil_which(["claude"])

        # Glossary: seed a term to validate B1 OR-logic + M10 build_prompt contract.
        glossary_mod.save(["Notion", "애플"])

        # Mock transcribe to return a deterministic transcript with glossary term.
        from app import transcribe as transcribe_mod

        fake_transcript = "안녕하세요 오늘은 Notion 연동 회의입니다."

        def _fake_transcribe(audio_path, *, model_dir=None, prompt=None, progress_cb=None):
            # Emit a few progress events.
            if progress_cb:
                for pct in (0.1, 0.4, 0.7, 1.0):
                    progress_cb({"percent": pct, "segment_count": 1, "elapsed_sec": 0.0})
            return fake_transcript, []

        monkeypatch.setattr(transcribe_mod, "run", _fake_transcribe)

        # Mock the AI subprocess to yield FAKE_SUMMARY_MD.
        async def _fake_exec(*args, **kwargs):  # noqa: ARG001
            return _FakeProc(FAKE_SUMMARY_MD)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

        app = create_app()
        with TestClient(app) as c:
            # 1. Create document with audioPath pointing at a fake audio file.
            audio_path = tmp_home / "a.m4a"
            audio_path.write_bytes(b"\x00" * 128)

            r = c.post(
                "/api/documents",
                json={"title": "회의", "audioPath": str(audio_path)},
            )
            assert r.status_code == 201
            doc = r.json()
            doc_id = doc["id"]
            assert doc["title"] == "회의"
            assert doc["status"] == "pending"

            # 2. Transcribe.
            r = c.post(f"/api/documents/{doc_id}/transcribe")
            assert r.status_code == 200
            events = _parse_sse(r.text)
            progress = [e for e in events if e.get("event") == "progress"]
            complete = [e for e in events if e.get("event") == "complete"]
            assert len(progress) >= 4
            assert len(complete) == 1
            transcript_path = complete[0]["data"]["transcriptPath"]
            assert transcript_path.endswith(".md")
            # B1: transcript contains the glossary term.
            t = Path(transcript_path).read_text(encoding="utf-8")
            assert "Notion" in t

            # 3. Summarize auto.
            r = c.post(
                f"/api/documents/{doc_id}/summarize",
                json={"ai": "auto"},
            )
            assert r.status_code == 200
            events = _parse_sse(r.text)
            complete = [e for e in events if e.get("event") == "complete"]
            assert len(complete) == 1
            summary_path = complete[0]["data"]["summaryPath"]
            summary_content = Path(summary_path).read_text(encoding="utf-8")
            assert summary_content.startswith("# ")
            # 5 h2 headings per plan §5.
            h2_count = summary_content.count("\n## ")
            assert h2_count == 5, f"expected 5 h2 headings, got {h2_count}"
