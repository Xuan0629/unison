"""test_manifest.py — Tests for unison.manifest: build_halt_manifest + ManifestWriter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from unison.manifest import ManifestWriter, build_halt_manifest


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Temporary project root with clean .unison directory."""
    unison_dir = tmp_path / ".unison"
    unison_dir.mkdir()
    return tmp_path


@pytest.fixture
def manifest_path(tmp_path: Path) -> Path:
    """Path for a test manifest file."""
    return tmp_path / "test_manifest.json"


@pytest.fixture
def writer(manifest_path: Path) -> ManifestWriter:
    """A ManifestWriter targeting a temp file."""
    return ManifestWriter(path=manifest_path)


# ============================================================================
# build_halt_manifest
# ============================================================================


class TestBuildHaltManifest:
    """Tests for the canonical halt manifest builder."""

    def test_minimal_args_writes_file(self, project_root: Path) -> None:
        """Minimal arguments produce a valid manifest file."""
        manifest = build_halt_manifest(project_root)

        halt_path = project_root / ".unison" / "halt_manifest.json"
        assert halt_path.exists()

        # Verify content
        data = json.loads(halt_path.read_text())
        assert data["phase"] == "init"
        assert data["iteration"] == 0
        assert data["halt_reason"] is None
        assert data["last_commit"] is None
        assert data["last_verdict"] is None
        assert data["budget"] == {}
        assert data["history"] == []
        assert "halted_at" in data

    def test_returns_manifest_dict(self, project_root: Path) -> None:
        """build_halt_manifest returns the dict it wrote."""
        result = build_halt_manifest(project_root, phase="dev_active")
        assert isinstance(result, dict)
        assert result["phase"] == "dev_active"

    def test_all_fields_populated(self, project_root: Path) -> None:
        """All optional fields are serialized correctly."""
        manifest = build_halt_manifest(
            project_root,
            phase="dev_review",
            iteration=3,
            halt_reason="SIGINT received",
            last_commit="abc1234",
            last_verdict="REQUEST_CHANGES",
            budget={"daily_used": 145_000, "daily_limit": 1_000_000},
            history=[{"to_phase": "dev_active", "by": "orchestrator"}],
            agents=[{"role": "developer", "runtime": "claude", "model": "sonnet"}],
            tasks=[{"id": "1", "label": "Write code", "status": "done"}],
            extra={"custom_field": "extra_value"},
        )

        assert manifest["phase"] == "dev_review"
        assert manifest["iteration"] == 3
        assert manifest["halt_reason"] == "SIGINT received"
        assert manifest["last_commit"] == "abc1234"
        assert manifest["last_verdict"] == "REQUEST_CHANGES"
        assert manifest["budget"]["daily_used"] == 145_000
        assert len(manifest["history"]) == 1
        assert len(manifest["agents"]) == 1
        assert len(manifest["tasks"]) == 1
        assert manifest["custom_field"] == "extra_value"
        assert "halted_at" in manifest

    def test_returns_new_dict_each_call(self, project_root: Path) -> None:
        """Each call returns a distinct dict (no shared mutation)."""
        m1 = build_halt_manifest(project_root, iteration=1)
        m2 = build_halt_manifest(project_root, iteration=2)
        assert m1["iteration"] == 1
        assert m2["iteration"] == 2
        assert m1 is not m2

    def test_atomic_write_no_tmp_leftover(self, project_root: Path) -> None:
        """The .tmp file is cleaned up after atomic rename."""
        build_halt_manifest(project_root)
        halt_dir = project_root / ".unison"
        tmp_files = list(halt_dir.glob("*.tmp"))
        assert len(tmp_files) == 0, f"tmp file left behind: {tmp_files}"

    def test_env_summary_included(self, project_root: Path) -> None:
        """Manifest includes an env_summary section."""
        manifest = build_halt_manifest(project_root)
        env = manifest["env_summary"]
        assert "python_version" in env
        assert "platform" in env
        assert "hostname" in env
        assert "cwd" in env
        assert env["python_version"]  # non-empty

    def test_no_extra_key_leaks_defaults(self, project_root: Path) -> None:
        """Without extra=, no accidental extra keys appear."""
        manifest = build_halt_manifest(project_root)
        expected_keys = {
            "halted_at", "phase", "iteration", "halt_reason",
            "last_commit", "last_verdict", "budget", "history",
            "agents", "tasks", "env_summary",
        }
        assert set(manifest.keys()) == expected_keys

    def test_existing_file_overwritten(self, project_root: Path) -> None:
        """Calling twice overwrites the previous manifest."""
        build_halt_manifest(project_root, phase="planning_active")
        build_halt_manifest(project_root, phase="dev_active")

        halt_path = project_root / ".unison" / "halt_manifest.json"
        data = json.loads(halt_path.read_text())
        assert data["phase"] == "dev_active"

    def test_budget_edge_zero_limits(self, project_root: Path) -> None:
        """Budget with zero limits is serialized correctly."""
        manifest = build_halt_manifest(
            project_root,
            budget={"daily_used": 0, "daily_limit": 0, "per_task_used": 0, "per_task_limit": 0},
        )
        assert manifest["budget"]["daily_limit"] == 0

    def test_budget_edge_over_limit(self, project_root: Path) -> None:
        """Budget over limit (exhausted) is serialized correctly."""
        manifest = build_halt_manifest(
            project_root,
            budget={"daily_used": 2_000_000, "daily_limit": 1_000_000},
        )
        assert manifest["budget"]["daily_used"] == 2_000_000


