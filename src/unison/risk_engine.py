"""Risk engine — RuleEngineRiskEvaluator with 3-tuple rules
(operation x path x known-safe-command-downgrade)."""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path

from interfaces import Operation, RiskLevel, RiskMatrixConfig


# ---------------------------------------------------------------------------
# Default matrix rules (used when config dicts are empty)
# ---------------------------------------------------------------------------

_DEFAULT_WORKSPACE: dict[Operation, RiskLevel] = {
    Operation.READ: RiskLevel.L1,
    Operation.CREATE: RiskLevel.L1,
    Operation.MODIFY: RiskLevel.L2,
    Operation.DELETE: RiskLevel.L2,
}

_DEFAULT_EXTERNAL: dict[Operation, RiskLevel] = {
    Operation.READ: RiskLevel.L0,
    Operation.CREATE: RiskLevel.L2,
    Operation.MODIFY: RiskLevel.L2,
    Operation.DELETE: RiskLevel.L2,
}

_SUDO_RE = re.compile(r"\bsudo\b")

_DOWNGRADE: dict[RiskLevel, RiskLevel] = {
    RiskLevel.L3: RiskLevel.L2,
    RiskLevel.L2: RiskLevel.L1,
    RiskLevel.L1: RiskLevel.L0,
    RiskLevel.L0: RiskLevel.L0,
}


# ---------------------------------------------------------------------------
# RiskEvaluation
# ---------------------------------------------------------------------------

@dataclass
class RiskEvaluation:
    """Result of a single risk evaluation."""

    level: RiskLevel
    reason: str
    snapshot_path: Path | None = None
    halted: bool = False


# ---------------------------------------------------------------------------
# RuleEngineRiskEvaluator
# ---------------------------------------------------------------------------

class RuleEngineRiskEvaluator:
    """Rule engine implementation.

    LLM only intervenes when the path is not in any known category.
    """

    def __init__(self, matrix: RiskMatrixConfig, workspace: Path) -> None:
        self.matrix = matrix
        self.workspace = workspace

    # -- public API ----------------------------------------------------------

    def evaluate(
        self,
        operation: Operation,
        path: str,
        command: str = "",
        matrix: RiskMatrixConfig | None = None,
    ) -> RiskEvaluation:
        """Evaluate risk for an operation on a path with an optional command.

        Priority (top-down):
          1. command contains sudo → L3
          2. path in system_critical_paths → L3
          3. command in known_safe_external_commands → downgrade 1 level
          4. operation x scope matrix lookup
          5. default L2
        """
        actual_matrix = matrix if matrix is not None else self.matrix

        # 1. sudo → unconditional L3
        if command and _SUDO_RE.search(command):
            return RiskEvaluation(
                level=RiskLevel.L3,
                reason="sudo command detected — unconditional halt",
                halted=True,
            )

        # 2. system critical path → L3
        if self._is_critical(path, actual_matrix):
            return RiskEvaluation(
                level=RiskLevel.L3,
                reason=f"system critical path: {path}",
                halted=True,
            )

        # 4. operation x scope matrix
        scope = self._scope(path)
        level = self._matrix_lookup(actual_matrix, operation, scope)

        # 3. known safe command → downgrade (applied AFTER matrix lookup
        #    so the downgrade is relative to the matrix result)
        if command and self._is_safe(command, actual_matrix):
            level = _DOWNGRADE[level]

        return RiskEvaluation(
            level=level,
            reason=f"{operation.value} {path} ({scope}) -> {level.name}",
            snapshot_path=Path(path) if level == RiskLevel.L2 else None,
            halted=level == RiskLevel.L3,
        )

    def is_known_safe_command(self, command: str) -> bool:
        """Return True if *command* matches a known-safe pattern."""
        return self._is_safe(command, self.matrix)

    def is_system_critical_path(self, path: str) -> bool:
        """Return True if *path* matches a system-critical pattern."""
        return self._is_critical(path, self.matrix)

    # -- internal helpers ----------------------------------------------------

    @staticmethod
    def _is_safe(command: str, matrix: RiskMatrixConfig) -> bool:
        for pattern in matrix.known_safe_external_commands:
            if fnmatch.fnmatch(command, pattern):
                return True
        return False

    @staticmethod
    def _is_critical(path: str, matrix: RiskMatrixConfig) -> bool:
        expanded = os.path.expanduser(path)
        for pattern in matrix.system_critical_paths:
            if fnmatch.fnmatch(expanded, os.path.expanduser(pattern)):
                return True
        return False

    def _scope(self, path: str) -> str:
        """Return 'workspace' if *path* is inside the workspace, else 'external'."""
        try:
            Path(path).resolve().relative_to(self.workspace.resolve())
            return "workspace"
        except ValueError:
            return "external"

    @staticmethod
    def _matrix_lookup(
        matrix: RiskMatrixConfig,
        operation: Operation,
        scope: str,
    ) -> RiskLevel:
        """Look up risk level from matrix rules, falling back to defaults."""
        if scope == "workspace":
            rules = matrix.workspace_rules or _DEFAULT_WORKSPACE
        else:
            rules = matrix.external_rules or _DEFAULT_EXTERNAL
        return rules.get(operation, RiskLevel.L2)
