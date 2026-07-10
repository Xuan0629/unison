"""Tests for completion.py — GitCompletionDetector."""
import tempfile
from pathlib import Path
import pytest
import subprocess

from unison.completion import GitCompletionDetector
from unison.interfaces import AgentResult


class TestGitCompletionDetector:
    """GitCompletionDetector tests."""

    def test_create_detector(self):
        """Create a GitCompletionDetector."""
        detector = GitCompletionDetector()
        assert detector is not None

    def test_detect_successful_run(self, tmp_path):
        """Detect a successful agent run with commit."""
        # Initialize git repo
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
        
        # Create a file and commit
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "test commit"], cwd=tmp_path, capture_output=True)
        
        # Create log file
        log_path = tmp_path / "log.txt"
        log_path.write_text("Agent output here")
        
        detector = GitCompletionDetector()
        result = detector.detect(
            workspace=tmp_path,
            expected_iter=1,
            role="developer",
            log_path=log_path
        )
        
        assert result.success is True
        assert result.exit_code == 0
        assert result.commit is not None
        assert len(result.commit) == 40  # Git commit hash length

    def test_detect_failed_run(self, tmp_path):
        """Detect a failed agent run (no commit)."""
        # Initialize git repo but don't commit
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        
        log_path = tmp_path / "log.txt"
        log_path.write_text("Error: something went wrong")
        
        detector = GitCompletionDetector()
        result = detector.detect(
            workspace=tmp_path,
            expected_iter=1,
            role="developer",
            log_path=log_path
        )
        
        # No commit → success=False
        assert result.success is False
        assert result.commit is None

    def test_detect_reviewer_with_verdict_file(self, tmp_path):
        """Detect reviewer run with verdict file."""
        # Initialize git repo
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
        
        # Create reviews directory and verdict file
        reviews_dir = tmp_path / "reviews"
        reviews_dir.mkdir()
        verdict_file = reviews_dir / "iter-1.md"
        verdict_file.write_text("---\nverdict: PASS\n---\nReview content")
        
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "review commit"], cwd=tmp_path, capture_output=True)
        
        log_path = tmp_path / "log.txt"
        log_path.write_text("Reviewer output")
        
        detector = GitCompletionDetector()
        result = detector.detect(
            workspace=tmp_path,
            expected_iter=1,
            role="reviewer",
            log_path=log_path
        )
        
        assert result.success is True
        assert result.commit is not None

    def test_detect_developer_with_tests(self, tmp_path):
        """Detect developer run with tests directory."""
        # Initialize git repo
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
        
        # Create tests directory
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_example.py").write_text("def test_example(): pass")
        
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add tests"], cwd=tmp_path, capture_output=True)
        
        log_path = tmp_path / "log.txt"
        log_path.write_text("Developer output")
        
        detector = GitCompletionDetector()
        result = detector.detect(
            workspace=tmp_path,
            expected_iter=1,
            role="developer",
            log_path=log_path
        )
        
        assert result.success is True

    def test_detect_reads_log_file(self, tmp_path):
        """Detect reads log file for stdout/stderr tail."""
        # Initialize git repo
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
        
        log_path = tmp_path / "log.txt"
        log_content = "=== COMMAND ===\nclaude -p 'test'\n\n=== STDOUT ===\nOutput line 1\nOutput line 2\n\n=== STDERR ===\n"
        log_path.write_text(log_content)
        
        detector = GitCompletionDetector()
        result = detector.detect(
            workspace=tmp_path,
            expected_iter=1,
            role="developer",
            log_path=log_path
        )
        
        # Should read log file
        assert result.log_path == log_path
        assert result.duration >= 0

    def test_detect_nonexistent_log_file(self, tmp_path):
        """Detect with non-existent log file."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)

        log_path = tmp_path / "nonexistent.txt"

        detector = GitCompletionDetector()
        result = detector.detect(
            workspace=tmp_path,
            expected_iter=1,
            role="developer",
            log_path=log_path
        )

        # Should handle gracefully
        assert result.log_path == log_path

    # ------------------------------------------------------------------
    # Phase 4: planner artifact check
    # ------------------------------------------------------------------

    def test_detect_planner_with_both_artifacts(self, tmp_path):
        """Phase 4: planner with both prd/PRD.md and prd/tech-design.md passes."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"],
                       cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"],
                       cwd=tmp_path, capture_output=True)

        prd_dir = tmp_path / "prd"
        prd_dir.mkdir()
        (prd_dir / "PRD.md").write_text("# PRD")
        (prd_dir / "tech-design.md").write_text("# tech-design")

        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "planner"],
                       cwd=tmp_path, capture_output=True)

        log_path = tmp_path / "log.txt"
        log_path.write_text("Planner output")

        detector = GitCompletionDetector()
        result = detector.detect(
            workspace=tmp_path,
            expected_iter=1,
            role="planner",
            log_path=log_path,
        )
        assert result.success is True
        assert "planner artifact missing" not in (result.error or "")

    def test_detect_planner_fails_without_prd(self, tmp_path):
        """Phase 4: planner without prd/PRD.md fails."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"],
                       cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"],
                       cwd=tmp_path, capture_output=True)

        # No prd/ dir at all
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "planner"],
                       cwd=tmp_path, capture_output=True, check=False)

        log_path = tmp_path / "log.txt"
        log_path.write_text("Planner output")

        detector = GitCompletionDetector()
        result = detector.detect(
            workspace=tmp_path,
            expected_iter=1,
            role="planner",
            log_path=log_path,
        )
        assert result.success is False
        assert "PRD.md" in (result.error or "")

    def test_detect_planner_fails_without_tech_design(self, tmp_path):
        """Phase 4: planner with PRD.md but missing tech-design.md fails."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"],
                       cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"],
                       cwd=tmp_path, capture_output=True)

        prd_dir = tmp_path / "prd"
        prd_dir.mkdir()
        (prd_dir / "PRD.md").write_text("# PRD")
        # No tech-design.md

        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "planner"],
                       cwd=tmp_path, capture_output=True)

        log_path = tmp_path / "log.txt"
        log_path.write_text("Planner output")

        detector = GitCompletionDetector()
        result = detector.detect(
            workspace=tmp_path,
            expected_iter=1,
            role="planner",
            log_path=log_path,
        )
        assert result.success is False
        assert "tech-design.md" in (result.error or "")


