"""Tests for db.migrate_stuck_recordings (AC9, AC10 regression).

AC9: migrate_stuck_recordings is idempotent — double-call yields 0 on second pass.
AC10: rows in statuses not in ('recording', 'pending') are never touched.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app import db as db_mod
from app.db import create_document, get_document, migrate, migrate_stuck_recordings


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    """Isolate ~/.locally per test — mirrors test_db.py pattern."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    yield tmp_path


@pytest.fixture
def conn(tmp_path):
    """Fresh in-process DB for each test."""
    db_file = tmp_path / "test.db"
    c = sqlite3.connect(str(db_file))
    c.row_factory = sqlite3.Row
    migrate(c)
    try:
        yield c
    finally:
        c.close()


class TestMigrateStuckRecordings:
    def test_recording_status_transitions_to_transcription_failed(self, conn):
        doc = create_document(conn, title="t1", status="recording")

        affected = migrate_stuck_recordings(conn)

        assert affected == 1
        updated = get_document(conn, doc["id"])
        assert updated["status"] == "transcription_failed"

    def test_pending_status_transitions_to_transcription_failed(self, conn):
        doc = create_document(conn, title="t1", status="pending")

        affected = migrate_stuck_recordings(conn)

        assert affected == 1
        assert get_document(conn, doc["id"])["status"] == "transcription_failed"

    def test_transcribing_status_is_not_touched(self, conn):
        """AC10 regression: rows actively transcribing must not be touched."""
        doc = create_document(conn, title="t1", status="transcribing")

        affected = migrate_stuck_recordings(conn)

        assert affected == 0
        assert get_document(conn, doc["id"])["status"] == "transcribing"

    def test_completed_status_is_not_touched(self, conn):
        doc = create_document(conn, title="t1", status="completed")

        migrate_stuck_recordings(conn)

        assert get_document(conn, doc["id"])["status"] == "completed"

    def test_transcription_failed_status_is_not_re_touched(self, conn):
        """Already-failed rows must not be double-counted on second call."""
        doc = create_document(conn, title="t1", status="transcription_failed")

        affected = migrate_stuck_recordings(conn)

        assert affected == 0
        assert get_document(conn, doc["id"])["status"] == "transcription_failed"

    def test_idempotent_double_call(self, conn):
        """AC9: calling twice — first returns 1, second returns 0."""
        create_document(conn, title="t1", status="recording")

        first = migrate_stuck_recordings(conn)
        second = migrate_stuck_recordings(conn)

        assert first == 1
        assert second == 0

    def test_multiple_stuck_rows_all_transitioned(self, conn):
        """Both 'recording' and 'pending' rows in one DB are all migrated."""
        create_document(conn, title="r1", status="recording")
        create_document(conn, title="p1", status="pending")
        create_document(conn, title="c1", status="completed")

        affected = migrate_stuck_recordings(conn)

        assert affected == 2
        docs = db_mod.list_documents(conn)
        statuses = {d["title"]: d["status"] for d in docs}
        assert statuses["r1"] == "transcription_failed"
        assert statuses["p1"] == "transcription_failed"
        assert statuses["c1"] == "completed"
