from pathlib import Path

from unison.alignment import build_execution_contract, verify_execution_contract
from unison.interfaces import AgentSpec
from unison.world import RunContext, World


def _ctx(world: World) -> RunContext:
    return RunContext(
        project_id=world.project_id,
        pipeline_key="story-123456",
        run_id="run-123",
        pipeline_name="Story",
    )


def test_contract_verification_detects_changed_declared_input(tmp_path):
    world = World(tmp_path)
    ctx = _ctx(world)
    prompt = tmp_path / "prompts" / "developer.md"
    prompt.parent.mkdir()
    prompt.write_text("original", encoding="utf-8")
    spec = AgentSpec("developer", "codex", "test", Path("prompts/developer.md"))
    contract = build_execution_contract(
        world,
        ctx,
        spec,
        role="developer",
        phase="dev_active",
        iteration=1,
        task="task",
        inputs={"system_prompt": prompt},
    )

    assert verify_execution_contract(world, contract) == ()
    prompt.write_text("changed", encoding="utf-8")

    assert verify_execution_contract(world, contract) == ("system_prompt:digest_mismatch",)


def test_contract_verification_rejects_project_escape(tmp_path):
    world = World(tmp_path / "project")
    world.root.mkdir()
    ctx = _ctx(world)
    prompt = world.root / "prompts" / "developer.md"
    prompt.parent.mkdir()
    prompt.write_text("original", encoding="utf-8")
    spec = AgentSpec("developer", "codex", "test", Path("prompts/developer.md"))
    contract = build_execution_contract(
        world,
        ctx,
        spec,
        role="developer",
        phase="dev_active",
        iteration=1,
        task="task",
        inputs={"system_prompt": prompt},
    )
    contract["inputs"][0]["path"] = "../outside.md"

    assert verify_execution_contract(world, contract) == ("system_prompt:path_invalid",)
