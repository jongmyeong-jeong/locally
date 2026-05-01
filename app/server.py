"""FastAPI app for locally.

Serves the static SPA (built into app/static/) and the /api/* routes listed in
plan §4.2. Same-origin; no CORS; all SSE emitters add:
  - Content-Type: text/event-stream
  - X-Accel-Buffering: no
  - Cache-Control: no-cache
and intersperse 1s `: keep-alive\\n\\n` comments to defeat intermediate
buffering.

SSE event wire format:
    event: <name>\\n
    data: <one JSON object>\\n
    \\n
"""
from __future__ import annotations

import asyncio
import json
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Literal

from fastapi import (
    Body,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import logger
from app import ai_detect
from app import audio_io
from app import db as db_mod
from app import glossary as glossary_mod
from app import models_catalog
from app import paths
from app import prompts as prompts_mod
from app import recording_chunks
from app import recordings as recordings_mod
from app import server_jobs
from app import summarize as summarize_mod
from app import transcribe as transcribe_mod
from app import transcript_format as transcript_format_mod
from app import transcribe_queue
from app import vad_realtime

# ---------------------------------------------------------------------------
# File upload whitelist (plan §5 AC-5 "drag-drop whitelist").
# ---------------------------------------------------------------------------
_ALLOWED_EXT = {"m4a", "wav", "mp3", "webm", "aac", "ogg", "flac", "m4r"}

# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}
_SYSTEM_INFO_CACHE_TTL_SEC = 5.0
_SYSTEM_INFO_CACHE: dict[str, float | dict | None] = {
    "expires_at": 0.0,
    "value": None,
}
_SYSTEM_INFO_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# Per-session live-recording state (Step 6a/6b/6c)
# ---------------------------------------------------------------------------
# VAD detector instance, per session_id.
_VAD_DETECTORS: dict[str, vad_realtime.ChunkBoundaryDetector] = {}
# asyncio.Lock per session to serialize concurrent chunk VAD calls.
_VAD_LOCKS: dict[str, asyncio.Lock] = {}
# Boundary-chunk seq counter per session (for recording_chunks.insert_chunk).
_CHUNK_SEQ: dict[str, int] = {}

# ---------------------------------------------------------------------------
# Per-session SSE queues for transcript-stream channel
# ---------------------------------------------------------------------------
_TRANSCRIPT_SSE_QUEUES: dict[str, asyncio.Queue[bytes | None]] = {}


def _register_transcript_queue(session_id: str) -> "asyncio.Queue[bytes | None]":
    """Create and register a new SSE queue for the given session."""
    q: asyncio.Queue[bytes | None] = asyncio.Queue()
    _TRANSCRIPT_SSE_QUEUES[session_id] = q
    return q


def _remove_transcript_queue(session_id: str) -> None:
    """Remove the SSE queue for the given session (idempotent)."""
    _TRANSCRIPT_SSE_QUEUES.pop(session_id, None)


async def _push_chunk_transcribed(
    session_id: str, seq: int, start_ms: int, end_ms: int, text: str
) -> None:
    """Push a chunk_transcribed SSE event to the per-session queue, if registered."""
    q = _TRANSCRIPT_SSE_QUEUES.get(session_id)
    if q is None:
        return
    await q.put(_sse_event("chunk_transcribed", {
        "seq": seq,
        "startMs": start_ms,
        "endMs": end_ms,
        "text": text,
    }))


# Register the callback with transcribe_queue so it can push events without
# creating a circular import.
transcribe_queue.register_chunk_transcribed_callback(_push_chunk_transcribed)


def _any_model_ready() -> bool:
    """Return True when at least one model is downloaded and ready."""
    catalog = models_catalog.catalog_for_current_os()
    return any(models_catalog.model_ready(e["id"]) for e in catalog)


def _active_recording_exists() -> bool:
    """True if any live recording session is currently active."""
    return recordings_mod.get_active_session_count() > 0


def _ffmpeg_extract_range(
    session_webm: str, start_ms: int, end_ms: int, out_path: str
) -> None:
    """Extract [start_ms, end_ms) from session_webm into out_path via ffmpeg."""
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    cmd = [
        ffmpeg,
        "-y",
        "-loglevel", "error",
        "-ss", f"{start_ms / 1000:.3f}",
        "-to", f"{end_ms / 1000:.3f}",
        "-i", session_webm,
        "-c", "copy",
        out_path,
    ]
    import subprocess
    subprocess.run(cmd, check=True)


def _cleanup_session_live_state(session_id: str) -> None:
    """Remove VAD detector, lock, and chunk-seq counter for session_id."""
    _VAD_DETECTORS.pop(session_id, None)
    _VAD_LOCKS.pop(session_id, None)
    _CHUNK_SEQ.pop(session_id, None)


def _sse_event(name: str, payload: dict) -> bytes:
    """Format an SSE event: `event:` + `data:` + blank line."""
    body = json.dumps(payload, ensure_ascii=False)
    return f"event: {name}\ndata: {body}\n\n".encode("utf-8")


def _sse_keepalive() -> bytes:
    return b": keep-alive\n\n"


async def _merge_with_keepalive(
    queue: asyncio.Queue[bytes | None],
    keepalive_interval: float = 1.0,
) -> AsyncIterator[bytes]:
    """Yield items from `queue` and emit a `: keep-alive\n\n` comment whenever
    the producer is silent for `keepalive_interval` seconds. A sentinel `None`
    terminates the stream."""
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=keepalive_interval)
        except asyncio.TimeoutError:
            yield _sse_keepalive()
            continue
        if item is None:
            return
        yield item


async def _write_upload_file(
    upload: UploadFile,
    destination: Path,
    *,
    chunk_size: int = 1024 * 1024,
) -> None:
    await upload.seek(0)
    await asyncio.to_thread(
        _copy_fileobj_to_path,
        upload.file,
        destination,
        chunk_size=chunk_size,
    )


def _copy_fileobj_to_path(source, destination: Path, *, chunk_size: int = 1024 * 1024) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with destination.open("wb") as output:
        while True:
            chunk = source.read(chunk_size)
            if not chunk:
                return written
            output.write(chunk)
            written += len(chunk)


