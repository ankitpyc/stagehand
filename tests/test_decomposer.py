"""
Tests for decomposer.py — prompt building and response parsing (pure).

These tests use only stdlib + the in-process ``stagehand.spec``,
``stagehand.validator``, and ``stagehand.capabilities`` modules. No
network, no anthropic SDK, no subprocess — the decomposer module is
exercised end-to-end purely against in-memory inputs.
"""

from __future__ import annotations

import json
import os

import pytest

# Stagehand tests historically use an isolated checkpoint dir. The decomposer
# does no I/O but other test modules import the package, so set this to
# match the project convention.
os.environ.setdefault("STAGEHAND_DIR", "/tmp/stagehand-test-decomposer")

# The decomposer depends on stagehand.spec, stagehand.validator, and
# stagehand.capabilities, which are landing on separate branches (issue-21
# and issue-23). Skip the entire module gracefully until those deps merge.
pytest.importorskip("stagehand.capabilities")
pytest.importorskip("stagehand.spec")
pytest.importorskip("stagehand.validator")

from stagehand.capabilities import (
    Capability,
    CapabilityParam,
    CapabilityRegistry,
)
from stagehand.decomposer import (
    DecompositionParseError,
    build_correction_prompt,
    build_decomposition_prompt,
    parse_decomposition_response,
)
from stagehand.spec import PipelineSpec, StageSpec
from stagehand.validator import ValidationError


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def registry() -> CapabilityRegistry:
    """A small in-memory registry covering a couple of capability kinds."""
    reg = CapabilityRegistry()
    reg.register(Capability(
        name="claude_stage",
        description="Run a Claude prompt as a stage.",
        kind="provider",
        params=[CapabilityParam(name="prompt", required=True)],
        when_to_use="When you need an LLM-driven step.",
        example="claude_stage('Summarise: {fetch}')",
    ))
    reg.register(Capability(
        name="http_stage",
        description="Issue an HTTP request and capture the response.",
        kind="provider",
        params=[CapabilityParam(name="url", required=True)],
    ))
    reg.register_function(
        "scrape",
        fn=lambda ctx: None,
        description="Fetch and clean a URL.",
        params=[CapabilityParam(name="url", required=True)],
    )
    return reg


@pytest.fixture
def valid_spec() -> PipelineSpec:
    return PipelineSpec(
        pipeline_id="example-pipeline",
        task="Summarise the docs.",
        stages=[
            StageSpec(
                name="fetch",
                prompt="Fetch the docs from {task}",
                model="claude-sonnet-4-5",
            ),
            StageSpec(
                name="summarise",
                prompt="Summarise: {fetch}",
                model="claude-sonnet-4-5",
                deps=["fetch"],
            ),
        ],
    )


# ── build_decomposition_prompt ────────────────────────────────────────────────

class TestBuildDecompositionPrompt:
    def test_contains_task_text(self, registry):
        task = "Research LangGraph and write a Substack post."
        prompt = build_decomposition_prompt(task, registry)
        assert task in prompt

    def test_contains_every_capability_name(self, registry):
        prompt = build_decomposition_prompt("any task", registry)
        for name in registry.names():
            assert name in prompt, f"capability {name!r} missing from prompt"

    def test_contains_pipelinespec_token(self, registry):
        prompt = build_decomposition_prompt("any task", registry)
        assert "PipelineSpec" in prompt

    def test_contains_stagespec_token(self, registry):
        # The schema section should also surface StageSpec for the LLM.
        prompt = build_decomposition_prompt("any task", registry)
        assert "StageSpec" in prompt

    def test_includes_final_return_only_instruction(self, registry):
        prompt = build_decomposition_prompt("any task", registry)
        assert "Return ONLY a JSON object" in prompt

    def test_is_deterministic(self, registry):
        a = build_decomposition_prompt("same task", registry)
        b = build_decomposition_prompt("same task", registry)
        assert a == b

    def test_examples_are_rendered_when_provided(self, registry, valid_spec):
        prompt = build_decomposition_prompt(
            "any task", registry, examples=[valid_spec]
        )
        assert "Example 1" in prompt
        assert "example-pipeline" in prompt
        # The example should be rendered as a JSON code block.
        assert "```json" in prompt

    def test_examples_section_omitted_when_no_examples(self, registry):
        prompt = build_decomposition_prompt("any task", registry, examples=None)
        assert "# Examples" not in prompt

    def test_dict_examples_are_serialised(self, registry):
        prompt = build_decomposition_prompt(
            "any task",
            registry,
            examples=[{"pipeline_id": "ex-1", "task": "demo", "stages": []}],
        )
        assert "ex-1" in prompt


# ── build_correction_prompt ───────────────────────────────────────────────────

