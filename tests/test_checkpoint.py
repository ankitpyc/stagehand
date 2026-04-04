"""Tests for checkpoint.py — atomic writes, file locking, run history."""

import json
import os
import threading
import pytest
from pathlib import Path

# Use an isolated temp dir for all checkpoint tests
os.environ["STAGEHAND_DIR"] = "/tmp/stagehand-test"

from stagehand import checkpoint as ckpt


@pytest.fixture(autouse=True)
def clean_checkpoints(tmp_path):
    """Each test uses a fresh temp dir."""
    os.environ["STAGEHAND_DIR"] = str(tmp_path / "stagehand")
    yield
    os.environ["STAGEHAND_DIR"] = "/tmp/stagehand-test"


def _state(pipeline_id, stages=None):
    return {
        "pipeline_id": pipeline_id,
        "started_at": "2026-03-14T00:00:00Z",
        "stages": stages or {},
    }


class TestSaveLoad:
    def test_save_and_load_roundtrip(self):
        state = _state("test-pipe", {"fetch": {"status": "done", "output": {"x": 1}, "error": None, "attempts": 1}})
        ckpt.save("test-pipe", state)
        loaded = ckpt.load("test-pipe")
        assert loaded is not None
        assert loaded["pipeline_id"] == "test-pipe"
        assert loaded["stages"]["fetch"]["output"] == {"x": 1}

    def test_load_returns_none_when_no_checkpoint(self):
        assert ckpt.load("nonexistent-pipeline") is None

    def test_clear_removes_checkpoint(self):
        ckpt.save("test-pipe", _state("test-pipe"))
        ckpt.clear("test-pipe")
        assert ckpt.load("test-pipe") is None

    def test_safe_id_handles_slashes_and_spaces(self):
        pipeline_id = "my pipeline/with spaces"
        ckpt.save(pipeline_id, _state(pipeline_id))
        loaded = ckpt.load(pipeline_id)
        assert loaded is not None


class TestAtomicWrite:
    def test_no_tmp_file_left_after_write(self, tmp_path):
        os.environ["STAGEHAND_DIR"] = str(tmp_path / "stagehand")
        ckpt.save("test-atomic", _state("test-atomic"))
        active_dir = Path(os.environ["STAGEHAND_DIR"]) / "active"
        tmp_files = list(active_dir.glob("*.tmp"))
        assert tmp_files == [], f"Stale .tmp files found: {tmp_files}"

    def test_checkpoint_is_valid_json_after_write(self, tmp_path):
        os.environ["STAGEHAND_DIR"] = str(tmp_path / "stagehand")
        state = _state("test-json", {"s1": {"status": "done", "output": "hello", "error": None, "attempts": 1}})
        ckpt.save("test-json", state)
        path = Path(os.environ["STAGEHAND_DIR"]) / "active" / "test-json.json"
        data = json.loads(path.read_text())
        assert data["stages"]["s1"]["output"] == "hello"


class TestConcurrentAccess:
    def test_concurrent_saves_do_not_corrupt(self):
        errors = []
        pipeline_id = "concurrent-test"

        def writer(i):
            try:
                state = _state(pipeline_id, {f"stage_{i}": {"status": "done", "output": i, "error": None, "attempts": 1}})
                ckpt.save(pipeline_id, state)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Errors during concurrent writes: {errors}"
        loaded = ckpt.load(pipeline_id)
        # Last write wins — should be valid JSON either way
        assert loaded is not None


class TestRunHistory:
    def test_archive_creates_run_file(self):
        state = _state("archive-test")
        archived = ckpt.archive("archive-test", state)
        assert archived.exists()
        data = json.loads(archived.read_text())
        assert data["pipeline_id"] == "archive-test"
        assert "archived_at" in data

    def test_list_runs_returns_history(self):
        state = _state("runs-test")
        ckpt.archive("runs-test", state)
        ckpt.archive("runs-test", state)
        runs = ckpt.list_runs("runs-test")
        assert len(runs) == 2

    def test_list_runs_empty_for_unknown_pipeline(self):
        assert ckpt.list_runs("no-such-pipeline") == []


class TestListActive:
    def test_list_active_shows_saved_checkpoints(self):
        ckpt.save("pipeline-a", _state("pipeline-a"))
        ckpt.save("pipeline-b", _state("pipeline-b"))
        active = ckpt.list_active()
        ids = [r["pipeline_id"] for r in active]
        assert "pipeline-a" in ids
        assert "pipeline-b" in ids

    def test_cleared_pipeline_not_in_active(self):
        ckpt.save("to-clear", _state("to-clear"))
        ckpt.clear("to-clear")
        active = ckpt.list_active()
        assert not any(r["pipeline_id"] == "to-clear" for r in active)
