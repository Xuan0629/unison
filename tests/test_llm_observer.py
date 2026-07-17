"""Tests for run-scoped LLM Observer manifests and audit records."""
import json
from types import SimpleNamespace

from unison.llm_observer import (
    ControlObservationResult,
    ControlProposal,
    append_audit,
    llm_control_receipt_path,
    llm_observation_path,
    run_claude_control_observation,
    run_claude_observation,
    run_hermes_observation,
    write_manifest,
)
from unison.checklist import ChecklistItem, ChecklistStatus
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


def test_control_manifest_bounds_and_redacts_allowlisted_evidence(tmp_path):
    world = World(tmp_path)
    ctx = _ctx(world)
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    evidence = {
        "reviewer_findings": [
            {"id": "review.finding.1", "text": f"{secret} " + "x" * 500},
        ],
        "open_checklist": [
            {"id": "checklist.P1", "severity": "HIGH", "title": "y" * 500},
        ],
        "verification": {"id": "verification.declared", "status": "failed"},
        "risk": {"id": "risk.post_invoke", "status": "unavailable"},
        "budget": {"id": "budget.current", "status": "unavailable"},
    }

    path, _ = write_manifest(world, ctx, State(), evidence=evidence)
    manifest = json.loads(path.read_text(encoding="utf-8"))

    assert manifest["evidence"]["reviewer_findings"][0]["id"] == "review.finding.1"
    assert secret not in manifest["evidence"]["reviewer_findings"][0]["text"]
    assert len(manifest["evidence"]["reviewer_findings"][0]["text"]) <= 240
    assert len(manifest["evidence"]["open_checklist"][0]["title"]) <= 160
    assert manifest["evidence"]["verification"]["status"] == "failed"
    assert "raw_log" not in json.dumps(manifest)


def test_claude_control_observer_persists_only_valid_typed_proposal(tmp_path, monkeypatch):
    world = World(tmp_path)
    ctx = _ctx(world)
    evidence = {
        "reviewer_findings": [{"id": "review.finding.1", "text": "scope differs from approved goal"}],
        "verification": {"id": "verification.declared", "status": "passed"},
        "risk": {"id": "risk.post_invoke", "status": "unavailable"},
        "budget": {"id": "budget.current", "status": "unavailable"},
    }
    manifest_path, digest = write_manifest(world, ctx, State(), evidence=evidence)
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"structured_output": {
                "project_id": ctx.project_id,
                "pipeline_key": ctx.pipeline_key,
                "run_id": ctx.run_id,
                "phase": "init",
                "iteration": 0,
                "manifest_sha256": digest,
                "action": "halt",
                "reason_code": "goal_deviation",
                "evidence_ids": ["review.finding.1"],
                "target_role": None,
                "directive_code": None,
            }}),
            stderr="raw stderr must not persist",
        )

    monkeypatch.setattr("unison.llm_observer.subprocess.run", fake_run)
    result = run_claude_control_observation(
        world, ctx, manifest_path, digest, "deepseek-v4-pro", 30,
        allow_halt=True, allow_redirect=False, redirect_roles=(), redirect_directives=(),
    )

    assert result.status == "proposed"
    assert result.proposal.action == "halt"
    assert "--tools" in captured["command"]
    assert captured["command"][captured["command"].index("--tools") + 1] == ""
    assert captured["command"][captured["command"].index("--max-budget-usd") + 1] == "0.10"
    assert f"manifest_sha256 must be exactly {digest}" in captured["command"][-1]
    persisted = json.loads(result.path.read_text(encoding="utf-8"))
    assert persisted["manifest_sha256"] == digest
    assert persisted["project_id"] == ctx.project_id
    assert persisted["run_id"] == ctx.run_id
    assert persisted["phase"] == "init"
    assert persisted["iteration"] == 0
    assert persisted["evidence_ids"] == ["review.finding.1"]
    assert "stderr" not in persisted


def test_claude_control_observer_accepts_only_declared_redirect(tmp_path, monkeypatch):
    world = World(tmp_path)
    ctx = _ctx(world)
    manifest_path, digest = write_manifest(
        world, ctx, State(), evidence={
            "open_checklist": [{"id": "checklist.P1", "severity": "HIGH", "title": "missing test"}],
        },
    )
    monkeypatch.setattr(
        "unison.llm_observer.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"structured_output": {
                "project_id": ctx.project_id,
                "pipeline_key": ctx.pipeline_key,
                "run_id": ctx.run_id,
                "phase": "init",
                "iteration": 0,
                "manifest_sha256": digest,
                "action": "redirect",
                "reason_code": "unresolved_work",
                "evidence_ids": ["checklist.P1"],
                "target_role": "developer",
                "directive_code": "address_open_checklist",
            }}),
            stderr="",
        ),
    )

    result = run_claude_control_observation(
        world, ctx, manifest_path, digest, "deepseek-v4-pro", 30,
        allow_halt=False, allow_redirect=True,
        redirect_roles=("developer",), redirect_directives=("address_open_checklist",),
    )

    assert result.status == "proposed"
    assert result.proposal.directive_code == "address_open_checklist"


