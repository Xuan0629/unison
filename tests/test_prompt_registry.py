"""Tests for prompt_registry.py — PromptRegistry class."""
import tempfile
from pathlib import Path

import pytest

from unison.prompt_registry import PromptRegistry


class TestPromptRegistry:
    """PromptRegistry unit tests."""

    # ------------------------------------------------------------------
    # resolve()
    # ------------------------------------------------------------------

    def test_resolve_returns_file_content_when_path_exists(self, tmp_path):
        """resolve() returns file content when pipeline_path exists."""
        prompt_file = tmp_path / "custom.md"
        prompt_file.write_text("# Custom system prompt")

        registry = PromptRegistry()
        result = registry.resolve("developer", prompt_file)
        assert result == "# Custom system prompt"

    def test_resolve_returns_builtin_for_known_role_no_file(self):
        """resolve() returns DEFAULT_PROMPTS[role] when no file and role is known."""
        registry = PromptRegistry()
        result = registry.resolve("developer", None)
        assert "developer" in result.lower()
        assert result == registry.DEFAULT_PROMPTS["developer"]

    def test_resolve_returns_builtin_for_planner(self):
        """resolve() returns planner built-in when no file exists."""
        registry = PromptRegistry()
        result = registry.resolve("planner", None)
        assert "planner" in result.lower()
        assert result == registry.DEFAULT_PROMPTS["planner"]

    def test_resolve_returns_builtin_for_reviewer(self):
        """resolve() returns reviewer built-in when no file exists."""
        registry = PromptRegistry()
        result = registry.resolve("reviewer", None)
        assert "reviewer" in result.lower()
        assert result == registry.DEFAULT_PROMPTS["reviewer"]

    def test_resolve_returns_fallback_for_unknown_role(self):
        """resolve() returns generic fallback for completely unknown roles."""
        registry = PromptRegistry()
        result = registry.resolve("nonexistent-role", None)
        assert "nonexistent-role" in result

    def test_resolve_returns_fallback_when_path_does_not_exist_and_role_unknown(self):
        """resolve() returns generic fallback when file is missing and role unknown."""
        registry = PromptRegistry()
        missing = Path("/nonexistent/path.md")
        result = registry.resolve("custom-xyz", missing)
        assert result == "You are the custom-xyz agent."

    def test_resolve_file_takes_priority_over_builtin(self, tmp_path):
        """resolve() prefers pipeline file over built-in default."""
        prompt_file = tmp_path / "override.md"
        prompt_file.write_text("# Override content")

        registry = PromptRegistry()
        result = registry.resolve("developer", prompt_file)
        assert result == "# Override content"
        assert result != registry.DEFAULT_PROMPTS["developer"]

    def test_resolve_spec_verifier_role(self):
        """resolve() returns proper system prompt for spec-verifier role.

        spec-verifier is a known role referenced in the tech-design. Since it's
        not in DEFAULT_PROMPTS, it should get the generic fallback.
        """
        registry = PromptRegistry()
        result = registry.resolve("spec-verifier", None)
        assert "spec-verifier" in result

    # ------------------------------------------------------------------
    # task_for()
    # ------------------------------------------------------------------

    def test_task_for_developer(self):
        """task_for() returns developer operational constraints."""
        registry = PromptRegistry()
        task = registry.task_for(
            "developer", iteration=2, review_phase="dev_review",
            test_command="pytest tests/ -v",
        )
        assert "Iteration 2" in task
        assert "pytest tests/ -v" in task
        assert "Developer Operational Constraints" in task

    def test_task_for_planner(self):
        """task_for() returns planner product requirements instruction."""
        registry = PromptRegistry()
        task = registry.task_for("planner", iteration=1)
        assert "PRD" in task
        assert "tech-design" in task

    def test_task_for_reviewer(self):
        """task_for() returns reviewer instruction with review file path."""
        registry = PromptRegistry()
        task = registry.task_for(
            "reviewer", iteration=3, review_phase="dev_review",
            test_command="make test",
            review_file="reviews/iter-3.md",
        )
        assert "Iteration 3" in task
        assert "make test" in task
        assert "reviews/iter-3.md" in task
        assert "YAML frontmatter" in task

    def test_task_for_reviewer_includes_anti_sycophancy(self):
        """task_for() appends anti-sycophancy note when provided."""
        registry = PromptRegistry()
        task = registry.task_for(
            "reviewer", iteration=1, review_phase="dev_review",
            review_file="reviews/iter-1.md",
            anti_sycophancy_note="⚠️ ANTI-SYCOPHANCY: Be skeptical.",
        )
        assert "ANTI-SYCOPHANCY" in task

    def test_task_for_unknown_role_returns_fallback(self):
        """task_for() returns generic fallback for unknown roles."""
        registry = PromptRegistry()
        task = registry.task_for("custom-role", iteration=5)
        assert "custom-role" in task
        assert "5" in task
        assert "duties" in task

    # ------------------------------------------------------------------
    # DEFAULT_PROMPTS completeness
    # ------------------------------------------------------------------

    def test_default_prompts_has_all_known_roles(self):
        """DEFAULT_PROMPTS has entries for planner, developer, and reviewer."""
        registry = PromptRegistry()
        for role in ("planner", "developer", "reviewer"):
            assert role in registry.DEFAULT_PROMPTS, (
                f"DEFAULT_PROMPTS missing entry for '{role}'"
            )

    def test_default_tasks_has_all_known_roles(self):
        """DEFAULT_TASKS has entries for planner, developer, and reviewer."""
        registry = PromptRegistry()
        for role in ("planner", "developer", "reviewer"):
            assert role in registry.DEFAULT_TASKS, (
                f"DEFAULT_TASKS missing entry for '{role}'"
            )

    def test_default_prompts_are_non_empty(self):
        """Every DEFAULT_PROMPTS entry has non-empty content."""
        registry = PromptRegistry()
        for role, prompt in registry.DEFAULT_PROMPTS.items():
            assert prompt.strip(), f"DEFAULT_PROMPTS['{role}'] is empty"

    def test_default_tasks_are_non_empty(self):
        """Every DEFAULT_TASKS entry has non-empty content."""
        registry = PromptRegistry()
        for role, task in registry.DEFAULT_TASKS.items():
            assert task.strip(), f"DEFAULT_TASKS['{role}'] is empty"

    # ------------------------------------------------------------------
    # Placeholder contract — every role template must accept all standard
    # kwargs without KeyError, so that _build_prompt and _build_prompt_for_agent
    # can safely call .format(role=..., iteration=..., test_command=...,
    # review_file=...) for every role.
    # ------------------------------------------------------------------

    _ALL_FORMAT_KWARGS = {
        "role": "test-role",
        "iteration": 42,
        "test_command": "pytest tests/ -v",
        "review_file": "reviews/iter-42.md",
    }

    @pytest.mark.parametrize("role", ["planner", "developer", "reviewer"])
    def test_task_template_accepts_all_standard_kwargs(self, role):
        """Each built-in task template formats without KeyError for all kwargs."""
        registry = PromptRegistry()
        template = registry.DEFAULT_TASKS[role]
        # This must not raise KeyError
        result = template.format(**self._ALL_FORMAT_KWARGS)
        assert result.strip(), f"Formatted template for '{role}' is empty"

    # ------------------------------------------------------------------
    # Placeholder contract — each role's template carries only the
    # placeholder tokens it genuinely needs.  Planner does not receive
    # a test_command at runtime; developer does not write review files.
    # Using the same format() kwargs for every role keeps call sites
    # uniform, so templates must tolerate extra kwargs — but the tokens
    # that ARE in the template must match role expectations.
    # ------------------------------------------------------------------

    # role → set of placeholder tokens that MUST appear in the template
    _REQUIRED_PLACEHOLDERS: dict[str, set[str]] = {
        "planner":   {"{iteration}"},
        "developer": {"{iteration}", "{test_command}"},
        "reviewer":  {"{iteration}", "{test_command}", "{review_file}"},
    }

    # role → set of placeholder tokens that MUST NOT appear
    # (avoids silent drift where a token is added but call sites don't
    # pass a meaningful value for that role)
    _FORBIDDEN_PLACEHOLDERS: dict[str, set[str]] = {
        "planner":   {"{test_command}", "{review_file}"},
        "developer": {"{review_file}"},
        "reviewer":  set(),
    }

    @pytest.mark.parametrize("role", ["planner", "developer", "reviewer"])
    def test_task_template_contains_required_placeholders(self, role):
        """Each DEFAULT_TASKS template contains its role-appropriate tokens."""
        registry = PromptRegistry()
        template = registry.DEFAULT_TASKS[role]
        required = self._REQUIRED_PLACEHOLDERS.get(role, set())
        for token in required:
            assert token in template, (
                f"DEFAULT_TASKS['{role}'] missing required placeholder {token}"
            )

    @pytest.mark.parametrize("role", ["planner", "developer", "reviewer"])
    def test_task_template_avoids_forbidden_placeholders(self, role):
        """Each DEFAULT_TASKS template does NOT contain role-inappropriate tokens."""
        registry = PromptRegistry()
        template = registry.DEFAULT_TASKS[role]
        forbidden = self._FORBIDDEN_PLACEHOLDERS.get(role, set())
        for token in forbidden:
            assert token not in template, (
                f"DEFAULT_TASKS['{role}'] contains placeholder {token} "
                f"which is not appropriate for this role"
            )

    @pytest.mark.parametrize("role", ["planner", "developer", "reviewer"])
    def test_task_for_includes_iteration_for_all_roles(self, role):
        """task_for() output includes iteration number for every known role."""
        registry = PromptRegistry()
        task = registry.task_for(
            role, iteration=99,
            test_command="make test",
            review_file="reviews/iter-99.md",
        )
        assert "99" in task, (
            f"task_for('{role}') output missing iteration number"
        )
