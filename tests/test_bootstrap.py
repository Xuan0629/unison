"""Tests for bootstrap.py — Bootstrap command execution."""
import tempfile
from pathlib import Path
import pytest

from unison.bootstrap import BootstrapExecutor


class TestBootstrapExecutor:
    """BootstrapExecutor tests."""

    def test_create_executor(self):
        """Create a BootstrapExecutor."""
        executor = BootstrapExecutor()
        assert executor is not None

    def test_execute_empty_commands(self, tmp_path):
        """Execute with empty command list."""
        executor = BootstrapExecutor()
        result = executor.execute(commands=[], workdir=tmp_path)
        assert result is True

    def test_execute_single_command(self, tmp_path):
        """Execute a single command."""
        executor = BootstrapExecutor()
        result = executor.execute(commands=["echo 'hello'"], workdir=tmp_path)
        assert result is True

    def test_execute_multiple_commands(self, tmp_path):
        """Execute multiple commands."""
        executor = BootstrapExecutor()
        result = executor.execute(
            commands=["echo 'first'", "echo 'second'"],
            workdir=tmp_path
        )
        assert result is True

    def test_execute_command_failure(self, tmp_path):
        """Execute with failing command returns False."""
        executor = BootstrapExecutor()
        result = executor.execute(
            commands=["false"],  # Command that always fails
            workdir=tmp_path
        )
        assert result is False
