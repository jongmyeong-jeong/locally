"""Tests for app/db.py: schema, CRUD, and B3 (title=None → 'untitled')."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import db as mod
from app.db import (
    create_note,
    delete_note,
    get_note,
    list_notes,
    migrate,
    open_db,
    update_note,
)


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    """Isolate ~/.lonta/db.sqlite per test."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    yield tmp_path


@pytest.fixture
def conn(tmp_path):
    """Open a fresh DB rooted in tmp for each test."""
    c = open_db(tmp_path / "test.sqlite")
    try:
        yield c
    finally:
        c.close()


# ── schema ──────────────────────────────────────────────────────────────


class TestSchema:
    def test_creates_notes_table(self, conn):
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='notes'"
        ).fetchall()
        assert len(rows) == 1

    def test_migrate_is_idempotent(self, conn):
        migrate(conn)  # second call must not raise
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='notes'"
        ).fetchall()
        assert len(rows) == 1

    def test_all_expected_columns_exist(self, conn):
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(notes)")}
        assert cols == {
            "id",
            "title",
            "created_at",
            "status",
            "audio_path",
            "transcript_path",
        }

    def test_open_db_creates_parent_dir(self, tmp_path):
        nested = tmp_path / "nested" / "sub" / "db.sqlite"
        c = mod.open_db(nested)
        try:
            assert nested.exists()
        finally:
            c.close()


# ── create_note ─────────────────────────────────────────────────────


class TestCreateNote:
    def test_creates_with_title_and_audio_path(self, conn):
        doc = create_note(conn, title="Notion meeting", audio_path="/tmp/a.wav")
        assert doc["id"]
        assert doc["title"] == "Notion meeting"
        assert doc["audioPath"] == "/tmp/a.wav"
        assert doc["status"] == "pending"
        assert doc["createdAt"]
        assert doc["transcriptPath"] is None

    def test_create_note_no_title(self, conn):
        """B3: title=None → stored as literal 'untitled'.

        This test is a Phase B gate-blocker per the plan §3 Change Matrix.
        """
        doc = create_note(conn)
        assert doc["title"] == "untitled"
        assert doc["status"] == "pending"

    def test_empty_string_title_treated_as_untitled(self, conn):
        """B3 extension: empty string also falls back to 'untitled'."""
        doc = create_note(conn, title="")
        assert doc["title"] == "untitled"

    def test_explicit_audio_path_none_is_stored(self, conn):
        doc = create_note(conn, title="t")
        assert doc["audioPath"] is None

    def test_ids_are_unique(self, conn):
        a = create_note(conn, title="a")
        b = create_note(conn, title="b")
        assert a["id"] != b["id"]

    def test_keeps_verbatim_title(self, conn):
        """No slugify at DB layer; title is stored as-given."""
        doc = create_note(conn, title="  Weird / Title!  ")
        assert doc["title"] == "  Weird / Title!  "


# ── get_note / list_notes ───────────────────────────────────────


class TestReadPath:
    def test_get_note_returns_row(self, conn):
        created = create_note(conn, title="x", audio_path="/x")
        found = get_note(conn, created["id"])
        assert found is not None
        assert found["id"] == created["id"]
        assert found["title"] == "x"

    def test_get_note_unknown_returns_none(self, conn):
        assert get_note(conn, "no-such-id") is None

    def test_list_notes_empty(self, conn):
        assert list_notes(conn) == []

    def test_list_notes_ordered_desc(self, conn):
        conn.execute(
            "INSERT INTO notes (id, title, created_at, status, audio_path) "
            "VALUES (?, ?, ?, ?, ?)",
            ("id-1", "first", "2024-01-01T00:00:00.000Z", "pending", "/1"),
        )
        conn.execute(
            "INSERT INTO notes (id, title, created_at, status, audio_path) "
            "VALUES (?, ?, ?, ?, ?)",
            ("id-2", "second", "2024-01-02T00:00:00.000Z", "pending", "/2"),
        )
        conn.commit()
        rows = list_notes(conn)
        assert [r["title"] for r in rows] == ["second", "first"]


# ── update_note ─────────────────────────────────────────────────────


class TestUpdateNote:
    def test_updates_status(self, conn):
        doc = create_note(conn, title="x")
        updated = update_note(conn, doc["id"], status="transcribing")
        assert updated is not None
        assert updated["status"] == "transcribing"

    def test_updates_transcript_path_via_camel_case_alias(self, conn):
        doc = create_note(conn, title="x")
        updated = update_note(
            conn, doc["id"], transcriptPath="/tmp/transcript.md"
        )
        assert updated["transcriptPath"] == "/tmp/transcript.md"

    def test_updates_transcript_path_via_snake_case(self, conn):
        doc = create_note(conn, title="x")
        updated = update_note(
            conn, doc["id"], transcript_path="/tmp/transcript.md"
        )
        assert updated["transcriptPath"] == "/tmp/transcript.md"

    def test_ignores_unknown_columns(self, conn):
        doc = create_note(conn, title="x")
        # Should not raise; should not modify anything.
        updated = update_note(conn, doc["id"], bogus="nope")
        assert updated["title"] == "x"

    def test_no_fields_returns_current_row(self, conn):
        doc = create_note(conn, title="x")
        updated = update_note(conn, doc["id"])
        assert updated is not None
        assert updated["id"] == doc["id"]


# ── delete_note ─────────────────────────────────────────────────────


class TestDeleteNote:
    def test_deletes_row(self, conn):
        doc = create_note(conn, title="x")
        delete_note(conn, doc["id"])
        assert get_note(conn, doc["id"]) is None

    def test_delete_audio_removes_file(self, conn, tmp_path):
        audio = tmp_path / "voice.wav"
        audio.write_bytes(b"\x00" * 32)
        doc = create_note(conn, title="x", audio_path=str(audio))
        assert audio.exists()
        delete_note(conn, doc["id"], delete_audio=True)
        assert not audio.exists()

    def test_delete_keeps_audio_by_default(self, conn, tmp_path):
        audio = tmp_path / "voice.wav"
        audio.write_bytes(b"\x00" * 32)
        doc = create_note(conn, title="x", audio_path=str(audio))
        delete_note(conn, doc["id"])
        assert audio.exists()

    def test_delete_audio_missing_file_is_safe(self, conn, tmp_path):
        # audio_path recorded but file never existed — must not raise.
        doc = create_note(
            conn, title="x", audio_path=str(tmp_path / "nope.wav")
        )
        delete_note(conn, doc["id"], delete_audio=True)
        assert get_note(conn, doc["id"]) is None
