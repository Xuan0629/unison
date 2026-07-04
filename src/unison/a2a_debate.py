"""a2a_debate.py — A2A (Agent-to-Agent) asynchronous debate mode.

Multi-agent debate via filesystem communication. Agents write position
papers to inbox/, critiques to outbox/, and the orchestrator manages
rounds until convergence or max_rounds exceeded.

Design: stateless per-round invocation, filesystem-only audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from interfaces import PipelineSpec, World
from unison.state import State, Transition


@dataclass
class DebateRound:
    """One round of a multi-agent debate."""

    round_number: int
    agents: list[str]
    papers: dict[str, Path] = field(default_factory=dict)
    critiques: dict[str, Path] = field(default_factory=dict)

    def has_converged(self, prev_round: "DebateRound | None") -> bool:
        """True when no new arguments appeared vs previous round."""
        if prev_round is None:
            return False
        # TODO: implement convergence detection
        return False


class A2ADebateMode:
    """Run a multi-agent filesystem-based debate.

    Round 1: All planners write position papers → inbox/
    Round 2: All reviewers read papers → write critiques → inbox/
    Round 3: Planners read critiques → rebuttals
    ... repeat until convergence or max_rounds.

    Communication is filesystem-only (inbox/outbox JSONL).
    Each round is a fresh stateless invoke.
    """

    def __init__(self, spec: PipelineSpec, world: World, max_rounds: int = 3):
        self.spec = spec
        self.world = world
        self.max_rounds = max_rounds
        self.rounds: list[DebateRound] = []

    def run(self, topic: str) -> Path:
        """Execute the debate and return path to synthesis document."""
        # TODO: implement debate loop
        for rn in range(1, self.max_rounds + 1):
            current = DebateRound(round_number=rn, agents=[])
            prev = self.rounds[-1] if self.rounds else None
            # TODO: run planners
            # TODO: run reviewers
            # TODO: write papers/critiques to inbox/outbox
            self.rounds.append(current)
            if current.has_converged(prev):
                break

        # TODO: write synthesis document
        synthesis = self.world.reports_dir / "debate-synthesis.md"
        synthesis.parent.mkdir(parents=True, exist_ok=True)
        synthesis.write_text("# Debate Synthesis\n\nTODO: aggregate all rounds\n")
        return synthesis
