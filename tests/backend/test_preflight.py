"""Tests for app/preflight.py: first-run detection, ffmpeg check, version check, upgrade."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app import preflight as onboarding
from app import paths as paths_mod


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    yield tmp_path


# ── is_first_run ────────────────────────────────────────────────────────────


class TestIsFirstRun:
    def test_file_missing_returns_true(self):
        assert onboarding.is_first_run() is True

    def test_setup_done_false_returns_true(self, _tmp_home):
        p = paths_mod.setup_json_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"setup_done": False}), encoding="utf-8")
        assert onboarding.is_first_run() is True

    def test_setup_done_true_returns_false(self, _tmp_home):
        p = paths_mod.setup_json_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"setup_done": True}), encoding="utf-8")
        assert onboarding.is_first_run() is False

    def test_malformed_json_returns_true(self, _tmp_home):
        p = paths_mod.setup_json_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("not-json", encoding="utf-8")
        assert onboarding.is_first_run() is True


# ── mark_setup_done ─────────────────────────────────────────────────────────


class TestMarkSetupDone:
    def test_writes_expected_schema(self, _tmp_home):
        onboarding.mark_setup_done()
        p = paths_mod.setup_json_path()
        assert p.exists()
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["setup_done"] is True
        assert "setup_at" in data
        assert "version" in data

    def test_is_idempotent(self, _tmp_home):
        onboarding.mark_setup_done()
        onboarding.mark_setup_done()
        p = paths_mod.setup_json_path()
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["setup_done"] is True


# ── check_ffmpeg ─────────────────────────────────────────────────────────────


class TestCheckFfmpeg:
    def test_in_path(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)
        result = onboarding.check_ffmpeg()
        assert result.ok is True
        assert "PATH" in result.message

    def test_in_cache_bin(self, _tmp_home, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        cache_bin = paths_mod.app_home() / "bin" / "ffmpeg"
        cache_bin.parent.mkdir(parents=True, exist_ok=True)
        cache_bin.touch()
        cache_bin.chmod(0o755)
        result = onboarding.check_ffmpeg()
        assert result.ok is True
        assert "cache" in result.message

    def test_both_missing(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        result = onboarding.check_ffmpeg()
        assert result.ok is False
        assert "not found" in result.message


# ── check_version ─────────────────────────────────────────────────────────────


def _fake_urlopen(version_str: str):
    payload = json.dumps({"info": {"version": version_str}}).encode()

    class _FakeResponse:
        def read(self):
            return payload
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass

    return lambda url, timeout=None: _FakeResponse()


class TestCheckVersion:
    def test_up_to_date(self, monkeypatch):
        monkeypatch.setattr(onboarding, "_current_version", lambda: "0.2.0")
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen("0.2.0"))
        result = onboarding.check_version()
        assert result.ok is True
        assert "up to date" in result.message

    def test_update_available(self, monkeypatch):
        monkeypatch.setattr(onboarding, "_current_version", lambda: "0.2.0")
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen("0.3.0"))
        result = onboarding.check_version()
        assert result.ok is True
        assert result.detail == "0.3.0"
        assert "0.2.0 → 0.3.0" in result.message

    def test_network_error(self, monkeypatch):
        def _raise(*a, **kw):
            raise urllib.error.URLError("timeout")
        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        result = onboarding.check_version()
        assert result.ok is True
        assert result.detail == "skipped (network)"


# ── run_upgrade ───────────────────────────────────────────────────────────────


class TestRunUpgrade:
    def test_uses_pipx_when_available(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pipx" if name == "pipx" else None)
        captured = {}

        def fake_run(cmd, **_):
            captured["cmd"] = cmd
            m = MagicMock()
            m.returncode = 0
            return m

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert onboarding.run_upgrade() is True
        assert captured["cmd"] == ["pipx", "upgrade", "lonta"]

    def test_falls_back_to_pip(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        captured = {}

        def fake_run(cmd, **_):
            captured["cmd"] = cmd
            m = MagicMock()
            m.returncode = 0
            return m

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert onboarding.run_upgrade() is True
        assert captured["cmd"] == [sys.executable, "-m", "pip", "install", "--upgrade", "lonta"]
