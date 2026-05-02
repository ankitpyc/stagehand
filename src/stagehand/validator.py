"""
validator.py — Pre-execution validation for a PipelineSpec.

Runs nine independent checks against a pipeline spec produced by the
decomposer (or hand-written) before it ever reaches the executor. Returns a
structured ``ValidationResult`` with one ``ValidationError`` per failure,
each carrying a stable ``code``, a human ``message``, the ``path`` inside the
spec where the problem lives, and a ``fix`` hint that the self-correction
loop can feed back to Claude.

Spec shape (dict or duck-typed object with the same attributes):

    {
        "pipeline_id": "research-langgraph",
        "task":        "Research LangGraph and write a Substack post",
        "stages": [
            {
                "name":     "search_papers",
                "function": "claude_stage",
                "prompt":   "Find papers about: {task}",
                "deps":     [],
                "model":    "claude-sonnet-4-5",   # optional
                "timeout":  60,                     # optional
            },
            ...
        ],
    }

The nine rules (codes are stable — the decomposer prompt references them):

    E001  stages_present       — pipeline has at least one stage
    E002  unique_names         — all stage names are unique
    E003  valid_names          — stage names are non-empty snake_case identifiers
    E004  required_fields      — every stage has the required keys (name, function)
    E005  deps_exist           — every dep references a defined stage
    E006  no_self_dep          — no stage depends on itself
    E007  no_cycles            — the dependency graph is acyclic
    E008  function_in_registry — stage.function is known to the capability registry
    E009  prompt_refs_upstream — prompt placeholders only reference upstream stages,
                                 the literal ``task``, or top-level pipeline keys

Zero external deps — stdlib only, like the rest of the engine.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set


# ── Public types ───────────────────────────────────────────────────────────────

@dataclass
class ValidationError:
    """A single validation failure with a stable code and a fix hint."""
    code: str
    message: str
    path: str = ""
    fix: str = ""


@dataclass
class ValidationResult:
    """Aggregate result. Truthy iff the spec is valid."""
    ok: bool
    errors: List[ValidationError] = field(default_factory=list)

    def __bool__(self) -> bool:  # allow `if result: ...`
        return self.ok

    def codes(self) -> List[str]:
        return [e.code for e in self.errors]


# ── Constants ──────────────────────────────────────────────────────────────────

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_REQUIRED_STAGE_FIELDS = ("name", "function")
# Pipeline-level keys that prompts may legitimately reference even though they
# are not stage outputs. ``task`` is the original user request; the others are
# common context keys threaded through ``Pipeline.run(context=...)``.
_RESERVED_PROMPT_KEYS = frozenset({"task", "pipeline_id", "context"})


# ── Public API ─────────────────────────────────────────────────────────────────

def validate(
    spec: Any,
    registry: Optional[Any] = None,
) -> ValidationResult:
    """
    Run all nine validation rules against ``spec``.

    ``spec`` may be a dict or any object whose attributes match the spec
    shape (e.g. a dataclass-based ``PipelineSpec``). ``registry`` is optional;
    if provided it must expose a ``has(name)`` method or behave like a
    mapping/iterable of capability names. When omitted, rule E008 is skipped.

    Returns a :class:`ValidationResult` with every failure encountered. Rules
    are independent: one failure does not short-circuit the others, so
    Claude's self-correction loop sees the full picture in a single pass.
    """
    errors: List[ValidationError] = []

    spec_dict = _as_dict(spec)
    stages_raw = spec_dict.get("stages") or []
    stages = [_as_dict(s) for s in stages_raw]

    # Each rule appends zero or more ValidationErrors. Rules that depend on
    # earlier ones (e.g. cycle detection needs deps to exist) guard themselves.
    _rule_stages_present(stages, errors)
    _rule_unique_names(stages, errors)
    _rule_valid_names(stages, errors)
    _rule_required_fields(stages, errors)

    name_set = {s.get("name") for s in stages if isinstance(s.get("name"), str)}
    _rule_deps_exist(stages, name_set, errors)
    _rule_no_self_dep(stages, errors)
    _rule_no_cycles(stages, name_set, errors)

    _rule_function_in_registry(stages, registry, errors)
    _rule_prompt_refs_upstream(stages, name_set, errors)

    return ValidationResult(ok=not errors, errors=errors)


# ── Rules ──────────────────────────────────────────────────────────────────────

def _rule_stages_present(stages: List[dict], errors: List[ValidationError]) -> None:
    """E001 — pipeline must declare at least one stage."""
    if not stages:
        errors.append(ValidationError(
            code="E001",
            message="Pipeline has no stages.",
            path="stages",
            fix="Add at least one stage with a name, function, and prompt.",
        ))


def _rule_unique_names(stages: List[dict], errors: List[ValidationError]) -> None:
    """E002 — stage names must be unique."""
    seen: Set[str] = set()
    dupes: List[str] = []
    for s in stages:
        name = s.get("name")
        if not isinstance(name, str):
            continue
        if name in seen and name not in dupes:
            dupes.append(name)
        seen.add(name)
    for name in dupes:
        errors.append(ValidationError(
            code="E002",
            message=f"Stage name '{name}' is used more than once.",
            path=f"stages[name={name}]",
            fix=f"Rename the duplicate(s) so every stage.name is unique.",
        ))


def _rule_valid_names(stages: List[dict], errors: List[ValidationError]) -> None:
    """E003 — names must be non-empty, lowercase snake_case identifiers."""
    for i, s in enumerate(stages):
        name = s.get("name")
        if not isinstance(name, str) or not name:
            errors.append(ValidationError(
                code="E003",
                message=f"Stage at index {i} has an empty or non-string name.",
                path=f"stages[{i}].name",
                fix="Set stage.name to a non-empty snake_case string (e.g. 'fetch_papers').",
            ))
            continue
        if not _NAME_RE.match(name):
            errors.append(ValidationError(
                code="E003",
                message=(
                    f"Stage name '{name}' is not a valid snake_case identifier "
                    "(lowercase letters, digits, underscores; must start with a letter)."
                ),
                path=f"stages[{i}].name",
                fix=f"Rename '{name}' to snake_case, e.g. '{_to_snake_suggestion(name)}'.",
            ))


def _rule_required_fields(stages: List[dict], errors: List[ValidationError]) -> None:
    """E004 — every stage must declare the required keys."""
    for i, s in enumerate(stages):
        for key in _REQUIRED_STAGE_FIELDS:
            value = s.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                errors.append(ValidationError(
                    code="E004",
                    message=f"Stage at index {i} is missing required field '{key}'.",
                    path=f"stages[{i}].{key}",
                    fix=f"Add a non-empty '{key}' to this stage.",
                ))


def _rule_deps_exist(
    stages: List[dict],
    name_set: Set[str],
    errors: List[ValidationError],
) -> None:
    """E005 — every dep must reference a stage that's actually defined."""
    for s in stages:
        name = s.get("name", "?")
        for dep in _deps_of(s):
            if dep not in name_set:
                errors.append(ValidationError(
                    code="E005",
                    message=f"Stage '{name}' depends on undefined stage '{dep}'.",
                    path=f"stages[name={name}].deps",
                    fix=f"Either add a stage named '{dep}' or remove it from '{name}'.deps.",
                ))


