"""Tests for checklist.py — ChecklistItem, ChecklistStatus, atomic I/O."""
import json
import tempfile
from pathlib import Path

import pytest

from unison.checklist import ChecklistItem, ChecklistStatus
from unison.io import atomic_read_json, atomic_write_json
from unison.world import World


# ============================================================================
# ChecklistItem
# ============================================================================


class TestChecklistItem:
    """ChecklistItem dataclass tests."""

    def test_create_minimal(self):
        """Create a checklist item with required fields only."""
        item = ChecklistItem(id="P1.1", title="Add logging")
        assert item.id == "P1.1"
        assert item.title == "Add logging"
        assert item.status == "pending"
        assert item.severity == "MEDIUM"
        assert item.evidence == ""
        assert item.source == ""

    def test_create_full(self):
        """Create a checklist item with all fields."""
        item = ChecklistItem(
            id="P2.1",
            title="Add validation",
            status="done",
            severity="HIGH",
            evidence="commit abc1234",
            source="moa-synthesis-round2.md",
        )
        assert item.id == "P2.1"
        assert item.title == "Add validation"
        assert item.status == "done"
        assert item.severity == "HIGH"
        assert item.evidence == "commit abc1234"
        assert item.source == "moa-synthesis-round2.md"

    def test_to_dict(self):
        """to_dict serializes all fields."""
        item = ChecklistItem(
            id="P1.1",
            title="Add logging",
            status="done",
            severity="HIGH",
            evidence="commit abc1234",
            source="review.md",
        )
        d = item.to_dict()
        assert d == {
            "id": "P1.1",
            "title": "Add logging",
            "status": "done",
            "severity": "HIGH",
            "evidence": "commit abc1234",
            "source": "review.md",
        }

    def test_from_dict_full(self):
        """from_dict deserializes all fields."""
        d = {
            "id": "P1.1",
            "title": "Add logging",
            "status": "done",
            "severity": "HIGH",
            "evidence": "commit abc1234",
            "source": "review.md",
        }
        item = ChecklistItem.from_dict(d)
        assert item.id == "P1.1"
        assert item.title == "Add logging"
        assert item.status == "done"
        assert item.severity == "HIGH"
        assert item.evidence == "commit abc1234"
        assert item.source == "review.md"

    def test_from_dict_partial(self):
        """from_dict handles missing fields with defaults."""
        item = ChecklistItem.from_dict({"id": "P1.1"})
        assert item.id == "P1.1"
        assert item.title == ""
        assert item.status == "pending"
        assert item.severity == "MEDIUM"
        assert item.evidence == ""

    def test_from_dict_empty(self):
        """from_dict handles empty dict."""
        item = ChecklistItem.from_dict({})
        assert item.id == ""
        assert item.title == ""
        assert item.status == "pending"


# ============================================================================
# ChecklistStatus
# ============================================================================


