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
        "planner::spec-driven": (
            "# SDD Planner Prompt\n\n"
            "You are a planner agent in the Unison multi-agent pipeline "
            "operating in **Spec-Driven Development (SDD)** mode.\n\n"
            "## SDD Mode: 4 Required Artifacts\n"
            "You MUST produce exactly these 4 artifacts. The pipeline will "
            "verify they all exist before development begins.\n\n"
            "### 1. prd/proposal.md\n"
            "Problem statement, solution overview, scope, and success criteria. "
            "Must be substantive (>500 bytes).\n\n"
            "### 2. prd/design.md\n"
            "Technical architecture, data flow, component design, and trade-offs. "
            "Must be substantive (>500 bytes).\n\n"
            "### 3. prd/specs/<feature>.md\n"
            "One or more Gherkin-style specification files. Every spec file "
            "MUST contain at least one scenario using **GIVEN / WHEN / THEN** "
            "keywords. These are machine-verified before development.\n\n"
            "### 4. prd/tasks.md\n"
            "Ordered, concrete implementation tasks. Each task should be "
            "small enough that a developer can complete it in one iteration.\n\n"
            "## Workflow\n"
            "1. Read the project context and requirements\n"
            "2. Write proposal.md covering problem, solution, and scope\n"
            "3. Write design.md covering architecture and technical decisions\n"
            "4. Write specs/*.md files with Gherkin scenarios (GIVEN/WHEN/THEN)\n"
            "5. Write tasks.md with ordered implementation steps\n\n"
            "## Important\n"
            "- Do NOT write PRD.md or tech-design.md — use the 4-artifact format\n"
            "- Every spec file MUST include GIVEN/WHEN/THEN scenarios\n"
            "- The pipeline gate will reject empty or placeholder specs\n"
        ),
        "spec-verifier": (
            "# Spec Verifier Prompt\n\n"
            "You are a spec-verifier agent in the Unison multi-agent pipeline.\n\n"
            "## Responsibilities\n"
            "1. Verify that all 4 SDD artifacts exist and are substantive\n"
            "2. Check prd/proposal.md (>500 bytes)\n"
            "3. Check prd/design.md (>500 bytes)\n"
            "4. Check prd/specs/*.md contain GIVEN/WHEN/THEN scenarios\n"
            "5. Check prd/tasks.md exists\n"
            "6. Report missing or inadequate artifacts\n"
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
        "planner::spec-driven": (
            "Iteration {iteration} — SDD Planner Operational Constraints:\n"
            "Write the 4 SDD artifacts:\n"
            "1. prd/proposal.md — Problem statement, solution, scope\n"
            "2. prd/design.md — Technical architecture, trade-offs\n"
            "3. prd/specs/<feature>.md — Gherkin scenarios (GIVEN/WHEN/THEN)\n"
            "4. prd/tasks.md — Ordered implementation tasks\n"
            "Do NOT write prd/PRD.md or prd/tech-design.md."
        ),
        "developer": (
            "Iteration {iteration} — Developer Operational Constraints:\n"
            "- Read prd/PRD.md and prd/tech-design.md for requirements context\n"
            "- Run tests after changes: {test_command}\n"
            "- Commit with: git add -A && git commit -m '<descriptive message>'\n"
            "- Follow the Developer Instructions below for your specific task"
        ),
        "developer::spec-driven": (
            "Iteration {iteration} — Developer Operational Constraints:\n"
            "- Read prd/proposal.md and prd/design.md for requirements context\n"
            "- Read prd/specs/ for Gherkin scenario specifications\n"
            "- Read prd/tasks.md for ordered implementation steps\n"
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

    def resolve(self, role: str, pipeline_path: Path | None = None, mode: str | None = None) -> str:
        """Resolve a system prompt for *role* with priority:

        1. *pipeline_path* file content (when the file exists)
        2. ``DEFAULT_PROMPTS["{role}::{mode}"]`` (when *mode* is set and the
           mode-specific key exists)
        3. ``DEFAULT_PROMPTS[role]`` (when *role* is a known built-in)
        4. Generic fallback ``"You are the {role} agent."``

        Args:
            role: Agent role name (e.g. ``"developer"``, ``"planner"``).
            pipeline_path: Path to a pipeline-specific prompt file, or ``None``.
            mode: Optional pipeline mode (e.g. ``"spec-driven"``) for
                mode-specific prompt lookup.

        Returns:
            The resolved system prompt string.
        """
        # Priority 1: pipeline-specific file
        if pipeline_path is not None and pipeline_path.exists():
            return pipeline_path.read_text(encoding="utf-8")

        # Priority 2: mode-specific built-in (e.g. "planner::spec-driven")
        if mode is not None:
            mode_key = f"{role}::{mode}"
            if mode_key in self.DEFAULT_PROMPTS:
                return self.DEFAULT_PROMPTS[mode_key]

        # Priority 3: built-in default for known roles
        if role in self.DEFAULT_PROMPTS:
            return self.DEFAULT_PROMPTS[role]

        # Priority 4: generic fallback
        return f"You are the {role} agent."

    def task_for(
        self,
        role: str,
        iteration: int,
        review_phase: str = "dev_review",
        test_command: str = "",
        review_file: str = "",
        anti_sycophancy_note: str = "",
        carry_forward: str = "",
        mode: str | None = None,
    ) -> str:
        """Return a role-specific task instruction with variables substituted.

        When *mode* is set, the registry first tries the mode-specific key
        ``"{role}::{mode}"`` in :attr:`DEFAULT_TASKS`, falling back to the
        generic *role* key.

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
            carry_forward: Optional finding carry-forward block (developer
                only).  Appended after the task instruction.
            mode: Optional pipeline mode (e.g. ``"spec-driven"``) for
                mode-specific task lookup.

        Returns:
            The formatted task instruction string.
        """
        # Priority 1: mode-specific key (e.g. "developer::spec-driven")
        if mode is not None:
            mode_key = f"{role}::{mode}"
            if mode_key in self.DEFAULT_TASKS:
                template = self.DEFAULT_TASKS[mode_key]
            else:
                template = self.DEFAULT_TASKS.get(
                    role,
                    "Perform {role} duties for iteration {iteration}.",
                )
        else:
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

        if carry_forward:
            result += f"\n\n{carry_forward}"

        return result
