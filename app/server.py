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
from app import db as db_mod
from app import glossary as glossary_mod
from app import models_catalog
from app import paths
from app import recordings as recordings_mod
from app import server_jobs
from app import summarize as summarize_mod
from app import transcribe as transcribe_mod
from app import transcript_format as transcript_format_mod

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
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                return
            output.write(chunk)


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


def _get_document_or_404(conn, doc_id: str) -> dict:
    doc = db_mod.get_document(conn, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    return doc


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


class CreateDocumentJSON(BaseModel):
    title: str | None = None
    audioPath: str | None = None


class PatchDocumentJSON(BaseModel):
    title: str | None = None
    status: str | None = None


class DownloadModelJSON(BaseModel):
    modelId: str


class SummarizeJSON(BaseModel):
    ai: Literal["auto", "claude", "codex", "none"] | None = None


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
    # Documents CRUD
    # ------------------------------------------------------------------
    @app.get("/api/documents")
    def list_documents() -> list[dict]:
        with db_mod.open_db() as conn:
            return db_mod.list_documents(conn)

    @app.post("/api/documents", status_code=status.HTTP_201_CREATED)
    async def create_document_endpoint(
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
            doc = db_mod.create_document(
                conn, title=effective_title, audio_path=audio_path
            )
        return JSONResponse(status_code=status.HTTP_201_CREATED, content=doc)

    @app.get("/api/documents/{doc_id}")
    def get_document(doc_id: str) -> dict:
        with db_mod.open_db() as conn:
            doc = _get_document_or_404(conn, doc_id)
        return doc

    @app.patch("/api/documents/{doc_id}")
    def patch_document(doc_id: str, body: PatchDocumentJSON) -> dict:
        fields = body.model_dump(exclude_none=True)
        with db_mod.open_db() as conn:
            doc = db_mod.update_document(conn, doc_id, **fields)
        if doc is None:
            raise HTTPException(status_code=404, detail="document not found")
        return doc

    @app.delete(
        "/api/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT
    )
    def delete_document(doc_id: str, deleteAudio: bool = False) -> Response:
        with db_mod.open_db() as conn:
            db_mod.delete_document(conn, doc_id, delete_audio=deleteAudio)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Transcribe (SSE)
    # ------------------------------------------------------------------
    @app.post("/api/documents/{doc_id}/transcribe")
    async def transcribe_document(doc_id: str) -> StreamingResponse:
        with db_mod.open_db() as conn:
            doc = _get_document_or_404(conn, doc_id)
        if not doc.get("audioPath"):
            raise HTTPException(status_code=400, detail="document has no audioPath")

        audio_path = doc["audioPath"]
        title = doc["title"]
        await server_jobs.register(doc_id, "transcribe")

        async def producer(queue: asyncio.Queue[bytes | None]) -> None:
            loop = asyncio.get_running_loop()
            handle = await server_jobs.get(doc_id)

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
                return transcribe_mod.run(
                    audio_path, model_dir=model_dir, progress_cb=_progress_cb
                )

            try:
                with db_mod.open_db() as conn:
                    db_mod.update_document(conn, doc_id, status="transcribing")

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
                        db_mod.update_document(
                            conn, doc_id, status="transcription_failed"
                        )
                    await queue.put(
                        _sse_event(
                            "error",
                            {"message": "no segments", "canRetry": False},
                        )
                    )
                    await server_jobs.set_status(doc_id, "error")
                    return

                # Write transcript file.
                basename = paths.audio_basename(title, datetime.now(), doc_id=doc_id)
                out_path = paths.transcripts_dir() / f"{basename}.md"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                content = transcript_format_mod.format_transcript_markdown(segments)
                out_path.write_text(content, encoding="utf-8")

                with db_mod.open_db() as conn:
                    db_mod.update_document(
                        conn,
                        doc_id,
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
                await server_jobs.set_status(doc_id, "completed")
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "transcribe_error", {"doc_id": doc_id, "error": str(exc)}
                )
                await queue.put(
                    _sse_event(
                        "error", {"message": str(exc), "canRetry": True}
                    )
                )
                await server_jobs.set_status(doc_id, "error")
                with db_mod.open_db() as conn:
                    # AC6(C): 전사 중 예외도 transcription_failed로 통일.
                    db_mod.update_document(conn, doc_id, status="transcription_failed")
            finally:
                await queue.put(None)

        return _sse_stream(producer)

    # ------------------------------------------------------------------
    # Summarize (SSE)
    # ------------------------------------------------------------------
    @app.post("/api/documents/{doc_id}/summarize")
    async def summarize_document(
        doc_id: str,
        request: Request,
        body: SummarizeJSON | None = Body(default=None),
    ) -> StreamingResponse:
        requested_ai = (body.ai if body is not None else None) or "auto"

        with db_mod.open_db() as conn:
            doc = _get_document_or_404(conn, doc_id)
        if doc.get("status") == "summarizing":
            raise HTTPException(status_code=409, detail="이미 요약 중인 문서입니다")
        if not doc.get("transcriptPath"):
            raise HTTPException(
                status_code=400, detail="document has no transcriptPath"
            )

        transcript_text = Path(doc["transcriptPath"]).read_text(encoding="utf-8")
        title = doc["title"]
        glossary_terms = glossary_mod.load()
        prompt = summarize_mod.build_prompt(
            transcript=transcript_text,
            glossary_terms=glossary_terms,
            title=title,
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

        handle = await server_jobs.register(doc_id, "summarize")

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
                    basename = paths.audio_basename(title, datetime.now(), doc_id=doc_id)
                    summarize_mod.write_outputs(
                        doc_id=doc_id,
                        slug=basename,
                        summary_md=None,
                        prompt_md=prompt,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "summarize_prompt_write_error",
                        {"doc_id": doc_id, "error": str(exc)},
                    )
                await server_jobs.set_status(doc_id, "completed")
                await queue.put(None)
                return

            async def _on_heartbeat(elapsed_s: int) -> None:
                await queue.put(_sse_event("ai_waiting", {"elapsed_s": elapsed_s}))

            def _on_process_started(proc: asyncio.subprocess.Process) -> None:
                asyncio.create_task(server_jobs.attach_subprocess(doc_id, proc))

            try:
                with db_mod.open_db() as conn:
                    db_mod.update_document(conn, doc_id, status="summarizing")

                stdout, _proc = await summarize_mod.run_ai(
                    ai_name=ai_info["name"],  # type: ignore[arg-type]
                    ai_path=ai_info["path"],
                    prompt=prompt,
                    on_heartbeat=_on_heartbeat,
                    cancel_event=handle.cancel_event,
                    on_process_started=_on_process_started,
                )

                basename = paths.audio_basename(title, datetime.now(), doc_id=doc_id)
                out = summarize_mod.write_outputs(
                    doc_id=doc_id,
                    slug=basename,
                    summary_md=stdout,
                    prompt_md=prompt,
                )
                summary_path = out["summary_path"]

                with db_mod.open_db() as conn:
                    db_mod.update_document(
                        conn,
                        doc_id,
                        status="completed",
                        summary_path=summary_path,
                    )

                await queue.put(
                    _sse_event("complete", {"summaryPath": summary_path})
                )
                await server_jobs.set_status(doc_id, "completed")
            except asyncio.CancelledError:
                await queue.put(
                    _sse_event(
                        "error", {"message": "cancelled", "canRetry": False}
                    )
                )
                await server_jobs.set_status(doc_id, "cancelled")
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "summarize_error", {"doc_id": doc_id, "error": str(exc)}
                )
                await queue.put(
                    _sse_event(
                        "error", {"message": str(exc), "canRetry": True}
                    )
                )
                await server_jobs.set_status(doc_id, "error")
                with db_mod.open_db() as conn:
                    db_mod.update_document(conn, doc_id, status="error")
            finally:
                await queue.put(None)

        return _sse_stream(producer)

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------
    @app.post(
        "/api/documents/{doc_id}/cancel",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def cancel_job(doc_id: str) -> Response:
        await server_jobs.cancel(doc_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Transcript / summary fetch
    # ------------------------------------------------------------------
    @app.get("/api/documents/{doc_id}/transcript")
    def get_transcript(doc_id: str) -> dict:
        with db_mod.open_db() as conn:
            doc = _get_document_or_404(conn, doc_id)
        if not doc.get("transcriptPath"):
            raise HTTPException(status_code=404, detail="transcript not found")
        content = Path(doc["transcriptPath"]).read_text(encoding="utf-8")
        return {"content": content, "segments": []}

    @app.get("/api/documents/{doc_id}/summary")
    def get_summary(doc_id: str) -> dict:
        with db_mod.open_db() as conn:
            doc = _get_document_or_404(conn, doc_id)
        if not doc.get("summaryPath"):
            raise HTTPException(status_code=404, detail="summary not found")
        content = Path(doc["summaryPath"]).read_text(encoding="utf-8")
        return {"content": content}

    @app.get("/api/documents/{doc_id}/prompt")
    def get_document_prompt(doc_id: str) -> dict:
        with db_mod.open_db() as conn:
            doc = _get_document_or_404(conn, doc_id)
        if not doc.get("transcriptPath"):
            raise HTTPException(status_code=404, detail="transcript not found")
        transcript_text = Path(doc["transcriptPath"]).read_text(encoding="utf-8")
        title = doc["title"]
        glossary_terms = glossary_mod.load()
        prompt = summarize_mod.build_prompt(
            transcript=transcript_text,
            glossary_terms=glossary_terms,
            title=title,
        )
        return {"prompt": prompt}

    # ------------------------------------------------------------------
    # Events proxy (plan extension): re-emits the most recent cancel signal.
    # Minimal implementation: confirm there is an active job and stream
    # keep-alives until it completes or is cancelled.
    # ------------------------------------------------------------------
    @app.get("/api/documents/{doc_id}/events")
    async def document_events(doc_id: str, request: Request) -> StreamingResponse:
        async def producer(queue: asyncio.Queue[bytes | None]) -> None:
            while True:
                handle = await server_jobs.get(doc_id)
                if handle is None or handle.status != "running":
                    break
                if await request.is_disconnected():
                    break
                await asyncio.sleep(1.0)
            final_status = "completed"
            h = await server_jobs.get(doc_id)
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
    # Recordings
    # ------------------------------------------------------------------
    @app.post("/api/recordings", status_code=status.HTTP_201_CREATED)
    def create_recording(body: RecordingStartJSON | None = None) -> JSONResponse:
        session = recordings_mod.start_session(
            title=(body.title if body is not None else None)
        )
        return JSONResponse(
            status_code=status.HTTP_201_CREATED, content=session
        )

    @app.post("/api/recordings/{session_id}/chunk")
    async def append_recording_chunk(
        session_id: str,
        chunk: UploadFile = File(...),
        seq: int = Form(...),
    ) -> dict:
        await chunk.seek(0)
        session = recordings_mod.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="recording session not found")
        try:
            if session.document_id is None:
                with db_mod.open_db() as conn:
                    result = recordings_mod.append_chunk_stream(
                        conn, session_id, chunk.file, int(seq)
                    )
            else:
                result = recordings_mod.append_chunk_stream(
                    None, session_id, chunk.file, int(seq)
                )
        except recordings_mod.ChunkSeqConflict as exc:
            return JSONResponse(  # type: ignore[return-value]
                status_code=status.HTTP_409_CONFLICT,
                content={"error": "duplicate seq", "seq": exc.seq},
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="recording session not found")
        return result

    @app.post("/api/recordings/{session_id}/finalize")
    def finalize_recording(
        session_id: str, body: RecordingFinalizeJSON | None = None
    ) -> JSONResponse:
        title = body.title if body is not None else None
        duration = body.durationSec if body is not None else None
        try:
            with db_mod.open_db() as conn:
                result = recordings_mod.finalize(
                    conn,
                    session_id,
                    title=title,
                    duration_sec=duration,
                )
        except recordings_mod.ChunkGapError as exc:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "missing chunks", "missing": exc.missing},
            )
        except recordings_mod.RecordingTooShortError as exc:
            # AC6(A): 너무 짧은 녹음(<1s)은 transcription_failed로 기록 후 400 반환.
            session = recordings_mod.get_session(session_id)
            doc_id = session.document_id if session is not None else None
            if doc_id:
                with db_mod.open_db() as conn:
                    db_mod.update_document(
                        conn, doc_id, status="transcription_failed"
                    )
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "error": "recording too short",
                    "min_sec": exc.min_sec,
                },
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="recording session not found")
        return JSONResponse(status_code=200, content=result)

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
