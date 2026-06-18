"""Tests for optimizer.py — HarnessOptimizer."""
import tempfile
from pathlib import Path
import pytest

from unison.optimizer import HarnessOptimizer
from unison.world import World
from unison.state import State


class TestHarnessOptimizer:
    """HarnessOptimizer tests."""

    def test_create_optimizer(self, tmp_path):
        """Create a HarnessOptimizer."""
        optimizer = HarnessOptimizer()
        assert optimizer is not None

    def test_analyze_creates_report(self, tmp_path):
        """analyze() creates optimizer report."""
        world = World(root=tmp_path)
        world.ensure_directories()
        
        # Create notifications file
        world.notifications_file.write_text('{"timestamp": "2026-06-18T10:00:00Z", "phase": "dev_active", "severity": "info", "title": "Test", "body": "Test"}\n')
        
        state = State(phase="done", iteration=5)
        
        optimizer = HarnessOptimizer()
        report_path = optimizer.analyze(
            project="test-project",
            notifications_path=world.notifications_file,
            outbox_dir=world.outbox_dir,
            logs_dir=world.logs_dir,
            state=state
        )
        
        assert report_path.exists()
        assert "optimizer" in report_path.name

    def test_analyze_empty_notifications(self, tmp_path):
        """analyze() handles empty notifications file."""
        world = World(root=tmp_path)
        world.ensure_directories()
        
        # Create empty notifications file
        world.notifications_file.write_text("")
        
        state = State(phase="done", iteration=0)
        
        optimizer = HarnessOptimizer()
        report_path = optimizer.analyze(
            project="test-project",
            notifications_path=world.notifications_file,
            outbox_dir=world.outbox_dir,
            logs_dir=world.logs_dir,
            state=state
        )
        
        assert report_path.exists()
