"""
decomposer.py — Pure functions for decomposition prompts and response parsing.

Part 1 of the Decomposer subsystem. The Decomposer turns a plain-English
task into a validated ``PipelineSpec`` by:

  1. Building a prompt for the LLM that contains the capability registry,
     the ``PipelineSpec`` JSON schema, optional few-shot examples, and the
     task itself (``build_decomposition_prompt``).
  2. Parsing the LLM's response — which may be wrapped in `````json``
     fences — into a ``PipelineSpec`` (``parse_decomposition_response``).
  3. On validation failure, building a correction prompt that re-asks with
     the prior attempt verbatim plus the validator's error list
     (``build_correction_prompt``).

Network calls live in Part 2 of the Decomposer; this module is pure and
imports only ``stagehand.spec``, ``stagehand.validator``,
``stagehand.capabilities``, and the standard library — so it is trivially
testable without any LLM in the loop.
"""

from __future__ import annotations

import json
import re
from dataclasses import fields, is_dataclass
from typing import Any, Iterable, List, Optional

from .capabilities import CapabilityRegistry
from .spec import PipelineSpec, StageSpec
from .validator import ValidationError


# ── Errors ─────────────────────────────────────────────────────────────────────

class DecompositionParseError(Exception):
    """
    Raised when raw LLM output cannot be parsed into a ``PipelineSpec``.

    The original ``raw`` response is preserved on the exception so the
    self-correction loop can echo it back to the model.
    """

    def __init__(self, raw: Any, cause: Optional[BaseException] = None) -> None:
        if cause is not None:
            msg = (
                f"Failed to parse decomposition response: "
                f"{cause.__class__.__name__}: {cause}"
            )
        else:
            msg = "Failed to parse decomposition response"
        super().__init__(msg)
        self.raw = raw
        if cause is not None:
            # Preserve the underlying error for ``raise ... from`` chains and
            # tooling that walks ``__cause__``.
            self.__cause__ = cause


# ── Schema introspection ───────────────────────────────────────────────────────

def _dataclass_field_schema(cls: type) -> dict:
    """
    Render a dataclass's field shape as a small, human-readable dict.

    Prefers a class-level ``json_schema()`` classmethod when one is defined,
    otherwise falls back to ``dataclasses.fields()`` and stringifies the
    declared field types. The output is for prompt rendering only — it is
    not a strict JSON Schema.
    """
    json_schema = getattr(cls, "json_schema", None)
    if callable(json_schema):
        try:
            schema = json_schema()
            if isinstance(schema, dict):
                return schema
        except Exception:
            pass
    if not is_dataclass(cls):
        return {}
    out: dict = {}
    for f in fields(cls):
        type_repr = getattr(f.type, "__name__", None) or str(f.type)
        out[f.name] = type_repr
    return out


def _render_pipeline_schema() -> str:
    """Render the ``PipelineSpec`` schema as a fenced JSON code block."""
    schema = {
        "PipelineSpec": _dataclass_field_schema(PipelineSpec),
        "StageSpec": _dataclass_field_schema(StageSpec),
    }
    return "```json\n" + json.dumps(schema, indent=2, default=str) + "\n```"


# ── Prompt builders ────────────────────────────────────────────────────────────

_FINAL_INSTRUCTION = (
    "Return ONLY a JSON object matching the `PipelineSpec` schema. "
    "Do not include prose, explanations, or markdown code fences — output "
    "the JSON object and nothing else."
)


def build_decomposition_prompt(
    task: str,
    registry: CapabilityRegistry,
    examples: Optional[List[Any]] = None,
) -> str:
    """
    Build a deterministic decomposition prompt.

    The prompt has five sections, in order:

      1. Task brief — the original user request.
      2. Capability registry — rendered via ``registry.to_prompt_context()``.
      3. ``PipelineSpec`` JSON schema — introspected from the dataclass.
      4. Few-shot examples — each rendered as a fenced JSON block.
      5. Final ``Return ONLY a JSON object…`` instruction.

    The output is pure-function deterministic: the same inputs always
    produce the same prompt, which makes prompt caching and snapshot tests
    trivial.
    """
    sections: List[str] = []

    # 1. Task brief
    sections.append("# Task")
    sections.append("")
    sections.append(
        "Decompose the following task into a `PipelineSpec` — an ordered "
        "list of agent stages that, when executed in dependency order, "
        "accomplish the task."
    )
    sections.append("")
    sections.append(f"**Task:** {task}")
    sections.append("")

    # 2. Capability registry
    sections.append(registry.to_prompt_context().rstrip())
    sections.append("")

    # 3. PipelineSpec schema
    sections.append("# PipelineSpec Schema")
    sections.append("")
    sections.append(
        "Your output must be a JSON object matching the `PipelineSpec` "
        "schema below. `stages` is a list of `StageSpec` objects."
    )
    sections.append("")
    sections.append(_render_pipeline_schema())
    sections.append("")

    # 4. Few-shot examples
    if examples:
        sections.append("# Examples")
        sections.append("")
        for i, ex in enumerate(examples, 1):
            sections.append(f"## Example {i}")
            sections.append("")
            sections.append("```json")
            sections.append(_example_to_json(ex))
            sections.append("```")
            sections.append("")

    # 5. Final instruction
    sections.append("# Output")
    sections.append("")
    sections.append(_FINAL_INSTRUCTION)

    return "\n".join(sections).rstrip() + "\n"


