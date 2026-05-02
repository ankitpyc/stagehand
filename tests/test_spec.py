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
