"""
gemini_stage — Run a Gemini prompt as a pipeline stage.

Requires: pip install stagehand[gemini]
API key:  GEMINI_API_KEY environment variable
"""

from __future__ import annotations

from typing import Callable, Dict


def gemini_stage(
    prompt_template: str,
    model: str = "gemini-2.0-flash",
    timeout: int = 300,
    max_tokens: int = 4096,
    system: str = None,
) -> Callable[[Dict], str]:
    """
    Returns a stage function that calls Gemini with the given prompt.

    Args:
        prompt_template: Python format string. Reference ctx keys as {stage_name}.
        model:           Model ID (default: gemini-2.0-flash).
        timeout:         Seconds before the call is abandoned.
        max_tokens:      Max output tokens.
        system:          Optional system instruction.

    Example:
        p.stage("draft",
            gemini_stage("Write a post about:\n\n{research}"),
            deps=["research"])
    """

    def fn(ctx: Dict) -> str:
        try:
            import google.generativeai as genai
        except ImportError:
            raise ImportError("google-generativeai not installed. Run: pip install stagehand[gemini]")
        import os

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY environment variable not set")

        genai.configure(api_key=api_key)

        prompt = _render(prompt_template, ctx)

        gen_config = genai.GenerationConfig(max_output_tokens=max_tokens)
        m = genai.GenerativeModel(
            model_name=model,
            generation_config=gen_config,
            system_instruction=system,
        )
        resp = m.generate_content(prompt, request_options={"timeout": timeout})
        return resp.text.strip()

    fn.__name__ = "gemini_stage"
    fn.__doc__ = f"Gemini ({model}): {prompt_template[:60]}..."
    return fn


def _render(template: str, ctx: dict) -> str:
    try:
        return template.format_map(ctx)
    except KeyError as e:
        available = ", ".join(sorted(ctx.keys()))
        raise KeyError(f"gemini_stage template references missing key {e}. Available context keys: {available}") from None
