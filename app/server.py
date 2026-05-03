"""FastAPI app for lonta.

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
import os
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

import numpy as np
import scipy.io.wavfile

from fastapi import (
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

import platform

from app import logger
from app import audio_io
from app import db as db_mod
from app import paths
from app import prompt as prompt_mod
from app import recording_chunks
from app import recordings as recordings_mod
from app import server_jobs
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


def _sidecar_segments_path(note_id: str, seq: int) -> Path:
    """Return the path for the per-batch segments sidecar JSON file."""
    return paths.audio_dir() / f"segments-{note_id}-{seq:03d}.json"


async def _push_chunk_transcribed(
    session_id: str,
    seq: int,
    start_ms: int,
    end_ms: int,
    text: str,
    *,
    segments: list[dict] | None = None,
) -> None:
    """Push a chunk_transcribed SSE event to the per-session queue, if registered.

    Side-effect: if segments is not None, persist them as a sidecar JSON file
    so the download endpoint can reconstruct fine-grained segment data later.
    The note_id is obtained from the active recording session (session_id == note_id
    for live recordings started via POST /api/recordings).
    """
    # Persist segments sidecar (note_id == session_id for live recordings).
    if segments is not None:
        try:
            sidecar = _sidecar_segments_path(session_id, seq)
            sidecar_payload = json.dumps(
                {"seq": seq, "segments": segments}, ensure_ascii=False
            )
            tmp = sidecar.with_suffix(".tmp")
            await asyncio.to_thread(tmp.write_text, sidecar_payload, "utf-8")
            await asyncio.to_thread(os.replace, tmp, sidecar)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "segments_sidecar_write_error",
                {"session_id": session_id, "seq": seq, "error": str(exc)},
            )

    q = _TRANSCRIPT_SSE_QUEUES.get(session_id)
    if q is None:
        return
    payload: dict = {
        "seq": seq,
        "startMs": start_ms,
        "endMs": end_ms,
        "text": text,
    }
    if segments is not None:
        payload["segments"] = segments
    await q.put(_sse_event("chunk_transcribed", payload))


# Register the callbacks with transcribe_queue so it can push events without
# creating a circular import.
transcribe_queue.register_chunk_transcribed_callback(_push_chunk_transcribed)

# SSE event name for groq errors (rate_limit, server_error, etc.)
_GROQ_ERROR_SSE_FIELD = "groq_error"


async def _push_groq_error(
    session_id: str,
    error_type: str,
    details: dict,
) -> None:
    """Push a groq_error SSE event to the per-session queue, if registered.

    error_type is one of: "rate_limit", "server_error", "api_key_missing",
    "client_error", "concat_error", "unexpected_error".
    ("network_failed_max_retries" is NOT pushed per spec C2 — silent.)
    """
    q = _TRANSCRIPT_SSE_QUEUES.get(session_id)
    if q is None:
        return
    await q.put(_sse_event(_GROQ_ERROR_SSE_FIELD, {
        "errorType": error_type,
        "details": details,
    }))


transcribe_queue.register_groq_error_callback(_push_groq_error)


def _groq_api_key_set() -> bool:
    """Return True when GROQ_API_KEY is present in the environment."""
    import os
    return bool(os.environ.get("GROQ_API_KEY"))


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
    import os
    return {
        "os": platform.system().lower(),
        "arch": platform.machine().lower(),
        "ffmpegAvailable": shutil.which("ffmpeg") is not None,
        "groqConfigured": bool(os.environ.get("GROQ_API_KEY")),
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
# Pydantic request bodies
# ---------------------------------------------------------------------------


class PatchNoteJSON(BaseModel):
    title: str | None = None
    status: str | None = None



class RecordingStartJSON(BaseModel):
    title: str | None = None


class RecordingFinalizeJSON(BaseModel):
    title: str | None = None
    durationSec: float | None = None


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(title="lonta", version="0.1.0")

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
    # Transcript fetch
    # ------------------------------------------------------------------
    @app.get("/api/notes/{note_id}/transcript")
    def get_transcript(note_id: str) -> dict:
        with db_mod.open_db() as conn:
            note = _get_note_or_404(conn, note_id)
        if not note.get("transcriptPath"):
            raise HTTPException(status_code=404, detail="transcript not found")
        content = Path(note["transcriptPath"]).read_text(encoding="utf-8")
        return {"content": content, "segments": []}

    # ------------------------------------------------------------------
    # Transcript download (.md)
    # ------------------------------------------------------------------
    @app.get("/api/notes/{note_id}/download")
    async def download_transcript(note_id: str) -> FileResponse:
        """Generate and return a .md transcript file for download.

        Segment data is read from per-batch sidecar JSON files written during
        transcription (option B — no schema change). Failed ranges are derived
        from recording_chunks rows with status='failed'.
        """
        import glob as glob_mod
        import re as re_mod
        import unicodedata
        from datetime import timezone

        from app import markdown_writer

        def _load_note_and_chunks() -> tuple[dict | None, list[dict]]:
            with db_mod.open_db() as conn:
                note_local = db_mod.get_note(conn, note_id)
                chunks_local = (
                    recording_chunks.get_chunks(conn, note_id)
                    if note_local is not None
                    else []
                )
            return note_local, chunks_local

        note, chunks = await asyncio.to_thread(_load_note_and_chunks)
        if note is None:
            raise HTTPException(status_code=404, detail="note not found")
        if not chunks:
            raise HTTPException(status_code=404, detail="no recording chunks found")

        # 3. Collect segments from sidecar files, sorted by seq.
        _seq_re = re_mod.compile(r"-(\d+)\.json$")

        def _read_sidecars() -> list[dict]:
            pattern = str(paths.audio_dir() / f"segments-{note_id}-*.json")
            files = sorted(
                glob_mod.glob(pattern),
                key=lambda p: int(m.group(1)) if (m := _seq_re.search(p)) else 0,
            )
            collected: list[dict] = []
            for sf in files:
                try:
                    data = json.loads(Path(sf).read_text(encoding="utf-8"))
                    collected.extend(data.get("segments", []))
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning(
                        "segments_sidecar_read_error",
                        {"note_id": note_id, "file": sf, "error": str(exc)},
                    )
            return collected

        all_segments = await asyncio.to_thread(_read_sidecars)

        # 4. Build failed_ranges from chunks with status='failed'.
        failed_ranges = [
            {"start_ms": c["start_ms"], "end_ms": c["end_ms"]}
            for c in chunks
            if c.get("status") == "failed"
        ]

        # 5. Parse recorded_at from note's createdAt ISO string.
        try:
            recorded_at = datetime.fromisoformat(note["createdAt"])
        except (KeyError, ValueError):
            recorded_at = datetime.now(timezone.utc)

        note_title: str = note.get("title") or "untitled"

        # 6. Write .md to a temp file.
        def _write_md() -> Path:
            with tempfile.NamedTemporaryFile(
                suffix=".md", delete=False, mode="w", encoding="utf-8"
            ) as tmp:
                path = Path(tmp.name)
            markdown_writer.write_transcript_md(
                title=note_title,
                recorded_at=recorded_at,
                segments=all_segments,
                failed_ranges=failed_ranges,
                output_path=path,
            )
            return path

        tmp_path = await asyncio.to_thread(_write_md)

        # 7. Build a safe filename (ASCII + RFC 5987 UTF-8 fallback).
        date_str = recorded_at.strftime("%Y-%m-%d")
        # ASCII-safe: strip non-ASCII for the plain filename= token.
        ascii_title = (
            unicodedata.normalize("NFKD", note_title)
            .encode("ascii", "ignore")
            .decode("ascii")
            .strip()
            or "transcript"
        )
        ascii_filename = f"{date_str}-{ascii_title}.md"
        # Percent-encode the UTF-8 version for filename*= (RFC 5987).
        from urllib.parse import quote as _url_quote
        utf8_filename = f"{date_str}-{note_title}.md"
        encoded_filename = _url_quote(utf8_filename, safe="")
        content_disposition = (
            f"attachment; filename=\"{ascii_filename}\"; "
            f"filename*=UTF-8''{encoded_filename}"
        )

        return FileResponse(
            path=str(tmp_path),
            media_type="text/markdown",
            headers={"Content-Disposition": content_disposition},
        )

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
    # Recordings
    # ------------------------------------------------------------------
    @app.post("/api/recordings", status_code=status.HTTP_201_CREATED)
    async def create_recording(body: RecordingStartJSON | None = None) -> JSONResponse:
        # 1. groq API key check — return 503 when GROQ_API_KEY is missing.
        if not _groq_api_key_set():
            return JSONResponse(
                status_code=503,
                content={"error": "groq_api_key_missing"},
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

        # 5. Load prompt for chunk transcription (live recording path only).
        recording_prompt: str | None = prompt_mod.load()

        # 6. Create per-session transcription queue and start the worker.
        queue = await transcribe_queue.create_session_queue(
            session_id,
            session_id,  # note_id not yet known; will be set after seq=0 chunk
            prompt=recording_prompt,
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

        if detector is not None:
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
                        # Reserve sequential chunk_seq values inside the same lock
                        # so concurrent chunk POSTs cannot collide on _CHUNK_SEQ.
                        first_seq = _CHUNK_SEQ.get(session_id, 0)
                        _CHUNK_SEQ[session_id] = first_seq + len(boundaries)

                    if boundaries and note_id:
                        sq = await transcribe_queue.get_session_queue(session_id)
                        with db_mod.open_db() as conn:
                            for offset, (start_ms, end_ms, pcm_slice) in enumerate(boundaries):
                                chunk_seq = first_seq + offset
                                chunk_id = recording_chunks.insert_chunk(
                                    conn, note_id, chunk_seq, start_ms, end_ms
                                )
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
            if chunk_temp_path is not None:
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
            # complete regardless of client connection state (only logs
            # disconnect for observability).
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
                def _finalize_sync() -> dict:
                    with db_mod.open_db() as conn:
                        return recordings_mod.finalize(
                            conn,
                            session_id,
                            title=title,
                            duration_sec=duration,
                            live=True,
                        )

                try:
                    result = await asyncio.to_thread(_finalize_sync)
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
                        def _mark_failed() -> None:
                            with db_mod.open_db() as conn:
                                db_mod.update_note(
                                    conn, note_id_short, status="transcription_failed"
                                )
                        await asyncio.to_thread(_mark_failed)
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

                # 2. Flush VAD detector to drain any remaining internal buffer.
                # flush() returns the time range of buffered audio but does not
                # produce a WAV file — the VAD pipeline captures complete speech
                # segments during feed(). Any sub-threshold tail is discarded here.
                detector = _VAD_DETECTORS.get(session_id)
                if detector is not None:
                    lock = _VAD_LOCKS.get(session_id)
                    if lock is not None:
                        async with lock:
                            discarded = detector.flush()
                    else:
                        discarded = detector.flush()
                    if discarded:
                        logger.info(
                            "vad_flush_discarded",
                            {"discarded_ms_range": discarded},
                        )

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

                # 4. (Option H retry removed — retries handled by groq_client
                #     at batch level; failed_ranges recorded in .md by markdown_writer.)

                # 5. Assemble transcript + persist note row in one off-loop step.
                #    Single DB connection covers chunks fetch, note update, and
                #    transcript file write to keep the event loop unblocked.
                def _persist_transcript() -> tuple[Path, bool, bool]:
                    with db_mod.open_db() as conn:
                        all_done_local = recording_chunks.all_chunks_done(conn, note_id)
                        chunks_local = recording_chunks.get_chunks(conn, note_id)
                        note_row_local = db_mod.get_note(conn, note_id)
                        text_body = (
                            "\n".join(c["text"] for c in chunks_local if c.get("text"))
                            if chunks_local
                            else ""
                        )
                        title_local = (
                            note_row_local["title"] if note_row_local else title
                        )
                        basename_local = paths.audio_basename(
                            title_local, datetime.now(), note_id=note_id
                        )
                        out_path_local = paths.transcripts_dir() / f"{basename_local}.md"
                        out_path_local.parent.mkdir(parents=True, exist_ok=True)
                        out_path_local.write_text(text_body, encoding="utf-8")
                        db_mod.update_note(
                            conn,
                            note_id,
                            status="transcribed",
                            transcript_path=str(out_path_local),
                        )
                        return out_path_local, all_done_local, bool(chunks_local)

                out_path, all_done, has_chunks = await asyncio.to_thread(_persist_transcript)
                if not all_done and has_chunks:
                    logger.warning(
                        "finalize_chunks_not_all_done",
                        {"session_id": session_id, "note_id": note_id},
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
