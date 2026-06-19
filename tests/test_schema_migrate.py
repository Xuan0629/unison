"""Tests for schema_migrate.py — version parsing, exceptions, migrate engine,
and V1→V2 State / PipelineSpec migrations."""

import logging

import pytest

from unison.schema_migrate import (
    CURRENT_VERSION,
    PIPELINE_MIGRATIONS,
    STATE_MIGRATIONS,
    SchemaMigrationError,
    SchemaVersionError,
    _migrate_pipeline_1_to_2,
    _migrate_state_1_to_2,
    _parse_version,
    migrate,
    register_pipeline_migration,
    register_state_migration,
)


# ============================================================================
# _parse_version
# ============================================================================


class TestParseVersion:
    """Tests for _parse_version()."""

    def test_valid_two_part(self):
        """Parse "2.0" → (2, 0)."""
        assert _parse_version("2.0") == (2, 0)

    def test_valid_with_larger_numbers(self):
        """Parse "10.15" → (10, 15)."""
        assert _parse_version("10.15") == (10, 15)

    def test_single_part_raises(self):
        """Single-part version ("1") raises ValueError."""
        with pytest.raises(ValueError, match="Invalid version"):
            _parse_version("1")

    def test_three_parts_raises(self):
        """Three-part version ("1.0.0") raises ValueError."""
        with pytest.raises(ValueError, match="Invalid version"):
            _parse_version("1.0.0")

    def test_non_numeric_raises(self):
        """Non-numeric version ("x.y") raises ValueError."""
        with pytest.raises(ValueError, match="Invalid version"):
            _parse_version("x.y")

    def test_none_raises(self):
        """None raises ValueError (via AttributeError catch)."""
        with pytest.raises(ValueError, match="Invalid version"):
            _parse_version(None)  # type: ignore[arg-type]


# ============================================================================
# Exceptions
# ============================================================================


class TestSchemaMigrationError:
    """Tests for SchemaMigrationError."""

    def test_attributes(self):
        """Exception carries from_ver, to_ver, original_error."""
        orig = ValueError("boom")
        exc = SchemaMigrationError("1.0", "2.0", original_error=orig)
        assert exc.from_ver == "1.0"
        assert exc.to_ver == "2.0"
        assert exc.original_error is orig

    def test_default_original_error(self):
        """original_error defaults to None."""
        exc = SchemaMigrationError("1.0", "2.0")
        assert exc.original_error is None

    def test_str_contains_versions(self):
        """String representation includes version and error."""
        exc = SchemaMigrationError(
            "1.0", "2.0", original_error=ValueError("boom")
        )
        assert "1.0" in str(exc)
        assert "2.0" in str(exc)
        assert "boom" in str(exc)


class TestSchemaVersionError:
    """Tests for SchemaVersionError."""

    def test_attributes(self):
        """Exception carries found_version, current_version."""
        exc = SchemaVersionError("3.0", "2.0")
        assert exc.found_version == "3.0"
        assert exc.current_version == "2.0"

    def test_str_contains_versions(self):
        """String representation includes both versions."""
        exc = SchemaVersionError("3.0", "2.0")
        assert "3.0" in str(exc)
        assert "2.0" in str(exc)


# ============================================================================
# Registration decorators
# ============================================================================


class TestRegistrationDecorators:
    """Tests for register_state_migration / register_pipeline_migration."""

    def test_register_state_migration(self):
        """Decorator adds entry to STATE_MIGRATIONS."""

        @register_state_migration("9.9", "10.0")
        def _test_fn(d: dict) -> dict:
            d["version"] = "10.0"
            return d

        assert ("9.9", "10.0") in STATE_MIGRATIONS
        assert STATE_MIGRATIONS[("9.9", "10.0")] is _test_fn

        # Clean up so other tests aren't affected
        del STATE_MIGRATIONS[("9.9", "10.0")]

    def test_register_pipeline_migration(self):
        """Decorator adds entry to PIPELINE_MIGRATIONS."""

        @register_pipeline_migration("9.9", "10.0")
        def _test_fn(d: dict) -> dict:
            d["version"] = "10.0"
            return d

        assert ("9.9", "10.0") in PIPELINE_MIGRATIONS
        assert PIPELINE_MIGRATIONS[("9.9", "10.0")] is _test_fn

        # Clean up
        del PIPELINE_MIGRATIONS[("9.9", "10.0")]