def _append_recording_chunk_sync(
    session_id: str,
    stream,
    seq: int,
    *,
    needs_note: bool,
) -> dict:
    if needs_note:
        with db_mod.open_db() as conn:
            return recordings_mod.append_chunk_stream(conn, session_id, stream, seq)
    return recordings_mod.append_chunk_stream(None, session_id, stream, seq)


def invalidate_system_info_cache() -> None:
    with _SYSTEM_INFO_LOCK:
        _SYSTEM_INFO_CACHE["expires_at"] = 0.0
        _SYSTEM_INFO_CACHE["value"] = None


def _build_system_info() -> dict:
    catalog = models_catalog.catalog_for_current_os()
    ready = any(models_catalog.model_ready(entry["id"]) for entry in catalog)
    ai_avail = ai_detect.availability()
    return {
        "os": models_catalog.current_os(),
        "arch": models_catalog.current_arch(),
        "modelCatalog": catalog,
        "modelReady": ready,
        "aiAvailable": ai_avail,
        "ffmpegAvailable": shutil.which("ffmpeg") is not None,
    }


def get_cached_system_info() -> dict:
    now = time.monotonic()
    cached = _SYSTEM_INFO_CACHE.get("value")
    expires_at = _SYSTEM_INFO_CACHE.get("expires_at", 0.0)
    if isinstance(cached, dict) and isinstance(expires_at, float) and expires_at > now:
        return cached
    with _SYSTEM_INFO_LOCK:
        cached = _SYSTEM_INFO_CACHE.get("value")
        expires_at = _SYSTEM_INFO_CACHE.get("expires_at", 0.0)
        if isinstance(cached, dict) and isinstance(expires_at, float) and expires_at > now:
            return cached
        payload = _build_system_info()
        _SYSTEM_INFO_CACHE["value"] = payload
        _SYSTEM_INFO_CACHE["expires_at"] = now + _SYSTEM_INFO_CACHE_TTL_SEC
        return payload