# ============================================================================
# F7: pre_commit comparison — no false success on no-op runs
# ============================================================================


class TestPreCommitDetection:
    """F7: CompletionDetector with pre_commit baseline.

    Agent non-zero exit with no new commit → success=False.
    Previously, any existing HEAD commit triggered success=True.
    """

    def test_no_new_commit_with_pre_commit_returns_failure(self, tmp_path):
        """pre_commit == current HEAD, no artifact → success=False."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"],
                       cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"],
                       cwd=tmp_path, capture_output=True)

        # Create initial commit
        (tmp_path / "existing.txt").write_text("pre-existing")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"],
                       cwd=tmp_path, capture_output=True)

        pre_commit = subprocess.run(
            ["git", "log", "-1", "--format=%H"],
            cwd=tmp_path, capture_output=True, text=True,
        ).stdout.strip()

        # Agent runs but produces no new commit
        log_path = tmp_path / "log.txt"
        log_path.write_text("Agent exited with error, no changes made")

        detector = GitCompletionDetector()
        result = detector.detect(
            workspace=tmp_path,
            expected_iter=1,
            role="developer",
            log_path=log_path,
            pre_commit=pre_commit,
        )

        # F7: no new commit, no artifact → success=False
        assert result.success is False
        assert result.commit == pre_commit

    def test_new_commit_with_pre_commit_returns_success(self, tmp_path):
        """HEAD advanced past pre_commit → success=True."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"],
                       cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"],
                       cwd=tmp_path, capture_output=True)

        # Initial commit
        (tmp_path / "existing.txt").write_text("pre-existing")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"],
                       cwd=tmp_path, capture_output=True)

        pre_commit = subprocess.run(
            ["git", "log", "-1", "--format=%H"],
            cwd=tmp_path, capture_output=True, text=True,
        ).stdout.strip()

        # Agent makes a new commit
        (tmp_path / "new_feature.py").write_text("def foo(): pass")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "agent work"],
                       cwd=tmp_path, capture_output=True)

        log_path = tmp_path / "log.txt"
        log_path.write_text("Agent completed successfully")

        detector = GitCompletionDetector()
        result = detector.detect(
            workspace=tmp_path,
            expected_iter=1,
            role="developer",
            log_path=log_path,
            pre_commit=pre_commit,
        )

        assert result.success is True
        assert result.commit != pre_commit

    def test_reviewer_artifact_without_commit_succeeds(self, tmp_path):
        """Reviewer writes verdict file but no new commit → success (artifact fallback)."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"],
                       cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"],
                       cwd=tmp_path, capture_output=True)

        (tmp_path / "existing.txt").write_text("pre-existing")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"],
                       cwd=tmp_path, capture_output=True)

        pre_commit = subprocess.run(
            ["git", "log", "-1", "--format=%H"],
            cwd=tmp_path, capture_output=True, text=True,
        ).stdout.strip()

        # Reviewer writes verdict file but doesn't commit it
        reviews_dir = tmp_path / "reviews"
        reviews_dir.mkdir()
        (reviews_dir / "iter-1.md").write_text("---\nverdict: PASS\n---")

        log_path = tmp_path / "log.txt"
        log_path.write_text("Reviewer output")

        detector = GitCompletionDetector()
        result = detector.detect(
            workspace=tmp_path,
            expected_iter=1,
            role="reviewer",
            log_path=log_path,
            pre_commit=pre_commit,
        )

        # Artifact exists → success even without new commit
        assert result.success is True

    def test_backward_compat_no_pre_commit(self, tmp_path):
        """Without pre_commit: falls back to "any commit exists" behavior."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"],
                       cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"],
                       cwd=tmp_path, capture_output=True)

        (tmp_path / "file.txt").write_text("content")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"],
                       cwd=tmp_path, capture_output=True)

        log_path = tmp_path / "log.txt"
        log_path.write_text("Agent output")

        detector = GitCompletionDetector()
        result = detector.detect(
            workspace=tmp_path,
            expected_iter=1,
            role="developer",
            log_path=log_path,
            # No pre_commit → backward-compatible behavior
        )

        # Old behavior: any commit → success
        assert result.success is True
        assert result.commit is not None


