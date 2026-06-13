"""Regression tests for the SPA fallback route's path-traversal containment.

The catch-all ``GET /{full_path:path}`` handler must never serve a file that
resolves outside ``app/static/``. A URL-encoded slash (``%2f``) bypasses ASGI
path normalization, so ``/..%2fserver.py`` reaches the handler as
``full_path='../server.py'`` — without a containment guard that would have
served the ``app/server.py`` source file (arbitrary file read).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.server import create_app


@pytest.fixture
def client(tmp_home):  # noqa: ARG001
    app = create_app()
    with TestClient(app) as c:
        yield c


# Encoded-slash traversal payloads that survive ASGI normalization and reach
# the handler with a `../` prefix intact.
_TRAVERSAL_URLS = [
    "/..%2fserver.py",
    "/%2e%2e%2fserver.py",
    "/..%2f..%2f..%2f..%2f..%2fetc%2fpasswd",
]


@pytest.mark.parametrize("url", _TRAVERSAL_URLS)
def test_spa_fallback_blocks_path_traversal(client, url):
    """Traversal attempts must fall through to the SPA, never leak outside files."""
    res = client.get(url)
    # Server source must never be returned.
    assert "def create_app" not in res.text
    assert "FastAPI app for lonta" not in res.text
    # /etc/passwd-style content must never be returned.
    assert "root:" not in res.text
    # The handler either serves the SPA shell (index.html) or 404s — both safe.
    assert res.status_code in (200, 404)
    if res.status_code == 200:
        assert "text/html" in res.headers.get("content-type", "")


def test_spa_fallback_serves_index_html(client):
    """Sanity: a normal unknown route still serves the SPA shell."""
    res = client.get("/some/spa/route")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")
