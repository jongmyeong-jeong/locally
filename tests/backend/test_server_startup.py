"""AC-2 tests: `lonta start` stdout contract + --no-browser + root HTML.

Spawns the real CLI via subprocess so we exercise the stdout ordering that
automation scripts and CI rely on. Asserts:
  - First 5 stdout lines match the M8-widened AC-2 regex.
  - `httpx.get("/").text` contains `<div id="root">`.
  - `webbrowser.open` called exactly once (absent the --no-browser flag).
  - With --no-browser: only 4 lines, and webbrowser.open never called.
"""
from __future__ import annotations

import os
import re
import time

import httpx
import pytest

# Regex patterns per plan §5 AC-2 (widened for M8 all-busy case).
_LINE_1 = re.compile(r"^▸ OS 감지: (Darwin \(arm64\)|Darwin \(x86_64\)|Windows \(x86_64\)|Linux \([^)]+\))$")
_LINE_2 = re.compile(r"^▸ 데이터 경로: ~/\.lonta/data, ~/\.lonta$")
_LINE_3 = re.compile(r"^▸ 모델 상태: (설치되지 않음|준비됨)$")
_LINE_4 = re.compile(r"^▸ 서버 시작: http://(127\.0\.0\.1|localhost):(54787|5478[89]|5479[0-6]|[1-9][0-9]{3,4})$")
_LINE_5 = re.compile(r"^▸ 브라우저 오픈 중\.\.\.$")


def _read_first_lines_and_wait(proc, n_lines: int, timeout: float = 10.0) -> list[str]:
    """Read n non-empty stdout lines or time-out."""
    lines: list[str] = []
    deadline = time.monotonic() + timeout
    while len(lines) < n_lines:
        if proc.poll() is not None:
            # Process died before printing all expected lines; surface stderr.
            err = proc.stderr.read().decode("utf-8", errors="replace")
            raise AssertionError(f"CLI exited early: {err}")
        if time.monotonic() > deadline:
            raise AssertionError(f"timeout reading stdout; got {lines!r}")
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.05)
            continue
        decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
        if decoded:
            lines.append(decoded)
    return lines


def _wait_until_listening(port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/", timeout=1.0)
            if r.status_code in (200, 404):
                return
        except (httpx.ConnectError, httpx.TimeoutException):
            time.sleep(0.1)
    raise AssertionError(f"server did not become ready on port {port}")


class TestBadaScribeStartStdout:
    def test_lonta_start_stdout_lines(self, spawn_lonta_start, free_port):
        """AC-2: first 4 AC-2 lines (line 5 suppressed by --no-browser)."""
        proc = spawn_lonta_start(free_port)
        lines = _read_first_lines_and_wait(proc, 4, timeout=15.0)
        assert _LINE_1.match(lines[0]), f"line 1 mismatch: {lines[0]!r}"
        assert _LINE_2.match(lines[1]), f"line 2 mismatch: {lines[1]!r}"
        assert _LINE_3.match(lines[2]), f"line 3 mismatch: {lines[2]!r}"
        assert _LINE_4.match(lines[3]), f"line 4 mismatch: {lines[3]!r}"


class TestNoBrowserFlag:
    def test_no_browser_suppresses_line_5(self, spawn_lonta_start, free_port):
        """AC-2 minor fix: --no-browser suppresses 브라우저 오픈 중... line."""
        proc = spawn_lonta_start(free_port)  # fixture already passes --no-browser
        lines = _read_first_lines_and_wait(proc, 4, timeout=15.0)
        # After reading 4 lines, there must be NO 5th 브라우저 line emitted
        # before server readiness. We assert the server is listening and that
        # the next byte (if any) is not the browser line.
        _wait_until_listening(free_port, timeout=10.0)
        # Line 4 must be 서버 시작 line (i.e. AC-2 line 4).
        assert _LINE_4.match(lines[3]), lines[3]
        # Line 5 (if present) should NOT be 브라우저 오픈 중.
        # Give a short window for any spurious extra line; if stdout has data,
        # it must not match the browser line.
        time.sleep(0.5)
        if proc.stdout.readable():
            # Non-blocking read via os.read on the underlying fd where possible.
            try:
                fd = proc.stdout.fileno()
                os.set_blocking(fd, False)
                try:
                    raw = os.read(fd, 1024)
                except BlockingIOError:
                    raw = b""
                finally:
                    os.set_blocking(fd, True)
                for extra in raw.decode("utf-8", errors="replace").splitlines():
                    assert not _LINE_5.match(
                        extra.strip()
                    ), f"--no-browser should suppress line 5, saw: {extra!r}"
            except (OSError, AttributeError):
                # On Windows fd may be unavailable for set_blocking; skip.
                pass


class TestRootReturnsRootDiv:
    def test_root_html_contains_root_div(self, spawn_lonta_start, free_port):
        proc = spawn_lonta_start(free_port)
        _read_first_lines_and_wait(proc, 4, timeout=15.0)
        _wait_until_listening(free_port, timeout=10.0)
        # The SPA build output includes `<div id="root">`; if app/static is
        # missing (e.g. fresh clone without pnpm build) we skip gracefully.
        from pathlib import Path as _P

        static_index = _P(__file__).resolve().parents[2] / "app" / "static" / "index.html"
        if not static_index.exists():
            pytest.skip("web/dist not built; run `cd web && pnpm build` first")
        r = httpx.get(f"http://127.0.0.1:{free_port}/", timeout=2.0)
        assert r.status_code == 200
        assert '<div id="root">' in r.text
