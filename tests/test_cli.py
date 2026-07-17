"""Tests for cli.py — CLI entry point."""
import argparse
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from unison.cli import main, _cmd_reconcile, _cmd_resume, _cmd_run, _cmd_webui
from unison.interfaces import AgentSpec, PipelineSpec, ProjectConfig, World
from unison.state import State




class TestCliAuthorization:
    @staticmethod
    def _args(tmp_path):
        return SimpleNamespace(
            pipeline=tmp_path / "pipeline.yaml",
            project=None,
            dry_run=False,
            json=False,
            switch=None,
            model=None,
            save_pref=False,
        )

    @staticmethod
    def _spec(tmp_path, principals):
        return PipelineSpec(
            version="2.0",
            world=World(root=tmp_path),
            agents={},
            project=ProjectConfig(),
            mode="moa:analyze",
            who_can_run=principals,
        )

    def test_cli_denied_before_orchestrator_construction(self, tmp_path, monkeypatch):
        import unison.cli as cli

        spec = self._spec(tmp_path, ["discord:123"])
        monkeypatch.setattr(cli, "_load", lambda path: (spec, MagicMock()))
        orchestrator = MagicMock()
        monkeypatch.setattr(cli, "Orchestrator", orchestrator)

        assert _cmd_run(self._args(tmp_path)) == 3
        orchestrator.assert_not_called()
        records = [
            json.loads(line)
            for line in (tmp_path / "observer" / "audit.jsonl").read_text().splitlines()
        ]
        assert records[-1]["event"] == "run_authorization"
        assert records[-1]["principal"] == "cli"
        assert records[-1]["allowed"] is False
        assert records[-1]["reason"] == "principal_not_listed"
        assert records[-1]["configured"] == ["discord:123"]

    def test_cli_allowed_and_audited(self, tmp_path, monkeypatch):
        import unison.cli as cli
        from unison.state import State

        spec = self._spec(tmp_path, ["cli", "discord:123"])
        monkeypatch.setattr(cli, "_load", lambda path: (spec, MagicMock()))
        monkeypatch.setattr(cli, "_check_tools", lambda spec: True)
        state = State(phase="done")
        runner = MagicMock()
        runner.run.return_value = state
        monkeypatch.setattr(cli, "Orchestrator", lambda **kwargs: runner)

        assert _cmd_run(self._args(tmp_path)) == 0
        runner.run.assert_called_once()
        record = json.loads(
            (tmp_path / "observer" / "audit.jsonl").read_text().splitlines()[-1]
        )
        assert record["allowed"] is True
        assert record["reason"] == "allowed"
        assert record["principal"] == "cli"

    def test_authorization_audit_failure_blocks_run(self, tmp_path, monkeypatch):
        import unison.cli as cli
        from unison.auth import RunAuthorizationError

        spec = self._spec(tmp_path, ["cli"])
        monkeypatch.setattr(cli, "_load", lambda path: (spec, MagicMock()))
        monkeypatch.setattr(
            cli,
            "authorize_run",
            MagicMock(side_effect=RunAuthorizationError("audit unavailable")),
        )
        orchestrator = MagicMock()
        monkeypatch.setattr(cli, "Orchestrator", orchestrator)

        assert main(["run", "--pipeline", str(tmp_path / "pipeline.yaml")]) == 3
        orchestrator.assert_not_called()

    def test_non_cli_principals_fail_closed_without_bridge(self, tmp_path):
        from unison.auth import authorize_run

        spec = self._spec(tmp_path, ["hermes:session-1", "discord:123"])
        assert authorize_run(spec, "cli") is False
        assert authorize_run(spec, "hermes:session-1") is False
        assert authorize_run(spec, "discord:123") is False
        records = [
            json.loads(line)
            for line in (tmp_path / "observer" / "audit.jsonl").read_text().splitlines()
        ]
        assert records[0]["reason"] == "principal_not_listed"
        assert records[1]["reason"] == "principal_source_untrusted"
        assert records[2]["reason"] == "principal_source_untrusted"


