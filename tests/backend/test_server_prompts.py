"""Integration tests for prompt-preset API routes (F1~F6, G3 plumbing)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.server import create_app


class TestGetPrompts:
    def test_returns_seed_when_empty(self, tmp_home):
        with TestClient(create_app()) as c:
            r = c.get("/api/prompts")
            assert r.status_code == 200
            data = r.json()
            assert len(data) == 1
            assert data[0]["name"] == "회의록"
            assert "{title}" in data[0]["template"]

    def test_returns_array_in_file_order(self, tmp_home):
        from app import prompts as prompts_mod
        prompts_mod.save([
            {"id": 2, "name": "B", "template": "T2"},
            {"id": 1, "name": "A", "template": "T1"},
        ])
        with TestClient(create_app()) as c:
            r = c.get("/api/prompts")
            assert [p["id"] for p in r.json()] == [2, 1]


class TestCreatePrompt:
    def test_creates_with_incremented_id(self, tmp_home):
        with TestClient(create_app()) as c:
            c.get("/api/prompts")  # ensure seed (id=1)
            r = c.post("/api/prompts", json={"name": "n", "template": "t"})
            assert r.status_code == 201
            assert r.json()["id"] == 2

    def test_appends_to_end(self, tmp_home):
        with TestClient(create_app()) as c:
            c.get("/api/prompts")
            c.post("/api/prompts", json={"name": "X", "template": "tx"})
            ids = [p["id"] for p in c.get("/api/prompts").json()]
            assert ids[-1] == 2

    def test_allows_empty_name(self, tmp_home):
        with TestClient(create_app()) as c:
            c.get("/api/prompts")
            r = c.post("/api/prompts", json={"name": "", "template": ""})
            assert r.status_code == 201


class TestUpdatePrompt:
    def test_partial_update_name_only(self, tmp_home):
        with TestClient(create_app()) as c:
            c.get("/api/prompts")
            r = c.put("/api/prompts/1", json={"name": "신이름"})
            assert r.status_code == 200
            assert r.json()["name"] == "신이름"

    def test_404_on_missing_id(self, tmp_home):
        with TestClient(create_app()) as c:
            c.get("/api/prompts")
            r = c.put("/api/prompts/999", json={"name": "x"})
            assert r.status_code == 404


class TestDeletePrompt:
    def test_removes_item(self, tmp_home):
        with TestClient(create_app()) as c:
            c.get("/api/prompts")
            c.post("/api/prompts", json={"name": "A", "template": ""})
            c.post("/api/prompts", json={"name": "B", "template": ""})
            r = c.delete("/api/prompts/2")
            assert r.status_code == 204
            ids = [p["id"] for p in c.get("/api/prompts").json()]
            assert 2 not in ids

    def test_404_on_missing_id(self, tmp_home):
        with TestClient(create_app()) as c:
            c.get("/api/prompts")
            r = c.delete("/api/prompts/999")
            assert r.status_code == 404

    def test_reseeds_on_next_get_after_empty(self, tmp_home):
        with TestClient(create_app()) as c:
            c.get("/api/prompts")  # seed id=1
            c.delete("/api/prompts/1")
            data = c.get("/api/prompts").json()
            assert len(data) == 1
            assert data[0]["name"] == "회의록"


class TestReorderPrompts:
    def test_reorders_correctly(self, tmp_home):
        with TestClient(create_app()) as c:
            c.get("/api/prompts")  # id=1
            c.post("/api/prompts", json={"name": "B", "template": ""})  # id=2
            c.post("/api/prompts", json={"name": "C", "template": ""})  # id=3
            r = c.put("/api/prompts/order", json={"order": [3, 1, 2]})
            assert r.status_code == 200
            ids = [p["id"] for p in c.get("/api/prompts").json()]
            assert ids == [3, 1, 2]

    def test_ignores_unknown_ids(self, tmp_home):
        with TestClient(create_app()) as c:
            c.get("/api/prompts")  # id=1
            c.post("/api/prompts", json={"name": "B", "template": ""})  # id=2
            r = c.put("/api/prompts/order", json={"order": [2, 999, 1]})
            assert r.status_code == 200
            ids = [p["id"] for p in c.get("/api/prompts").json()]
            assert ids == [2, 1]


class TestA3PromptsJsonDeletion:
    def test_a3_prompts_json_deletion_reseeds_on_get(self, tmp_home):
        # A3: prompts.json 삭제 후 GET /api/prompts → ensure_seed → 시드 1개 재생성
        from app.paths import prompts_path

        with TestClient(create_app()) as c:
            c.get("/api/prompts")  # 시드 생성 (id=1)
        p = prompts_path()
        assert p.exists()
        # 파일 삭제
        p.unlink()
        # 재시드 확인
        with TestClient(create_app()) as c:
            data = c.get("/api/prompts").json()
            assert len(data) == 1
            assert data[0]["name"] == "회의록"


class TestRouteOrder:
    """라우트 등록 순서 회귀 — `/api/prompts/order`가 `{prompt_id}`보다 먼저 등록되어야 함."""

    def test_order_endpoint_not_shadowed_by_id_route(self, tmp_home):
        with TestClient(create_app()) as c:
            c.get("/api/prompts")  # seed
            ids = [p["id"] for p in c.get("/api/prompts").json()]
            r = c.put("/api/prompts/order", json={"order": ids})
            # 만약 라우트 순서가 잘못되어 'order'가 int 파싱에 들어가면 422.
            assert r.status_code == 200, f"라우트 순서 오류: {r.text}"
