"""Tests for app/glossary.py: load/save round-trip + prompt injection."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import glossary as mod


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    """Sandbox HOME so glossary.json writes land in tmp."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    yield tmp_path


# ── load ────────────────────────────────────────────────────────────────


class TestLoad:
    def test_missing_file_returns_empty(self):
        assert mod.load() == []

    def test_corrupt_json_returns_empty(self, tmp_path):
        p = tmp_path / "glossary.json"
        p.write_text("not json", encoding="utf-8")
        assert mod.load(p) == []

    def test_non_list_json_returns_empty(self, tmp_path):
        p = tmp_path / "glossary.json"
        p.write_text('{"terms": ["a"]}', encoding="utf-8")
        assert mod.load(p) == []

    def test_reads_string_array(self, tmp_path):
        p = tmp_path / "glossary.json"
        p.write_text('["Notion","애플","Figma"]', encoding="utf-8")
        assert mod.load(p) == ["Notion", "애플", "Figma"]

    def test_filters_non_string_entries(self, tmp_path):
        p = tmp_path / "glossary.json"
        p.write_text('["Notion",42,null,"Figma"]', encoding="utf-8")
        assert mod.load(p) == ["Notion", "Figma"]


# ── save ────────────────────────────────────────────────────────────────


class TestSave:
    def test_creates_file_with_exact_json(self, tmp_path):
        p = tmp_path / "glossary.json"
        mod.save(["Notion", "애플", "Figma"], p)
        assert p.read_text(encoding="utf-8") == '["Notion", "애플", "Figma"]'

    def test_dedupe_preserves_first_seen_order(self, tmp_path):
        p = tmp_path / "glossary.json"
        mod.save(["Notion", "Figma", "Notion", "애플", "Figma"], p)
        assert json.loads(p.read_text(encoding="utf-8")) == [
            "Notion",
            "Figma",
            "애플",
        ]

    def test_strips_whitespace(self, tmp_path):
        p = tmp_path / "glossary.json"
        mod.save(["  Notion  ", "\t애플\n"], p)
        assert json.loads(p.read_text(encoding="utf-8")) == ["Notion", "애플"]

    def test_drops_empty_and_whitespace_only(self, tmp_path):
        p = tmp_path / "glossary.json"
        mod.save(["", "   ", "Notion"], p)
        assert json.loads(p.read_text(encoding="utf-8")) == ["Notion"]

    def test_overwrites_existing_file(self, tmp_path):
        p = tmp_path / "glossary.json"
        mod.save(["old"], p)
        mod.save(["new"], p)
        assert json.loads(p.read_text(encoding="utf-8")) == ["new"]

    def test_empty_list_written_as_empty_array(self, tmp_path):
        p = tmp_path / "glossary.json"
        mod.save([], p)
        assert p.read_text(encoding="utf-8") == "[]"

    def test_korean_not_ascii_escaped(self, tmp_path):
        """ensure_ascii=False: Korean chars are written verbatim (not \\uXXXX)."""
        p = tmp_path / "glossary.json"
        mod.save(["애플"], p)
        raw = p.read_text(encoding="utf-8")
        assert "애플" in raw
        assert "\\u" not in raw

    def test_save_to_default_location_uses_home(self, _tmp_home):
        mod.save(["Notion"])
        default = _tmp_home / ".locally" / "workspace" / "glossary.json"
        assert default.exists()
        assert json.loads(default.read_text(encoding="utf-8")) == ["Notion"]


# ── round-trip ──────────────────────────────────────────────────────────


class TestRoundTrip:
    def test_load_after_save_returns_same_list(self, tmp_path):
        p = tmp_path / "glossary.json"
        original = ["Notion", "애플", "Figma"]
        mod.save(original, p)
        assert mod.load(p) == original

    def test_load_after_save_default_location(self, _tmp_home):
        original = ["Notion", "애플"]
        mod.save(original)
        assert mod.load() == original


# ── inject_into_prompt ──────────────────────────────────────────────────


class TestInjectIntoPrompt:
    def test_replaces_placeholder(self):
        tpl = "terms: {glossary terms comma-separated}!"
        result = mod.inject_into_prompt(tpl, ["Notion", "애플"])
        assert result == "terms: Notion, 애플!"

    def test_empty_terms_yield_empty_substitution(self):
        tpl = "terms: {glossary terms comma-separated}!"
        assert mod.inject_into_prompt(tpl, []) == "terms: !"

    def test_no_placeholder_returns_original(self):
        tpl = "no placeholder here"
        assert mod.inject_into_prompt(tpl, ["x"]) == "no placeholder here"

    def test_multiple_placeholders_all_replaced(self):
        tpl = "{glossary terms comma-separated} / {glossary terms comma-separated}"
        assert mod.inject_into_prompt(tpl, ["a", "b"]) == "a, b / a, b"
