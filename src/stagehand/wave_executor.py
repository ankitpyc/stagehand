"""
wave_executor.py — Planning + execution for agent-pipeline waves.

This module exposes two layers:

1. ``compute_waves(spec)`` — *pure planning*. Groups the stages of a
   ``PipelineSpec`` into "waves": each wave is a list of stage names whose
   dependencies all lie in strictly earlier waves. Stages within a wave can
   be executed concurrently; waves themselves run sequentially.

2. ``WaveExecutor(spec, ...)`` — *execution*. Walks the waves and spawns one
   ``subprocess.Popen`` per stage (the agent runner). It does **not** block
   on ``proc.wait`` — completion is signalled through the shared checkpoint,
   which the executor polls every ``poll_interval`` seconds. On failure,
   sibling Popens in the wave are terminated and all stages in downstream
   waves are marked ``'skipped'`` in the checkpoint before execution halts.

The two layers are intentionally split: ``compute_waves`` has no I/O and no
side effects, which keeps it cheap to unit-test and reuse from other planners.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from . import checkpoint

if TYPE_CHECKING:  # pragma: no cover - type-only import
    from stagehand.spec import PipelineSpec


# ── Planning: compute_waves ────────────────────────────────────────────────────


def compute_waves(spec: "PipelineSpec") -> List[List[str]]:
    """
    Group stages into execution waves via Kahn-style topological BFS.

    A stage is placed in wave *N* iff every one of its declared dependencies
    appears in some wave *< N*. Within each wave, stage names are returned in
    alphabetical order so the output is deterministic across runs and Python
    hash seeds.

    Args:
        spec: A ``PipelineSpec``. Only ``spec.stages`` is read, and each stage
              need only expose ``.name`` (str) and ``.deps`` (iterable of str).

    Returns:
        A list of waves, where each wave is a sorted list of stage names.
        An empty ``spec.stages`` yields ``[]``.

    Raises:
        ValueError: ``"cycle detected in pipeline graph"`` if the dependency
                    graph contains a cycle. This is defense in depth — the
                    validator should catch cycles before execution.
        ValueError: ``"unknown dep '<d>' on stage '<s>'"`` if any stage names
                    a dependency that is not declared elsewhere in the spec.
    """
    stages = list(spec.stages)
    if not stages:
        return []

    # name -> list of deps, name -> list of dependents (forward edges)
    deps_of: Dict[str, List[str]] = {}
    dependents_of: Dict[str, List[str]] = {}
    for s in stages:
        deps_of[s.name] = list(s.deps or [])
        dependents_of[s.name] = []

    for s in stages:
        for d in deps_of[s.name]:
            if d not in deps_of:
                raise ValueError(f"unknown dep '{d}' on stage '{s.name}'")
            dependents_of[d].append(s.name)

    # Kahn-style BFS, but emitted in waves rather than a flat order.
    in_degree: Dict[str, int] = {name: len(deps_of[name]) for name in deps_of}
    frontier: deque = deque(sorted(n for n, deg in in_degree.items() if deg == 0))

    waves: List[List[str]] = []
    processed = 0
    while frontier:
        current_wave = sorted(frontier)
        frontier.clear()
        waves.append(current_wave)
        processed += len(current_wave)
        next_wave: List[str] = []
        for name in current_wave:
            for child in dependents_of[name]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    next_wave.append(child)
        frontier.extend(next_wave)

    if processed != len(stages):
        raise ValueError("cycle detected in pipeline graph")

    return waves


# ── Execution: WaveExecutor ───────────────────────────────────────────────────


# Terminal stage statuses — once a stage reports one of these the executor
# stops polling it. ``'skipped'`` is *only* written by the executor itself
# (never by an agent), so it is not part of the polling exit-set.
_TERMINAL = ("done", "failed")


class WaveExecutor:
    """
    Wave-by-wave subprocess executor for a ``PipelineSpec``.

    For each wave, one ``subprocess.Popen`` is spawned per stage with::

        runner_cmd + ["--pipeline-id", spec.pipeline_id, "--stage", name]

    The executor never calls ``proc.wait()`` directly — agent processes are
    expected to write their results into the shared checkpoint. The executor
    polls ``checkpoint.load(pipeline_id)`` every ``poll_interval`` seconds
    and considers a wave finished only when every one of its stages has
    reached a terminal status (``'done'`` or ``'failed'``).

    If any stage in a wave finishes ``'failed'``:
      * Remaining Popens in the wave are ``terminate()``-d (and
        ``wait(timeout=2)``-ed so the OS reaps them).
      * Every stage in *later* waves is marked ``'skipped'`` in the
        checkpoint so downstream consumers can tell the run was halted.
      * The wave loop breaks; ``run()`` returns immediately.

    Parameters
    ----------
    spec
        A ``PipelineSpec``-shaped object. Must expose ``pipeline_id`` (str),
        ``task`` (str), ``stages`` (iterable of stage objects with ``.name``
        and ``.deps``) and ``to_dict()``.
    poll_interval
        Seconds to sleep between checkpoint polls. Defaults to ``0.5``.
    runner_cmd
        Command (as a list of argv tokens) used to spawn each agent. The
        ``--pipeline-id`` and ``--stage`` flags are appended per-stage.
        Defaults to ``[sys.executable, "-m", "stagehand.agent_runner"]``.
    """

    def __init__(
        self,
        spec: "PipelineSpec",
        poll_interval: float = 0.5,
        runner_cmd: Optional[List[str]] = None,
    ) -> None:
        self.spec = spec
        self.poll_interval = poll_interval
        self.runner_cmd = (
            list(runner_cmd)
            if runner_cmd is not None
            else [sys.executable, "-m", "stagehand.agent_runner"]
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self) -> Dict[str, Any]:
        """Execute the pipeline wave-by-wave; return outputs of done stages."""
        waves = compute_waves(self.spec)
        all_names = [s.name for s in self.spec.stages]
        pid = self.spec.pipeline_id

        # (b) initialize checkpoint if absent — resume otherwise.
        if checkpoint.load(pid) is None:
            checkpoint.save(pid, self._initial_state(all_names))

        # (c) execute waves sequentially, stages within a wave in parallel.
        for wave_idx, wave in enumerate(waves):
            procs = self._spawn_wave(pid, wave)
            statuses = self._poll_until_terminal(pid, wave)

            failed = [n for n, s in statuses.items() if s == "failed"]
            if failed:
                self._terminate_all(procs)
                self._mark_skipped(pid, waves[wave_idx + 1 :])
                break

        # (d) return outputs only for stages that completed successfully.
        final = checkpoint.load(pid) or {"stages": {}}
        return {
            name: info.get("output")
            for name, info in final.get("stages", {}).items()
            if info.get("status") == "done"
        }

    # ── Internals ─────────────────────────────────────────────────────────────

    def _initial_state(self, names: List[str]) -> Dict[str, Any]:
        return {
            "pipeline_id": self.spec.pipeline_id,
            "task": self.spec.task,
            "spec": self.spec.to_dict(),
            "stages": {
                n: {"status": "pending", "output": None, "error": None, "attempts": 0}
                for n in names
            },
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

    def _spawn_wave(self, pid: str, wave: List[str]) -> Dict[str, "subprocess.Popen"]:
        procs: Dict[str, subprocess.Popen] = {}
        for name in wave:
            cmd = self.runner_cmd + ["--pipeline-id", pid, "--stage", name]
            procs[name] = subprocess.Popen(cmd)
        return procs

    def _poll_until_terminal(self, pid: str, wave: List[str]) -> Dict[str, str]:
        """Block (sleeping ``poll_interval``) until every stage is terminal."""
        while True:
            snapshot = checkpoint.load(pid) or {"stages": {}}
            stages = snapshot.get("stages", {}) or {}
            statuses = {
                name: (stages.get(name) or {}).get("status", "pending")
                for name in wave
            }
            if all(s in _TERMINAL for s in statuses.values()):
                return statuses
            time.sleep(self.poll_interval)

    @staticmethod
    def _terminate_all(procs: Dict[str, "subprocess.Popen"]) -> None:
        """Best-effort terminate + reap; never propagates subprocess errors."""
        for proc in procs.values():
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                pass

    @staticmethod
    def _mark_skipped(pid: str, later_waves: List[List[str]]) -> None:
        """Mark every stage in ``later_waves`` as ``'skipped'`` in the checkpoint."""
        state = checkpoint.load(pid)
        if state is None:
            return
        stages = state.setdefault("stages", {})
        for wave in later_waves:
            for name in wave:
                entry = stages.setdefault(
                    name, {"status": "pending", "output": None, "error": None, "attempts": 0}
                )
                entry["status"] = "skipped"
        checkpoint.save(pid, state)
