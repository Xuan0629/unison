"""Read-only Git evidence queries used in orchestrator prompt assembly."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitEvidenceReader:
    """Read compact Git evidence without making orchestration decisions."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def head_commit(self) -> str:
        """Return the current HEAD commit hash, or empty string on failure."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(self._root),
                capture_output=True,
                timeout=10,
                check=False,
            )
            return result.stdout.decode("utf-8", errors="replace").strip()[:8]
        except Exception:
            return ""

    def cumulative_diff(self, loop_start_commit: str, max_chars: int = 1200) -> str:
        """Return compact cumulative ``git diff <start> HEAD --stat`` evidence."""
        if not loop_start_commit:
            return ""
        try:
            check = subprocess.run(
                ["git", "cat-file", "-e", loop_start_commit],
                cwd=str(self._root),
                capture_output=True,
                timeout=10,
                check=False,
            )
            if check.returncode != 0:
                return ""
            result = subprocess.run(
                ["git", "diff", loop_start_commit, "HEAD", "--stat"],
                cwd=str(self._root),
                capture_output=True,
                timeout=30,
                check=False,
            )
            if result.returncode == 0:
                raw = result.stdout.decode("utf-8", errors="replace")
                return raw[:max_chars] + (
                    "\n...[cumulative diff truncated]" if len(raw) > max_chars else ""
                )
        except Exception:
            pass
        return ""

    def recent_diff(self, max_chars: int = 8192) -> str:
        """Return recent Git diff evidence, or empty string on failure."""
        try:
            parent_check = subprocess.run(
                ["git", "rev-parse", "HEAD~1"],
                cwd=str(self._root),
                capture_output=True,
                timeout=10,
                check=False,
            )
            command = ["git", "diff", "HEAD~1", "HEAD"]
            if parent_check.returncode != 0:
                command = ["git", "diff", "--cached"]
            result = subprocess.run(
                command,
                cwd=str(self._root),
                capture_output=True,
                timeout=30,
                check=False,
            )
            if result.returncode == 0:
                raw = result.stdout.decode("utf-8", errors="replace")
                return raw[:max_chars] + (
                    "\n...[diff truncated]" if len(raw) > max_chars else ""
                )
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            pass
        return ""
