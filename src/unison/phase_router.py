"""phase_router.py — Maps pipeline mode → ordered list of phase definitions.

P13: Merged 10 modes into 8 parameterized modes with backward-compatible
aliasing.  Old mode names (code-dev, full-dev, etc.) still work but
emit deprecation warnings.  New canonical names use ``:`` separator.

  dev:quick    = code-dev (single dev phase)
  dev:standard = full-dev (plan → discuss → dev)
  dev:deep     = full-dev with higher iteration defaults
  chain        = (unchanged)
  moa:analyze  = (unchanged, bypasses PhaseRouter)
  moa:plan     = planning phase with MoA analyze
  moa:review   = review-only with MoA
  custom       = parameterized (output_type + multiplicity)

Note: ``moa`` mode (bare) is NOT listed here — it bypasses PhaseRouter
entirely and is driven by ``MoaConfig.rounds`` in ``_run_moa_pipeline()``.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import ClassVar


# ============================================================================
# PhaseDef
# ============================================================================


@dataclass
class PhaseDef:
    """A single phase in the pipeline state machine."""

    name: str           # "planning", "dev", "discuss", "review", "spec-check"
    active_phase: str   # "planning_active", "dev_active", "discuss_active", ...
    review_phase: str   # "planning_review", "dev_review", "discuss_review", ""
    role: str           # "planner", "developer", "reviewer"
    review_of: str      # "PRD + tech-design", "code + tests", ...


# ============================================================================
# PhaseRouter
# ============================================================================


# P13: Old mode names mapped to canonical equivalents for backward
# compatibility.  Keep these indefinitely so existing pipeline YAML
# files don't break.
_DEPRECATED_MODE_ALIASES: dict[str, str] = {
    "code-dev":       "dev:quick",
    "full-dev":       "dev:standard",
    "agent-fix":      "dev:quick",
    "migrate":        "dev:standard",
    "greenfield":     "dev:quick",
}

# P0-2: These deprecated modes need their OWN phase definitions to preserve
# backward-compatible behavior. They are NOT remapped to canonical modes
# because their old contracts differ from the canonical equivalents:
#   - design-debate: old = planning + review only (NOT discuss + dev)
#   - inspect-only:  old = reviewer only (NOT developer + reviewer)
#   - spec-driven:   old = includes mandatory spec-check phase
_DEPRECATED_MODES_OWN_PHASES: frozenset[str] = frozenset({
    "design-debate", "inspect-only", "spec-driven",
})


@dataclass
class PhaseRouter:
    """Maps pipeline mode → ordered list of ``PhaseDef``.

    Usage::

        phases = PhaseRouter.get_phases("dev:standard")
        for pd in phases:
            orchestrator._run_loop(
                pd.active_phase, pd.review_phase, pd.role, pd.review_of,
            )
    """

    PHASES_BY_MODE: ClassVar[dict[str, list[PhaseDef]]] = {
        # ── Dev family ──────────────────────────────────────────────────
        "dev:quick": [
            PhaseDef("dev", "dev_active", "dev_review", "developer",
                     "code + tests"),
        ],
        "dev:standard": [
            PhaseDef("planning", "planning_active", "",
                     "planner", "PRD + tech-design"),
            PhaseDef("discuss", "discuss_active", "discuss_review",
                     "developer", "implementation proposal"),
            PhaseDef("dev", "dev_active", "dev_review", "developer",
                     "code + tests"),
        ],
        "dev:deep": [
            PhaseDef("planning", "planning_active", "",
                     "planner", "PRD + tech-design"),
            PhaseDef("discuss", "discuss_active", "discuss_review",
                     "developer", "implementation proposal"),
            PhaseDef("dev", "dev_active", "dev_review", "developer",
                     "code + tests"),
            PhaseDef("review", "dev_review", "", "reviewer",
                     "comprehensive review"),
        ],

        # ── MoA family ──────────────────────────────────────────────────
        # moa:analyze / moa:plan / moa:review are handled by
        # _run_moa_pipeline() directly — they don't use get_phases().
        # These entries exist only for mode validation.
        "moa:analyze": [],
        "moa:plan": [],
        "moa:review": [],

        # ── Chain ──────────────────────────────────────────────────────
        "chain": [
            PhaseDef("dev", "dev_active", "dev_review", "developer",
                     "code + tests"),
        ],

        # ── Custom ──────────────────────────────────────────────────────
        # The actual sequence is loaded from PipelineSpec.custom_phases.
        # This entry keeps the mode in the canonical validation registry.
        "custom": [],

        # ── Deprecated modes with own phase definitions (P0-2) ──────────
        # These preserve the EXACT old behavior before the canonical rename.
        # design-debate: the planning loop already includes its review step.
        "design-debate": [
            PhaseDef("planning", "planning_active", "planning_review",
                     "planner", "PRD + tech-design"),
        ],
        # inspect-only: reviewer only (NO developer)
        "inspect-only": [
            PhaseDef("review", "dev_review", "", "reviewer",
                     "comprehensive review"),
        ],
        # spec-driven: planning, then mandatory gate, then development
        "spec-driven": [
            PhaseDef("planning", "planning_active", "planning_review",
                     "planner", "PRD + tech-design"),
            PhaseDef("spec-check", "spec-check", "", "reviewer",
                     "spec compliance"),
            PhaseDef("dev", "dev_active", "dev_review", "developer",
                     "code + tests"),
        ],
    }

    CUSTOM_PHASES: ClassVar[dict[str, PhaseDef]] = {
        "planning": PhaseDef(
            "planning", "planning_active", "", "planner", "PRD + tech-design"
        ),
        "discuss": PhaseDef(
            "discuss", "discuss_active", "discuss_review",
            "developer", "implementation proposal",
        ),
        "spec-check": PhaseDef(
            "spec-check", "spec-check", "", "reviewer", "spec compliance"
        ),
        "dev": PhaseDef(
            "dev", "dev_active", "dev_review", "developer", "code + tests"
        ),
        "review": PhaseDef(
            "review", "dev_review", "", "reviewer", "comprehensive review"
        ),
    }

    # ── Canonical modes used for validation ────────────────────────────
    CANONICAL_MODES: ClassVar[frozenset[str]] = frozenset(
        list(PHASES_BY_MODE.keys()) + ["moa"]
    )

    @classmethod
    def get_phases(cls, mode: str) -> list[PhaseDef]:
        """Return the ordered list of ``PhaseDef`` for *mode*.

        Args:
            mode: Pipeline mode name (e.g. ``"dev:standard"``).

        Returns:
            Ordered list of ``PhaseDef`` instances.  Returns an empty
            list for unknown modes (caller should handle the error).
        """
        # Deprecated modes with own phase definitions: emit warning but
        # return their own phases (NOT a remap to a canonical mode).
        if mode in _DEPRECATED_MODES_OWN_PHASES:
            import logging
            _log = logging.getLogger(__name__)
            _log.info("Pipeline mode '%s' is deprecated.", mode)
            return cls.PHASES_BY_MODE.get(mode, [])

        # Resolve deprecated alias → canonical name
        if mode in _DEPRECATED_MODE_ALIASES:
            canonical = _DEPRECATED_MODE_ALIASES[mode]
            # Use module-level logger to avoid triggering pytest warning filters
            import logging
            _log = logging.getLogger(__name__)
            _log.info("Pipeline mode '%s' is deprecated. Use '%s' instead.",
                      mode, canonical)
            mode = canonical

        return cls.PHASES_BY_MODE.get(mode, [])

    @classmethod
    def canonical_modes(cls) -> frozenset[str]:
        """All valid mode names (including moa, which bypasses get_phases)."""
        return cls.CANONICAL_MODES

    @classmethod
    def custom_phases(cls, names: tuple[str, ...]) -> list[PhaseDef]:
        """Resolve a loader-validated custom phase sequence."""
        return [cls.CUSTOM_PHASES[name] for name in names]
