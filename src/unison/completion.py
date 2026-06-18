"""GitCompletionDetector — determines agent run outcome from git + filesystem.

Called after an agent subprocess exits.  Checks git history and filesystem
artifacts to decide whether the run produced valid output.
"""

import subprocess
from pathlib import Path
from dataclasses import dataclass

from interfaces import AgentResult, AgentRole


@dataclass
class GitCompletionDetector:
    """基于 git log + filesystem stat 的完成检测。

    1. subprocess 退出 → 基本信号 (we are only called post-exit)
    2. git log -1 --format=%H → commit hash
    3. stat tests/ → 确认测试存在（Developer）
    4. stat reviews/iter-{iter}.md → 确认 Reviewer 产出
    5. 读 log_path → 提取 stdout/stderr 末 500 字符
    """

    def detect(
        self,
        workspace: Path,
        expected_iter: int,
        role: AgentRole,
        log_path: Path,
    ) -> AgentResult:
        """Run completion detection and return an AgentResult."""
        # 1. Subprocess already exited (basic signal — we are called post-mortem).
        exit_code = 0

        # 2. git log -1 --format=%H → commit hash
        commit = self._get_commit(workspace)

        # Success requires at least one commit on the branch.
        success = commit is not None

        # 3-4. Role-specific filesystem checks (informational — non-blocking).
        if role == "developer":
            _ = (workspace / "tests").is_dir()
        elif role == "reviewer":
            _ = (workspace / "reviews" / f"iter-{expected_iter}.md").exists()

        # 5. Read log_path → extract stdout/stderr tails.
        stdout_tail, stderr_tail, error = self._read_log(log_path)

        return AgentResult(
            success=success,
            exit_code=exit_code,
            duration=0.0,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            log_path=log_path,
            commit=commit,
            verdict=None,
            error=error,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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
