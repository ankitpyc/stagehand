"""
wave_executor.py — Pure planning function for grouping pipeline stages into
execution waves.

`compute_waves(spec)` returns the stages of a `PipelineSpec` grouped into
"waves": each wave is a list of stage names whose dependencies all lie in
strictly earlier waves. Stages within a wave can be executed concurrently;
waves themselves run sequentially.

The grouping uses Kahn-style topological BFS. Output is deterministic — names
within a wave are sorted alphabetically.

This is the *planning* step that the `WaveExecutor` (Part 2) consumes. It
performs no I/O, spawns no subprocesses, and has no side effects.
"""

from collections import deque
from typing import TYPE_CHECKING, Dict, List

if TYPE_CHECKING:  # pragma: no cover - type-only import
    from stagehand.spec import PipelineSpec


def compute_waves(spec: "PipelineSpec") -> List[List[str]]:
    """
    Group stages into execution waves via Kahn-style topological BFS.

    A stage is placed in wave *N* iff every one of its declared dependencies
    appears in some wave *< N*. Within each wave, stage names are returned in
    alphabetical order so the output is deterministic across runs and Python
    hash seeds.

    Args:
        spec: A `PipelineSpec`. Only `spec.stages` is read, and each stage
              need only expose `.name` (str) and `.deps` (iterable of str).

    Returns:
        A list of waves, where each wave is a sorted list of stage names.
        An empty `spec.stages` yields ``[]``.

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