class TestChecklistStatus:
    """ChecklistStatus aggregate tests."""

    @staticmethod
    def _make_items():
        """Return a standard set of 3 items with different statuses."""
        return [
            ChecklistItem(id="P1.1", title="Add logging", status="done", severity="HIGH",
                          evidence="commit abc1234"),
            ChecklistItem(id="P1.2", title="Add tests", status="pending", severity="MEDIUM"),
            ChecklistItem(id="P1.3", title="Update docs", status="deferred", severity="LOW",
                          evidence="Out of scope for P9"),
        ]

    def test_counts(self):
        """done/deferred/pending/total counts are correct."""
        status = ChecklistStatus(items=self._make_items())
        assert status.total == 3
        assert status.done == 1
        assert status.pending == 1
        assert status.deferred == 1

    def test_all_resolved_false(self):
        """all_resolved is False when items are pending."""
        status = ChecklistStatus(items=self._make_items())
        assert not status.all_resolved

    def test_all_resolved_true(self):
        """all_resolved is True when all items are done or deferred."""
        items = [
            ChecklistItem(id="P1.1", title="A", status="done"),
            ChecklistItem(id="P1.2", title="B", status="deferred",
                          evidence="Not needed"),
        ]
        status = ChecklistStatus(items=items)
        assert status.all_resolved

    def test_all_resolved_empty(self):
        """all_resolved is True for an empty checklist."""
        status = ChecklistStatus(items=[])
        assert status.all_resolved

    def test_pending_items(self):
        """pending_items returns only items with status='pending'."""
        status = ChecklistStatus(items=self._make_items())
        pending = status.pending_items
        assert len(pending) == 1
        assert pending[0].id == "P1.2"
        assert pending[0].status == "pending"

    def test_pending_items_none(self):
        """pending_items returns empty list when nothing pending."""
        items = [
            ChecklistItem(id="P1.1", title="A", status="done"),
            ChecklistItem(id="P1.2", title="B", status="deferred"),
        ]
        status = ChecklistStatus(items=items)
        assert status.pending_items == []

    def test_markdown_table(self):
        """markdown_table renders a markdown table."""
        status = ChecklistStatus(items=self._make_items())
        md = status.markdown_table()
        assert "| ID | Title | Status | Severity |" in md
        assert "P1.1" in md
        assert "Add logging" in md
        assert "done" in md
        assert "**Summary**: 1 done, 1 deferred, 1 pending" in md

    def test_markdown_table_empty(self):
        """markdown_table handles empty checklist gracefully."""
        status = ChecklistStatus(items=[])
        md = status.markdown_table()
        assert "_No checklist items._" in md

    def test_remaining_block_with_pending(self):
        """remaining_block lists pending items for developer prompt."""
        status = ChecklistStatus(items=self._make_items())
        block = status.remaining_block()
        assert "## Remaining Checklist Items" in block
        assert "P1.2" in block
        assert "Add tests" in block
        assert "severity: MEDIUM" in block

    def test_remaining_block_empty(self):
        """remaining_block returns empty string when nothing pending."""
        items = [
            ChecklistItem(id="P1.1", title="A", status="done"),
        ]
        status = ChecklistStatus(items=items)
        assert status.remaining_block() == ""

    def test_to_dict(self):
        """to_dict serializes the full checklist."""
        status = ChecklistStatus(items=self._make_items())
        d = status.to_dict()
        assert d["version"] == "1.0"
        assert len(d["items"]) == 3
        assert d["items"][0]["id"] == "P1.1"

    def test_from_dict(self):
        """from_dict deserializes the full checklist."""
        status = ChecklistStatus(items=self._make_items())
        d = status.to_dict()
        restored = ChecklistStatus.from_dict(d)
        assert restored.total == 3
        assert restored.done == 1
        assert restored.pending == 1
        assert restored.deferred == 1
        assert restored.items[0].id == "P1.1"


# ============================================================================
# Atomic I/O
# ============================================================================


class TestAtomicIO:
    """atomic_write_json / atomic_read_json tests."""

    def test_write_and_read(self, tmp_path):
        """Round-trip: write JSON, read it back."""
        filepath = tmp_path / "test.json"
        data = {"key": "value", "nested": {"a": 1}}
        atomic_write_json(filepath, data)
        result = atomic_read_json(filepath)
        assert result == data

    def test_read_missing_file(self):
        """Returns None when file does not exist."""
        result = atomic_read_json("/nonexistent/path/checklist.json")
        assert result is None

    def test_read_invalid_json(self, tmp_path):
        """Returns None for invalid JSON."""
        filepath = tmp_path / "bad.json"
        filepath.write_text("not valid json {{{")
        result = atomic_read_json(filepath)
        assert result is None

    def test_creates_parent_directory(self, tmp_path):
        """atomic_write_json creates parent directories."""
        filepath = tmp_path / "subdir" / "nested" / "data.json"
        atomic_write_json(filepath, {"x": 1})
        assert filepath.exists()
        result = atomic_read_json(filepath)
        assert result == {"x": 1}

    def test_atomic_write_replaces(self, tmp_path):
        """Subsequent writes replace the file contents."""
        filepath = tmp_path / "test.json"
        atomic_write_json(filepath, {"version": 1})
        atomic_write_json(filepath, {"version": 2})
        result = atomic_read_json(filepath)
        assert result == {"version": 2}

    def test_checklist_status_file_roundtrip(self, tmp_path):
        """ChecklistStatus can be persisted and restored via atomic I/O."""
        filepath = tmp_path / "checklist.json"
        items = [
            ChecklistItem(id="P1.1", title="Add logging", status="done",
                          severity="HIGH", evidence="commit abc1234"),
            ChecklistItem(id="P1.2", title="Add tests", status="pending",
                          severity="MEDIUM"),
        ]
        status = ChecklistStatus(items=items)
        atomic_write_json(filepath, status.to_dict())
        raw = atomic_read_json(filepath)
        restored = ChecklistStatus.from_dict(raw)
        assert restored.total == 2
        assert restored.done == 1
        assert restored.pending == 1
        assert not restored.all_resolved


# ============================================================================
# World.checklist_file
# ============================================================================


