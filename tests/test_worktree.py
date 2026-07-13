"""Tests for worktree.py — WorktreeManager with git worktree isolation."""
import subprocess
from pathlib import Path

import pytest

from unison.interfaces import WorktreeConfig
from unison.worktree import MergeResult, WorktreeInfo, WorktreeManager


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository with an initial commit.

    Returns the path to the repo root.  The default branch is renamed
    to "main" so it matches the WorktreeConfig default.
    """
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )

    git("init")
    git("config", "user.email", "test@unison.local")
    git("config", "user.name", "Unison Test")

    # Rename the default branch to "main"
    current_branch_result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=30,
    )
    current_branch = current_branch_result.stdout.strip()
    if current_branch and current_branch != "main":
        git("branch", "-m", current_branch, "main")

    # Create an initial commit (worktree requires at least one commit)
    (repo / "README.md").write_text("# Test Repo\n")
    git("add", "README.md")
    git("commit", "-m", "initial commit")

    return repo


@pytest.fixture
def enabled_config() -> WorktreeConfig:
    """A WorktreeConfig with worktree isolation enabled."""
    return WorktreeConfig(enabled=True, base_branch="main")


@pytest.fixture
def disabled_config() -> WorktreeConfig:
    """A WorktreeConfig with worktree isolation disabled."""
    return WorktreeConfig(enabled=False)


# ============================================================================
# WorktreeConfig
# ============================================================================


class TestWorktreeConfig:
    """WorktreeConfig dataclass tests."""

    def test_default_values(self):
        """Default values match the design spec."""
        cfg = WorktreeConfig()
        assert cfg.enabled is False
        assert cfg.base_branch == "main"
        assert cfg.worktree_root == Path(".worktrees")

    def test_custom_values(self):
        """Custom values are preserved."""
        cfg = WorktreeConfig(
            enabled=True,
            base_branch="develop",
            worktree_root=Path("tmp/worktrees"),
        )
        assert cfg.enabled is True
        assert cfg.base_branch == "develop"
        assert cfg.worktree_root == Path("tmp/worktrees")

    def test_is_frozen(self):
        """WorktreeConfig is frozen (immutable)."""
        cfg = WorktreeConfig()
        with pytest.raises(AttributeError):
            cfg.enabled = True  # type: ignore[misc]

    def test_equality(self):
        """Equal configs compare equal."""
        a = WorktreeConfig(enabled=True, base_branch="main")
        b = WorktreeConfig(enabled=True, base_branch="main")
        c = WorktreeConfig(enabled=False, base_branch="main")
        assert a == b
        assert a != c


# ============================================================================
# WorktreeInfo
# ============================================================================


class TestWorktreeInfo:
    """WorktreeInfo dataclass tests."""

    def test_create_info(self, tmp_path: Path):
        """Create a WorktreeInfo with explicit fields."""
        path = tmp_path / "worktrees" / "feature-x"
        info = WorktreeInfo(path=path, branch="feature-x", hash="abc1234")
        assert info.path == path
        assert info.branch == "feature-x"
        assert info.hash == "abc1234"


# ============================================================================
# WorktreeManager — disabled / no-git fallback
# ============================================================================


class TestWorktreeManagerFallback:
    """Graceful fallback when disabled or not a git repo."""

    def test_create_worktree_disabled(self, disabled_config, tmp_path):
        """create_worktree returns None when disabled."""
        mgr = WorktreeManager(config=disabled_config, project_root=tmp_path)
        result = mgr.create_worktree("feature-a")
        assert result is None

    def test_remove_worktree_disabled(self, disabled_config, tmp_path):
        """remove_worktree returns False when disabled."""
        mgr = WorktreeManager(config=disabled_config, project_root=tmp_path)
        result = mgr.remove_worktree("feature-a")
        assert result is False

    def test_list_worktrees_disabled(self, disabled_config, tmp_path):
        """list_worktrees returns empty list when disabled."""
        mgr = WorktreeManager(config=disabled_config, project_root=tmp_path)
        result = mgr.list_worktrees()
        assert result == []

    def test_create_worktree_no_git(self, enabled_config, tmp_path):
        """create_worktree returns None when project_root is not a git repo."""
        mgr = WorktreeManager(config=enabled_config, project_root=tmp_path)
        result = mgr.create_worktree("feature-a")
        assert result is None

    def test_remove_worktree_no_git(self, enabled_config, tmp_path):
        """remove_worktree returns False when not a git repo."""
        mgr = WorktreeManager(config=enabled_config, project_root=tmp_path)
        result = mgr.remove_worktree("feature-a")
        assert result is False

    def test_list_worktrees_no_git(self, enabled_config, tmp_path):
        """list_worktrees returns empty list when not a git repo."""
        mgr = WorktreeManager(config=enabled_config, project_root=tmp_path)
        result = mgr.list_worktrees()
        assert result == []


# ============================================================================
# WorktreeManager — create / list / remove
# ============================================================================


class TestWorktreeManagerCreate:
    """Worktree creation tests."""

    def test_create_worktree(self, enabled_config, git_repo):
        """Create a worktree for a new feature branch."""
        mgr = WorktreeManager(config=enabled_config, project_root=git_repo)
        info = mgr.create_worktree("feature-a")

        assert info is not None
        assert info.branch == "feature-a"
        assert info.path == git_repo / ".worktrees" / "feature-a"
        assert info.path.exists()
        assert info.hash  # non-empty commit hash

    def test_create_worktree_duplicate(self, enabled_config, git_repo):
        """Creating a worktree that already exists returns None."""
        mgr = WorktreeManager(config=enabled_config, project_root=git_repo)

        first = mgr.create_worktree("feature-b")
        assert first is not None

        second = mgr.create_worktree("feature-b")
        assert second is None

    def test_create_multiple_worktrees(self, enabled_config, git_repo):
        """Create multiple independent worktrees."""
        mgr = WorktreeManager(config=enabled_config, project_root=git_repo)

        feat_a = mgr.create_worktree("feature-a")
        feat_b = mgr.create_worktree("feature-b")

        assert feat_a is not None
        assert feat_b is not None
        assert feat_a.path != feat_b.path
        assert feat_a.path.exists()
        assert feat_b.path.exists()

    def test_create_worktree_isolated(self, enabled_config, git_repo):
        """Worktree is isolated — changes don't appear in the main repo."""
        mgr = WorktreeManager(config=enabled_config, project_root=git_repo)

        info = mgr.create_worktree("feature-x")
        assert info is not None

        # Write a file in the worktree
        worktree_file = info.path / "feature_only.txt"
        worktree_file.write_text("only in worktree")

        # The file should NOT exist in the main repo
        main_file = git_repo / "feature_only.txt"
        assert not main_file.exists()

    def test_create_worktree_with_custom_root(self, git_repo):
        """Worktree can be created under a custom worktree_root."""
        cfg = WorktreeConfig(
            enabled=True,
            base_branch="main",
            worktree_root=Path("tmp/wt"),
        )
        mgr = WorktreeManager(config=cfg, project_root=git_repo)

        info = mgr.create_worktree("custom-root-feat")
        assert info is not None
        assert info.path == git_repo / "tmp" / "wt" / "custom-root-feat"
        assert info.path.exists()

    @pytest.mark.parametrize("feature_name", ["..", "../../evil", "/tmp/evil"])
    def test_create_rejects_unsafe_feature_name(
        self, enabled_config, git_repo, feature_name, monkeypatch
    ):
        mgr = WorktreeManager(config=enabled_config, project_root=git_repo)
        monkeypatch.setattr(
            mgr, "_git", lambda *args, **kwargs: pytest.fail("git must not run")
        )

        assert mgr.create_worktree(feature_name) is None
        assert not (git_repo.parent / "evil").exists()


