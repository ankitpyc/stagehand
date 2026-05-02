"""Tests for spec.py — StageSpec and PipelineSpec construction + roundtrip serialization."""

import json
import pytest

from stagehand.spec import PipelineSpec, StageSpec


# ── StageSpec ──────────────────────────────────────────────────────────────────

class TestStageSpecConstruction:
    def test_minimal_stage_has_defaults(self):
        s = StageSpec(name="fetch", prompt="Fetch the data", model="claude-sonnet-4-5")
        assert s.name == "fetch"
        assert s.prompt == "Fetch the data"
        assert s.model == "claude-sonnet-4-5"
        assert s.deps == []
        assert s.capabilities == []
        assert s.timeout is None
        assert s.retry == 1

    def test_full_stage_preserves_all_fields(self):
        s = StageSpec(
            name="analyze",
            prompt="Analyze {fetch}",
            model="claude-opus-4",
            deps=["fetch"],
            capabilities=["web_search", "calculator"],
            timeout=120.0,
            retry=3,
        )
        assert s.deps == ["fetch"]
        assert s.capabilities == ["web_search", "calculator"]
        assert s.timeout == 120.0
        assert s.retry == 3

    def test_default_lists_are_independent(self):
        a = StageSpec(name="a", prompt="p", model="m")
        b = StageSpec(name="b", prompt="p", model="m")
        a.deps.append("x")
        assert b.deps == []


class TestStageSpecSerialization:
    def test_to_dict_contains_all_fields(self):
        s = StageSpec(name="s", prompt="p", model="m", deps=["d"], capabilities=["c"], timeout=5.0, retry=2)
        d = s.to_dict()
        assert d == {
            "name": "s",
            "prompt": "p",
            "model": "m",
            "deps": ["d"],
            "capabilities": ["c"],
            "timeout": 5.0,
            "retry": 2,
        }

    def test_from_dict_minimal(self):
        s = StageSpec.from_dict({"name": "s", "prompt": "p", "model": "m"})
        assert s.name == "s"
        assert s.deps == []
        assert s.retry == 1

    def test_roundtrip_preserves_equality(self):
        original = StageSpec(name="s", prompt="p", model="m", deps=["a", "b"], capabilities=["x"], timeout=10.0, retry=2)
        roundtripped = StageSpec.from_dict(original.to_dict())
        assert roundtripped == original

    def test_from_dict_rejects_non_dict(self):
        with pytest.raises(TypeError):
            StageSpec.from_dict(["name", "s"])

    def test_from_dict_raises_on_missing_required_field(self):
        with pytest.raises(KeyError) as exc:
            StageSpec.from_dict({"name": "s", "prompt": "p"})
        assert "model" in str(exc.value)

    def test_from_dict_coerces_none_lists_to_empty(self):
        s = StageSpec.from_dict({"name": "s", "prompt": "p", "model": "m", "deps": None, "capabilities": None})
        assert s.deps == []
        assert s.capabilities == []


# ── PipelineSpec ───────────────────────────────────────────────────────────────

class TestPipelineSpecConstruction:
    def test_minimal_spec_has_empty_stages(self):
        p = PipelineSpec(pipeline_id="run-1", task="do something")
        assert p.pipeline_id == "run-1"
        assert p.task == "do something"
        assert p.stages == []
        assert p.created_at is None

    def test_spec_holds_stages_in_order(self):
        a = StageSpec(name="a", prompt="p", model="m")
        b = StageSpec(name="b", prompt="p", model="m", deps=["a"])
        p = PipelineSpec(pipeline_id="id", task="t", stages=[a, b])
        assert p.stage_names() == ["a", "b"]

    def test_get_stage_returns_matching_stage(self):
        a = StageSpec(name="a", prompt="p", model="m")
        p = PipelineSpec(pipeline_id="id", task="t", stages=[a])
        assert p.get_stage("a") is a

    def test_get_stage_raises_when_missing(self):
        p = PipelineSpec(pipeline_id="id", task="t", stages=[])
        with pytest.raises(KeyError):
            p.get_stage("missing")


