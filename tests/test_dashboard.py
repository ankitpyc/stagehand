"""Tests for dashboard.py — data collection functions."""

import os

import pytest

os.environ["STAGEHAND_DIR"] = "/tmp/stagehand-test-dashboard"

from stagehand import checkpoint as ckpt
from stagehand.dashboard import collect_all_pipelines, get_dashboard_data


@pytest.fixture(autouse=True)
def isolated_dir(tmp_path):
    """Each test uses a fresh temp dir."""
    os.environ["STAGEHAND_DIR"] = str(tmp_path / "stagehand")
    yield
    os.environ["STAGEHAND_DIR"] = "/tmp/stagehand-test-dashboard"


def _state(pipeline_id, stages=None):
    return {
        "pipeline_id": pipeline_id,
        "started_at": "2026-03-14T00:00:00Z",
        "stages": stages or {},
    }


class TestGetDashboardData:
    def test_returns_empty_when_no_data(self):
        data = get_dashboard_data()
        assert data["active"] == []
        assert data["registry"] == {}
        assert data["runs"] == {}

    def test_includes_active_pipelines(self):
        ckpt.save("dash-pipe", _state("dash-pipe", {
            "fetch": {"status": "done", "output": "ok", "error": None, "attempts": 1},
        }))
        data = get_dashboard_data()
        assert len(data["active"]) == 1
        assert data["active"][0]["pipeline_id"] == "dash-pipe"

    def test_collects_run_history(self):
        state = _state("history-pipe", {
            "fetch": {"status": "done", "output": "ok", "error": None, "attempts": 1},
        })
        ckpt.archive("history-pipe", state)
        # Also save active so the pipeline_id is known
        ckpt.save("history-pipe", state)
        data = get_dashboard_data()
        assert "history-pipe" in data["runs"]
        assert len(data["runs"]["history-pipe"]) >= 1

    def test_no_name_error_on_ckpt_calls(self):
        """Regression: ckt.list_runs() used to raise NameError (should be ckpt)."""
        ckpt.save("regress-pipe", _state("regress-pipe"))
        # This should not raise NameError
        data = get_dashboard_data()
        assert "active" in data


class TestCollectAllPipelines:
    def test_returns_empty_list_when_no_data(self):
        result = collect_all_pipelines()
        assert result == []

    def test_includes_active_checkpoint(self):
        ckpt.save("active-pipe", _state("active-pipe", {
            "s1": {"status": "done", "output": 1, "error": None, "attempts": 1},
            "s2": {"status": "pending", "output": None, "error": None, "attempts": 0},
        }))
        result = collect_all_pipelines()
        assert len(result) >= 1
        pipe = next(p for p in result if p["id"] == "active-pipe")
        assert pipe["status"] == "active"
        assert pipe["stages"]["s1"] == "done"
        assert pipe["stages"]["s2"] == "pending"