def test_claude_control_observer_rejects_tampered_manifest_before_subprocess(tmp_path, monkeypatch):
    world = World(tmp_path)
    ctx = _ctx(world)
    manifest_path, digest = write_manifest(world, ctx, State())
    manifest_path.write_text('{"tampered":true}', encoding="utf-8")
    monkeypatch.setattr(
        "unison.llm_observer.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    result = run_claude_control_observation(
        world, ctx, manifest_path, digest, "deepseek-v4-pro", 30,
        allow_halt=True, allow_redirect=False, redirect_roles=(), redirect_directives=(),
    )

    assert result.status == "failed"
    assert result.summary == "manifest digest mismatch"


def test_claude_control_observer_rejects_cross_phase_proposal(tmp_path, monkeypatch):
    world = World(tmp_path)
    ctx = _ctx(world)
    manifest_path, digest = write_manifest(
        world, ctx, State(), evidence={
            "reviewer_findings": [{"id": "review.finding.1", "text": "finding"}],
        },
    )
    monkeypatch.setattr(
        "unison.llm_observer.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"structured_output": {
                "project_id": ctx.project_id,
                "pipeline_key": ctx.pipeline_key,
                "run_id": ctx.run_id,
                "phase": "developer",
                "iteration": 0,
                "manifest_sha256": digest,
                "action": "halt",
                "reason_code": "goal_deviation",
                "evidence_ids": ["review.finding.1"],
                "target_role": None,
                "directive_code": None,
            }}),
            stderr="",
        ),
    )

    result = run_claude_control_observation(
        world, ctx, manifest_path, digest, "deepseek-v4-pro", 30,
        allow_halt=True, allow_redirect=False, redirect_roles=(), redirect_directives=(),
    )

    assert result.status == "failed"
    assert result.summary == "invalid control proposal"
    assert not result.path.exists()


def test_claude_control_observer_rejects_malformed_evidence_ids_fail_closed(tmp_path, monkeypatch):
    world = World(tmp_path)
    ctx = _ctx(world)
    manifest_path, digest = write_manifest(
        world, ctx, State(), evidence={
            "reviewer_findings": [{"id": "review.finding.1", "text": "finding"}],
        },
    )
    monkeypatch.setattr(
        "unison.llm_observer.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"structured_output": {
                "project_id": ctx.project_id,
                "pipeline_key": ctx.pipeline_key,
                "run_id": ctx.run_id,
                "phase": "init",
                "iteration": 0,
                "manifest_sha256": digest,
                "action": "halt",
                "reason_code": "goal_deviation",
                "evidence_ids": [["review.finding.1"]],
                "target_role": None,
                "directive_code": None,
            }}),
            stderr="",
        ),
    )

    result = run_claude_control_observation(
        world, ctx, manifest_path, digest, "deepseek-v4-pro", 30,
        allow_halt=True, allow_redirect=False, redirect_roles=(), redirect_directives=(),
    )

    assert result.status == "failed"
    assert result.summary == "invalid control proposal"


def test_claude_control_observer_rejects_unsupported_evidence_action(tmp_path, monkeypatch):
    world = World(tmp_path)
    ctx = _ctx(world)
    manifest_path, digest = write_manifest(
        world, ctx, State(), evidence={
            "reviewer_findings": [{"id": "review.finding.1", "text": "finding"}],
        },
    )
    monkeypatch.setattr(
        "unison.llm_observer.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"structured_output": {
                "project_id": ctx.project_id,
                "pipeline_key": ctx.pipeline_key,
                "run_id": ctx.run_id,
                "phase": "init",
                "iteration": 0,
                "manifest_sha256": digest,
                "action": "halt",
                "reason_code": "safety_evidence",
                "evidence_ids": ["review.finding.1"],
                "target_role": None,
                "directive_code": None,
            }}),
            stderr="",
        ),
    )

    result = run_claude_control_observation(
        world, ctx, manifest_path, digest, "deepseek-v4-pro", 30,
        allow_halt=True, allow_redirect=False, redirect_roles=(), redirect_directives=(),
    )

    assert result.status == "failed"
    assert result.summary == "invalid control proposal"
    assert not result.path.exists()


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


