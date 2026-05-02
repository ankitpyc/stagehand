"""Tests for validator.py — nine pre-execution rules for PipelineSpec."""

from dataclasses import dataclass, field
from typing import List

import pytest

from stagehand.validator import (
    ValidationError,
    ValidationResult,
    validate,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def spec(stages, **extra):
    return {"pipeline_id": "test-pipeline", "task": "test", "stages": stages, **extra}


def stage(name, function="claude_stage", deps=None, prompt=None, **extra):
    s = {"name": name, "function": function, "deps": list(deps or [])}
    if prompt is not None:
        s["prompt"] = prompt
    s.update(extra)
    return s


def codes(result: ValidationResult) -> List[str]:
    return [e.code for e in result.errors]


# ── ValidationResult shape ─────────────────────────────────────────────────────

class TestValidationResult:
    def test_ok_result_is_truthy(self):
        result = ValidationResult(ok=True)
        assert result
        assert result.ok is True
        assert result.errors == []

    def test_failed_result_is_falsy(self):
        result = ValidationResult(ok=False, errors=[ValidationError("E001", "x")])
        assert not result
        assert result.codes() == ["E001"]


# ── E001: stages_present ───────────────────────────────────────────────────────

class TestStagesPresent:
    def test_empty_stages_list_fails(self):
        result = validate(spec([]))
        assert "E001" in codes(result)

    def test_missing_stages_key_fails(self):
        result = validate({"pipeline_id": "x", "task": "y"})
        assert "E001" in codes(result)

    def test_one_stage_passes_e001(self):
        result = validate(spec([stage("a", prompt="hi")]))
        assert "E001" not in codes(result)


# ── E002: unique_names ─────────────────────────────────────────────────────────

class TestUniqueNames:
    def test_duplicate_name_fails(self):
        result = validate(spec([
            stage("fetch", prompt="hi"),
            stage("fetch", prompt="hello"),
        ]))
        assert "E002" in codes(result)

    def test_unique_names_pass(self):
        result = validate(spec([
            stage("fetch", prompt="hi"),
            stage("write", prompt="hello", deps=["fetch"]),
        ]))
        assert "E002" not in codes(result)

    def test_three_duplicates_only_reported_once(self):
        result = validate(spec([
            stage("fetch", prompt="x"),
            stage("fetch", prompt="y"),
            stage("fetch", prompt="z"),
        ]))
        e002 = [e for e in result.errors if e.code == "E002"]
        assert len(e002) == 1


# ── E003: valid_names ──────────────────────────────────────────────────────────

class TestValidNames:
    def test_uppercase_name_fails(self):
        result = validate(spec([stage("FetchPapers", prompt="hi")]))
        assert "E003" in codes(result)

    def test_dashes_fail(self):
        result = validate(spec([stage("fetch-papers", prompt="hi")]))
        assert "E003" in codes(result)

    def test_starts_with_digit_fails(self):
        result = validate(spec([stage("1fetch", prompt="hi")]))
        assert "E003" in codes(result)

    def test_empty_name_fails(self):
        result = validate(spec([stage("", prompt="hi")]))
        assert "E003" in codes(result)

    def test_snake_case_passes(self):
        result = validate(spec([stage("fetch_papers_v2", prompt="hi")]))
        assert "E003" not in codes(result)


# ── E004: required_fields ──────────────────────────────────────────────────────

class TestRequiredFields:
    def test_missing_function_fails(self):
        s = {"name": "fetch", "deps": [], "prompt": "hi"}  # no function
        result = validate(spec([s]))
        assert "E004" in codes(result)

    def test_missing_name_fails(self):
        s = {"function": "claude_stage", "deps": [], "prompt": "hi"}
        result = validate(spec([s]))
        assert "E004" in codes(result)

    def test_blank_function_fails(self):
        result = validate(spec([stage("fetch", function="   ", prompt="hi")]))
        assert "E004" in codes(result)


# ── E005: deps_exist ───────────────────────────────────────────────────────────

class TestDepsExist:
    def test_undefined_dep_fails(self):
        result = validate(spec([
            stage("write", prompt="hi", deps=["does_not_exist"]),
        ]))
        assert "E005" in codes(result)

    def test_defined_dep_passes(self):
        result = validate(spec([
            stage("fetch", prompt="hi"),
            stage("write", prompt="{fetch}", deps=["fetch"]),
        ]))
        assert "E005" not in codes(result)


# ── E006: no_self_dep ──────────────────────────────────────────────────────────

class TestNoSelfDep:
    def test_self_dep_fails(self):
        result = validate(spec([stage("loop", prompt="hi", deps=["loop"])]))
        assert "E006" in codes(result)

    def test_normal_deps_pass(self):
        result = validate(spec([
            stage("fetch", prompt="hi"),
            stage("write", prompt="{fetch}", deps=["fetch"]),
        ]))
        assert "E006" not in codes(result)


# ── E007: no_cycles ────────────────────────────────────────────────────────────

class TestNoCycles:
    def test_two_node_cycle_fails(self):
        result = validate(spec([
            stage("a", prompt="hi", deps=["b"]),
            stage("b", prompt="hi", deps=["a"]),
        ]))
        assert "E007" in codes(result)

    def test_three_node_cycle_fails(self):
        result = validate(spec([
            stage("a", prompt="hi", deps=["c"]),
            stage("b", prompt="hi", deps=["a"]),
            stage("c", prompt="hi", deps=["b"]),
        ]))
        assert "E007" in codes(result)

    def test_dag_passes(self):
        result = validate(spec([
            stage("a", prompt="hi"),
            stage("b", prompt="{a}", deps=["a"]),
            stage("c", prompt="{a}", deps=["a"]),
            stage("d", prompt="{b} {c}", deps=["b", "c"]),
        ]))
        assert "E007" not in codes(result)

    def test_self_dep_does_not_double_report(self):
        # E006 handles self-deps; E007 should stay quiet to avoid noise.
        result = validate(spec([stage("loop", prompt="hi", deps=["loop"])]))
        assert "E007" not in codes(result)
        assert "E006" in codes(result)


# ── E008: function_in_registry ─────────────────────────────────────────────────

class TestFunctionInRegistry:
    def test_unknown_function_fails_with_set_registry(self):
        registry = {"claude_stage", "openai_stage"}
        result = validate(
            spec([stage("a", function="bogus_fn", prompt="hi")]),
            registry=registry,
        )
        assert "E008" in codes(result)

    def test_known_function_passes_with_set_registry(self):
        registry = {"claude_stage", "openai_stage"}
        result = validate(
            spec([stage("a", function="claude_stage", prompt="hi")]),
            registry=registry,
        )
        assert "E008" not in codes(result)

    def test_dict_registry_uses_keys(self):
        registry = {"claude_stage": "...", "web_search": "..."}
        result = validate(
            spec([stage("a", function="web_search", prompt="hi")]),
            registry=registry,
        )
        assert "E008" not in codes(result)

    def test_registry_with_has_method(self):
        class Reg:
            def has(self, name): return name == "claude_stage"

        result = validate(
            spec([stage("a", function="bogus_fn", prompt="hi")]),
            registry=Reg(),
        )
        assert "E008" in codes(result)

    def test_no_registry_skips_e008(self):
        # Without a registry, we have no ground truth, so don't report E008.
        result = validate(spec([stage("a", function="anything", prompt="hi")]))
        assert "E008" not in codes(result)


# ── E009: prompt_refs_upstream ─────────────────────────────────────────────────

class TestPromptRefsUpstream:
    def test_unreferenced_stage_fails(self):
        # 'write' references {fetch} but doesn't depend on 'fetch'
        result = validate(spec([
            stage("fetch", prompt="hi"),
            stage("write", prompt="Use {fetch} here", deps=[]),
        ]))
        assert "E009" in codes(result)

    def test_dep_reference_passes(self):
        result = validate(spec([
            stage("fetch", prompt="hi"),
            stage("write", prompt="Use {fetch} here", deps=["fetch"]),
        ]))
        assert "E009" not in codes(result)

    def test_transitive_dep_passes(self):
        result = validate(spec([
            stage("fetch", prompt="hi"),
            stage("clean", prompt="{fetch}", deps=["fetch"]),
            stage("write", prompt="Use {fetch} here", deps=["clean"]),
        ]))
        assert "E009" not in codes(result)

    def test_task_reference_passes(self):
        result = validate(spec([
            stage("fetch", prompt="Search for: {task}"),
        ]))
        assert "E009" not in codes(result)

    def test_nested_subscript_resolves_to_root(self):
        # {fetch[title]} should be treated as referencing 'fetch'.
        result = validate(spec([
            stage("fetch", prompt="hi"),
            stage("write", prompt="Title: {fetch[title]}", deps=["fetch"]),
        ]))
        assert "E009" not in codes(result)

    def test_escaped_braces_are_not_placeholders(self):
        result = validate(spec([
            stage("a", prompt="literal {{braces}} here"),
        ]))
        assert "E009" not in codes(result)


# ── Integration: clean spec & multi-failure spec ───────────────────────────────

class TestEndToEnd:
    def test_clean_spec_passes_all_rules(self):
        s = spec([
            stage("fetch_papers", function="claude_stage",
                  prompt="Find papers about {task}"),
            stage("synthesize", function="claude_stage",
                  prompt="Summarize {fetch_papers}", deps=["fetch_papers"]),
            stage("draft", function="claude_stage",
                  prompt="Draft post from {synthesize}", deps=["synthesize"]),
        ])
        registry = {"claude_stage", "openai_stage"}
        result = validate(s, registry=registry)
        assert result.ok, f"Expected pass, got: {codes(result)}"

    def test_multiple_violations_collected_in_one_pass(self):
        # Bad name, missing function, undefined dep, prompt ref to non-upstream
        result = validate(spec([
            {"name": "BadName", "deps": [], "prompt": "hi"},  # E003 + E004 (function)
            {"name": "uses_undef", "function": "claude_stage",
             "deps": ["ghost"], "prompt": "{ghost}"},  # E005 + E009
        ]))
        c = codes(result)
        assert "E003" in c
        assert "E004" in c
        assert "E005" in c
        assert "E009" in c

    def test_dataclass_spec_is_accepted(self):
        @dataclass
        class StageSpec:
            name: str
            function: str = "claude_stage"
            prompt: str = ""
            deps: List[str] = field(default_factory=list)

        @dataclass
        class PipelineSpec:
            pipeline_id: str
            task: str
            stages: List[StageSpec]

        s = PipelineSpec(
            pipeline_id="x",
            task="t",
            stages=[
                StageSpec(name="fetch", prompt="hi"),
                StageSpec(name="write", prompt="{fetch}", deps=["fetch"]),
            ],
        )
        result = validate(s)
        assert result.ok, f"Expected pass, got: {codes(result)}"