class TestWorktreeManagerList:
    """Worktree listing tests."""

    def test_list_worktrees_empty(self, enabled_config, git_repo):
        """list_worktrees returns empty when no additional worktrees exist."""
        mgr = WorktreeManager(config=enabled_config, project_root=git_repo)
        result = mgr.list_worktrees()
        assert result == []

    def test_list_worktrees_after_create(self, enabled_config, git_repo):
        """list_worktrees returns created worktree."""
        mgr = WorktreeManager(config=enabled_config, project_root=git_repo)

        mgr.create_worktree("feature-a")
        worktrees = mgr.list_worktrees()

        assert len(worktrees) == 1
        assert worktrees[0].branch == "feature-a"
        assert worktrees[0].path == git_repo / ".worktrees" / "feature-a"

    def test_list_worktrees_multiple(self, enabled_config, git_repo):
        """list_worktrees returns all created worktrees."""
        mgr = WorktreeManager(config=enabled_config, project_root=git_repo)

        mgr.create_worktree("feature-a")
        mgr.create_worktree("feature-b")
        mgr.create_worktree("feature-c")

        worktrees = mgr.list_worktrees()
        branches = {wt.branch for wt in worktrees}

        assert len(worktrees) == 3
        assert branches == {"feature-a", "feature-b", "feature-c"}


class TestWorktreeManagerRemove:
    """Worktree removal tests."""

    def test_remove_worktree(self, enabled_config, git_repo):
        """Remove a worktree removes the directory."""
        mgr = WorktreeManager(config=enabled_config, project_root=git_repo)

        info = mgr.create_worktree("feature-x")
        assert info is not None
        assert info.path.exists()

        result = mgr.remove_worktree("feature-x")
        assert result is True
        assert not info.path.exists()

    def test_remove_nonexistent_worktree(self, enabled_config, git_repo):
        """Removing a non-existent worktree returns False."""
        mgr = WorktreeManager(config=enabled_config, project_root=git_repo)
        result = mgr.remove_worktree("nonexistent")
        assert result is False

    @pytest.mark.parametrize("feature_name", ["..", "../../evil", "/tmp/evil"])
    def test_remove_rejects_unsafe_feature_name(
        self, enabled_config, git_repo, feature_name, monkeypatch
    ):
        mgr = WorktreeManager(config=enabled_config, project_root=git_repo)
        monkeypatch.setattr(
            mgr, "_git", lambda *args, **kwargs: pytest.fail("git must not run")
        )

        assert mgr.remove_worktree(feature_name) is False

    def test_remove_worktree_cleans_list(self, enabled_config, git_repo):
        """After removal, the worktree is no longer listed."""
        mgr = WorktreeManager(config=enabled_config, project_root=git_repo)

        mgr.create_worktree("feature-a")
        mgr.create_worktree("feature-b")

        assert len(mgr.list_worktrees()) == 2

        mgr.remove_worktree("feature-a")
        remaining = mgr.list_worktrees()

        assert len(remaining) == 1
        assert remaining[0].branch == "feature-b"

    def test_remove_then_recreate(self, enabled_config, git_repo):
        """After removal, a worktree with the same name can be recreated."""
        mgr = WorktreeManager(config=enabled_config, project_root=git_repo)

        first = mgr.create_worktree("feature-x")
        assert first is not None

        mgr.remove_worktree("feature-x")

        second = mgr.create_worktree("feature-x")
        assert second is not None
        assert second.path.exists()
