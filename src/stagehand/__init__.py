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
from .providers import claude_stage, openai_stage, gemini_stage, http_stage

__version__ = "0.1.0"
__all__ = [
    "Pipeline",
    "Stage",
    "claude_stage",
    "openai_stage",
    "gemini_stage",
    "http_stage",
]
