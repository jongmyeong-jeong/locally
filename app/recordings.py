"""Chunked recording upload session management.

Flow:
  1. start_session(title?) → returns {id, uploadUrl}.
  2. POST chunks with (seq, blob); append_chunk stores seq→bytes mapping.
     On seq==0, create a Document row with status='recording' (N1).
     Duplicate seq → ChunkSeqConflict → HTTP 409.
  3. finalize(session_id, title?) → validates contiguous seq 0..N-1;
     missing → ChunkGapError → HTTP 400.
     Writes concatenated .webm into audio_dir(), updates Document to 'pending',
     returns {audioPath, documentId}. Total duration <1s → RecordingTooShortError.

Notes:
  - Duration <1s check requires probing the audio file (ffprobe) in practice.
    For the MVP/testable contract, callers (server.py) may supply
    duration_sec_override; otherwise we approximate by byte-count of opus
    payload (>= 10 bytes per chunk = non-empty).
"""
from __future__ import annotations

import sqlite3
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from app.db import create_document, update_document
from app.paths import audio_basename, audio_dir, locally_home


class ChunkSeqConflict(Exception):
    """Raised when a duplicate seq is appended."""

    def __init__(self, seq: int):
        super().__init__(f"duplicate seq: {seq}")
        self.seq = seq


class ChunkGapError(Exception):
    """Raised at finalize when the seq list has gaps."""

    def __init__(self, missing: list[int]):
        super().__init__(f"missing chunks: {missing}")
        self.missing = missing


class RecordingTooShortError(Exception):
    """Raised when total recording duration is under 1 second."""

    def __init__(self, min_sec: int = 1):
        super().__init__("recording too short")
        self.min_sec = min_sec


@dataclass
class RecordingSession:
    id: str
    document_id: str | None = None
    title: str | None = None
    started_at: float = field(default_factory=time.time)
    # seq → bytes; kept in memory and also flushed to disk by append_chunk.
    seen_seqs: set[int] = field(default_factory=set)
    chunk_count: int = 0
    max_seq: int = -1


_SESSIONS: dict[str, RecordingSession] = {}
_LOCK = threading.Lock()


def _session_tmp_path(session_id: str) -> Path:
    root = locally_home() / "tmp" / "recordings"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{session_id}.webm"


def start_session(title: str | None = None) -> dict:
    """Create a new recording session; return {id, uploadUrl}."""
    session_id = str(uuid.uuid4())
    with _LOCK:
        _SESSIONS[session_id] = RecordingSession(id=session_id, title=title)
    # Ensure the temp file exists but empty; simplifies later append semantics.
    _session_tmp_path(session_id).write_bytes(b"")
    return {
        "id": session_id,
        "uploadUrl": f"/api/recordings/{session_id}/chunk",
    }


def get_session(session_id: str) -> RecordingSession | None:
    with _LOCK:
        return _SESSIONS.get(session_id)


def append_chunk(
    conn: sqlite3.Connection | None,
    session_id: str,
    chunk_bytes: bytes,
    seq: int,
) -> dict:
    """Append one chunk to the session; validate seq uniqueness.

    N1: on seq==0, create a Document row with status='recording' so the
    session shows up in GET /api/documents for recovery.
    Returns {documentId, bytes_written}.
    """
    tmp_path = _session_tmp_path(session_id)
    with tmp_path.open("ab") as handle:
        handle.write(chunk_bytes)
    return _register_appended_chunk(
        conn,
        session_id,
        seq,
        bytes_written=len(chunk_bytes),
    )


def append_chunk_stream(
    conn: sqlite3.Connection | None,
    session_id: str,
    stream: BinaryIO,
    seq: int,
    *,
    chunk_size: int = 1024 * 1024,
) -> dict:
    """Persist an uploaded chunk from a file-like object without buffering it.

    Keeps the upload hot path on a streaming write path so large chunks do not
    require an additional in-memory bytes copy at the HTTP layer.
    """
    tmp_path = _session_tmp_path(session_id)
    bytes_written = 0
    with tmp_path.open("ab") as handle:
        while True:
            part = stream.read(chunk_size)
            if not part:
                break
            handle.write(part)
            bytes_written += len(part)
    return _register_appended_chunk(
        conn,
        session_id,
        seq,
        bytes_written=bytes_written,
    )


