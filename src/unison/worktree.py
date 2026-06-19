"""WorktreeManager — git worktree 管理，支持多 Developer 并行隔离。

每个 feature_name 对应一个独立 git worktree，多个 Developer
可在不同 worktree 中并行开发，最后通过 merge 合并。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from interfaces import WorktreeConfig


@dataclass
class WorktreeInfo:
    """单个 worktree 的信息。"""
    path: Path
    branch: str
    hash: str


@dataclass
class MergeResult:
    """merge_reconciliation 的结果。"""
    success: bool
    conflicts: list[str]  # 冲突的分支名列表
    merged_branches: list[str] = None  # 成功合并的分支

    def __post_init__(self):
        if self.merged_branches is None:
            self.merged_branches = []


@dataclass
class WorktreeManager:
    """git worktree 管理器。

    每个 feature_name 对应一个独立 worktree，支持并行 Developer。
    无 git 仓库时所有操作 graceful fallback（返回空/None/False）。

    Usage::

        config = WorktreeConfig(enabled=True, base_branch="main")
        mgr = WorktreeManager(config=config, project_root=Path("/repo"))
        info = mgr.create_worktree("feature-a")
        # ... Developer 在 info.path 中工作 ...
        mgr.remove_worktree("feature-a")
    """

    config: WorktreeConfig
    project_root: Path

    # ==================================================================
    # Internal helpers
    # ==================================================================

    def _worktree_dir(self) -> Path:
        """Resolve the worktree parent directory (project_root / worktree_root)."""
        return self.project_root / self.config.worktree_root

    def _git(
        self, *args: str, cwd: Path | None = None, timeout: int = 60
    ) -> subprocess.CompletedProcess[str]:
        """Run a git subprocess and return the CompletedProcess.

        Always uses capture_output=True, text=True.  Does **not** raise
        on non-zero exit — callers inspect ``returncode``.
        """
        try:
            return subprocess.run(
                ["git", *args],
                cwd=str(cwd or self.project_root),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            # git unavailable — return a synthetic failure
            proc = subprocess.CompletedProcess(
                args=["git", *args],
                returncode=-1,
                stdout="",
                stderr="git unavailable",
            )
            return proc  # type: ignore[return-value]

    def _is_git_repo(self) -> bool:
        """Return True if *project_root* is a git repository."""
        result = self._git("rev-parse", "--git-dir")
        return result.returncode == 0

    # ==================================================================
    # Public API
    # ==================================================================

    def create_worktree(self, feature_name: str) -> WorktreeInfo | None:
        """Create a git worktree for *feature_name*.

        Creates a new branch named *feature_name* from
        ``config.base_branch`` and checks it out at
        ``<worktree_root>/<feature_name>``.

        If the branch already exists, attempts to add a worktree for
        the existing branch.

        Args:
            feature_name: Branch name and worktree directory name.

        Returns:
            WorktreeInfo on success, None on failure (disabled, not a
            git repo, worktree already exists, or git error).
        """
        if not self.config.enabled:
            return None

        if not self._is_git_repo():
            return None

        worktree_path = self._worktree_dir() / feature_name

        # Already exists — don't overwrite
        if worktree_path.exists():
            return None

        # Ensure parent directory exists
        worktree_path.parent.mkdir(parents=True, exist_ok=True)

        # Try creating a new branch + worktree from base_branch
        result = self._git(
            "worktree", "add",
            str(worktree_path),
            "-b", feature_name,
            self.config.base_branch,
        )

        if result.returncode != 0:
            # Fallback: branch might already exist — add worktree for it
            result = self._git(
                "worktree", "add",
                str(worktree_path),
                feature_name,
            )

        if result.returncode != 0:
            return None

        # Read the commit hash in the new worktree
        hash_result = self._git("rev-parse", "HEAD", cwd=worktree_path)
        commit_hash = (
            hash_result.stdout.strip()
            if hash_result.returncode == 0
            else ""
        )

        return WorktreeInfo(
            path=worktree_path,
            branch=feature_name,
            hash=commit_hash,
        )

    def remove_worktree(self, feature_name: str) -> bool:
        """Remove the git worktree for *feature_name*.

        Uses ``git worktree remove``.  If the worktree has uncommitted
        changes the removal may fail — the caller should decide whether
        to force-remove.

        Args:
            feature_name: Branch/worktree name to remove.

        Returns:
            True on success, False on failure (disabled, not a git
            repo, worktree not found, or git error).
        """
        if not self.config.enabled:
            return False

        if not self._is_git_repo():
            return False

        worktree_path = self._worktree_dir() / feature_name

        if not worktree_path.exists():
            return False

        result = self._git("worktree", "remove", str(worktree_path))

        if result.returncode != 0:
            # Try force-removing (uncommitted changes present)
            result = self._git(
                "worktree", "remove", "--force", str(worktree_path)
            )

        return result.returncode == 0

    def list_worktrees(self) -> list[WorktreeInfo]:
        """List all worktrees in this project.

        Returns only worktrees under ``worktree_root``, not the main
        worktree.

        Returns:
            List of WorktreeInfo.  Empty list if disabled, not a git
            repo, or no additional worktrees exist.
        """
        if not self.config.enabled:
            return []

        if not self._is_git_repo():
            return []

        result = self._git("worktree", "list")
        if result.returncode != 0:
            return []

        worktree_dir_str = str(self._worktree_dir())
        worktrees: list[WorktreeInfo] = []

        for line in result.stdout.strip().splitlines():
            if not line:
                continue

            # Output format: "<path>  <hash> [<branch>]"
            # e.g. "/repo  abc1234 [main]"
            # e.g. "/repo/.worktrees/feat  def5678 [feat]"
            parts = line.split()
            if len(parts) < 2:
                continue

            path = Path(parts[0])
            commit_hash = parts[1]

            # Extract branch name from [...] brackets
            branch = ""
            full_line = " ".join(parts[2:])
            if full_line.startswith("[") and full_line.endswith("]"):
                branch = full_line[1:-1]
            elif "[" in full_line:
                # e.g. "(detached HEAD)" or similar
                start = full_line.find("[")
                end = full_line.find("]")
                if start != -1 and end != -1:
                    branch = full_line[start + 1:end]

            # Only include worktrees under our worktree_root
            if worktree_dir_str in str(path) and str(path) != str(self.project_root):
                worktrees.append(WorktreeInfo(
                    path=path,
                    branch=branch,
                    hash=commit_hash,
                ))

        return worktrees

    # ==================================================================
    # merge_reconciliation — consolidate parallel feature branches
    # ==================================================================

    def merge_reconciliation(
        self,
        branches: list[str],
        strategy: Literal["ff", "octopus", "manual"] = "ff",
    ) -> MergeResult:
        """Merge feature branches back to ``config.base_branch``.

        Args:
            branches: List of branch names (= feature names) to merge.
            strategy: Merge strategy:
                ``"ff"`` — fast-forward each branch in order; abort on
                    conflict.
                ``"octopus"`` — ``git merge --octopus``; report conflicts.
                ``"manual"`` — leave branches separate, return
                    ``success=False`` with the unmerged branch list.

        Returns:
            MergeResult with success flag and conflict list.
        """
        if not self.config.enabled:
            return MergeResult(success=False, conflicts=branches)

        if not self._is_git_repo():
            return MergeResult(success=False, conflicts=branches)

        if not branches:
            return MergeResult(success=True, conflicts=[])

        if strategy == "manual":
            return MergeResult(
                success=False,
                conflicts=branches,
                merged_branches=[],
            )

        # Save current branch so we can restore it after merge
        current_branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
        current_branch_name = (
            current_branch.stdout.strip()
            if current_branch.returncode == 0
            else self.config.base_branch
        )

        # Check out the base branch
        checkout = self._git("checkout", self.config.base_branch)
        if checkout.returncode != 0:
            # Try to restore original branch before returning
            self._git("checkout", current_branch_name)
            return MergeResult(success=False, conflicts=branches)

        conflicts: list[str] = []
        merged: list[str] = []

        if strategy == "ff":
            for branch in branches:
                # Attempt to fast-forward merge the branch
                result = self._git(
                    "merge", "--ff-only", branch,
                    timeout=120,
                )
                if result.returncode == 0:
                    merged.append(branch)
                else:
                    conflicts.append(branch)
                    # Abort any in-progress merge before trying next
                    self._git("merge", "--abort")

        elif strategy == "octopus":
            # git merge --octopus <branch1> <branch2> ...
            result = self._git(
                "merge", "--octopus", *branches,
                timeout=120,
            )
            if result.returncode == 0:
                merged = list(branches)
            else:
                # Octopus merge failure — all branches are conflicted
                conflicts = list(branches)
                self._git("merge", "--abort")

        # Restore original branch (best-effort)
        self._git("checkout", current_branch_name)

        return MergeResult(
            success=len(conflicts) == 0,
            conflicts=conflicts,
            merged_branches=merged,
        )