class TestAgentOverrides:
    @staticmethod
    def _args(tmp_path, *, switch=None, model=None, save_pref=False):
        return SimpleNamespace(
            pipeline=tmp_path / "pipeline.yaml",
            project=None,
            dry_run=False,
            json=False,
            switch=switch,
            model=model,
            save_pref=save_pref,
        )

    @staticmethod
    def _spec(tmp_path):
        return PipelineSpec(
            version="2.0",
            world=World(root=tmp_path),
            agents={
                "reviewer": AgentSpec(
                    role="reviewer",
                    pipeline_role="reviewer",
                    runtime="codex",
                    model="old-model",
                    system_prompt_path=Path("prompts/reviewer.md"),
                ),
            },
            project=ProjectConfig(),
            mode="inspect-only",
        )

    def test_switch_and_model_reach_orchestrator_spec(self, tmp_path, monkeypatch):
        import unison.cli as cli
        from unison.state import State

        spec = self._spec(tmp_path)
        monkeypatch.setattr(cli, "_load", lambda path: (spec, MagicMock()))
        monkeypatch.setattr(cli, "_check_tools", lambda spec: True)
        captured = {}

        class FakeOrchestrator:
            def __init__(self, *, spec, dry_run):
                captured["spec"] = spec

            def run(self):
                return State(phase="done")

        monkeypatch.setattr(cli, "Orchestrator", FakeOrchestrator)

        result = _cmd_run(self._args(
            tmp_path,
            switch="reviewer:claude",
            model="reviewer:new-model",
        ))

        assert result == 0
        assert captured["spec"].agents["reviewer"].runtime == "claude"
        assert captured["spec"].agents["reviewer"].model == "new-model"

    def test_unknown_agent_override_fails_before_orchestrator(
        self, tmp_path, monkeypatch, capsys,
    ):
        import unison.cli as cli

        spec = self._spec(tmp_path)
        monkeypatch.setattr(cli, "_load", lambda path: (spec, MagicMock()))
        orchestrator = MagicMock()
        monkeypatch.setattr(cli, "Orchestrator", orchestrator)

        result = _cmd_run(self._args(tmp_path, switch="missing:claude"))

        assert result == 1
        assert "unknown agent key" in capsys.readouterr().err
        orchestrator.assert_not_called()

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("switch", "badformat", "SWITCH ERROR"),
            ("model", "badformat", "MODEL ERROR"),
        ],
    )
    def test_malformed_override_is_rejected(
        self, tmp_path, monkeypatch, capsys, field, value, message,
    ):
        import unison.cli as cli

        spec = self._spec(tmp_path)
        monkeypatch.setattr(cli, "_load", lambda path: (spec, MagicMock()))
        orchestrator = MagicMock()
        monkeypatch.setattr(cli, "Orchestrator", orchestrator)
        kwargs = {field: value}

        result = _cmd_run(self._args(tmp_path, **kwargs))

        assert result == 1
        assert message in capsys.readouterr().err
        orchestrator.assert_not_called()

    def test_unknown_model_agent_fails_before_orchestrator(
        self, tmp_path, monkeypatch, capsys,
    ):
        import unison.cli as cli

        spec = self._spec(tmp_path)
        monkeypatch.setattr(cli, "_load", lambda path: (spec, MagicMock()))
        orchestrator = MagicMock()
        monkeypatch.setattr(cli, "Orchestrator", orchestrator)

        result = _cmd_run(self._args(tmp_path, model="missing:new-model"))

        assert result == 1
        assert "unknown agent key" in capsys.readouterr().err
        orchestrator.assert_not_called()

    def test_model_only_override_reaches_tool_check_and_orchestrator(
        self, tmp_path, monkeypatch,
    ):
        import unison.cli as cli
        from unison.state import State

        spec = self._spec(tmp_path)
        monkeypatch.setattr(cli, "_load", lambda path: (spec, MagicMock()))
        checked = {}

        def check_tools(effective_spec):
            checked["model"] = effective_spec.agents["reviewer"].model
            return True

        monkeypatch.setattr(cli, "_check_tools", check_tools)
        captured = {}

        class FakeOrchestrator:
            def __init__(self, *, spec, dry_run):
                captured["model"] = spec.agents["reviewer"].model

            def run(self):
                return State(phase="done")

        monkeypatch.setattr(cli, "Orchestrator", FakeOrchestrator)

        result = _cmd_run(self._args(tmp_path, model="reviewer:new-model"))

        assert result == 0
        assert checked["model"] == "new-model"
        assert captured["model"] == "new-model"

    def test_save_pref_atomically_persists_effective_agent_values(
        self, tmp_path, monkeypatch,
    ):
        import yaml
        import unison.cli as cli
        from unison.state import State

        pipeline = tmp_path / "pipeline.yaml"
        pipeline.write_text(yaml.safe_dump({
            "version": "2.0",
            "project_root": ".",
            "mode": "inspect-only",
            "agents": {
                "reviewer": {
                    "role": "reviewer",
                    "pipeline_role": "reviewer",
                    "runtime": "codex",
                    "model": "old-model",
                    "system_prompt_path": "prompts/reviewer.md",
                },
            },
        }), encoding="utf-8")
        spec = self._spec(tmp_path)
        monkeypatch.setattr(cli, "_load", lambda path: (spec, MagicMock()))
        monkeypatch.setattr(cli, "_check_tools", lambda spec: True)
        runner = MagicMock()
        runner.run.return_value = State(phase="done")
        monkeypatch.setattr(cli, "Orchestrator", lambda **kwargs: runner)

        result = _cmd_run(SimpleNamespace(
            pipeline=pipeline,
            project=None,
            dry_run=False,
            json=False,
            switch="reviewer:claude",
            model="reviewer:new-model",
            save_pref=True,
        ))

        assert result == 0
        saved = yaml.safe_load(pipeline.read_text(encoding="utf-8"))
        assert saved["agents"]["reviewer"]["runtime"] == "claude"
        assert saved["agents"]["reviewer"]["model"] == "new-model"
        assert list(tmp_path.glob(".pipeline.yaml.*.tmp")) == []

    def test_denied_run_does_not_persist_preferences(self, tmp_path, monkeypatch):
        import yaml
        import unison.cli as cli

        pipeline = tmp_path / "pipeline.yaml"
        original = yaml.safe_dump({
            "version": "2.0",
            "agents": {
                "reviewer": {
                    "role": "reviewer",
                    "runtime": "codex",
                    "model": "old-model",
                },
            },
        }, sort_keys=False)
        pipeline.write_text(original, encoding="utf-8")
        spec = replace(self._spec(tmp_path), who_can_run=["discord:123"])
        monkeypatch.setattr(cli, "_load", lambda path: (spec, MagicMock()))
        orchestrator = MagicMock()
        monkeypatch.setattr(cli, "Orchestrator", orchestrator)

        result = _cmd_run(SimpleNamespace(
            pipeline=pipeline,
            project=None,
            dry_run=False,
            json=False,
            switch="reviewer:claude",
            model=None,
            save_pref=True,
        ))

        assert result == 3
        assert pipeline.read_text(encoding="utf-8") == original
        orchestrator.assert_not_called()

    def test_registered_runtime_override_reaches_tool_preflight(self, tmp_path, monkeypatch, capsys):
        import unison.cli as cli

        spec = self._spec(tmp_path)
        monkeypatch.setattr(cli, "_load", lambda path: (spec, MagicMock()))
        orchestrator = MagicMock()
        monkeypatch.setattr(cli, "Orchestrator", orchestrator)

        result = _cmd_run(self._args(tmp_path, switch="reviewer:crush"))

        assert result == 1
        assert "TOOL CHECK: CRUSH NOT FOUND" in capsys.readouterr().out
        orchestrator.assert_not_called()

    def test_save_pref_write_failure_does_not_start_or_replace_pipeline(
        self, tmp_path, monkeypatch,
    ):
        import yaml
        import unison.cli as cli

        pipeline = tmp_path / "pipeline.yaml"
        original = yaml.safe_dump({
            "version": "2.0",
            "agents": {
                "reviewer": {
                    "role": "reviewer",
                    "runtime": "codex",
                    "model": "old-model",
                },
            },
        }, sort_keys=False)
        pipeline.write_text(original, encoding="utf-8")
        spec = self._spec(tmp_path)
        monkeypatch.setattr(cli, "_load", lambda path: (spec, MagicMock()))
        monkeypatch.setattr(cli.os, "replace", MagicMock(side_effect=OSError("disk failure")))
        orchestrator = MagicMock()
        monkeypatch.setattr(cli, "Orchestrator", orchestrator)

        result = _cmd_run(SimpleNamespace(
            pipeline=pipeline,
            project=None,
            dry_run=False,
            json=False,
            switch="reviewer:claude",
            model=None,
            save_pref=True,
        ))

        assert result == 1
        assert pipeline.read_text(encoding="utf-8") == original
        assert list(tmp_path.glob(".pipeline.yaml.*.tmp")) == []
        orchestrator.assert_not_called()