class TestPipelineSpecSerialization:
    def _sample(self) -> PipelineSpec:
        return PipelineSpec(
            pipeline_id="research-1",
            task="Research LangGraph and write a Substack post",
            stages=[
                StageSpec(name="search", prompt="Search for {task}", model="claude-sonnet-4-5"),
                StageSpec(
                    name="draft",
                    prompt="Draft post from {search}",
                    model="claude-opus-4",
                    deps=["search"],
                    capabilities=["web_fetch"],
                    timeout=180.0,
                    retry=2,
                ),
            ],
            created_at="2026-05-02T10:00:00Z",
        )

    def test_to_dict_nests_stages(self):
        p = self._sample()
        d = p.to_dict()
        assert d["pipeline_id"] == "research-1"
        assert d["created_at"] == "2026-05-02T10:00:00Z"
        assert len(d["stages"]) == 2
        assert d["stages"][1]["deps"] == ["search"]

    def test_from_dict_reconstructs_stages(self):
        p = PipelineSpec.from_dict(self._sample().to_dict())
        assert isinstance(p.stages[0], StageSpec)
        assert p.stages[1].deps == ["search"]
        assert p.stages[1].capabilities == ["web_fetch"]

    def test_dict_roundtrip_preserves_equality(self):
        original = self._sample()
        roundtripped = PipelineSpec.from_dict(original.to_dict())
        assert roundtripped == original

    def test_json_roundtrip_preserves_equality(self):
        original = self._sample()
        roundtripped = PipelineSpec.from_json(original.to_json())
        assert roundtripped == original

    def test_to_json_produces_valid_json_string(self):
        payload = self._sample().to_json()
        parsed = json.loads(payload)
        assert parsed["pipeline_id"] == "research-1"
        assert parsed["stages"][0]["name"] == "search"

    def test_from_dict_rejects_non_dict(self):
        with pytest.raises(TypeError):
            PipelineSpec.from_dict("not-a-dict")

    def test_from_dict_raises_on_missing_required_field(self):
        with pytest.raises(KeyError) as exc:
            PipelineSpec.from_dict({"pipeline_id": "id"})
        assert "task" in str(exc.value)

    def test_from_dict_rejects_non_list_stages(self):
        with pytest.raises(TypeError):
            PipelineSpec.from_dict({"pipeline_id": "id", "task": "t", "stages": {"not": "a-list"}})

    def test_from_dict_handles_missing_stages(self):
        p = PipelineSpec.from_dict({"pipeline_id": "id", "task": "t"})
        assert p.stages == []


# ── Edge Cases for Decomposer Self-Correction ──────────────────────────────

