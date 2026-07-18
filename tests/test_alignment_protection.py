from pathlib import Path
import subprocess
from types import SimpleNamespace

from unison.alignment import protected_deletions
from unison.interfaces import AgentResult, AgentSpec, Operation
from unison.orchestrator import Orchestrator
from unison.pipeline import PipelineLoader


def _orchestrator(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "developer.md").write_text("developer", encoding="utf-8")
    (tmp_path / "prompts" / "reviewer.md").write_text("reviewer", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("rules", encoding="utf-8")
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text("""
version: "1.0"
project_root: "."
agents:
  developer:
    role: developer
    runtime: codex
    system_prompt_path: prompts/developer.md
  reviewer:
    role: reviewer
    runtime: codex
    system_prompt_path: prompts/reviewer.md
snapshots:
  enabled: true
  max_pre_snapshot_size_mb: 10
llm_observer:
  enabled: true
  runtime: claude
  alignment:
    enabled: true
    max_corrections_per_run: 3
""", encoding="utf-8")
    return Orchestrator(PipelineLoader().load(pipeline))


def test_protected_deletions_recognize_governance_and_declared_prompt_only(tmp_path):
    spec = AgentSpec("developer", "codex", "test", Path("prompts/developer.md"))

    assert protected_deletions(tmp_path, spec, ["CLAUDE.md", "prompts/developer.md", "src/old.py"]) == [
        "CLAUDE.md", "prompts/developer.md",
    ]


def test_protected_deletion_restores_workspace_snapshot_and_halts(tmp_path):
    orchestrator = _orchestrator(tmp_path)
    snapshot = orchestrator._snapshot_mgr.snapshot(
        path=tmp_path,
        operation=Operation.MODIFY,
        agent="developer", iteration=1, project_id=orchestrator.spec.world.project_id,
        pipeline_name=orchestrator.spec.pipeline_name, run_id=orchestrator._run_ctx.run_id,
    )
    (tmp_path / "CLAUDE.md").unlink()
    spec = orchestrator.spec.agents["developer"]

    assert orchestrator._halt_on_protected_deletion(
        spec=spec, deleted=["CLAUDE.md"], workspace_snapshot_id=snapshot.audit_id,
    ) is True
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "rules"
    assert orchestrator.state().halt_signal is True
    assert "protected project input deleted" in orchestrator.state().halt_reason


def test_invoke_path_restores_protected_deletion_and_halts(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path)
    subprocess.run(["git", "init", "-b", "master"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp_path, check=True, capture_output=True)
    spec = orchestrator.spec.agents["developer"]
    runner = SimpleNamespace(run=lambda **_kwargs: AgentResult(
        success=True, exit_code=0, duration=0.1, stdout_tail="", stderr_tail="",
        log_path=tmp_path / "agent.log",
    ))
    monkeypatch.setattr(orchestrator, "_select_runner", lambda _role: (runner, spec))
    monkeypatch.setattr(orchestrator, "_get_budget_tracker", lambda _role: SimpleNamespace(
        check_budget=lambda: True,
        add_usage=lambda *_args, **_kwargs: None,
    ))
    monkeypatch.setattr(orchestrator, "_build_prompt", lambda *_args, **_kwargs: "task")
    monkeypatch.setattr(orchestrator._detector, "_get_commit", lambda _root: "baseline")
    monkeypatch.setattr(orchestrator._detector, "detect", lambda **_kwargs: AgentResult(
        success=True, exit_code=0, duration=0.1, stdout_tail="", stderr_tail="",
        log_path=tmp_path / "agent.log",
    ))

    def delete_governance(*_args, **_kwargs):
        (tmp_path / "CLAUDE.md").unlink()
        return AgentResult(True, 0, 0.1, "", "", tmp_path / "agent.log")

    runner.run = delete_governance
    orchestrator._invoke_agent_for_role("developer", 1)

    assert orchestrator.state().halt_signal is True
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "rules"
