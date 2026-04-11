"""Tests for pipeline.py — execution order, checkpointing, retry, parallel stages."""

import os
import time

import pytest

os.environ["STAGEHAND_DIR"] = "/tmp/stagehand-test-pipeline"

from stagehand import Pipeline
from stagehand import checkpoint as ckpt


@pytest.fixture(autouse=True)
def isolated_dir(tmp_path):
    os.environ["STAGEHAND_DIR"] = str(tmp_path / "stagehand")
    yield
    os.environ["STAGEHAND_DIR"] = "/tmp/stagehand-test-pipeline"


def pid(name="test"):
    """Unique pipeline ID per test."""
    import uuid

    return f"{name}-{uuid.uuid4().hex[:8]}"


# ── Basic execution ────────────────────────────────────────────────────────────


class TestBasicExecution:
    def test_stages_run_in_dep_order(self):
        order = []
        p = Pipeline(pid())
        p.stage("a", lambda ctx: order.append("a") or "a-out")
        p.stage("b", lambda ctx: order.append("b") or "b-out", deps=["a"])
        p.stage("c", lambda ctx: order.append("c") or "c-out", deps=["b"])
        p.run()
        assert order == ["a", "b", "c"]

    def test_stage_output_available_in_context(self):
        p = Pipeline(pid())
        p.stage("fetch", lambda ctx: {"name": "Ankit"})
        p.stage("greet", lambda ctx: f"Hello, {ctx['fetch']['name']}", deps=["fetch"])
        outputs = p.run()
        assert outputs["greet"] == "Hello, Ankit"

    def test_initial_context_available_to_all_stages(self):
        p = Pipeline(pid())
        p.stage("check", lambda ctx: ctx.get("dry_run"))
        outputs = p.run(context={"dry_run": True})
        assert outputs["check"] is True

    def test_run_returns_all_outputs(self):
        p = Pipeline(pid())
        p.stage("s1", lambda ctx: 1)
        p.stage("s2", lambda ctx: 2)
        outputs = p.run()
        assert outputs == {"s1": 1, "s2": 2}


# ── Checkpointing and resume ───────────────────────────────────────────────────


class TestCheckpointing:
    def test_checkpoint_saved_after_each_stage(self):
        pipeline_id = pid()
        calls = []

        def s1(ctx):
            calls.append("s1")
            return "s1-out"

        def s2(ctx):
            calls.append("s2")
            raise RuntimeError("s2 fails")

        p = Pipeline(pipeline_id)
        p.stage("s1", s1)
        p.stage("s2", s2, deps=["s1"])
        p.run()

        # Checkpoint should have s1=done, s2=failed
        state = ckpt.load(pipeline_id)
        assert state["stages"]["s1"]["status"] == "done"
        assert state["stages"]["s1"]["output"] == "s1-out"
        assert state["stages"]["s2"]["status"] == "failed"

    def test_resume_skips_completed_stages(self):
        pipeline_id = pid()
        s1_calls = []

        def s1(ctx):
            s1_calls.append(1)
            return "result"

        # First run — inject a partial checkpoint as if s1 already succeeded
        partial = {
            "pipeline_id": pipeline_id,
            "started_at": "2026-01-01T00:00:00Z",
            "stages": {
                "s1": {"status": "done", "output": "cached", "error": None, "attempts": 1, "finished_at": "2026-01-01T00:01:00Z"},
                "s2": {"status": "pending", "output": None, "error": None, "attempts": 0},
            },
        }
        ckpt.save(pipeline_id, partial)

        p = Pipeline(pipeline_id)
        p.stage("s1", s1)
        p.stage("s2", lambda ctx: ctx["s1"] + "-processed", deps=["s1"])
        outputs = p.run()

        # s1 must NOT have been called again
        assert s1_calls == []
        # s2 must use the cached s1 output
        assert outputs["s2"] == "cached-processed"

    def test_checkpoint_cleared_on_success(self):
        pipeline_id = pid()
        p = Pipeline(pipeline_id)
        p.stage("s1", lambda ctx: "ok")
        p.run()
        assert ckpt.load(pipeline_id) is None

    def test_checkpoint_kept_on_failure(self):
        pipeline_id = pid()
        p = Pipeline(pipeline_id)
        p.stage("s1", lambda ctx: (_ for _ in ()).throw(RuntimeError("boom")))
        p.run()
        assert ckpt.load(pipeline_id) is not None

    def test_run_history_archived_on_success(self):
        pipeline_id = pid()
        p = Pipeline(pipeline_id)
        p.stage("s1", lambda ctx: "done")
        p.run()
        runs = ckpt.list_runs(pipeline_id)
        assert len(runs) == 1
        assert runs[0]["done"] == 1

    def test_run_history_archived_on_failure(self):
        pipeline_id = pid()
        p = Pipeline(pipeline_id)
        p.stage("s1", lambda ctx: (_ for _ in ()).throw(RuntimeError("boom")))
        p.run()
        runs = ckpt.list_runs(pipeline_id)
        assert len(runs) == 1
        assert runs[0]["failed_stages"] == ["s1"]


# ── Retry + backoff ────────────────────────────────────────────────────────────


