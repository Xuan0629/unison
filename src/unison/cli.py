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
import sys
from pathlib import Path

from unison.auth import RunAuthorizationError, authorize_run
from unison.interfaces import TRUSTED_LOCAL_PRINCIPAL
from unison.orchestrator import Orchestrator
from unison.pipeline import PipelineLoader, PipelineValidationError
from unison.state import State


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
        help="Replace missing runtimes: --switch codex:claude,hermes:claude",
    )
    run.add_argument(
        "--save-pref", action="store_true",
        help="Save switch/model preferences to pipeline.yaml",
    )
    run.add_argument(
        "--model", type=str, default=None,
        help="Override agent model: --model developer:deepseek-v4-pro",
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
    """Parse key:value pairs from --switch or --model flags.

    'developer:claude,reviewer:hermes' -> {'developer': 'claude', 'reviewer': 'hermes'}
    """
    if not flag_arg:
        return {}
    result = {}
    for pair in flag_arg.split(','):
        parts = pair.strip().split(':', 1)
        if len(parts) == 2:
            result[parts[0].strip()] = parts[1].strip()
    return result


def _check_tools(spec, switches: dict[str,str] | None = None) -> bool:
    """Pre-flight: check all required tools. Returns True if all OK.

    If tools are missing, prints actionable error messages and returns False.
    The caller should halt the pipeline.
    """
    import shutil
    # Collect (runtime, agent_key) pairs, applying --switch if configured
    needed: dict[str, list[str]] = {}
    for agent_key, agent in spec.agents.items():
        runtime = getattr(agent, 'runtime', '')
        if runtime and runtime not in ('openclaw',):
            # --switch overrides the runtime for this agent
            effective = (switches or {}).get(agent_key, runtime)
            needed.setdefault(effective, []).append(agent_key)

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
        from unison.interfaces import World
        project_root = Path(args.project).resolve()
        spec = replace(spec, world=World(root=project_root))

    if not authorize_run(spec, TRUSTED_LOCAL_PRINCIPAL):
        print(
            "AUTHORIZATION ERROR: local CLI is not allowed by who_can_run",
            file=sys.stderr,
        )
        return 3

    orchestrator = Orchestrator(spec=spec, dry_run=args.dry_run)

    # Pre-flight: check required tools (halt if missing)
    switches = _parse_kv(args.switch)
    model_overrides = _parse_kv(args.model) if hasattr(args, 'model') else {}
    if not _check_tools(spec, switches):
        print("\nTip: use --switch <agent>:<runtime> to remap, --model <agent>:<model> to change model, --save-pref to persist")
        return 1

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


def _cmd_dry_run(args: argparse.Namespace) -> int:
    spec, loader = _load(args.pipeline)
    mode = loader.mode(spec)
    print(f"OK  spec.version = {spec.version}")
    print(f"OK  mode = {mode}")
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
    from unison.webui import serve
    serve(str(args.project), port=args.port, token=args.token)
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
