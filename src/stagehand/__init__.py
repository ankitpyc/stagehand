"""
Stagehand — Minimal Python pipeline runner with checkpointing and AI support.

The pipeline never loses your work. Every stage is checkpointed after success.
Re-run after failure to resume from where it stopped.

Quick start:
    from stagehand import Pipeline
    from stagehand.providers import claude_stage

    p = Pipeline("my-pipeline-2026-03-14")
    p.stage("fetch",     fetch_fn)
    p.stage("analyze",   claude_stage("Analyze: {fetch}"), deps=["fetch"])
    p.stage("deliver",   deliver_fn, deps=["analyze"], retry=2)
    outputs = p.run(context={"dry_run": True})
"""

from .pipeline import Pipeline, Stage
from .providers import claude_stage, gemini_stage, http_stage, openai_stage

__version__ = "0.3.0"
__all__ = [
    "Pipeline",
    "Stage",
    "claude_stage",
    "openai_stage",
    "gemini_stage",
    "http_stage",
]

# ── Dynamic agent-pipeline API ─────────────────────────────────────────────────
# AgentPipeline depends on the decomposer/wave_executor/capabilities/spec
# subsystems. They're guarded with try/except so missing optional pieces
# don't break the core ``import stagehand`` path; once all parts are merged
# the imports succeed and the symbols become available.
try:
    from .agent_pipeline import AgentPipeline
    from .capabilities import CapabilityRegistry
except ImportError:  # pragma: no cover - optional subsystems not yet merged
    pass
else:
    __all__ += ["AgentPipeline", "CapabilityRegistry"]
