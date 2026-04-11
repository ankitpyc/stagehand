# Context for Issue #0: Fix dashboard.py bug: replace ckt_list_all() and ckt.list_runs() with ckpt equivalents

## Issue

**Title:** Fix dashboard.py bug: replace ckt_list_all() and ckt.list_runs() with ckpt equivalents
**Labels:** P0
**Body:**


## Relevant Memories

- [2026-04-11_0201_are-we-doing-wrong-with-claude-i-see-performance-n.md] only whats broken do not refactor or add features ## Topics - are we doing wrong with claude i see performance not so go...
- [engineering-patterns.md] ## Architecture Decision Patterns  ### When to Use What - **Single HTML file**: Prototypes, one-off tools, dashboards <5...
- [visual-variety.md] ## Slide Type Selection Guide  Don't just use `content` for everything. Match slide type to what you're communicating:  ...
- [2026-04-08_1819_ideopenedfilethe-user-opened-the-file-te.md] ideopenedfilethe user opened the file te ## Topics - <ide_opened_file>The user opened the file /temp/readonly/Bash tool ...
- [2026-04-08_1832_ideopenedfilethe-user-opened-the-file-te.md] ideopenedfilethe user opened the file te ## Topics - <ide_opened_file>The user opened the file /temp/readonly/Bash tool ...

## Codebase

### Structure
```
src/stagehand/__init__.py
src/stagehand/checkpoint.py
src/stagehand/cli.py
src/stagehand/dashboard.py
src/stagehand/pipeline.py
src/stagehand/providers/__init__.py
src/stagehand/providers/claude.py
src/stagehand/providers/gemini.py
src/stagehand/providers/http.py
src/stagehand/providers/openai.py
src/stagehand/registry.py
tests/__init__.py
tests/test_checkpoint.py
tests/test_pipeline.py
tests/test_providers.py
```

### README (summary)

# Stagehand

> Lightweight Python pipeline runner with agent-per-stage execution, dynamic task decomposition, and first-class AI support.

