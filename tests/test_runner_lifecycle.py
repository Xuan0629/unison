import sys
from pathlib import Path

import pytest

from unison.interfaces import AgentResult, AgentSpec
from unison.runners.base import BaseRunner, ProcessHandle


class _PythonRunner(BaseRunner):
    def _build_command(self, spec, prompt):
        del spec, prompt
        return [sys.executable, "-c", "print('ok')"]


@pytest.mark.skipif(sys.platform != "linux", reason="verified /proc process identity requires Linux")
def test_runner_reports_verified_process_group_before_completion(tmp_path):
    runner = _PythonRunner(binary=sys.executable)
    spec = AgentSpec("developer", "codex", "test", Path("prompts/developer.md"))
    seen: list[ProcessHandle] = []

    result = runner.run(
        spec, "ignored", tmp_path, 10, tmp_path / "agent.log", on_started=seen.append,
    )

    assert result.success is True
    assert len(seen) == 1
    handle = seen[0]
    assert handle.pid > 0
    assert handle.process_group == handle.pid
    assert handle.start_identity.startswith("linux:")
    assert handle.started_at


def test_openclaw_runner_forwards_lifecycle_callback(tmp_path, monkeypatch):
    from unison.runners.openclaw import OpenClawRunner

    runner = OpenClawRunner()
    spec = AgentSpec("developer", "openclaw", "test", Path("prompts/developer.md"))
    expected = ProcessHandle(12, 12, "linux:99", "2026-07-17T00:00:00+00:00")
    seen = []

    def fake_base_run(self, spec, prompt, workdir, timeout, log_path, *, on_started=None):
        assert on_started is not None
        on_started(expected)
        return AgentResult(True, 0, 0.1, "", "", log_path)

    monkeypatch.setattr(BaseRunner, "run", fake_base_run)
    result = runner.run(spec, "prompt", tmp_path, 30, tmp_path / "agent.log", on_started=seen.append)

    assert result.success is True
    assert seen == [expected]


def test_crush_runner_forwards_lifecycle_callback(tmp_path, monkeypatch):
    from unison.runners.crush import CrushRunner

    runner = CrushRunner()
    spec = AgentSpec("developer", "crush", "test", Path("prompts/developer.md"))
    expected = ProcessHandle(12, 12, "linux:99", "2026-07-17T00:00:00+00:00")
    seen = []

    def fake_run_command(*_args, on_started=None, **_kwargs):
        assert on_started is not None
        on_started(expected)
        return AgentResult(False, 1, 0.1, "", "", tmp_path / "agent.log")

    monkeypatch.setattr(runner, "_run_command", fake_run_command)
    result = runner.run(spec, "prompt", tmp_path, 30, tmp_path / "agent.log", on_started=seen.append)

    assert result.success is False
    assert seen == [expected]
