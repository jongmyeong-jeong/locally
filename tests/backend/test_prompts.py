"""Unit tests for app.prompts module (load/save/seed/next_id/SEED_TEMPLATE)."""
from __future__ import annotations

import json

from app import prompts as prompts_mod


class TestLoad:
    def test_missing_returns_empty(self, tmp_path):
        assert prompts_mod.load(tmp_path / "nope.json") == []

    def test_corrupt_json_returns_empty(self, tmp_path):
        p = tmp_path / "prompts.json"
        p.write_text("{not json", encoding="utf-8")
        assert prompts_mod.load(p) == []

    def test_non_list_returns_empty(self, tmp_path):
        p = tmp_path / "prompts.json"
        p.write_text(json.dumps({"a": 1}), encoding="utf-8")
        assert prompts_mod.load(p) == []

    def test_filters_items_missing_fields(self, tmp_path):
        p = tmp_path / "prompts.json"
        p.write_text(json.dumps([
            {"id": 1, "name": "ok", "template": "T"},
            {"id": 2, "name": "no_template"},
            "string-not-dict",
        ]), encoding="utf-8")
        result = prompts_mod.load(p)
        assert len(result) == 1
        assert result[0] == {"id": 1, "name": "ok", "template": "T"}

    def test_reads_valid_array(self, tmp_path):
        p = tmp_path / "prompts.json"
        items = [
            {"id": 1, "name": "회의록", "template": "T1"},
            {"id": 2, "name": "강의", "template": "T2"},
        ]
        p.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        assert prompts_mod.load(p) == items

    def test_skips_items_with_invalid_id_type(self, tmp_path):
        p = tmp_path / "prompts.json"
        p.write_text(json.dumps([
            {"id": "abc", "name": "bad", "template": "T"},
            {"id": 2, "name": "good", "template": "OK"},
        ]), encoding="utf-8")
        assert prompts_mod.load(p) == [
            {"id": 2, "name": "good", "template": "OK"},
        ]


class TestSave:
    def test_atomic_write_roundtrip(self, tmp_path):
        p = tmp_path / "prompts.json"
        prompts_mod.save([{"id": 1, "name": "n", "template": "t"}], p)
        assert p.exists()
        loaded = json.loads(p.read_text(encoding="utf-8"))
        assert loaded == [{"id": 1, "name": "n", "template": "t"}]

    def test_korean_preserved(self, tmp_path):
        p = tmp_path / "prompts.json"
        prompts_mod.save(
            [{"id": 1, "name": "회의록", "template": "안녕하세요"}], p
        )
        text = p.read_text(encoding="utf-8")
        assert "회의록" in text  # ensure_ascii=False

    def test_no_leftover_tmp_files(self, tmp_path):
        p = tmp_path / "prompts.json"
        prompts_mod.save([{"id": 1, "name": "n", "template": "t"}], p)
        leftovers = [f for f in tmp_path.iterdir() if f.suffix == ".tmp"]
        assert leftovers == []


class TestNextId:
    def test_empty_returns_1(self):
        assert prompts_mod.next_id([]) == 1

    def test_max_plus_one(self):
        assert prompts_mod.next_id([
            {"id": 1, "name": "a", "template": ""},
            {"id": 5, "name": "b", "template": ""},
            {"id": 3, "name": "c", "template": ""},
        ]) == 6

    def test_no_reuse_after_delete(self):
        # 시뮬레이션: id=2 삭제 후에도 next_id는 max+1 = 4
        presets = [
            {"id": 1, "name": "a", "template": ""},
            {"id": 3, "name": "c", "template": ""},
        ]
        assert prompts_mod.next_id(presets) == 4


class TestEnsureSeed:
    def test_creates_seed_when_missing(self, tmp_path):
        p = tmp_path / "prompts.json"
        prompts_mod.ensure_seed(p)
        loaded = prompts_mod.load(p)
        assert len(loaded) == 1
        assert loaded[0]["id"] == 1
        assert loaded[0]["name"] == "회의록"
        assert loaded[0]["template"] == prompts_mod.SEED_TEMPLATE

    def test_creates_seed_when_empty_array(self, tmp_path):
        p = tmp_path / "prompts.json"
        p.write_text("[]", encoding="utf-8")
        prompts_mod.ensure_seed(p)
        loaded = prompts_mod.load(p)
        assert len(loaded) == 1

    def test_no_op_when_populated(self, tmp_path):
        p = tmp_path / "prompts.json"
        prompts_mod.save(
            [{"id": 1, "name": "기존", "template": "X"}], p
        )
        prompts_mod.ensure_seed(p)
        assert prompts_mod.load(p) == [
            {"id": 1, "name": "기존", "template": "X"}
        ]


class TestSeedTemplate:
    def test_contains_user_facing_variables(self):
        t = prompts_mod.SEED_TEMPLATE
        assert "{title}" in t
        assert "{transcript}" in t
        assert "{glossary}" in t

    def test_does_not_contain_internal_literals(self):
        t = prompts_mod.SEED_TEMPLATE
        assert "{제목}" not in t
        assert "{transcript text}" not in t
        assert "{glossary terms comma-separated}" not in t

    def test_preserves_natural_language_braces(self):
        # 자연어 토큰들이 SEED_TEMPLATE에 그대로 보존되어야 함
        t = prompts_mod.SEED_TEMPLATE
        assert "{내용}" in t
        assert "{담당자}" in t
        assert "{마감}" in t
