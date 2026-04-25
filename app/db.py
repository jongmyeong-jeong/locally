"""Stdlib sqlite3 wrapper for the `documents` table.

Schema (idempotent):
  documents(id TEXT PRIMARY KEY, title TEXT, created_at TEXT, status TEXT,
            audio_path TEXT, transcript_path TEXT, summary_path TEXT)

Status values (informational; not a CHECK constraint):
  'recording' | 'pending' | 'transcribing' | 'transcribed' | 'summarizing'
  | 'completed' | 'error' | 'transcription_failed'

create_document(title=None) → title stored as literal 'untitled' (B3).
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.paths import db_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'untitled',
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    audio_path TEXT,
    transcript_path TEXT,
    summary_path TEXT
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents(created_at DESC)",
)

_ALLOWED_UPDATE_COLUMNS = {
    "title",
    "status",
    "audio_path",
    "transcript_path",
    "summary_path",
}

# Map camelCase aliases accepted by update_document to DB columns.
_UPDATE_ALIASES = {
    "audioPath": "audio_path",
    "transcriptPath": "transcript_path",
    "summaryPath": "summary_path",
}


def open_db(path: Path | None = None) -> sqlite3.Connection:
    """Open the locally db at ~/.locally/db.sqlite (or override).

    Applies migrate() and returns a connection with Row factory.
    """
    target = path if path is not None else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Idempotent schema creation."""
    conn.execute(_SCHEMA)
    for stmt in _INDEXES:
        conn.execute(stmt)
    conn.commit()


def migrate_stuck_recordings(conn: sqlite3.Connection) -> int:
    """Convert stuck in-progress rows to transcription_failed at app startup.

    Idempotent: rows already in 'transcription_failed' are not re-touched
    (WHERE status IN ('recording','pending')). Returns affected row count.
    """
    cur = conn.execute(
        "UPDATE documents SET status = 'transcription_failed' "
        "WHERE status IN ('recording', 'pending')"
    )
    conn.commit()
    return cur.rowcount


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "title": row["title"],
        "createdAt": row["created_at"],
        "status": row["status"],
        "audioPath": row["audio_path"],
        "transcriptPath": row["transcript_path"],
        "summaryPath": row["summary_path"],
    }


def create_document(
    conn: sqlite3.Connection,
    title: str | None = None,
    audio_path: str | None = None,
    *,
    status: str = "pending",
) -> dict:
    """Insert a document row.

    Behavior (B3):
      - title is None or empty string → stored as literal 'untitled'.
      - Otherwise title is stored verbatim (no trim, no slugify).
      - status defaults to 'pending'.
    """
    doc_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    stored_title = title if title else "untitled"
    conn.execute(
        "INSERT INTO documents (id, title, created_at, status, audio_path) "
        "VALUES (?, ?, ?, ?, ?)",
        (doc_id, stored_title, created_at, status, audio_path),
    )
    conn.commit()
    return get_document(conn, doc_id)  # type: ignore[return-value]


def get_document(conn: sqlite3.Connection, doc_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    return _row_to_dict(row)


def list_documents(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM documents ORDER BY created_at DESC"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]  # type: ignore[misc]


def update_document(conn: sqlite3.Connection, doc_id: str, **fields) -> dict | None:
    sets: list[str] = []
    values: list[object] = []
    for key, value in fields.items():
        column = _UPDATE_ALIASES.get(key, key)
        if column in _ALLOWED_UPDATE_COLUMNS:
            sets.append(f"{column} = ?")
            values.append(value)
    if not sets:
        return get_document(conn, doc_id)
    values.append(doc_id)
    conn.execute(
        f"UPDATE documents SET {', '.join(sets)} WHERE id = ?",
        values,
    )
    conn.commit()
    return get_document(conn, doc_id)


def delete_document(
    conn: sqlite3.Connection,
    doc_id: str,
    *,
    delete_audio: bool = False,
) -> None:
    row = conn.execute(
        "SELECT audio_path FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.commit()
    if delete_audio and row is not None and row["audio_path"]:
        try:
            Path(row["audio_path"]).unlink()
        except (FileNotFoundError, OSError):
            pass