def _get_note_or_404(conn, note_id: str) -> dict:
    note = db_mod.get_note(conn, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")
    return note


# ---------------------------------------------------------------------------
# Model download helpers
# ---------------------------------------------------------------------------


def _dir_size(path: Path) -> int:
    """Recursively sum file sizes under path. Returns 0 on any OSError."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _hf_cache_dir_for(model_id: str) -> Path:
    """Return the HF hub cache blobs directory for model_id."""
    try:
        from huggingface_hub.constants import HF_HUB_CACHE  # type: ignore
        cache_name = f"models--{model_id.replace('/', '--')}"
        return Path(HF_HUB_CACHE) / cache_name / "blobs"
    except Exception:
        return Path("/nonexistent")


# ---------------------------------------------------------------------------
# Pydantic request bodies
# ---------------------------------------------------------------------------


class CreateNoteJSON(BaseModel):
    title: str | None = None
    audioPath: str | None = None


class PatchNoteJSON(BaseModel):
    title: str | None = None
    status: str | None = None


class DownloadModelJSON(BaseModel):
    modelId: str


class SummarizeJSON(BaseModel):
    ai: Literal["auto", "claude", "codex", "none"] | None = None
    prompt_id: int | None = None  # F5: 선택된 프리셋 id


class PromptCreateJSON(BaseModel):
    name: str
    template: str


class PromptUpdateJSON(BaseModel):
    name: str | None = None
    template: str | None = None


class PromptReorderJSON(BaseModel):
    order: list[int]


class SettingsJSON(BaseModel):
    preferredAi: Literal["auto", "claude", "codex", "none"] | None = None


class RecordingStartJSON(BaseModel):
    title: str | None = None


class RecordingFinalizeJSON(BaseModel):
    title: str | None = None
    durationSec: float | None = None


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(title="locally", version="0.1.0")

    # Request-logging middleware (JSONL logger configured in app/__init__.py).
    @app.middleware("http")
    async def _access_log(request: Request, call_next):  # type: ignore[no-redef]
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.error(
                "http_request_error",
                {
                    "method": request.method,
                    "path": request.url.path,
                    "elapsed_ms": elapsed_ms,
                },
            )
            raise
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "http_request",
            {
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "elapsed_ms": elapsed_ms,
            },
        )
        return response

    # ------------------------------------------------------------------
    # System info
    # ------------------------------------------------------------------
    @app.get("/api/system/info")
    def get_system_info() -> dict:
        return get_cached_system_info()

    # ------------------------------------------------------------------
    # Settings (preferred AI CLI)
    # ------------------------------------------------------------------
    def _read_settings() -> dict:
        p = paths.settings_json_path()
        if not p.exists():
            return {"preferredAi": "auto"}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"preferredAi": "auto"}

    def _write_settings(data: dict) -> None:
        p = paths.settings_json_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    @app.get("/api/settings")
    def get_settings() -> dict:
        return _read_settings()

    @app.patch("/api/settings")
    def patch_settings(body: SettingsJSON) -> dict:
        current = _read_settings()
        if body.preferredAi is not None:
            current["preferredAi"] = body.preferredAi
        _write_settings(current)
        return current

    # ------------------------------------------------------------------
    # Models: download (SSE)
    # ------------------------------------------------------------------
    @app.post("/api/models/download")
    async def download_model(body: DownloadModelJSON) -> StreamingResponse:
        model_id = body.modelId

        async def producer(queue: asyncio.Queue[bytes | None]) -> None:
            loop = asyncio.get_running_loop()

            total_bytes = models_catalog.size_mb_for(model_id) * 1024 * 1024
            stop_event = threading.Event()
            thread_started = False

            dest_dir = models_catalog.model_dir_for(model_id)
            incomplete = models_catalog.incomplete_dir_for(model_id)
            cache_dir = _hf_cache_dir_for(model_id)

            def _poll() -> None:
                last_bytes = 0
                last_time = time.monotonic()
                while not stop_event.wait(1.0):
                    current = max(_dir_size(dest_dir), _dir_size(cache_dir))
                    now = time.monotonic()
                    dt = now - last_time
                    delta = max(0, current - last_bytes)
                    speed_mbs = (delta / dt / 1_000_000) if dt > 0.5 else 0.0
                    pct = min(current / total_bytes, 0.99) if total_bytes > 0 else 0.0
                    eta = int((total_bytes - current) / (speed_mbs * 1_000_000)) if speed_mbs > 0.1 else None
                    payload = {
                        "percent": round(pct, 4),
                        "downloaded_mb": int(current // (1024 * 1024)),
                        "total_mb": int(total_bytes // (1024 * 1024)),
                        "speed_mbps": round(speed_mbs, 2),
                        "eta_seconds": eta,
                    }
                    asyncio.run_coroutine_threadsafe(
                        queue.put(_sse_event("progress", payload)), loop
                    )
                    last_bytes = current
                    last_time = now

            poll_thread = threading.Thread(target=_poll, daemon=True)

            try:
                from huggingface_hub import snapshot_download  # type: ignore

                incomplete.mkdir(parents=True, exist_ok=True)
                poll_thread.start()
                thread_started = True

                def _blocking_download() -> str:
                    return snapshot_download(
                        repo_id=model_id,
                        local_dir=str(dest_dir),
                        local_dir_use_symlinks=False,
                        resume_download=True,
                    )

                path = await asyncio.to_thread(_blocking_download)

                # Stop polling and emit final 100%.
                stop_event.set()
                poll_thread.join(timeout=2.0)
                await queue.put(_sse_event("progress", {
                    "percent": 1.0,
                    "downloaded_mb": int(total_bytes // (1024 * 1024)),
                    "total_mb": int(total_bytes // (1024 * 1024)),
                    "speed_mbps": 0.0,
                    "eta_seconds": 0,
                }))

                # Cleanup sentinel.
                try:
                    if incomplete.exists():
                        for p in incomplete.iterdir():
                            try:
                                p.unlink()
                            except OSError:
                                pass
                        incomplete.rmdir()
                except OSError:
                    pass
                invalidate_system_info_cache()

                await queue.put(
                    _sse_event("complete", {"modelId": model_id, "path": path})
                )
            except Exception as exc:  # noqa: BLE001 — surface to client
                logger.error("models_download_error", {"error": str(exc)})
                await queue.put(
                    _sse_event(
                        "error", {"message": str(exc), "canRetry": True}
                    )
                )
            finally:
                stop_event.set()
                if thread_started:
                    poll_thread.join(timeout=2.0)
                await queue.put(None)

        return _sse_stream(producer)

    # ------------------------------------------------------------------
    # Notes CRUD
    # ------------------------------------------------------------------
    @app.get("/api/notes")
    def list_notes() -> list[dict]:
        with db_mod.open_db() as conn:
            return db_mod.list_notes(conn)

    @app.post("/api/notes", status_code=status.HTTP_201_CREATED)
    async def create_note_endpoint(
        request: Request,
        file: UploadFile | None = File(default=None),
        title: str | None = Form(default=None),
    ) -> JSONResponse:
        """Multipart OR JSON.

        Title precedence (plan §4.2 / B3): multipart `title` > JSON `title` >
        literal `"untitled"`.
        """
        ctype = request.headers.get("content-type", "")
        json_title: str | None = None
        json_audio_path: str | None = None

        if ctype.startswith("application/json"):
            try:
                raw = await request.body()
                if raw:
                    payload = json.loads(raw.decode("utf-8"))
                    if isinstance(payload, dict):
                        json_title = payload.get("title") if isinstance(
                            payload.get("title"), str
                        ) else None
                        json_audio_path = payload.get("audioPath") if isinstance(
                            payload.get("audioPath"), str
                        ) else None
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

        effective_title = title if title else json_title

        audio_path: str | None = json_audio_path
        if file is not None and file.filename:
            ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
            if ext not in _ALLOWED_EXT:
                return JSONResponse(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    content={"error": "지원하지 않는 파일 형식", "ext": ext},
                )
            dest_dir = paths.audio_dir()
            dest_name = f"{uuid.uuid4()}.{ext}"
            dest = dest_dir / dest_name
            await _write_upload_file(file, dest)
            audio_path = str(dest)

        with db_mod.open_db() as conn:
            note = db_mod.create_note(
                conn, title=effective_title, audio_path=audio_path
            )
        return JSONResponse(status_code=status.HTTP_201_CREATED, content=note)

    @app.get("/api/notes/{note_id}")
    def get_note(note_id: str) -> dict:
        with db_mod.open_db() as conn:
            note = _get_note_or_404(conn, note_id)
        return note

    @app.patch("/api/notes/{note_id}")
    def patch_note(note_id: str, body: PatchNoteJSON) -> dict:
        fields = body.model_dump(exclude_none=True)
        with db_mod.open_db() as conn:
            note = db_mod.update_note(conn, note_id, **fields)
        if note is None:
            raise HTTPException(status_code=404, detail="note not found")
        return note

    @app.delete(
        "/api/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT
    )
    def delete_note(note_id: str, deleteAudio: bool = False) -> Response:
        with db_mod.open_db() as conn:
            db_mod.delete_note(conn, note_id, delete_audio=deleteAudio)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Transcribe (SSE)
    # ------------------------------------------------------------------
    @app.post("/api/notes/{note_id}/transcribe")
    async def transcribe_note(note_id: str) -> StreamingResponse:
        with db_mod.open_db() as conn:
            note = _get_note_or_404(conn, note_id)
        if not note.get("audioPath"):
            raise HTTPException(status_code=400, detail="note has no audioPath")

        audio_path = note["audioPath"]
        title = note["title"]
        await server_jobs.register(note_id, "transcribe")

        async def producer(queue: asyncio.Queue[bytes | None]) -> None:
            loop = asyncio.get_running_loop()
            handle = await server_jobs.get(note_id)

            def _progress_cb(payload: dict) -> None:
                if handle is not None and handle.cancel_event.is_set():
                    return
                evt = _sse_event("progress", payload)
                asyncio.run_coroutine_threadsafe(queue.put(evt), loop)

            def _blocking_transcribe() -> tuple[str, list[dict]]:
                catalog = models_catalog.catalog_for_current_os()
                ready = [e for e in catalog if models_catalog.model_ready(e["id"])]
                model_dir = (
                    str(models_catalog.model_dir_for(ready[0]["id"])) if ready else None
                )
                glossary_terms = glossary_mod.load()
                glossary_prompt: str | None = (
                    ", ".join(glossary_terms) if glossary_terms else None
                )
                return transcribe_mod.run(
                    audio_path,
                    model_dir=model_dir,
                    prompt=glossary_prompt,
                    progress_cb=_progress_cb,
                )

            try:
                with db_mod.open_db() as conn:
                    db_mod.update_note(conn, note_id, status="transcribing")

                text, segments = await asyncio.to_thread(_blocking_transcribe)

                if handle is not None and handle.cancel_event.is_set():
                    await queue.put(
                        _sse_event(
                            "error",
                            {"message": "cancelled", "canRetry": False},
                        )
                    )
                    return

                if not segments:
                    # AC6(B): 전사 결과 세그먼트가 0개면 transcription_failed로 기록.
                    with db_mod.open_db() as conn:
                        db_mod.update_note(
                            conn, note_id, status="transcription_failed"
                        )
                    await queue.put(
                        _sse_event(
                            "error",
                            {"message": "no segments", "canRetry": False},
                        )
                    )
                    await server_jobs.set_status(note_id, "error")
                    return

                # Write transcript file.
                basename = paths.audio_basename(title, datetime.now(), note_id=note_id)
                out_path = paths.transcripts_dir() / f"{basename}.md"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                content = transcript_format_mod.format_transcript_markdown(segments)
                out_path.write_text(content, encoding="utf-8")

                with db_mod.open_db() as conn:
                    db_mod.update_note(
                        conn,
                        note_id,
                        status="transcribed",
                        transcript_path=str(out_path),
                    )

                await queue.put(
                    _sse_event(
                        "complete",
                        {
                            "status": "completed",
                            "transcriptPath": str(out_path),
                        },
                    )
                )
                await server_jobs.set_status(note_id, "completed")
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "transcribe_error", {"note_id": note_id, "error": str(exc)}
                )
                await queue.put(
                    _sse_event(
                        "error", {"message": str(exc), "canRetry": True}
                    )
                )
                await server_jobs.set_status(note_id, "error")
                with db_mod.open_db() as conn:
                    # AC6(C): 전사 중 예외도 transcription_failed로 통일.
                    db_mod.update_note(conn, note_id, status="transcription_failed")
            finally:
                await queue.put(None)

        return _sse_stream(producer)

    # ------------------------------------------------------------------
    # Summarize (SSE)
    # ------------------------------------------------------------------
    @app.post("/api/notes/{note_id}/summarize")
    async def summarize_note(
        note_id: str,
        request: Request,
        body: SummarizeJSON | None = Body(default=None),
    ) -> StreamingResponse:
        requested_ai = (body.ai if body is not None else None) or "auto"

        with db_mod.open_db() as conn:
            note = _get_note_or_404(conn, note_id)
        if note.get("status") == "summarizing":
            raise HTTPException(status_code=409, detail="이미 요약 중인 노트입니다")
        if not note.get("transcriptPath"):
            raise HTTPException(
                status_code=400, detail="note has no transcriptPath"
            )

        # F5: prompt_id로 프리셋 선택. 없거나 유효하지 않으면 첫 항목 fallback.
        prompts_mod.ensure_seed()  # G2 방어 — 요약 진입 시점 시드 보장
        presets = prompts_mod.load()
        selected_template: str | None = None
        prompt_id = body.prompt_id if body is not None else None
        if prompt_id is not None:
            selected = next((p for p in presets if p["id"] == prompt_id), None)
            if selected is not None:
                selected_template = selected["template"]
        if selected_template is None and presets:
            selected_template = presets[0]["template"]

        transcript_text = Path(note["transcriptPath"]).read_text(encoding="utf-8")
        title = note["title"]
        glossary_terms = glossary_mod.load()
        prompt = summarize_mod.build_prompt(
            transcript=transcript_text,
            glossary_terms=glossary_terms,
            title=title,
            template=selected_template,
        )

        # Resolve AI CLI.
        ai_info: dict | None
        if requested_ai == "none":
            ai_info = None
        elif requested_ai in ("claude", "codex"):
            path = shutil.which(requested_ai)
            ai_info = {"name": requested_ai, "path": path} if path else None
        else:
            ai_info = ai_detect.detect_ai_cli()

        handle = await server_jobs.register(note_id, "summarize")

        async def producer(queue: asyncio.Queue[bytes | None]) -> None:
            # Watch for client disconnect and propagate to cancel_event.
            async def _disconnect_watcher() -> None:
                while not handle.cancel_event.is_set():
                    if await request.is_disconnected():
                        handle.cancel_event.set()
                        return
                    await asyncio.sleep(1.0)

            disconnect_task = asyncio.create_task(_disconnect_watcher())
            try:
                await _producer_body(queue)
            finally:
                disconnect_task.cancel()

        async def _producer_body(queue: asyncio.Queue[bytes | None]) -> None:
            # No AI path → immediately emit prompt_ready + write prompt.md.
            if ai_info is None:
                copy_text = prompt + "\n\n---\n전사:\n" + transcript_text
                await queue.put(
                    _sse_event(
                        "prompt_ready",
                        {
                            "prompt": prompt,
                            "transcript": transcript_text,
                            "copyText": copy_text,
                        },
                    )
                )
                try:
                    basename = paths.audio_basename(title, datetime.now(), note_id=note_id)
                    summarize_mod.write_outputs(
                        note_id=note_id,
                        slug=basename,
                        summary_md=None,
                        prompt_md=prompt,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "summarize_prompt_write_error",
                        {"note_id": note_id, "error": str(exc)},
                    )
                await server_jobs.set_status(note_id, "completed")
                await queue.put(None)
                return

            async def _on_heartbeat(elapsed_s: int) -> None:
                await queue.put(_sse_event("ai_waiting", {"elapsed_s": elapsed_s}))

            def _on_process_started(proc: asyncio.subprocess.Process) -> None:
                asyncio.create_task(server_jobs.attach_subprocess(note_id, proc))

            try:
                with db_mod.open_db() as conn:
                    db_mod.update_note(conn, note_id, status="summarizing")

                stdout, _proc = await summarize_mod.run_ai(
                    ai_name=ai_info["name"],  # type: ignore[arg-type]
                    ai_path=ai_info["path"],
                    prompt=prompt,
                    on_heartbeat=_on_heartbeat,
                    cancel_event=handle.cancel_event,
                    on_process_started=_on_process_started,
                )

                basename = paths.audio_basename(title, datetime.now(), note_id=note_id)
                out = summarize_mod.write_outputs(
                    note_id=note_id,
                    slug=basename,
                    summary_md=stdout,
                    prompt_md=prompt,
                )
                summary_path = out["summary_path"]

                with db_mod.open_db() as conn:
                    db_mod.update_note(
                        conn,
                        note_id,
                        status="completed",
                        summary_path=summary_path,
                    )

                await queue.put(
                    _sse_event("complete", {"summaryPath": summary_path})
                )
                await server_jobs.set_status(note_id, "completed")
            except asyncio.CancelledError:
                await queue.put(
                    _sse_event(
                        "error", {"message": "cancelled", "canRetry": False}
                    )
                )
                await server_jobs.set_status(note_id, "cancelled")
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "summarize_error", {"note_id": note_id, "error": str(exc)}
                )
                await queue.put(
                    _sse_event(
                        "error", {"message": str(exc), "canRetry": True}
                    )
                )
                await server_jobs.set_status(note_id, "error")
                with db_mod.open_db() as conn:
                    db_mod.update_note(conn, note_id, status="error")
            finally:
                await queue.put(None)

        return _sse_stream(producer)

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------
    @app.post(
        "/api/notes/{note_id}/cancel",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def cancel_job(note_id: str) -> Response:
        await server_jobs.cancel(note_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Transcript / summary fetch
    # ------------------------------------------------------------------
    @app.get("/api/notes/{note_id}/transcript")
    def get_transcript(note_id: str) -> dict:
        with db_mod.open_db() as conn:
            note = _get_note_or_404(conn, note_id)
        if not note.get("transcriptPath"):
            raise HTTPException(status_code=404, detail="transcript not found")
        content = Path(note["transcriptPath"]).read_text(encoding="utf-8")
        return {"content": content, "segments": []}

    @app.get("/api/notes/{note_id}/summary")
    def get_summary(note_id: str) -> dict:
        with db_mod.open_db() as conn:
            note = _get_note_or_404(conn, note_id)
        if not note.get("summaryPath"):
            raise HTTPException(status_code=404, detail="summary not found")
        content = Path(note["summaryPath"]).read_text(encoding="utf-8")
        return {"content": content}

    @app.get("/api/notes/{note_id}/prompt")
    def get_note_prompt(note_id: str, prompt_id: int | None = None) -> dict:
        prompts_mod.ensure_seed()  # 방어
        with db_mod.open_db() as conn:
            note = _get_note_or_404(conn, note_id)
        if not note.get("transcriptPath"):
            raise HTTPException(status_code=404, detail="transcript not found")

        presets = prompts_mod.load()
        selected_template: str | None = None
        if prompt_id is not None:
            selected = next((p for p in presets if p["id"] == prompt_id), None)
            if selected is not None:
                selected_template = selected["template"]
        if selected_template is None and presets:
            selected_template = presets[0]["template"]

        transcript_text = Path(note["transcriptPath"]).read_text(encoding="utf-8")
        title = note["title"]
        glossary_terms = glossary_mod.load()
        prompt = summarize_mod.build_prompt(
            transcript=transcript_text,
            glossary_terms=glossary_terms,
            title=title,
            template=selected_template,
        )
        return {"prompt": prompt}

    # ------------------------------------------------------------------
    # Events proxy (plan extension): re-emits the most recent cancel signal.
    # Minimal implementation: confirm there is an active job and stream
    # keep-alives until it completes or is cancelled.
    # ------------------------------------------------------------------
    @app.get("/api/notes/{note_id}/events")
    async def note_events(note_id: str, request: Request) -> StreamingResponse:
        async def producer(queue: asyncio.Queue[bytes | None]) -> None:
            while True:
                handle = await server_jobs.get(note_id)
                if handle is None or handle.status != "running":
                    break
                if await request.is_disconnected():
                    break
                await asyncio.sleep(1.0)
            final_status = "completed"
            h = await server_jobs.get(note_id)
            if h is not None:
                final_status = h.status
            await queue.put(
                _sse_event("complete", {"status": final_status})
            )
            await queue.put(None)

        return _sse_stream(producer)

    # ------------------------------------------------------------------
    # Glossary
    # ------------------------------------------------------------------
    @app.get("/api/glossary")
    def get_glossary() -> list[str]:
        return glossary_mod.load()

    @app.put("/api/glossary")
    async def put_glossary(request: Request) -> Response:
        raw = await request.body()
        try:
            terms = json.loads(raw.decode("utf-8")) if raw else []
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="invalid JSON body")
        if not isinstance(terms, list):
            raise HTTPException(status_code=400, detail="expected array of strings")
        terms = [str(t) for t in terms]
        glossary_mod.save(terms)
        return Response(
            status_code=200,
            content=b"",
            headers={"Content-Length": "0"},
        )

    # ------------------------------------------------------------------
    # Prompt presets (F1~F4, F6)
    # ------------------------------------------------------------------
    # NOTE: 라우트 등록 순서 중요 — `/api/prompts/order`를 `/api/prompts/{prompt_id}`
    # 보다 먼저 등록해야 'order'가 int 파싱에 실패하지 않는다.

    @app.get("/api/prompts")
    def list_prompts() -> list[dict]:
        # A1/A3/G2 보장 — 진입 시 시드.
        prompts_mod.ensure_seed()
        return prompts_mod.load()

    @app.get("/api/prompts/{prompt_id}")
    def get_prompt(prompt_id: int) -> dict:
        prompts_mod.ensure_seed()
        presets = prompts_mod.load()
        preset = next((p for p in presets if p["id"] == prompt_id), None)
        if preset is None:
            raise HTTPException(status_code=404, detail="prompt not found")
        return preset

    @app.post("/api/prompts", status_code=201)
    def create_prompt(body: PromptCreateJSON) -> dict:
        presets = prompts_mod.load()
        new_id = prompts_mod.next_id(presets)
        new_preset = {
            "id": new_id,
            "name": body.name,
            "template": body.template,
        }
        presets.append(new_preset)  # 배열 끝에 append (F2)
        prompts_mod.save(presets)
        return new_preset

    @app.put("/api/prompts/order")
    def reorder_prompts(body: PromptReorderJSON) -> dict:
        presets = prompts_mod.load()
        id_to_preset = {p["id"]: p for p in presets}
        requested_ids = set(body.order)
        # F6: order 배열에서 파일에 없는 id는 무시 (실용적).
        reordered = [
            id_to_preset[i] for i in body.order if i in id_to_preset
        ]
        reordered.extend(p for p in presets if p["id"] not in requested_ids)
        prompts_mod.save(reordered)
        return {"ok": True}

    @app.put("/api/prompts/{prompt_id}")
    def update_prompt(prompt_id: int, body: PromptUpdateJSON) -> dict:
        presets = prompts_mod.load()
        target = next((p for p in presets if p["id"] == prompt_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="prompt not found")
        if body.name is not None:
            target["name"] = body.name
        if body.template is not None:
            target["template"] = body.template
        prompts_mod.save(presets)
        return target

    @app.delete(
        "/api/prompts/{prompt_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_prompt(prompt_id: int) -> Response:
        presets = prompts_mod.load()
        new_presets = [p for p in presets if p["id"] != prompt_id]
        if len(new_presets) == len(presets):
            raise HTTPException(status_code=404, detail="prompt not found")
        prompts_mod.save(new_presets)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Recordings
    # ------------------------------------------------------------------
    @app.post("/api/recordings", status_code=status.HTTP_201_CREATED)
    async def create_recording(body: RecordingStartJSON | None = None) -> JSONResponse:
        # 1. Model presence check — return 503 when no model is installed.
        if not _any_model_ready():
            return JSONResponse(
                status_code=503,
                content={"error": "model_not_installed"},
            )

        # 2. Concurrent-recording guard + session creation are atomic.
        session = recordings_mod.try_start_session(
            title=(body.title if body is not None else None)
        )
        if session is None:
            return JSONResponse(
                status_code=409,
                content={"error": "concurrent_recording"},
            )
        session_id = session["id"]

        # 4. Determine ready model_dir.
        catalog = models_catalog.catalog_for_current_os()
        ready = [e for e in catalog if models_catalog.model_ready(e["id"])]
        model_dir = str(models_catalog.model_dir_for(ready[0]["id"])) if ready else None

        # 5. Load glossary for chunk transcription (live recording path only).
        glossary_terms = glossary_mod.load()
        glossary_prompt: str | None = ", ".join(glossary_terms) if glossary_terms else None

        # 6. Create per-session transcription queue and start the worker.
        queue = await transcribe_queue.create_session_queue(
            session_id,
            session_id,  # note_id not yet known; will be set after seq=0 chunk
            model_dir,
            glossary_prompt=glossary_prompt,
        )
        await queue.start()

        # 7. Initialise per-session live-recording state.
        _VAD_DETECTORS[session_id] = vad_realtime.ChunkBoundaryDetector()
        _VAD_LOCKS[session_id] = asyncio.Lock()
        _CHUNK_SEQ[session_id] = 0

        return JSONResponse(
            status_code=status.HTTP_201_CREATED, content=session
        )

    @app.post("/api/recordings/{session_id}/chunk")
    async def append_recording_chunk(
        session_id: str,
        chunk: UploadFile = File(...),
        seq: int = Form(...),
    ) -> dict:
        if seq < 0:
            raise HTTPException(status_code=400, detail="seq must be >= 0")
        await chunk.seek(0)
        session = recordings_mod.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="recording session not found")

        # -- Per-chunk temp file for VAD / pre-transcription (O(N) decode). --
        # Only materialise when a live VAD detector exists for this session
        # (i.e. session was created via POST /api/recordings, not directly in tests).
        detector = _VAD_DETECTORS.get(session_id)
        chunk_temp_path: Path | None = None
        keep_chunk_temp = False

        if detector is not None:
            import tempfile
            chunk_temp_path = (
                Path(tempfile.gettempdir()) / f"{session_id}_chunk_{seq}.webm"
            )
            await asyncio.to_thread(
                _copy_fileobj_to_path,
                chunk.file,
                chunk_temp_path,
            )
            await chunk.seek(0)

        try:
            try:
                result = await asyncio.to_thread(
                    _append_recording_chunk_sync,
                    session_id,
                    chunk.file,
                    int(seq),
                    needs_note=session.note_id is None,
                )
            except recordings_mod.ChunkSeqConflict as exc:
                return JSONResponse(  # type: ignore[return-value]
                    status_code=status.HTTP_409_CONFLICT,
                    content={"error": "duplicate seq", "seq": exc.seq},
                )
            except KeyError:
                raise HTTPException(status_code=404, detail="recording session not found")

            # -- VAD feed + queue push (only when live state exists). --
            if detector is not None and chunk_temp_path is not None:
                note_id = result.get("noteId")
                # Decode per-chunk temp file to PCM for VAD.
                try:
                    pcm = await asyncio.to_thread(
                        audio_io.load_pcm_16k_mono, str(chunk_temp_path)
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "vad_decode_error",
                        {"session_id": session_id, "seq": seq, "error": str(exc)},
                    )
                    pcm = None

                if pcm is not None:
                    lock = _VAD_LOCKS.get(session_id)
                    if lock is None:
                        lock = asyncio.Lock()
                        _VAD_LOCKS[session_id] = lock
                    async with lock:
                        boundaries = detector.feed(pcm)

                    if boundaries and note_id:
                        import tempfile

                        import numpy as np
                        import scipy.io.wavfile

                        sq = await transcribe_queue.get_session_queue(session_id)
                        with db_mod.open_db() as conn:
                            for start_ms, end_ms, pcm_slice in boundaries:
                                chunk_seq = _CHUNK_SEQ.get(session_id, 0)
                                chunk_id = recording_chunks.insert_chunk(
                                    conn, note_id, chunk_seq, start_ms, end_ms
                                )
                                _CHUNK_SEQ[session_id] = chunk_seq + 1
                                # Write VAD PCM slice to a per-boundary WAV temp file.
                                # This replaces the approximate webm-box audio path so that
                                # boundaries spanning multiple browser chunks are captured fully.
                                wav_filename = (
                                    Path(tempfile.gettempdir())
                                    / f"{session_id}_vad_{chunk_seq}.wav"
                                )
                                pcm_int16 = np.clip(
                                    pcm_slice * 32767, -32768, 32767
                                ).astype(np.int16)
                                scipy.io.wavfile.write(str(wav_filename), 16000, pcm_int16)
                                logger.debug(
                                    "vad_emit session=%s seq=%d boundary=(%dms, %dms) wav=%s",
                                    session_id,
                                    chunk_seq,
                                    start_ms,
                                    end_ms,
                                    wav_filename.name,
                                )
                                job = transcribe_queue.ChunkJob(
                                    chunk_id=chunk_id,
                                    note_id=note_id,
                                    seq=chunk_seq,
                                    start_ms=start_ms,
                                    end_ms=end_ms,
                                    audio_path=str(wav_filename),
                                )
                                if sq is not None:
                                    await sq.push(job)

            return result
        finally:
            if chunk_temp_path is not None and not keep_chunk_temp:
                chunk_temp_path.unlink(missing_ok=True)

    @app.post("/api/recordings/{session_id}/finalize")
    async def finalize_recording(
        session_id: str,
        request: Request,
        body: RecordingFinalizeJSON | None = None,
    ) -> StreamingResponse:
        title = body.title if body is not None else None
        duration = body.durationSec if body is not None else None

        # Validate session exists before entering SSE producer.
        session_check = recordings_mod.get_session(session_id)
        if session_check is None:
            raise HTTPException(status_code=404, detail="recording session not found")

        async def producer(queue: asyncio.Queue[bytes | None]) -> None:
            # Intentionally does NOT set cancel_event — finalize work must
            # complete regardless of client connection state. Contrast with
            # _disconnect_watcher in summarize path (line 751) which cancels.
            note_id_ref: list[str | None] = [None]

            async def _disconnect_logger() -> None:
                while True:
                    if await request.is_disconnected():
                        logger.info(
                            "finalize_client_disconnected",
                            {"session_id": session_id, "note_id": note_id_ref[0]},
                        )
                        return
                    await asyncio.sleep(1.0)

            disconnect_task = asyncio.create_task(_disconnect_logger())
            try:
                # 1. Finalize session — move webm to audio_dir, set status='finalizing'.
                try:
                    with db_mod.open_db() as conn:
                        result = recordings_mod.finalize(
                            conn,
                            session_id,
                            title=title,
                            duration_sec=duration,
                            live=True,
                        )
                except recordings_mod.ChunkGapError as exc:
                    await queue.put(
                        _sse_event(
                            "error",
                            {
                                "error": "missing chunks",
                                "missing": exc.missing,
                                "canRetry": False,
                            },
                        )
                    )
                    return
                except recordings_mod.RecordingTooShortError as exc:
                    # AC6(A): preserve h14 behavior — set transcription_failed.
                    session_obj = recordings_mod.get_session(session_id)
                    note_id_short = (
                        session_obj.note_id if session_obj is not None else None
                    )
                    if note_id_short:
                        with db_mod.open_db() as conn:
                            db_mod.update_note(
                                conn, note_id_short, status="transcription_failed"
                            )
                    await queue.put(
                        _sse_event(
                            "error",
                            {
                                "error": "recording too short",
                                "min_sec": exc.min_sec,
                                "canRetry": False,
                            },
                        )
                    )
                    return
                except KeyError:
                    await queue.put(
                        _sse_event(
                            "error",
                            {"error": "session not found", "canRetry": False},
                        )
                    )
                    return

                note_id: str = result["noteId"]
                note_id_ref[0] = note_id
                session_audio_path: str = result["audioPath"]

                # Determine model_dir (same logic as create_recording).
                catalog = models_catalog.catalog_for_current_os()
                ready_models = [
                    e for e in catalog if models_catalog.model_ready(e["id"])
                ]
                model_dir = (
                    str(models_catalog.model_dir_for(ready_models[0]["id"]))
                    if ready_models
                    else None
                )
                # Read glossary_prompt from the session queue (set at create_recording time).
                _sq_for_glossary = await transcribe_queue.get_session_queue(session_id)
                glossary_prompt: str | None = (
                    _sq_for_glossary.glossary_prompt if _sq_for_glossary is not None else None
                )

                # 2. Flush VAD detector to emit any trailing audio boundary.
                detector = _VAD_DETECTORS.get(session_id)
                if detector is not None:
                    lock = _VAD_LOCKS.get(session_id)
                    tail: tuple[int, int] | None
                    if lock is not None:
                        async with lock:
                            tail = detector.flush()
                    else:
                        tail = detector.flush()

                    if tail is not None:
                        start_ms, end_ms = tail
                        import tempfile
                        tail_path = (
                            Path(tempfile.gettempdir())
                            / f"{session_id}_tail.webm"
                        )
                        try:
                            await asyncio.to_thread(
                                _ffmpeg_extract_range,
                                session_audio_path,
                                start_ms,
                                end_ms,
                                str(tail_path),
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "finalize_tail_extract_error",
                                {"session_id": session_id, "error": str(exc)},
                            )
                            tail_path = None

                        if tail_path is not None:
                            sq = await transcribe_queue.get_session_queue(session_id)
                            with db_mod.open_db() as conn:
                                chunk_seq = _CHUNK_SEQ.get(session_id, 0)
                                chunk_id = recording_chunks.insert_chunk(
                                    conn,
                                    note_id,
                                    chunk_seq,
                                    start_ms,
                                    end_ms,
                                )
                                _CHUNK_SEQ[session_id] = chunk_seq + 1
                            job = transcribe_queue.ChunkJob(
                                chunk_id=chunk_id,
                                note_id=note_id,
                                seq=chunk_seq,
                                start_ms=start_ms,
                                end_ms=end_ms,
                                audio_path=str(tail_path),
                            )
                            if sq is not None:
                                await sq.push(job)

                # 3. Drain the queue — wait for all pre-transcription jobs.
                await queue.put(
                    _sse_event("progress", {"status": "draining", "done": 0})
                )
                sq = await transcribe_queue.get_session_queue(session_id)
                if sq is not None:
                    await sq.drain()
                await queue.put(
                    _sse_event("progress", {"status": "drained"})
                )

                # 4. Option H — re-transcribe failed ranges from session webm.
                if sq is not None and sq.failed_ranges:
                    import tempfile
                    for fr in sorted(sq.failed_ranges, key=lambda x: x["seq"]):
                        fr_path = (
                            Path(tempfile.gettempdir())
                            / f"{session_id}_retry_{fr['seq']}.webm"
                        )
                        try:
                            await asyncio.to_thread(
                                _ffmpeg_extract_range,
                                session_audio_path,
                                fr["start_ms"],
                                fr["end_ms"],
                                str(fr_path),
                            )
                            text, _ = await asyncio.to_thread(
                                transcribe_mod.run,
                                str(fr_path),
                                model_dir=model_dir,
                                prompt=glossary_prompt,
                                profile="chunk",
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "finalize_retry_error",
                                {
                                    "session_id": session_id,
                                    "seq": fr["seq"],
                                    "error": str(exc),
                                },
                            )
                            text = ""
                        finally:
                            fr_path.unlink(missing_ok=True)

                        if text:
                            with db_mod.open_db() as conn:
                                row = conn.execute(
                                    "SELECT id FROM recording_chunks "
                                    "WHERE note_id = ? AND start_ms = ? AND end_ms = ?",
                                    (note_id, fr["start_ms"], fr["end_ms"]),
                                ).fetchone()
                                if row:
                                    recording_chunks.update_chunk_status(
                                        conn, row["id"], "success", text
                                    )

                # 5. Assemble transcript from all chunks.
                with db_mod.open_db() as conn:
                    all_done = recording_chunks.all_chunks_done(conn, note_id)
                    chunks = recording_chunks.get_chunks(conn, note_id)

                if not all_done and chunks:
                    # Some chunks still failed; fall through with what we have.
                    logger.warning(
                        "finalize_chunks_not_all_done",
                        {"session_id": session_id, "note_id": note_id},
                    )

                if chunks:
                    transcript_text = "\n".join(
                        c["text"] for c in chunks if c.get("text")
                    )
                else:
                    # No pre-transcription chunks were queued (e.g. very short
                    # recording that produced no VAD boundaries); leave transcript
                    # empty — caller can trigger normal transcription.
                    transcript_text = ""

                # Write transcript file.
                with db_mod.open_db() as conn:
                    note_row = db_mod.get_note(conn, note_id)
                note_title = note_row["title"] if note_row else title
                basename = paths.audio_basename(
                    note_title, datetime.now(), note_id=note_id
                )
                out_path = paths.transcripts_dir() / f"{basename}.md"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(transcript_text, encoding="utf-8")

                with db_mod.open_db() as conn:
                    db_mod.update_note(
                        conn,
                        note_id,
                        status="transcribed",
                        transcript_path=str(out_path),
                    )

                # 6. Emit complete event.
                await queue.put(
                    _sse_event(
                        "complete",
                        {
                            "status": "completed",
                            "noteId": note_id,
                            "audioPath": session_audio_path,
                            "transcriptPath": str(out_path),
                        },
                    )
                )

            finally:
                # 7. Notify transcript-stream SSE clients before removing queues.
                _tsq = _TRANSCRIPT_SSE_QUEUES.get(session_id)
                if _tsq is not None:
                    await _tsq.put(_sse_event("stream_end", {"reason": "finalized"}))
                    await _tsq.put(None)  # sentinel to close the SSE generator
                _remove_transcript_queue(session_id)
                # 8. Remove session queue and clean up live state regardless of outcome.
                await transcribe_queue.remove_session_queue(session_id)
                _cleanup_session_live_state(session_id)
                recordings_mod.close_session(session_id)
                await queue.put(None)
                disconnect_task.cancel()
                try:
                    await disconnect_task
                except asyncio.CancelledError:
                    pass

        return _sse_stream(producer)

    # ------------------------------------------------------------------
    # Transcript stream (realtime SSE — Phase 2)
    # ------------------------------------------------------------------
    @app.get("/api/recordings/{session_id}/transcript-stream")
    async def transcript_stream(session_id: str) -> StreamingResponse:
        """SSE channel that pushes chunk_transcribed / stream_end events."""
        sse_queue = _register_transcript_queue(session_id)

        async def _producer(queue: asyncio.Queue[bytes | None]) -> None:
            try:
                async for chunk in _merge_with_keepalive(sse_queue):
                    await queue.put(chunk)
            except asyncio.CancelledError:
                _remove_transcript_queue(session_id)
                raise
            finally:
                await queue.put(None)

        return _sse_stream(_producer)

    # ------------------------------------------------------------------
    # Jobs list
    # ------------------------------------------------------------------
    @app.get("/api/jobs")
    async def get_jobs() -> list[dict]:
        return await server_jobs.list_jobs()

    # ------------------------------------------------------------------
    # Static SPA — mounted LAST so /api/* wins.
    # ------------------------------------------------------------------
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        @app.get("/{full_path:path}", include_in_schema=False)
        async def _spa_fallback(full_path: str) -> FileResponse:
            candidate = static_dir / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            # Hashed build assets must 404 when missing; otherwise the SPA
            # fallback would serve index.html (text/html) for a stale chunk
            # URL, triggering "Failed to load module script ... MIME type
            # text/html" in browsers with a cached index.html.
            if full_path.startswith("assets/"):
                raise HTTPException(status_code=404, detail="asset not found")
            return FileResponse(static_dir / "index.html")

        app.mount(
            "/", StaticFiles(directory=str(static_dir), html=True), name="static"
        )

    # AC9: 앱 기동 시 stuck recording/pending row를 transcription_failed로 정리.
    with db_mod.open_db() as conn:
        db_mod.migrate_stuck_recordings(conn)

    return app


def _sse_stream(producer) -> StreamingResponse:
    """Wrap a producer coroutine in an SSE StreamingResponse.

    `producer(queue)` must push encoded events into the queue and finally push
    a `None` sentinel.
    """
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def _run() -> None:
        try:
            await producer(queue)
        except Exception as exc:  # noqa: BLE001 — ensure stream closes
            logger.error("sse_producer_crash", {"error": str(exc)})
            await queue.put(
                _sse_event("error", {"message": str(exc), "canRetry": True})
            )
            await queue.put(None)

    asyncio.create_task(_run())
    return StreamingResponse(
        _merge_with_keepalive(queue),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


# Module-level app instance for `uvicorn app.server:app`.
app = create_app()
