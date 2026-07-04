"""init_wizard.py — Interactive pipeline.yaml generator for `unison init`."""

from __future__ import annotations

from pathlib import Path

# Supported agent runtimes
RUNTIMES = ["claude", "codex", "hermes", "openclaw"]

# Mode detection keywords
MODE_HINTS = {
    "code-dev": ["code", "implement", "build", "develop", "写代码", "开发", "实现"],
    "full-dev": ["full", "prd", "plan", "design first", "规划", "设计", "完整"],
    "design-debate": ["debate", "discuss", "review only", "讨论", "辩论", "审查"],
    "inspect-only": ["audit", "inspect", "check", "审计", "检查"],
}


class InitWizard:
    """Interactive Q&A that generates a pipeline.yaml + prompts/."""

    def __init__(self, project_root: Path):
        self.root = project_root

    def run(self, description: str | None = None) -> Path:
        """Run the wizard and return path to generated pipeline.yaml."""
        # TODO: interactive Q&A
        # 1. Ask what to build → detect mode
        # 2. Ask how many developers → configure agents
        # 3. Ask runtimes → claude/codex/hermes
        # 4. Ask test command
        # 5. Generate pipeline.yaml + prompts/
        # 6. Support --preset=<mode> for non-interactive mode
        return self.root / "pipeline.yaml"

    def detect_mode(self, description: str) -> str:
        """Auto-detect pipeline mode from description text."""
        # TODO: keyword matching → mode
        return "code-dev"
