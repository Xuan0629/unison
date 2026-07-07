"""phase_router.py — Maps pipeline mode → ordered list of phase definitions.

Extracted from orchestrator.py _DISPATCH.  Adding a new phase is now a matter
of adding a ``PhaseDef`` — no orchestrator logic changes needed.

Note: ``moa`` mode is NOT listed here — it bypasses PhaseRouter entirely
and is driven by ``MoaConfig.rounds`` in ``_run_moa_pipeline()``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


# ============================================================================
# PhaseDef
# ============================================================================


@dataclass
class PhaseDef:
    """A single phase in the pipeline state machine.

    Each phase either runs as a standard active→review loop via
    ``Orchestrator._run_loop()``, or is handled as a special-case phase
    (``name == "review"`` → ``_run_review_only``, ``active_phase ==
    "spec-check"`` → ``_run_spec_verification``, ``name == "discuss"``
    → ``_run_discussion_loop``).
    """

    name: str           # "planning", "dev", "discuss", "review", "spec-check"
    active_phase: str   # "planning_active", "dev_active", "discuss_active", ...
    review_phase: str   # "planning_review", "dev_review", "discuss_review", ""
    role: str           # "planner", "developer", "reviewer"
    review_of: str      # "PRD + tech-design", "code + tests", "implementation proposal", ...


# ============================================================================
# PhaseRouter
# ============================================================================


@dataclass
class PhaseRouter:
    """Maps pipeline mode → ordered list of ``PhaseDef``.

    Usage::

        phases = PhaseRouter.get_phases("full-dev")
        for pd in phases:
            orchestrator._run_loop(
                pd.active_phase, pd.review_phase, pd.role, pd.review_of,
            )
    """

    PHASES_BY_MODE: ClassVar[dict[str, list[PhaseDef]]] = {
        "code-dev": [
            PhaseDef("dev", "dev_active", "dev_review", "developer",
                     "code + tests"),
        ],
        "full-dev": [
            PhaseDef("planning", "planning_active", "planning_review",
                     "planner", "PRD + tech-design"),
            PhaseDef("discuss", "discuss_active", "discuss_review",
                     "developer", "implementation proposal"),
            PhaseDef("dev", "dev_active", "dev_review", "developer",
                     "code + tests"),
        ],
        "design-debate": [
            PhaseDef("planning", "planning_active", "planning_review",
                     "planner", "PRD + tech-design"),
        ],
        "inspect-only": [
            PhaseDef("review", "dev_review", "", "reviewer", "codebase"),
        ],
        "agent-fix": [
            PhaseDef("dev", "dev_active", "dev_review", "developer",
                     "code + tests"),
        ],
        "migrate": [
            PhaseDef("planning", "planning_active", "planning_review",
                     "planner", "PRD + tech-design"),
            PhaseDef("discuss", "discuss_active", "discuss_review",
                     "developer", "implementation proposal"),
            PhaseDef("dev", "dev_active", "dev_review", "developer",
                     "code + tests"),
        ],
        "greenfield": [
            PhaseDef("dev", "dev_active", "dev_review", "developer",
                     "code + tests"),
        ],
        "spec-driven": [
            PhaseDef("planning", "planning_active", "planning_review",
                     "planner", "PRD + tech-design"),
            PhaseDef("spec-check", "spec-check", "", "", ""),
            PhaseDef("discuss", "discuss_active", "discuss_review",
                     "developer", "implementation proposal"),
            PhaseDef("dev", "dev_active", "dev_review", "developer",
                     "code + tests"),
        ],
    }

    @classmethod
    def get_phases(cls, mode: str) -> list[PhaseDef]:
        """Return the ordered list of ``PhaseDef`` for *mode*.

        Args:
            mode: Pipeline mode name (e.g. ``"full-dev"``, ``"code-dev"``).

        Returns:
            Ordered list of ``PhaseDef`` instances.  Returns an empty list
            for unknown modes (caller should handle the error).
        """
        return cls.PHASES_BY_MODE.get(mode, [])