class TestRetry:
    def test_stage_retried_on_failure(self):
        attempts = []

        def flaky(ctx):
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("not yet")
            return "ok"

        p = Pipeline(pid())
        p.stage("flaky", flaky, retry=3, retry_backoff=0.01)
        outputs = p.run()
        assert outputs["flaky"] == "ok"
        assert len(attempts) == 3

    def test_stage_fails_after_max_retries(self):
        p = Pipeline(pid())
        p.stage("always_fail", lambda ctx: (_ for _ in ()).throw(RuntimeError("boom")), retry=2, retry_backoff=0.01)
        outputs = p.run()
        assert "always_fail" not in outputs

    def test_full_traceback_saved_to_checkpoint(self):
        pipeline_id = pid()
        p = Pipeline(pipeline_id)

        def explode(ctx):
            raise ValueError("this is the error")

        p.stage("boom", explode)
        p.run()
        state = ckpt.load(pipeline_id)
        error_text = state["stages"]["boom"]["error"]
        assert "ValueError" in error_text
        assert "this is the error" in error_text
        # Full traceback should include file and line info
        assert "Traceback" in error_text


# ── Fail modes ─────────────────────────────────────────────────────────────────


class TestFailModes:
    def test_fail_fast_stops_pipeline(self):
        # fail_fast stops stages that have not yet started (i.e. dependent stages).
        # s2 depends on s1, so when s1 fails fast, s2 must not run.
        ran = []
        p = Pipeline(pid())
        p.stage("s1", lambda ctx: (_ for _ in ()).throw(RuntimeError("boom")), fail_mode="fail_fast")
        p.stage("s2", lambda ctx: ran.append("s2") or "ok", deps=["s1"])
        p.run()
        assert "s2" not in ran

    def test_fail_continue_keeps_running_independent_stages(self):
        ran = []
        p = Pipeline(pid())
        p.stage("s1", lambda ctx: (_ for _ in ()).throw(RuntimeError("boom")), fail_mode="continue")
        p.stage("s2", lambda ctx: ran.append("s2") or "ok")  # no dep on s1
        p.run()
        assert "s2" in ran

    def test_dep_of_failed_stage_is_skipped(self):
        ran = []
        p = Pipeline(pid())
        p.stage("s1", lambda ctx: (_ for _ in ()).throw(RuntimeError("boom")), fail_mode="continue")
        p.stage("s2", lambda ctx: ran.append("s2") or "ok", deps=["s1"])  # dep on s1
        p.run()
        assert "s2" not in ran


# ── Parallel execution ─────────────────────────────────────────────────────────


class TestParallelExecution:
    def test_independent_stages_run_in_parallel(self):
        """Stages with no deps should overlap in time."""
        start_times = {}
        end_times = {}

        def slow_stage(name):
            def fn(ctx):
                start_times[name] = time.monotonic()
                time.sleep(0.1)
                end_times[name] = time.monotonic()
                return name

            fn.__name__ = name
            return fn

        p = Pipeline(pid())
        p.stage("a", slow_stage("a"))
        p.stage("b", slow_stage("b"))
        p.stage("c", slow_stage("c"))
        p.run()

        # All three should have started before any of them finished
        earliest_end = min(end_times.values())
        assert sum(1 for s in start_times.values() if s < earliest_end) > 1

    def test_parallel_stages_share_context(self):
        """Parallel stages must receive the same initial context."""
        received = {}

        def capture(name):
            def fn(ctx):
                received[name] = ctx.get("shared_key")
                return name

            fn.__name__ = name
            return fn

        p = Pipeline(pid())
        p.stage("a", capture("a"))
        p.stage("b", capture("b"))
        p.run(context={"shared_key": "hello"})
        assert received["a"] == "hello"
        assert received["b"] == "hello"


# ── Timeout ────────────────────────────────────────────────────────────────────


class TestTimeout:
    def test_stage_fails_when_timeout_exceeded(self):
        pipeline_id = pid()

        def slow(ctx):
            time.sleep(10)
            return "done"

        p = Pipeline(pipeline_id)
        p.stage("slow", slow, timeout=0.1)
        p.run()
        state = ckpt.load(pipeline_id)
        assert state["stages"]["slow"]["status"] == "failed"
        assert "timed out" in state["stages"]["slow"]["error"].lower()

    def test_stage_completes_within_timeout(self):
        p = Pipeline(pid())
        p.stage("fast", lambda ctx: "ok", timeout=5.0)
        outputs = p.run()
        assert outputs["fast"] == "ok"


# ── Edge cases ─────────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_pipeline_runs_without_error(self):
        p = Pipeline(pid())
        outputs = p.run()
        assert outputs == {}

    def test_pipeline_id_with_special_chars(self):
        p = Pipeline("my pipeline / 2026-03-14")
        p.stage("s1", lambda ctx: "ok")
        outputs = p.run()
        assert outputs["s1"] == "ok"

    def test_stage_output_none_is_valid(self):
        p = Pipeline(pid())
        p.stage("nullable", lambda ctx: None)
        outputs = p.run()
        assert "nullable" in outputs
        assert outputs["nullable"] is None

    def test_duplicate_stage_name_raises(self):
        p = Pipeline(pid())
        p.stage("s1", lambda ctx: "ok")
        with pytest.raises(ValueError, match="already registered"):
            p.stage("s1", lambda ctx: "ok")

    def test_reset_clears_checkpoint(self):
        pipeline_id = pid()
        p = Pipeline(pipeline_id)
        p.stage("s1", lambda ctx: (_ for _ in ()).throw(RuntimeError("boom")))
        p.run()
        assert ckpt.load(pipeline_id) is not None
        p.reset()
        assert ckpt.load(pipeline_id) is None
