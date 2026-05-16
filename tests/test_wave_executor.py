"""Tests for wave_executor.compute_waves — wave grouping, determinism, errors."""

from types import SimpleNamespace
from typing import Iterable, List, Optional

import pytest

from stagehand.wave_executor import compute_waves


# ── Test helpers ───────────────────────────────────────────────────────────────
#
# `compute_waves` only reads `spec.stages`, and from each stage only `.name`
# and `.deps`. Using `SimpleNamespace` stubs keeps these tests focused on the
# planning algorithm and independent of `PipelineSpec`'s wider surface area.


def _stage(name: str, deps: Optional[Iterable[str]] = None) -> SimpleNamespace:
    return SimpleNamespace(name=name, deps=list(deps or []))


def _spec(stages: List[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(stages=stages)


# ── Topology cases ─────────────────────────────────────────────────────────────


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


# ── Determinism ────────────────────────────────────────────────────────────────


def test_within_wave_names_are_sorted_alphabetically():
    # Stage declaration order shouldn't influence the per-wave ordering.
    spec = _spec([
        _stage("zebra"),
        _stage("apple"),
        _stage("mango"),
    ])
    assert compute_waves(spec) == [["apple", "mango", "zebra"]]


# ── Error paths ────────────────────────────────────────────────────────────────


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
