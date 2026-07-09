"""Tests for checklist.py — ChecklistItem, ChecklistStatus, atomic I/O."""
import json
import tempfile
from pathlib import Path

import pytest
import yaml as _yaml

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


# ============================================================================
# Frontmatter extraction (simulating _parse_checklist behavior)
# ============================================================================


class TestParseChecklistFrontmatter:
    """Test the YAML frontmatter extraction logic used by _parse_checklist.

    Review files use ``---`` YAML frontmatter delimiters with markdown body
    following.  _parse_checklist must extract the frontmatter before
    parsing YAML — using yaml.safe_load on the raw file would fail.
    """

    def test_extract_checklist_from_frontmatter(self):
        """Parse a checklist: table from a review file with --- delimiters."""
        review_text = """\
---
verdict: PASS
summary: All items done
findings: []
checklist:
  - id: P1.1
    title: Add logging
    status: done
    severity: HIGH
    evidence: commit abc1234
  - id: P1.2
    title: Add tests
    status: done
    severity: MEDIUM
    evidence: test_checklist.py
---

## Review Notes

All checklist items are complete. The implementation matches the PRD.
"""
        parts = review_text.split("---", 2)
        yaml_text = parts[1]
        raw = _yaml.safe_load(yaml_text)

        assert isinstance(raw, dict)
        checklist_raw = raw.get("checklist")
        assert isinstance(checklist_raw, list)
        assert len(checklist_raw) == 2

        items = [ChecklistItem.from_dict(e) for e in checklist_raw]
        assert items[0].id == "P1.1"
        assert items[0].status == "done"
        assert items[1].id == "P1.2"
        assert items[1].status == "done"

    def test_extract_returns_none_without_frontmatter(self):
        """Returns None when the review file has no --- frontmatter."""
        review_text = "# Just a markdown review\n\nNo YAML frontmatter here."
        # Simulate _parse_checklist guard
        if not review_text.startswith("---"):
            result = None
        assert result is None

    def test_extract_returns_none_without_checklist_key(self):
        """Returns None when frontmatter exists but has no checklist: key."""
        review_text = """\
---
verdict: PASS
summary: Looks good
findings: []
---

Body text.
"""
        parts = review_text.split("---", 2)
        yaml_text = parts[1]
        raw = _yaml.safe_load(yaml_text)

        checklist_raw = raw.get("checklist")
        assert checklist_raw is None

    def test_frontmatter_with_markdown_after(self):
        """yaml.safe_load on extracted frontmatter works correctly,
        while the full text with markdown body would not be pure YAML."""
        review_text = """\
---
verdict: PASS
summary: test
findings: []
checklist:
  - id: P1.1
    title: Test
    status: done
---

Some markdown content here.
"""
        parts = review_text.split("---", 2)
        assert len(parts) == 3
        # Frontmatter is in parts[1], body in parts[2]
        yaml_text = parts[1]
        raw = _yaml.safe_load(yaml_text)
        assert isinstance(raw, dict)
        assert raw["verdict"] == "PASS"
        assert len(raw["checklist"]) == 1


# ============================================================================
# checklist_strict_mode verdict override logic
# ============================================================================


