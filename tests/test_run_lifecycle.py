"""Tests for run_lifecycle.py — durable run lifecycle persistence."""
from pathlib import Path

from unison.checkpoint import FileCheckpointManager
from unison.run_history import RunHistoryStore
from unison.run_lifecycle import RunLifecyclePersistence
from unison.state import State
from unison.world import RunContext, World


def _persistence(tmp_path: Path) -> tuple[RunLifecyclePersistence, World]:
    world = World(tmp_path / "project")
    world.ensure_directories()
    return (
        RunLifecyclePersistence(
            world=world,
            checkpoint_manager=FileCheckpointManager(tmp_path / "checkpoints"),
            run_history=RunHistoryStore(world.root),
        ),
        world,
    )


def test_start_and_finish_preserve_run_history_record(tmp_path):
    persistence, world = _persistence(tmp_path)
    state = State(phase="done", iteration=2, last_review_verdict="PASS")

    assert persistence.start_run("run-1", "P10", "dev:quick") is True
    persistence.finish_run("run-1", state)

    records = RunHistoryStore(world.root).list_runs(migrate=False)
    assert len(records) == 1
    assert records[0]["id"] == "run-1"
    assert records[0]["pipeline_name"] == "P10"
    assert records[0]["mode"] == "dev:quick"
    assert records[0]["status"] == "done"
    assert records[0]["phase"] == "done"
    assert records[0]["iteration"] == 2
    assert records[0]["verdict"] == "PASS"


def test_notification_write_does_not_mutate_state(tmp_path):
    persistence, world = _persistence(tmp_path)
    state = State(phase="dev_active", iteration=3, pipeline_name="P10", observer_language="zh")
    before = state.to_dict()

    persistence.write_notification(
        state,
        event_type="phase_done",
        phase="dev_review",
        verdict="PASS",
        summary="review passed",
    )

    assert state.to_dict() == before
    record = (world.notifications_file).read_text(encoding="utf-8")
    assert '"event_type": "phase_done"' in record
    assert '"pipeline": "P10"' in record
    assert '"language": "zh"' in record


def test_checkpoint_writes_scoped_and_latest_state_without_mutation(tmp_path):
    persistence, world = _persistence(tmp_path)
    context = RunContext.create(world.root, "P10")
    state = State(phase="dev_active", iteration=4, last_dev_commit="abc123")
    before = state.to_dict()

    persistence.save_checkpoint(state, context, iteration=7)

    assert state.to_dict() == before
    assert (world.unison_dir / "state.json").exists()
    assert world.run_state_file(context).exists()
    checkpoints = list((tmp_path / "checkpoints" / world.project_id).glob("ckpt-7-*.json"))
    assert len(checkpoints) == 1