def _rule_no_self_dep(stages: List[dict], errors: List[ValidationError]) -> None:
    """E006 — a stage cannot depend on itself."""
    for s in stages:
        name = s.get("name")
        if isinstance(name, str) and name in _deps_of(s):
            errors.append(ValidationError(
                code="E006",
                message=f"Stage '{name}' depends on itself.",
                path=f"stages[name={name}].deps",
                fix=f"Remove '{name}' from its own deps list.",
            ))


def _rule_no_cycles(
    stages: List[dict],
    name_set: Set[str],
    errors: List[ValidationError],
) -> None:
    """E007 — the dependency graph must be a DAG."""
    # Build adjacency from dep → dependents-of (who depends on dep).
    # We walk a DFS from each node and report the first cycle we hit, with the
    # offending edge in the path. Only consider deps that resolve to a real
    # stage name — otherwise E005 already covered it.
    graph: Dict[str, List[str]] = {n: [] for n in name_set}
    for s in stages:
        name = s.get("name")
        if not isinstance(name, str):
            continue
        for dep in _deps_of(s):
            # Skip self-deps (E006 handles them) to avoid duplicate noise here.
            if dep in name_set and dep != name:
                graph[name].append(dep)

    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[str, int] = {n: WHITE for n in graph}
    reported: Set[str] = set()

    def dfs(node: str, stack: List[str]) -> None:
        color[node] = GRAY
        stack.append(node)
        for nxt in graph.get(node, []):
            if color.get(nxt) == GRAY:
                # Found a back-edge — extract the cycle.
                if nxt in stack:
                    cycle = stack[stack.index(nxt):] + [nxt]
                else:
                    cycle = [nxt, node, nxt]
                key = "->".join(sorted(set(cycle)))
                if key not in reported:
                    reported.add(key)
                    errors.append(ValidationError(
                        code="E007",
                        message=f"Dependency cycle detected: {' -> '.join(cycle)}.",
                        path="stages[*].deps",
                        fix="Break the cycle by removing one dep along the path.",
                    ))
            elif color.get(nxt) == WHITE:
                dfs(nxt, stack)
        stack.pop()
        color[node] = BLACK

    for node in list(graph.keys()):
        if color[node] == WHITE:
            dfs(node, [])


def _rule_function_in_registry(
    stages: List[dict],
    registry: Optional[Any],
    errors: List[ValidationError],
) -> None:
    """E008 — stage.function must be a known capability (when a registry is supplied)."""
    if registry is None:
        return
    known = _registry_names(registry)
    if known is None:
        # Registry of unknown shape — be conservative and skip rather than
        # produce false positives.
        return
    for s in stages:
        fn = s.get("function")
        name = s.get("name", "?")
        if isinstance(fn, str) and fn and fn not in known:
            sample = ", ".join(sorted(known)[:5]) or "<empty>"
            errors.append(ValidationError(
                code="E008",
                message=f"Stage '{name}' uses unknown function '{fn}'.",
                path=f"stages[name={name}].function",
                fix=f"Pick a capability from the registry (e.g. {sample}) or register '{fn}' first.",
            ))


