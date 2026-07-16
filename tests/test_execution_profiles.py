"""Execution-profile loading and Hermes command forwarding tests."""
from pathlib import Path

import pytest
import yaml

from unison.interfaces import AgentSpec
from unison.pipeline import PipelineLoader, PipelineValidationError
from unison.runners.hermes import HermesRunner


def _write_pipeline(tmp_path: Path, payload: dict) -> Path:
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return pipeline


def _reviewer_agent(**overrides: object) -> dict:
    agent = {
        "role": "reviewer",
        "pipeline_role": "reviewer",
        "runtime": "hermes",
    }
    agent.update(overrides)
    return agent


def test_profile_resolves_hermes_model_prompt_skills_and_toolsets(tmp_path: Path) -> None:
    pipeline = _write_pipeline(tmp_path, {
        "version": "2.0",
        "mode": "inspect-only",
        "profiles": {
            "focused-review": {
                "system_prompt_path": "prompts/reviewer.md",
                "model": "gpt-5.6-sol",
                "skills": ["test-driven-development"],
                "toolsets": ["terminal", "file"],
            },
        },
        "agents": {"reviewer": _reviewer_agent(profile="focused-review")},
    })

    spec = PipelineLoader().load(pipeline).agents["reviewer"]

    assert spec.model == "gpt-5.6-sol"
    assert spec.system_prompt_path == Path("prompts/reviewer.md")
    assert spec.skills == ("test-driven-development",)
    assert spec.toolsets == ("terminal", "file")


def test_profile_rejects_scope_fields_for_non_hermes_runtime(tmp_path: Path) -> None:
    pipeline = _write_pipeline(tmp_path, {
        "version": "2.0",
        "mode": "inspect-only",
        "profiles": {
            "scoped": {
                "system_prompt_path": "prompts/reviewer.md",
                "skills": ["test-driven-development"],
            },
        },
        "agents": {
            "reviewer": {
                **_reviewer_agent(profile="scoped"),
                "runtime": "codex",
            },
        },
    })

    with pytest.raises(PipelineValidationError, match="only supported by runtime 'hermes'"):
        PipelineLoader().load(pipeline)


def test_profile_rejects_unknown_fields_and_agent_conflicts(tmp_path: Path) -> None:
    pipeline = _write_pipeline(tmp_path, {
        "version": "2.0",
        "mode": "inspect-only",
        "profiles": {
            "invalid": {
                "system_prompt_path": "prompts/reviewer.md",
                "memory": "disabled",
            },
        },
        "agents": {"reviewer": _reviewer_agent(profile="invalid")},
    })

    with pytest.raises(PipelineValidationError, match="unknown fields: memory"):
        PipelineLoader().load(pipeline)

    conflict = _write_pipeline(tmp_path, {
        "version": "2.0",
        "mode": "inspect-only",
        "profiles": {
            "review": {
                "system_prompt_path": "prompts/reviewer.md",
                "model": "gpt-5.6-sol",
            },
        },
        "agents": {
            "reviewer": _reviewer_agent(profile="review", model="other-model"),
        },
    })

    with pytest.raises(PipelineValidationError, match="profile conflicts with agent field: model"):
        PipelineLoader().load(conflict)


def test_hermes_runner_forwards_profile_scopes_and_preserves_default_skills() -> None:
    spec = AgentSpec(
        role="reviewer",
        runtime="hermes",
        model="gpt-5.6-sol",
        system_prompt_path=Path("prompts/reviewer.md"),
        skills=("test-driven-development",),
        toolsets=("terminal", "file"),
    )

    command = HermesRunner()._build_command(spec, "Review this project")

    assert command == [
        "hermes", "chat", "--yolo", "-q",
        "-m", "gpt-5.6-sol",
        "--skills", "test-driven-development",
        "--toolsets", "terminal,file",
        "Review this project",
    ]


def test_hermes_runner_preserves_default_skills_without_a_profile() -> None:
    spec = AgentSpec(
        role="reviewer",
        runtime="hermes",
        model="",
        system_prompt_path=Path("prompts/reviewer.md"),
    )

    command = HermesRunner()._build_command(spec, "Review this project")

    assert command == [
        "hermes", "chat", "--yolo", "-q",
        "--skills",
        "spec-driven-development,test-driven-development,code-review-and-quality,"
        "incremental-implementation,source-driven-development,planning-and-task-breakdown",
        "Review this project",
    ]
