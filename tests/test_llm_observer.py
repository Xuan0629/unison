"""Tests for run-scoped LLM Observer manifests and audit records."""
import json
from types import SimpleNamespace

from unison.llm_observer import (
    append_audit,
    llm_observation_path,
    run_claude_observation,
    write_manifest,
)
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


def test_orchestrator_records_opt_in_observation_without_control(tmp_path, monkeypatch):
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
  runtime: claude
  model: deepseek-v4-pro
""",
        encoding="utf-8",
    )
    orchestrator = Orchestrator(PipelineLoader().load(pipeline), dry_run=True)
    monkeypatch.setattr(
        "unison.orchestrator.run_claude_observation",
        lambda *args: SimpleNamespace(status="observed", summary="review complete"),
    )

    orchestrator._start_llm_observer_audit()

    audit_path = orchestrator.spec.world.unison_run_dir_for(orchestrator._run_ctx) / "llm-observer" / "audit.jsonl"
    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert [record["event"] for record in records] == [
        "manifest_created", "observation_started", "observation_succeeded",
    ]
    assert records[-1]["detail"] == "review complete"


def test_claude_observer_uses_no_tools_and_persists_only_structured_result(tmp_path, monkeypatch):
    world = World(tmp_path)
    ctx = _ctx(world)
    manifest_path, digest = write_manifest(world, ctx, State(phase="dev_review", iteration=2))
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "structured_output": {
                    "status": "observed",
                    "summary": "review complete",
                },
                "session_id": "isolated-session",
            }),
            stderr="raw stderr must not persist",
        )

    monkeypatch.setattr("unison.llm_observer.subprocess.run", fake_run)

    result = run_claude_observation(world, ctx, manifest_path, digest, "deepseek-v4-pro", 30)

    assert result.status == "observed"
    assert result.summary == "review complete"
    assert "--dangerously-skip-permissions" not in captured["command"]
    assert "--tools" in captured["command"]
    assert captured["command"][captured["command"].index("--tools") + 1] == ""
    assert "--no-session-persistence" in captured["command"]
    assert "--permission-mode" in captured["command"]
    assert captured["command"][captured["command"].index("--permission-mode") + 1] == "plan"
    assert "--bare" in captured["command"]
    assert captured["kwargs"]["cwd"] == str(manifest_path.parent)
    persisted = json.loads(llm_observation_path(world, ctx).read_text(encoding="utf-8"))
    assert persisted == {"status": "observed", "summary": "review complete"}
    assert "isolated-session" not in llm_observation_path(world, ctx).read_text(encoding="utf-8")


def test_claude_observer_rejects_manifest_changed_after_audit(tmp_path, monkeypatch):
    world = World(tmp_path)
    ctx = _ctx(world)
    manifest_path, digest = write_manifest(world, ctx, State())
    manifest_path.write_text('{"tampered":true}', encoding="utf-8")
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("observer must not run with a changed manifest")

    monkeypatch.setattr("unison.llm_observer.subprocess.run", fake_run)

    result = run_claude_observation(world, ctx, manifest_path, digest, "deepseek-v4-pro", 30)

    assert result.status == "failed"
    assert result.summary == "manifest digest mismatch"
    assert called is False


def test_claude_observer_fails_closed_for_subprocess_error(tmp_path, monkeypatch):
    world = World(tmp_path)
    ctx = _ctx(world)
    manifest_path, digest = write_manifest(world, ctx, State())
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return SimpleNamespace(returncode=1, stdout="", stderr="error")

    monkeypatch.setattr("unison.llm_observer.subprocess.run", fake_run)

    result = run_claude_observation(world, ctx, manifest_path, digest, "", 30)

    assert result.status == "failed"
    assert result.summary == "observer invocation failed"
    assert "--model" not in captured["command"]
    assert not llm_observation_path(world, ctx).exists()


def test_claude_observer_fails_closed_for_timeout(tmp_path, monkeypatch):
    import subprocess

    world = World(tmp_path)
    ctx = _ctx(world)
    manifest_path, digest = write_manifest(world, ctx, State())
    monkeypatch.setattr(
        "unison.llm_observer.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("claude", 30)),
    )

    result = run_claude_observation(world, ctx, manifest_path, digest, "deepseek-v4-pro", 30)

    assert result.status == "failed"
    assert result.summary == "observer invocation failed"
    assert not llm_observation_path(world, ctx).exists()


def test_orchestrator_records_failed_observation_without_halt(tmp_path, monkeypatch):
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
  runtime: claude
""",
        encoding="utf-8",
    )
    orchestrator = Orchestrator(PipelineLoader().load(pipeline), dry_run=True)
    monkeypatch.setattr(
        "unison.orchestrator.run_claude_observation",
        lambda *args: SimpleNamespace(status="failed", summary="observer invocation failed"),
    )

    orchestrator._start_llm_observer_audit()

    audit_path = orchestrator.spec.world.unison_run_dir_for(orchestrator._run_ctx) / "llm-observer" / "audit.jsonl"
    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["event"] == "observation_failed"
    assert records[-1]["detail"] == "observer invocation failed"
    assert orchestrator._state.halt_signal is False


def test_claude_observer_fails_closed_for_invalid_output(tmp_path, monkeypatch):
    world = World(tmp_path)
    ctx = _ctx(world)
    manifest_path, digest = write_manifest(world, ctx, State())

    monkeypatch.setattr(
        "unison.llm_observer.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="not json", stderr=""),
    )

    result = run_claude_observation(world, ctx, manifest_path, digest, "deepseek-v4-pro", 30)

    assert result.status == "failed"
    assert result.summary == "invalid structured observation output"
    assert not llm_observation_path(world, ctx).exists()