def _control_orchestrator(tmp_path):
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
snapshots:
  enabled: false
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
  allow_halt: true
  allow_redirect: true
  redirect:
    roles: [developer]
    directives: [address_open_checklist]
""",
        encoding="utf-8",
    )
    return Orchestrator(PipelineLoader().load(pipeline), dry_run=True)


def test_orchestrator_control_gate_blocks_common_agent_dispatch(tmp_path, monkeypatch):
    orchestrator = _control_orchestrator(tmp_path)
    calls = []
    monkeypatch.setattr(orchestrator, "_run_llm_control_boundary", lambda **kwargs: False)
    monkeypatch.setattr(
        orchestrator,
        "_select_runner",
        lambda role: calls.append(role) or (_ for _ in ()).throw(AssertionError("must not select runner")),
    )

    orchestrator._invoke_agent_for_role("planner", 1, review_phase="planning_review")

    assert calls == []


def test_orchestrator_drops_redirect_when_developer_dispatch_is_unavailable(tmp_path, monkeypatch):
    orchestrator = _control_orchestrator(tmp_path)
    orchestrator._llm_redirect_directive = "fixed directive"
    monkeypatch.setattr(orchestrator, "_run_llm_control_boundary", lambda **kwargs: True)
    monkeypatch.setattr(orchestrator, "_select_runner", lambda role: (None, None))

    orchestrator._invoke_agent_for_role("developer", 1)

    assert orchestrator._llm_redirect_directive == ""


def test_orchestrator_compiles_only_fixed_redirect_directives(tmp_path):
    orchestrator = _control_orchestrator(tmp_path)

    assert orchestrator._compile_llm_redirect("address_open_checklist", ("checklist.P1",)).startswith("Address the listed")
    assert orchestrator._compile_llm_redirect("address_reviewer_findings", ("review.finding.1",)).startswith("Address the listed")
    assert orchestrator._compile_llm_redirect("run_declared_verification", ("verification.declared",)).startswith("Run the declared")


def test_orchestrator_consumes_halt_proposal_before_agent(tmp_path, monkeypatch):
    orchestrator = _control_orchestrator(tmp_path)

    def fake_control(world, ctx, manifest_path, digest, *args, **kwargs):
        proposal = ControlProposal(
            project_id=ctx.project_id, pipeline_key=ctx.pipeline_key, run_id=ctx.run_id,
            phase=orchestrator._state.phase, iteration=orchestrator._state.iteration,
            manifest_sha256=digest, action="halt", reason_code="goal_deviation",
            evidence_ids=("review.finding.1",), target_role=None, directive_code=None,
        )
        return ControlObservationResult("proposed", "goal_deviation", proposal, tmp_path / "proposal.json")

    monkeypatch.setattr("unison.orchestrator.run_claude_control_observation", fake_control)
    monkeypatch.setattr(orchestrator, "_llm_control_evidence", lambda: {
        "reviewer_findings": [{"id": "review.finding.1", "text": "finding"}],
    })
    monkeypatch.setattr(orchestrator, "_save_checkpoint", lambda *args: None)

    assert orchestrator._run_llm_control_boundary(role="developer", iteration=1) is False
    assert orchestrator._state.halt_signal is True
    receipt_paths = list((orchestrator.spec.world.unison_run_dir_for(orchestrator._run_ctx) / "llm-observer" / "receipts").glob("*.json"))
    assert len(receipt_paths) == 1
    receipt = json.loads(receipt_paths[0].read_text(encoding="utf-8"))
    assert receipt["action"] == "halt"
    assert "finding\"" not in receipt_paths[0].read_text(encoding="utf-8")


def test_orchestrator_rejects_replayed_control_receipt_before_model_call(tmp_path, monkeypatch):
    orchestrator = _control_orchestrator(tmp_path)
    evidence = {
        "risk": {"id": "risk.post_invoke", "status": "failed"},
        "verification": {"id": "verification.declared", "status": "unavailable"},
        "budget": {"id": "budget.current", "status": "unavailable"},
    }
    monkeypatch.setattr(orchestrator, "_llm_control_evidence", lambda: evidence)
    calls = []

    def fake_control(world, ctx, manifest_path, digest, *args, **kwargs):
        calls.append(digest)
        proposal = ControlProposal(
            project_id=ctx.project_id, pipeline_key=ctx.pipeline_key, run_id=ctx.run_id,
            phase=orchestrator._state.phase, iteration=orchestrator._state.iteration,
            manifest_sha256=digest, action="halt", reason_code="safety_evidence",
            evidence_ids=("risk.post_invoke",), target_role=None, directive_code=None,
        )
        return ControlObservationResult("proposed", "typed halt", proposal, tmp_path / "proposal.json")

    monkeypatch.setattr("unison.orchestrator.run_claude_control_observation", fake_control)
    monkeypatch.setattr(orchestrator, "_save_checkpoint", lambda *args: None)

    assert orchestrator._run_llm_control_boundary(role="developer", iteration=1) is False
    assert len(calls) == 1
    orchestrator._state.halt_signal = False
    orchestrator._state.halt_reason = None
    assert orchestrator._run_llm_control_boundary(role="developer", iteration=1) is False
    assert len(calls) == 1
    assert "receipt already exists" in (orchestrator._state.halt_reason or "")


def test_orchestrator_compiles_redirect_without_llm_prompt_text(tmp_path, monkeypatch):
    orchestrator = _control_orchestrator(tmp_path)

    def fake_control(world, ctx, manifest_path, digest, *args, **kwargs):
        proposal = ControlProposal(
            project_id=ctx.project_id, pipeline_key=ctx.pipeline_key, run_id=ctx.run_id,
            phase=orchestrator._state.phase, iteration=orchestrator._state.iteration,
            manifest_sha256=digest, action="redirect", reason_code="unresolved_work",
            evidence_ids=("checklist.P1",), target_role="developer", directive_code="address_open_checklist",
        )
        return ControlObservationResult("proposed", "untrusted free text", proposal, tmp_path / "proposal.json")

    monkeypatch.setattr("unison.orchestrator.run_claude_control_observation", fake_control)
    monkeypatch.setattr(orchestrator, "_llm_control_evidence", lambda: {
        "open_checklist": [{"id": "checklist.P1", "severity": "HIGH", "title": "missing test"}],
    })
    monkeypatch.setattr(orchestrator, "_save_checkpoint", lambda *args: None)

    assert orchestrator._run_llm_control_boundary(role="developer", iteration=1) is True
    assert orchestrator._state.halt_signal is False
    assert orchestrator._llm_redirect_directive == (
        "Address the listed unresolved checklist items before the next review. Evidence IDs: checklist.P1"
    )
    assert "untrusted free text" not in orchestrator._llm_redirect_directive


def test_orchestrator_control_evidence_excludes_pipeline_global_checklist(tmp_path):
    orchestrator = _control_orchestrator(tmp_path)
    global_checklist = orchestrator.spec.world.checklist_file_for(orchestrator.spec.pipeline_name)
    global_checklist.parent.mkdir(parents=True, exist_ok=True)
    global_checklist.write_text(json.dumps({
        "version": "1.0",
        "items": [{"id": "global-only", "title": "must not leak", "status": "pending"}],
    }), encoding="utf-8")

    evidence = orchestrator._llm_control_evidence()

    assert evidence["open_checklist"] == []


def test_orchestrator_control_evidence_reads_current_run_checklist(tmp_path):
    orchestrator = _control_orchestrator(tmp_path)
    run_checklist = orchestrator.spec.world.run_checklist_file(orchestrator._run_ctx)
    run_checklist.parent.mkdir(parents=True, exist_ok=True)
    run_checklist.write_text(json.dumps({
        "version": "1.0",
        "items": [{"id": "run-only", "title": "current run item", "severity": "HIGH", "status": "pending"}],
    }), encoding="utf-8")

    evidence = orchestrator._llm_control_evidence()

    assert evidence["open_checklist"] == [
        {"id": "run-only", "severity": "HIGH", "title": "current run item"}
    ]


def test_orchestrator_control_evidence_excludes_external_review_path(tmp_path):
    orchestrator = _control_orchestrator(tmp_path)
    external_review = tmp_path / "outside-review.md"
    external_review.write_text("---\nfindings:\n  - '[HIGH] must not leak'\n---\n", encoding="utf-8")
    orchestrator._state.last_review_path = external_review

    evidence = orchestrator._llm_control_evidence()

    assert evidence["reviewer_findings"] == []


def test_orchestrator_halts_on_duplicate_control_receipt(tmp_path, monkeypatch):
    orchestrator = _control_orchestrator(tmp_path)
    monkeypatch.setattr(orchestrator, "_llm_control_evidence", lambda: {})
    monkeypatch.setattr(orchestrator, "_save_checkpoint", lambda *args: None)
    manifest_path, digest = write_manifest(orchestrator.spec.world, orchestrator._run_ctx, orchestrator._state)
    receipt_path = llm_control_receipt_path(orchestrator.spec.world, orchestrator._run_ctx, digest)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "unison.orchestrator.write_manifest",
        lambda *args, **kwargs: (manifest_path, digest),
    )
    monkeypatch.setattr(
        "unison.orchestrator.run_claude_control_observation",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    assert orchestrator._run_llm_control_boundary(role="developer", iteration=1) is False
    assert orchestrator._state.halt_signal is True
    assert "receipt already exists" in (orchestrator._state.halt_reason or "")


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


def test_orchestrator_dispatches_hermes_observer_without_control(tmp_path, monkeypatch):
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
  runtime: hermes
  provider: custom:openai-987xyz
  model: gpt-5.6-terra
""",
        encoding="utf-8",
    )
    orchestrator = Orchestrator(PipelineLoader().load(pipeline), dry_run=True)
    captured = {}

    def fake_hermes(*args):
        captured["args"] = args
        return SimpleNamespace(status="observed", summary="review complete")

    monkeypatch.setattr("unison.orchestrator.run_hermes_observation", fake_hermes)
    monkeypatch.setattr(
        "unison.orchestrator.run_claude_observation",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not use Claude fallback")),
    )

    orchestrator._start_llm_observer_audit()

    assert captured["args"][4:6] == ("gpt-5.6-terra", "custom:openai-987xyz")
    audit_path = orchestrator.spec.world.unison_run_dir_for(orchestrator._run_ctx) / "llm-observer" / "audit.jsonl"
    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert records[1]["detail"] == "no-tool independent Hermes observation started"
    assert records[-1]["event"] == "observation_succeeded"
    assert orchestrator._state.halt_signal is False


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