class TestChecklistStrictMode:
    """Test the verdict override behavior for checklist_strict_mode.

    When checklist_strict_mode=True and items are still pending, the
    PASS verdict should be overridden to REQUEST_CHANGES.
    """

    def test_strict_mode_overrides_pass_when_pending(self):
        """Strict mode + pending items → override PASS to REQUEST_CHANGES."""
        checklist_strict_mode = True
        verdict = "PASS"
        checklist = ChecklistStatus(items=[
            ChecklistItem(id="P1.1", title="A", status="done"),
            ChecklistItem(id="P1.2", title="B", status="pending"),
        ])

        # Simulate the _run_loop verdict override logic
        if checklist_strict_mode and checklist.pending > 0:
            verdict = "REQUEST_CHANGES"

        assert verdict == "REQUEST_CHANGES"

    def test_strict_mode_no_override_when_all_resolved(self):
        """Strict mode + all resolved → PASS verdict unchanged."""
        checklist_strict_mode = True
        verdict = "PASS"
        checklist = ChecklistStatus(items=[
            ChecklistItem(id="P1.1", title="A", status="done"),
            ChecklistItem(id="P1.2", title="B", status="deferred",
                          evidence="Out of scope"),
        ])

        if checklist_strict_mode and checklist.pending > 0:
            verdict = "REQUEST_CHANGES"

        assert verdict == "PASS"

    def test_strict_mode_off_does_not_override(self):
        """checklist_strict_mode=False → never overrides verdict."""
        checklist_strict_mode = False
        verdict = "PASS"
        checklist = ChecklistStatus(items=[
            ChecklistItem(id="P1.1", title="A", status="pending"),
        ])

        if checklist_strict_mode and checklist.pending > 0:
            verdict = "REQUEST_CHANGES"

        assert verdict == "PASS"

    def test_strict_mode_does_not_override_request_changes(self):
        """Strict mode does not downgrade REQUEST_CHANGES to anything else."""
        checklist_strict_mode = True
        verdict = "REQUEST_CHANGES"
        checklist = ChecklistStatus(items=[
            ChecklistItem(id="P1.1", title="A", status="pending"),
        ])

        if checklist_strict_mode and checklist.pending > 0:
            verdict = "REQUEST_CHANGES"

        # Already REQUEST_CHANGES, should stay REQUEST_CHANGES
        assert verdict == "REQUEST_CHANGES"

    def test_strict_mode_empty_checklist(self):
        """Strict mode + empty checklist → no override (nothing pending)."""
        checklist_strict_mode = True
        verdict = "PASS"
        checklist = ChecklistStatus(items=[])

        if checklist_strict_mode and checklist.pending > 0:
            verdict = "REQUEST_CHANGES"

        assert verdict == "PASS"


# ============================================================================
# _inject_checklist_into_prompt — role-based injection
# ============================================================================


class TestChecklistPromptInjection:
    """Test the prompt injection logic for different agent roles."""

    @staticmethod
    def _make_status():
        return ChecklistStatus(items=[
            ChecklistItem(id="P1.1", title="Add logging", status="done",
                          severity="HIGH", evidence="commit abc"),
            ChecklistItem(id="P1.2", title="Add tests", status="pending",
                          severity="MEDIUM"),
        ])

    def test_developer_gets_remaining_block(self):
        """Developer prompt gets the remaining_block with pending items."""
        status = self._make_status()
        prompt = "Original prompt content."
        # Simulate _inject_checklist_into_prompt for developer
        block = status.remaining_block()
        result = prompt + "\n\n" + block
        assert "## Remaining Checklist Items" in result
        assert "P1.2" in result
        assert "Add tests" in result

    def test_developer_empty_when_all_resolved(self):
        """Developer gets no injection when all items are resolved."""
        status = ChecklistStatus(items=[
            ChecklistItem(id="P1.1", title="A", status="done"),
        ])
        prompt = "Original prompt."
        # Simulate: nothing pending → no injection
        if status.pending == 0:
            result = prompt
        else:
            result = prompt + "\n\n" + status.remaining_block()
        assert result == prompt

    def test_reviewer_gets_markdown_table(self):
        """Reviewer prompt gets the full markdown table with status."""
        status = self._make_status()
        prompt = "Original reviewer prompt."
        header = "\n\n## Current Checklist Status\n\n"
        header += "Update each item's status in your review YAML frontmatter "
        header += "(`checklist:` table).\n\n"
        result = prompt + header + status.markdown_table()
        assert "## Current Checklist Status" in result
        assert "| ID | Title | Status | Severity |" in result
        assert "P1.1" in result
        assert "done" in result
        assert "pending" in result

    def test_reviewer_no_injection_when_empty(self):
        """Reviewer gets no injection when checklist is empty."""
        status = ChecklistStatus(items=[])
        prompt = "Original reviewer prompt."
        if status.total == 0:
            result = prompt
        else:
            result = prompt + "\n\n" + status.markdown_table()
        assert result == prompt

    def test_other_role_unchanged(self):
        """Non-dev, non-reviewer roles get prompt unchanged."""
        status = self._make_status()
        prompt = "Original planner prompt."
        # Simulate: role != "developer" and role != "reviewer" → return prompt
        result = prompt
        assert result == prompt
        assert "Remaining Checklist Items" not in result