![Status](https://img.shields.io/badge/status-active-brightgreen)
![Python](https://img.shields.io/badge/python-3.10+-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![GitHub last commit](https://img.shields.io/github/last-commit/ankitpyc/stagehand)

## Overview

Stagehand is a minimal Python pipeline framework for multi-step workflows. It gives you checkpointed, resumable pipelines with zero external dependencies — and optionally lets Claude dynamically decompose any plain-English task into an executable pipeline, running each stage as an isolated AI agent subprocess.

It's the missing layer between "run a script" and "deploy Airflow."

## Features

- **Checkpointing** — every stage saves its output; crashes resume from the last completed stage
- **Agent-per-stage** — each LLM stage runs as an isolated subprocess with its own full context window
- **Dynamic decomposition** — Claude breaks down a plain-English task into a validated pipeline spec automatically
- **Self-correction** — invalid specs are corrected before execution (up to 3 attempts)
- **Capability registry** — extensible catalog of what Claude can use in stages; grows with your stack
- **Multi-provider** — `claude_stage`, `openai_stage`, `gemini_stage`, `http_stage` built in
- **Zero runtime deps** — stdlib only for the core engine
- **CLI** — `stagehand list`, `stagehand status`, `stagehand reset`, `stagehand runs`

## Quick Start

### Static pipeline (known workflow)


### Related Files

**src/stagehand/dashboard.py** (matches: dashboard, replace, ckt_list_all, ckt, list_runs)
```py
"""
dashboard.py — Visual pipeline dashboard.

Serves a self-contained HTML page that renders pipeline DAGs
from ~/.stagehand checkpoint and registry data.

Usage:
    stagehand dashboard              # open on port 7400
    stagehand dashboard --port 8080  # custom port
"""

import json
import http.server
import os
import threading
import webbrowser
from pathlib import Path

from . import checkpoint as ckpt
from . import registry as reg


def get_dashboard_data() -> dict:
    """Collect all pipeline data for the dashboard."""
    active = ckt_list_all()
    registry = reg.load()
    runs_data = {}

    # Collect run history for each known pipeline
    known_ids = set()
    for a in active:
        known_ids.add(a["pipeline_id"])
    for pid in registry.get("pipelines", {}):
        known_ids.add(pid)

    for pid in known_ids:
        runs = ckt.list_runs(pid)
        if runs:
            runs_data[pid] = runs[:10]

    return {
        "active": active,
        "registry": registry.get("pipelines", {}),
        "runs": runs_data,
    }


def ckt_list_all():
    """Extended list_active that includes DAG structure."""
    active_dir = Path(os.environ.get("STAGEHAND_DIR", "~/.stagehand")).expanduser() / "active"
```

**src/stagehand/cli.py** (matches: dashboard, list_runs, ckpt)
```py
"""
cli.py — `stagehand` command-line interface.

Commands:
    stagehand list                       List all active checkpoints
    stagehand status <pipeline_id>       Show stage-by-stage status
    stagehand reset  <pipeline_id>       Clear checkpoint (start fresh on next run)
    stagehand runs   <pipeline_id>       Show run history
    stagehand dashboard [--port N]       Visual pipeline dashboard
    stagehand version                    Print version
"""

import sys
from . import __version__
from . import checkpoint as ckpt
from .pipeline import Pipeline


def main():
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        _print_help()
        return

    cmd = args[0]

    if cmd == "version":
        print(f"stagehand {__version__}")

    elif cmd == "list":
        active = ckpt.list_active()
        if not active:
            print("No active checkpoints.")
            return
        _header("PIPELINE", "STARTED", "DONE", "FAILED", "PENDING")
        for r in active:
            _row(r["pipeline_id"][:50], r["started_at"][:16], r["done"], r["failed"], r["pending"])

    elif cmd == "status":
        if len(args) < 2:
            print("Usage: stagehand status <pipeline_id>", file=sys.stderr)
            sys.exit(1)
        p = Pipeline(args[1])
        p.status()

    elif cmd == "reset":
        if len(args) < 2:
            print("Usage: stagehand reset <pipeline_id>", file=sys.stderr)
            sys.exit(1)
```

**src/stagehand/checkpoint.py** (matches: replace, list_runs)
```py
"""
checkpoint.py — Atomic checkpoint read/write with file locking and run history.

Design:
- Active checkpoint: ~/.stagehand/active/<pipeline_id>.json
- Run history:       ~/.stagehand/runs/<pipeline_id>/<timestamp>.json
- Lock file:         ~/.stagehand/active/<pipeline_id>.lock
- All writes are atomic: write to .tmp, then os.replace() (POSIX rename)
- File lock held for the duration of each read-modify-write cycle
"""

import fcntl
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _checkpoint_dir() -> Path:
    base = os.environ.get("STAGEHAND_DIR", "~/.stagehand")
    return Path(base).expanduser()


def _active_path(pipeline_id: str) -> Path:
    safe = _safe_id(pipeline_id)
    return _checkpoint_dir() / "active" / f"{safe}.json"


def _lock_path(pipeline_id: str) -> Path:
    safe = _safe_id(pipeline_id)
    return _checkpoint_dir() / "active" / f"{safe}.lock"


def _runs_dir(pipeline_id: str) -> Path:
    safe = _safe_id(pipeline_id)
    return _checkpoint_dir() / "runs" / safe


def _safe_id(pipeline_id: str) -> str:
    return pipeline_id.replace("/", "_").replace(" ", "_").strip("_")


# ── File locking ───────────────────────────────────────────────────────────────

@contextmanager
def _lock(pipeline_id: str):
    """Acquire an exclusive file lock for this pipeline_id."""
```

**tests/test_checkpoint.py** (matches: list_runs, ckpt)
```py
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
```

**tests/test_pipeline.py** (matches: list_runs, ckpt)
```py
"""Tests for pipeline.py — execution order, checkpointing, retry, parallel stages."""

import os
import time
import threading
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
```

**src/stagehand/registry.py** (matches: replace)
```py
"""
registry.py — Central pipeline registry.

Auto-updated by Pipeline.run() after each execution.
Dashboard reads this to display pipeline status.

Location: ~/.stagehand/registry.json
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


MAX_ENTRIES = 50


def _registry_path() -> Path:
    base = os.environ.get("STAGEHAND_DIR", "~/.stagehand")
    return Path(base).expanduser() / "registry.json"


def load() -> Dict[str, Any]:
    """Load the full registry. Returns {"pipelines": {...}} or empty."""
    path = _registry_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {"pipelines": {}}


def update(
    pipeline_id: str,
    status: str,
    stages: Dict[str, str],
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
    script: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """
    Upsert a pipeline entry in the registry.

    stages: dict of {stage_name: status_string} e.g. {"fetch": "done", "deliver": "failed"}
    """
    path = _registry_path()
```

**src/stagehand/providers/http.py** (matches: replace)
```py
"""
http_stage — Make an HTTP request as a pipeline stage.

Zero external dependencies — uses urllib.request from stdlib.
Returns parsed JSON (dict/list) if response is JSON, raw text otherwise.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional


def http_stage(
    method: str,
    url_template: str,
    headers: Dict[str, str] = None,
    body_template: str = None,
    timeout: int = 30,
    expect_json: bool = True,
) -> Callable[[Dict], Any]:
    """
    Returns a stage function that makes an HTTP request.

    Args:
        method:          HTTP method (GET, POST, PATCH, DELETE).
        url_template:    URL with optional {ctx_key} placeholders.
        headers:         Static headers dict (e.g. Authorization).
        body_template:   Request body template string (for POST/PATCH).
        timeout:         Seconds before the request is abandoned.
        expect_json:     If True, parse response as JSON. Otherwise return raw text.

    Example:
        p.stage("get_user",
            http_stage("GET", "https://api.example.com/users/{user_id}",
                       headers={"Authorization": "Bearer mytoken"}),
            deps=[])

        p.stage("create_post",
            http_stage("POST", "https://api.example.com/posts",
                       body_template='{{"title": "{title}", "body": "{draft}"}}'),
            deps=["draft"])
    """
    def fn(ctx: Dict) -> Any:
        url = _render(url_template, ctx)
        body = _render(body_template, ctx).encode("utf-8") if body_template else None

        req = urllib.request.Request(url, data=body, method=method.upper())
```

**src/stagehand/pipeline.py** (matches: ckpt)
```py
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
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
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
    retry_backoff: float = 2.0   # seconds; doubles on each retry
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
```

## Libraries to Research

Use Context7 to look up current docs for:
- pipeline
- Development
- Intended
- License
- Programming
