"""Tests for a2a_debate.py — A2A Debate Mode."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from interfaces import AgentSpec, PipelineSpec, World, AgentResult
from unison.a2a_debate import (
    A2ADebateMode,
    DebateRound,
    _extract_header_topics,
    _runner_for_runtime,
    _make_log_path,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _make_world(root: Path) -> World:
    """Create a minimal World rooted at *root*."""
    return World(root=root)


def _make_agent_spec(
    role: str = "developer",
    runtime: str = "claude",
    model: str = "test-model",
    pipeline_role: str | None = None,
) -> AgentSpec:
    """Create a minimal AgentSpec for testing."""
    return AgentSpec(
        role=role,
        runtime=runtime,  # type: ignore[arg-type]
        model=model,
        system_prompt_path=Path("prompts/test.md"),
        pipeline_role=pipeline_role,
    )


def _make_spec(
    world: World,
    agents: dict[str, AgentSpec] | None = None,
    max_rounds: int = 3,
) -> PipelineSpec:
    """Create a minimal PipelineSpec for testing."""
    if agents is None:
        agents = {
            "planner_a": _make_agent_spec("designer-a", "claude", pipeline_role="planner"),
            "planner_b": _make_agent_spec("designer-b", "codex", pipeline_role="planner"),
            "reviewer": _make_agent_spec("reviewer", "claude", pipeline_role="reviewer"),
        }
    return PipelineSpec(
        version="2.0",
        world=world,
        agents=agents,
    )


# ------------------------------------------------------------------
# _extract_header_topics tests
# ------------------------------------------------------------------


class TestExtractHeaderTopics:
    """Tests for _extract_header_topics()."""

    def test_basic_headers(self, tmp_path: Path):
        """Extract ATX-style markdown headers from a file."""
        path = tmp_path / "test.md"
        path.write_text("""# Introduction
Some text here.

## Background
More text.

### Detail
Deep dive.

# Conclusion
Final thoughts.
""")
        topics = _extract_header_topics(path)
        assert topics == {"Introduction", "Background", "Detail", "Conclusion"}

    def test_mixed_header_levels(self, tmp_path: Path):
        """Extract headers of varying depths."""
        path = tmp_path / "test.md"
        path.write_text("""###### deepest
