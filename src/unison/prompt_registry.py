"""prompt_registry.py — Unified Prompt Template Management.

Single source of truth for built-in system prompts and task instructions.
Replaces hardcoded string constants in orchestrator.py and pipeline_generator.py.

Resolution priority:
    1. Pipeline ``system_prompt_path`` file (per-project override)
    2. Registry built-in ``DEFAULT_PROMPTS[role]``
    3. Generic fallback ``"You are the {role} agent."``
"""

from __future__ import annotations

from pathlib import Path


class PromptRegistry:
    """Central registry for built-in prompt defaults and task instructions.

    Usage::

        registry = PromptRegistry()

        # Resolve a system prompt (file > built-in > fallback)
        prompt = registry.resolve("developer", Path("prompts/developer.md"))

        # Get a role-specific task instruction
        task = registry.task_for("developer", iteration=1, review_phase="dev_review",
                                 test_command="pytest", review_file="reviews/iter-1.md")
    """

    # ------------------------------------------------------------------
    # Built-in system prompts (role → default prompt text)
    # ------------------------------------------------------------------

    DEFAULT_PROMPTS: dict[str, str] = {
        "planner": (
            "# Planner Prompt\n\n"
            "You are a planner agent in the Unison multi-agent pipeline.\n\n"
            "## Responsibilities\n"
            "1. Read the project context (PRD, tech-design, existing code)\n"
            "2. Break the task into clear, ordered steps\n"
            "3. Write a plan the developer can execute\n"
            "4. After developer completes, review the plan execution\n"
            "5. Adjust plan if reviewer finds issues\n\n"
            "## Output Format\n"
            "Produce a structured plan with:\n"
            "- Phase breakdown\n"
            "- Files to create or modify\n"
            "- Success criteria for each phase\n"
            "- Dependencies between phases\n"
        ),
        "developer": (
            "# Developer Prompt\n\n"
            "You are a developer agent in the Unison multi-agent pipeline.\n\n"
            "## Workflow\n"
            "1. Read the relevant specification files (PRD, tech-design, existing code)\n"
            "2. Write code in `src/`, tests in `tests/`\n"
            "3. Run the test command to verify your work\n"
            "4. Commit your changes: `git add -A && git commit -m \"<message>\"`\n"
            "5. Await reviewer feedback\n\n"
            "## If Reviewer Returns REQUEST_CHANGES\n"
            "- Only fix the specific issues raised\n"
            "- Do not change unrelated code\n"
            "- Re-run tests after fixing\n"
            "- Commit again\n\n"
            "## Constraints\n"
            "- Match existing code patterns and style\n"
            "- Minimal diffs — no reformatting\n"
            "- No unrelated refactors\n"
        ),
        "reviewer": (
            "# Reviewer Prompt\n\n"
            "You are a reviewer agent in the Unison multi-agent pipeline.\n\n"
            "## Review Checklist\n"
            "1. **Correctness** — Does the code do what was asked?\n"
            "2. **Minimality** — No unrelated changes, no reformatting?\n"
            "3. **Pattern-match** — Does it follow existing project conventions?\n"
            "4. **Edge cases** — Are errors and edge cases handled?\n"
            "5. **Tests** — Are tests present and passing?\n\n"
            "## Output Format (MUST follow)\n"
            "```yaml\n"
            "---\n"
            "verdict: PASS | REQUEST_CHANGES\n"
            "summary: <one-sentence summary>\n"
            "findings:\n"
            "  - [severity: critical/major/minor] <description + fix suggestion>\n"
            "---\n"
            "```\n\n"
            "## Rules\n"
            "- Do NOT modify src/ or tests/\n"
            "- Find at least 1 improvement (mark [RARE: NO_FINDINGS] if truly none)\n"
            "- Same issue repeated across iterations → escalate severity\n"
        ),
    }

    # ------------------------------------------------------------------
    # Built-in task instructions (role → default task template)
    # ------------------------------------------------------------------

    DEFAULT_TASKS: dict[str, str] = {
        "planner": (
            "Iteration {iteration} — Planner Operational Constraints:\n"
            "Write the Product Requirements Document to prd/PRD.md "
            "and the technical design to prd/tech-design.md."
        ),
        "developer": (
            "Iteration {iteration} — Developer Operational Constraints:\n"
            "- Read prd/PRD.md and prd/tech-design.md for requirements context\n"
            "- Run tests after changes: {test_command}\n"
            "- Commit with: git add -A && git commit -m '<descriptive message>'\n"
            "- Follow the Developer Instructions below for your specific task"
        ),
        "reviewer": (
            "Review Iteration {iteration}: "
            "1. Run tests: {test_command} "
            "2. Write review to {review_file} "
            "3. Use YAML frontmatter: verdict, summary, findings, metrics "
            "4. Do NOT modify src/"
        ),
    }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, role: str, pipeline_path: Path | None = None) -> str:
        """Resolve a system prompt for *role* with priority:

        1. *pipeline_path* file content (when the file exists)
        2. ``DEFAULT_PROMPTS[role]`` (when *role* is a known built-in)
        3. Generic fallback ``"You are the {role} agent."``

        Args:
            role: Agent role name (e.g. ``"developer"``, ``"planner"``).
            pipeline_path: Path to a pipeline-specific prompt file, or ``None``.

        Returns:
            The resolved system prompt string.
        """
        # Priority 1: pipeline-specific file
        if pipeline_path is not None and pipeline_path.exists():
            return pipeline_path.read_text(encoding="utf-8")

        # Priority 2: built-in default for known roles
        if role in self.DEFAULT_PROMPTS:
            return self.DEFAULT_PROMPTS[role]

        # Priority 3: generic fallback
        return f"You are the {role} agent."

    def task_for(
        self,
        role: str,
        iteration: int,
        review_phase: str = "dev_review",
        test_command: str = "",
        review_file: str = "",
        anti_sycophancy_note: str = "",
    ) -> str:
        """Return a role-specific task instruction with variables substituted.

        Args:
            role: Agent role (``"planner"``, ``"developer"``, ``"reviewer"``).
            iteration: Current iteration number.
            review_phase: ``"planning_review"`` or ``"dev_review"`` (unused in
                template substitution; kept for call-site compatibility).
            test_command: Project test command (substituted into template).
            review_file: Path to the review output file (substituted into
                the reviewer template).
            anti_sycophancy_note: Optional anti-sycophancy reminder appended
                after the task instruction (reviewer only).

        Returns:
            The formatted task instruction string.
        """
        template = self.DEFAULT_TASKS.get(
            role,
            "Perform {role} duties for iteration {iteration}.",
        )

        result = template.format(
            role=role,
            iteration=iteration,
            test_command=test_command,
            review_file=review_file,
        )

        if anti_sycophancy_note:
            result += f"\n{anti_sycophancy_note}"

        return result
