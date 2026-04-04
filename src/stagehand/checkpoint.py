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
    lock_path = _lock_path(pipeline_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


# ── Atomic write ───────────────────────────────────────────────────────────────

def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON atomically: write to .tmp then os.replace() (POSIX rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)  # atomic on Linux/Mac (POSIX rename)


# ── Public API ─────────────────────────────────────────────────────────────────

def load(pipeline_id: str) -> Optional[dict]:
    """Load checkpoint for pipeline_id. Returns None if no checkpoint exists."""
    path = _active_path(pipeline_id)
    if not path.exists():
        return None
    try:
        with _lock(pipeline_id):
            return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save(pipeline_id: str, state: dict) -> None:
    """Save checkpoint atomically under an exclusive lock."""
    with _lock(pipeline_id):
        _atomic_write(_active_path(pipeline_id), state)


def clear(pipeline_id: str) -> None:
    """Delete active checkpoint (called on pipeline success)."""
    path = _active_path(pipeline_id)
    with _lock(pipeline_id):
        if path.exists():
            path.unlink()


def archive(pipeline_id: str, state: dict) -> Path:
    """
    Copy final state into run history with a timestamp.
    Called on both success and failure so runs are auditable.
    Returns path to the archived file.
    """
    runs_dir = _runs_dir(pipeline_id)
    runs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = runs_dir / f"{ts}.json"
    _atomic_write(out, {**state, "archived_at": ts})
    return out


def list_active() -> list[dict]:
    """Return summary of all active (incomplete) checkpoints."""
    active_dir = _checkpoint_dir() / "active"
    if not active_dir.exists():
        return []
    results = []
    for f in sorted(active_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            stages = data.get("stages", {})
            results.append({
                "pipeline_id": data.get("pipeline_id", f.stem),
                "started_at": data.get("started_at", "?"),
                "total": len(stages),
                "done": sum(1 for s in stages.values() if s["status"] == "done"),
                "failed": sum(1 for s in stages.values() if s["status"] == "failed"),
                "pending": sum(1 for s in stages.values() if s["status"] == "pending"),
            })
        except Exception:
            pass
    return results


def list_runs(pipeline_id: str) -> list[dict]:
    """Return run history summaries for a pipeline."""
    runs_dir = _runs_dir(pipeline_id)
    if not runs_dir.exists():
        return []
    results = []
    for f in sorted(runs_dir.glob("*.json"), reverse=True)[:20]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            stages = data.get("stages", {})
            failed = [n for n, s in stages.items() if s["status"] == "failed"]
            results.append({
                "run": f.stem,
                "started_at": data.get("started_at", "?"),
                "archived_at": data.get("archived_at", "?"),
                "done": sum(1 for s in stages.values() if s["status"] == "done"),
                "total": len(stages),
                "failed_stages": failed,
            })
        except Exception:
            pass
    return results
