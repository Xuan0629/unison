"""GitCompletionDetector — determines agent run outcome from git + filesystem.

Called after an agent subprocess exits.  Checks git history and filesystem
artifacts to decide whether the run produced valid output.
"""

import subprocess
from pathlib import Path
from dataclasses import dataclass

from unison.interfaces import AgentResult, AgentRole


@dataclass
class GitCompletionDetector:
    """基于 git log + filesystem stat 的完成检测。

    1. subprocess 退出 → 基本信号 (we are only called post-exit)
    2. 比较 pre_commit 与当前 HEAD → 有新 commit = 产出
    3. 若无新 commit，检查 role-specific 产物（review 文件 / test 目录 / PRD）
    4. 读 log_path → 提取 stdout/stderr 末 500 字符
    """

    def detect(
        self,
        workspace: Path,
        expected_iter: int,
        role: AgentRole,
        log_path: Path,
        pre_commit: str | None = None,
    ) -> AgentResult:
        """Run completion detection and return an AgentResult.

        Args:
            workspace: Git workspace root.
            expected_iter: Current iteration number.
            role: Agent role (planner, developer, reviewer).
            log_path: Path to the agent invocation log.
            pre_commit: HEAD commit hash before agent invocation.
                If provided, success requires HEAD to have changed
                (new work was committed) OR a role-specific artifact
                to exist on disk.
        """
        # 1. Subprocess already exited (basic signal — we are called post-mortem).
        exit_code = 0

        # 2. Compare pre_commit with current HEAD.
        current_commit = self._get_commit(workspace)

        # F7: success requires new work — either HEAD advanced or an
        # artifact contract is satisfied.  A pre-existing commit with
        # no new work is NOT success.
        if pre_commit is not None:
            head_changed = current_commit != pre_commit
        else:
            # Backward compatible: no pre_commit → fall back to "any
            # commit exists" (existing callers without the F7 fix).
            head_changed = current_commit is not None

        # 3-4. Role-specific filesystem checks. Planner artifact check
        # (Phase 4) can return failure; developer/reviewer checks are
        # informational and non-blocking. They run AFTER the log read
        # so the AgentResult has all fields populated.
        stdout_tail, stderr_tail, error = self._read_log(log_path)

        # Determine success: HEAD must have changed OR artifact exists.
        success = head_changed

        if not success:
            # Check role-specific artifact as fallback evidence of work.
            success = self._check_artifact(workspace, expected_iter, role)

        if role == "planner":
            # Phase 4 fix: planner must produce prd/PRD.md AND
            # prd/tech-design.md. A successful git commit alone is
            # not sufficient — the user-stated artifacts must exist.
            prd = workspace / "prd" / "PRD.md"
            tech = workspace / "prd" / "tech-design.md"
            if not prd.is_file():
                return AgentResult(
                    success=False,
                    exit_code=exit_code,
                    duration=0.0,
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                    log_path=log_path,
                    commit=current_commit,
                    verdict=None,
                    error=f"planner artifact missing: {prd}",
                )
            if not tech.is_file():
                return AgentResult(
                    success=False,
                    exit_code=exit_code,
                    duration=0.0,
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                    log_path=log_path,
                    commit=current_commit,
                    verdict=None,
                    error=f"planner artifact missing: {tech}",
                )

        return AgentResult(
            success=success,
            exit_code=exit_code,
            duration=0.0,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            log_path=log_path,
            commit=current_commit,
            verdict=None,
            error=error,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_artifact(workspace: Path, iteration: int, role: AgentRole) -> bool:
        """Return True if a role-specific artifact exists on disk.

        This is the fallback success check when git HEAD has not advanced
        (no new commit) — the agent may have produced file output without
        committing (e.g. reviewer writing a verdict file).
        """
        if role == "developer":
            # Developer must produce either test output or source changes.
            return (workspace / "tests").is_dir()
        elif role == "reviewer":
            # Reviewer must produce a verdict/review file.
            return (workspace / "reviews" / f"iter-{iteration}.md").exists()
        elif role == "planner":
            # Planner must produce both PRD and tech-design.
            return (
                (workspace / "prd" / "PRD.md").is_file()
                and (workspace / "prd" / "tech-design.md").is_file()
            )
        return False

    @staticmethod
    def _get_commit(workspace: Path) -> str | None:
        """Return the latest commit hash (40-char hex) or None."""
        try:
            result = subprocess.run(
                ["git", "log", "-1", "--format=%H"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return None

    @staticmethod
    def _read_log(log_path: Path) -> tuple[str, str, str | None]:
        """Read the runner log and return (stdout_tail, stderr_tail, error).

        Expects log format:

            === COMMAND ===
            <cmd>

            === STDOUT ===
            <stdout>

            === STDERR ===
            <stderr>

        Tails are truncated to the last 500 characters.
        """
        stdout_tail = ""
        stderr_tail = ""
        error: str | None = None

        try:
            content = log_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            error = f"Log file not found: {log_path}"
            return stdout_tail, stderr_tail, error
        except (OSError, UnicodeDecodeError) as exc:
            error = str(exc)
            return stdout_tail, stderr_tail, error

        # Parse the structured log sections.
        if "=== STDOUT ===" in content:
            _before, rest = content.split("=== STDOUT ===", 1)
            if "=== STDERR ===" in rest:
                std_part, err_part = rest.split("=== STDERR ===", 1)
                stdout_tail = std_part.strip()[-500:]
                stderr_tail = err_part.strip()[-500:]
            else:
                stdout_tail = rest.strip()[-500:]
        elif "=== STDERR ===" in content:
            _before, rest = content.split("=== STDERR ===", 1)
            stderr_tail = rest.strip()[-500:]
        else:
            # No recognised markers — treat the whole content as stdout.
            stdout_tail = content.strip()[-500:]

        return stdout_tail, stderr_tail, error