# ============================================================================
# ManifestWriter — read / write
# ============================================================================


class TestManifestWriterReadWrite:
    """Tests for ManifestWriter.read() and .write()."""

    def test_read_missing_file_returns_empty_dict(self, writer: ManifestWriter) -> None:
        """Reading a non-existent manifest returns {}."""
        assert writer.read() == {}

    def test_write_then_read_roundtrip(self, writer: ManifestWriter) -> None:
        """Data written is the same data read back."""
        data = {"key1": "val1", "key2": 42}
        writer.write(data)
        assert writer.read() == data

    def test_write_persists_to_disk(self, writer: ManifestWriter) -> None:
        """Data is persisted to the file on disk."""
        writer.write({"a": 1})
        assert writer.path.exists()
        disk_data = json.loads(writer.path.read_text())
        assert disk_data == {"a": 1}

    def test_write_overwrites_previous(self, writer: ManifestWriter) -> None:
        """Subsequent writes replace the entire manifest."""
        writer.write({"first": True})
        writer.write({"second": True})
        assert writer.read() == {"second": True}

    def test_atomic_write_no_tmp_leftover(self, writer: ManifestWriter) -> None:
        """The .tmp file does not remain after write."""
        writer.write({"x": 1})
        tmp_files = list(writer.path.parent.glob("*.tmp"))
        assert len(tmp_files) == 0, f"tmp file left behind: {tmp_files}"


# ============================================================================
# ManifestWriter — append / update
# ============================================================================


class TestManifestWriterAppend:
    """Tests for ManifestWriter.append_record() and .update_record()."""

    def test_append_record_adds_new_key(self, writer: ManifestWriter) -> None:
        """append_record inserts a record under a new key."""
        writer.append_record("session-1", {"attempt": 1, "error": "TIMEOUT"})
        data = writer.read()
        assert "session-1" in data
        assert data["session-1"]["attempt"] == 1

    def test_append_record_overwrites_existing_key(self, writer: ManifestWriter) -> None:
        """append_record replaces an existing key entirely."""
        writer.append_record("session-1", {"attempt": 1})
        writer.append_record("session-1", {"attempt": 2, "error": "MODEL_ERROR"})
        data = writer.read()
        # Full replacement, not merge
        assert data["session-1"] == {"attempt": 2, "error": "MODEL_ERROR"}

    def test_append_record_preserves_other_keys(self, writer: ManifestWriter) -> None:
        """Appending under a new key does not affect other records."""
        writer.append_record("s1", {"x": 1})
        writer.append_record("s2", {"y": 2})
        data = writer.read()
        assert data["s1"] == {"x": 1}
        assert data["s2"] == {"y": 2}

    def test_update_record_merges_existing(self, writer: ManifestWriter) -> None:
        """update_record merges updates into an existing record."""
        writer.append_record("s1", {"attempt": 1, "error": "TIMEOUT"})
        writer.update_record("s1", {"retryable": True, "exit_code": 0})
        data = writer.read()
        assert data["s1"]["attempt"] == 1  # preserved
        assert data["s1"]["error"] == "TIMEOUT"  # preserved
        assert data["s1"]["retryable"] is True  # merged
        assert data["s1"]["exit_code"] == 0  # merged

    def test_update_record_creates_new_if_missing(self, writer: ManifestWriter) -> None:
        """update_record creates a new record when key does not exist."""
        writer.update_record("new-key", {"field": "value"})
        assert writer.read()["new-key"] == {"field": "value"}

    def test_update_record_handles_non_dict_existing(self, writer: ManifestWriter) -> None:
        """update_record replaces a non-dict value safely."""
        writer.append_record("k", "not-a-dict")
        writer.update_record("k", {"now": "dict"})
        assert writer.read()["k"] == {"now": "dict"}


# ============================================================================
# ManifestWriter — records_for
# ============================================================================


