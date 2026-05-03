"""Shared pytest configuration + fixtures.

Adds the project root to sys.path so `from app import ...` works without
requiring a full `pip install -e .`. Extended in Phase G with the fixture
set listed in plan §5.
"""
from __future__ import annotations

import contextlib
import os
import platform as _platform
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# tmp_home — redirect Path.home() and ~ expansion to a tmp dir
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_home(monkeypatch, tmp_path):
    """Sandbox the user's home directory for the duration of a test.

    Redirects:
      - Path.home() → tmp_path
      - os.path.expanduser("~...") → tmp_path/...
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    real_expanduser = os.path.expanduser

    def _fake_expanduser(p: str) -> str:
        if not isinstance(p, str):
            return real_expanduser(p)
        if p == "~":
            return str(tmp_path)
        if p.startswith("~/") or p.startswith("~" + os.sep):
            return str(tmp_path / p[2:])
        return real_expanduser(p)

    monkeypatch.setattr(os.path, "expanduser", _fake_expanduser)
    yield tmp_path


# ---------------------------------------------------------------------------
# mock_platform — monkeypatch platform.system()/machine()
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_platform(monkeypatch):
    """Returns a setter function: set_platform(system, machine)."""

    def _set(system: str, machine: str) -> None:
        monkeypatch.setattr(_platform, "system", lambda: system)
        monkeypatch.setattr(_platform, "machine", lambda: machine)

    return _set


# ---------------------------------------------------------------------------
# mock_shutil_which — parametrize which()
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_shutil_which(monkeypatch):
    """Returns a setter: configure_which(found: list[str]).

    After calling, shutil.which(name) returns f"/usr/local/bin/{name}" if
    `name in found`, else None. Applies to every `shutil.which` import site
    (stdlib module-level patch).
    """
    import shutil as _shutil

    def _configure(found: list[str]) -> None:
        s = set(found)

        def _fake_which(cmd, mode=os.F_OK | os.X_OK, path=None):  # noqa: ARG001
            return f"/usr/local/bin/{cmd}" if cmd in s else None

        monkeypatch.setattr(_shutil, "which", _fake_which)

    return _configure


# ---------------------------------------------------------------------------
# free_port — bind then release to get an unused port
# ---------------------------------------------------------------------------


@pytest.fixture
def free_port():
    """Return an OS-assigned free port (released before the test body runs)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


# ---------------------------------------------------------------------------
# mock_webbrowser_open — capture webbrowser.open calls
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_webbrowser_open(monkeypatch):
    """Replace webbrowser.open with a MagicMock; return the mock."""
    import webbrowser as _wb

    m = MagicMock(return_value=True)
    monkeypatch.setattr(_wb, "open", m)
    return m


# ---------------------------------------------------------------------------
# sample_m4a — synthesize a short silent m4a via ffmpeg if available, else
#              fall back to an empty-but-extensioned file (some tests only
#              check file IO, not decoding).
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_m4a(tmp_path):
    """Return a Path to a small silent m4a file.

    Uses ffmpeg if available (preferred — a real file is decodable); else
    writes a stub empty file so server upload tests still get a valid path.
    """
    dest = tmp_path / "sample.m4a"
    ffmpeg = None
    try:
        import shutil as _sh

        ffmpeg = _sh.which("ffmpeg")
    except Exception:  # noqa: BLE001
        ffmpeg = None

    if ffmpeg:
        try:
            subprocess.run(
                [
                    ffmpeg,
                    "-nostdin",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=16000:cl=mono",
                    "-t",
                    "1",
                    "-c:a",
                    "aac",
                    str(dest),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=20,
            )
            return dest
        except (subprocess.SubprocessError, OSError):
            pass
    # Fallback: write placeholder bytes.
    dest.write_bytes(b"\x00" * 128)
    return dest


# ---------------------------------------------------------------------------
# spawn_lonta_start — start `lonta start --no-browser --port=<free>` subprocess
# ---------------------------------------------------------------------------


@pytest.fixture
def spawn_lonta_start(tmp_path, monkeypatch):
    """Start a real `lonta start --no-browser --host 127.0.0.1 --port <p>` process.

    Yields (process, port). Test body must poll until HTTP readiness.
    Terminates on teardown.
    """
    spawned: list[subprocess.Popen] = []

    def _spawn(port: int, extra_args: list[str] | None = None, env: dict | None = None):
        env_full = os.environ.copy()
        if env:
            env_full.update(env)
        # Make the spawned process use tmp_path as HOME so it doesn't litter
        # the real ~/.lonta during the test.
        env_full["HOME"] = str(tmp_path)
        env_full["USERPROFILE"] = str(tmp_path)  # Windows
        # Skip update check and preflight so AC-2 lines are exactly lines 1-4.
        env_full.setdefault("LONTA_SKIP_UPDATE", "1")
        env_full.setdefault("LONTA_SKIP_PREFLIGHT", "1")
        cmd = [
            sys.executable,
            "-m",
            "app.cli",
            "start",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-browser",
        ]
        if extra_args:
            cmd.extend(extra_args)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env_full,
            cwd=str(_PROJECT_ROOT),
        )
        spawned.append(proc)
        return proc

    yield _spawn

    for proc in spawned:
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)
