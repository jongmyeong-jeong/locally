"""Stdlib sqlite3 wrapper for the `notes` table.

Schema (idempotent):
  notes(id TEXT PRIMARY KEY, title TEXT, created_at TEXT, status TEXT,
        audio_path TEXT, transcript_path TEXT)

Status values (informational; not a CHECK constraint):
  'recording' | 'pending' | 'transcribing' | 'transcribed'
  | 'completed' | 'error' | 'transcription_failed'

create_note(title=None) → title stored as literal 'untitled' (B3).
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.paths import db_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'untitled',
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    audio_path TEXT,
    transcript_path TEXT
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_notes_created_at ON notes(created_at DESC)",
)

_SCHEMA_CHUNKS = """
CREATE TABLE IF NOT EXISTS recording_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued','transcribing','success','retry','failed')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    text TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(note_id, seq)
)
"""

_INDEXES_CHUNKS = (
    "CREATE INDEX IF NOT EXISTS idx_recording_chunks_note ON recording_chunks(note_id, seq)",
)

_ALLOWED_UPDATE_COLUMNS = {
    "title",
    "status",
    "audio_path",
    "transcript_path",
}

# Map camelCase aliases accepted by update_note to DB columns.
_UPDATE_ALIASES = {
    "audioPath": "audio_path",
    "transcriptPath": "transcript_path",
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
    """Idempotent schema creation and migrations."""
    conn.execute(_SCHEMA)
    for stmt in _INDEXES:
        conn.execute(stmt)
    conn.execute(_SCHEMA_CHUNKS)
    for stmt in _INDEXES_CHUNKS:
        conn.execute(stmt)
    conn.commit()

    # Migration 1: drop notes.summary_path (Groq migration)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(notes)").fetchall()}
    if "summary_path" in cols:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("""
                CREATE TABLE notes_new (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT 'untitled',
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    audio_path TEXT,
                    transcript_path TEXT
                )
            """)
            conn.execute("""
                INSERT INTO notes_new (id, title, created_at, status, audio_path, transcript_path)
                SELECT id, title, created_at, status, audio_path, transcript_path FROM notes
            """)
            conn.execute("DROP TABLE notes")
            conn.execute("ALTER TABLE notes_new RENAME TO notes")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_notes_created_at ON notes(created_at DESC)"
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")


def migrate_stuck_recordings(conn: sqlite3.Connection) -> int:
    """Convert stuck in-progress rows to transcription_failed at app startup.

    Idempotent: rows already in 'transcription_failed' are not re-touched
    (WHERE status IN ('recording','pending')). Returns affected row count.
    """
    cur = conn.execute(
        "UPDATE notes SET status = 'transcription_failed' "
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
    }


def create_note(
    conn: sqlite3.Connection,
    title: str | None = None,
    audio_path: str | None = None,
    *,
    status: str = "pending",
) -> dict:
    """Insert a note row.

    Behavior (B3):
      - title is None or empty string → stored as literal 'untitled'.
      - Otherwise title is stored verbatim (no trim, no slugify).
      - status defaults to 'pending'.
    """
    note_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    stored_title = title if title else "untitled"
    conn.execute(
        "INSERT INTO notes (id, title, created_at, status, audio_path) "
        "VALUES (?, ?, ?, ?, ?)",
        (note_id, stored_title, created_at, status, audio_path),
    )
    conn.commit()
    return get_note(conn, note_id)  # type: ignore[return-value]


def get_note(conn: sqlite3.Connection, note_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM notes WHERE id = ?", (note_id,)
    ).fetchone()
    return _row_to_dict(row)


def list_notes(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM notes ORDER BY created_at DESC"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]  # type: ignore[misc]


def update_note(conn: sqlite3.Connection, note_id: str, **fields) -> dict | None:
    sets: list[str] = []
    values: list[object] = []
    for key, value in fields.items():
        column = _UPDATE_ALIASES.get(key, key)
        if column in _ALLOWED_UPDATE_COLUMNS:
            sets.append(f"{column} = ?")
            values.append(value)
    if not sets:
        return get_note(conn, note_id)
    values.append(note_id)
    conn.execute(
        f"UPDATE notes SET {', '.join(sets)} WHERE id = ?",
        values,
    )
    conn.commit()
    return get_note(conn, note_id)


def delete_note(
    conn: sqlite3.Connection,
    note_id: str,
    *,
    delete_audio: bool = False,
) -> None:
    row = conn.execute(
        "SELECT audio_path FROM notes WHERE id = ?", (note_id,)
    ).fetchone()
    conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    conn.commit()
    if delete_audio and row is not None and row["audio_path"]:
        try:
            Path(row["audio_path"]).unlink()
        except (FileNotFoundError, OSError):
            pass
