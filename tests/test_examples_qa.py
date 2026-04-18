"""
QA-added tests for examples/.

These complement tests/test_examples.py by covering gaps discovered during
validation of Issue #14:

  - main() entrypoint smoke tests (the developer's tests only exercised
    build_pipeline + run, never main(), which owns argv + random.seed wiring)
  - ai_pipeline default-topic fallback path (main picks a default when argv
    carries no topic)
  - basic_pipeline resume-from-failure path — the canonical "checkpointing
    lets you recover" demo behavior claimed in the README
  - claude_stage template rendering failure with a clear error when an
    upstream stage's output is missing
  - format_final banner structure (not just substring presence)
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _load_example(name: str):
    """Import an example module by file path, fresh every time."""
    path = EXAMPLES_DIR / f"{name}.py"
    mod_name = f"stagehand_examples_qa.{name}.{uuid.uuid4().hex[:6]}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def isolated_checkpoint_dir(tmp_path):
    prev = os.environ.get("STAGEHAND_DIR")
    os.environ["STAGEHAND_DIR"] = str(tmp_path / "stagehand")
    yield
    if prev is None:
        os.environ.pop("STAGEHAND_DIR", None)
    else:
        os.environ["STAGEHAND_DIR"] = prev


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ── basic_pipeline.main smoke ──────────────────────────────────────────────────

class TestBasicPipelineMain:
    def test_main_runs_to_completion(self, monkeypatch, capsys):
        """main() must execute end-to-end and print the report banner."""
        mod = _load_example("basic_pipeline")
        # Deterministic: force flaky stage to succeed on first attempt.
        monkeypatch.setattr(mod.random, "random", lambda: 1.0)
        # Unique pipeline_id so runs don't collide across tests in the same
        # shared STAGEHAND_DIR invocations.
        monkeypatch.setattr(
            mod, "build_pipeline",
            lambda pipeline_id=_uid("basic-main"): _build_wrapped(mod, pipeline_id),
        )
        mod.main()
        out = capsys.readouterr().out
        assert "Pipeline outputs" in out
        assert "deliver" in out
        assert "Report" in out  # deliver output

    def test_main_survives_flaky_stage_within_retries(self, monkeypatch, capsys):
        """
        flaky_deliver fails ~40% per call but has retry=3. Simulate two
        failures followed by a success to prove the retry path is what makes
        the demo resilient.
        """
        mod = _load_example("basic_pipeline")
        calls = {"n": 0}

        def almost_always_fails():
            calls["n"] += 1
            # Fail first 2 attempts, succeed on 3rd
            return 0.0 if calls["n"] <= 2 else 1.0

        monkeypatch.setattr(mod.random, "random", almost_always_fails)
        p = mod.build_pipeline(pipeline_id=_uid("basic-flaky"))
        # Keep the test fast: drop the backoff base
        p._stages[-1].retry_backoff = 0.01
        outputs = p.run()
        assert "deliver" in outputs
        # flaky_deliver was invoked 3 times before succeeding
        assert calls["n"] == 3


def _build_wrapped(mod, pipeline_id):
    """Original build_pipeline signature accepts pipeline_id kwarg — call it."""
    # Call through to the module's real builder. This helper exists only to
    # let the monkeypatched main() use a UUID-scoped pipeline_id.
    return _original_build_pipeline(mod)(pipeline_id=pipeline_id)


def _original_build_pipeline(mod):
    """Re-import and return the unpatched build_pipeline."""
    fresh = _load_example("basic_pipeline")
    return fresh.build_pipeline


# ── basic_pipeline resume path ─────────────────────────────────────────────────

class TestBasicPipelineResume:
    def test_resume_after_deliver_failure_skips_completed_stages(self, monkeypatch):
        """
        Exercise the README claim: "crashes resume from the last completed stage."

        Stage-by-stage: force deliver to fail on the first run (all 3 retries
        raise). Then on the second run, the upstream stages MUST be skipped
        (already 'done' in the checkpoint) and only deliver is attempted.
        """
        mod = _load_example("basic_pipeline")
        pipeline_id = _uid("basic-resume")

        # Run 1: deliver always fails.
        monkeypatch.setattr(mod.random, "random", lambda: 0.0)
        p1 = mod.build_pipeline(pipeline_id=pipeline_id)
        # Speed up backoff.
        p1._stages[-1].retry_backoff = 0.01
        outputs1 = p1.run()
        assert "fetch" in outputs1 and "enrich" in outputs1
        assert "deliver" not in outputs1  # failed, not in outputs

        # Run 2: deliver succeeds. Upstream must not re-execute.
        monkeypatch.setattr(mod.random, "random", lambda: 1.0)
        call_count = {"fetch": 0}
        original_fetch = mod.fetch_users

        def counting_fetch(ctx):
            call_count["fetch"] += 1
            return original_fetch(ctx)

        monkeypatch.setattr(mod, "fetch_users", counting_fetch)
        p2 = mod.build_pipeline(pipeline_id=pipeline_id)
        p2._stages[-1].retry_backoff = 0.01
        outputs2 = p2.run()

        assert set(outputs2.keys()) == {
            "fetch", "enrich", "count_by_status", "average_age", "deliver",
        }
        assert call_count["fetch"] == 0, (
            "fetch stage re-executed on resume — checkpoint resume is broken"
        )


# ── ai_pipeline.main default-topic path ────────────────────────────────────────

class TestAiPipelineMain:
    def test_main_uses_default_topic_when_no_argv(self, monkeypatch, capsys):
        """
        No CLI args -> the default topic string kicks in.
        We don't care what the default IS, only that the pipeline runs.
        """
        mod = _load_example("ai_pipeline")

        from stagehand.providers import claude as claude_mod
        monkeypatch.setattr(claude_mod, "_sdk_available", lambda: False)

        def fake_run(cmd, *a, **kw):
            class R:
                returncode = 0
                stdout = "stubbed llm reply"
                stderr = ""
            return R()

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(sys, "argv", ["ai_pipeline.py"])  # no topic arg

        # Bind a unique pipeline_id to avoid checkpoint collisions.
        original_build = mod.build_pipeline
        monkeypatch.setattr(
            mod, "build_pipeline",
            lambda topic, pipeline_id=_uid("ai-main"): original_build(topic, pipeline_id),
        )

        mod.main()
        out = capsys.readouterr().out
        # Banner always printed when final stage completes
        assert "Topic:" in out
        assert "stubbed llm reply" in out

    def test_main_accepts_multi_word_topic(self, monkeypatch, capsys):
        """Multi-word CLI args must be joined, not truncated."""
        mod = _load_example("ai_pipeline")

        from stagehand.providers import claude as claude_mod
        monkeypatch.setattr(claude_mod, "_sdk_available", lambda: False)

        seen = []

        def fake_run(cmd, *a, **kw):
            seen.append(cmd[2])

            class R:
                returncode = 0
                stdout = "ok"
                stderr = ""
            return R()

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(sys, "argv", ["ai_pipeline.py", "multi", "word", "topic"])

        original_build = mod.build_pipeline
        monkeypatch.setattr(
            mod, "build_pipeline",
            lambda topic, pipeline_id=_uid("ai-multi"): original_build(topic, pipeline_id),
        )
        mod.main()

        out = capsys.readouterr().out
        assert "Topic: multi word topic" in out
        # First prompt (research) must have substituted the full topic
        assert any("multi word topic" in p for p in seen)


# ── Prompt-templating failure propagation ──────────────────────────────────────

class TestAiPipelinePromptFailure:
    def test_missing_upstream_output_raises_keyerror(self, monkeypatch):
        """
        If the draft stage is somehow invoked without {research} available,
        claude_stage's template rendering must raise KeyError (not silently
        send an unformatted prompt to Claude).
        """
        mod = _load_example("ai_pipeline")
        from stagehand.providers import claude as claude_mod

        monkeypatch.setattr(claude_mod, "_sdk_available", lambda: False)

        # Extract the draft stage's rendered callable.
        p = mod.build_pipeline("X", pipeline_id=_uid("ai-keyerror"))
        draft_stage = next(s for s in p._stages if s.name == "draft")
        with pytest.raises(KeyError, match="research"):
            # ctx has topic but no research output
            draft_stage.fn({"topic": "X"})


# ── format_final banner structure ──────────────────────────────────────────────

class TestFormatFinalStructure:
    def test_banner_wraps_topic_and_separates_body(self):
        mod = _load_example("ai_pipeline")
        out = mod.format_final({"topic": "LangGraph", "revise": "body text"})
        lines = out.splitlines()
        # Two banner lines framing the topic, then blank, then body
        assert lines[0].startswith("━")
        assert "LangGraph" in lines[1]
        assert lines[2].startswith("━")
        assert "body text" in out

    def test_banner_includes_full_topic_even_if_long(self):
        mod = _load_example("ai_pipeline")
        long_topic = "x" * 200
        out = mod.format_final({"topic": long_topic, "revise": "b"})
        assert long_topic in out
