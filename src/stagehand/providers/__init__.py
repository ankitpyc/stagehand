"""
providers — Stage helper factories for common integrations.

Each provider returns a Callable[[dict], str] usable as a stage function.
All providers share the same interface:

    p.stage("step", claude_stage("Summarize: {fetch}"), deps=["fetch"])
    p.stage("step", openai_stage("Summarize: {fetch}"), deps=["fetch"])
    p.stage("step", gemini_stage("Summarize: {fetch}"), deps=["fetch"])
    p.stage("step", http_stage("GET", "https://api.example.com/{fetch[id]}"), deps=["fetch"])

prompt_template / url_template use Python's str.format_map(ctx) —
reference any key in the pipeline context by name.
Missing keys raise a clear KeyError instead of silently passing {key} forward.
"""

from .claude import claude_stage
from .openai import openai_stage
from .gemini import gemini_stage
from .http import http_stage

__all__ = ["claude_stage", "openai_stage", "gemini_stage", "http_stage"]
