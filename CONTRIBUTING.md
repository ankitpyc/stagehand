# Contributing to Stagehand

Thanks for your interest in contributing! Stagehand is a small, focused project
with a strong "zero runtime dependencies for the core engine" philosophy.
Please keep that in mind when proposing changes.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Dev Setup](#dev-setup)
- [Project Layout](#project-layout)
- [Running Tests](#running-tests)
- [Coding Conventions](#coding-conventions)
- [Submitting a Pull Request](#submitting-a-pull-request)
- [Reporting Bugs / Requesting Features](#reporting-bugs--requesting-features)

## Code of Conduct

Be kind, be specific, assume good intent. Discussions stay on technical merit.
Harassment of any kind will not be tolerated.

## Dev Setup

Stagehand requires **Python 3.9+**.

```bash
# 1. Fork and clone
git clone https://github.com/<your-username>/stagehand.git
cd stagehand

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

# 3. Install in editable mode with dev extras
pip install -e ".[dev]"

# 4. (Optional) Install AI provider extras you want to test against
pip install -e ".[claude]"           # or .[openai], .[gemini], .[all]
```

The core engine has **zero runtime dependencies** — please do not add any to
`[project].dependencies` in `pyproject.toml`. New optional integrations belong
under `[project.optional-dependencies]`.

## Project Layout

```
src/stagehand/
├── pipeline.py        # Static Pipeline API
├── checkpoint.py      # Atomic checkpointing with file locking
├── registry.py        # Capability registry
├── cli.py             # `stagehand` CLI entrypoint
├── dashboard.py       # Visual pipeline dashboard
└── providers/         # claude_stage, openai_stage, gemini_stage, http_stage
tests/                 # pytest test suite
examples/              # End-to-end usage examples
```

See [`PLAN.md`](PLAN.md) for the broader architecture and roadmap.

## Running Tests

Stagehand uses **pytest**. Run the full suite from the repo root:

```bash
pytest tests/ -v
```

With coverage:

```bash
pytest tests/ -v --cov=stagehand --cov-report=term-missing
```

Run a single test file or test:

```bash
pytest tests/test_pipeline.py -v
pytest tests/test_pipeline.py::test_pipeline_runs_in_order -v
```

### Writing tests

- Add tests next to the existing ones in `tests/`, named `test_<module>.py`.
- Test the **happy path** plus at least one **edge case**.
- Avoid mocking unless the test would otherwise hit the network or an LLM.
- Provider-specific tests should `pytest.importorskip` the provider SDK so the
  suite still passes for contributors who haven't installed the optional extras.

## Coding Conventions

- **Style:** match the surrounding code. The repo uses standard Python
  formatting (4-space indent, double quotes, type hints where helpful).
- **Stdlib-only core:** anything in `src/stagehand/` outside `providers/` must
  not import third-party packages.
- **Public API stability:** the existing `Pipeline` API is considered stable.
  Breaking changes need a strong justification and a deprecation path.
- **Docstrings:** every public function/class gets a one-line summary; expand
  if the behavior is non-obvious.
- **Imports:** stdlib first, then third-party, then local — separated by blank
  lines.
- **Commits:** use [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`).

## Submitting a Pull Request

1. **Open an issue first** for anything larger than a small bug fix or doc
   tweak — it saves wasted work if the direction needs adjustment.
2. **Branch off `main`** with a descriptive name:
   `feat/agent-runner-timeouts`, `fix/checkpoint-race`, `docs/contributing`.
3. **Keep PRs focused.** One logical change per PR. If you need to refactor
   along the way, do it in a separate PR.
4. **Add or update tests** for any behavior change.
5. **Update docs** (`README.md`, `PLAN.md`, docstrings) when the change is
   user-visible.
6. **Run the full test suite locally** before pushing:
   ```bash
   pytest tests/ -v
   ```
7. **Fill out the PR template** — it's at
   [`.github/pull_request_template.md`](.github/pull_request_template.md) and
   includes a checklist for tests, docs, and secrets.
8. **No secrets in commits.** Never commit API keys, tokens, or credentials —
   not even in test fixtures or comments.

### PR review expectations

- Maintainers aim to leave a first review within a few days.
- Address review comments by pushing new commits (don't force-push during
  review unless asked); we'll squash on merge.
- Once approved and CI is green, a maintainer will merge.

## Reporting Bugs / Requesting Features

- **Bugs:** use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md).
  Include Python version, Stagehand version, a minimal repro, and the full
  traceback.
- **Features:** use the
  [feature request template](.github/ISSUE_TEMPLATE/feature_request.md).
  Describe the use case before the proposed API — it's easier to design a good
  API once the problem is concrete.

Thanks for helping make Stagehand better!
