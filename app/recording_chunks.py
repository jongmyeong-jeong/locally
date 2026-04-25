"""DB operations for the recording_chunks table.

State machine:
  queued → transcribing → success
  queued → transcribing → retry → transcribing → success
  queued → transcribing → retry → transcribing → failed

All functions accept an open sqlite3.Connection and perform no business logic
beyond state transitions. Callers are responsible for committing or rolling back.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

_ALLOWED_STATUSES = frozenset({"queued", "transcribing", "success", "retry", "failed"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def insert_chunk(
    conn: sqlite3.Connection,
    note_id: str,
    seq: int,
    start_ms: int,
    end_ms: int,
) -> int:
    """Insert a chunk with status='queued'; return the new row id."""
    now = _now_iso()
    cur = conn.execute(
        "INSERT INTO recording_chunks "
        "(note_id, seq, start_ms, end_ms, status, retry_count, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'queued', 0, ?, ?)",
        (note_id, seq, start_ms, end_ms, now, now),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def update_chunk_status(
    conn: sqlite3.Connection,
    chunk_id: int,
    status: str,
    text: str | None = None,
) -> None:
    """Update status (and optionally text). Raises ValueError for invalid status."""
    if status not in _ALLOWED_STATUSES:
        raise ValueError(
            f"Invalid chunk status {status!r}. Must be one of {sorted(_ALLOWED_STATUSES)}"
        )
    now = _now_iso()
    if text is not None:
        conn.execute(
            "UPDATE recording_chunks SET status = ?, text = ?, updated_at = ? WHERE id = ?",
            (status, text, now, chunk_id),
        )
    else:
        conn.execute(
            "UPDATE recording_chunks SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, chunk_id),
        )
    conn.commit()


def get_chunks(conn: sqlite3.Connection, note_id: str) -> list[dict]:
    """Return all chunks for a note ordered by seq."""
    rows = conn.execute(
        "SELECT * FROM recording_chunks WHERE note_id = ? ORDER BY seq",
        (note_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_failed_chunks(conn: sqlite3.Connection, note_id: str) -> list[dict]:
    """Return chunks with status='failed', ordered by seq."""
    rows = conn.execute(
        "SELECT * FROM recording_chunks WHERE note_id = ? AND status = 'failed' ORDER BY seq",
        (note_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def all_chunks_done(conn: sqlite3.Connection, note_id: str) -> bool:
    """True iff every chunk has status='success'. False if no rows exist."""
    row = conn.execute(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS done "
        "FROM recording_chunks WHERE note_id = ?",
        (note_id,),
    ).fetchone()
    total = row["total"]
    done = row["done"] or 0
    return total > 0 and total == done
