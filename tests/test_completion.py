"""Tests for completion.py — GitCompletionDetector."""
import tempfile
from pathlib import Path
import pytest
import subprocess

from unison.completion import GitCompletionDetector
from interfaces import AgentResult


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
