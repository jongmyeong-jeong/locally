"""Pre-flight checks for `lonta start`.

Runs once on first launch (detected via ~/.lonta/setup.json).
Subsequent launches are silent unless a warning/error occurs.

Test seam: set LONTA_SKIP_PREFLIGHT=1 to bypass entirely (see cli.py).
"""
from __future__ import annotations

import datetime
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from app import paths


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    message: str
    detail: str | None = None


def is_first_run() -> bool:
    path = paths.setup_json_path()
    if not path.exists():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return not data.get("setup_done", False)
    except (OSError, json.JSONDecodeError):
        return True


def mark_setup_done() -> None:
    path = paths.setup_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "setup_done": True,
        "setup_at": datetime.datetime.now().astimezone().isoformat(),
        "version": _current_version(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _current_version() -> str:
    try:
        from importlib.metadata import version
        return version("lonta")
    except Exception:
        from app import __version__
        return __version__


def check_ffmpeg() -> CheckResult:
    if shutil.which("ffmpeg"):
        return CheckResult(ok=True, message="ffmpeg found (PATH)")
    cache_bin = paths.app_home() / "bin" / "ffmpeg"
    if cache_bin.exists() and os.access(cache_bin, os.X_OK):
        os.environ["PATH"] = f"{cache_bin.parent}{os.pathsep}{os.environ.get('PATH', '')}"
        return CheckResult(ok=True, message="ffmpeg found (cache)")
    return CheckResult(ok=False, message="ffmpeg not found")


def _download_ffmpeg(console) -> bool:
    """Download ffmpeg for macOS only. Returns True on success."""
    if platform.system() != "Darwin":
        console.print("[yellow]⚠ ffmpeg not found — install manually (non-macOS)[/yellow]")
        return False

    url = "https://evermeet.cx/ffmpeg/get/zip"
    console.print("[blue]⧗ Downloading ffmpeg...[/blue]")
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_zip = Path(tmp_dir) / "ffmpeg.zip"
            urllib.request.urlretrieve(url, tmp_zip)
            with zipfile.ZipFile(tmp_zip) as zf:
                zf.extract("ffmpeg", tmp_dir)
            dest = paths.app_home() / "bin" / "ffmpeg"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(Path(tmp_dir) / "ffmpeg"), dest)
            dest.chmod(0o755)
            subprocess.run(["codesign", "--sign", "-", "--force", str(dest)], check=False)
            os.environ["PATH"] = f"{dest.parent}{os.pathsep}{os.environ.get('PATH', '')}"
            console.print("[green]✓ ffmpeg installed to cache[/green]")
            return True
    except Exception as exc:
        console.print(f"[red]✗ ffmpeg download failed: {exc}[/red]")
        return False


def ensure_ffmpeg(*, first_run: bool, console) -> CheckResult:
    result = check_ffmpeg()
    if result.ok:
        if first_run:
            console.print(f"[green]✓ {result.message}[/green]")
        return result
    if first_run:
        console.print(f"[yellow]⚠ {result.message}[/yellow]")
        _download_ffmpeg(console)
        return check_ffmpeg()
    console.print(f"[yellow]⚠ {result.message} — audio processing may fail[/yellow]")
    return result


def check_version(*, timeout: float = 2.5) -> CheckResult:
    current = _current_version()
    try:
        from packaging.version import Version
        with urllib.request.urlopen("https://pypi.org/pypi/lonta/json", timeout=timeout) as r:
            latest = json.loads(r.read())["info"]["version"]
        if Version(latest) > Version(current):
            return CheckResult(
                ok=True,
                message=f"New version available: {current} → {latest}",
                detail=latest,
            )
        return CheckResult(ok=True, message=f"lonta {current} is up to date")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, KeyError):
        return CheckResult(ok=True, message="Checking for updates...", detail="skipped (network)")


def maybe_prompt_upgrade(result: CheckResult, *, console) -> bool:
    if not result.detail or result.detail == "skipped (network)":
        return False
    try:
        from packaging.version import Version
        if not Version(result.detail) > Version(_current_version()):
            return False
    except Exception:
        return False
    console.print(f"[cyan]{result.message}[/cyan]")
    try:
        from rich.prompt import Prompt
        answer = Prompt.ask("Upgrade now?", choices=["y", "n"], default="n", console=console)
        return answer == "y"
    except Exception:
        return False


def run_upgrade() -> bool:
    cmd = (
        ["pipx", "upgrade", "lonta"]
        if shutil.which("pipx")
        else [sys.executable, "-m", "pip", "install", "--upgrade", "lonta"]
    )
    return subprocess.run(cmd).returncode == 0


def restart_cli() -> NoReturn:
    mark_setup_done()
    os.execvp(sys.executable, [sys.executable, "-m", "app.cli"] + sys.argv[1:])


def run_preflight(*, no_browser: bool) -> None:
    from rich.console import Console

    console = Console(stderr=True)
    first_run = is_first_run()

    if first_run:
        console.print("[bold]lonta — pre-flight check[/bold]")

    ensure_ffmpeg(first_run=first_run, console=console)

    version_result = check_version()
    if first_run:
        console.print(f"[dim]{version_result.message}[/dim]")

    if maybe_prompt_upgrade(version_result, console=console):
        if run_upgrade():
            restart_cli()

    mark_setup_done()
