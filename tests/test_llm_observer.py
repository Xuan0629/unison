"""Tests for run-scoped LLM Observer manifests and audit records."""
import json

from unison.llm_observer import append_audit, write_manifest
from unison.state import State, Transition
from unison.world import RunContext, World


def _ctx(world):
    return RunContext(
        project_id=world.project_id,
        pipeline_key="alpha-123456",
        run_id="run-123",
        pipeline_name="Alpha",
    )


def test_manifest_is_run_scoped_and_contains_only_allowlisted_state(tmp_path):
    world = World(tmp_path)
    ctx = _ctx(world)
    state = State(
        phase="dev_review",
        iteration=2,
        halt_reason="safe reason",
        history=[
            Transition(
                from_phase="dev_active", to_phase="dev_review",
                by="orchestrator", timestamp="2026-07-16T00:00:00Z",
            )
        ],
    )

    path, digest = write_manifest(world, ctx, state)
    manifest = json.loads(path.read_text(encoding="utf-8"))

    assert path == world.unison_run_dir_for(ctx) / "llm-observer" / "manifest.json"
    assert len(digest) == 64
    assert manifest["run_id"] == "run-123"
    assert manifest["transition_count"] == 1
    assert "history" not in manifest
    assert "runtime_agents" not in manifest


def test_audit_is_append_only_and_excludes_observation_content(tmp_path):
    world = World(tmp_path)
    ctx = _ctx(world)
    _, digest = write_manifest(world, ctx, State())

    path = append_audit(
        world, ctx, event="observation_skipped", manifest_sha256=digest,
        runtime="codex", model="gpt-5.6-sol",
        detail="no read-only runtime binding",
    )
    append_audit(
        world, ctx, event="action_rejected", manifest_sha256=digest,
        runtime="codex", model="gpt-5.6-sol",
        detail="rerun requires user confirmation",
    )

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [record["event"] for record in records] == ["observation_skipped", "action_rejected"]
    assert all(record["run_id"] == "run-123" for record in records)
    assert all("prompt" not in record and "output" not in record for record in records)


def test_orchestrator_records_opt_in_without_launching_a_writable_runtime(tmp_path):
    from unison.orchestrator import Orchestrator
    from unison.pipeline import PipelineLoader

    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "developer.md").write_text("developer", encoding="utf-8")
    (prompts / "reviewer.md").write_text("reviewer", encoding="utf-8")
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(
        """version: "1.0"
mode: code-dev
project_root: .
agents:
  developer:
    role: developer
    runtime: codex
    system_prompt_path: prompts/developer.md
  reviewer:
    role: reviewer
    runtime: codex
    system_prompt_path: prompts/reviewer.md
llm_observer:
  enabled: true
  runtime: codex
  model: gpt-5.6-sol
""",
        encoding="utf-8",
    )
    orchestrator = Orchestrator(PipelineLoader().load(pipeline), dry_run=True)

    orchestrator._start_llm_observer_audit()

    audit_path = orchestrator.spec.world.unison_run_dir_for(orchestrator._run_ctx) / "llm-observer" / "audit.jsonl"
    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert [record["event"] for record in records] == ["manifest_created", "observation_skipped"]
    assert records[-1]["detail"] == "no verified read-only runtime binding"
