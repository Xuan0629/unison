"""Tests for cli.py — CLI entry point."""
import pytest
from unison.cli import main


class TestCLI:
    """CLI tests."""

    def test_main_exists(self):
        """main() function exists."""
        assert callable(main)
