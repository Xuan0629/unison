"""Phase A tests for the opt-in interactive execution contract."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

from unison.interfaces import AgentSpec, PipelineSpec, ProjectConfig, World
from unison.pipeline import PipelineLoader, PipelineValidationError


def _pipeline_data(**overrides):
    data = {
        "version": "2.0",
        "project_root": ".",
        "mode": "dev:quick",
        "agents": {
            "developer": {
                "role": "developer",
                "pipeline_role": "developer",
                "runtime": "claude",
                "model": "default",
                "system_prompt_path": "prompts/developer.md",
            },
            "reviewer": {
                "role": "reviewer",
                "pipeline_role": "reviewer",
                "runtime": "codex",
                "model": "default",
                "system_prompt_path": "prompts/reviewer.md",
            },
        },
    }
    data.update(overrides)
    return data


def _load(tmp_path: Path, **overrides):
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(yaml.safe_dump(_pipeline_data(**overrides)), encoding="utf-8")
    return PipelineLoader().load(pipeline)


class TestInteractiveExecutionLoader:
    def test_omitted_execution_defaults_to_headless(self, tmp_path):
        spec = _load(tmp_path)

        assert spec.execution.mode == "headless"
        assert spec.execution.interactive.backend == "herdr"

    def test_interactive_claude_codex_sequential_pipeline_loads(self, tmp_path):
        spec = _load(
            tmp_path,
            execution={
                "mode": "interactive",
                "interactive": {"session_name": "local-review"},
            },
        )

        assert spec.execution.mode == "interactive"
        assert spec.execution.interactive.session_name == "local-review"

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("mode", "unsafe", "execution.mode"),
            ("backend", "socket", "execution.interactive.backend"),
            ("approval_timeout_seconds", -1, "approval_timeout_seconds"),
            ("pause_pipeline_timeout_while_blocked", "yes", "pause_pipeline_timeout_while_blocked"),
        ],
    )
    def test_invalid_interactive_config_fails_closed(self, tmp_path, field, value, message):
        interactive = {"backend": "herdr"}
        execution = {"mode": "interactive", "interactive": interactive}
        if field == "mode":
            execution["mode"] = value
        else:
            interactive[field] = value

        with pytest.raises(PipelineValidationError, match=message):
            _load(tmp_path, execution=execution)

    @pytest.mark.parametrize("runtime", ["hermes", "openclaw"])
    def test_interactive_unsupported_runtime_fails_at_load(self, tmp_path, runtime):
        overrides = {"agents": {
            "developer": {
                "role": "developer", "pipeline_role": "developer",
                "runtime": runtime, "model": "default",
                "system_prompt_path": "prompts/developer.md",
            },
            "reviewer": {
                "role": "reviewer", "pipeline_role": "reviewer",
                "runtime": "codex", "model": "default",
                "system_prompt_path": "prompts/reviewer.md",
            },
        }, "execution": {"mode": "interactive"}}

        with pytest.raises(PipelineValidationError, match="only supports claude and codex"):
            _load(tmp_path, **overrides)

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"mode": "moa:analyze", "agents": {}}, "does not support MoA"),
            ({"dag": [{"name": "stage"}]}, "does not support DAG"),
            ({"parallel_dev": {"enabled": True}}, "does not support parallel_dev"),
            ({"mode": "chain", "chain": {"stages": [{"mode": "dev:quick"}]}}, "does not support chain"),
        ],
    )
    def test_interactive_unsupported_combinations_fail_at_load(self, tmp_path, overrides, message):
        overrides["execution"] = {"mode": "interactive"}

        with pytest.raises(PipelineValidationError, match=message):
            _load(tmp_path, **overrides)


class TestInteractiveExecutionCli:
    def _spec(self, tmp_path: Path) -> PipelineSpec:
        agent = AgentSpec(
            role="reviewer",
            pipeline_role="reviewer",
            runtime="codex",
            model="default",
            system_prompt_path=Path("prompts/reviewer.md"),
        )
        return PipelineSpec(
            version="2.0",
            world=World(root=tmp_path),
            agents={"reviewer": agent},
            project=ProjectConfig(),
            mode="inspect-only",
        )

    def test_interactive_override_is_ephemeral_and_reported(self, tmp_path, monkeypatch, capsys):
        import unison.cli as cli

        pipeline = tmp_path / "pipeline.yaml"
        original = yaml.safe_dump(_pipeline_data(), sort_keys=False)
        pipeline.write_text(original, encoding="utf-8")
        spec = self._spec(tmp_path)
        monkeypatch.setattr(cli, "_load", lambda path: (spec, MagicMock()))
        orchestrator = MagicMock()
        monkeypatch.setattr(cli, "Orchestrator", orchestrator)
        args = SimpleNamespace(
            pipeline=pipeline,
            project=None,
            dry_run=False,
            json=False,
            switch=None,
            model=None,
            save_pref=False,
            interactive=True,
        )

        assert cli._cmd_run(args) == 1
        assert pipeline.read_text(encoding="utf-8") == original
        assert "Effective execution mode: interactive" in capsys.readouterr().out
        orchestrator.assert_not_called()

    def test_interactive_override_revalidates_effective_spec(self, tmp_path, monkeypatch, capsys):
        import unison.cli as cli

        spec = replace(self._spec(tmp_path), agents={
            "reviewer": replace(self._spec(tmp_path).agents["reviewer"], runtime="hermes"),
        })
        monkeypatch.setattr(cli, "_load", lambda path: (spec, MagicMock()))
        orchestrator = MagicMock()
        monkeypatch.setattr(cli, "Orchestrator", orchestrator)
        args = SimpleNamespace(
            pipeline=tmp_path / "pipeline.yaml",
            project=None,
            dry_run=False,
            json=False,
            switch=None,
            model=None,
            save_pref=False,
            interactive=True,
        )

        assert cli._cmd_run(args) == 1
        assert "only supports claude and codex" in capsys.readouterr().err
        orchestrator.assert_not_called()

    def test_dry_run_reports_headless_execution_mode(self, tmp_path, monkeypatch, capsys):
        import unison.cli as cli

        spec = self._spec(tmp_path)
        loader = MagicMock()
        loader.mode.return_value = "inspect-only"
        monkeypatch.setattr(cli, "_load", lambda path: (spec, loader))

        assert cli._cmd_dry_run(SimpleNamespace(pipeline=tmp_path / "pipeline.yaml")) == 0
        assert "OK  execution.mode = headless" in capsys.readouterr().out
