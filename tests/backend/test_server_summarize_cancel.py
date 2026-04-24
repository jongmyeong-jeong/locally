"""AC-5 + A2: mid-flight cancel emits SSE error {message:'cancelled', canRetry:false}.

Mock strategy:
  - shutil.which("claude") → path.
  - asyncio.create_subprocess_exec → real `python -c "time.sleep(60)"` so
    cancellation has a real PID to kill.
  - After POSTing /summarize, issue POST /cancel on the same doc_id.
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import db as db_mod
from app import server_jobs
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


@pytest.fixture(autouse=True)
def _reset_jobs():
    server_jobs.reset_for_testing()
    yield
    server_jobs.reset_for_testing()


class TestSummarizeCancel:
    def test_cancel_kills_subprocess(
        self, tmp_home, mock_platform, mock_shutil_which, monkeypatch
    ):
        """A2: POST /cancel mid-summarize → SSE error 'cancelled' within 2s."""
        mock_platform("Darwin", "arm64")
        mock_shutil_which(["claude"])

        # Seed document + transcript.
        transcript_path = tmp_home / "t.md"
        transcript_path.write_text("some transcript", encoding="utf-8")
        with db_mod.open_db() as conn:
            doc = db_mod.create_document(conn, title="x")
            db_mod.update_document(
                conn,
                doc["id"],
                status="transcribed",
                transcript_path=str(transcript_path),
            )
        doc_id = doc["id"]

        # Replace create_subprocess_exec with a long-lived python sleeper.
        real_exec = asyncio.create_subprocess_exec

        async def _long_sleep_exec(*args, **kwargs):
            return await real_exec(
                sys.executable,
                "-c",
                "import time; time.sleep(60)",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _long_sleep_exec)

        app = create_app()

        with TestClient(app) as c:
            # Fire cancel from a background thread ~0.5s after the request starts.
            def _cancel_after_delay():
                time.sleep(0.5)
                # Retry a few times since server must register job before cancel can find it.
                for _ in range(20):
                    r = c.post(f"/api/documents/{doc_id}/cancel")
                    if r.status_code == 204:
                        break
                    time.sleep(0.1)

            t = threading.Thread(target=_cancel_after_delay, daemon=True)
            t.start()

            t0 = time.monotonic()
            r = c.post(
                f"/api/documents/{doc_id}/summarize",
                json={"ai": "auto"},
            )
            elapsed = time.monotonic() - t0
            assert r.status_code == 200
            t.join(timeout=5)

        events = _parse_sse(r.text)
        errors = [e for e in events if e.get("event") == "error"]
        assert len(errors) == 1, f"expected one error event; got events={events}"
        data = errors[0]["data"]
        assert data["message"] == "cancelled"
        assert data["canRetry"] is False
        # Must have happened within a reasonable window (≤ 10s budget).
        assert elapsed < 10.0, f"cancel took too long: {elapsed}s"