def test_hermes_observer_uses_explicit_provider_and_no_tools(tmp_path, monkeypatch):
    world = World(tmp_path)
    ctx = _ctx(world)
    manifest_path, digest = write_manifest(world, ctx, State(phase="dev_review", iteration=2))
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="review complete\n", stderr="raw stderr must not persist")

    monkeypatch.setattr("unison.llm_observer.subprocess.run", fake_run)

    result = run_hermes_observation(
        world, ctx, manifest_path, digest, "gpt-5.6-terra", "custom:openai-987xyz", 30,
    )

    assert result.status == "observed"
    assert result.summary == "review complete"
    assert captured["command"][:2] == ["hermes", "-z"]
    assert "--provider" in captured["command"]
    assert captured["command"][captured["command"].index("--provider") + 1] == "custom:openai-987xyz"
    assert "--toolsets" in captured["command"]
    assert captured["command"][captured["command"].index("--toolsets") + 1] == "none"
    assert "-m" in captured["command"]
    assert captured["command"][captured["command"].index("-m") + 1] == "gpt-5.6-terra"
    assert "--yolo" not in captured["command"]
    assert captured["kwargs"]["cwd"] == str(manifest_path.parent)
    persisted = json.loads(llm_observation_path(world, ctx).read_text(encoding="utf-8"))
    assert persisted == {"status": "observed", "summary": "review complete"}


