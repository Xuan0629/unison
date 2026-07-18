import json
from pathlib import Path

import pytest

from unison.alignment import (
    AlignmentBindingError,
    build_execution_contract,
    write_execution_summary,
)
from unison.interfaces import AgentResult, AgentSpec
from unison.world import RunContext, World


def _ctx(world: World) -> RunContext:
    return RunContext(
        project_id=world.project_id,
        pipeline_key="story-123456",
        run_id="run-123",
        pipeline_name="Story",
    )


def test_execution_contract_records_only_existing_project_scoped_inputs(tmp_path):
    world = World(tmp_path)
    ctx = _ctx(world)
    prompt = tmp_path / "prompts" / "writer.md"
    prompt.parent.mkdir()
    prompt.write_text("write chapter", encoding="utf-8")
    prd = world.prd_for(ctx.pipeline_key)
    design = world.tech_design_for(ctx.pipeline_key)
    prd.parent.mkdir(parents=True, exist_ok=True)
    prd.write_text("user wants a mystery", encoding="utf-8")
    design.write_text("three acts", encoding="utf-8")
    spec = AgentSpec("writer", "claude", "test", Path("prompts/writer.md"), pipeline_role="developer")

    contract = build_execution_contract(
        world, ctx, spec, role="writer", phase="dev_active", iteration=2,
        task="write the second chapter", inputs={
            "system_prompt": prompt,
            "prd": prd,
            "design": design,
        },
    )

    assert contract["role"] == "writer"
    assert contract["task_sha256"]
    assert [item["kind"] for item in contract["inputs"]] == ["design", "prd", "system_prompt"]
    assert all(item["sha256"] for item in contract["inputs"])
    assert all(not item["path"].startswith("/") for item in contract["inputs"])


def test_execution_contract_rejects_empty_task_and_missing_system_prompt(tmp_path):
    world = World(tmp_path)
    ctx = _ctx(world)
    prompt = tmp_path / "prompts" / "writer.md"
    prompt.parent.mkdir()
    prompt.write_text("prompt", encoding="utf-8")
    spec = AgentSpec("writer", "claude", "test", Path("prompts/writer.md"))

    with pytest.raises(AlignmentBindingError, match="task is empty"):
        build_execution_contract(
            world, ctx, spec, role="writer", phase="dev_active", iteration=1,
            task="", inputs={"system_prompt": prompt},
        )
    with pytest.raises(AlignmentBindingError, match="system_prompt"):
        build_execution_contract(
            world, ctx, spec, role="writer", phase="dev_active", iteration=1,
            task="task", inputs={"prd": prompt},
        )


def test_execution_contract_rejects_missing_or_external_binding(tmp_path):
    world = World(tmp_path / "project")
    world.root.mkdir()
    ctx = _ctx(world)
    prompt = world.root / "prompts" / "writer.md"
    prompt.parent.mkdir()
    prompt.write_text("prompt", encoding="utf-8")
    spec = AgentSpec("writer", "claude", "test", Path("prompts/writer.md"))

    with pytest.raises(AlignmentBindingError, match="missing"):
        build_execution_contract(
            world, ctx, spec, role="writer", phase="dev_active", iteration=1,
            task="task", inputs={"system_prompt": prompt, "prd": world.root / "missing.md"},
        )

    external = tmp_path / "other-project.md"
    external.write_text("wrong project", encoding="utf-8")
    with pytest.raises(AlignmentBindingError, match="outside project"):
        build_execution_contract(
            world, ctx, spec, role="writer", phase="dev_active", iteration=1,
            task="task", inputs={"system_prompt": prompt, "prd": external},
        )


def test_execution_summary_records_authoritative_contract_process_and_delta(tmp_path):
    world = World(tmp_path)
    ctx = _ctx(world)
    contract = {
        "role": "developer", "phase": "dev_active", "iteration": 1,
        "task_sha256": "a" * 64, "inputs": [], "sha256": "b" * 64,
    }
    result = AgentResult(True, 0, 1.25, "untrusted raw output", "", tmp_path / "agent.log")

    path = write_execution_summary(
        world, ctx, contract=contract, runtime="claude", model="test-model",
        pid=123, process_group=123, started_at="2026-07-17T00:00:00+00:00",
        ended_at="2026-07-17T00:00:01+00:00", result=result,
        created=["src/new.py"], modified=["src/old.py"], deleted=["tmp/a.txt"],
    )
    summary = json.loads(path.read_text(encoding="utf-8"))

    assert summary["agent"] == {"runtime": "claude", "model": "test-model", "pid": 123, "process_group": 123}
    assert summary["filesystem_delta"] == {
        "created": ["src/new.py"], "modified": ["src/old.py"], "deleted": ["tmp/a.txt"],
    }
    assert summary["process"]["status"] == "completed"
    assert "untrusted raw output" not in json.dumps(summary)
    assert summary["sha256"]


def test_execution_summary_rejects_malformed_contract_and_records_failure(tmp_path):
    world = World(tmp_path)
    ctx = _ctx(world)
    result = AgentResult(False, 7, 0.5, "raw", "", tmp_path / "agent.log", error="failed")
    contract = {
        "role": "developer", "phase": "dev_active", "iteration": 1,
        "task_sha256": "a" * 64, "inputs": [], "sha256": "b" * 64,
    }

    with pytest.raises(AlignmentBindingError, match="missing required"):
        write_execution_summary(
            world, ctx, contract={"role": "developer"}, runtime="claude", model="test",
            pid=None, process_group=None, started_at="start", ended_at="end", result=result,
            created=[], modified=[], deleted=[],
        )

    path = write_execution_summary(
        world, ctx, contract=contract, runtime="claude", model="test",
        pid=None, process_group=None, started_at="start", ended_at="end", result=result,
        created=[], modified=[], deleted=[],
    )
    summary = json.loads(path.read_text(encoding="utf-8"))
    assert summary["process"]["status"] == "failed"
    assert summary["process"]["exit_code"] == 7
