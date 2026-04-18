"""
basic_pipeline.py — End-to-end demo of the core Pipeline API.

No API keys required. Uses only stdlib + stagehand.

Demonstrates:
  - Stage registration with dependencies (DAG)
  - Output chaining via the context dict
  - Parallel execution of independent stages
  - Retry with backoff
  - Checkpoint-based resume (re-run to see it skip completed stages)

Run it:
    python examples/basic_pipeline.py

Re-run after the first success — every stage will be skipped because the
pipeline_id is stable. Delete ~/.stagehand/active/<id>.json or call
`pipeline.reset()` to start fresh.
"""

from __future__ import annotations

import random
import time
from typing import Dict, List

from stagehand import Pipeline


# ── Stage functions ────────────────────────────────────────────────────────────

def fetch_users(ctx: Dict) -> List[Dict]:
    """Simulate an API fetch. No network — deterministic for demo purposes."""
    return [
        {"id": 1, "name": "Ada",   "age": 36, "status": "active"},
        {"id": 2, "name": "Grace", "age": 42, "status": "active"},
        {"id": 3, "name": "Linus", "age": 54, "status": "inactive"},
        {"id": 4, "name": "Rob",   "age": 29, "status": "active"},
    ]


def enrich_users(ctx: Dict) -> List[Dict]:
    """Add computed fields. Depends on fetch_users — reads ctx['fetch']."""
    users = ctx["fetch"]
    return [
        {**u, "bucket": "senior" if u["age"] >= 40 else "junior"}
        for u in users
    ]


def count_by_status(ctx: Dict) -> Dict[str, int]:
    """Aggregate: count users per status. Runs in parallel with average_age."""
    counts: Dict[str, int] = {}
    for u in ctx["enrich"]:
        counts[u["status"]] = counts.get(u["status"], 0) + 1
    return counts


def average_age(ctx: Dict) -> float:
    """Aggregate: mean age. Runs in parallel with count_by_status."""
    users = ctx["enrich"]
    return round(sum(u["age"] for u in users) / len(users), 2)


def flaky_deliver(ctx: Dict) -> str:
    """
    Simulate a flaky downstream (e.g. a webhook that occasionally fails).
    With retry=3, the pipeline will retry on failure with exponential backoff.
    """
    if random.random() < 0.4:
        raise RuntimeError("downstream 503 — try again")
    return (
        f"Report: {ctx['count_by_status']} · "
        f"avg age {ctx['average_age']}"
    )


# ── Pipeline builder ───────────────────────────────────────────────────────────

def build_pipeline(pipeline_id: str = "basic-demo") -> Pipeline:
    """
    Wire up the DAG:

        fetch ── enrich ─┬─ count_by_status ─┐
                         │                   ├─ deliver
                         └─ average_age ─────┘
    """
    p = Pipeline(pipeline_id)
    p.stage("fetch",           fetch_users)
    p.stage("enrich",          enrich_users,    deps=["fetch"])
    p.stage("count_by_status", count_by_status, deps=["enrich"])
    p.stage("average_age",     average_age,     deps=["enrich"])
    p.stage(
        "deliver",
        flaky_deliver,
        deps=["count_by_status", "average_age"],
        retry=3,
        retry_backoff=0.5,
    )
    return p


# ── Entrypoint ─────────────────────────────────────────────────────────────────

def main() -> None:
    random.seed(int(time.time()))  # lets the flaky stage succeed eventually
    p = build_pipeline()
    outputs = p.run()

    print()
    print("─── Pipeline outputs " + "─" * 40)
    for stage, output in outputs.items():
        print(f"  {stage:18}  {output}")
    print()
    print("Tip: re-run this script — completed stages will be skipped")
    print("     until you call p.reset() or delete the checkpoint file.")


if __name__ == "__main__":
    main()
