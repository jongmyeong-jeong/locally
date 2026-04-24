"""AC-5 + B4: ai_waiting heartbeat SSE events at elapsed_s ∈ {10, 20, 30}.

We directly exercise app.summarize._heartbeat_loop (the contract-level
seam) so the test is fast and deterministic. The server layer wires the
same elapsed_s into a SSE `ai_waiting` event in producer().
"""
from __future__ import annotations

import asyncio

import pytest

from app import summarize as summarize_mod


class TestHeartbeatMonotonic:
    @pytest.mark.asyncio
    async def test_heartbeat_monotonic_10_20_30(self, monkeypatch):
        """B4: _heartbeat_loop fires at 10, 20, 30 in order, none at t=0."""
        ticks: list[int] = []

        orig_sleep = asyncio.sleep
        call_count = {"n": 0}

        async def _fast(delay, *args, **kwargs):
            if delay == 10:
                call_count["n"] += 1
                if call_count["n"] > 3:
                    await orig_sleep(0)
                    raise asyncio.CancelledError
                await orig_sleep(0)
                return
            return await orig_sleep(delay, *args, **kwargs)

        monkeypatch.setattr(asyncio, "sleep", _fast)

        class _Proc:
            returncode = None

        async def _on_hb(elapsed_s: int) -> None:
            ticks.append(elapsed_s)

        task = asyncio.create_task(
            summarize_mod._heartbeat_loop(_Proc(), _on_hb, None)
        )
        await orig_sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Must be strictly [10, 20, 30, ...]; no t=0 entry.
        assert ticks[:3] == [10, 20, 30]
        assert 0 not in ticks

    @pytest.mark.asyncio
    async def test_heartbeat_stops_when_process_exits(self, monkeypatch):
        """Once proc.returncode is set, heartbeat exits without more ticks."""
        ticks: list[int] = []
        orig_sleep = asyncio.sleep

        async def _fast(delay, *args, **kwargs):
            if delay == 10:
                await orig_sleep(0)
                return
            return await orig_sleep(delay, *args, **kwargs)

        monkeypatch.setattr(asyncio, "sleep", _fast)

        class _Proc:
            def __init__(self):
                self.returncode = None

        proc = _Proc()

        async def _on_hb(elapsed_s: int) -> None:
            ticks.append(elapsed_s)
            # Simulate process exiting after first tick.
            proc.returncode = 0

        await summarize_mod._heartbeat_loop(proc, _on_hb, None)
        assert ticks == [10]  # stopped as soon as returncode set

    @pytest.mark.asyncio
    async def test_heartbeat_stops_on_cancel_event(self, monkeypatch):
        """If cancel_event is set, heartbeat loop exits silently."""
        ticks: list[int] = []
        orig_sleep = asyncio.sleep

        async def _fast(delay, *args, **kwargs):
            if delay == 10:
                await orig_sleep(0)
                return
            return await orig_sleep(delay, *args, **kwargs)

        monkeypatch.setattr(asyncio, "sleep", _fast)

        class _Proc:
            returncode = None

        cancel = asyncio.Event()
        cancel.set()

        async def _on_hb(elapsed_s: int) -> None:
            ticks.append(elapsed_s)

        await summarize_mod._heartbeat_loop(_Proc(), _on_hb, cancel)
        assert ticks == []
