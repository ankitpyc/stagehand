"""
spec.py — Contract types for dynamic agent pipelines.

`PipelineSpec` and `StageSpec` are the data structures shared between the
decomposer (which generates them from a plain-English task) and the executor
(which runs them as agent subprocesses).

These types are deliberately:
- Plain dataclasses with no runtime dependencies
- JSON-roundtrip safe (`to_dict` / `from_dict`, `to_json` / `from_json`)
- Validation-free at the type level — see `validator.py` for semantic checks

The agent runner reads a `StageSpec` from the checkpoint, the decomposer writes
a `PipelineSpec` into it. Both must agree on this shape.
"""

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


# ── Stage spec ─────────────────────────────────────────────────────────────────

@dataclass
class StageSpec:
    """
    Declarative description of a single agent stage.

    name         — unique identifier within the pipeline
    prompt       — prompt template; may reference dependency outputs as `{dep_name}`
    model        — model identifier passed to the agent runner (e.g. "claude-sonnet-4-5")
    deps         — names of stages that must complete before this one runs
    capabilities — names of registry capabilities the agent is allowed to invoke
    timeout      — seconds before the agent subprocess is killed (None = no timeout)
    retry        — number of attempts before the stage is marked failed
    """

    name: str
    prompt: str
    model: str
    deps: List[str] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)
    timeout: Optional[float] = None
    retry: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StageSpec":
        if not isinstance(data, dict):
            raise TypeError(f"StageSpec.from_dict expects dict, got {type(data).__name__}")
        missing = [k for k in ("name", "prompt", "model") if k not in data]
        if missing:
            raise KeyError(f"StageSpec missing required field(s): {missing}")
        return cls(
            name=data["name"],
            prompt=data["prompt"],
            model=data["model"],
            deps=list(data.get("deps", []) or []),
            capabilities=list(data.get("capabilities", []) or []),
            timeout=data.get("timeout"),
            retry=int(data.get("retry", 1)),
        )


# ── Pipeline spec ──────────────────────────────────────────────────────────────

@dataclass
class PipelineSpec:
    """
    Declarative description of a full agent pipeline.

    pipeline_id — identifier used for checkpointing and registry entries
    task        — the original plain-English task that produced this spec
    stages      — ordered list of `StageSpec` (execution order is by deps, not list order)
    created_at  — optional ISO-8601 timestamp set by the decomposer
    """

    pipeline_id: str
    task: str
    stages: List[StageSpec] = field(default_factory=list)
    created_at: Optional[str] = None

    # ── Convenience ────────────────────────────────────────────────────────────

    def stage_names(self) -> List[str]:
        """Return the ordered list of stage names."""
        return [s.name for s in self.stages]

    def get_stage(self, name: str) -> StageSpec:
        """Return the `StageSpec` with `name`. Raises `KeyError` if absent."""
        for s in self.stages:
            if s.name == name:
                return s
        raise KeyError(f"Stage '{name}' not in pipeline spec")

    # ── Serialization ──────────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "task": self.task,
            "stages": [s.to_dict() for s in self.stages],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PipelineSpec":
        if not isinstance(data, dict):
            raise TypeError(f"PipelineSpec.from_dict expects dict, got {type(data).__name__}")
        missing = [k for k in ("pipeline_id", "task") if k not in data]
        if missing:
            raise KeyError(f"PipelineSpec missing required field(s): {missing}")
        raw_stages = data.get("stages", []) or []
        if not isinstance(raw_stages, list):
            raise TypeError(f"PipelineSpec 'stages' must be a list, got {type(raw_stages).__name__}")
        return cls(
            pipeline_id=data["pipeline_id"],
            task=data["task"],
            stages=[StageSpec.from_dict(s) for s in raw_stages],
            created_at=data.get("created_at"),
        )

    def to_json(self, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_json(cls, payload: str) -> "PipelineSpec":
        return cls.from_dict(json.loads(payload))