# ============================================================================
# migrate() core
# ============================================================================


class TestMigrateCore:
    """Tests for the migrate() engine."""

    def test_already_current_noop(self):
        """When version is already current, dict is returned unchanged."""
        d = {"version": "2.0", "data": "value"}
        result = migrate(d, STATE_MIGRATIONS, "2.0")
        assert result is d  # same object
        assert result["version"] == "2.0"

    def test_newer_version_raises(self):
        """Version newer than current raises SchemaVersionError."""
        d = {"version": "3.0", "data": "value"}
        with pytest.raises(SchemaVersionError) as exc_info:
            migrate(d, STATE_MIGRATIONS, "2.0")
        assert exc_info.value.found_version == "3.0"
        assert exc_info.value.current_version == "2.0"

    def test_missing_version_defaults_to_v1(self):
        """When version key is missing, defaults to "1.0" and migrates."""
        d = {"data": "value"}  # no version
        result = migrate(d, STATE_MIGRATIONS, "2.0")
        assert result["version"] == "2.0"
        assert "dag_status" in result

    def test_no_migration_registered_raises(self):
        """When no migration exists for a version, raises
        SchemaMigrationError."""
        d = {"version": "1.5"}  # no 1.5 → anything registered
        with pytest.raises(SchemaMigrationError) as exc_info:
            migrate(d, STATE_MIGRATIONS, "2.0")
        assert exc_info.value.from_ver == "1.5"
        assert "1.5" in str(exc_info.value.original_error)

    def test_migration_function_exception_wrapped(self):
        """If a migration function raises, it's wrapped in
        SchemaMigrationError."""
        # Register a broken migration
        @register_state_migration("5.0", "6.0")
        def _broken(d: dict) -> dict:
            raise RuntimeError("intentional test error")

        d = {"version": "5.0"}
        with pytest.raises(SchemaMigrationError) as exc_info:
            migrate(d, STATE_MIGRATIONS, "6.0")
        assert exc_info.value.from_ver == "5.0"
        assert exc_info.value.to_ver == "6.0"
        assert isinstance(exc_info.value.original_error, RuntimeError)
        assert "intentional test error" in str(exc_info.value.original_error)

        # Clean up
        del STATE_MIGRATIONS[("5.0", "6.0")]

    def test_migration_function_sets_wrong_version(self):
        """If migration fn sets version to something not matching the chain,
        the loop either continues or a 'No migration registered' error is
        raised."""
        # Register a migration that advances to an intermediate version
        # that has no further migration registered → error
        @register_state_migration("7.0", "7.5")
        def _bad_migration(d: dict) -> dict:
            d["version"] = "7.5"
            return d

        d = {"version": "7.0"}
        # 7.5 has no further migration → chain broken
        with pytest.raises(SchemaMigrationError) as exc_info:
            migrate(d, STATE_MIGRATIONS, "8.0")
        assert "No migration registered" in str(exc_info.value.original_error)

        # Clean up
        del STATE_MIGRATIONS[("7.0", "7.5")]

    def test_multi_hop_migration(self):
        """Chain: 1.0 → 2.0 → 2.1 works with multi-hop."""
        # Register a 2.0 → 2.1 hop temporarily
        @register_state_migration("2.0", "2.1")
        def _v2_to_v2_1(d: dict) -> dict:
            d["new_field"] = "added in 2.1"
            d["version"] = "2.1"
            return d

        d = {"version": "1.0", "dag_status": None, "reviewer_verdicts": []}
        result = migrate(d, STATE_MIGRATIONS, "2.1")
        assert result["version"] == "2.1"
        assert result["new_field"] == "added in 2.1"
        assert "dag_status" in result

        # Clean up
        del STATE_MIGRATIONS[("2.0", "2.1")]

    def test_max_hops_exceeded(self):
        """If migration chain exceeds max_hops (100), raises error."""
        # Register a self-loop that never advances the version:
        # 1.5 → 1.5 (doesn't change version → infinite loop)
        @register_state_migration("1.5", "1.5")
        def _cycle(d: dict) -> dict:
            return d  # doesn't advance version

        d = {"version": "1.5"}
        with pytest.raises(SchemaMigrationError, match="exceeded"):
            migrate(d, STATE_MIGRATIONS, "2.0")

        # Clean up
        del STATE_MIGRATIONS[("1.5", "1.5")]

    def test_migrate_logs_info(self, caplog):
        """Migration steps are logged at INFO level."""
        d = {"version": "1.0"}
        with caplog.at_level(logging.INFO, logger="unison.schema_migrate"):
            migrate(d, STATE_MIGRATIONS, "2.0")
        assert "Schema migration: 1.0 → 2.0" in caplog.text


