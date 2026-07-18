import sys
from pathlib import Path

import pytest

from unison.alignment import build_execution_contract
from unison.interfaces import AgentSpec, Operation
from unison.orchestrator import Orchestrator
from unison.pipeline import PipelineLoader
from unison.runners.base import BaseRunner


class _DriftRunner(BaseRunner):
    def _build_command(self, spec, prompt):
        del spec, prompt
        return [
            sys.executable,
            "-c",
            "from pathlib import Path; import time; Path('prompts/developer.md').write_text('drift'); time.sleep(10)",
        ]


class _InstantDriftRunner(BaseRunner):
    def _build_command(self, spec, prompt):
        del spec, prompt
        return [sys.executable, "-c", "from pathlib import Path; Path('prompts/developer.md').write_text('drift')"]


def _orchestrator(tmp_path, *, max_corrections=1):
    (tmp_path / "prompts").mkdir()
    prompt = tmp_path / "prompts" / "developer.md"
    prompt.write_text("original", encoding="utf-8")
    pipeline = tmp_path / "pipeline.yaml"
    pipeline.write_text(
        f'''version: "1.0"
project_root: "."
agents:
  developer:
    role: developer
    runtime: codex
    system_prompt_path: prompts/developer.md
  reviewer:
    role: reviewer
    runtime: codex
    system_prompt_path: prompts/developer.md
snapshots:
  enabled: true
  max_pre_snapshot_size_mb: 10
llm_observer:
  enabled: true
  runtime: claude
  alignment:
    enabled: true
    max_corrections_per_run: {max_corrections}
''',
        encoding="utf-8",
    )
    return Orchestrator(PipelineLoader().load(pipeline))


def _supervision_inputs(tmp_path, *, max_corrections=1):
    orchestrator = _orchestrator(tmp_path, max_corrections=max_corrections)
    spec = orchestrator.spec.agents["developer"]
    prompt = tmp_path / "prompts" / "developer.md"
    contract = build_execution_contract(
        orchestrator.spec.world,
        orchestrator._run_ctx,
        spec,
        role="developer",
        phase="dev_active",
        iteration=1,
        task="task",
        inputs={"system_prompt": prompt},
    )
    assert orchestrator._snapshot_mgr is not None
    snapshot = orchestrator._snapshot_mgr.snapshot(
        path=tmp_path,
        operation=Operation.MODIFY,
        agent="developer",
        iteration=1,
        project_id=orchestrator.spec.world.project_id,
        pipeline_name=orchestrator.spec.pipeline_name,
        run_id=orchestrator._run_ctx.run_id,
    )
    return orchestrator, spec, prompt, contract, snapshot.audit_id


@pytest.mark.skipif(sys.platform != "linux", reason="verified process correction requires Linux")
def test_alignment_contract_drift_kills_restores_and_replaces(tmp_path):
    orchestrator, spec, prompt, contract, snapshot_id = _supervision_inputs(tmp_path)

    result, corrected, handle = orchestrator._run_alignment_supervised(
        runner=_DriftRunner(binary=sys.executable),
        spec=spec,
        prompt="task",
        workdir=tmp_path,
        timeout=1,
        log_path=tmp_path / "agent.log",
        contract=contract,
        workspace_snapshot_id=snapshot_id,
        role="developer",
        iteration=1,
    )

    assert corrected is True
    assert handle is not None
    assert result.success is False
    assert orchestrator.state().halt_signal is True
    assert "budget exhausted" in (orchestrator.state().halt_reason or "")
    assert orchestrator.state().alignment_corrections == 1
    assert prompt.read_text(encoding="utf-8") == "original"


@pytest.mark.skipif(sys.platform != "linux", reason="verified process correction requires Linux")
def test_alignment_detects_drift_when_child_exits_before_poll(tmp_path):
    orchestrator, spec, prompt, contract, snapshot_id = _supervision_inputs(tmp_path, max_corrections=0)

    result, corrected, _ = orchestrator._run_alignment_supervised(
        runner=_InstantDriftRunner(binary=sys.executable),
        spec=spec,
        prompt="task",
        workdir=tmp_path,
        timeout=1,
        log_path=tmp_path / "agent.log",
        contract=contract,
        workspace_snapshot_id=snapshot_id,
        role="developer",
        iteration=1,
    )

    assert corrected is False
    assert result.success is True
    assert orchestrator.state().halt_signal is True
    assert "budget exhausted" in (orchestrator.state().halt_reason or "")
    assert prompt.read_text(encoding="utf-8") == "original"
