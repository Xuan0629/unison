"""init_wizard.py — Interactive pipeline.yaml generator for `unison init`.

``unison init`` walks the user through a guided Q&A to generate a
pipeline.yaml and prompt files. It reuses pipeline_generator internals
for YAML construction, prompt templates, and mode detection.

Non-interactive fallback: ``unison init --preset=<mode>``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from unison.pipeline_generator import (
    _MODE_AGENT_TEMPLATES,
    _VALID_RUNTIMES,
    _build_pipeline_yaml,
    _write_prompt_file,
    detect_mode,
)
from unison.prompt_registry import PromptRegistry

# Re-export for convenience
RUNTIMES = list(_VALID_RUNTIMES)

MODE_HINTS = {
    "code-dev": ["code", "implement", "build", "develop", "写代码", "开发", "实现"],
    "full-dev": ["full", "prd", "plan", "design first", "规划", "设计", "完整"],
    "design-debate": ["debate", "discuss", "review only", "讨论", "辩论", "审查"],
    "inspect-only": ["audit", "inspect", "check", "审计", "检查"],
}

_DEFAULT_TEST_COMMAND = "pytest tests/ -v"


class InitWizard:
    """Interactive Q&A that generates a pipeline.yaml + prompts/."""

    def __init__(self, project_root: Path):
        self.root = Path(project_root)

    def run(self, description: str | None = None, *, preset: str | None = None) -> Path:
        """Run the wizard and return path to generated pipeline.yaml.

        Args:
            description: What the user is building. If None, asked interactively.
            preset: If set (e.g. "code-dev"), skip all prompts and use defaults
                    for that mode. For non-interactive terminals.

        Returns:
            Path to the generated pipeline.yaml.
        """
        # ── non-interactive preset path ──────────────────────────
        if preset is not None:
            return self._run_preset(preset, description or "pipeline task")

        # ── interactive path ─────────────────────────────────────
        return self._run_interactive(description)

    def detect_mode(self, description: str) -> str:
        """Auto-detect pipeline mode from description text using keyword matching."""
        return detect_mode(description)

    # ── interactive Q&A ──────────────────────────────────────────

    def _run_interactive(self, description: str | None) -> Path:
        """Walk user through interactive Q&A, generate files, return pipeline path."""
        print(f"\n{'='*60}")
        print("  🔗  Unison Init — Interactive Onboarding")
        print(f"{'='*60}")

        # 1. What are you building?
        if description is None:
            description = self._prompt("\n? What are you building?", "")
            while not description.strip():
                description = self._prompt("? Please describe what you're building", "")
        description = description.strip()

        # 2. Detect mode
        mode = self.detect_mode(description)
        print(f"  → Detected mode: {mode}")
        mode = self._ask_mode(mode)

        # 3. How many developers?
        dev_count = self._ask_dev_count()

        # 4. Configure agents from template
        agents = self._configure_agents(mode, dev_count)

        # 5. Test command
        test_command = self._ask_test_command()

        # 6. Generate files
        pipeline_path = self._generate(mode, agents, test_command, description)

        # 7. Done
        self._print_success(pipeline_path, mode)

        return pipeline_path

    def _run_preset(self, preset_mode: str, description: str) -> Path:
        """Non-interactive path: use preset mode with all defaults."""
        valid_modes = list(_MODE_AGENT_TEMPLATES.keys())
        if preset_mode not in valid_modes:
            print(f"Invalid preset '{preset_mode}'. Choose: {', '.join(valid_modes)}")
            sys.exit(1)

        agents = {
            k: dict(v)
            for k, v in _MODE_AGENT_TEMPLATES[preset_mode].items()
        }
        test_command = _DEFAULT_TEST_COMMAND
        pipeline_path = self._generate(preset_mode, agents, test_command, description)
        self._print_success(pipeline_path, preset_mode)
        return pipeline_path

    # ── Q&A helpers ──────────────────────────────────────────────

    @staticmethod
    def _prompt(text: str, default: str = "") -> str:
        """Print prompt and return user input. Returns default on EOF."""
        try:
            if default:
                value = input(f"{text} [{default}]: ")
            else:
                value = input(f"{text} ")
        except (EOFError, KeyboardInterrupt):
            print()
            return default
        return value.strip() or default

    @staticmethod
    def _ask_yes_no(question: str, default: bool = True) -> bool:
        """Ask yes/no, return bool."""
        suffix = "Y/n" if default else "y/N"
        answer = InitWizard._prompt(f"{question} ({suffix})", "Y" if default else "N")
        return answer.lower().startswith("y")

    def _ask_mode(self, detected: str) -> str:
        """Confirm or change the detected mode."""
        if self._ask_yes_no(f"? Use mode '{detected}'?", default=True):
            return detected
        mode_choices = list(_MODE_AGENT_TEMPLATES.keys())
        print("  Available modes:")
        for i, m in enumerate(mode_choices, 1):
            marker = " <--" if m == detected else ""
            print(f"    {i}. {m}{marker}")
        choice = self._prompt("  Choose", str(mode_choices.index(detected) + 1))
        try:
            return mode_choices[int(choice) - 1]
        except (ValueError, IndexError):
            print(f"  Keeping '{detected}'")
            return detected

    def _ask_dev_count(self) -> int:
        """Ask how many developer agents."""
        raw = self._prompt("? How many developers?", "1")
        try:
            count = int(raw)
            return max(1, min(count, 10))
        except ValueError:
            return 1

    def _ask_runtime(self, agent_label: str, default: str) -> str:
        """Ask which runtime an agent should use."""
        runtimes_str = "/".join(_VALID_RUNTIMES)
        while True:
            answer = self._prompt(f"? Runtime for {agent_label} ({runtimes_str})", default)
            if answer in _VALID_RUNTIMES:
                return answer
            print(f"  Invalid runtime '{answer}'. Choose: {runtimes_str}")

    def _configure_agents(self, mode: str, dev_count: int) -> dict:
        """Build agent config dict from template, customizing runtimes interactively."""
        template = _MODE_AGENT_TEMPLATES.get(mode, _MODE_AGENT_TEMPLATES["code-dev"])
        agents: dict = {k: dict(v) for k, v in template.items()}  # deep copy

        print(f"\n  --- Agent Configuration ---")
        for name, cfg in agents.items():
            label = f"{name} ({cfg['role']})"
            cfg["runtime"] = self._ask_runtime(label, cfg["runtime"])
            cfg["model"] = self._prompt(f"? Model for {name}", cfg["model"])

        return agents

    def _ask_test_command(self) -> str:
        """Ask for the project test command."""
        return self._prompt("? Test command?", _DEFAULT_TEST_COMMAND)

    # ── generation ───────────────────────────────────────────────

    def _generate(
        self, mode: str, agents: dict, test_command: str, description: str
    ) -> Path:
        """Write pipeline.yaml + prompts/ to self.root. Return path to yaml."""
        self.root.mkdir(parents=True, exist_ok=True)

        # Write prompts/
        prompts_dir = self.root / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        _write_prompt_file(
            prompts_dir / "developer.md",
            PromptRegistry.DEFAULT_PROMPTS["developer"],
            description,
        )
        _write_prompt_file(
            prompts_dir / "reviewer.md",
            PromptRegistry.DEFAULT_PROMPTS["reviewer"],
            description,
        )
        if mode in ("full-dev", "design-debate"):
            _write_prompt_file(
                prompts_dir / "planner.md",
                PromptRegistry.DEFAULT_PROMPTS["planner"],
                description,
            )

        # Write pipeline.yaml
        yaml_content = _build_pipeline_yaml(
            mode=mode,
            agents=agents,
            project_root=str(self.root.resolve()),
            test_command=test_command,
        )
        pipeline_path = self.root / "pipeline.yaml"
        pipeline_path.write_text(yaml_content, encoding="utf-8")

        return pipeline_path

    @staticmethod
    def _print_success(pipeline_path: Path, mode: str) -> None:
        """Print a success summary after generation."""
        prompts_dir = pipeline_path.parent / "prompts"
        print()
        print(f"✅ Created {pipeline_path}")
        print(f"✅ Created {prompts_dir / 'developer.md'}")
        print(f"✅ Created {prompts_dir / 'reviewer.md'}")
        if mode in ("full-dev", "design-debate"):
            print(f"✅ Created {prompts_dir / 'planner.md'}")
        print(f"\n→ Run: unison run --pipeline {pipeline_path}")