# ============================================================================
# V1 → V2 State migration
# ============================================================================


class TestStateV1ToV2:
    """Tests for _migrate_state_1_to_2."""

    def test_adds_dag_status(self):
        """V1 → V2 adds dag_status default None."""
        d = {"version": "1.0"}
        result = migrate(d, STATE_MIGRATIONS, "2.0")
        assert result["dag_status"] is None

    def test_adds_reviewer_verdicts(self):
        """V1 → V2 adds reviewer_verdicts default []."""
        d = {"version": "1.0"}
        result = migrate(d, STATE_MIGRATIONS, "2.0")
        assert result["reviewer_verdicts"] == []

    def test_updates_version(self):
        """V1 → V2 updates version to "2.0"."""
        d = {"version": "1.0"}
        result = migrate(d, STATE_MIGRATIONS, "2.0")
        assert result["version"] == "2.0"

    def test_preserves_existing_fields(self):
        """Existing fields survive migration unchanged."""
        d = {
            "version": "1.0",
            "phase": "dev_active",
            "iteration": 3,
            "halt_signal": True,
            "history": [{"from_phase": "init", "to_phase": "dev_active"}],
        }
        result = migrate(d, STATE_MIGRATIONS, "2.0")
        assert result["phase"] == "dev_active"
        assert result["iteration"] == 3
        assert result["halt_signal"] is True
        assert len(result["history"]) == 1

    def test_does_not_overwrite_existing_dag_status(self):
        """If dag_status already present, value is preserved."""
        d = {"version": "1.0", "dag_status": "custom_value"}
        result = migrate(d, STATE_MIGRATIONS, "2.0")
        assert result["dag_status"] == "custom_value"


# ============================================================================
# V1 → V2 PipelineSpec migration
# ============================================================================


class TestPipelineV1ToV2:
    """Tests for _migrate_pipeline_1_to_2."""

    def test_v2_migration_adds_dag(self):
        """V1 → V2 migration adds dag=None default."""
        d = {"version": "1.0", "agents": {}}
        result = migrate(d, PIPELINE_MIGRATIONS, "2.0")
        assert result["version"] == "2.0"
        assert "dag" in result
        assert result["dag"] is None

    def test_v2_migration_adds_reviewer_config(self):
        """V1 → V2 migration adds reviewer_config=None default."""
        d = {"version": "1.0", "agents": {}}
        result = migrate(d, PIPELINE_MIGRATIONS, "2.0")
        assert result["version"] == "2.0"
        assert "reviewer_config" in result
        assert result["reviewer_config"] is None

    def test_v2_migration_adds_parallel_dev(self):
        """V1 → V2 migration adds parallel_dev=None default."""
        d = {"version": "1.0", "agents": {}}
        result = migrate(d, PIPELINE_MIGRATIONS, "2.0")
        assert result["version"] == "2.0"
        assert "parallel_dev" in result
        assert result["parallel_dev"] is None

    def test_updates_version(self):
        """V1 → V2 updates version to "2.0"."""
        d = {"version": "1.0", "agents": {}}
        result = migrate(d, PIPELINE_MIGRATIONS, "2.0")
        assert result["version"] == "2.0"

    def test_preserves_existing_agent_fields(self):
        """Existing agent fields survive migration."""
        d = {
            "version": "1.0",
            "agents": {
                "developer": {"role": "developer", "runtime": "claude", "model": "gpt-5"},
            },
        }
        result = migrate(d, PIPELINE_MIGRATIONS, "2.0")
        assert result["agents"]["developer"]["role"] == "developer"
        assert result["agents"]["developer"]["runtime"] == "claude"
        assert result["agents"]["developer"]["model"] == "gpt-5"

    def test_does_not_overwrite_existing_dag(self):
        """Existing dag field preserved (migration no-op on existing keys)."""
        d = {"version": "1.0", "agents": {}, "dag": [{"name": "existing"}]}
        result = migrate(d, PIPELINE_MIGRATIONS, "2.0")
        assert result["dag"] == [{"name": "existing"}]
        assert result["version"] == "2.0"

    def test_preserves_non_agent_top_level_keys(self):
        """Top-level keys outside agents are preserved."""
        d = {
            "version": "1.0",
            "agents": {},
            "project_root": "/tmp",
            "project": {"language": "python"},
        }
        result = migrate(d, PIPELINE_MIGRATIONS, "2.0")
        assert result["project_root"] == "/tmp"
        assert result["project"]["language"] == "python"


