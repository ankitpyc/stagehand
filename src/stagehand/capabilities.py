"""
capabilities.py — Catalog of stages/functions Claude can use when decomposing a task.

A `CapabilityRegistry` holds named capabilities (built-in providers + user-registered
functions) with descriptions and parameter schemas. The decomposer renders the
registry as prompt context so Claude knows what stages it can compose into a
pipeline.

Design:
- The registry is purely data + lookup; no execution happens here.
- Built-in capabilities ship in a bundled `capabilities_default.json` next to this
  module — load with `CapabilityRegistry.load_default()`.
- User-defined Python callables are registered with `register_function()`; the
  callable is kept in-memory so the agent runner can resolve and invoke it.
- `to_prompt_context()` renders the catalog as markdown for inclusion in the
  decomposer prompt.

Example:
    reg = CapabilityRegistry.load_default()
    reg.register_function(
        "scrape",
        scrape_fn,
        description="Fetch and clean a URL",
        params=[CapabilityParam("url", required=True)],
    )
    prompt = reg.to_prompt_context()
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional


# Bundled default capability catalog — sits next to this module so it's
# packaged into the wheel automatically.
_DEFAULT_FILE = Path(__file__).parent / "capabilities_default.json"


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class CapabilityParam:
    """A single parameter on a capability."""
    name: str
    type: str = "string"
    required: bool = False
    description: str = ""
    default: Any = None


@dataclass
class Capability:
    """
    A single named capability (a stage helper or a user-registered function).

    `kind` is informational and helps Claude choose appropriately:
      - "provider": built-in stage helper (claude_stage, http_stage, ...)
      - "function": user-registered Python callable
      - "tool":     external tool exposed via the registry
    """
    name: str
    description: str
    kind: str = "function"
    params: List[CapabilityParam] = field(default_factory=list)
    example: Optional[str] = None
    when_to_use: Optional[str] = None
    # In-memory only; never serialized to JSON.
    fn: Optional[Callable] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "params": [asdict(p) for p in self.params],
        }
        if self.example is not None:
            d["example"] = self.example
        if self.when_to_use is not None:
            d["when_to_use"] = self.when_to_use
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Capability":
        raw_params = d.get("params", []) or []
        params = [
            p if isinstance(p, CapabilityParam) else CapabilityParam(**p)
            for p in raw_params
        ]
        return cls(
            name=d["name"],
            description=d["description"],
            kind=d.get("kind", "function"),
            params=params,
            example=d.get("example"),
            when_to_use=d.get("when_to_use"),
        )


# ── Registry ───────────────────────────────────────────────────────────────────

class CapabilityRegistry:
    """
    In-memory registry of capabilities the decomposer can invoke as pipeline
    stages.

    The registry is order-preserving (insertion order) so the prompt context
    renders capabilities in a stable, predictable order.
    """

    def __init__(self) -> None:
        self._caps: Dict[str, Capability] = {}

    # ── Construction ──────────────────────────────────────────────────────────

    @classmethod
    def load_default(cls) -> "CapabilityRegistry":
        """Load the bundled default capabilities (built-in stage providers)."""
        return cls.load_file(_DEFAULT_FILE)

    @classmethod
    def load_file(cls, path) -> "CapabilityRegistry":
        """Load capabilities from a JSON file at `path`."""
        path = Path(path)
        data = json.loads(path.read_text())
        reg = cls()
        for cap in data.get("capabilities", []):
            reg.register(Capability.from_dict(cap))
        return reg

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, capability: Capability) -> None:
        """Register a Capability. Raises ValueError on name collision."""
        if capability.name in self._caps:
            raise ValueError(
                f"Capability '{capability.name}' already registered"
            )
        self._caps[capability.name] = capability

    def register_function(
        self,
        name: str,
        fn: Callable,
        description: str,
        params: Optional[List[CapabilityParam]] = None,
        example: Optional[str] = None,
        when_to_use: Optional[str] = None,
    ) -> None:
        """
        Register a Python callable as a capability. The callable is kept
        in-memory so the agent runner can resolve and invoke it.
        """
        cap = Capability(
            name=name,
            description=description,
            kind="function",
            params=list(params) if params else [],
            example=example,
            when_to_use=when_to_use,
            fn=fn,
        )
        self.register(cap)

    # ── Lookup ────────────────────────────────────────────────────────────────

    def get(self, name: str) -> Capability:
        """Return the Capability for `name`. Raises KeyError if not found."""
        if name not in self._caps:
            available = ", ".join(sorted(self._caps)) or "<empty>"
            raise KeyError(
                f"Unknown capability '{name}'. Available: {available}"
            )
        return self._caps[name]

    def names(self) -> List[str]:
        """Return registered capability names in insertion order."""
        return list(self._caps.keys())

    def __contains__(self, name: object) -> bool:
        return name in self._caps

    def __len__(self) -> int:
        return len(self._caps)

    def __iter__(self) -> Iterator[Capability]:
        return iter(self._caps.values())

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict representation of the registry."""
        return {
            "version": 1,
            "capabilities": [c.to_dict() for c in self._caps.values()],
        }

    def save(self, path) -> None:
        """Atomically write the registry to a JSON file at `path`."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2))
        tmp.replace(path)

    # ── Prompt rendering ──────────────────────────────────────────────────────

    def to_prompt_context(self) -> str:
        """
        Render the registry as markdown for inclusion in the decomposer prompt.

        The output is deterministic — capabilities render in insertion order so
        identical registries always produce identical prompts (good for caching
        and snapshot testing).
        """
        if not self._caps:
            return "No capabilities registered.\n"

        lines: List[str] = ["# Available Capabilities", ""]
        for cap in self._caps.values():
            lines.append(f"## `{cap.name}` ({cap.kind})")
            lines.append(cap.description)
            if cap.when_to_use:
                lines.append("")
                lines.append(f"**When to use:** {cap.when_to_use}")
            if cap.params:
                lines.append("")
                lines.append("**Parameters:**")
                for p in cap.params:
                    req = "required" if p.required else "optional"
                    desc = f" — {p.description}" if p.description else ""
                    default = (
                        f" (default: `{p.default}`)" if p.default is not None else ""
                    )
                    lines.append(
                        f"- `{p.name}` ({p.type}, {req}){default}{desc}"
                    )
            if cap.example:
                lines.append("")
                lines.append(f"**Example:** `{cap.example}`")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
