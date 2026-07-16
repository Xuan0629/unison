"""cli.py — CLI entry point for the Unison multi-agent bridge.

Minimum-viable: parse ``unison run --pipeline <yaml>`` and drive
``Orchestrator.run()``. Exits with the final pipeline phase.

Subcommands:
  run       Run a pipeline (loads spec, invokes Orchestrator)
  dry-run   Load + validate spec without executing agents
  mode      Print the pipeline mode (4-agent / 2-agent)
  init      Interactive onboarding — generate pipeline.yaml
  new       Generate pipeline.yaml + prompts/ from a description
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
import os
import sys
import tempfile
from pathlib import Path

from unison.auth import RunAuthorizationError, authorize_run
from unison.interfaces import EXECUTION_POLICY_PHASES, TRUSTED_LOCAL_PRINCIPAL
from unison.orchestrator import Orchestrator
from unison.pipeline import PipelineLoader, PipelineValidationError
from unison.runtime_capabilities import get_runtime_capability, is_registered_runtime
from unison.state import State
from unison.world import RunContext


def _load_api_keys() -> None:
    """Load API keys from ~/.hermes/.env into os.environ for subprocess agents."""
    import os
    env_path = Path.home() / ".hermes" / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("\"'")
            if key and val and key not in os.environ:
                os.environ[key] = val


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="unison",
        description="万物一心 — 本地优先、文件驱动的 Multi-Agent 自动化协作桥梁",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # --- run ---------------------------------------------------------
    run = sub.add_parser("run", help="Run a pipeline to completion")
    run.add_argument(
        "--pipeline", required=True, type=Path,
        help="Path to pipeline.yaml",
    )
    run.add_argument(
        "--project", type=Path, default=None,
        help="Project root (overrides pipeline.yaml project_root)",
    )
    run.add_argument(
        "--dry-run", action="store_true",
        help="Validate spec without executing agents",
    )
    run.add_argument(
        "--json", action="store_true",
        help="Print final state as JSON (instead of human summary)",
    )
    run.add_argument(
        "--switch", type=str, default=None,
        help="Override agent runtime: --switch reviewer:claude",
    )
    run.add_argument(
        "--save-pref", action="store_true",
        help="Persist --switch/--model overrides to pipeline.yaml",
    )
    run.add_argument(
        "--model", type=str, default=None,
        help="Override agent model: --model reviewer:YOUR_MODEL",
    )

    run.add_argument(
        "--execution-policy", type=str, default=None,
        help="Use an execution policy for this run only",
    )
    run.add_argument(
        "--save-execution-policy", type=str, default=None,
        help="Persist this execution policy as execution.selected_policy",
    )

    # --- reconcile ---------------------------------------------------
    reconcile = sub.add_parser(
        "reconcile",
        help="Verify a foreground result and resume its persisted run",
    )
    reconcile.add_argument(
        "--pipeline", required=True, type=Path,
        help="Path to the original pipeline.yaml",
    )
    reconcile.add_argument(
        "--json", action="store_true",
        help="Print final state as JSON (instead of human summary)",
    )

    # --- resume ------------------------------------------------------
    resume = sub.add_parser(
        "resume",
        help="Replace a verified-dead interrupted foreground invocation",
    )
    resume.add_argument(
        "--pipeline", required=True, type=Path,
        help="Path to the original pipeline.yaml",
    )
    resume.add_argument(
        "--json", action="store_true",
        help="Print final state as JSON (instead of human summary)",
    )

    # --- dry-run -----------------------------------------------------
    dr = sub.add_parser("dry-run", help="Validate pipeline.yaml without running")
    dr.add_argument("--pipeline", required=True, type=Path)

    # --- mode --------------------------------------------------------
    md = sub.add_parser("mode", help="Print pipeline mode (full-dev, code-dev, ...)")
    md.add_argument("--pipeline", required=True, type=Path)

    # --- init --------------------------------------------------------
    init = sub.add_parser("init", help="Interactive onboarding — generate pipeline.yaml")
    init.add_argument("description", nargs="?", type=str, default=None,
                      help="What are you building? (asked interactively if omitted)")
    init.add_argument("--output", "-o", type=Path, default=Path("."),
                      help="Output directory (default: current dir)")
    init.add_argument("--preset", type=str, default=None,
                      help="Skip prompts, use preset mode (code-dev/full-dev/design-debate)")
    init.add_argument("--project-root", type=str, default=".",
                      help="project_root value in pipeline.yaml (default: '.')")

    # --- new ---------------------------------------------------------
    new = sub.add_parser("new", help="Generate pipeline.yaml + prompts/ from a description")
    new.add_argument("description", type=str, help="Natural-language description of the task")
    new.add_argument("--output", "-o", type=Path, default=Path("."),
                     help="Output directory (default: current dir)")
    new.add_argument("--yes", "-y", action="store_true",
                     help="Skip prompts, use auto-detected defaults")
    new.add_argument("--project-root", type=str, default=".",
                     help="project_root value in pipeline.yaml (default: '.')")

    # --- webui -------------------------------------------------------
    wui = sub.add_parser("webui", help="Start web dashboard for pipeline status")
    wui.add_argument("--project", type=Path, default=Path("."),
                     help="Project root (default: current dir)")
    wui.add_argument("--port", type=int, default=9099,
                     help="Listen port (default: 9099)")
    wui.add_argument("--token", type=str, default="",
                     help="F8: Session token for control endpoint auth")

    # --- observe -----------------------------------------------------
    obs = sub.add_parser("observe", help="Start observer daemon (file watcher + notifications)")
    obs.add_argument("--project", type=Path, default=Path("."),
                     help="Project root (default: current dir)")

    return p


def _load(spec_path: Path) -> tuple:
    """Load and dry-validate a pipeline spec. Returns (spec, loader)."""
    loader = PipelineLoader()
    spec = loader.load(spec_path)
    loader.dry_run(spec)
    return spec, loader


def _parse_kv(flag_arg: str | None) -> dict[str, str]:
    """Parse ``agent-key:value`` pairs from override flags."""
    if not flag_arg:
        return {}
    result: dict[str, str] = {}
    for pair in flag_arg.split(","):
        key, sep, value = pair.strip().partition(":")
        key = key.strip()
        value = value.strip()
        if not sep or not key or not value:
            raise ValueError(f"invalid override {pair!r}; expected <agent-key>:<value>")
        result[key] = value
    return result


def _apply_agent_overrides(
    spec,
    switches: dict[str, str],
    model_overrides: dict[str, str],
):
    """Return a PipelineSpec with validated agent-key overrides applied."""
    unknown = (set(switches) | set(model_overrides)) - set(spec.agents)
    if unknown:
        raise ValueError(f"unknown agent key(s): {', '.join(sorted(unknown))}")

    invalid_runtimes = {
        runtime for runtime in switches.values()
        if not is_registered_runtime(runtime)
    }
    if invalid_runtimes:
        raise ValueError(
            "invalid runtime(s): " + ", ".join(sorted(invalid_runtimes))
        )

    agents = {}
    for key, agent in spec.agents.items():
        agents[key] = replace(
            agent,
            runtime=switches.get(key, agent.runtime),
            model=model_overrides.get(key, agent.model),
        )
    return replace(spec, agents=agents)


def _save_execution_policy(pipeline_path: Path, policy_name: str) -> None:
    """Atomically persist a selected policy after validating the whole YAML."""
    import yaml
    from unison.io import _fsync_parent_directory

    path = pipeline_path.resolve()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ValueError("pipeline YAML is invalid") from error
    if not isinstance(raw, dict):
        raise ValueError("pipeline YAML must be a mapping")
    execution = raw.setdefault("execution", {})
    if not isinstance(execution, dict):
        raise ValueError("pipeline execution must be a mapping")
    execution["selected_policy"] = policy_name

    payload = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        PipelineLoader().load(tmp_path)
        os.replace(tmp_path, path)
        _fsync_parent_directory(path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _save_agent_preferences(
    pipeline_path: Path,
    switches: dict[str, str],
    model_overrides: dict[str, str],
) -> None:
    """Atomically persist validated runtime/model overrides to pipeline YAML.

    PyYAML preserves data and key order but discards comments and custom
    formatting. Use this explicit opt-in only when that trade-off is acceptable.
    """
    import yaml
    from unison.io import _fsync_parent_directory

    path = pipeline_path.resolve()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("agents"), dict):
        raise ValueError("pipeline YAML has no agents mapping")

    agents = raw["agents"]
    missing = (set(switches) | set(model_overrides)) - set(agents)
    if missing:
        raise ValueError(
            "pipeline YAML missing agent key(s): " + ", ".join(sorted(missing))
        )

    for key, runtime in switches.items():
        if not isinstance(agents[key], dict):
            raise ValueError(f"pipeline YAML agent {key!r} is not a mapping")
        agents[key]["runtime"] = runtime
    for key, model in model_overrides.items():
        if not isinstance(agents[key], dict):
            raise ValueError(f"pipeline YAML agent {key!r} is not a mapping")
        agents[key]["model"] = model

    payload = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _fsync_parent_directory(path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _check_tools(spec) -> bool:
    """Pre-flight: check all required tools. Returns True if all OK.

    If tools are missing, prints actionable error messages and returns False.
    The caller should halt the pipeline.
    """
    import shutil
    needed: dict[str, list[str]] = {}
    for agent_key, agent in spec.agents.items():
        runtime = getattr(agent, "runtime", "")
        if runtime:
            capability = get_runtime_capability(runtime)
            if capability.executable:
                needed.setdefault(capability.executable, []).append(agent_key)

    # Also check git
    missing: list[str] = []
    all_tools = set(needed.keys()) | {'git'}
    for tool in all_tools:
        if not shutil.which(tool):
            missing.append(tool)

    if not missing:
        print(f"Tools OK: {', '.join(sorted(all_tools))}")
        return True

    print(f"\nTOOL CHECK: {', '.join(t.upper() for t in missing)} NOT FOUND")
    for tool in missing:
        agents_needing = needed.get(tool, [])
        if agents_needing:
            print(f"  Needed by agent(s): {', '.join(agents_needing)}")
    print()
    print("  Options:")
    print("    1. Install the missing tools")
    for tool in missing:
        if tool != 'git':
            for agent_key in needed.get(tool, [])[:1]:
                print(f"    2. Switch agent '{agent_key}' runtime: --switch {agent_key}:claude")
    print(f"\nPipeline halted. Fix missing tools and retry.")
    return False


def _cmd_run(args: argparse.Namespace) -> int:
    # Load API keys from ~/.hermes/.env before subprocess agents run
    _load_api_keys()
    spec, _ = _load(args.pipeline)
    if args.project is not None:
        # Override project_root from CLI flag
        from unison.world import World
        project_root = Path(args.project).resolve()
        spec = replace(spec, world=World(root=project_root))

    try:
        switches = _parse_kv(args.switch)
    except ValueError as error:
        print(f"SWITCH ERROR: {error}", file=sys.stderr)
        return 1
    try:
        model_overrides = _parse_kv(args.model)
    except ValueError as error:
        print(f"MODEL ERROR: {error}", file=sys.stderr)
        return 1
    try:
        spec = _apply_agent_overrides(spec, switches, model_overrides)
    except ValueError as error:
        print(f"OVERRIDE ERROR: {error}", file=sys.stderr)
        return 1

    execution_policy = getattr(args, "execution_policy", None)
    save_execution_policy = getattr(args, "save_execution_policy", None)
    if (
        execution_policy is not None
        and save_execution_policy is not None
        and execution_policy != save_execution_policy
    ):
        print(
            "EXECUTION ERROR: --execution-policy and --save-execution-policy must match when both are set",
            file=sys.stderr,
        )
        return 1
    selected_policy = execution_policy or save_execution_policy
    if selected_policy is not None:
        spec = replace(
            spec,
            execution=replace(spec.execution, selected_policy=selected_policy),
        )
    try:
        PipelineLoader.validate_execution(spec)
    except PipelineValidationError as error:
        print(f"EXECUTION ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Effective execution policy: {spec.execution.selected_policy}")

    if not authorize_run(spec, TRUSTED_LOCAL_PRINCIPAL):
        print(
            "AUTHORIZATION ERROR: local CLI is not allowed by who_can_run",
            file=sys.stderr,
        )
        return 3

    if save_execution_policy is not None:
        try:
            _save_execution_policy(args.pipeline, save_execution_policy)
        except (OSError, ValueError, PipelineValidationError) as error:
            print(f"SAVE EXECUTION POLICY ERROR: {error}", file=sys.stderr)
            return 1

    if args.save_pref:
        try:
            _save_agent_preferences(args.pipeline, switches, model_overrides)
        except (OSError, ValueError) as error:
            print(f"SAVE PREF ERROR: {error}", file=sys.stderr)
            return 1

    if not _check_tools(spec):
        print(
            "\nTip: edit the agent runtime/model in pipeline YAML or use "
            "--switch <agent-key>:<runtime> / --model <agent-key>:<model>"
        )
        return 1

    orchestrator = Orchestrator(spec=spec, dry_run=args.dry_run)
    final_state: State = orchestrator.run()

    if args.json:
        print(json.dumps(_state_to_dict(final_state), indent=2, default=str))
    else:
        _print_human_summary(final_state)

    # Exit code: 0 = done, 2 = halted
    if final_state.halt_signal:
        return 2
    if final_state.phase == "done":
        return 0
    return 1


def _load_reconcile_state(spec) -> State:
    """Load only the canonical state for the projected foreground run."""
    projected_path = spec.world.state_file
    if not projected_path.is_file():
        raise ValueError("no projected state exists for foreground reconciliation")
    try:
        projected_raw = json.loads(projected_path.read_text(encoding="utf-8"))
        projected = State.from_dict(projected_raw)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError) as error:
        raise ValueError("projected foreground state is unreadable or invalid") from error
    if not projected.run_id or projected.pipeline_name != spec.pipeline_name:
        raise ValueError("projected state does not identify this pipeline run")
    ctx = RunContext(
        project_id=spec.world.project_id,
        pipeline_key=spec.world.pipeline_key(projected.pipeline_name),
        run_id=projected.run_id,
        pipeline_name=projected.pipeline_name,
    )
    scoped_path = spec.world.run_state_file(ctx)
    if not scoped_path.is_file():
        raise ValueError("canonical run state is missing for projected foreground run")
    try:
        scoped_raw = json.loads(scoped_path.read_text(encoding="utf-8"))
        state = State.from_dict(scoped_raw)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError) as error:
        raise ValueError("canonical foreground run state is unreadable or invalid") from error
    if (
        state.run_id != projected.run_id
        or state.pipeline_name != spec.pipeline_name
        or (
            state.active_foreground_invocation is None
            and not (
                state.foreground_reconcile is not None
                and state.foreground_reconcile.status == "reconciled"
                and state.foreground_reconcile.phase
                and state.foreground_reconcile.role
            )
        )
    ):
        raise ValueError("canonical run state does not match the projected foreground run")
    return state


def _cmd_reconcile(args: argparse.Namespace) -> int:
    _load_api_keys()
    spec, _ = _load(args.pipeline)
    try:
        state = _load_reconcile_state(spec)
    except ValueError as error:
        print(f"RECONCILE ERROR: {error}", file=sys.stderr)
        return 1
    if not authorize_run(spec, TRUSTED_LOCAL_PRINCIPAL):
        print(
            "AUTHORIZATION ERROR: local CLI is not allowed by who_can_run",
            file=sys.stderr,
        )
        return 3
    if not _check_tools(spec):
        return 1
    orchestrator = Orchestrator(spec=spec)
    try:
        orchestrator.load_reconcile_state(state)
    except ValueError as error:
        print(f"RECONCILE ERROR: {error}", file=sys.stderr)
        return 1
    if not orchestrator.reconcile_foreground():
        final_state = orchestrator.state()
    else:
        final_state = orchestrator.run()
    if args.json:
        print(json.dumps(_state_to_dict(final_state), indent=2, default=str))
    else:
        _print_human_summary(final_state)
    if final_state.halt_signal:
        return 2
    return 0 if final_state.phase == "done" else 1


def _cmd_resume(args: argparse.Namespace) -> int:
    _load_api_keys()
    spec, _ = _load(args.pipeline)
    try:
        state = _load_reconcile_state(spec)
    except ValueError as error:
        print(f"RESUME ERROR: {error}", file=sys.stderr)
        return 1
    if not authorize_run(spec, TRUSTED_LOCAL_PRINCIPAL):
        print(
            "AUTHORIZATION ERROR: local CLI is not allowed by who_can_run",
            file=sys.stderr,
        )
        return 3
    if not _check_tools(spec):
        return 1
    orchestrator = Orchestrator(spec=spec)
    try:
        orchestrator.load_resume_state(state)
    except ValueError as error:
        print(f"RESUME ERROR: {error}", file=sys.stderr)
        return 1
    final_state = orchestrator.run()
    if args.json:
        print(json.dumps(_state_to_dict(final_state), indent=2, default=str))
    else:
        _print_human_summary(final_state)
    if final_state.halt_signal:
        return 2
    return 0 if final_state.phase == "done" else 1


def _cmd_dry_run(args: argparse.Namespace) -> int:
    spec, loader = _load(args.pipeline)
    mode = loader.mode(spec)
    print(f"OK  spec.version = {spec.version}")
    print(f"OK  mode = {mode}")
    print(f"OK  execution.selected_policy = {spec.execution.selected_policy}")
    print(f"OK  agents = {sorted(spec.agents.keys())}")
    print(f"OK  world.root = {spec.world.root}")
    print(f"OK  project.test_command = {spec.project.test_command}")
    return 0


def _cmd_mode(args: argparse.Namespace) -> int:
    spec, loader = _load(args.pipeline)
    print(loader.mode(spec))
    return 0


def _state_to_dict(state: State) -> dict:
    """Serialize a State for JSON output."""
    return {
        "version": state.version,
        "phase": state.phase,
        "iteration": state.iteration,
        "halt_signal": state.halt_signal,
        "halt_reason": state.halt_reason,
        "last_dev_commit": state.last_dev_commit,
        "last_review_verdict": state.last_review_verdict,
        "last_review_path": str(state.last_review_path) if state.last_review_path else None,
        "last_activity": state.last_activity,
        "history_len": len(state.history),
    }


def _print_human_summary(state: State) -> None:
    print("=" * 60)
    print(f"Final phase: {state.phase}")
    print(f"Iteration:   {state.iteration}")
    print(f"Halted:      {state.halt_signal} ({state.halt_reason or 'no reason'})")
    print(f"Last commit: {state.last_dev_commit or '-'}")
    print(f"Last verdict:{state.last_review_verdict or '-'}")
    print(f"Last review: {state.last_review_path or '-'}")
    print(f"Transitions: {len(state.history)}")
    print("=" * 60)


def _cmd_init(args: argparse.Namespace) -> int:
    """Run the interactive init wizard."""
    from unison.init_wizard import InitWizard

    wizard = InitWizard(project_root=args.output)
    wizard.run(
        description=args.description,
        preset=args.preset,
    )
    return 0


def _cmd_new(args: argparse.Namespace) -> int:
    """Generate pipeline.yaml + prompts/ from a natural-language description."""
    from unison.pipeline_generator import generate
    generate(
        description=args.description,
        output_dir=args.output,
        yes=args.yes,
        project_root=args.project_root,
    )
    return 0


def _cmd_webui(args: argparse.Namespace) -> int:
    """Start the web dashboard."""
    import os
    from unison.webui import serve
    token = args.token or os.environ.get("UNISON_WEBUI_TOKEN", "")
    serve(str(args.project), port=args.port, token=token)
    return 0


def _cmd_observe(args: argparse.Namespace) -> int:
    """Start the observer daemon."""
    from unison.world import World
    from unison.observer import Observer
    world = World(args.project.resolve())
    obs = Observer(world)
    obs.run()
    return 0


_HANDLERS = {
    "run": _cmd_run,
    "reconcile": _cmd_reconcile,
    "resume": _cmd_resume,
    "dry-run": _cmd_dry_run,
    "mode": _cmd_mode,
    "init": _cmd_init,
    "new": _cmd_new,
    "webui": _cmd_webui,
    "observe": _cmd_observe,
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = _HANDLERS[args.command]
    try:
        return handler(args)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except PipelineValidationError as e:
        print(f"VALIDATION ERROR: {e}", file=sys.stderr)
        return 1
    except RunAuthorizationError as e:
        print(f"AUTHORIZATION ERROR: {e}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