class TestStageSpecEdgeCases:
    """These edge cases document that spec.py is validation-free; validator.py handles semantic checks."""

    def test_empty_string_name_allowed(self):
        """Empty name is allowed at spec level; validator.py will reject it."""
        s = StageSpec(name="", prompt="p", model="m")
        assert s.name == ""
        # Roundtrips successfully
        roundtrip = StageSpec.from_dict(s.to_dict())
        assert roundtrip.name == ""

    def test_empty_prompt_allowed(self):
        """Empty prompt is allowed at spec level; validator.py will reject it."""
        s = StageSpec(name="s", prompt="", model="m")
        assert s.prompt == ""

    def test_empty_model_allowed(self):
        """Empty model is allowed at spec level; validator.py will reject it."""
        s = StageSpec(name="s", prompt="p", model="")
        assert s.model == ""

    def test_negative_retry_allowed(self):
        """Negative retry is allowed at spec level; validator.py will reject it."""
        s = StageSpec(name="s", prompt="p", model="m", retry=-1)
        assert s.retry == -1

    def test_zero_retry_allowed(self):
        """Zero retry is allowed at spec level."""
        s = StageSpec(name="s", prompt="p", model="m", retry=0)
        assert s.retry == 0

    def test_negative_timeout_allowed(self):
        """Negative timeout is allowed at spec level; validator.py will reject it."""
        s = StageSpec(name="s", prompt="p", model="m", timeout=-1.0)
        assert s.timeout == -1.0

    def test_zero_timeout_allowed(self):
        """Zero timeout is allowed at spec level."""
        s = StageSpec(name="s", prompt="p", model="m", timeout=0.0)
        assert s.timeout == 0.0

    def test_very_large_timeout(self):
        """Very large timeout is allowed."""
        s = StageSpec(name="s", prompt="p", model="m", timeout=1e10)
        assert s.timeout == 1e10

    def test_from_dict_coerces_string_retry(self):
        """from_dict coerces string retry to int (lenient deserialization)."""
        s = StageSpec.from_dict({"name": "s", "prompt": "p", "model": "m", "retry": "5"})
        assert s.retry == 5
        assert isinstance(s.retry, int)

    def test_from_dict_unknown_fields_ignored(self):
        """from_dict ignores unknown fields (lenient deserialization for forward compatibility)."""
        s = StageSpec.from_dict({
            "name": "s",
            "prompt": "p",
            "model": "m",
            "unknown_field": "ignored",
            "another_unknown": 123,
        })
        assert s.name == "s"
        # Unknown fields don't appear in roundtrip
        d = s.to_dict()
        assert "unknown_field" not in d

    def test_special_characters_in_name(self):
        """Special characters in name are preserved."""
        s = StageSpec(name="fetch-data/v2.0", prompt="p", model="m")
        assert s.name == "fetch-data/v2.0"
        roundtrip = StageSpec.from_dict(s.to_dict())
        assert roundtrip.name == "fetch-data/v2.0"

    def test_unicode_in_prompt(self):
        """Unicode characters in prompt are preserved."""
        s = StageSpec(name="s", prompt="Bonjour 👋 мир 世界", model="m")
        assert "👋" in s.prompt
        roundtrip = StageSpec.from_dict(s.to_dict())
        assert roundtrip.prompt == "Bonjour 👋 мир 世界"

    def test_very_long_prompt(self):
        """Very long prompt is handled."""
        long_prompt = "x" * 100000
        s = StageSpec(name="s", prompt=long_prompt, model="m")
        assert len(s.prompt) == 100000
        roundtrip = StageSpec.from_dict(s.to_dict())
        assert roundtrip.prompt == long_prompt

    def test_empty_deps_list(self):
        """Empty deps list is valid."""
        s = StageSpec(name="s", prompt="p", model="m", deps=[])
        assert s.deps == []

    def test_many_deps(self):
        """Many dependencies are preserved."""
        deps = [f"stage_{i}" for i in range(100)]
        s = StageSpec(name="s", prompt="p", model="m", deps=deps)
        assert len(s.deps) == 100
        roundtrip = StageSpec.from_dict(s.to_dict())
        assert roundtrip.deps == deps

    def test_duplicate_deps_allowed(self):
        """Duplicate dependencies are allowed at spec level (semantic validation in validator.py)."""
        s = StageSpec(name="s", prompt="p", model="m", deps=["a", "a", "b"])
        assert s.deps == ["a", "a", "b"]


