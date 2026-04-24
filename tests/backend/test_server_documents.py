"""Tests for /api/documents endpoints (B3) and /api/glossary (AC-8)."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.server import create_app


@pytest.fixture
def client(tmp_home):  # noqa: ARG001
    app = create_app()
    with TestClient(app) as c:
        yield c


class TestCreateDocument:
    def test_create_document_no_title(self, client):
        """B3: POST /api/documents with empty JSON body → 201, title='untitled'."""
        r = client.post("/api/documents", json={})
        assert r.status_code == 201
        body = r.json()
        assert body["title"] == "untitled"
        assert body["status"] == "pending"
        assert body["id"]

    def test_create_document_with_title(self, client):
        r = client.post("/api/documents", json={"title": "회의 2026"})
        assert r.status_code == 201
        body = r.json()
        assert body["title"] == "회의 2026"

    def test_create_document_with_audio_path(self, client, tmp_path):
        """JSON payload carries audioPath; accepted verbatim."""
        r = client.post(
            "/api/documents",
            json={"title": "x", "audioPath": str(tmp_path / "a.m4a")},
        )
        assert r.status_code == 201
        assert r.json()["audioPath"] == str(tmp_path / "a.m4a")

    def test_create_document_multipart_upload_persists_file(self, client):
        payload = b"\x01\x02\x03" * 64
        r = client.post(
            "/api/documents",
            data={"title": "upload"},
            files={
                "file": (
                    "meeting.webm",
                    io.BytesIO(payload),
                    "audio/webm",
                )
            },
        )
        assert r.status_code == 201
        body = r.json()
        saved = Path(body["audioPath"])
        assert saved.exists()
        assert saved.read_bytes() == payload
        assert body["title"] == "upload"

    def test_list_documents(self, client):
        client.post("/api/documents", json={"title": "first"})
        client.post("/api/documents", json={"title": "second"})
        r = client.get("/api/documents")
        assert r.status_code == 200
        titles = [d["title"] for d in r.json()]
        assert "first" in titles
        assert "second" in titles


class TestDocumentCrud:
    def test_get_document(self, client):
        created = client.post("/api/documents", json={"title": "x"}).json()
        r = client.get(f"/api/documents/{created['id']}")
        assert r.status_code == 200
        assert r.json()["id"] == created["id"]

    def test_get_unknown_document_404(self, client):
        r = client.get("/api/documents/no-such-id")
        assert r.status_code == 404

    def test_patch_document(self, client):
        created = client.post("/api/documents", json={"title": "x"}).json()
        r = client.patch(
            f"/api/documents/{created['id']}",
            json={"title": "renamed"},
        )
        assert r.status_code == 200
        assert r.json()["title"] == "renamed"

    def test_delete_document(self, client):
        created = client.post("/api/documents", json={"title": "x"}).json()
        r = client.delete(f"/api/documents/{created['id']}")
        assert r.status_code == 204
        r2 = client.get(f"/api/documents/{created['id']}")
        assert r2.status_code == 404


class TestGlossaryEndpoint:
    def test_get_empty_glossary(self, client):
        r = client.get("/api/glossary")
        assert r.status_code == 200
        assert r.json() == []

    def test_put_glossary_persists(self, client, tmp_home):
        """AC-8: PUT returns 200 w/ Content-Length:0 + file written verbatim."""
        r = client.put(
            "/api/glossary",
            content=json.dumps(["Notion", "애플", "Figma"]).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 200
        # Content-Length:0 + empty body.
        assert r.headers.get("content-length") == "0"
        assert r.content == b""

        # File must contain the exact list with ensure_ascii=False.
        glossary_file = tmp_home / ".locally" / "workspace" / "glossary.json"
        assert glossary_file.exists()
        content = glossary_file.read_text(encoding="utf-8")
        assert json.loads(content) == ["Notion", "애플", "Figma"]

    def test_put_glossary_invalid_body(self, client):
        r = client.put(
            "/api/glossary",
            content=b'{"not":"array"}',
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400

    def test_put_glossary_get_round_trip(self, client, tmp_home):
        client.put(
            "/api/glossary",
            content=json.dumps(["A", "B"]).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        r = client.get("/api/glossary")
        assert r.json() == ["A", "B"]
