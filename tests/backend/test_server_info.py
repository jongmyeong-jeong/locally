"""AC-3 tests for GET /api/system/info: shape for Mac and Windows."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import ai_detect
from app.server import create_app, get_cached_system_info, invalidate_system_info_cache


@pytest.fixture
def client(tmp_home):  # noqa: ARG001 — side effect fixture
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_system_info_cache():
    invalidate_system_info_cache()
    yield
    invalidate_system_info_cache()


class TestInfoShape:
    def test_info_shape_mac(self, tmp_home, mock_platform, mock_shutil_which):
        mock_platform("Darwin", "arm64")
        mock_shutil_which(["ffmpeg", "claude"])
        app = create_app()
        with TestClient(app) as c:
            r = c.get("/api/system/info")
        assert r.status_code == 200
        body = r.json()
        assert body["os"] == "Darwin"
        assert body["arch"] == "arm64"
        assert isinstance(body["modelCatalog"], list)
        assert len(body["modelCatalog"]) == 1
        assert body["modelCatalog"][0]["format"] == "mlx"
        assert isinstance(body["modelReady"], bool)
        assert isinstance(body["aiAvailable"], dict)
        assert isinstance(body["aiAvailable"]["claude"], bool)
        assert isinstance(body["aiAvailable"]["codex"], bool)
        assert body["aiAvailable"]["claude"] is True
        assert body["aiAvailable"]["codex"] is False
        assert isinstance(body["ffmpegAvailable"], bool)
        assert body["ffmpegAvailable"] is True

    def test_info_shape_windows(self, tmp_home, mock_platform, mock_shutil_which):
        mock_platform("Windows", "AMD64")
        mock_shutil_which([])  # no claude/codex/ffmpeg on clean Windows
        app = create_app()
        with TestClient(app) as c:
            r = c.get("/api/system/info")
        assert r.status_code == 200
        body = r.json()
        assert body["os"] == "Windows"
        assert body["arch"] == "AMD64"
        assert body["modelCatalog"][0]["format"] == "ct2"
        assert body["aiAvailable"] == {"claude": False, "codex": False}
        assert body["ffmpegAvailable"] is False

    def test_model_ready_false_without_download(self, tmp_home, mock_platform):
        """Fresh tmp_home has no models/ — modelReady must be False."""
        mock_platform("Darwin", "arm64")
        app = create_app()
        with TestClient(app) as c:
            r = c.get("/api/system/info")
        assert r.json()["modelReady"] is False

    def test_system_info_endpoint_uses_short_ttl_cache(self, monkeypatch, tmp_home):
        calls = {"count": 0}
        original = ai_detect.availability

        def counted_availability():
            calls["count"] += 1
            return original()

        invalidate_system_info_cache()
        monkeypatch.setattr(ai_detect, "availability", counted_availability)

        app = create_app()
        with TestClient(app) as c:
            first = c.get("/api/system/info")
            second = c.get("/api/system/info")

        assert first.status_code == 200
        assert second.status_code == 200
        assert calls["count"] == 1

        invalidate_system_info_cache()
        get_cached_system_info()
        assert calls["count"] == 2
