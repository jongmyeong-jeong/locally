"""Tests for finalize SSE disconnect behavior.

Verifies that when a client disconnects during finalize:
1. The server logs 'finalize_client_disconnected' with session_id and note_id.
2. The finalize work continues to completion — 'complete' SSE event is emitted.
3. cancel_event is NOT set (finalize work is not cancelled).
"""
from __future__ import annotations

import io
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app import recordings
from app import server as server_mod
from app import transcribe as transcribe_mod
from app.server import create_app


# ---------------------------------------------------------------------------
# SSE parsing helper (same as test_finalize_live.py)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Autouse fixtures (mirror test_finalize_live.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_sessions():
    recordings._SESSIONS.clear()
    yield
    recordings._SESSIONS.clear()


@pytest.fixture(autouse=True)
def _reset_live_state():
    server_mod._VAD_DETECTORS.clear()
    server_mod._VAD_LOCKS.clear()
    server_mod._CHUNK_SEQ.clear()
    yield
    server_mod._VAD_DETECTORS.clear()
    server_mod._VAD_LOCKS.clear()
    server_mod._CHUNK_SEQ.clear()


# ---------------------------------------------------------------------------
# Test: client disconnect is logged but finalize work completes
# ---------------------------------------------------------------------------


class TestFinalizeDisconnectLogs:
    def test_finalize_logs_disconnect_without_cancel(
        self, tmp_home, monkeypatch, caplog
    ):
        """Disconnect during finalize → log emitted, 'complete' SSE still received.

        Verifies:
        - 'finalize_client_disconnected' log record exists
        - log payload includes 'session_id' and 'note_id'
        - SSE stream contains 'complete' event (cancel_event was NOT set)
        """
        monkeypatch.setattr(server_mod, "_any_model_ready", lambda: True)
        monkeypatch.setattr(transcribe_mod, "run", lambda *a, **kw: ("mocked text", []))

        # Patch Request.is_disconnected to return True immediately.
        # This makes _disconnect_logger fire on its first poll.
        from starlette.requests import Request as StarletteRequest
        monkeypatch.setattr(
            StarletteRequest,
            "is_disconnected",
            lambda self: _async_true(),
        )

        # Collect log records from the 'locally' logger which has propagate=False.
        locally_logger = logging.getLogger("locally")
        captured: list[logging.LogRecord] = []

        class _CapHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(record)

        handler = _CapHandler()
        handler.setLevel(logging.INFO)
        locally_logger.addHandler(handler)
        try:
            app = create_app()
            with TestClient(app) as c:
                # 1. Start recording session.
                r_start = c.post("/api/recordings", json={"title": "disconnect test"})
                assert r_start.status_code == 201
                sid = r_start.json()["id"]

                # 2. Upload seq=0 chunk to create the note row.
                chunk_resp = c.post(
                    f"/api/recordings/{sid}/chunk",
                    data={"seq": "0"},
                    files={
                        "chunk": (
                            "chunk.webm",
                            io.BytesIO(b"\x1a\x45\xdf\xa3" + b"\x00" * 124),
                            "application/octet-stream",
                        )
                    },
                )
                assert chunk_resp.status_code == 200

                # 3. Call finalize — client "disconnects" immediately.
                r_fin = c.post(
                    f"/api/recordings/{sid}/finalize",
                    json={"durationSec": 30.0},
                )
                assert r_fin.status_code == 200

            # 4. Parse SSE events and verify 'complete' was emitted.
            events = _parse_sse(r_fin.text)
            event_names = [e.get("event") for e in events]
            assert "complete" in event_names, (
                f"Expected 'complete' SSE event (cancel_event must NOT be set); "
                f"got events={event_names}"
            )

        finally:
            locally_logger.removeHandler(handler)

        # 5. Assert 'finalize_client_disconnected' log record exists.
        disconnect_records = [
            r for r in captured if r.msg == "finalize_client_disconnected"
        ]
        assert disconnect_records, (
            f"Expected 'finalize_client_disconnected' log record; "
            f"captured msgs={[r.msg for r in captured]}"
        )

        # 6. Assert payload contains 'session_id' and 'note_id'.
        rec = disconnect_records[0]
        payload = rec.args if isinstance(rec.args, dict) else {}
        assert "session_id" in payload, (
            f"Expected 'session_id' in log payload; payload={payload}"
        )
        assert "note_id" in payload, (
            f"Expected 'note_id' in log payload (value may be None); payload={payload}"
        )


async def _async_true() -> bool:
    """Coroutine that returns True immediately, simulating a disconnected client."""
    return True
