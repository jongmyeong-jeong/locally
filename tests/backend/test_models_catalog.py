"""Tests for app/models_catalog.py: OS branching + N7 .incomplete/ sentinel."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import models_catalog


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    yield tmp_path


class TestCurrentOS:
    def test_darwin_returned_for_darwin(self, mock_platform):
        mock_platform("Darwin", "arm64")
        assert models_catalog.current_os() == "Darwin"

    def test_windows_returned_for_windows(self, mock_platform):
        mock_platform("Windows", "AMD64")
        assert models_catalog.current_os() == "Windows"

    def test_linux_returned_for_linux(self, mock_platform):
        mock_platform("Linux", "x86_64")
        assert models_catalog.current_os() == "Linux"

    def test_unknown_os_falls_back_to_linux(self, mock_platform):
        mock_platform("FreeBSD", "amd64")
        assert models_catalog.current_os() == "Linux"


class TestCatalogForCurrentOS:
    def test_mac_returns_mlx_entry(self, mock_platform):
        mock_platform("Darwin", "arm64")
        catalog = models_catalog.catalog_for_current_os()
        assert len(catalog) == 1
        assert catalog[0]["format"] == "mlx"
        assert "mlx" in catalog[0]["id"].lower()

    def test_windows_returns_ct2_entry(self, mock_platform):
        mock_platform("Windows", "AMD64")
        catalog = models_catalog.catalog_for_current_os()
        assert len(catalog) == 1
        assert catalog[0]["format"] == "ct2"
        assert "faster-whisper" in catalog[0]["id"]

    def test_linux_returns_ct2_entry(self, mock_platform):
        mock_platform("Linux", "x86_64")
        catalog = models_catalog.catalog_for_current_os()
        assert len(catalog) == 1
        assert catalog[0]["format"] == "ct2"


class TestModelReady:
    """N7: canonical dir must exist AND sibling .incomplete/ must NOT exist."""

    def test_missing_dir_returns_false(self):
        assert models_catalog.model_ready("foo/bar") is False

    def test_canonical_dir_with_files_returns_true(self, _tmp_home):
        cdir = models_catalog.model_dir_for("org/name")
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "model.bin").write_bytes(b"x")
        assert models_catalog.model_ready("org/name") is True

    def test_empty_canonical_dir_returns_false(self, _tmp_home):
        cdir = models_catalog.model_dir_for("org/name")
        cdir.mkdir(parents=True, exist_ok=True)
        assert models_catalog.model_ready("org/name") is False

    def test_incomplete_sibling_blocks_ready(self, _tmp_home):
        """N7: .incomplete/ presence → modelReady False even if canonical exists."""
        cdir = models_catalog.model_dir_for("org/name")
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "model.bin").write_bytes(b"x")
        idir = models_catalog.incomplete_dir_for("org/name")
        idir.mkdir(parents=True, exist_ok=True)
        assert models_catalog.model_ready("org/name") is False


class TestDirHelpers:
    def test_model_dir_uses_last_segment(self, _tmp_home):
        p = models_catalog.model_dir_for("org/sub/name-v3")
        assert p.name == "name-v3"
        assert p.parent == _tmp_home / ".locally" / "models"

    def test_incomplete_dir_suffix(self, _tmp_home):
        p = models_catalog.incomplete_dir_for("org/sub/name-v3")
        assert p.name == "name-v3.incomplete"