def _register_appended_chunk(
    conn: sqlite3.Connection | None,
    session_id: str,
    seq: int,
    *,
    bytes_written: int,
) -> dict:
    session_title, document_id, needs_document = _reserve_chunk(session_id, seq)
    if needs_document:
        if conn is None:
            raise RuntimeError("append_chunk requires conn when creating a document")
        basename = audio_basename(session_title, datetime.now())
        audio_path = audio_dir() / f"{basename}.webm"
        doc = create_document(
            conn,
            title=session_title,
            audio_path=str(audio_path),
            status="recording",
        )
        document_id = _attach_document_id(session_id, doc["id"])
    return {"documentId": document_id, "bytes_written": bytes_written}


def _reserve_chunk(session_id: str, seq: int) -> tuple[str | None, str | None, bool]:
    with _LOCK:
        session = _SESSIONS.get(session_id)
        if session is None:
            raise KeyError(f"unknown recording session: {session_id}")
        if seq in session.seen_seqs:
            raise ChunkSeqConflict(seq)
        session.seen_seqs.add(seq)
        session.chunk_count += 1
        if seq > session.max_seq:
            session.max_seq = seq
        return session.title, session.document_id, seq == 0 and session.document_id is None


def _attach_document_id(session_id: str, document_id: str) -> str:
    with _LOCK:
        session = _SESSIONS.get(session_id)
        if session is None:
            raise KeyError(f"unknown recording session: {session_id}")
        session.document_id = document_id
        return session.document_id


def get_active_session_count() -> int:
    """Return the number of currently active recording sessions."""
    return len(_SESSIONS)


def finalize(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    title: str | None = None,
    duration_sec: float | None = None,
    live: bool = False,
) -> dict:
    """Validate contiguous seqs, move file to audio_dir, update document.

    If any seq in [0, max(seen)) is missing → ChunkGapError.
    If no chunks seen → ChunkGapError(missing=[0]).
    If duration_sec is provided and < 1.0 → RecordingTooShortError.
    Returns {'audioPath', 'documentId'}.
    """
    with _LOCK:
        session = _SESSIONS.get(session_id)
        if session is None:
            raise KeyError(f"unknown recording session: {session_id}")

    if not session.seen_seqs:
        raise ChunkGapError(missing=[0])

    expected_count = session.max_seq + 1
    if session.chunk_count != expected_count:
        missing = _find_missing_seqs(session.seen_seqs, session.max_seq)
        raise ChunkGapError(missing=missing)

    if duration_sec is not None and duration_sec < 1.0:
        raise RecordingTooShortError(min_sec=1)

    if session.document_id is None:
        # seq=0 must have been appended to create the document, but be defensive.
        basename = audio_basename(title or session.title, datetime.now())
        dest = audio_dir() / f"{basename}.webm"
        doc = create_document(conn, title=title or session.title, audio_path=str(dest))
        session.document_id = doc["id"]
    else:
        doc = _fetch_document(conn, session.document_id)
        if doc is None:
            raise KeyError(f"document not found: {session.document_id}")
        dest = Path(doc["audioPath"]) if doc["audioPath"] else (
            audio_dir() / f"{audio_basename(title or session.title, datetime.now())}.webm"
        )

    tmp_path = _session_tmp_path(session_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Move (rename if on same FS, else copy+unlink).
    try:
        tmp_path.replace(dest)
    except OSError:
        with tmp_path.open("rb") as src, dest.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass

    # live=True → 'finalizing' (real-time pre-transcription path); default 'pending' for legacy file-upload finalize
    status = "finalizing" if live else "pending"
    update_document(
        conn,
        session.document_id,
        status=status,
        audio_path=str(dest),
    )

    with _LOCK:
        _SESSIONS.pop(session_id, None)

    return {"audioPath": str(dest), "documentId": session.document_id}


def _fetch_document(conn: sqlite3.Connection, doc_id: str) -> dict | None:
    # Tiny local wrapper; keeps the import graph flat.
    from app.db import get_document

    return get_document(conn, doc_id)


def _find_missing_seqs(seen_seqs: set[int], max_seq: int) -> list[int]:
    missing: list[int] = []
    expected = 0
    for seq in sorted(seen_seqs):
        while expected < seq:
            missing.append(expected)
            expected += 1
        expected = seq + 1
    while expected <= max_seq:
        missing.append(expected)
        expected += 1
    return missing
