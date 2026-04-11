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
    path.parent.mkdir(parents=True, exist_ok=True)

    registry = load()
    pipelines = registry.get("pipelines", {})

    pipelines[pipeline_id] = {
        "name": pipeline_id,
        "script": script,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at or datetime.now(timezone.utc).isoformat(),
        "stages": stages,
        "error": error,
    }

    # Trim to MAX_ENTRIES — keep the most recent by finished_at
    if len(pipelines) > MAX_ENTRIES:
        sorted_ids = sorted(
            pipelines.keys(),
            key=lambda k: pipelines[k].get("finished_at") or "",
            reverse=True,
        )
        pipelines = {k: pipelines[k] for k in sorted_ids[:MAX_ENTRIES]}

    registry["pipelines"] = pipelines

    # Atomic write
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(registry, indent=2, default=str))
    os.replace(tmp, path)