class TestWorldChecklistFile:
    """World.checklist_file property tests."""

    def test_checklist_file_path(self):
        """checklist_file points to .unison/checklist.json."""
        w = World(root=Path("/tmp/project"))
        cf = w.checklist_file
        assert cf.name == "checklist.json"
        assert cf.parent.name == ".unison"
        assert ".unison" in str(cf)

    def test_checklist_file_in_unison_dir(self, tmp_path):
        """checklist_file is inside .unison directory."""
        w = World(root=tmp_path)
        assert w.checklist_file == w.unison_dir / "checklist.json"


# ============================================================================
# Merge logic (simulating _parse_checklist behavior)
# ============================================================================


class TestChecklistMerge:
    """Test the reviewer→persisted merge logic used by _parse_checklist."""

    def test_merge_reviewer_done_updates_pending(self):
        """Reviewer marks a pending item as done — persisted status updates."""
        persisted = ChecklistStatus(items=[
            ChecklistItem(id="P1.1", title="Add logging", status="pending"),
            ChecklistItem(id="P1.2", title="Add tests", status="pending"),
        ])
        # Simulate reviewer output
        reviewer_entries = [
            {"id": "P1.1", "title": "Add logging", "status": "done",
             "evidence": "commit abc1234"},
        ]
        reviewer_items = {}
        for entry in reviewer_entries:
            item = ChecklistItem.from_dict(entry)
            reviewer_items[item.id] = item

        # Merge
        current_by_id = {it.id: it for it in persisted.items}
        for item_id, rev_item in reviewer_items.items():
            if item_id in current_by_id and rev_item.status != "pending":
                current_by_id[item_id].status = rev_item.status
                current_by_id[item_id].evidence = rev_item.evidence

        assert persisted.items[0].status == "done"
        assert persisted.items[0].evidence == "commit abc1234"
        assert persisted.items[1].status == "pending"  # unchanged
        assert persisted.pending == 1
        assert not persisted.all_resolved

    def test_merge_all_resolved(self):
        """Reviewer marks all items done — checklist converges."""
        persisted = ChecklistStatus(items=[
            ChecklistItem(id="P1.1", title="A", status="pending"),
            ChecklistItem(id="P1.2", title="B", status="pending"),
        ])
        reviewer_entries = [
            {"id": "P1.1", "status": "done", "evidence": "commit abc"},
            {"id": "P1.2", "status": "deferred", "evidence": "Out of scope"},
        ]
        reviewer_items = {}
        for entry in reviewer_entries:
            item = ChecklistItem.from_dict(entry)
            reviewer_items[item.id] = item

        current_by_id = {it.id: it for it in persisted.items}
        for item_id, rev_item in reviewer_items.items():
            if item_id in current_by_id and rev_item.status != "pending":
                current_by_id[item_id].status = rev_item.status
                current_by_id[item_id].evidence = rev_item.evidence

        assert persisted.all_resolved
        assert persisted.done == 1
        assert persisted.deferred == 1

    def test_merge_new_item_from_reviewer(self):
        """Reviewer introduces a new checklist item — appended."""
        persisted = ChecklistStatus(items=[
            ChecklistItem(id="P1.1", title="A", status="done"),
        ])
        reviewer_entries = [
            {"id": "P1.2", "title": "Missing edge case", "status": "pending",
             "severity": "HIGH"},
        ]
        reviewer_items = {}
        for entry in reviewer_entries:
            item = ChecklistItem.from_dict(entry)
            reviewer_items[item.id] = item

        current_by_id = {it.id: it for it in persisted.items}
        for item_id, rev_item in reviewer_items.items():
            if item_id not in current_by_id:
                persisted.items.append(rev_item)

        assert persisted.total == 2
        assert persisted.pending == 1
        assert persisted.items[1].id == "P1.2"

    def test_reviewer_pending_does_not_override(self):
        """Reviewer leaves item as pending — original status preserved."""
        # This tests the guard: only update when reviewer_item.status != "pending"
        persisted = ChecklistStatus(items=[
            ChecklistItem(id="P1.1", title="A", status="done",
                          evidence="commit abc"),
        ])
        reviewer_entries = [
            {"id": "P1.1", "status": "pending"},
        ]
        reviewer_items = {}
        for entry in reviewer_entries:
            item = ChecklistItem.from_dict(entry)
            reviewer_items[item.id] = item

        current_by_id = {it.id: it for it in persisted.items}
        for item_id, rev_item in reviewer_items.items():
            if item_id in current_by_id and rev_item.status != "pending":
                current_by_id[item_id].status = rev_item.status
                current_by_id[item_id].evidence = rev_item.evidence

        # Pending reviewer status should NOT override existing done
        assert persisted.items[0].status == "done"
        assert persisted.items[0].evidence == "commit abc"