# ============================================================================
# Integration: State.from_dict with migration
# ============================================================================


class TestStateFromDictMigration:
    """Integration tests for State.from_dict with schema migration."""

    def test_v1_dict_auto_migrated(self):
        """A V1 dict is auto-migrated to V2 during from_dict."""
        from unison.state import State

        v1_dict = {
            "version": "1.0",
            "phase": "dev_active",
            "iteration": 2,
            "history": [],
            "halt_signal": False,
            "halt_reason": None,
            "last_dev_commit": None,
            "last_review_verdict": None,
            "last_review_path": None,
            "last_activity": None,
        }
        s = State.from_dict(v1_dict)
        assert s.version == "2.0"
        assert s.phase == "dev_active"

    def test_current_dict_no_migration(self):
        """A V2 dict passes through without triggering migration chain."""
        from unison.state import State

        v2_dict = {
            "version": "2.0",
            "phase": "planning_review",
            "iteration": 1,
            "history": [],
            "halt_signal": False,
            "halt_reason": None,
            "last_dev_commit": None,
            "last_review_verdict": None,
            "last_review_path": None,
            "last_activity": None,
        }
        s = State.from_dict(v2_dict)
        assert s.version == "2.0"
        assert s.phase == "planning_review"


# ============================================================================
# Integration: PipelineLoader.load with migration
# ============================================================================


class TestPipelineLoadMigration:
    """Integration tests for PipelineLoader.load with schema migration."""

    def test_v1_pipeline_auto_migrated(self, tmp_path):
        """A V1 pipeline.yaml is auto-migrated to V2 during load."""
        from unison.pipeline import PipelineLoader

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "1.0"
project_root: "."
agents:
  developer:
    role: developer
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer.md"
""")
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        assert spec.version == "2.0"

    def test_v2_pipeline_no_unnecessary_migration(self, tmp_path):
        """A V2 pipeline.yaml loads without migration errors."""
        from unison.pipeline import PipelineLoader

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
version: "2.0"
project_root: "."
agents:
  developer:
    role: developer
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer.md"
""")
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        assert spec.version == "2.0"

    def test_missing_version_treated_as_v1(self, tmp_path):
        """Pipeline without version field is treated as V1 and migrated."""
        from unison.pipeline import PipelineLoader

        pipeline_file = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("""
project_root: "."
agents:
  developer:
    role: developer
    runtime: claude
    model: deepseek-v4-pro
    system_prompt_path: "prompts/developer.md"
  reviewer:
    role: reviewer
    runtime: codex
    model: gpt-5.5
    system_prompt_path: "prompts/reviewer.md"
""")
        loader = PipelineLoader()
        spec = loader.load(pipeline_file)
        assert spec.version == "2.0"