class TestBuildCorrectionPrompt:
    def test_includes_prior_attempt_verbatim(self, registry):
        prior = json.dumps(
            {"pipeline_id": "broken", "task": "demo", "stages": []},
            indent=2,
        )
        prompt = build_correction_prompt(
            "demo task", registry, prior, errors=[]
        )
        assert prior in prompt

    def test_validation_errors_section_is_present(self, registry):
        prompt = build_correction_prompt(
            "demo task", registry, "{}", errors=[]
        )
        assert "## Validation errors" in prompt

    def test_lists_every_error_message(self, registry):
        errors = [
            ValidationError(
                code="E001",
                message="Pipeline has no stages.",
                path="stages",
                fix="Add at least one stage.",
            ),
            ValidationError(
                code="E007",
                message="Dependency cycle detected: a -> b -> a.",
                path="stages[*].deps",
                fix="Break the cycle by removing one dep.",
            ),
        ]
        prompt = build_correction_prompt(
            "demo task", registry, "{}", errors=errors
        )
        for err in errors:
            assert err.message in prompt
            assert err.fix in prompt
            assert err.code in prompt

    def test_includes_task_text(self, registry):
        prompt = build_correction_prompt(
            "specific task description",
            registry,
            "{}",
            errors=[],
        )
        assert "specific task description" in prompt

    def test_includes_capability_registry(self, registry):
        prompt = build_correction_prompt(
            "demo task", registry, "{}", errors=[]
        )
        for name in registry.names():
            assert name in prompt

    def test_final_return_only_instruction(self, registry):
        prompt = build_correction_prompt(
            "demo task", registry, "{}", errors=[]
        )
        assert "Return ONLY a JSON object" in prompt

    def test_handles_iterable_errors(self, registry):
        # Generators / non-list iterables should be accepted.
        def gen():
            yield ValidationError(
                code="E003",
                message="Bad name.",
                fix="Use snake_case.",
            )

        prompt = build_correction_prompt(
            "demo task", registry, "{}", errors=gen()
        )
        assert "Bad name." in prompt
        assert "Use snake_case." in prompt


# ── parse_decomposition_response ──────────────────────────────────────────────

def _spec_dict() -> dict:
    return {
        "pipeline_id": "demo",
        "task": "demo task",
        "stages": [
            {
                "name": "fetch",
                "prompt": "Fetch {task}",
                "model": "claude-sonnet-4-5",
            }
        ],
    }


class TestParseDecompositionResponse:
    def test_parses_bare_json(self):
        raw = json.dumps(_spec_dict())
        spec = parse_decomposition_response(raw)
        assert isinstance(spec, PipelineSpec)
        assert spec.pipeline_id == "demo"
        assert spec.stage_names() == ["fetch"]

    def test_parses_json_fenced(self):
        raw = "```json\n" + json.dumps(_spec_dict(), indent=2) + "\n```"
        spec = parse_decomposition_response(raw)
        assert spec.pipeline_id == "demo"

    def test_parses_bare_fenced(self):
        raw = "```\n" + json.dumps(_spec_dict()) + "\n```"
        spec = parse_decomposition_response(raw)
        assert spec.pipeline_id == "demo"

    def test_tolerates_surrounding_whitespace(self):
        raw = "   \n\n" + json.dumps(_spec_dict()) + "  \n  "
        spec = parse_decomposition_response(raw)
        assert spec.pipeline_id == "demo"

    def test_tolerates_whitespace_inside_fences(self):
        raw = "```json\n\n" + json.dumps(_spec_dict(), indent=2) + "\n\n```"
        spec = parse_decomposition_response(raw)
        assert spec.pipeline_id == "demo"

    def test_uppercase_json_language_tag(self):
        raw = "```JSON\n" + json.dumps(_spec_dict()) + "\n```"
        spec = parse_decomposition_response(raw)
        assert spec.pipeline_id == "demo"

    def test_raises_on_garbage(self):
        with pytest.raises(DecompositionParseError) as exc:
            parse_decomposition_response("this is not json at all")
        assert exc.value.raw == "this is not json at all"

    def test_raises_on_empty_response(self):
        with pytest.raises(DecompositionParseError) as exc:
            parse_decomposition_response("")
        assert exc.value.raw == ""

    def test_raises_on_whitespace_only(self):
        with pytest.raises(DecompositionParseError):
            parse_decomposition_response("   \n\n   ")

    def test_raises_on_empty_fenced_block(self):
        with pytest.raises(DecompositionParseError):
            parse_decomposition_response("```json\n\n```")

    def test_raises_on_missing_required_fields(self):
        # Valid JSON, but PipelineSpec.from_dict will reject it.
        raw = json.dumps({"task": "no pipeline_id"})
        with pytest.raises(DecompositionParseError) as exc:
            parse_decomposition_response(raw)
        assert exc.value.raw == raw

    def test_raises_on_non_string_input(self):
        with pytest.raises(DecompositionParseError):
            parse_decomposition_response(12345)  # type: ignore[arg-type]

    def test_error_preserves_underlying_cause(self):
        with pytest.raises(DecompositionParseError) as exc:
            parse_decomposition_response("{not valid json")
        assert exc.value.__cause__ is not None
        assert isinstance(exc.value.__cause__, json.JSONDecodeError)

    def test_error_class_has_raw_attribute(self):
        # Sanity: the exception itself must expose .raw, even when
        # constructed directly (so callers can build it from anywhere).
        err = DecompositionParseError("some raw blob")
        assert err.raw == "some raw blob"


# ── Roundtrip ──────────────────────────────────────────────────────────────────

class TestRoundtrip:
    def test_prompt_builder_to_parser_smoke(self, registry, valid_spec):
        # Build a prompt (we don't actually send it anywhere) then ensure
        # that an LLM-shaped response with that spec roundtrips through the
        # parser.
        _ = build_decomposition_prompt(valid_spec.task, registry)
        raw = "```json\n" + valid_spec.to_json(indent=2) + "\n```"
        parsed = parse_decomposition_response(raw)
        assert parsed.to_dict() == valid_spec.to_dict()
