"""a2a_debate.py — A2A (Agent-to-Agent) asynchronous debate mode.

Multi-agent debate via filesystem communication. Agents write position
papers to inbox/, critiques to outbox/, and the orchestrator manages
rounds until convergence or max_rounds exceeded.

Design: stateless per-round invocation, filesystem-only audit trail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from unison.interfaces import AgentSpec, PipelineSpec
from unison.world import World
from unison.runners.claude import ClaudeRunner
from unison.runners.codex import CodexRunner
from unison.runners.hermes import HermesRunner
from unison.runners.openclaw import OpenClawRunner
from unison.runners.base import AgentRunner


# ------------------------------------------------------------------
# Runner registry
# ------------------------------------------------------------------

_RUNNER_REGISTRY: dict[str, type[AgentRunner]] = {
    "claude": ClaudeRunner,
    "codex": CodexRunner,
    "hermes": HermesRunner,
    "openclaw": OpenClawRunner,
}


def _runner_for_runtime(runtime: str) -> AgentRunner:
    """Return a concrete runner instance for the given *runtime* string.

    Args:
        runtime: One of ``"claude"``, ``"codex"``, ``"hermes"``, ``"openclaw"``.

    Returns:
        An instantiated runner matching the runtime.

    Raises:
        ValueError: If *runtime* is unknown.
    """
    runner_cls = _RUNNER_REGISTRY.get(runtime)
    if runner_cls is None:
        raise ValueError(
            f"Unknown runtime {runtime!r}. Known: {list(_RUNNER_REGISTRY)}"
        )
    return runner_cls()


# ------------------------------------------------------------------
# DebateRound
# ------------------------------------------------------------------


@dataclass
class DebateRound:
    """One round of a multi-agent debate."""

    round_number: int
    agents: list[str]
    papers: dict[str, Path] = field(default_factory=dict)
    critiques: dict[str, Path] = field(default_factory=dict)

    def has_converged(self, prev_round: "DebateRound | None") -> bool:
        """True when no new arguments appeared vs previous round.

        Convergence is detected by extracting markdown header topics
        from each agent's position paper and comparing the current
        round's topic set against the previous round's. If no new
        topics have appeared, the debate has converged.

        Args:
            prev_round: The preceding round, or ``None`` for round 1.

        Returns:
            ``True`` if no round is needed (debate converged).
        """
        if prev_round is None:
            return False

        prev_topics: set[str] = set()
        curr_topics: set[str] = set()

        for agent_name in self.agents:
            prev_paper = prev_round.papers.get(agent_name)
            if prev_paper is not None:
                prev_topics |= _extract_header_topics(prev_paper)

            curr_paper = self.papers.get(agent_name)
            if curr_paper is not None:
                curr_topics |= _extract_header_topics(curr_paper)

        # Converged if current round introduces no new topics
        new_topics = curr_topics - prev_topics
        return len(new_topics) == 0


def _extract_header_topics(path: Path) -> set[str]:
    """Extract markdown header lines from *path* as a set of topic strings.

    Matches ATX-style headers (``#`` through ``######``) and strips
    leading ``#`` markers and surrounding whitespace. Returns an empty
    set if the file does not exist or cannot be read.
    """
    topics: set[str] = set()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return topics

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            # Strip leading # markers and whitespace
            topic = stripped.lstrip("#").strip()
            if topic:
                topics.add(topic)
    return topics


# ------------------------------------------------------------------
# A2ADebateMode
# ------------------------------------------------------------------


class A2ADebateMode:
    """Run a multi-agent filesystem-based debate.

    Round 1: All planners write position papers → inbox/
    Round 2: All reviewers read papers → write critiques → outbox/
    Round 3: Planners read critiques → rebuttals
    ... repeat until convergence or max_rounds.

    Communication is filesystem-only (inbox/outbox).
    Each round is a fresh stateless invoke.
    """

    def __init__(self, spec: PipelineSpec, world: World, max_rounds: int = 3):
        self.spec = spec
        self.world = world
        self.max_rounds = max_rounds
        self.rounds: list[DebateRound] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, topic: str) -> Path:
        """Execute the debate and return path to synthesis document.

        Args:
            topic: The debate topic / question to resolve.

        Returns:
            Path to the synthesis document written to
            ``<reports_dir>/debate-synthesis.md``.
        """
        # --- 0. Identify agents by pipeline_role ---
        planners: dict[str, AgentSpec] = {}
        reviewers: dict[str, AgentSpec] = {}
        for name, agent_spec in self.spec.agents.items():
            if agent_spec.effective_role == "planner":
                planners[name] = agent_spec
            elif agent_spec.effective_role == "reviewer":
                reviewers[name] = agent_spec

        all_agent_names = list(planners.keys()) + list(reviewers.keys())

        # Ensure communication directories exist
        self.world.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.world.outbox_dir.mkdir(parents=True, exist_ok=True)

        # --- 1. Debate loop ---
        for rn in range(1, self.max_rounds + 1):
            current = DebateRound(round_number=rn, agents=list(all_agent_names))
            prev = self.rounds[-1] if self.rounds else None

            # a. Invoke planners → write position papers to inbox/
            for name, agent_spec in planners.items():
                paper_path = self.world.inbox_dir / f"{name}_round{rn}.md"
                self._invoke_planner(name, agent_spec, topic, rn, paper_path)
                current.papers[name] = paper_path

            # b. Invoke reviewers → read papers, write critiques to outbox/
            for name, agent_spec in reviewers.items():
                critique_path = self.world.outbox_dir / f"{name}_round{rn}.md"
                self._invoke_reviewer(name, agent_spec, topic, rn, critique_path)
                current.critiques[name] = critique_path

            self.rounds.append(current)

            # c. Check convergence
            if current.has_converged(prev):
                break

        # --- 2. Write synthesis ---
        return self._write_synthesis(topic)

    # ------------------------------------------------------------------
    # Agent invocation helpers
    # ------------------------------------------------------------------

    def _invoke_planner(
        self,
        name: str,
        agent_spec: AgentSpec,
        topic: str,
        round_num: int,
        output_path: Path,
    ) -> None:
        """Invoke a single planner agent for one round.

        The planner is asked to write its position paper to *output_path*.
        """
        runner = _runner_for_runtime(agent_spec.runtime)
        prompt = self._build_planner_prompt(
            agent_name=name,
            topic=topic,
            round_num=round_num,
            output_path=output_path,
        )
        log_path = _make_log_path(self.world, name, round_num)
        runner.run(
            spec=agent_spec,
            prompt=prompt,
            workdir=self.world.root,
            timeout=self.spec.per_agent_timeout,
            log_path=log_path,
        )

    def _invoke_reviewer(
        self,
        name: str,
        agent_spec: AgentSpec,
        topic: str,
        round_num: int,
        output_path: Path,
    ) -> None:
        """Invoke a single reviewer agent for one round.

        The reviewer reads all papers in ``inbox/`` and writes its
        critique to *output_path*.
        """
        runner = _runner_for_runtime(agent_spec.runtime)
        prompt = self._build_reviewer_prompt(
            agent_name=name,
            topic=topic,
            round_num=round_num,
            inbox_dir=self.world.inbox_dir,
            all_papers=list(self.world.inbox_dir.glob(f"*_round{round_num}.md")),
            output_path=output_path,
        )
        log_path = _make_log_path(self.world, name, round_num)
        runner.run(
            spec=agent_spec,
            prompt=prompt,
            workdir=self.world.root,
            timeout=self.spec.per_agent_timeout,
            log_path=log_path,
        )

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    def _build_planner_prompt(
        self,
        agent_name: str,
        topic: str,
        round_num: int,
        output_path: Path,
    ) -> str:
        """Build the prompt for a planner agent.

        On round 1 the planner writes a fresh position paper. On later
        rounds it reads the previous critiques from ``outbox/`` and
        writes a rebuttal.
        """
        lines = [
            f"# Debate Planner Task — Round {round_num}",
            "",
            f"You are **{agent_name}**, a planner agent in a multi-agent debate.",
            "",
            f"## Topic",
            f"{topic}",
            "",
            f"## Round {round_num} Instructions",
        ]
        if round_num == 1:
            lines.extend([
                "",
                "Write a position paper that presents your initial arguments,",
                "analysis, and recommended approach. Structure your paper with",
                "clear markdown headers for each major argument or section.",
                "",
                f"**Output file**: {output_path}",
                "",
                "Write only to this file. Do not modify any other files.",
            ])
        else:
            lines.extend([
                "",
                "Read all critiques from the previous round in `outbox/`.",
                "Write a rebuttal that addresses each critique point-by-point.",
                "If you agree with a critique, acknowledge it and adjust your position.",
                "Structure your rebuttal with clear markdown headers.",
                "",
                f"**Output file**: {output_path}",
                "",
                "Write only to this file. Do not modify any other files.",
            ])
        return "\n".join(lines)

    def _build_reviewer_prompt(
        self,
        agent_name: str,
        topic: str,
        round_num: int,
        inbox_dir: Path,
        all_papers: list[Path],
        output_path: Path,
    ) -> str:
        """Build the prompt for a reviewer agent.

        The reviewer reads all position papers from this round and writes
        a structured critique.
        """
        paper_lines = ""
        for p in sorted(all_papers):
            paper_lines += f"- `{p.relative_to(inbox_dir)}`\n"

        return "\n".join([
            f"# Debate Reviewer Task — Round {round_num}",
            "",
            f"You are **{agent_name}**, a reviewer agent in a multi-agent debate.",
            "",
            f"## Topic",
            f"{topic}",
            "",
            f"## Round {round_num} Instructions",
            "",
            f"Read all position papers from this round in `{inbox_dir}`:",
            "",
            paper_lines,
            "Write a critique that evaluates each paper's arguments for:",
            "",
            "1. **Logical soundness** — are the arguments internally consistent?",
            "2. **Factual accuracy** — are claims supported by evidence?",
            "3. **Completeness** — does the paper address all aspects of the topic?",
            "4. **Novelty** — does this paper introduce new arguments vs prior rounds?",
            "",
            "Structure your critique with clear markdown headers for each paper reviewed.",
            "",
            f"**Output file**: {output_path}",
            "",
            "Write only to this file. Do not modify any other files.",
        ])

    # ------------------------------------------------------------------
    # Synthesis
    # ------------------------------------------------------------------

    def _write_synthesis(self, topic: str) -> Path:
        """Aggregate all rounds into a synthesis document.

        The final agreed-upon position is placed at the top, followed
        by a round-by-round summary of papers and critiques.
        """
        synthesis_path = self.world.reports_dir / "debate-synthesis.md"
        synthesis_path.parent.mkdir(parents=True, exist_ok=True)

        lines: list[str] = [
            f"# Debate Synthesis",
            "",
            f"**Topic**: {topic}",
            f"**Total rounds**: {len(self.rounds)}",
            f"**Max rounds**: {self.max_rounds}",
            f"**Converged**: {self._converged_at_round()}",
            "",
            "---",
            "",
        ]

        # Final agreed-upon position (from last round's papers)
        if self.rounds:
            last_round = self.rounds[-1]
            lines.append("## Final Position")
            lines.append("")
            if last_round.papers:
                lines.append(
                    f"The debate concluded after {len(self.rounds)} round(s). "
                    f"The final position papers are:"
                )
                lines.append("")
                for agent_name, paper_path in sorted(last_round.papers.items()):
                    lines.append(f"- **{agent_name}**: `{paper_path}`")
                    lines.append("")
                    lines.append("  Summary of final argument:")
                    lines.append("")
                    topics = _extract_header_topics(paper_path)
                    for topic_text in sorted(topics):
                        lines.append(f"  - {topic_text}")
                    lines.append("")
            else:
                lines.append("No position papers were produced.")
                lines.append("")

        lines.append("---")
        lines.append("")

        # Round-by-round summary
        for rd in self.rounds:
            lines.append(f"## Round {rd.round_number}")
            lines.append("")

            lines.append("### Position Papers")
            lines.append("")
            if rd.papers:
                for agent_name, paper_path in sorted(rd.papers.items()):
                    lines.append(f"- **{agent_name}**: `{paper_path}`")
                    lines.append("")
                    # Show a preview of topics
                    topics = _extract_header_topics(paper_path)
                    if topics:
                        lines.append("  Topics:")
                        for t in sorted(topics):
                            lines.append(f"  - {t}")
                    lines.append("")
            else:
                lines.append("No papers in this round.")
                lines.append("")

            lines.append("### Critiques")
            lines.append("")
            if rd.critiques:
                for agent_name, critique_path in sorted(rd.critiques.items()):
                    lines.append(f"- **{agent_name}**: `{critique_path}`")
                    topics = _extract_header_topics(critique_path)
                    if topics:
                        for t in sorted(topics):
                            lines.append(f"  - {t}")
                lines.append("")
            else:
                lines.append("No critiques in this round.")
                lines.append("")

            lines.append("---")
            lines.append("")

        synthesis_path.write_text("\n".join(lines), encoding="utf-8")
        return synthesis_path

    def _converged_at_round(self) -> str:
        """Return a human-readable convergence summary."""
        if not self.rounds:
            return "N/A"
        if len(self.rounds) >= 2 and self.rounds[-1].has_converged(self.rounds[-2]):
            return f"Yes (round {len(self.rounds)})"
        if len(self.rounds) < self.max_rounds:
            return f"No (stopped at round {len(self.rounds)})"
        return "No (max rounds reached)"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_log_path(world: World, agent_name: str, round_num: int) -> Path:
    """Build a log file path for an agent invocation.

    Uses the observer logs directory with a timestamp to avoid collisions.
    """
    world.logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return world.logs_dir / f"debate_{agent_name}_round{round_num}_{timestamp}.log"
