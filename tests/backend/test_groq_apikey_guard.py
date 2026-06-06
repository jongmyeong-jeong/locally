"""Tests for groq API key guard on FastAPI endpoints (Step 6.5).

Uses FastAPI TestClient (httpx-based synchronous client).
Monkey-patches os.environ to control GROQ_API_KEY.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.server import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Redirect ~/.lonta to a temp dir so tests don't touch real data."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Also ensure the system-info cache is cold for each test.
    import app.server as server_mod
    server_mod._SYSTEM_INFO_CACHE["expires_at"] = 0.0
    server_mod._SYSTEM_INFO_CACHE["value"] = None
    yield tmp_path


@pytest.fixture
def client(monkeypatch):
    """TestClient with no GROQ_API_KEY set."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def client_with_key(monkeypatch):
    """TestClient with GROQ_API_KEY set."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key-12345")
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests: GET /api/system/info
# ---------------------------------------------------------------------------


class TestSystemInfo:
    def test_groq_configured_false_when_key_unset(self, client):
        resp = client.get("/api/system/info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["groqConfigured"] is False

    def test_groq_configured_true_when_key_set(self, client_with_key):
        resp = client_with_key.get("/api/system/info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["groqConfigured"] is True


# ---------------------------------------------------------------------------
# Tests: POST /api/recordings
# ---------------------------------------------------------------------------


class TestRecordingsGuard:
    def test_503_when_groq_key_missing(self, client):
        resp = client.post("/api/recordings", json={})
        assert resp.status_code == 503
        data = resp.json()
        assert data["error"] == "groq_api_key_missing"

    def test_201_when_groq_key_present(self, client_with_key, monkeypatch):
        """With key set, POST /api/recordings proceeds past the guard."""
        import app.recordings as rec_mod

        # Stub recordings.try_start_session to return a fake session dict.
        monkeypatch.setattr(
            rec_mod,
            "try_start_session",
            lambda title=None: {"id": "sess-test", "title": title or "untitled"},
        )

        resp = client_with_key.post("/api/recordings", json={})
        assert resp.status_code == 201
        assert resp.json()["id"] == "sess-test"


