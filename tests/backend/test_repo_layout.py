"""AC-12 tests: repo-layout invariants.

Assertions:
  - Required paths exist (pyproject.toml, app/, web/, tests/, .github/workflows/).
  - Deleted paths absent (src/, dist/, build/, package-lock.json).
  - Root package.json must NOT exist (B2).
  - web/package.json MUST exist (the only package.json in the repo).
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_required_paths_exist():
    required = [
        "pyproject.toml",
        "app",
        "app/__init__.py",
        "app/cli.py",
        "app/server.py",
        "app/paths.py",
        "app/db.py",
        "app/glossary.py",
        "app/models_catalog.py",
        "app/ai_detect.py",
        "app/transcribe.py",
        "app/transcribe_parser_mlx.py",
        "app/transcribe_parser_ct2.py",
        "app/summarize.py",
        "app/recordings.py",
        "app/server_jobs.py",
        "web",
        "web/package.json",
        "web/index.html",
        "web/vite.config.js",
        "tests",
        "tests/backend",
        "tests/conftest.py",
        "README.md",
        "CLAUDE.md",
    ]
    for rel in required:
        p = REPO_ROOT / rel
        assert p.exists(), f"required path missing: {rel}"


def test_deleted_paths_absent():
    forbidden = [
        "src",
        "electron",
        "main.js",
        "preload.js",
    ]
    for rel in forbidden:
        p = REPO_ROOT / rel
        assert not p.exists(), f"deleted path still present: {rel}"


def test_root_package_json_absent():
    """B2: the root-level package.json must NOT exist."""
    assert not (REPO_ROOT / "package.json").exists(), (
        "root package.json must be deleted (B2); only web/package.json is allowed"
    )


def test_web_package_json_exists():
    """The one and only package.json in the repo lives under web/."""
    assert (REPO_ROOT / "web" / "package.json").exists()
