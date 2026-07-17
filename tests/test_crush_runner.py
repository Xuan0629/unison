"""Contract tests for the constrained headless Crush runtime."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from unison.interfaces import AgentResult, AgentSpec
from unison.runners.base import BaseRunner
from unison.runners.crush import CrushRunner


def _spec(model: str = "unison-minimax-cn/MiniMax-M3") -> AgentSpec:
    return AgentSpec(
        role="developer",
        runtime="crush",
        model=model,
        system_prompt_path=Path("prompts/developer.md"),
    )


class TestCrushRunner:
    def test_build_command_uses_isolated_state_and_explicit_model(self, tmp_path):
        runner = CrushRunner(binary="crush")
        log_path = tmp_path / "developer.log"

        cmd = runner._build_command_for_state(
            _spec(),
            "Implement the requested change.",
            tmp_path,
            log_path,
            tmp_path / "state",
        )

        assert cmd == [
            "crush", "run", "--quiet",
            "--cwd", str(tmp_path),
            "--data-dir", str(tmp_path / "state"),
            "--model", "unison-minimax-cn/MiniMax-M3",
            "Implement the requested change.",
        ]
        assert "--continue" not in cmd
        assert "--session" not in cmd

    def test_extract_usage_rejects_session_meta_without_cache_breakdown(self):
        usage = CrushRunner.extract_usage({
            "prompt_tokens": 8,
            "completion_tokens": 2,
            "total_tokens": 10,
            "cost": 0,
        })

        assert usage.token_provenance == "unavailable"
        assert usage.cost_provenance == "unavailable"

    def test_extract_usage_accepts_complete_consistent_breakdown_without_cost_claim(self):
        usage = CrushRunner.extract_usage({
            "prompt_tokens": 8,
            "completion_tokens": 2,
            "cache_read_tokens": 1,
            "total_tokens": 11,
            "cost": 0,
        })

        assert usage.token_provenance == "actual"
        assert usage.input_tokens == 8
        assert usage.output_tokens == 2
        assert usage.cache_read_tokens == 1
        assert usage.total_tokens == 11
        assert usage.cost_provenance == "unavailable"
        assert usage.cost_usd is None

    def test_run_queries_only_its_isolated_session_metadata(self, tmp_path, monkeypatch):
        runner = CrushRunner(binary="crush")
        log_path = tmp_path / "developer.log"
        state_dir = tmp_path / "state"
        monkeypatch.setattr(runner, "_new_state_dir", lambda _log: state_dir)

        def fake_base_run(self, spec, prompt, workdir, timeout, log_path):
            return AgentResult(
                success=True,
                exit_code=0,
                duration=0.1,
                stdout_tail="done",
                stderr_tail="",
                log_path=log_path,
            )

        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if command[2] == "list":
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps([{"uuid": "session-1"}]),
                    stderr="",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"meta": {
                    "prompt_tokens": 8,
                    "completion_tokens": 2,
                    "total_tokens": 10,
                    "cost": 0,
                }}),
                stderr="",
            )

        monkeypatch.setattr(BaseRunner, "run", fake_base_run)
        monkeypatch.setattr(
            runner,
            "_run_command",
            lambda command, prompt, workdir, timeout, log_path: AgentResult(
                success=True,
                exit_code=0,
                duration=0.1,
                stdout_tail="done",
                stderr_tail="",
                log_path=log_path,
            ),
        )
        monkeypatch.setattr("unison.runners.crush.subprocess.run", fake_run)

        result = runner.run(_spec(), "prompt", tmp_path, 30, log_path)

        assert result.success is True
        assert result.usage.token_provenance == "unavailable"
        assert json.loads((state_dir / "unison-session.json").read_text()) == {
            "session_uuid": "session-1"
        }
        assert calls == [
            ["crush", "session", "list", "--cwd", str(tmp_path), "--data-dir", str(state_dir), "--json"],
            ["crush", "session", "show", "session-1", "--cwd", str(tmp_path), "--data-dir", str(state_dir), "--json"],
        ]

    def test_timeout_requests_sigint_before_forced_kill(self, monkeypatch):
        runner = CrushRunner()
        proc = subprocess.Popen(["true"], text=True)
        proc.wait()
        signals: list[int] = []

        monkeypatch.setattr("unison.runners.crush.os.getpgid", lambda _pid: 123)
        monkeypatch.setattr("unison.runners.crush.os.killpg", lambda _pgid, sig: signals.append(sig))
        monkeypatch.setattr("unison.runners.crush.CrushRunner._wait_after_interrupt", lambda *_: False)

        runner._terminate_on_timeout(proc, 456)

        assert signals == [2, 9]