def test_hermes_observer_rejects_missing_provider(tmp_path, monkeypatch):
    world = World(tmp_path)
    ctx = _ctx(world)
    manifest_path, digest = write_manifest(world, ctx, State())
    monkeypatch.setattr(
        "unison.llm_observer.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run without provider")),
    )

    result = run_hermes_observation(world, ctx, manifest_path, digest, "gpt-5.6-terra", "", 30)

    assert result.status == "failed"
    assert result.summary == "observer provider is required"
    assert not llm_observation_path(world, ctx).exists()


def test_hermes_observer_bounds_verbose_report_without_granting_control(tmp_path, monkeypatch):
    world = World(tmp_path)
    ctx = _ctx(world)
    manifest_path, digest = write_manifest(world, ctx, State())
    verbose = "x" * 300
    monkeypatch.setattr(
        "unison.llm_observer.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=verbose, stderr=""),
    )

    result = run_hermes_observation(
        world, ctx, manifest_path, digest, "gpt-5.6-terra", "custom:openai-987xyz", 30,
    )

    assert result.status == "observed"
    assert result.summary == ("x" * 239) + "…"
    persisted = json.loads(llm_observation_path(world, ctx).read_text(encoding="utf-8"))
    assert persisted == {"status": "observed", "summary": result.summary}


def test_hermes_observer_rejects_manifest_changed_after_audit(tmp_path, monkeypatch):
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

    result = run_hermes_observation(
        world, ctx, manifest_path, digest, "gpt-5.6-terra", "custom:openai-987xyz", 30,
    )

    assert result.status == "failed"
    assert result.summary == "manifest digest mismatch"
    assert called is False


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
