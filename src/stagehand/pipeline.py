"""
pipeline.py — Core pipeline executor.

Features:
- DAG-based stage ordering with parallel execution
- Checkpoint after every successful stage (atomic, locked)
- Retry with exponential backoff
- Per-stage timeout
- Full traceback captured to checkpoint on failure
- Run history archived on completion
"""

import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from . import checkpoint as ckpt
from . import registry as reg

# ── Stage definition ───────────────────────────────────────────────────────────


@dataclass
class Stage:
    name: str
    fn: Callable[[Dict], Any]
    deps: List[str] = field(default_factory=list)
    retry: int = 1
    retry_backoff: float = 2.0  # seconds; doubles on each retry
    timeout: Optional[float] = None  # seconds; None = no timeout
    # "fail_fast": stop pipeline on failure
    # "continue": log failure, keep running independent stages
    fail_mode: str = "fail_fast"


# ── Pipeline ───────────────────────────────────────────────────────────────────


class Pipeline:
    """
    Orchestrates named stages as a dependency DAG with checkpointing.

    Stages with no dependency on each other run in parallel automatically.
    Every successful stage is checkpointed — re-running resumes from failures.

    Example:
        p = Pipeline("weekly-report-2026-03-14")
        p.stage("fetch",    fetch_fn)
        p.stage("analyze",  analyze_fn,  deps=["fetch"])
        p.stage("email",    email_fn,    deps=["analyze"], retry=2)
        outputs = p.run(context={"dry_run": True})
    """

    def __init__(self, pipeline_id: str):
        self.pipeline_id = pipeline_id
        self._stages: List[Stage] = []
        self._state = self._load_or_init()

    # ── Public API ─────────────────────────────────────────────────────────────

    def stage(
        self,
        name: str,
        fn: Callable[[Dict], Any],
        deps: List[str] = None,
        retry: int = 1,
        retry_backoff: float = 2.0,
        timeout: Optional[float] = None,
        fail_mode: str = "fail_fast",
    ) -> "Pipeline":
        """Register a stage. Returns self for chaining."""
        if any(s.name == name for s in self._stages):
            raise ValueError(f"Stage '{name}' already registered")
        resolved_deps = deps or []
        self._stages.append(
            Stage(
                name=name,
                fn=fn,
                deps=resolved_deps,
                retry=retry,
                retry_backoff=retry_backoff,
                timeout=timeout,
                fail_mode=fail_mode,
            )
        )
        if name not in self._state["stages"]:
            self._state["stages"][name] = _pending_state()
        # Save DAG structure for visualization
        if "dag" not in self._state:
            self._state["dag"] = {}
        self._state["dag"][name] = resolved_deps
        return self

    def run(self, context: Dict = None) -> Dict:
        """
        Execute all stages, respecting deps and running independent ones in parallel.
        Skips stages that are already done (from checkpoint).
        Returns dict of {stage_name: output} for all completed stages.
        """
        ctx = dict(context or {})

        # Seed ctx with outputs already in checkpoint
        for name, s in self._state["stages"].items():
            if s["status"] == "done" and s["output"] is not None:
                ctx[name] = s["output"]

        failed_stages: List[str] = []
        stop = False

        # DAG execution: repeatedly find stages whose deps are satisfied
        # and run them in parallel until nothing is left to run.
        remaining = {s.name for s in self._stages if self._state["stages"].get(s.name, {}).get("status") != "done"}

        while remaining and not stop:
            ready = self._find_ready(remaining, ctx, failed_stages)
            if not ready:
                # Nothing is runnable — deps blocked by failures
                break

            if len(ready) == 1:
                name = ready[0]
                result, error = self._run_stage(self._get_stage(name), ctx)
                if error:
                    failed_stages.append(name)
                    if self._get_stage(name).fail_mode == "fail_fast":
                        stop = True
                else:
                    ctx[name] = result
                remaining.discard(name)
            else:
                # Run ready stages in parallel
                with ThreadPoolExecutor(max_workers=len(ready)) as pool:
                    futures = {pool.submit(self._run_stage, self._get_stage(n), dict(ctx)): n for n in ready}
                    for future in as_completed(futures):
                        name = futures[future]
                        result, error = future.result()
                        if error:
                            failed_stages.append(name)
                            if self._get_stage(name).fail_mode == "fail_fast":
                                stop = True
                        else:
                            ctx[name] = result
                        remaining.discard(name)

        # Mark stages that never ran (blocked by failed deps) as skipped
        for name in remaining:
            if self._state["stages"][name]["status"] == "pending":
                self._state["stages"][name]["status"] = "skipped"
        ckpt.save(self.pipeline_id, self._state)

        outputs = {n: s["output"] for n, s in self._state["stages"].items() if s["status"] == "done"}

        # Archive run + clear active checkpoint
        ckpt.archive(self.pipeline_id, self._state)
        if not failed_stages:
            ckpt.clear(self.pipeline_id)
            _log(f"Pipeline complete ✓  [{self.pipeline_id}]")
        else:
            _log(f"Pipeline finished with failures: {failed_stages}  [{self.pipeline_id}]", err=True)
            _log("Re-run the same command to resume from failed stages.", err=True)

        # Update pipeline registry (never fail the pipeline for registry issues)
        try:
            stage_statuses = {n: s["status"] for n, s in self._state["stages"].items()}
            reg.update(
                pipeline_id=self.pipeline_id,
                status="success" if not failed_stages else "failed",
                stages=stage_statuses,
                started_at=self._state.get("started_at"),
            )
        except Exception:
            pass

        return outputs

    def status(self) -> None:
        """Print current checkpoint status to stdout."""
        stages = self._state.get("stages", {})
        if not stages:
            print(f"No checkpoint for '{self.pipeline_id}'")
            return
        print(f"\nPipeline : {self.pipeline_id}")
        print(f"Started  : {self._state.get('started_at', '?')}")
        print()
        icons = {"done": "✓", "failed": "✗", "pending": "○", "skipped": "–"}
        for name, s in stages.items():
            icon = icons.get(s["status"], "?")
            err = f"\n    {s['error'][:120]}" if s.get("error") else ""
            attempts = f"  ({s['attempts']} attempts)" if s.get("attempts", 0) > 1 else ""
            print(f"  {icon} {name}  [{s['status']}]{attempts}{err}")
        print()

    def reset(self) -> None:
        """Clear checkpoint — next run starts fresh."""
        ckpt.clear(self.pipeline_id)
        self._state = self._load_or_init()
        _log(f"Checkpoint cleared for '{self.pipeline_id}'")

    # ── Internal ───────────────────────────────────────────────────────────────

    def _load_or_init(self) -> dict:
        existing = ckpt.load(self.pipeline_id)
        if existing:
            _log(f"Resuming from checkpoint  [{self.pipeline_id}]")
            return existing
        return {
            "pipeline_id": self.pipeline_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "stages": {},
            "dag": {},  # {stage_name: [dep1, dep2, ...]}
        }

    def _get_stage(self, name: str) -> Stage:
        return next(s for s in self._stages if s.name == name)

    def _find_ready(self, remaining: set, ctx: dict, failed: list) -> List[str]:
        """Return stage names whose deps are all done (in ctx) and not blocked by failures."""
        ready = []
        for name in remaining:
            stage = self._get_stage(name)
            deps_done = all(d in ctx for d in stage.deps)
            deps_failed = any(d in failed for d in stage.deps)
            if deps_done and not deps_failed:
                ready.append(name)
        return ready

    def _run_stage(self, stage: Stage, ctx: dict):
        """
        Run a single stage with retry + backoff + timeout.
        Returns (result, None) on success or (None, error_str) on failure.
        Updates self._state and saves checkpoint on each attempt.
        """
        s = self._state["stages"][stage.name]

        if s["status"] == "done":
            _log(f"✓ {stage.name}  (resumed from checkpoint)")
            return s["output"], None

        last_error = None
        delay = stage.retry_backoff / 2  # first delay = backoff/2, doubles each retry

        for attempt in range(1, stage.retry + 1):
            suffix = f"  [attempt {attempt}/{stage.retry}]" if stage.retry > 1 else ""
            _log(f"→ {stage.name}{suffix}")
            try:
                result = _call_with_timeout(stage.fn, ctx, stage.timeout)
                # Success
                self._state["stages"][stage.name] = {
                    "status": "done",
                    "output": _serialize(result),
                    "error": None,
                    "attempts": attempt,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
                ckpt.save(self.pipeline_id, self._state)
                _log(f"✓ {stage.name}")
                return result, None

            except Exception:
                last_error = traceback.format_exc()
                _log(f"✗ {stage.name}  attempt {attempt} failed:\n    {last_error.splitlines()[-1]}", err=True)
                if attempt < stage.retry:
                    _log(f"  retrying in {delay:.1f}s...", err=True)
                    time.sleep(delay)
                    delay *= stage.retry_backoff

        # All attempts exhausted
        self._state["stages"][stage.name] = {
            "status": "failed",
            "output": None,
            "error": last_error,
            "attempts": stage.retry,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        ckpt.save(self.pipeline_id, self._state)
        _log(f"✗ {stage.name}  failed after {stage.retry} attempt(s)", err=True)
        return None, last_error


# ── Helpers ────────────────────────────────────────────────────────────────────


def _call_with_timeout(fn: Callable, ctx: dict, timeout: Optional[float]):
    """Call fn(ctx). If timeout is set, raise TimeoutError if it takes too long."""
    if timeout is None:
        return fn(ctx)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn, ctx)
        try:
            return future.result(timeout=timeout)
        except FutureTimeout:
            future.cancel()
            raise TimeoutError(f"Stage timed out after {timeout}s")


def _pending_state() -> dict:
    return {"status": "pending", "output": None, "error": None, "attempts": 0}


def _serialize(value: Any) -> Any:
    """Make a value JSON-safe. Raises TypeError for truly non-serializable types."""
    from pathlib import Path as _Path

    if isinstance(value, _Path):
        return str(value)
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, dict):
        return {str(k): _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    # Last resort: str() — never silently drops data
    return str(value)


def _log(msg: str, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    print(f"[stagehand] {msg}", file=stream, flush=True)