class TestForegroundReconcileCli:
    def _spec(self, tmp_path):
        return PipelineSpec(
            version="2.0",
            world=World(root=tmp_path),
            agents={},
            project=ProjectConfig(),
            mode="moa:analyze",
            pipeline_name="foreground-test",
        )

    def _write_states(self, spec, state):
        state.atomic_write(spec.world.state_file)
        from unison.world import RunContext
        ctx = RunContext(
            project_id=spec.world.project_id,
            pipeline_key=spec.world.pipeline_key(state.pipeline_name),
            run_id=state.run_id,
            pipeline_name=state.pipeline_name,
        )
        state.atomic_write(spec.world.run_state_file(ctx))

    def test_reconcile_loads_canonical_run_before_continuation(self, tmp_path, monkeypatch):
        import unison.cli as cli
        from unison.state import ForegroundInvocationState

        spec = self._spec(tmp_path)
        state = State(
            phase="dev_active", run_id="resume-run", pipeline_name=spec.pipeline_name,
            active_foreground_invocation=ForegroundInvocationState(
                invocation_id="foreground-id", phase="dev_active", role="developer",
                runtime="claude", wrapper_pid=None, wrapper_start_identity=None,
                launcher_pid=1, artifact_dir=str(tmp_path / "artifact"),
                result_path=str(tmp_path / "artifact" / "result.json"),
                output_path=str(tmp_path / "artifact" / "output.log"),
                started_at="2026-07-15T00:00:00Z", last_heartbeat_observed_at=None,
            ),
        )
        self._write_states(spec, state)
        monkeypatch.setattr(cli, "_load", lambda _path: (spec, MagicMock()))
        runner = MagicMock()
        runner.reconcile_foreground.return_value = True
        runner.run.return_value = State(phase="done")
        monkeypatch.setattr(cli, "Orchestrator", lambda **_kwargs: runner)

        assert _cmd_reconcile(SimpleNamespace(pipeline=tmp_path / "pipeline.yaml", json=False)) == 0
        runner.load_reconcile_state.assert_called_once()
        loaded = runner.load_reconcile_state.call_args.args[0]
        assert loaded.run_id == "resume-run"
        runner.reconcile_foreground.assert_called_once()
        runner.run.assert_called_once()

    def test_reconcile_refuses_missing_canonical_run_state(self, tmp_path, monkeypatch, capsys):
        import unison.cli as cli

        spec = self._spec(tmp_path)
        State(
            phase="dev_active", run_id="resume-run", pipeline_name=spec.pipeline_name,
        ).atomic_write(spec.world.state_file)
        monkeypatch.setattr(cli, "_load", lambda _path: (spec, MagicMock()))
        orchestrator = MagicMock()
        monkeypatch.setattr(cli, "Orchestrator", orchestrator)

        assert _cmd_reconcile(SimpleNamespace(pipeline=tmp_path / "pipeline.yaml", json=False)) == 1
        assert "canonical run state is missing" in capsys.readouterr().err
        orchestrator.assert_not_called()

    def test_main_routes_reconcile_command(self, tmp_path, monkeypatch):
        import unison.cli as cli

        called = []
        monkeypatch.setattr(cli, "_cmd_reconcile", lambda args: called.append(args.pipeline) or 0)
        monkeypatch.setitem(cli._HANDLERS, "reconcile", cli._cmd_reconcile)
        pipeline = tmp_path / "pipeline.yaml"

        assert main(["reconcile", "--pipeline", str(pipeline)]) == 0
        assert called == [pipeline]


    def test_resume_loads_canonical_run_before_replacement(self, tmp_path, monkeypatch):
        import unison.cli as cli
        from unison.state import ForegroundInvocationState

        spec = self._spec(tmp_path)
        state = State(
            phase="dev_active", run_id="resume-run", pipeline_name=spec.pipeline_name,
            halt_signal=True,
            halt_reason="foreground interrupted_unverified: heartbeat stale",
            active_foreground_invocation=ForegroundInvocationState(
                invocation_id="foreground-id", phase="dev_active", role="developer",
                runtime="claude", wrapper_pid=None, wrapper_start_identity=None,
                launcher_pid=1, artifact_dir=str(tmp_path / "artifact"),
                result_path=str(tmp_path / "artifact" / "result.json"),
                output_path=str(tmp_path / "artifact" / "output.log"),
                started_at="2026-07-15T00:00:00Z", last_heartbeat_observed_at=None,
            ),
        )
        self._write_states(spec, state)
        monkeypatch.setattr(cli, "_load", lambda _path: (spec, MagicMock()))
        runner = MagicMock()
        runner.run.return_value = State(phase="done")
        monkeypatch.setattr(cli, "Orchestrator", lambda **_kwargs: runner)

        assert _cmd_resume(SimpleNamespace(pipeline=tmp_path / "pipeline.yaml", json=False)) == 0
        runner.load_resume_state.assert_called_once()
        assert runner.load_resume_state.call_args.args[0].run_id == "resume-run"
        runner.run.assert_called_once()

    def test_main_routes_resume_command(self, tmp_path, monkeypatch):
        import unison.cli as cli

        called = []
        monkeypatch.setattr(cli, "_cmd_resume", lambda args: called.append(args.pipeline) or 0)
        monkeypatch.setitem(cli._HANDLERS, "resume", cli._cmd_resume)
        pipeline = tmp_path / "pipeline.yaml"

        assert main(["resume", "--pipeline", str(pipeline)]) == 0
        assert called == [pipeline]


class TestWebUiTokenTransport:
    def test_webui_reads_token_from_environment(self, tmp_path, monkeypatch):
        import unison.webui as webui

        serve = MagicMock()
        monkeypatch.setattr(webui, "serve", serve)
        monkeypatch.setenv("UNISON_WEBUI_TOKEN", "environment-token")
        args = argparse.Namespace(project=tmp_path, port=9099, token="")

        assert _cmd_webui(args) == 0
        serve.assert_called_once_with(
            str(tmp_path), port=9099, token="environment-token"
        )

    def test_explicit_token_remains_backward_compatible(self, tmp_path, monkeypatch):
        import unison.webui as webui

        serve = MagicMock()
        monkeypatch.setattr(webui, "serve", serve)
        monkeypatch.setenv("UNISON_WEBUI_TOKEN", "environment-token")
        args = argparse.Namespace(
            project=tmp_path, port=9099, token="explicit-token"
        )

        assert _cmd_webui(args) == 0
        serve.assert_called_once_with(str(tmp_path), port=9099, token="explicit-token")
