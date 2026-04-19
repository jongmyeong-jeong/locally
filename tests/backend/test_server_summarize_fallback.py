"""AC-6 fallback test: no AI CLI → SSE `prompt_ready` with copyText contract."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as db_mod
from app.server import create_app


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


class TestSummarizeFallback:
    def test_summarize_no_ai_emits_prompt_ready(
        self, tmp_home, mock_platform, mock_shutil_which
    ):
        """AC-6: neither claude nor codex on PATH → SSE `prompt_ready`.

        Contract:
          - data.prompt starts with "다음 전사 내용을 한국어 회의록으로 정리해주세요".
          - data.copyText == data.prompt + "\\n\\n---\\n전사:\\n" + data.transcript.
          - All three fields are RFC 8259 JSON-escaped (we simply verify parse).
        """
        mock_platform("Darwin", "arm64")
        mock_shutil_which([])  # no claude, no codex

        # Seed a document with a transcript file.
        transcript = "오늘은 Notion 연동 회의였습니다."
        transcript_path = tmp_home / ".locally" / "workspace" / "documents" / "t.md"
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(transcript, encoding="utf-8")

        with db_mod.open_db() as conn:
            doc = db_mod.create_document(conn, title="회의")
            db_mod.update_document(
                conn,
                doc["id"],
                status="transcribed",
                transcript_path=str(transcript_path),
            )
        doc_id = doc["id"]

        app = create_app()
        with TestClient(app) as c:
            r = c.post(
                f"/api/documents/{doc_id}/summarize",
                json={"ai": "auto"},
            )
            assert r.status_code == 200
            events = _parse_sse(r.text)

        prompt_events = [e for e in events if e.get("event") == "prompt_ready"]
        assert len(prompt_events) == 1
        data = prompt_events[0]["data"]
        # RFC 8259: the parse above succeeded → JSON is valid.
        assert data["prompt"].startswith(
            "다음 전사 내용을 한국어 회의록으로 정리해주세요"
        )
        # copyText = prompt + "\n\n---\n전사:\n" + transcript
        expected = data["prompt"] + "\n\n---\n전사:\n" + data["transcript"]
        assert data["copyText"] == expected
        assert data["transcript"] == transcript

        # {slug}.prompt.md file exists in transcripts/ subfolder.
        transcripts = tmp_home / ".locally" / "workspace" / "documents" / "transcripts"
        prompt_files = list(transcripts.glob("*.prompt.md"))
        assert len(prompt_files) == 1

    def test_summarize_none_explicit_also_emits_prompt_ready(
        self, tmp_home, mock_platform, mock_shutil_which
    ):
        """Explicit `ai=none` forces fallback even when claude is installed."""
        mock_platform("Darwin", "arm64")
        mock_shutil_which(["claude"])

        transcript = "test"
        transcript_path = tmp_home / "t.md"
        transcript_path.write_text(transcript, encoding="utf-8")

        with db_mod.open_db() as conn:
            doc = db_mod.create_document(conn, title="회의")
            db_mod.update_document(
                conn,
                doc["id"],
                status="transcribed",
                transcript_path=str(transcript_path),
            )

        app = create_app()
        with TestClient(app) as c:
            r = c.post(
                f"/api/documents/{doc['id']}/summarize",
                json={"ai": "none"},
            )
            events = _parse_sse(r.text)
        prompt_events = [e for e in events if e.get("event") == "prompt_ready"]
        assert len(prompt_events) == 1