class TestManifestWriterRecordsFor:
    """Tests for ManifestWriter.records_for()."""

    def test_empty_manifest_returns_empty_list(self, writer: ManifestWriter) -> None:
        """No records → empty list."""
        assert writer.records_for("anything") == []

    def test_exact_key_match(self, writer: ManifestWriter) -> None:
        """records_for returns record matching exact key prefix."""
        writer.append_record("20250101-1", {"a": 1})
        result = writer.records_for("20250101-1")
        assert result == [{"a": 1}]

    def test_prefix_matches_multiple(self, writer: ManifestWriter) -> None:
        """records_for returns all records with matching prefix."""
        writer.append_record("20250101-1", {"n": 1})
        writer.append_record("20250101-2", {"n": 2})
        writer.append_record("20250101-3", {"n": 3})
        result = writer.records_for("20250101")
        assert len(result) == 3

    def test_prefix_does_not_match_partial(self, writer: ManifestWriter) -> None:
        """Prefix matching is by startswith, not substring."""
        writer.append_record("abc123", {"n": 1})
        result = writer.records_for("abc12")  # prefix exists
        assert len(result) == 1
        result2 = writer.records_for("bc12")  # not a prefix
        assert len(result2) == 0

    def test_newest_first_sort_order(self, writer: ManifestWriter) -> None:
        """Records are returned newest-first (reverse chronological by key)."""
        writer.append_record("session-001", {"n": 1})
        writer.append_record("session-003", {"n": 3})
        writer.append_record("session-002", {"n": 2})
        result = writer.records_for("session-")
        assert [r["n"] for r in result] == [3, 2, 1]

    def test_non_overlapping_prefix(self, writer: ManifestWriter) -> None:
        """Prefix only matches the intended keys."""
        writer.append_record("s1", {"n": 1})
        writer.append_record("other", {"n": 99})
        result = writer.records_for("s1")
        assert len(result) == 1
        assert result[0]["n"] == 1


# ============================================================================
# ManifestWriter — record_count
# ============================================================================


class TestManifestWriterRecordCount:
    """Tests for ManifestWriter.record_count()."""

    def test_empty_returns_zero(self, writer: ManifestWriter) -> None:
        assert writer.record_count() == 0

    def test_counts_all_keys(self, writer: ManifestWriter) -> None:
        writer.append_record("a", {})
        writer.append_record("b", {})
        writer.append_record("c", {})
        assert writer.record_count() == 3

    def test_count_after_append(self, writer: ManifestWriter) -> None:
        assert writer.record_count() == 0
        writer.append_record("x", {})
        assert writer.record_count() == 1


# ============================================================================
# ManifestWriter — concurrency-safe structure
# ============================================================================


class TestManifestWriterEdgeCases:
    """Edge case tests for ManifestWriter."""

    def test_nested_directories_created(self, tmp_path: Path) -> None:
        """ManifestWriter creates parent directories on write."""
        deep_path = tmp_path / "deep" / "nested" / "dir" / "manifest.json"
        w = ManifestWriter(path=deep_path)
        w.write({"ok": True})
        assert deep_path.exists()

    def test_empty_dict_write(self, writer: ManifestWriter) -> None:
        """Writing an empty dict works."""
        writer.write({})
        assert writer.read() == {}

    def test_unicode_keys_and_values(self, writer: ManifestWriter) -> None:
        """Unicode in keys and values survives round-trip."""
        data = {"reason": "流水线已暂停", "迭代": 3, "裁决": "需修改"}
        writer.write(data)
        assert writer.read() == data

    def test_nested_data_structures(self, writer: ManifestWriter) -> None:
        """Nested lists and dicts survive round-trip."""
        data = {
            "history": [
                {"from": None, "to": "init", "tags": ["bootstrap", "initial"]},
                {"from": "init", "to": "planning_active", "tags": []},
            ],
            "metadata": {"version": 2, "nested": {"deep": True}},
        }
        writer.write(data)
        assert writer.read() == data

    def test_special_values_serialized(self, writer: ManifestWriter) -> None:
        """None, bool, int, float survive round-trip."""
        data = {"none_val": None, "bool_val": True, "int_val": 42, "float_val": 3.14}
        writer.write(data)
        result = writer.read()
        assert result["none_val"] is None
        assert result["bool_val"] is True
        assert result["int_val"] == 42
        assert result["float_val"] == 3.14

    def test_file_not_directory(self, tmp_path: Path) -> None:
        """Writing to a path where a file exists at a parent segment works."""
        # Create a file where a directory component would be
        p = tmp_path / "collision"
        p.write_text("block")
        # Now try to write manifest.json inside "collision" — should fail
        w = ManifestWriter(path=p / "manifest.json")
        with pytest.raises((FileNotFoundError, NotADirectoryError, OSError)):
            w.write({"x": 1})

    def test_multiple_writers_independent(self, tmp_path: Path) -> None:
        """Two ManifestWriter instances pointing at different files are independent."""
        w1 = ManifestWriter(path=tmp_path / "a.json")
        w2 = ManifestWriter(path=tmp_path / "b.json")
        w1.write({"from": "a"})
        w2.write({"from": "b"})
        assert w1.read() == {"from": "a"}
        assert w2.read() == {"from": "b"}
