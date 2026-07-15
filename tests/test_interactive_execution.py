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


class TestExecutionPolicyLoader:
    def test_omitted_execution_defaults_to_automatic_headless_bypass(self, tmp_path):
        spec = _load(tmp_path)

        assert spec.execution.selected_policy == "automatic"
        assert spec.execution.resolve_phase("dev_active") == "headless_bypass"

    def test_interactive_builtin_policy_resolves_all_phases_to_foreground(self, tmp_path):
        spec = _load(tmp_path, execution={"selected_policy": "interactive"})

        assert spec.execution.selected_policy == "interactive"
        assert spec.execution.resolve_phase("planning_active") == "foreground_manual"
        assert spec.execution.resolve_phase("dev_review") == "foreground_manual"

    def test_named_policy_phase_override_wins_over_default(self, tmp_path):
        spec = _load(
            tmp_path,
            execution={
                "selected_policy": "review-plan-first",
                "policies": {
                    "review-plan-first": {
                        "default": "headless_bypass",
                        "phases": {"planning_active": "foreground_manual"},
                    },
                },
            },
        )

        assert spec.execution.resolve_phase("planning_active") == "foreground_manual"
        assert spec.execution.resolve_phase("dev_active") == "headless_bypass"

    @pytest.mark.parametrize(
        ("execution", "message"),
        [
            ({"selected_policy": "missing"}, "selected_policy"),
            ({"selected_policy": 1}, "selected_policy must be a string"),
            ({"policies": []}, "policies must be a mapping"),
            ({"policies": {"interactive": {"default": "headless_bypass"}}}, "reserved"),
            ({"policies": {"bad name": {"default": "headless_bypass"}}}, "policy name"),
            ({"policies": {"safe": {"default": "unsafe"}}}, "default"),
            ({"policies": {"safe": {"default": "headless_bypass", "phases": {"not-a-phase": "foreground_manual"}}}}, "phase"),
            ({"policies": {"safe": {"default": "headless_bypass", "phases": {"dev_active": "unsafe"}}}}, "mode"),
        ],
    )
    def test_invalid_policy_config_fails_closed(self, tmp_path, execution, message):
        with pytest.raises(PipelineValidationError, match=message):
            _load(tmp_path, execution=execution)

    def test_legacy_herdr_execution_config_is_rejected(self, tmp_path):
        with pytest.raises(PipelineValidationError, match="execution.mode is no longer supported"):
            _load(tmp_path, execution={"mode": "interactive"})

    def test_legacy_herdr_interactive_block_is_rejected(self, tmp_path):
        with pytest.raises(PipelineValidationError, match="execution.interactive is no longer supported"):
            _load(tmp_path, execution={"interactive": {"backend": "herdr"}})

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"mode": "moa:analyze", "agents": {}}, "does not support MoA"),
            ({"dag": [{"name": "stage"}]}, "does not support DAG"),
            ({"parallel_dev": {"enabled": True}}, "does not support parallel_dev"),
            ({"mode": "chain", "chain": {"stages": [{"mode": "dev:quick"}]}}, "does not support chain"),
        ],
    )
    def test_foreground_policy_unsupported_combinations_fail_at_load(self, tmp_path, overrides, message):
        overrides["execution"] = {"selected_policy": "interactive"}

        with pytest.raises(PipelineValidationError, match=message):
            _load(tmp_path, **overrides)