# ============================================================================
# Phase 4: review-path helper
# ============================================================================


class TestReviewFileForPhase:
    """Orchestrator._review_file_for_phase — Phase 4 fix.

    Planning review must use a different filename than development
    review so a stale planning PASS is not parsed as a dev verdict.
    """

    def test_planning_review_path(self, tmp_path):
        from unison.orchestrator import Orchestrator
        from unison.state import State
        from unison.world import World
        from unison.interfaces import PipelineSpec, AgentSpec

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(
            "version: '1.0'\nproject_root: '.'\n"
            "agents:\n  developer:\n    role: developer\n    runtime: claude\n    model: m\n    system_prompt_path: prompts/d.md\n  reviewer:\n    role: reviewer\n    runtime: codex\n    model: m\n    system_prompt_path: prompts/r.md\n"
        )
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "d.md").write_text("d")
        (tmp_path / "prompts" / "r.md").write_text("r")

        loader_module = __import__("unison.pipeline", fromlist=["PipelineLoader"])
        spec = loader_module.PipelineLoader().load(pipeline_file)
        orch = Orchestrator(spec=spec)
        path = orch._review_file_for_phase("planning_review", 1)
        assert path.name == "plan-iter-1.md"

    def test_dev_review_path(self, tmp_path):
        from unison.orchestrator import Orchestrator

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(
            "version: '1.0'\nproject_root: '.'\n"
            "agents:\n  developer:\n    role: developer\n    runtime: claude\n    model: m\n    system_prompt_path: prompts/d.md\n  reviewer:\n    role: reviewer\n    runtime: codex\n    model: m\n    system_prompt_path: prompts/r.md\n"
        )
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "d.md").write_text("d")
        (tmp_path / "prompts" / "r.md").write_text("r")

        loader_module = __import__("unison.pipeline", fromlist=["PipelineLoader"])
        spec = loader_module.PipelineLoader().load(pipeline_file)
        orch = Orchestrator(spec=spec)
        path = orch._review_file_for_phase("dev_review", 1)
        assert path.name == "iter-1.md"
