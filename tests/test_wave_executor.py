"""Tests for wave_executor — compute_waves planning + WaveExecutor execution."""

import os
import time
import uuid
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pytest

# Use an isolated temp dir for any checkpoint I/O performed during these tests.
# The fixture below overrides this per-test, but the env var must be set
# *before* ``stagehand.checkpoint`` resolves any paths.
os.environ.setdefault("STAGEHAND_DIR", "/tmp/stagehand-test-wave-executor")

from stagehand import checkpoint as ckpt
from stagehand import wave_executor
from stagehand.wave_executor import WaveExecutor, compute_waves


# ── Planning-test helpers ─────────────────────────────────────────────────────
#
# ``compute_waves`` only reads ``spec.stages``, and from each stage only
# ``.name`` and ``.deps``. Using ``SimpleNamespace`` stubs keeps these tests
# focused on the planning algorithm and independent of ``PipelineSpec``'s
# wider surface area.


def _stage(name: str, deps: Optional[Iterable[str]] = None) -> SimpleNamespace:
    return SimpleNamespace(name=name, deps=list(deps or []))


def _spec(stages: List[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(stages=stages)


# ── Topology cases ───────────────────────────────────────────────────────────


def test_linear_chain_yields_one_stage_per_wave():
    # a → b → c → d  — each stage depends on the previous one.
    spec = _spec([
        _stage("a"),
        _stage("b", ["a"]),
        _stage("c", ["b"]),
        _stage("d", ["c"]),
    ])
    assert compute_waves(spec) == [["a"], ["b"], ["c"], ["d"]]


def test_diamond_groups_parallel_branches_into_one_wave():
    # a → {b, c} → d
    spec = _spec([
        _stage("a"),
        _stage("b", ["a"]),
        _stage("c", ["a"]),
        _stage("d", ["b", "c"]),
    ])
    assert compute_waves(spec) == [["a"], ["b", "c"], ["d"]]


def test_two_independent_chains_run_side_by_side():
    # a → b   and   c → d   — fully disjoint subgraphs.
    spec = _spec([
        _stage("a"),
        _stage("b", ["a"]),
        _stage("c"),
        _stage("d", ["c"]),
    ])
    assert compute_waves(spec) == [["a", "c"], ["b", "d"]]


def test_empty_spec_returns_empty_list():
    assert compute_waves(_spec([])) == []


# ── Determinism ──────────────────────────────────────────────────────────────


def test_within_wave_names_are_sorted_alphabetically():
    # Stage declaration order shouldn't influence the per-wave ordering.
    spec = _spec([
        _stage("zebra"),
        _stage("apple"),
        _stage("mango"),
    ])
    assert compute_waves(spec) == [["apple", "mango", "zebra"]]


# ── Error paths ──────────────────────────────────────────────────────────────


def test_cycle_raises_value_error():
    # a → b → a  — a tight two-node cycle.
    spec = _spec([
        _stage("a", ["b"]),
        _stage("b", ["a"]),
    ])
    with pytest.raises(ValueError, match="cycle"):
        compute_waves(spec)


def test_unknown_dep_raises_value_error_with_clear_message():
    # 'a' depends on 'ghost' which is not declared in the spec.
    spec = _spec([
        _stage("a", ["ghost"]),
    ])
    with pytest.raises(ValueError, match="unknown dep"):
        compute_waves(spec)


def test_unknown_dep_message_names_both_dep_and_stage():
    spec = _spec([
        _stage("a", ["ghost"]),
    ])
    with pytest.raises(ValueError) as exc_info:
        compute_waves(spec)
    msg = str(exc_info.value)
    assert "ghost" in msg
    assert "a" in msg


# ── WaveExecutor: fixtures and fake subprocess ────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_checkpoint_dir(tmp_path, monkeypatch):
    """Point every test at a fresh tmp directory for checkpoint I/O."""
    monkeypatch.setenv("STAGEHAND_DIR", str(tmp_path / "stagehand"))
    yield


def _pid(label: str = "we") -> str:
    """Generate a unique pipeline id per test to avoid cross-test leakage."""
    return f"{label}-{uuid.uuid4().hex[:8]}"


def _exec_stage(name: str, deps: Optional[Iterable[str]] = None) -> SimpleNamespace:
    """Build a ``StageSpec``-shaped stub for WaveExecutor tests."""
    return SimpleNamespace(
        name=name,
        prompt="",
        model="m",
        deps=list(deps or []),
        capabilities=[],
        timeout=None,
        retry=1,
    )


def _exec_spec(pipeline_id: str, stages: List[SimpleNamespace], task: str = "t") -> SimpleNamespace:
    """Build a minimal ``PipelineSpec``-shaped stub with ``to_dict``."""
    spec = SimpleNamespace(
        pipeline_id=pipeline_id,
        task=task,
        stages=stages,
        created_at=None,
    )

    def _to_dict() -> Dict[str, Any]:
        return {
            "pipeline_id": spec.pipeline_id,
            "task": spec.task,
            "stages": [dict(s.__dict__) for s in spec.stages],
            "created_at": spec.created_at,
        }

    spec.to_dict = _to_dict
    return spec


class FakePopen:
    """
    Drop-in replacement for ``subprocess.Popen`` used in WaveExecutor tests.

    On construction it records the argv it was called with, extracts the
    ``--pipeline-id`` and ``--stage`` flags from those args, and writes a
    terminal status/output for that stage straight into the real checkpoint
    via ``ckpt.save`` — simulating an agent runner that completes instantly.

    Per-stage behavior can be customised by populating ``FakePopen.behavior``
    before invoking the executor::

        FakePopen.behavior["a"] = ("failed", None)   # → a fails
        FakePopen.behavior["b"] = ("done", "b-out")  # → b succeeds with output

    Stages absent from ``behavior`` default to ``("done", f"{stage}-out")``.
    """

    # Class-level state — reset between tests by the ``fake_popen`` fixture.
    instances: List["FakePopen"] = []
    behavior: Dict[str, Tuple[str, Optional[str]]] = {}

    def __init__(self, args, *_, **__):
        type(self).instances.append(self)
        self.args = list(args)
        self._returncode: Optional[int] = 0
        self.terminated = False
        self.waited = False

        try:
            pid = self.args[self.args.index("--pipeline-id") + 1]
            stage = self.args[self.args.index("--stage") + 1]
        except (ValueError, IndexError):  # pragma: no cover - guards malformed argv
            return

        status, output = type(self).behavior.get(stage, ("done", f"{stage}-out"))
        state = ckpt.load(pid)
        if state is not None:
            entry = state["stages"].setdefault(
                stage,
                {"status": "pending", "output": None, "error": None, "attempts": 0},
            )
            entry["status"] = status
            entry["output"] = output if status == "done" else None
            if status == "failed":
                entry["error"] = output or "stage failed"
            ckpt.save(pid, state)

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: Optional[float] = None) -> int:
        self.waited = True
        return self._returncode or 0

    def poll(self) -> Optional[int]:
        return self._returncode


@pytest.fixture
def fake_popen(monkeypatch):
    """Monkeypatch ``subprocess.Popen`` in wave_executor with FakePopen."""
    FakePopen.instances = []
    FakePopen.behavior = {}
    monkeypatch.setattr(wave_executor.subprocess, "Popen", FakePopen)
    yield FakePopen
    FakePopen.instances = []
    FakePopen.behavior = {}


# ── WaveExecutor tests ───────────────────────────────────────────────────────


def test_run_three_stage_chain_returns_outputs_per_stage(fake_popen):
    # a → b → c — three sequential waves; FakePopen reports each one done
    # with output "<stage>-out".
    pid = _pid("chain")
    spec = _exec_spec(pid, [
        _exec_stage("a"),
        _exec_stage("b", ["a"]),
        _exec_stage("c", ["b"]),
    ])

    result = WaveExecutor(spec, poll_interval=0.01).run()

    assert result == {"a": "a-out", "b": "b-out", "c": "c-out"}

    # Every stage should have been spawned exactly once with the expected argv.
    spawned_for = [
        p.args[p.args.index("--stage") + 1] for p in fake_popen.instances
    ]
    assert sorted(spawned_for) == ["a", "b", "c"]
    for p in fake_popen.instances:
        assert "--pipeline-id" in p.args
        assert p.args[p.args.index("--pipeline-id") + 1] == pid


def test_run_marks_downstream_stages_skipped_when_wave_fails(fake_popen):
    # a → b → c — a fails; b and c should be marked 'skipped' and not run.
    pid = _pid("fail")
    spec = _exec_spec(pid, [
        _exec_stage("a"),
        _exec_stage("b", ["a"]),
        _exec_stage("c", ["b"]),
    ])
    fake_popen.behavior["a"] = ("failed", None)

    result = WaveExecutor(spec, poll_interval=0.01).run()

    # Only successes are returned — no 'a' (failed), no 'b'/'c' (skipped).
    assert result == {}

    # Checkpoint reflects the halt: a failed, b/c skipped.
    state = ckpt.load(pid)
    assert state is not None
    assert state["stages"]["a"]["status"] == "failed"
    assert state["stages"]["b"]["status"] == "skipped"
    assert state["stages"]["c"]["status"] == "skipped"

    # Only 'a' got spawned — downstream waves were short-circuited.
    spawned_for = [
        p.args[p.args.index("--stage") + 1] for p in fake_popen.instances
    ]
    assert spawned_for == ["a"]


def test_run_completes_quickly_with_short_poll_interval(fake_popen):
    # With FakePopen writing 'done' synchronously during construction, the
    # polling loop should exit on its very first iteration for every wave —
    # so a 3-stage chain at poll_interval=0.01 finishes well under a second.
    pid = _pid("fast")
    spec = _exec_spec(pid, [
        _exec_stage("a"),
        _exec_stage("b", ["a"]),
        _exec_stage("c", ["b"]),
    ])

    start = time.monotonic()
    result = WaveExecutor(spec, poll_interval=0.01).run()
    elapsed = time.monotonic() - start

    assert result == {"a": "a-out", "b": "b-out", "c": "c-out"}
    assert elapsed < 1.0, f"WaveExecutor.run() took {elapsed:.3f}s; expected < 1s"


def test_run_uses_default_runner_cmd_when_none_supplied(fake_popen):
    # The default runner_cmd should target ``python -m stagehand.agent_runner``.
    pid = _pid("dflt")
    spec = _exec_spec(pid, [_exec_stage("a")])

    WaveExecutor(spec, poll_interval=0.01).run()

    assert len(fake_popen.instances) == 1
    argv = fake_popen.instances[0].args
    assert argv[1:3] == ["-m", "stagehand.agent_runner"]
    assert "--pipeline-id" in argv and "--stage" in argv


def test_run_uses_custom_runner_cmd_when_supplied(fake_popen):
    pid = _pid("custom")
    spec = _exec_spec(pid, [_exec_stage("a")])

    WaveExecutor(
        spec,
        poll_interval=0.01,
        runner_cmd=["/usr/bin/env", "agent-runner"],
    ).run()

    argv = fake_popen.instances[0].args
    assert argv[:2] == ["/usr/bin/env", "agent-runner"]
    # Per-stage flags are appended after the user-supplied prefix.
    assert argv[-4:] == ["--pipeline-id", pid, "--stage", "a"]


def test_run_initializes_checkpoint_with_all_stages_pending(fake_popen):
    # Use a no-op runner so no stage status changes during this test — that
    # way we can observe the initial pending state and the persisted spec.
    pid = _pid("init")
    spec = _exec_spec(pid, [_exec_stage("a"), _exec_stage("b", ["a"])])

    # Pre-mark every stage as 'done' so the executor never spawns anything;
    # we just want to confirm the initial state is written.
    fake_popen.behavior["a"] = ("done", "a-out")
    fake_popen.behavior["b"] = ("done", "b-out")

    WaveExecutor(spec, poll_interval=0.01).run()

    state = ckpt.load(pid)
    assert state is not None
    assert state["pipeline_id"] == pid
    assert state["task"] == "t"
    assert state["spec"]["pipeline_id"] == pid
    assert set(state["stages"].keys()) == {"a", "b"}
    assert "started_at" in state


def test_run_terminates_remaining_popens_on_failure(fake_popen):
    # When wave 1 has parallel siblings and one fails, the executor should
    # terminate() + wait() on every spawned Popen.
    pid = _pid("term")
    spec = _exec_spec(pid, [
        _exec_stage("a"),
        _exec_stage("b"),      # sibling of 'a' in wave 0
        _exec_stage("c", ["a", "b"]),
    ])
    fake_popen.behavior["a"] = ("failed", None)
    fake_popen.behavior["b"] = ("done", "b-out")

    result = WaveExecutor(spec, poll_interval=0.01).run()

    # Only 'b' completed cleanly; 'a' failed; 'c' skipped.
    assert result == {"b": "b-out"}

    # All wave-0 Popens should have been terminated and waited on.
    wave0_procs = [
        p for p in fake_popen.instances
        if p.args[p.args.index("--stage") + 1] in ("a", "b")
    ]
    assert len(wave0_procs) == 2
    assert all(p.terminated for p in wave0_procs)
    assert all(p.waited for p in wave0_procs)
