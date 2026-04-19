"""Tests for app/ai_detect.py: N2 tiebreaker + LOCALLY_AI env override."""
from __future__ import annotations

import pytest

from app import ai_detect


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("LOCALLY_AI", raising=False)


class TestDetectAiCli:
    def test_no_cli_returns_none(self, mock_shutil_which):
        mock_shutil_which([])
        assert ai_detect.detect_ai_cli() is None

    def test_claude_only(self, mock_shutil_which):
        mock_shutil_which(["claude"])
        result = ai_detect.detect_ai_cli()
        assert result == {"name": "claude", "path": "/usr/local/bin/claude"}

    def test_codex_only(self, mock_shutil_which):
        mock_shutil_which(["codex"])
        result = ai_detect.detect_ai_cli()
        assert result == {"name": "codex", "path": "/usr/local/bin/codex"}

    def test_both_present_returns_claude(self, mock_shutil_which):
        """N2 tiebreaker: claude wins by default when both are installed."""
        mock_shutil_which(["claude", "codex"])
        result = ai_detect.detect_ai_cli()
        assert result == {"name": "claude", "path": "/usr/local/bin/claude"}

    def test_env_locally_ai_codex_overrides(self, mock_shutil_which, monkeypatch):
        """N2 env: LOCALLY_AI=codex forces codex selection."""
        monkeypatch.setenv("LOCALLY_AI", "codex")
        mock_shutil_which(["claude", "codex"])
        result = ai_detect.detect_ai_cli()
        assert result == {"name": "codex", "path": "/usr/local/bin/codex"}

    def test_env_locally_ai_claude_explicit(self, mock_shutil_which, monkeypatch):
        monkeypatch.setenv("LOCALLY_AI", "claude")
        mock_shutil_which(["claude", "codex"])
        result = ai_detect.detect_ai_cli()
        assert result == {"name": "claude", "path": "/usr/local/bin/claude"}

    def test_env_none_returns_none_strict(self, mock_shutil_which, monkeypatch):
        """LOCALLY_AI=none returns None even if both CLIs are on PATH."""
        monkeypatch.setenv("LOCALLY_AI", "none")
        mock_shutil_which(["claude", "codex"])
        assert ai_detect.detect_ai_cli() is None

    def test_env_override_case_insensitive(self, mock_shutil_which, monkeypatch):
        monkeypatch.setenv("LOCALLY_AI", "CODEX")
        mock_shutil_which(["claude", "codex"])
        result = ai_detect.detect_ai_cli()
        assert result == {"name": "codex", "path": "/usr/local/bin/codex"}

    def test_env_override_missing_cli_falls_back(self, mock_shutil_which, monkeypatch):
        """LOCALLY_AI=codex but codex not installed → fall through to default probe."""
        monkeypatch.setenv("LOCALLY_AI", "codex")
        mock_shutil_which(["claude"])
        result = ai_detect.detect_ai_cli()
        assert result == {"name": "claude", "path": "/usr/local/bin/claude"}


class TestAvailability:
    def test_neither(self, mock_shutil_which):
        mock_shutil_which([])
        assert ai_detect.availability() == {"claude": False, "codex": False}

    def test_both(self, mock_shutil_which):
        mock_shutil_which(["claude", "codex"])
        assert ai_detect.availability() == {"claude": True, "codex": True}

    def test_availability_ignores_env(self, mock_shutil_which, monkeypatch):
        monkeypatch.setenv("LOCALLY_AI", "none")
        mock_shutil_which(["claude"])
        # availability() reports actual PATH, not env-gated selection.
        assert ai_detect.availability() == {"claude": True, "codex": False}
