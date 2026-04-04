"""
claude_stage — Run a Claude prompt as a pipeline stage.

Two backends (auto-selected):
  1. anthropic SDK  — if `anthropic` is installed (pip install stagehand[claude])
  2. claude CLI     — fallback to `claude -p` subprocess (Claude Code users)

prompt_template uses str.format_map(ctx) — KeyError on missing keys (no silent pass-through).
"""

from __future__ import annotations

import os
import subprocess
from typing import Callable, Dict, Optional


def claude_stage(
    prompt_template: str,
    model: str = None,
    timeout: int = 300,
    max_tokens: int = 4096,
    system: str = None,
) -> Callable[[Dict], str]:
    """
    Returns a stage function that calls Claude with the given prompt.

    Args:
        prompt_template: Python format string. Reference ctx keys as {stage_name}.
        model:           Model ID (e.g. "claude-sonnet-4-6"). Defaults to provider default.
        timeout:         Seconds before the call is abandoned.
        max_tokens:      Max tokens in the response (SDK backend only).
        system:          Optional system prompt (SDK backend only).

    Example:
        p.stage("draft",
            claude_stage("Write a LinkedIn post about:\n\n{research}"),
            deps=["research"])
    """
    def fn(ctx: Dict) -> str:
        prompt = _render(prompt_template, ctx)
        if _sdk_available():
            return _call_sdk(prompt, model, timeout, max_tokens, system)
        return _call_cli(prompt, model, timeout)

    fn.__name__ = f"claude_stage"
    fn.__doc__ = f"Claude: {prompt_template[:60]}..."
    return fn


def _render(template: str, ctx: dict) -> str:
    try:
        return template.format_map(ctx)
    except KeyError as e:
        available = ", ".join(sorted(ctx.keys()))
        raise KeyError(
            f"claude_stage template references missing key {e}. "
            f"Available context keys: {available}"
        ) from None


def _sdk_available() -> bool:
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def _call_sdk(prompt: str, model: Optional[str], timeout: int, max_tokens: int, system: Optional[str]) -> str:
    import anthropic
    client = anthropic.Anthropic(timeout=timeout)
    kwargs = dict(
        model=model or "claude-sonnet-4-6",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if system:
        kwargs["system"] = system
    msg = client.messages.create(**kwargs)
    return msg.content[0].text.strip()


def _call_cli(prompt: str, model: Optional[str], timeout: int) -> str:
    """Fallback: call `claude -p` subprocess (works inside Claude Code with CLAUDECODE unset)."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    cmd = ["claude", "-p", prompt]
    if model:
        cmd += ["--model", model]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI failed (exit {result.returncode}):\n{result.stderr[:1000]}"
        )
    return result.stdout.strip()
