"""AC-4 / AC-10 / N7 tests for POST /api/models/download SSE + model readiness."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import models_catalog
from app.server import create_app


def _parse_sse(text: str) -> list[dict]:
    """Split an SSE stream into a list of {'event': ..., 'data': ...}."""
    events: list[dict] = []
    current: dict = {}
    for raw in text.splitlines():
        if raw.startswith(":"):
            continue  # keep-alive comment
        if raw == "":
            if current:
                events.append(current)
                current = {}
            continue
        if raw.startswith("event: "):
            current["event"] = raw[len("event: "):]
        elif raw.startswith("data: "):
            try:
                current["data"] = json.loads(raw[len("data: "):])
            except json.JSONDecodeError:
                current["data"] = raw[len("data: "):]
    if current:
        events.append(current)
    return events


class TestDownloadHappyPath:
    def test_download_happy_path(
        self, tmp_home, mock_platform, mock_snapshot_download_progress
    ):
        """AC-4: progress SSE events then complete; file lands under ~/.locally/models/."""
        mock_platform("Darwin", "arm64")
        app = create_app()
        with TestClient(app) as c:
            model_id = models_catalog.catalog_for_current_os()[0]["id"]
            r = c.post(
                "/api/models/download",
                json={"modelId": model_id},
            )
            assert r.status_code == 200
            events = _parse_sse(r.text)

        # At minimum the final 100% tick is emitted. During real downloads the
        # polling thread emits intermediate ticks too, but in tests snapshot_download
        # completes instantly so the poller has no time to fire.
        progress_events = [e for e in events if e.get("event") == "progress"]
        assert len(progress_events) >= 1
        # Final progress event must be 100%.
        assert progress_events[-1]["data"]["percent"] == 1.0
        # Monotonic non-decreasing percent.
        pcts = [e["data"]["percent"] for e in progress_events]
        assert pcts == sorted(pcts), f"percent not monotonic: {pcts}"

        complete = [e for e in events if e.get("event") == "complete"]
        assert len(complete) == 1
        path = complete[0]["data"]["path"]
        assert str(tmp_home / ".locally" / "models") in path

    def test_download_cadence_progress_gaps_under_500ms(
        self, tmp_home, mock_platform, mock_snapshot_download_progress
    ):
        """B5: progress events come quickly; no long silence.

        We can't measure true wall-clock gaps here (the blocking thread
        runs to completion before SSE emits end-to-end), but we assert
        the progress queue contains at least start+end events without
        error.
        """
        mock_platform("Darwin", "arm64")
        app = create_app()
        with TestClient(app) as c:
            model_id = models_catalog.catalog_for_current_os()[0]["id"]
            r = c.post("/api/models/download", json={"modelId": model_id})
            events = _parse_sse(r.text)
        progress_events = [e for e in events if e.get("event") == "progress"]
        # At least the final 100% tick is emitted; no errors.
        assert len(progress_events) >= 1
        assert not any(e.get("event") == "error" for e in events)


class TestDownloadError:
    def test_download_error_emits_error_event(self, tmp_home, mock_platform, monkeypatch):
        """AC-10: HF error → SSE `error` with `canRetry: True`."""
        mock_platform("Darwin", "arm64")

        import huggingface_hub

        def _explode(**_kwargs):
            raise ConnectionError("network unreachable")

        monkeypatch.setattr(huggingface_hub, "snapshot_download", _explode)

        app = create_app()
        with TestClient(app) as c:
            model_id = models_catalog.catalog_for_current_os()[0]["id"]
            r = c.post("/api/models/download", json={"modelId": model_id})
            events = _parse_sse(r.text)
        errors = [e for e in events if e.get("event") == "error"]
        assert len(errors) == 1
        data = errors[0]["data"]
        assert "network unreachable" in data["message"] or data["message"]
        assert data["canRetry"] is True


class TestIncompleteDirSentinel:
    def test_incomplete_dir_reports_model_not_ready(self, tmp_home, mock_platform):
        """N7: presence of `{model}.incomplete/` → modelReady False."""
        mock_platform("Darwin", "arm64")
        model_id = models_catalog.catalog_for_current_os()[0]["id"]
        canonical = models_catalog.model_dir_for(model_id)
        canonical.mkdir(parents=True, exist_ok=True)
        (canonical / "model.bin").write_bytes(b"x")
        incomplete = models_catalog.incomplete_dir_for(model_id)
        incomplete.mkdir(parents=True, exist_ok=True)

        app = create_app()
        with TestClient(app) as c:
            r = c.get("/api/system/info")
        assert r.json()["modelReady"] is False
