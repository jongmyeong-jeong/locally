"""Tests for app/paths.py: slugify, audio_basename, runtime.json, path resolvers.

Covers M7 slugify contract:
  - NFC normalization
  - 50-char code-point cap
  - Korean + English + digits preserved; other punctuation stripped
  - Whitespace → '-'; collapses consecutive '-'
  - Empty / all-rejected input → 'untitled'
"""
from __future__ import annotations

import json
import unicodedata
from datetime import datetime
from pathlib import Path

import pytest

from app import paths as mod
from app.paths import (
    audio_basename,
    clear_runtime,
    read_runtime,
    runtime_json_path,
    slugify,
    write_runtime,
)


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    """Sandbox $HOME for every test so runtime.json, glossary.json, etc. are isolated."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    yield tmp_path


# ── slugify ─────────────────────────────────────────────────────────────


class TestSlugify:
    def test_empty_input_returns_untitled(self):
        assert slugify("") == "untitled"

    def test_whitespace_only_returns_untitled(self):
        assert slugify("   ") == "untitled"

    def test_none_returns_untitled(self):
        assert slugify(None) == "untitled"  # type: ignore[arg-type]

    def test_punctuation_only_returns_untitled(self):
        assert slugify("!!!???...") == "untitled"

    def test_preserves_ascii_letters_and_digits(self):
        assert slugify("Notion") == "Notion"
        assert slugify("meeting-2026") == "meeting-2026"

    def test_preserves_korean(self):
        assert slugify("회의록") == "회의록"

    def test_mixed_korean_english(self):
        assert slugify("Notion 회의록") == "Notion-회의록"

    def test_spaces_become_hyphens(self):
        assert slugify("hello world") == "hello-world"

    def test_multiple_spaces_collapse_to_single_hyphen(self):
        assert slugify("hello    world") == "hello-world"

    def test_tabs_and_newlines_become_hyphens(self):
        assert slugify("hello\tworld\nagain") == "hello-world-again"

    def test_strips_punctuation(self):
        assert slugify("2026/04/17 회의!") == "20260417-회의"

    def test_collapses_adjacent_hyphens(self):
        assert slugify("a -- b -- c") == "a-b-c"

    def test_strips_leading_trailing_hyphens(self):
        assert slugify("--hello--") == "hello"

    def test_truncates_to_50_code_points_by_default(self):
        long_ascii = "a" * 80
        result = slugify(long_ascii)
        assert len(result) == 50
        assert result == "a" * 50

    def test_truncates_at_code_point_not_byte_boundary(self):
        # Each Korean syllable is 3 bytes UTF-8 but 1 code point.
        long_korean = "가" * 80
        result = slugify(long_korean)
        assert len(result) == 50  # 50 code points, not bytes
        assert result == "가" * 50

    def test_custom_max_len(self):
        assert slugify("abcdef", max_len=3) == "abc"

    def test_truncation_strips_trailing_hyphen(self):
        # After truncation, trailing '-' must be stripped (still non-empty).
        result = slugify("hello-world-more-and-more", max_len=12)
        assert not result.endswith("-")

    def test_nfc_normalization(self):
        # '가' can be composed (single code point U+AC00) or decomposed
        # (ᄀ U+1100 + ᅡ U+1161). After NFC both should yield the same slug.
        composed = "\uac00"
        decomposed = "\u1100\u1161"
        assert unicodedata.normalize("NFC", decomposed) == composed
        assert slugify(composed) == slugify(decomposed) == "가"

    def test_fallback_on_all_rejected_unicode(self):
        # Emoji / punctuation / CJK unified ideographs outside hangul ranges.
        assert slugify("🎉🎉") == "untitled"
        assert slugify("漢字") == "untitled"  # not Hangul → rejected


# ── audio_basename ──────────────────────────────────────────────────────


class TestAudioBasename:
    def test_formats_date_prefix_and_slug(self):
        now = datetime(2026, 4, 17, 12, 0, 0)
        assert audio_basename("Notion meeting", now) == "2026-04-17-Notion-meeting"

    def test_none_title_uses_untitled(self):
        now = datetime(2026, 4, 17, 9, 30, 0)
        assert audio_basename(None, now) == "2026-04-17-untitled"

    def test_empty_title_uses_untitled(self):
        now = datetime(2026, 1, 1, 0, 0, 0)
        assert audio_basename("", now) == "2026-01-01-untitled"

    def test_korean_title(self):
        now = datetime(2026, 4, 17, 9, 0, 0)
        assert audio_basename("회의록", now) == "2026-04-17-회의록"


# ── path resolvers ──────────────────────────────────────────────────────


class TestPathResolvers:
    def test_data_root_creates_dir(self, _tmp_home):
        root = mod.data_root()
        assert root.exists()
        assert root.is_dir()
        assert root == _tmp_home / ".lonta" / "data"

    def test_app_home_creates_dir(self, _tmp_home):
        root = mod.app_home()
        assert root.exists()
        assert root == _tmp_home / ".lonta"

    def test_subdirs_exist(self, _tmp_home):
        for fn in (mod.notes_dir, mod.audio_dir, mod.logs_dir):
            p = fn()
            assert p.exists()
            assert p.is_dir()

    def test_db_path_points_under_app_home(self, _tmp_home):
        assert mod.db_path() == _tmp_home / ".lonta" / "db.sqlite"

    def test_runtime_json_path_points_under_app_home(self, _tmp_home):
        assert runtime_json_path() == _tmp_home / ".lonta" / "runtime.json"


# ── runtime.json ────────────────────────────────────────────────────────


class TestRuntimeJson:
    def test_read_missing_returns_none(self):
        assert read_runtime() is None

    def test_write_and_read_round_trip(self):
        write_runtime(pid=4242, port=54787, started_at=1760000000.0)
        got = read_runtime()
        assert got == {"pid": 4242, "port": 54787, "started_at": 1760000000.0}

    def test_malformed_json_returns_none(self):
        runtime_json_path().write_text("not-json", encoding="utf-8")
        assert read_runtime() is None

    def test_clear_runtime_removes_file(self):
        write_runtime(pid=1, port=1, started_at=0.0)
        assert runtime_json_path().exists()
        clear_runtime()
        assert not runtime_json_path().exists()

    def test_clear_runtime_is_idempotent(self):
        # No file present; must not raise.
        clear_runtime()
        clear_runtime()

    def test_write_is_valid_utf8_json(self, _tmp_home):
        write_runtime(pid=9, port=54787, started_at=1.5)
        raw = runtime_json_path().read_text(encoding="utf-8")
        assert json.loads(raw) == {"pid": 9, "port": 54787, "started_at": 1.5}
