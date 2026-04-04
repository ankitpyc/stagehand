"""Tests for providers — prompt rendering, error handling (LLMs mocked)."""

import os
import pytest

os.environ["STAGEHAND_DIR"] = "/tmp/stagehand-test-providers"

from stagehand.providers.claude import claude_stage, _render as claude_render
from stagehand.providers.openai import _render as openai_render
from stagehand.providers.gemini import _render as gemini_render
from stagehand.providers.http import http_stage, _render as http_render


# ── Prompt rendering ───────────────────────────────────────────────────────────

class TestPromptRendering:
    def test_renders_simple_key(self):
        result = claude_render("Hello {name}", {"name": "Ankit"})
        assert result == "Hello Ankit"

    def test_renders_nested_key(self):
        result = claude_render("Title: {fetch[title]}", {"fetch": {"title": "My Post"}})
        assert result == "Title: My Post"

    def test_raises_on_missing_key(self):
        with pytest.raises(KeyError) as exc:
            claude_render("Hello {missing_key}", {"name": "Ankit"})
        assert "missing_key" in str(exc.value)
        assert "name" in str(exc.value)  # shows available keys

    def test_all_providers_raise_on_missing_key(self):
        ctx = {"fetch": "data"}
        for render_fn in [claude_render, openai_render, gemini_render, http_render]:
            with pytest.raises(KeyError):
                render_fn("Reference to {nonexistent}", ctx)

    def test_context_keys_shown_in_error(self):
        ctx = {"fetch": "a", "generate": "b"}
        with pytest.raises(KeyError) as exc:
            claude_render("{missing}", ctx)
        err = str(exc.value)
        assert "fetch" in err
        assert "generate" in err


# ── claude_stage ───────────────────────────────────────────────────────────────

class TestClaudeStage:
    def test_returns_callable(self):
        fn = claude_stage("Write about: {topic}")
        assert callable(fn)

    def test_cli_backend_raises_on_bad_command(self, monkeypatch):
        """Test CLI backend error handling (mocked subprocess)."""
        import subprocess
        from stagehand.providers import claude as claude_mod

        # Force CLI backend (pretend SDK is not installed)
        monkeypatch.setattr(claude_mod, "_sdk_available", lambda: False)

        def mock_run(*args, **kwargs):
            class Result:
                returncode = 1
                stdout = ""
                stderr = "some error"
            return Result()

        monkeypatch.setattr(subprocess, "run", mock_run)

        fn = claude_stage("Hello {name}")
        with pytest.raises(RuntimeError, match="claude CLI failed"):
            fn({"name": "world"})

    def test_cli_backend_returns_stdout(self, monkeypatch):
        """Test CLI backend returns stripped stdout on success."""
        import subprocess
        from stagehand.providers import claude as claude_mod

        monkeypatch.setattr(claude_mod, "_sdk_available", lambda: False)

        def mock_run(*args, **kwargs):
            class Result:
                returncode = 0
                stdout = "  Hello Ankit  \n"
                stderr = ""
            return Result()

        monkeypatch.setattr(subprocess, "run", mock_run)

        fn = claude_stage("Hello {name}")
        result = fn({"name": "Ankit"})
        assert result == "Hello Ankit"

    def test_prompt_template_rendered_before_call(self, monkeypatch):
        """Verify the rendered prompt is passed to subprocess, not the template."""
        import subprocess
        from stagehand.providers import claude as claude_mod

        monkeypatch.setattr(claude_mod, "_sdk_available", lambda: False)
        captured = {}

        def mock_run(cmd, *args, **kwargs):
            captured["prompt"] = cmd[2]  # claude -p <prompt>
            class Result:
                returncode = 0
                stdout = "ok"
                stderr = ""
            return Result()

        monkeypatch.setattr(subprocess, "run", mock_run)

        fn = claude_stage("Summarize: {data}")
        fn({"data": "actual content"})
        assert captured["prompt"] == "Summarize: actual content"


# ── http_stage ─────────────────────────────────────────────────────────────────

class TestHttpStage:
    def test_returns_callable(self):
        fn = http_stage("GET", "https://example.com/{id}")
        assert callable(fn)

    def test_url_rendered_from_context(self, monkeypatch):
        import urllib.request
        from stagehand.providers import http as http_mod

        opened_urls = []

        class MockResponse:
            def read(self): return b'{"ok": true}'
            def __enter__(self): return self
            def __exit__(self, *a): pass

        def mock_urlopen(req, timeout=None):
            opened_urls.append(req.full_url)
            return MockResponse()

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

        fn = http_stage("GET", "https://api.example.com/users/{user_id}")
        fn({"user_id": "42"})
        assert "42" in opened_urls[0]

    def test_returns_parsed_json(self, monkeypatch):
        import urllib.request

        class MockResponse:
            def read(self): return b'{"name": "Ankit"}'
            def __enter__(self): return self
            def __exit__(self, *a): pass

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: MockResponse())

        fn = http_stage("GET", "https://example.com/")
        result = fn({})
        assert result == {"name": "Ankit"}

    def test_raises_on_http_error(self, monkeypatch):
        import urllib.request
        import urllib.error
        import io

        def mock_urlopen(*a, **kw):
            # HTTPError fp argument provides the response body
            raise urllib.error.HTTPError("url", 404, "Not Found", {}, io.BytesIO(b"not found"))

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

        fn = http_stage("GET", "https://example.com/")
        with pytest.raises(RuntimeError, match="HTTP 404"):
            fn({})
