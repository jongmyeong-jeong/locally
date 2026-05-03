"""Typer CLI for lonta.

Public CLI surface:
  lonta start [--host --port --no-browser]

AC-2 stdout (plan §4.7 — paste verbatim, '▸' is U+25B8):
  ▸ OS 감지: {os_label}
  ▸ 데이터 경로: ~/.lonta/data, ~/.lonta
  ▸ 서버 시작: http://localhost:{port}
  ▸ 브라우저 오픈 중...
"""
from __future__ import annotations

import platform
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import typer

from app import paths

app = typer.Typer(help="lonta — 로컬 회의록 생성기", no_args_is_help=True)

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _bootstrap() -> None:
    """Load project-scoped .env before any env reads occur.

    override=False means real shell env vars take precedence over .env values,
    so existing ``export GROQ_API_KEY=...`` users are not disrupted.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=_ENV_PATH, override=False)
    except Exception as exc:  # noqa: BLE001
        import warnings

        warnings.warn(f"Failed to load .env ({_ENV_PATH}): {exc}", stacklevel=1)

_ARROW = "\u25b8"  # ▸  (U+25B8)
_DEFAULT_PORT = 54787
_PORT_RANGE_END = 54796  # inclusive


@app.callback()
def main() -> None:
    """Top-level CLI group."""
    _bootstrap()


def _os_label() -> str:
    system = platform.system()
    machine = platform.machine() or ""
    if machine.upper() == "AMD64":
        machine = "x86_64"
    if system == "Darwin":
        return f"Darwin ({machine or 'arm64'})"
    if system == "Windows":
        return f"Windows ({machine or 'x86_64'})"
    return f"{system} ({machine or 'unknown'})"


def _port_is_free(host: str, port: int) -> bool:
    """Return True iff we can bind (host, port)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def _pick_os_assigned_port(host: str) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def _resolve_port(host: str, requested: int) -> tuple[int, list[str]]:
    """Apply the M8 cascade. Returns (chosen_port, extra_stdout_lines)."""
    if _port_is_free(host, requested):
        return requested, []

    cascade: list[str] = []
    current = requested
    next_port = current + 1
    while next_port <= _PORT_RANGE_END:
        cascade.append(f"{_ARROW} 포트 {current} 점유, {next_port}로 시도...")
        if _port_is_free(host, next_port):
            return next_port, cascade
        current = next_port
        next_port += 1

    os_port = _pick_os_assigned_port(host)
    cascade.append(
        f"{_ARROW} 포트 54787~54796 모두 점유, OS 할당 포트로 fallback..."
    )
    return os_port, cascade


@app.command("start")
def start(
    host: str = typer.Option("localhost", help="바인드 호스트"),
    port: int = typer.Option(_DEFAULT_PORT, help="바인드 포트 (기본 54787)"),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="브라우저 자동 오픈 비활성화"
    ),
) -> None:
    """로컬 웹 서버를 시작합니다."""
    import os

    if not os.environ.get("LONTA_SKIP_UPDATE"):
        from app import updater
        typer.echo(f"{_ARROW} 업데이트 확인 중...")
        if updater.check_and_apply():
            typer.echo(f"{_ARROW} 업데이트 완료 — 다시 실행해주세요")
            raise typer.Exit()

    if not os.environ.get("LONTA_SKIP_PREFLIGHT"):
        from app import preflight
        preflight.run_preflight(no_browser=no_browser)
    _print_start(host=host, port=port, no_browser=no_browser)


def _print_start(*, host: str, port: int, no_browser: bool) -> None:
    typer.echo(f"{_ARROW} OS 감지: {_os_label()}")
    typer.echo(f"{_ARROW} 데이터 경로: ~/.lonta/data, ~/.lonta")

    chosen_port, cascade = _resolve_port(host, port)
    all_busy = any("모두 점유" in line for line in cascade)
    for line in cascade:
        if "모두 점유" not in line:
            typer.echo(line)
    for line in cascade:
        if "모두 점유" in line:
            typer.echo(line)

    typer.echo(f"{_ARROW} 서버 시작: http://localhost:{chosen_port}")
    if not no_browser:
        typer.echo(f"{_ARROW} 브라우저 오픈 중...")

    if all_busy:
        typer.echo(
            f"{_ARROW} 기존 lonta 프로세스 가능성 — lsof -i :54787 / "
            "작업 관리자에서 lonta.exe 종료 권장"
        )

    paths.write_runtime(pid=_pid(), port=chosen_port, started_at=time.time())

    import uvicorn

    if not no_browser:
        def _open_when_ready() -> None:
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                try:
                    urllib.request.urlopen(
                        f"http://localhost:{chosen_port}/api/system/info",
                        timeout=1,
                    )
                    webbrowser.open(f"http://localhost:{chosen_port}/")
                    return
                except Exception:
                    time.sleep(0.1)

        threading.Thread(target=_open_when_ready, daemon=True).start()

    uvicorn.run(
        "app.server:app",
        host=host,
        port=chosen_port,
        log_level="warning",
        access_log=False,
    )


def _pid() -> int:
    import os

    return os.getpid()


if __name__ == "__main__":
    app()