def _rule_prompt_refs_upstream(
    stages: List[dict],
    name_set: Set[str],
    errors: List[ValidationError],
) -> None:
    """
    E009 — every ``{placeholder}`` in stage.prompt must reference either an
    upstream stage (transitive dep), a reserved key, or a top-level subscript
    of one of those (e.g. ``{fetch[title]}``).
    """
    upstream_map = _upstream_map(stages, name_set)

    for s in stages:
        prompt = s.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            continue
        name = s.get("name", "?")
        upstream = upstream_map.get(name, set()) | _RESERVED_PROMPT_KEYS

        for placeholder in _extract_placeholders(prompt):
            root = placeholder.split("[", 1)[0].split(".", 1)[0]
            if root in upstream:
                continue
            errors.append(ValidationError(
                code="E009",
                message=(
                    f"Stage '{name}' prompt references '{{{placeholder}}}' which is "
                    "not an upstream stage or reserved key."
                ),
                path=f"stages[name={name}].prompt",
                fix=(
                    f"Either add '{root}' to '{name}'.deps (if it's a stage) "
                    "or remove the placeholder."
                ),
            ))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _as_dict(obj: Any) -> dict:
    """Coerce a spec object (dict or dataclass-like) to a plain dict."""
    if isinstance(obj, dict):
        return obj
    if obj is None:
        return {}
    # Prefer asdict() if available (dataclasses), else __dict__.
    try:
        from dataclasses import asdict, is_dataclass
        if is_dataclass(obj):
            return asdict(obj)
    except Exception:
        pass
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return {}


def _deps_of(stage: dict) -> List[str]:
    deps = stage.get("deps") or []
    if isinstance(deps, str):  # tolerate scalar dep
        return [deps]
    return [d for d in deps if isinstance(d, str)]


def _upstream_map(stages: List[dict], name_set: Set[str]) -> Dict[str, Set[str]]:
    """
    For each stage, compute the set of *transitive* dependency names. Used by
    E009 to check that prompt placeholders reference reachable upstream output.
    Cycles are tolerated here (E007 handles them); we just stop at visited.
    """
    direct: Dict[str, List[str]] = {}
    for s in stages:
        name = s.get("name")
        if isinstance(name, str):
            direct[name] = [d for d in _deps_of(s) if d in name_set]

    upstream: Dict[str, Set[str]] = {}

    def collect(node: str, seen: Set[str]) -> Set[str]:
        if node in upstream:
            return upstream[node]
        if node in seen:
            return set()
        seen.add(node)
        result: Set[str] = set()
        for d in direct.get(node, []):
            result.add(d)
            result |= collect(d, seen)
        upstream[node] = result
        return result

    for n in direct:
        collect(n, set())
    return upstream


def _extract_placeholders(template: str) -> List[str]:
    """
    Pull placeholder names out of an ``str.format``-style template. Skips
    escaped braces (``{{`` / ``}}``) and ignores positional/empty fields.
    """
    placeholders: List[str] = []
    try:
        for literal_text, field_name, format_spec, conversion in string.Formatter().parse(template):
            if field_name is None or field_name == "":
                continue
            placeholders.append(field_name)
    except ValueError:
        # Malformed template — treat as no placeholders; the stage will fail
        # at render time with a clearer error than we can give here.
        return []
    return placeholders


def _registry_names(registry: Any) -> Optional[Set[str]]:
    """
    Best-effort extraction of capability names from a registry. Returns None
    if we can't introspect it, in which case rule E008 is skipped.
    """
    # Preferred: explicit membership API.
    if hasattr(registry, "has") and callable(registry.has):
        # Wrap as a proxy set-like that answers __contains__ via has().
        # We can't enumerate, but we can still check membership per-stage.
        class _HasProxy:
            def __contains__(self, item): return bool(registry.has(item))
            def __iter__(self): return iter(())  # sample list will be empty
            def __len__(self): return 0
        return _HasProxy()  # type: ignore[return-value]
    if hasattr(registry, "names") and callable(registry.names):
        try:
            return set(registry.names())
        except Exception:
            return None
    if isinstance(registry, dict):
        return set(registry.keys())
    if isinstance(registry, (set, frozenset)):
        return set(registry)
    if isinstance(registry, (list, tuple)):
        return set(registry)
    if isinstance(registry, Iterable):
        try:
            return set(registry)  # type: ignore[arg-type]
        except Exception:
            return None
    return None


def _to_snake_suggestion(name: str) -> str:
    """Best-effort suggestion: lowercase, swap non-alphanum to underscore."""
    s = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    if not s or not s[0].isalpha():
        s = "stage_" + s
    return s or "stage_1"