class TestExecutionPolicyCli:
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

    def test_policy_override_is_ephemeral_and_reported(self, tmp_path, monkeypatch, capsys):
        import unison.cli as cli

        pipeline = tmp_path / "pipeline.yaml"
        original = yaml.safe_dump(_pipeline_data(), sort_keys=False)
        pipeline.write_text(original, encoding="utf-8")
        spec = self._spec(tmp_path)
        monkeypatch.setattr(cli, "_load", lambda path: (spec, MagicMock()))
        orchestrator = MagicMock()
        monkeypatch.setattr(cli, "Orchestrator", orchestrator)
        args = SimpleNamespace(
            pipeline=pipeline, project=None, dry_run=False, json=False,
            switch=None, model=None, save_pref=False,
            execution_policy="interactive", save_execution_policy=None,
        )

        assert cli._cmd_run(args) == 1
        assert pipeline.read_text(encoding="utf-8") == original
        assert "Effective execution policy: interactive" in capsys.readouterr().out
        orchestrator.assert_not_called()

    def test_policy_override_revalidates_effective_spec(self, tmp_path, monkeypatch, capsys):
        import unison.cli as cli

        spec = replace(self._spec(tmp_path), agents={
            "reviewer": replace(self._spec(tmp_path).agents["reviewer"], runtime="hermes"),
        })
        monkeypatch.setattr(cli, "_load", lambda path: (spec, MagicMock()))
        orchestrator = MagicMock()
        monkeypatch.setattr(cli, "Orchestrator", orchestrator)
        args = SimpleNamespace(
            pipeline=tmp_path / "pipeline.yaml", project=None, dry_run=False,
            json=False, switch=None, model=None, save_pref=False,
            execution_policy="interactive", save_execution_policy=None,
        )

        assert cli._cmd_run(args) == 1
        assert "only supports claude and codex" in capsys.readouterr().err
        orchestrator.assert_not_called()

    def test_unknown_policy_override_fails_before_dispatch(self, tmp_path, monkeypatch, capsys):
        import unison.cli as cli

        monkeypatch.setattr(cli, "_load", lambda path: (self._spec(tmp_path), MagicMock()))
        orchestrator = MagicMock()
        monkeypatch.setattr(cli, "Orchestrator", orchestrator)
        args = SimpleNamespace(
            pipeline=tmp_path / "pipeline.yaml", project=None, dry_run=False,
            json=False, switch=None, model=None, save_pref=False,
            execution_policy="missing", save_execution_policy=None,
        )

        assert cli._cmd_run(args) == 1
        assert "selected_policy 'missing'" in capsys.readouterr().err
        orchestrator.assert_not_called()

    def test_conflicting_execution_policy_flags_fail_before_dispatch(self, tmp_path, monkeypatch, capsys):
        import unison.cli as cli

        monkeypatch.setattr(cli, "_load", lambda path: (self._spec(tmp_path), MagicMock()))
        orchestrator = MagicMock()
        monkeypatch.setattr(cli, "Orchestrator", orchestrator)
        args = SimpleNamespace(
            pipeline=tmp_path / "pipeline.yaml", project=None, dry_run=False,
            json=False, switch=None, model=None, save_pref=False,
            execution_policy="automatic", save_execution_policy="interactive",
        )

        assert cli._cmd_run(args) == 1
        assert "must match" in capsys.readouterr().err
        orchestrator.assert_not_called()

    def test_save_execution_policy_persists_only_after_validation(self, tmp_path):
        import unison.cli as cli

        pipeline = tmp_path / "pipeline.yaml"
        pipeline.write_text(yaml.safe_dump(_pipeline_data(), sort_keys=False), encoding="utf-8")

        cli._save_execution_policy(pipeline, "automatic")

        assert yaml.safe_load(pipeline.read_text(encoding="utf-8"))["execution"] == {
            "selected_policy": "automatic"
        }

    def test_save_execution_policy_rejects_unknown_policy_before_replace(self, tmp_path):
        import unison.cli as cli

        pipeline = tmp_path / "pipeline.yaml"
        original = yaml.safe_dump(_pipeline_data(), sort_keys=False)
        pipeline.write_text(original, encoding="utf-8")

        with pytest.raises(PipelineValidationError, match="selected_policy 'missing'"):
            cli._save_execution_policy(pipeline, "missing")

        assert pipeline.read_text(encoding="utf-8") == original

    def test_unauthorized_run_does_not_save_execution_policy(self, tmp_path, monkeypatch):
        import unison.cli as cli

        pipeline = tmp_path / "pipeline.yaml"
        original = yaml.safe_dump(_pipeline_data(), sort_keys=False)
        pipeline.write_text(original, encoding="utf-8")
        denied_spec = replace(self._spec(tmp_path), who_can_run=["discord:123"])
        monkeypatch.setattr(cli, "_load", lambda path: (denied_spec, MagicMock()))
        save = MagicMock()
        monkeypatch.setattr(cli, "_save_execution_policy", save)
        args = SimpleNamespace(
            pipeline=pipeline, project=None, dry_run=False, json=False,
            switch=None, model=None, save_pref=False,
            execution_policy=None, save_execution_policy="automatic",
        )

        assert cli._cmd_run(args) == 3
        save.assert_not_called()
        assert pipeline.read_text(encoding="utf-8") == original

    def test_save_execution_policy_rejects_invalid_source_yaml(self, tmp_path):
        import unison.cli as cli

        pipeline = tmp_path / "pipeline.yaml"
        pipeline.write_text("execution: [", encoding="utf-8")

        with pytest.raises(ValueError, match="pipeline YAML is invalid"):
            cli._save_execution_policy(pipeline, "automatic")

    def test_save_execution_policy_rejects_non_mapping_source_yaml(self, tmp_path):
        import unison.cli as cli

        pipeline = tmp_path / "pipeline.yaml"
        pipeline.write_text("- not-a-pipeline", encoding="utf-8")

        with pytest.raises(ValueError, match="pipeline YAML must be a mapping"):
            cli._save_execution_policy(pipeline, "automatic")

    def test_dry_run_reports_selected_policy(self, tmp_path, monkeypatch, capsys):
        import unison.cli as cli

        spec = self._spec(tmp_path)
        loader = MagicMock()
        loader.mode.return_value = "inspect-only"
        monkeypatch.setattr(cli, "_load", lambda path: (spec, loader))

        assert cli._cmd_dry_run(SimpleNamespace(pipeline=tmp_path / "pipeline.yaml")) == 0
        assert "OK  execution.selected_policy = automatic" in capsys.readouterr().out
