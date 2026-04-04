"""
openai_stage — Run an OpenAI prompt as a pipeline stage.

Requires: pip install stagehand[openai]
API key:  OPENAI_API_KEY environment variable
"""

from __future__ import annotations

from typing import Callable, Dict, Optional


def openai_stage(
    prompt_template: str,
    model: str = "gpt-4o",
    timeout: int = 300,
    max_tokens: int = 4096,
    system: str = None,
) -> Callable[[Dict], str]:
    """
    Returns a stage function that calls OpenAI with the given prompt.

    Args:
        prompt_template: Python format string. Reference ctx keys as {stage_name}.
        model:           Model ID (default: gpt-4o).
        timeout:         Seconds before the call is abandoned.
        max_tokens:      Max tokens in the response.
        system:          Optional system prompt.

    Example:
        p.stage("draft",
            openai_stage("Summarize this research:\n\n{fetch}"),
            deps=["fetch"])
    """
    def fn(ctx: Dict) -> str:
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "openai package not installed. Run: pip install stagehand[openai]"
            )

        prompt = _render(prompt_template, ctx)
        client = OpenAI(timeout=timeout)

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )
        return resp.choices[0].message.content.strip()

    fn.__name__ = "openai_stage"
    fn.__doc__ = f"OpenAI ({model}): {prompt_template[:60]}..."
    return fn


def _render(template: str, ctx: dict) -> str:
    try:
        return template.format_map(ctx)
    except KeyError as e:
        available = ", ".join(sorted(ctx.keys()))
        raise KeyError(
            f"openai_stage template references missing key {e}. "
            f"Available context keys: {available}"
        ) from None
