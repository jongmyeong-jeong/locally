"""F5: POST /api/documents/:id/summarize with prompt_id selects preset template."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app import prompts as prompts_mod
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


class TestSummarizeWithPromptId:
    def _setup_doc(self, c: TestClient, tmp_home, transcript_text: str = "TRANSCRIPT") -> str:
        from app import db as db_mod
        from app import paths

        with db_mod.open_db() as conn:
            doc = db_mod.create_document(conn, title="회의", status="transcribed")
        doc_id = doc["id"]
        tx_dir = paths.transcripts_dir()
        tx_path = tx_dir / f"{doc_id}.md"
        tx_path.write_text(transcript_text, encoding="utf-8")
        with db_mod.open_db() as conn:
            db_mod.update_document(conn, doc_id, transcript_path=str(tx_path))
        return doc_id

    def test_uses_selected_preset_template(self, tmp_home):
        # Arrange: 두 프리셋, prompt_id로 두 번째 선택
        prompts_mod.save([
            {"id": 1, "name": "First", "template": "FIRST: {transcript}"},
            {"id": 2, "name": "Second", "template": "SECOND: {transcript}"},
        ])
        with TestClient(create_app()) as c:
            doc_id = self._setup_doc(c, tmp_home, transcript_text="HELLO")
            # ai=none → prompt_ready 이벤트로 prompt 확인 가능
            r = c.post(
                f"/api/documents/{doc_id}/summarize",
                json={"ai": "none", "prompt_id": 2},
            )
            assert r.status_code == 200
            events = _parse_sse(r.text)
            ready = next(e for e in events if e.get("event") == "prompt_ready")
            assert ready["data"]["prompt"].startswith("SECOND: HELLO")

    def test_falls_back_to_first_on_invalid_id(self, tmp_home):
        prompts_mod.save([
            {"id": 1, "name": "First", "template": "FIRST: {transcript}"},
            {"id": 2, "name": "Second", "template": "SECOND: {transcript}"},
        ])
        with TestClient(create_app()) as c:
            doc_id = self._setup_doc(c, tmp_home, transcript_text="X")
            r = c.post(
                f"/api/documents/{doc_id}/summarize",
                json={"ai": "none", "prompt_id": 999},
            )
            events = _parse_sse(r.text)
            ready = next(e for e in events if e.get("event") == "prompt_ready")
            assert ready["data"]["prompt"].startswith("FIRST: X")

    def test_falls_back_to_first_when_prompt_id_omitted(self, tmp_home):
        prompts_mod.save([
            {"id": 1, "name": "First", "template": "FIRST: {transcript}"},
        ])
        with TestClient(create_app()) as c:
            doc_id = self._setup_doc(c, tmp_home)
            r = c.post(
                f"/api/documents/{doc_id}/summarize",
                json={"ai": "none"},
            )
            events = _parse_sse(r.text)
            ready = next(e for e in events if e.get("event") == "prompt_ready")
            assert "FIRST:" in ready["data"]["prompt"]

    def test_ensure_seed_on_summarize_when_no_prompts_file(self, tmp_home):
        # prompts.json 파일이 없는 상태에서 summarize 진입 → ensure_seed가 시드 1개 생성
        # 이후 summarize는 시드의 SEED_TEMPLATE으로 진행
        with TestClient(create_app()) as c:
            doc_id = self._setup_doc(c, tmp_home, transcript_text="HI")
            r = c.post(
                f"/api/documents/{doc_id}/summarize",
                json={"ai": "none"},
            )
            assert r.status_code == 200
            events = _parse_sse(r.text)
            ready = next(e for e in events if e.get("event") == "prompt_ready")
            # SEED_TEMPLATE의 첫 라인이 들어있어야 함
            assert "한국어 회의록으로 정리" in ready["data"]["prompt"]
            assert "HI" in ready["data"]["prompt"]

    def test_locally_ai_none_writes_prompt_md_with_selected_template(self, tmp_home):
        # G3: LOCALLY_AI=none 시뮬레이션 (ai="none") → .prompt.md 파일에
        # 선택된 프리셋 템플릿이 렌더되어 저장됨.
        from app import paths

        prompts_mod.save([
            {"id": 1, "name": "First", "template": "FIRST: {transcript}"},
            {"id": 2, "name": "Second", "template": "SECOND: {transcript}"},
        ])
        with TestClient(create_app()) as c:
            doc_id = self._setup_doc(c, tmp_home, transcript_text="HELLO")
            r = c.post(
                f"/api/documents/{doc_id}/summarize",
                json={"ai": "none", "prompt_id": 2},
            )
            assert r.status_code == 200
            # prompt_ready 후 write_outputs로 .prompt.md가 디스크에 저장됨
            files = list(paths.transcripts_dir().glob("*.prompt.md"))
            assert len(files) >= 1
            content = files[0].read_text(encoding="utf-8")
            assert content.startswith("SECOND: HELLO")