def build_correction_prompt(
    task: str,
    registry: CapabilityRegistry,
    prior_attempt_json: str,
    errors: Iterable[ValidationError],
) -> str:
    """
    Build a prompt that asks the LLM to fix a previously-rejected
    ``PipelineSpec``.

    Includes the prior attempt verbatim (inside a fenced block) and a
    ``## Validation errors`` section that lists each error's ``message`` and
    ``fix`` so the model has concrete repair instructions.
    """
    sections: List[str] = []

    sections.append("# Task")
    sections.append("")
    sections.append(
        "Your previous decomposition was rejected by the validator. Fix the "
        "errors listed below and return a corrected `PipelineSpec`."
    )
    sections.append("")
    sections.append(f"**Task:** {task}")
    sections.append("")

    sections.append("## Prior attempt")
    sections.append("")
    sections.append("```json")
    sections.append(prior_attempt_json)
    sections.append("```")
    sections.append("")

    sections.append("## Validation errors")
    sections.append("")
    err_list = list(errors)
    if not err_list:
        sections.append(
            "(No errors were reported — return the prior attempt unchanged.)"
        )
    else:
        for err in err_list:
            code = getattr(err, "code", "") or ""
            message = getattr(err, "message", "") or ""
            fix = getattr(err, "fix", "") or ""
            header = f"- **{code}**: {message}" if code else f"- {message}"
            sections.append(header)
            if fix:
                sections.append(f"  - *Fix:* {fix}")
    sections.append("")

    sections.append(registry.to_prompt_context().rstrip())
    sections.append("")

    sections.append("# Output")
    sections.append("")
    sections.append(_FINAL_INSTRUCTION)

    return "\n".join(sections).rstrip() + "\n"


# ── Response parser ────────────────────────────────────────────────────────────

# Match either a ```json … ``` or a bare ``` … ``` block. Tolerant of
# leading/trailing whitespace and an optional ``json`` language tag.
_FENCE_RE = re.compile(
    r"\A\s*```(?:[ \t]*json)?[ \t]*\r?\n?(?P<body>.*?)\r?\n?```\s*\Z",
    re.DOTALL | re.IGNORECASE,
)


def _strip_fences(raw: str) -> str:
    """Strip surrounding `````json`` / bare ``````` fences if present."""
    m = _FENCE_RE.match(raw)
    if m:
        return m.group("body")
    return raw


def parse_decomposition_response(raw: str) -> PipelineSpec:
    """
    Parse a raw LLM response into a ``PipelineSpec``.

    Handles three common envelope shapes:

      * Bare JSON: ``{"pipeline_id": ...}``
      * Fenced JSON: `````json\\n{...}\\n`````
      * Bare-fenced JSON: `````\\n{...}\\n`````

    Surrounding whitespace is tolerated. Any failure (bad JSON, missing
    required fields, wrong shape) is re-raised as
    ``DecompositionParseError`` with the original ``raw`` response attached
    so the self-correction loop can echo it back to the model.
    """
    if not isinstance(raw, str):
        raise DecompositionParseError(
            raw, TypeError(f"expected str, got {type(raw).__name__}")
        )

    body = _strip_fences(raw).strip()
    if not body:
        raise DecompositionParseError(raw, ValueError("empty response"))

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise DecompositionParseError(raw, exc) from exc

    try:
        return PipelineSpec.from_dict(data)
    except (TypeError, KeyError, ValueError) as exc:
        raise DecompositionParseError(raw, exc) from exc


# ── Internal helpers ───────────────────────────────────────────────────────────

def _example_to_json(example: Any) -> str:
    """
    Render a few-shot example as JSON.

    Accepts a ``PipelineSpec`` (preferred), a dict, or any object with a
    ``to_dict`` method. Falls back to ``str(example)`` when nothing else
    works — examples are advisory, so we never raise from here.
    """
    if isinstance(example, PipelineSpec):
        return example.to_json(indent=2)
    if hasattr(example, "to_dict") and callable(example.to_dict):
        try:
            return json.dumps(example.to_dict(), indent=2, default=str)
        except Exception:
            pass
    if isinstance(example, dict):
        return json.dumps(example, indent=2, default=str)
    return str(example)


__all__ = [
    "DecompositionParseError",
    "build_decomposition_prompt",
    "build_correction_prompt",
    "parse_decomposition_response",
]
