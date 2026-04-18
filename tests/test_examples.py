"""
Tests for the example scripts in examples/.

The examples are shipped as the canonical "it works" demos, so these tests
exercise their pipeline structure and end-to-end execution. The AI example's
claude_stage calls are mocked — we don't want tests hitting real APIs — but
the DAG, prompt rendering, and output chaining are all real.
"""

import importlib.util
import os
import sys
import uuid
from pathlib import Path

import pytest


EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


# ── Dynamic import helper (examples/ is not a package) ─────────────────────────

def _load_example(name: str):
    """Import an example module by file path and cache it in sys.modules."""
    path = EXAMPLES_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"stagehand_examples.{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def isolated_checkpoint_dir(tmp_path):
    """Each test gets its own STAGEHAND_DIR so checkpoints don't leak."""
    prev = os.environ.get("STAGEHAND_DIR")
    os.environ["STAGEHAND_DIR"] = str(tmp_path / "stagehand")
    yield
    if prev is None:
        os.environ.pop("STAGEHAND_DIR", None)
    else:
        os.environ["STAGEHAND_DIR"] = prev


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ── basic_pipeline.py ──────────────────────────────────────────────────────────

class TestBasicPipelineExample:
    def test_module_imports(self):
        mod = _load_example("basic_pipeline")
        assert hasattr(mod, "build_pipeline")
        assert hasattr(mod, "main")

    def test_build_pipeline_wires_expected_dag(self):
        mod = _load_example("basic_pipeline")
        p = mod.build_pipeline(pipeline_id=_uid("basic"))
        dag = p._state["dag"]
        assert dag["fetch"] == []
        assert dag["enrich"] == ["fetch"]
        assert dag["count_by_status"] == ["enrich"]
        assert dag["average_age"] == ["enrich"]
        # deliver must depend on both aggregates
        assert set(dag["deliver"]) == {"count_by_status", "average_age"}

    def test_pipeline_runs_end_to_end(self, monkeypatch):
        mod = _load_example("basic_pipeline")
        # Deterministic: force the flaky stage to always succeed
        monkeypatch.setattr(mod.random, "random", lambda: 1.0)

        p = mod.build_pipeline(pipeline_id=_uid("basic"))
        outputs = p.run()

        # All 5 stages should have completed
        assert set(outputs.keys()) == {
            "fetch", "enrich", "count_by_status", "average_age", "deliver",
        }
        assert outputs["count_by_status"] == {"active": 3, "inactive": 1}
        # 4 users: 36 + 42 + 54 + 29 = 161 → avg 40.25
        assert outputs["average_age"] == 40.25
        assert "Report" in outputs["deliver"]

    def test_enrich_adds_bucket_field(self):
        mod = _load_example("basic_pipeline")
        ctx = {"fetch": mod.fetch_users({})}
        enriched = mod.enrich_users(ctx)
        buckets = {u["name"]: u["bucket"] for u in enriched}
        assert buckets == {
            "Ada": "junior",    # 36
            "Grace": "senior",  # 42
            "Linus": "senior",  # 54
            "Rob": "junior",    # 29
        }


# ── ai_pipeline.py ─────────────────────────────────────────────────────────────

class TestAiPipelineExample:
    def test_module_imports(self):
        mod = _load_example("ai_pipeline")
        assert hasattr(mod, "build_pipeline")
        assert hasattr(mod, "main")

    def test_build_pipeline_wires_expected_dag(self):
        mod = _load_example("ai_pipeline")
        p = mod.build_pipeline("test topic", pipeline_id=_uid("ai"))
        dag = p._state["dag"]
        assert dag["topic"] == []
        assert set(dag["research"]) == {"topic"}
        assert set(dag["draft"]) == {"research", "topic"}
        assert dag["critique"] == ["draft"]
        assert set(dag["revise"]) == {"draft", "critique"}
        assert set(dag["final"]) == {"revise", "topic"}

    def test_pick_topic_requires_context_key(self):
        mod = _load_example("ai_pipeline")
        with pytest.raises(ValueError, match="topic"):
            mod.pick_topic({})
        assert mod.pick_topic({"topic": "X"}) == "X"

    def test_format_final_wraps_revised_draft(self):
        mod = _load_example("ai_pipeline")
        out = mod.format_final({"topic": "LangGraph", "revise": "body text"})
        assert "Topic: LangGraph" in out
        assert "body text" in out

    def test_pipeline_runs_end_to_end_with_mocked_llm(self, monkeypatch):
        """
        Run the whole AI pipeline with claude_stage's SDK and CLI backends
        stubbed out. Verifies the DAG, prompt rendering, and context flow —
        without issuing a real API call.
        """
        mod = _load_example("ai_pipeline")

        from stagehand.providers import claude as claude_mod

        # Force the CLI backend path (so we only need to patch subprocess)
        monkeypatch.setattr(claude_mod, "_sdk_available", lambda: False)

        seen_prompts = []

        def fake_run(cmd, *args, **kwargs):
            prompt = cmd[2]
            seen_prompts.append(prompt)

            class Result:
                returncode = 0
                # Return a marker that embeds the stage-identifying keyword
                # so downstream stages receive distinguishable inputs.
                stderr = ""
                if "list the 5 most important" in prompt.lower():
                    stdout = "- fact one\n- fact two"
                elif "substack post for developers" in prompt.lower():
                    stdout = "Draft body about the topic."
                elif "harsh technical editor" in prompt.lower():
                    stdout = "- vague claim\n- missing example"
                else:
                    stdout = "Revised post body."
            return Result()

        import subprocess
        monkeypatch.setattr(subprocess, "run", fake_run)

        p = mod.build_pipeline("agent pipelines", pipeline_id=_uid("ai"))
        outputs = p.run(context={"topic": "agent pipelines"})

        assert set(outputs.keys()) == {
            "topic", "research", "draft", "critique", "revise", "final",
        }
        # Every claude_stage must have produced a prompt (4 LLM stages)
        assert len(seen_prompts) == 4
        # Prompt templating must substitute the topic, not leave {topic} literal
        assert "{topic}" not in seen_prompts[0]
        assert "agent pipelines" in seen_prompts[0]
        # Final stage wraps the revised draft
        assert "Revised post body" in outputs["final"]
        assert "agent pipelines" in outputs["final"]
