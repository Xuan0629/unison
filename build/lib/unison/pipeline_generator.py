"""pipeline_generator.py — Interactive ``pipeline.yaml`` + prompts/ generator.

``unison new "description"`` auto-detects the pipeline mode from keywords
in the description, walks the user through a short Q&A, then writes:

  - ``pipeline.yaml``  (valid, loadable by PipelineLoader)
  - ``prompts/developer.md``  (generic task prompt)
  - ``prompts/reviewer.md``   (generic review prompt)
  - ``prompts/planner.md``    (only for ``full-dev`` and ``design-debate``)

Pass ``--yes`` / ``-y`` on the CLI to skip prompts and accept all defaults.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

from unison.interfaces import PipelineMode
from unison.prompt_registry import PromptRegistry

# ────────────────────────────────────────────────────────────────
# Keyword patterns for auto-detecting pipeline mode
# ────────────────────────────────────────────────────────────────

_KEYWORD_MODE_MAP: list[tuple[re.Pattern, PipelineMode]] = [
    # design-debate: talk about design, debate, architecture decisions, no code
    (re.compile(r"\b(design|debate|architecture|brainstorm|proposal|spec|RFC)\b",
                re.IGNORECASE), "design-debate"),
    # full-dev: plan + implement, plan-first, plan-then-build
    (re.compile(r"\b(plan|full.?(dev|stack|cycle)|PRD|design.then|spec.then)\b",
                re.IGNORECASE), "full-dev"),
    # code-dev (default fallback): code, implement, build, fix, refactor
    (re.compile(r"\b(code|implement|build|fix|refactor|feature|bug|patch)\b",
                re.IGNORECASE), "code-dev"),
]


def detect_mode(description: str) -> PipelineMode:
    """Auto-detect pipeline mode from natural-language description.

    Scans *description* for keywords associated with each mode. The first
    match wins; if nothing matches, defaults to ``"code-dev"``.

    Returns one of: ``"code-dev"``, ``"full-dev"``, ``"design-debate"``.
    """
    for pattern, mode in _KEYWORD_MODE_MAP:
        if pattern.search(description):
            return mode
    return "code-dev"


# ────────────────────────────────────────────────────────────────
# Default agent configs per mode
# ────────────────────────────────────────────────────────────────

_MODE_AGENT_TEMPLATES: dict[PipelineMode, dict] = {
    "code-dev": {
        "developer": {
            "role": "developer",
            "runtime": "claude",
            "model": "claude-sonnet-4-6",
            "system_prompt_path": "prompts/developer.md",
            "pipeline_role": "developer",
        },
        "reviewer": {
            "role": "reviewer",
            "runtime": "claude",
            "model": "claude-sonnet-4-6",
            "system_prompt_path": "prompts/reviewer.md",
            "pipeline_role": "reviewer",
        },
    },
    "full-dev": {
        "planner": {
            "role": "planner",
            "runtime": "claude",
            "model": "claude-sonnet-4-6",
            "system_prompt_path": "prompts/planner.md",
            "pipeline_role": "planner",
        },
        "developer": {
            "role": "developer",
            "runtime": "claude",
            "model": "claude-sonnet-4-6",
            "system_prompt_path": "prompts/developer.md",
            "pipeline_role": "developer",
        },
        "reviewer": {
            "role": "reviewer",
            "runtime": "claude",
            "model": "claude-sonnet-4-6",
            "system_prompt_path": "prompts/reviewer.md",
            "pipeline_role": "reviewer",
        },
    },
    "design-debate": {
        "planner_a": {
            "role": "designer-a",
            "runtime": "claude",
            "model": "claude-sonnet-4-6",
            "system_prompt_path": "prompts/planner.md",
            "pipeline_role": "planner",
        },
        "planner_b": {
            "role": "designer-b",
            "runtime": "codex",
            "model": "gpt-5.5",
            "system_prompt_path": "prompts/planner.md",
            "pipeline_role": "planner",
        },
        "developer": {
            "role": "developer",
            "runtime": "claude",
            "model": "claude-sonnet-4-6",
            "system_prompt_path": "prompts/developer.md",
            "pipeline_role": "developer",
        },
        "reviewer": {
            "role": "reviewer",
            "runtime": "claude",
            "model": "claude-sonnet-4-6",
            "system_prompt_path": "prompts/reviewer.md",
            "pipeline_role": "reviewer",
        },
    },
}

# Valid runtime / model suggestions
_VALID_RUNTIMES = ("claude", "codex", "hermes", "openclaw")

_DEFAULT_TEST_COMMAND = "pytest tests/ -v"
_DEFAULT_MAX_ITERATIONS = 5
_DEFAULT_PER_AGENT_TIMEOUT = 600


# ────────────────────────────────────────────────────────────────
# Pipeline YAML template
# ────────────────────────────────────────────────────────────────

def _build_pipeline_yaml(
    mode: PipelineMode,
    agents: dict,
    project_root: str = ".",
    test_command: str = _DEFAULT_TEST_COMMAND,
    max_iterations: int = _DEFAULT_MAX_ITERATIONS,
    per_agent_timeout: int = _DEFAULT_PER_AGENT_TIMEOUT,
) -> str:
    """Build the complete pipeline.yaml content as a string."""
    data: dict = {
        "version": "2.0",
        "project_root": project_root,
        "mode": mode,
        "agents": agents,
        "project": {
            "test_command": test_command,
            "max_iterations": max_iterations,
            "per_agent_timeout": per_agent_timeout,
        },
    }
    return yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)


# ────────────────────────────────────────────────────────────────
# Interactive Q&A
# ────────────────────────────────────────────────────────────────

def _prompt(prompt_text: str, default: str = "") -> str:
    """Print a prompt and return stripped user input.

    On EOF / non-interactive stdin, returns *default*.
    """
    try:
        if default:
            value = input(f"{prompt_text} [{default}]: ")
        else:
            value = input(f"{prompt_text}: ")
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return value.strip() or default


def _ask_yes_no(question: str, default: bool = True) -> bool:
    """Ask a yes/no question, return bool."""
    suffix = "Y/n" if default else "y/N"
    answer = _prompt(f"{question} ({suffix})", "Y" if default else "N")
    return answer.lower().startswith("y")


def _ask_runtime(agent_name: str, default: str) -> str:
    """Ask which runtime an agent should use."""
    runtimes = "/".join(_VALID_RUNTIMES)
    while True:
        answer = _prompt(f"  Runtime for {agent_name} ({runtimes})", default)
        if answer in _VALID_RUNTIMES:
            return answer
        print(f"  Invalid runtime '{answer}'. Choose: {runtimes}")


def _interactive_configure(
    description: str,
    detected_mode: PipelineMode,
    output_dir: Path,
) -> tuple[PipelineMode, dict, str, int, int]:
    """Walk user through interactive configuration.

    Returns (mode, agents_dict, test_command, max_iterations, per_agent_timeout).
    """
    print(f"\n{'='*60}")
    print(f"Unison Pipeline Generator")
    print(f"{'='*60}")
    print(f"\nDescription: {description}")
    print(f"Detected mode: {detected_mode}")

    # --- mode ---
    mode_choices = ["code-dev", "full-dev", "design-debate"]
    mode = detected_mode
    if not _ask_yes_no(f"Use mode '{mode}'?", default=True):
        print("  Available modes:")
        for i, m in enumerate(mode_choices, 1):
            marker = " <--" if m == detected_mode else ""
            print(f"    {i}. {m}{marker}")
        choice = _prompt("  Choose (1-3)", str(mode_choices.index(detected_mode) + 1))
        try:
            mode = mode_choices[int(choice) - 1]
        except (ValueError, IndexError):
            print(f"  Invalid choice, keeping '{detected_mode}'")
            mode = detected_mode

    # --- agents ---
    print(f"\n--- Agent Configuration ---")
    agents: dict = _MODE_AGENT_TEMPLATES.get(mode, _MODE_AGENT_TEMPLATES["code-dev"])
    agents = {k: dict(v) for k, v in agents.items()}  # deep copy

    for name, cfg in agents.items():
        print(f"\n  [{name}] role={cfg['role']}")
        cfg["runtime"] = _ask_runtime(name, cfg["runtime"])
        cfg["model"] = _prompt(f"  Model for {name}", cfg["model"])

    # --- project config ---
    print(f"\n--- Project Configuration ---")
    test_command = _prompt("Test command", _DEFAULT_TEST_COMMAND)
    max_iterations_str = _prompt("Max iterations", str(_DEFAULT_MAX_ITERATIONS))
    try:
        max_iterations = int(max_iterations_str)
    except ValueError:
        max_iterations = _DEFAULT_MAX_ITERATIONS
    timeout_str = _prompt("Per-agent timeout (seconds)", str(_DEFAULT_PER_AGENT_TIMEOUT))
    try:
        per_agent_timeout = int(timeout_str)
    except ValueError:
        per_agent_timeout = _DEFAULT_PER_AGENT_TIMEOUT

    # --- summary ---
    print(f"\n{'='*60}")
    print(f"Summary")
    print(f"{'='*60}")
    print(f"  Mode:          {mode}")
    print(f"  Agents:        {len(agents)} ({', '.join(agents.keys())})")
    print(f"  Test command:  {test_command}")
    print(f"  Max iters:     {max_iterations}")
    print(f"  Timeout:       {per_agent_timeout}s")
    print(f"  Output:        {output_dir.resolve()}")

    if not _ask_yes_no("\nGenerate files?", default=True):
        print("Aborted.")
        sys.exit(0)

    return mode, agents, test_command, max_iterations, per_agent_timeout


# ────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────

def generate(
    description: str,
    output_dir: Path | None = None,
    *,
    yes: bool = False,
    project_root: str = ".",
) -> Path:
    """Generate a pipeline.yaml and prompts/ from a natural-language description.

    Args:
        description: What the pipeline should do (e.g. "code review workflow").
        output_dir: Directory to write files into. Defaults to ``Path.cwd()``.
        yes: If ``True``, skip interactive prompts and use auto-detected defaults.
        project_root: Value for the ``project_root`` field in pipeline.yaml.

    Returns:
        Path to the generated ``pipeline.yaml``.
    """
    output_dir = Path(output_dir) if output_dir else Path.cwd()
    output_dir.mkdir(parents=True, exist_ok=True)

    detected_mode = detect_mode(description)

    if yes:
        mode = detected_mode
        agents = {
            k: dict(v)
            for k, v in _MODE_AGENT_TEMPLATES.get(mode, _MODE_AGENT_TEMPLATES["code-dev"]).items()
        }
        test_command = _DEFAULT_TEST_COMMAND
        max_iterations = _DEFAULT_MAX_ITERATIONS
        per_agent_timeout = _DEFAULT_PER_AGENT_TIMEOUT
    else:
        mode, agents, test_command, max_iterations, per_agent_timeout = _interactive_configure(
            description, detected_mode, output_dir,
        )

    # --- write prompts/ ---
    prompts_dir = output_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    registry = PromptRegistry()
    _write_prompt_file(
        prompts_dir / "developer.md",
        registry.DEFAULT_PROMPTS["developer"],
        description,
    )
    _write_prompt_file(
        prompts_dir / "reviewer.md",
        registry.DEFAULT_PROMPTS["reviewer"],
        description,
    )
    if mode in ("full-dev", "design-debate"):
        _write_prompt_file(
            prompts_dir / "planner.md",
            registry.DEFAULT_PROMPTS["planner"],
            description,
        )

    # --- write pipeline.yaml ---
    pipeline_yaml = _build_pipeline_yaml(
        mode=mode,
        agents=agents,
        project_root=project_root,
        test_command=test_command,
        max_iterations=max_iterations,
        per_agent_timeout=per_agent_timeout,
    )
    pipeline_path = output_dir / "pipeline.yaml"
    pipeline_path.write_text(pipeline_yaml, encoding="utf-8")

    # --- report ---
    print(f"\nGenerated:")
    print(f"  {pipeline_path}")
    print(f"  {prompts_dir}/developer.md")
    print(f"  {prompts_dir}/reviewer.md")
    if mode in ("full-dev", "design-debate"):
        print(f"  {prompts_dir}/planner.md")
    print(f"\nNext: unison run --pipeline {pipeline_path}")

    return pipeline_path


def _write_prompt_file(path: Path, content: str, description: str) -> None:
    """Prepend a description header and write the prompt content to disk."""
    header = f"# Task: {description}\n\n"
    path.write_text(header + content, encoding="utf-8")