class TestPipelineSpecEdgeCases:
    """Edge cases for PipelineSpec serialization and construction."""

    def test_empty_pipeline_id_allowed(self):
        """Empty pipeline_id is allowed at spec level."""
        p = PipelineSpec(pipeline_id="", task="do something")
        assert p.pipeline_id == ""
        roundtrip = PipelineSpec.from_dict(p.to_dict())
        assert roundtrip.pipeline_id == ""

    def test_empty_task_allowed(self):
        """Empty task is allowed at spec level."""
        p = PipelineSpec(pipeline_id="id", task="")
        assert p.task == ""

    def test_from_dict_unknown_fields_ignored(self):
        """from_dict ignores unknown top-level fields."""
        p = PipelineSpec.from_dict({
            "pipeline_id": "id",
            "task": "t",
            "unknown_field": "ignored",
            "version": "2.0",
        })
        assert p.pipeline_id == "id"
        d = p.to_dict()
        assert "unknown_field" not in d
        assert "version" not in d

    def test_malformed_stage_in_stages_list(self):
        """from_dict with malformed stage raises KeyError with clear message."""
        with pytest.raises(KeyError) as exc:
            PipelineSpec.from_dict({
                "pipeline_id": "id",
                "task": "t",
                "stages": [{"name": "s", "prompt": "p"}],  # missing required 'model'
            })
        assert "model" in str(exc.value)

    def test_stage_with_all_none_optionals(self):
        """Stage with explicit None for optional list/timeout fields (retry must be int)."""
        s = StageSpec.from_dict({
            "name": "s",
            "prompt": "p",
            "model": "m",
            "deps": None,
            "capabilities": None,
            "timeout": None,
        })
        assert s.deps == []
        assert s.capabilities == []
        assert s.timeout is None
        assert s.retry == 1  # Default

    def test_stage_from_dict_retry_none_raises(self):
        """from_dict with retry=None raises TypeError (retry must be int)."""
        with pytest.raises(TypeError):
            StageSpec.from_dict({
                "name": "s",
                "prompt": "p",
                "model": "m",
                "retry": None,
            })

    def test_many_stages(self):
        """PipelineSpec with many stages."""
        stages = [
            StageSpec(name=f"stage_{i}", prompt=f"p{i}", model="m")
            for i in range(100)
        ]
        p = PipelineSpec(pipeline_id="id", task="t", stages=stages)
        assert len(p.stage_names()) == 100
        roundtrip = PipelineSpec.from_dict(p.to_dict())
        assert len(roundtrip.stages) == 100
        assert roundtrip.stage_names() == [f"stage_{i}" for i in range(100)]

    def test_get_stage_with_special_characters(self):
        """get_stage finds stages with special characters in name."""
        s = StageSpec(name="fetch/v2.0", prompt="p", model="m")
        p = PipelineSpec(pipeline_id="id", task="t", stages=[s])
        assert p.get_stage("fetch/v2.0") is s

    def test_stage_names_preserves_order(self):
        """stage_names preserves the insertion order of stages."""
        stages = [
            StageSpec(name=f"z_{i}", prompt="p", model="m")
            for i in range(10)
        ]
        p = PipelineSpec(pipeline_id="id", task="t", stages=stages)
        names = p.stage_names()
        # Should be in the order they were added, not alphabetical
        assert names == [f"z_{i}" for i in range(10)]

    def test_stage_with_circular_dependency_allowed(self):
        """Circular dependencies are allowed at spec level (validator.py handles semantic check)."""
        s1 = StageSpec(name="a", prompt="p", model="m", deps=["b"])
        s2 = StageSpec(name="b", prompt="p", model="m", deps=["a"])
        p = PipelineSpec(pipeline_id="id", task="t", stages=[s1, s2])
        # Should construct fine; validator will catch the cycle
        assert len(p.stages) == 2

    def test_stage_with_nonexistent_dependency_allowed(self):
        """Stages can reference non-existent dependencies (validator.py will catch this)."""
        s = StageSpec(name="s", prompt="p", model="m", deps=["nonexistent"])
        p = PipelineSpec(pipeline_id="id", task="t", stages=[s])
        # Should construct fine; validator will catch the missing dep
        assert p.get_stage("s").deps == ["nonexistent"]

    def test_json_with_indent_none(self):
        """to_json with indent=None produces compact JSON."""
        p = PipelineSpec(pipeline_id="id", task="t")
        compact = p.to_json(indent=None)
        assert "\n" not in compact
        # Should still roundtrip
        roundtrip = PipelineSpec.from_json(compact)
        assert roundtrip.pipeline_id == "id"

    def test_json_with_custom_indent(self):
        """to_json with custom indent."""
        p = PipelineSpec(pipeline_id="id", task="t")
        formatted = p.to_json(indent=4)
        assert "    " in formatted  # 4-space indent
        roundtrip = PipelineSpec.from_json(formatted)
        assert roundtrip.pipeline_id == "id"

    def test_from_json_with_invalid_json(self):
        """from_json with invalid JSON string raises JSONDecodeError."""
        with pytest.raises(ValueError):  # json.loads raises ValueError for invalid JSON
            PipelineSpec.from_json("not valid json {")

    def test_from_json_with_valid_json_but_missing_fields(self):
        """from_json with valid JSON but missing required fields raises KeyError."""
        with pytest.raises(KeyError) as exc:
            PipelineSpec.from_json('{"pipeline_id": "id"}')  # missing 'task'
        assert "task" in str(exc.value)
