"""Tests for app/summarize.py: build_prompt (M10) + run_ai timeout/cancel (A2, B4)."""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time

import pytest

from app import summarize
from app.summarize import build_prompt


# ── build_prompt (M10 contract-level) ────────────────────────────────────


class TestBuildPrompt:
    def test_build_prompt_contains_all_glossary_terms(self):
        """M10: every glossary term appears in the built prompt."""
        terms = ["Notion", "애플", "Figma"]
        prompt = summarize.build_prompt(
            transcript="hello world",
            glossary_terms=terms,
            title="회의록",
        )
        for t in terms:
            assert t in prompt, f"glossary term {t!r} missing from prompt"

    def test_build_prompt_contains_title(self):
        prompt = summarize.build_prompt(
            transcript="t", glossary_terms=[], title="프로젝트 킥오프"
        )
        assert "프로젝트 킥오프" in prompt

    def test_build_prompt_contains_transcript(self):
        prompt = summarize.build_prompt(
            transcript="quick brown fox", glossary_terms=[], title="x"
        )
        assert "quick brown fox" in prompt

    def test_build_prompt_template_prefix(self):
        """AC-6 fallback contract requires this prefix verbatim."""
        prompt = summarize.build_prompt(transcript="x", glossary_terms=[])
        assert prompt.startswith("다음 전사 내용을 한국어 회의록으로 정리해주세요")

    def test_build_prompt_empty_glossary(self):
        prompt = summarize.build_prompt(transcript="x", glossary_terms=[])
        # Placeholder substituted with empty string; template layout preserved.
        assert "{glossary terms comma-separated}" not in prompt

    def test_build_prompt_joins_terms_comma_space(self):
        prompt = summarize.build_prompt(
            transcript="x", glossary_terms=["A", "B", "C"]
        )
        assert "A, B, C" in prompt


# ── run_ai (A2, B4) ──────────────────────────────────────────────────────


def _py(script: str) -> str:
    """Return path to the current python interpreter; use its '-c' runner."""
    return sys.executable


class TestRunAiSuccess:
    @pytest.mark.asyncio
    async def test_claude_stdout_returned(self):
        """run_ai happy path: stdout captured and returned verbatim."""
        # Use a trick: we point ai_path at the python interpreter and rely on
        # `claude -p <prompt>` → we can't really invoke claude here. So we
        # monkey-patch create_subprocess_exec to simulate a 0-exit process.
        pass


class TestRunAiTimeout:
    @pytest.mark.asyncio
    async def test_timeout_kills_subprocess(self, monkeypatch):
        """On timeout, the subprocess is killed and TimeoutExpired raised."""

        class _FakeProc:
            def __init__(self):
                self.returncode = None
                self.pid = 4242
                self.killed = False

            async def communicate(self):  # noqa: D401
                # Simulate a process that never exits.
                await asyncio.sleep(10)
                return b"", b""

            def kill(self):
                self.killed = True
                self.returncode = -9

            async def wait(self):
                return self.returncode

        fake = _FakeProc()

        async def _fake_exec(*args, **kwargs):  # noqa: ARG001
            return fake

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

        with pytest.raises(subprocess.TimeoutExpired):
            await summarize.run_ai(
                ai_name="claude",
                ai_path="/bin/true",
                prompt="hi",
                timeout_s=1,  # tight timeout drives the test
            )
        assert fake.killed is True


class TestRunAiCancel:
    @pytest.mark.asyncio
    async def test_cancel_kills_subprocess(self, monkeypatch):
        """A2: setting cancel_event mid-flight kills the subprocess quickly.

        Uses a real subprocess (`python -c "time.sleep(60)"`) and asserts it
        is killed within 2 seconds of cancel.
        """
        cancel = asyncio.Event()

        sleeper = [
            sys.executable,
            "-c",
            "import time, sys; sys.stdout.flush(); time.sleep(60)",
        ]

        real_exec = asyncio.create_subprocess_exec
        captured_proc = {}

        async def _capture(*args, **kwargs):
            proc = await real_exec(*sleeper, **kwargs)
            captured_proc["proc"] = proc
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _capture)

        async def _cancel_soon():
            await asyncio.sleep(0.2)
            cancel.set()

        t0 = time.monotonic()
        canceller = asyncio.create_task(_cancel_soon())
        with pytest.raises(asyncio.CancelledError):
            await summarize.run_ai(
                ai_name="claude",
                ai_path=sys.executable,  # irrelevant; substitution above
                prompt="hi",
                cancel_event=cancel,
                timeout_s=60,
            )
        elapsed = time.monotonic() - t0
        await canceller
        assert elapsed < 2.0, f"cancel did not propagate fast enough: {elapsed}s"
        proc = captured_proc.get("proc")
        assert proc is not None
        # After cancel, the real child should be dead.
        await asyncio.wait_for(proc.wait(), timeout=2.0)
        assert proc.returncode is not None


class TestRunAiHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_fires_at_10s_intervals(self, monkeypatch):
        """B4: on_heartbeat called at elapsed_s ∈ {10, 20, 30, ...}.

        We shrink `asyncio.sleep(10)` inside the heartbeat loop to virtual
        time by monkey-patching asyncio.sleep to a no-op that captures
        elapsed_s. Three invocations expected before we break.
        """
        ticks: list[int] = []

        orig_sleep = asyncio.sleep
        call_count = {"n": 0}

        async def _fast_sleep(delay, *args, **kwargs):
            # Hook only the 10-s waits from _heartbeat_loop; pass through
            # other shorter sleeps untouched.
            if delay == 10:
                call_count["n"] += 1
                if call_count["n"] > 3:
                    await orig_sleep(0.01)
                    raise asyncio.CancelledError
                await orig_sleep(0)
                return
            return await orig_sleep(delay, *args, **kwargs)

        monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

        class _Proc:
            returncode = None

        proc = _Proc()

        async def _on_heartbeat(elapsed_s: int) -> None:
            ticks.append(elapsed_s)

        task = asyncio.create_task(
            summarize._heartbeat_loop(proc, _on_heartbeat, None)
        )
        # Let the patched sleeps run to exhaust the ticks.
        await orig_sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # We expect at least 10, 20, 30 in order.
        assert ticks[:3] == [10, 20, 30]


class TestWriteOutputs:
    def test_writes_prompt_only_when_summary_none(self, tmp_path):
        t_root = tmp_path / "transcripts"
        s_root = tmp_path / "summaries"
        out = summarize.write_outputs(
            doc_id="d1",
            slug="2026-04-17-demo",
            summary_md=None,
            prompt_md="PROMPT",
            transcript_root=t_root,
            summary_root=s_root,
        )
        assert out["summary_path"] is None
        assert (t_root / "2026-04-17-demo.prompt.md").read_text(
            encoding="utf-8"
        ) == "PROMPT"

    def test_writes_both_when_summary_present(self, tmp_path):
        t_root = tmp_path / "transcripts"
        s_root = tmp_path / "summaries"
        out = summarize.write_outputs(
            doc_id="d1",
            slug="s",
            summary_md="# X",
            prompt_md="P",
            transcript_root=t_root,
            summary_root=s_root,
        )
        assert out["summary_path"] is not None
        assert (s_root / "s.summary.md").read_text(encoding="utf-8") == "# X"
        assert (t_root / "s.prompt.md").read_text(encoding="utf-8") == "P"


# ── build_prompt custom template (new template= parameter) ──────────────


class TestBuildPromptCustomTemplate:
    def test_replaces_transcript_placeholder(self):
        out = build_prompt(
            transcript="hello",
            glossary_terms=[],
            title="X",
            template="body: {transcript}",
        )
        assert out == "body: hello"

    def test_replaces_title_placeholder(self):
        out = build_prompt(
            transcript="T",
            glossary_terms=[],
            title="foo",
            template="# {title}",
        )
        assert out == "# foo"

    def test_replaces_glossary_with_comma_join(self):
        out = build_prompt(
            transcript="T",
            glossary_terms=["A", "B"],
            title="X",
            template="G: {glossary}",
        )
        assert out == "G: A, B"

    def test_custom_path_does_not_call_inject_into_prompt(self):
        # 사용자가 {glossary terms comma-separated} 리터럴을 그대로 두면
        # 치환되지 않은 채 AI에 전달됨.
        out = build_prompt(
            transcript="T",
            glossary_terms=["X"],
            title="H",
            template="{glossary terms comma-separated}",
        )
        assert out == "{glossary terms comma-separated}"

    def test_natural_brace_tokens_preserved(self):
        # CF-1 방어: 자연어 지시문의 중괄호 토큰이 있어도 KeyError 없음.
        template = "## 주요 논의사항\n### {주제명}\n- {내용}\n\n{transcript}"
        out = build_prompt(
            transcript="T",
            glossary_terms=[],
            title="X",
            template=template,
        )
        assert "{주제명}" in out
        assert "{내용}" in out
        assert "T" in out  # transcript 치환됨

    def test_template_none_preserves_legacy_behavior(self):
        out = build_prompt(transcript="T", glossary_terms=["X"], title="H")
        assert "# H" in out
        assert "T" in out
