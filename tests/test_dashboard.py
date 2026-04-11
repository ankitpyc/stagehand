"""Tests for dashboard.py — data collection and bug fix verification."""

import os

import pytest

os.environ["STAGEHAND_DIR"] = "/tmp/stagehand-test-dashboard"

from stagehand import checkpoint as ckpt
from stagehand.dashboard import collect_all_pipelines, get_dashboard_data


@pytest.fixture(autouse=True)
def isolated_dir(tmp_path):
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
        ckpt.save(
            "dash-pipe",
            _state(
                "dash-pipe",
                {
                    "fetch": {
                        "status": "done",
                        "output": None,
                        "error": None,
                        "attempts": 1,
                    },
                },
            ),
        )
        data = get_dashboard_data()
        assert len(data["active"]) == 1
        assert data["active"][0]["pipeline_id"] == "dash-pipe"

    def test_includes_run_history(self):
        state = _state(
            "hist-pipe",
            {
                "step": {
                    "status": "done",
                    "output": "ok",
                    "error": None,
                    "attempts": 1,
                },
            },
        )
        ckpt.save("hist-pipe", state)
        ckpt.archive("hist-pipe", state)
        data = get_dashboard_data()
        assert "hist-pipe" in data["runs"]
        assert len(data["runs"]["hist-pipe"]) >= 1


class TestCollectAllPipelines:
    def test_returns_empty_list_when_no_data(self):
        result = collect_all_pipelines()
        assert result == []

    def test_active_checkpoint_appears(self):
        ckpt.save(
            "active-pipe",
            _state(
                "active-pipe",
                {
                    "build": {
                        "status": "pending",
                        "output": None,
                        "error": None,
                        "attempts": 0,
                    },
                },
            ),
        )
        result = collect_all_pipelines()
        assert len(result) == 1
        assert result[0]["id"] == "active-pipe"
        assert result[0]["status"] == "active"
