"""
http_stage — Make an HTTP request as a pipeline stage.

Zero external dependencies — uses urllib.request from stdlib.
Returns parsed JSON (dict/list) if response is JSON, raw text otherwise.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional


def http_stage(
    method: str,
    url_template: str,
    headers: Dict[str, str] = None,
    body_template: str = None,
    timeout: int = 30,
    expect_json: bool = True,
) -> Callable[[Dict], Any]:
    """
    Returns a stage function that makes an HTTP request.

    Args:
        method:          HTTP method (GET, POST, PATCH, DELETE).
        url_template:    URL with optional {ctx_key} placeholders.
        headers:         Static headers dict (e.g. Authorization).
        body_template:   Request body template string (for POST/PATCH).
        timeout:         Seconds before the request is abandoned.
        expect_json:     If True, parse response as JSON. Otherwise return raw text.

    Example:
        p.stage("get_user",
            http_stage("GET", "https://api.example.com/users/{user_id}",
                       headers={"Authorization": "Bearer mytoken"}),
            deps=[])

        p.stage("create_post",
            http_stage("POST", "https://api.example.com/posts",
                       body_template='{{"title": "{title}", "body": "{draft}"}}'),
            deps=["draft"])
    """
    def fn(ctx: Dict) -> Any:
        url = _render(url_template, ctx)
        body = _render(body_template, ctx).encode("utf-8") if body_template else None

        req = urllib.request.Request(url, data=body, method=method.upper())
        req.add_header("Accept", "application/json")
        if body:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"HTTP {e.code} {e.reason} for {method} {url}:\n{body_text[:500]}"
            )

        if expect_json:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw
        return raw

    fn.__name__ = "http_stage"
    fn.__doc__ = f"HTTP {method} {url_template[:60]}"
    return fn


def _render(template: str, ctx: dict) -> str:
    try:
        return template.format_map(ctx)
    except KeyError as e:
        available = ", ".join(sorted(ctx.keys()))
        raise KeyError(
            f"http_stage template references missing key {e}. "
            f"Available context keys: {available}"
        ) from None
