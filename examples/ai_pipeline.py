"""
ai_pipeline.py — End-to-end demo of an AI-powered pipeline.

Mixes regular Python stages with `claude_stage` LLM stages. Each LLM stage is
a simple function call from the pipeline's perspective — the Claude call is
rendered from the context dict using str.format_map.

Demonstrates:
  - claude_stage factory: plug an LLM into any stage
  - Prompt templating: reference upstream outputs as {stage_name}
  - Critique + revise pattern: a reviewer stage feeds back into a refine stage
  - Mixed human / AI stages in one DAG

Prerequisites (pick one):
  1. pip install stagehand[claude]  +  export ANTHROPIC_API_KEY=sk-ant-...
  2. Run inside Claude Code — the CLI backend kicks in automatically

Run it:
    python examples/ai_pipeline.py "LangGraph vs CrewAI"

If no topic is supplied on the CLI, a default is used.
"""

from __future__ import annotations

import sys
from typing import Dict

from stagehand import Pipeline, claude_stage


# ── Non-AI stages ──────────────────────────────────────────────────────────────

def pick_topic(ctx: Dict) -> str:
    """
    Read the topic from the initial pipeline context.
    Keeping this as a separate stage lets downstream stages reference {topic}
    in their prompt templates, and makes the topic visible in checkpoints.
    """
    topic = ctx.get("topic")
    if not topic:
        raise ValueError("Pipeline context must include a 'topic' key")
    return topic


def format_final(ctx: Dict) -> str:
    """
    Assemble the final post from the revised draft. Pure Python — no LLM.
    Keeps presentation logic out of the prompt.
    """
    banner = "━" * 64
    return (
        f"{banner}\n"
        f"Topic: {ctx['topic']}\n"
        f"{banner}\n\n"
        f"{ctx['revise']}\n"
    )


# ── Pipeline builder ───────────────────────────────────────────────────────────

def build_pipeline(topic: str, pipeline_id: str = "ai-demo") -> Pipeline:
    """
    DAG:

        topic ──► research ──► draft ──► critique ──► revise ──► final
                                   └──────────────────┘
                             (critique reads {draft} too)

    Each claude_stage is an independent LLM call. Outputs flow through the
    checkpoint, so a crash mid-pipeline resumes from the last completed stage.
    """
    p = Pipeline(pipeline_id)

    p.stage("topic", pick_topic)

    p.stage(
        "research",
        claude_stage(
            "Research this topic and list the 5 most important facts a "
            "technical reader must know. Output a plain bulleted list.\n\n"
            "Topic: {topic}"
        ),
        deps=["topic"],
        retry=2,
        timeout=180,
    )

    p.stage(
        "draft",
        claude_stage(
            "Write a 300-word Substack post for developers about "
            "{topic}.\n\nGround it in these facts:\n\n{research}\n\n"
            "Voice: confident, specific, no fluff. No preamble."
        ),
        deps=["research", "topic"],
        retry=2,
        timeout=240,
    )

    p.stage(
        "critique",
        claude_stage(
            "You are a harsh technical editor. Critique this draft in 5 "
            "bullets — call out vague claims, missing examples, and weak "
            "hooks. Do not rewrite.\n\n---\n{draft}"
        ),
        deps=["draft"],
        timeout=180,
    )

    p.stage(
        "revise",
        claude_stage(
            "Rewrite the draft below, incorporating every critique bullet. "
            "Preserve the word count (~300). Output only the revised post.\n\n"
            "## Draft\n{draft}\n\n## Critique\n{critique}"
        ),
        deps=["draft", "critique"],
        retry=2,
        timeout=240,
    )

    p.stage("final", format_final, deps=["revise", "topic"])

    return p


# ── Entrypoint ─────────────────────────────────────────────────────────────────

def main() -> None:
    topic = " ".join(sys.argv[1:]) or "Why agent-per-stage pipelines beat monolithic prompts"
    p = build_pipeline(topic)
    outputs = p.run(context={"topic": topic})

    if "final" in outputs:
        print()
        print(outputs["final"])
    else:
        print("\nPipeline did not complete. Run `stagehand status ai-demo` "
              "to inspect the checkpoint, then re-run to resume.")


if __name__ == "__main__":
    main()
