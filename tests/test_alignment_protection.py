import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from unison.alignment import (
    missing_protected_paths,
    protected_deletions,
    protected_existing_paths,
)
from unison.interfaces import AgentResult, AgentSpec, Operation
from unison.orchestrator import Orchestrator
from unison.pipeline import PipelineLoader
from unison.runners.base import BaseRunner


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




class _ProtectedDeleteRunner(BaseRunner):
    def _build_command(self, spec, prompt):
        del spec, prompt
        return [
            sys.executable,
            "-c",
            "from pathlib import Path; Path('CLAUDE.md').unlink()",
        ]


def test_protected_existing_paths_detects_untracked_governance_deletion(tmp_path):
    spec = AgentSpec("developer", "codex", "test", Path("prompts/developer.md"))
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "developer.md").write_text("prompt", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("rules", encoding="utf-8")

    expected = protected_existing_paths(tmp_path, spec)
    (tmp_path / "CLAUDE.md").unlink()

    assert missing_protected_paths(tmp_path, expected) == ["CLAUDE.md"]


def test_alignment_rejects_runner_without_verified_lifecycle(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path)
    spec = orchestrator.spec.agents["developer"]
    runner = SimpleNamespace(run=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not run")))
    monkeypatch.setattr(orchestrator, "_select_runner", lambda _role: (runner, spec))
    monkeypatch.setattr(orchestrator, "_get_budget_tracker", lambda _role: SimpleNamespace(
        check_budget=lambda: True,
        add_usage=lambda *_args, **_kwargs: None,
    ))
    monkeypatch.setattr(orchestrator, "_build_prompt", lambda *_args, **_kwargs: "task")

    orchestrator._invoke_agent_for_role("developer", 1)

    assert orchestrator.state().halt_signal is True
    assert "requires a BaseRunner" in (orchestrator.state().halt_reason or "")


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


def test_invoke_path_writes_contract_bound_execution_summary(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path)
    subprocess.run(["git", "init", "-b", "master"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp_path, check=True, capture_output=True)
    spec = orchestrator.spec.agents["developer"]
    runner = _ProtectedDeleteRunner(binary=sys.executable)
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

    def create_source(*_args, **_kwargs):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "new.py").write_text("x = 1\n", encoding="utf-8")
        return AgentResult(True, 0, 0.1, "untrusted", "", tmp_path / "agent.log")

    monkeypatch.setattr(runner, "_run_command", create_source)
    orchestrator._invoke_agent_for_role("developer", 1)

    summaries = list(
        orchestrator.spec.world.unison_run_dir_for(orchestrator._run_ctx)
        .glob("alignment/execution-summaries/*.json")
    )
    assert len(summaries) == 1
    summary = json.loads(summaries[0].read_text(encoding="utf-8"))
    assert summary["inputs"][0]["kind"] == "system_prompt"
    assert summary["filesystem_delta"]["created"] == ["src/new.py"]
    assert summary["agent"]["pid"] is None
    assert "untrusted" not in json.dumps(summary)


def test_invoke_path_restores_untracked_protected_deletion_and_halts(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path)
    subprocess.run(["git", "init", "-b", "master"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "pipeline.yaml", "prompts"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp_path, check=True, capture_output=True)
    spec = orchestrator.spec.agents["developer"]
    runner = _ProtectedDeleteRunner(binary=sys.executable)
    monkeypatch.setattr(orchestrator, "_select_runner", lambda _role: (runner, spec))
    monkeypatch.setattr(orchestrator, "_get_budget_tracker", lambda _role: SimpleNamespace(
        check_budget=lambda: True,
        add_usage=lambda *_args, **_kwargs: None,
    ))
    monkeypatch.setattr(orchestrator, "_build_prompt", lambda *_args, **_kwargs: "task")
    monkeypatch.setattr(orchestrator._detector, "_get_commit", lambda _root: "baseline")

    orchestrator._invoke_agent_for_role("developer", 1)

    assert orchestrator.state().halt_signal is True
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "rules"