## middle
# top
#### four
""")
        topics = _extract_header_topics(path)
        assert topics == {"deepest", "middle", "top", "four"}

    def test_non_existent_file(self):
        """Return empty set for non-existent file."""
        topics = _extract_header_topics(Path("/nonexistent/path.md"))
        assert topics == set()

    def test_empty_file(self, tmp_path: Path):
        """Return empty set for empty file."""
        path = tmp_path / "empty.md"
        path.write_text("")
        topics = _extract_header_topics(path)
        assert topics == set()

    def test_no_headers(self, tmp_path: Path):
        """Return empty set when file has no markdown headers."""
        path = tmp_path / "no_headers.md"
        path.write_text("This is just plain text.\nNo headers here.\n")
        topics = _extract_header_topics(path)
        assert topics == set()

    def test_header_with_extra_whitespace(self, tmp_path: Path):
        """Strip leading/trailing whitespace from header topics."""
        path = tmp_path / "test.md"
        path.write_text("#   padded header    \n")
        topics = _extract_header_topics(path)
        assert topics == {"padded header"}


# ------------------------------------------------------------------
# DebateRound.has_converged tests
# ------------------------------------------------------------------


class TestDebateRoundConvergence:
    """Tests for DebateRound.has_converged()."""

    def test_no_prev_round(self):
        """Round 1 never converges (no previous round to compare)."""
        rd = DebateRound(round_number=1, agents=["planner_a"])
        assert rd.has_converged(None) is False

    def test_same_topics_converges(self, tmp_path: Path):
        """Convergence when both rounds have identical header topics."""
        # Round 1
        paper1_r1 = tmp_path / "r1_paper.md"
        paper1_r1.write_text("# Alpha\n## Beta\n")
        round1 = DebateRound(
            round_number=1,
            agents=["planner_a"],
            papers={"planner_a": paper1_r1},
        )

        # Round 2 — same topics
        paper1_r2 = tmp_path / "r2_paper.md"
        paper1_r2.write_text("# Alpha\n## Beta\n")
        round2 = DebateRound(
            round_number=2,
            agents=["planner_a"],
            papers={"planner_a": paper1_r2},
        )

        assert round2.has_converged(round1) is True

    def test_new_topics_no_convergence(self, tmp_path: Path):
        """No convergence when new topics appear."""
        paper1_r1 = tmp_path / "r1_paper.md"
        paper1_r1.write_text("# Alpha\n")
        round1 = DebateRound(
            round_number=1,
            agents=["planner_a"],
            papers={"planner_a": paper1_r1},
        )

        paper1_r2 = tmp_path / "r2_paper.md"
        paper1_r2.write_text("# Alpha\n## Gamma\n")
        round2 = DebateRound(
            round_number=2,
            agents=["planner_a"],
            papers={"planner_a": paper1_r2},
        )

        assert round2.has_converged(round1) is False

    def test_fewer_topics_converges(self, tmp_path: Path):
        """Convergence when current round has fewer topics (no new ones)."""
        paper1_r1 = tmp_path / "r1_paper.md"
        paper1_r1.write_text("# Alpha\n## Beta\n### Gamma\n")
        round1 = DebateRound(
            round_number=1,
            agents=["planner_a"],
            papers={"planner_a": paper1_r1},
        )

        paper1_r2 = tmp_path / "r2_paper.md"
        paper1_r2.write_text("# Alpha\n## Beta\n")
        round2 = DebateRound(
            round_number=2,
            agents=["planner_a"],
            papers={"planner_a": paper1_r2},
        )

        assert round2.has_converged(round1) is True

    def test_multi_agent_convergence(self, tmp_path: Path):
        """Convergence across multiple agents."""
        # Round 1
        pa_r1 = tmp_path / "pa_r1.md"
        pa_r1.write_text("# Alpha\n## Common\n")
        pb_r1 = tmp_path / "pb_r1.md"
        pb_r1.write_text("# Beta\n## Common\n")
        round1 = DebateRound(
            round_number=1,
            agents=["planner_a", "planner_b"],
            papers={"planner_a": pa_r1, "planner_b": pb_r1},
        )

        # Round 2 — same topics
        pa_r2 = tmp_path / "pa_r2.md"
        pa_r2.write_text("# Alpha\n## Common\n")
        pb_r2 = tmp_path / "pb_r2.md"
        pb_r2.write_text("# Beta\n## Common\n")
        round2 = DebateRound(
            round_number=2,
            agents=["planner_a", "planner_b"],
            papers={"planner_a": pa_r2, "planner_b": pb_r2},
        )

        assert round2.has_converged(round1) is True

    def test_multi_agent_one_new_topic(self, tmp_path: Path):
        """No convergence when one agent adds a new topic."""
        pa_r1 = tmp_path / "pa_r1.md"
        pa_r1.write_text("# Alpha\n")
        pb_r1 = tmp_path / "pb_r1.md"
        pb_r1.write_text("# Beta\n")
        round1 = DebateRound(
            round_number=1,
            agents=["planner_a", "planner_b"],
            papers={"planner_a": pa_r1, "planner_b": pb_r1},
        )

        pa_r2 = tmp_path / "pa_r2.md"
        pa_r2.write_text("# Alpha\n## New Idea\n")
        pb_r2 = tmp_path / "pb_r2.md"
        pb_r2.write_text("# Beta\n")
        round2 = DebateRound(
            round_number=2,
            agents=["planner_a", "planner_b"],
            papers={"planner_a": pa_r2, "planner_b": pb_r2},
        )

        assert round2.has_converged(round1) is False

    def test_missing_agent_in_prev(self, tmp_path: Path):
        """Agent present in current round but not in previous."""
        pa_r2 = tmp_path / "pa_r2.md"
        pa_r2.write_text("# Alpha\n# New Topic\n")
        round1 = DebateRound(round_number=1, agents=["planner_a"])
        round2 = DebateRound(
            round_number=2,
            agents=["planner_a", "planner_b"],
            papers={"planner_a": pa_r2},
        )

        # New agent's topics should also be considered new
        assert round2.has_converged(round1) is False

    def test_missing_paper_file(self):
        """Missing paper file → treated as empty (no topics)."""
        round1 = DebateRound(
            round_number=1,
            agents=["planner_a"],
            papers={"planner_a": Path("/nonexistent.md")},
        )
        round2 = DebateRound(
            round_number=2,
            agents=["planner_a"],
            papers={"planner_a": Path("/another_nonexistent.md")},
        )

        assert round2.has_converged(round1) is True


# ------------------------------------------------------------------
# _runner_for_runtime tests
# ------------------------------------------------------------------


class TestRunnerForRuntime:
    """Tests for _runner_for_runtime()."""

    def test_claude(self):
        runner = _runner_for_runtime("claude")
        from unison.runners.claude import ClaudeRunner
        assert isinstance(runner, ClaudeRunner)

    def test_codex(self):
        runner = _runner_for_runtime("codex")
        from unison.runners.codex import CodexRunner
        assert isinstance(runner, CodexRunner)

    def test_hermes(self):
        runner = _runner_for_runtime("hermes")
        from unison.runners.hermes import HermesRunner
        assert isinstance(runner, HermesRunner)

    def test_openclaw(self):
        runner = _runner_for_runtime("openclaw")
        from unison.runners.openclaw import OpenClawRunner
        assert isinstance(runner, OpenClawRunner)

    def test_unknown_runtime(self):
        with pytest.raises(ValueError, match="Unknown runtime"):
            _runner_for_runtime("nonexistent")


# ------------------------------------------------------------------
# _make_log_path tests
# ------------------------------------------------------------------


class TestMakeLogPath:
    """Tests for _make_log_path()."""

    def test_creates_log_path(self, tmp_path: Path):
        world = _make_world(tmp_path)
        log = _make_log_path(world, "planner_a", 1)
        assert log.parent.exists()
        assert "debate_planner_a_round1" in log.name
        assert log.suffix == ".log"


# ------------------------------------------------------------------
# A2ADebateMode unit tests
# ------------------------------------------------------------------


class TestA2ADebateModeCreation:
    """Tests for A2ADebateMode instantiation."""

    def test_create(self, tmp_path: Path):
        world = _make_world(tmp_path)
        spec = _make_spec(world)
        mode = A2ADebateMode(spec, world)
        assert mode.spec is spec
        assert mode.world is world
        assert mode.max_rounds == 3
        assert mode.rounds == []

    def test_custom_max_rounds(self, tmp_path: Path):
        world = _make_world(tmp_path)
        spec = _make_spec(world)
        mode = A2ADebateMode(spec, world, max_rounds=5)
        assert mode.max_rounds == 5


class TestA2ADebateModePromptBuilders:
    """Tests for prompt builder methods."""

    def test_planner_prompt_round1(self, tmp_path: Path):
        world = _make_world(tmp_path)
        spec = _make_spec(world)
        mode = A2ADebateMode(spec, world)
        output = tmp_path / "paper.md"
        prompt = mode._build_planner_prompt("agent_x", "Test topic", 1, output)
        assert "Round 1" in prompt
        assert "agent_x" in prompt
        assert "Test topic" in prompt
        assert "position paper" in prompt.lower()
        assert str(output) in prompt

    def test_planner_prompt_rebuttal_round(self, tmp_path: Path):
        world = _make_world(tmp_path)
        spec = _make_spec(world)
        mode = A2ADebateMode(spec, world)
        output = tmp_path / "rebuttal.md"
        prompt = mode._build_planner_prompt("agent_x", "Topic", 3, output)
        assert "Round 3" in prompt
        assert "rebuttal" in prompt.lower()
        assert "outbox" in prompt

    def test_reviewer_prompt(self, tmp_path: Path):
        world = _make_world(tmp_path)
        spec = _make_spec(world)
        mode = A2ADebateMode(spec, world)
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        p1 = inbox / "planner_a_round2.md"
        p1.write_text("# Test\n")
        output = tmp_path / "critique.md"
        prompt = mode._build_reviewer_prompt(
            "reviewer_x", "Topic", 2, inbox, [p1], output,
        )
        assert "Round 2" in prompt
        assert "reviewer_x" in prompt
        assert "Topic" in prompt
        assert "Logical soundness" in prompt
        assert str(output) in prompt


class TestA2ADebateModeSynthesis:
    """Tests for synthesis document writing."""

    def test_write_synthesis(self, tmp_path: Path):
        world = _make_world(tmp_path)
        spec = _make_spec(world)
        mode = A2ADebateMode(spec, world, max_rounds=2)

        # Simulate two rounds
        inbox = world.inbox_dir
        inbox.mkdir(parents=True, exist_ok=True)

        r1_paper = inbox / "planner_a_round1.md"
        r1_paper.write_text("# First Argument\n## Sub Point\n")
        round1 = DebateRound(
            round_number=1,
            agents=["planner_a", "planner_b", "reviewer"],
            papers={"planner_a": r1_paper},
            critiques={},
        )

        r2_paper = inbox / "planner_a_round2.md"
        r2_paper.write_text("# First Argument\n## Refined Point\n")
        round2 = DebateRound(
            round_number=2,
            agents=["planner_a", "planner_b", "reviewer"],
            papers={"planner_a": r2_paper},
            critiques={},
        )

        mode.rounds = [round1, round2]
        path = mode._write_synthesis("Test Debate Topic")

        assert path.exists()
        content = path.read_text()
        assert "Debate Synthesis" in content
        assert "Test Debate Topic" in content
        assert "Round 1" in content
        assert "Round 2" in content
        assert "First Argument" in content

    def test_synthesis_converged_status(self, tmp_path: Path):
        world = _make_world(tmp_path)
        spec = _make_spec(world)
        mode = A2ADebateMode(spec, world, max_rounds=3)

        inbox = world.inbox_dir
        inbox.mkdir(parents=True, exist_ok=True)

        # Single round → not converged
        r1_paper = inbox / "p_round1.md"
        r1_paper.write_text("# Topic A\n")
        mode.rounds = [
            DebateRound(
                round_number=1,
                agents=["planner_a"],
                papers={"planner_a": r1_paper},
            )
        ]
        path = mode._write_synthesis("Topic")
        content = path.read_text()
        assert "No (stopped at round 1)" in content

    def test_synthesis_empty_rounds(self, tmp_path: Path):
        world = _make_world(tmp_path)
        spec = _make_spec(world)
        mode = A2ADebateMode(spec, world)
        path = mode._write_synthesis("Empty topic")
        content = path.read_text()
        assert "N/A" in content  # inside **Converged**: N/A markdown


class TestA2ADebateModeConvergedAtRound:
    """Tests for _converged_at_round()."""

    def test_empty(self, tmp_path: Path):
        world = _make_world(tmp_path)
        spec = _make_spec(world)
        mode = A2ADebateMode(spec, world)
        assert mode._converged_at_round() == "N/A"

    def test_converged_before_max(self, tmp_path: Path):
        world = _make_world(tmp_path)
        spec = _make_spec(world)
        mode = A2ADebateMode(spec, world, max_rounds=3)
        # Same topics → converged at round 2
        p1 = tmp_path / "r1.md"
        p1.write_text("# A\n")
        p2 = tmp_path / "r2.md"
        p2.write_text("# A\n")
        mode.rounds = [
            DebateRound(round_number=1, agents=["planner_a"], papers={"planner_a": p1}),
            DebateRound(round_number=2, agents=["planner_a"], papers={"planner_a": p2}),
        ]
        assert "Yes (round 2)" in mode._converged_at_round()

    def test_max_rounds_without_convergence(self, tmp_path: Path):
        world = _make_world(tmp_path)
        spec = _make_spec(world)
        mode = A2ADebateMode(spec, world, max_rounds=2)
        p1 = tmp_path / "r1.md"
        p1.write_text("# A\n")
        p2 = tmp_path / "r2.md"
        p2.write_text("# B\n")
        mode.rounds = [
            DebateRound(round_number=1, agents=["planner_a"], papers={"planner_a": p1}),
            DebateRound(round_number=2, agents=["planner_a"], papers={"planner_a": p2}),
        ]
        assert "No (max rounds reached)" in mode._converged_at_round()


# ------------------------------------------------------------------
# A2ADebateMode.run() integration tests
# ------------------------------------------------------------------


class MockRunner:
    """A mock AgentRunner that writes the expected output files.

    Instead of calling real subprocesses, this writes a minimal
    markdown document to the output path specified in the prompt.
    """

    def __init__(self):
        self.calls: list[dict] = []

    def run(
        self,
        spec: AgentSpec,
        prompt: str,
        workdir: Path,
        timeout: int,
        log_path: Path,
    ) -> AgentResult:
        """Record the call and write expected output files."""
        self.calls.append({
            "spec": spec,
            "prompt": prompt,
            "workdir": workdir,
        })
        # Find the output file path in the prompt and create it
        for line in prompt.splitlines():
            if "**Output file**:" in line:
                line = line.replace("**Output file**:", "").strip()
                output_path = Path(line)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                # Write a meaningful header so convergence tests work
                role_tag = "planner" if "Planner" in prompt else "reviewer"
                output_path.write_text(
                    f"# {role_tag} {spec.role} — Round argument\n"
                    f"## Core position\n"
                    f"This is the core argument.\n"
                )
        # Create the log file
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("mock log output\n")
        return AgentResult(
            success=True, exit_code=0, duration=0.1,
            stdout_tail="mock", stderr_tail="", log_path=log_path,
        )


class TestA2ADebateModeRun:
    """Integration tests for A2ADebateMode.run() with mocked runners."""

    def test_run_creates_synthesis(self, tmp_path: Path):
        """End-to-end: run() creates synthesis with expected content."""
        world = _make_world(tmp_path)
        world.reports_dir.mkdir(parents=True, exist_ok=True)

        agents = {
            "planner_a": _make_agent_spec("designer-a", "claude", pipeline_role="planner"),
            "reviewer": _make_agent_spec("reviewer", "claude", pipeline_role="reviewer"),
        }
        spec = _make_spec(world, agents=agents, max_rounds=2)

        mock_runner = MockRunner()

        with patch("unison.a2a_debate._runner_for_runtime", return_value=mock_runner):
            mode = A2ADebateMode(spec, world, max_rounds=2)
            result_path = mode.run("Test debate topic")

        # Synthesis was created
        assert result_path.exists()
        assert result_path.name == "debate-synthesis.md"

        content = result_path.read_text()
        assert "Debate Synthesis" in content
        assert "Test debate topic" in content

        # Ensure agents were invoked each round
        planner_calls = [c for c in mock_runner.calls if "Planner" in c["prompt"]]
        reviewer_calls = [c for c in mock_runner.calls if "Reviewer" in c["prompt"]]
        assert len(planner_calls) == 2  # 2 rounds
        assert len(reviewer_calls) == 2

    def test_run_rounds_tracked(self, tmp_path: Path):
        """Rounds are tracked in self.rounds after run()."""
        world = _make_world(tmp_path)
        world.reports_dir.mkdir(parents=True, exist_ok=True)

        agents = {
            "planner_a": _make_agent_spec("designer-a", "claude", pipeline_role="planner"),
            "reviewer": _make_agent_spec("reviewer", "claude", pipeline_role="reviewer"),
        }
        spec = _make_spec(world, agents=agents, max_rounds=2)

        mock_runner = MockRunner()

        with patch("unison.a2a_debate._runner_for_runtime", return_value=mock_runner):
            mode = A2ADebateMode(spec, world, max_rounds=2)
            mode.run("Topic")

        assert len(mode.rounds) == 2
        assert mode.rounds[0].round_number == 1
        assert mode.rounds[1].round_number == 2

    def test_run_convergence_breaks_early(self, tmp_path: Path):
        """Debate stops early when rounds converge (same topics)."""
        world = _make_world(tmp_path)
        world.reports_dir.mkdir(parents=True, exist_ok=True)

        agents = {
            "planner_a": _make_agent_spec("designer-a", "claude", pipeline_role="planner"),
            "reviewer": _make_agent_spec("reviewer", "claude", pipeline_role="reviewer"),
        }
        spec = _make_spec(world, agents=agents, max_rounds=3)

        mock_runner = MockRunner()

        with patch("unison.a2a_debate._runner_for_runtime", return_value=mock_runner):
            mode = A2ADebateMode(spec, world, max_rounds=3)
            mode.run("Topic")

        # MockRunner always writes same headers → should converge at round 2
        assert len(mode.rounds) == 2

    def test_run_no_planners(self, tmp_path: Path):
        """Debate with only reviewers runs without crashing."""
        world = _make_world(tmp_path)
        world.reports_dir.mkdir(parents=True, exist_ok=True)

        agents = {
            "reviewer": _make_agent_spec("reviewer", "claude", pipeline_role="reviewer"),
        }
        spec = _make_spec(world, agents=agents, max_rounds=2)

        mock_runner = MockRunner()

        with patch("unison.a2a_debate._runner_for_runtime", return_value=mock_runner):
            mode = A2ADebateMode(spec, world, max_rounds=2)
            result_path = mode.run("Topic")

        assert result_path.exists()

    def test_run_no_reviewers(self, tmp_path: Path):
        """Debate with only planners runs without crashing."""
        world = _make_world(tmp_path)
        world.reports_dir.mkdir(parents=True, exist_ok=True)

        agents = {
            "planner_a": _make_agent_spec("designer-a", "claude", pipeline_role="planner"),
        }
        spec = _make_spec(world, agents=agents, max_rounds=2)

        mock_runner = MockRunner()

        with patch("unison.a2a_debate._runner_for_runtime", return_value=mock_runner):
            mode = A2ADebateMode(spec, world, max_rounds=2)
            result_path = mode.run("Topic")

        assert result_path.exists()

    def test_run_inbox_files_created(self, tmp_path: Path):
        """Planner papers are written to inbox/."""
        world = _make_world(tmp_path)
        world.reports_dir.mkdir(parents=True, exist_ok=True)

        agents = {
            "planner_a": _make_agent_spec("designer-a", "claude", pipeline_role="planner"),
            "reviewer": _make_agent_spec("reviewer", "claude", pipeline_role="reviewer"),
        }
        spec = _make_spec(world, agents=agents, max_rounds=2)

        mock_runner = MockRunner()

        with patch("unison.a2a_debate._runner_for_runtime", return_value=mock_runner):
            mode = A2ADebateMode(spec, world, max_rounds=2)
            mode.run("Topic")

        assert (world.inbox_dir / "planner_a_round1.md").exists()
        assert (world.inbox_dir / "planner_a_round2.md").exists()

    def test_run_outbox_files_created(self, tmp_path: Path):
        """Reviewer critiques are written to outbox/."""
        world = _make_world(tmp_path)
        world.reports_dir.mkdir(parents=True, exist_ok=True)

        agents = {
            "planner_a": _make_agent_spec("designer-a", "claude", pipeline_role="planner"),
            "reviewer": _make_agent_spec("reviewer", "claude", pipeline_role="reviewer"),
        }
        spec = _make_spec(world, agents=agents, max_rounds=2)

        mock_runner = MockRunner()

        with patch("unison.a2a_debate._runner_for_runtime", return_value=mock_runner):
            mode = A2ADebateMode(spec, world, max_rounds=2)
            mode.run("Topic")

        assert (world.outbox_dir / "reviewer_round1.md").exists()
        assert (world.outbox_dir / "reviewer_round2.md").exists()


class TestA2ADebateModePipelineRoleFiltering:
    """Tests that agents are correctly filtered by pipeline_role."""

    def test_pipeline_role_fallback(self, tmp_path: Path):
        """effective_role falls back to role when pipeline_role is None."""
        agent = _make_agent_spec("developer", "claude", pipeline_role=None)
        assert agent.effective_role == "developer"

    def test_pipeline_role_override(self, tmp_path: Path):
        """effective_role uses pipeline_role when set."""
        agent = _make_agent_spec("designer-a", "claude", pipeline_role="planner")
        assert agent.effective_role == "planner"
