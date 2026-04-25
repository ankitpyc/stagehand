# Stagehand

> Lightweight Python pipeline runner with checkpointing, parallel DAG execution, and first-class AI provider support.

![Status](https://img.shields.io/badge/status-active-brightgreen)
![Python](https://img.shields.io/badge/python-3.9+-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![GitHub last commit](https://img.shields.io/github/last-commit/ankitpyc/stagehand)

## Overview

Stagehand is a minimal Python pipeline framework for multi-step workflows. It gives you checkpointed, resumable pipelines with zero runtime dependencies — so you can compose Python functions and LLM calls into a DAG and never lose work to a crash.

It's the missing layer between "run a script" and "deploy Airflow."

## Features (shipped)

- **Checkpointing** — every stage saves its output atomically; crashes resume from the last completed stage
- **DAG execution** — stages with no dependency on each other run in parallel automatically
- **Retries + timeouts** — per-stage `retry`, exponential backoff, and `timeout` controls
- **Multi-provider stages** — `claude_stage`, `openai_stage`, `gemini_stage`, `http_stage` built in
- **Pipeline registry** — every run is recorded in `~/.stagehand/registry.json` for inspection
- **CLI** — `stagehand list`, `status`, `reset`, `runs`, `dashboard`, `version`
- **Web dashboard** — `stagehand dashboard` serves a self-contained HTML view of all pipelines
- **Zero runtime deps** — stdlib only for the core engine; provider SDKs are optional extras

## Planned (not yet shipped)

The items below are on the roadmap but **not** part of the current release. Track progress in [PLAN.md](PLAN.md).

- `PipelineSpec` + `StageSpec` contract types
- Validator with structured errors
- Capability registry for agent-driven decomposition
- Decomposer + self-correction loop (Claude breaks a plain-English task into a pipeline spec)
- Per-stage agent subprocess runner
- `AgentPipeline` wave executor
- PyPI release v0.1.0 hardening

## Quick Start

```python
from stagehand import Pipeline, claude_stage

def fetch_from_notion(ctx):
    return {"title": "My Post", "body": "..."}

def send_to_telegram(ctx):
    print(ctx["generate"])
    return "sent"

p = Pipeline("weekly-content-2026-04-25")
p.stage("fetch",    fetch_from_notion)
p.stage("generate", claude_stage("Write a LinkedIn post about: {fetch}"), deps=["fetch"])
p.stage("deliver",  send_to_telegram, deps=["generate"])
p.run()
```

Re-running the script after a failure resumes from the last completed stage — no extra flags required.

## How it works

```
Pipeline.run()
  ├── topological sort of stages by deps
  ├── wave-by-wave parallel execution (ThreadPoolExecutor)
  ├── after each successful stage → atomic checkpoint write
  ├── on failure → traceback captured, run can resume later
  └── on completion → run archived to history, registry updated
```

Each stage receives a `ctx` dict containing the outputs of all previously completed stages, keyed by stage name.

## Installation

```bash
pip install stagehand-ai
```

With AI providers:

```bash
pip install stagehand-ai[claude]    # Anthropic SDK
pip install stagehand-ai[openai]    # OpenAI SDK
pip install stagehand-ai[gemini]    # Google Generative AI SDK
pip install stagehand-ai[all]       # All providers
pip install stagehand-ai[dev]       # pytest + hatch for development
```

The core engine has zero runtime dependencies — provider extras only install the SDKs you actually use.

## How it differs from Prefect / Airflow

| | Stagehand | Prefect | Airflow |
|--|--|--|--|
| Setup | `pip install` | Server + DB | K8s / Docker |
| Runtime deps | Zero (stdlib) | Many | Many |
| AI provider helpers | Built-in | No | No |
| Target | Solo devs / small scripts | Teams | Enterprise |

## Project Structure

```
stagehand/
├── src/stagehand/
│   ├── __init__.py        # Public API: Pipeline, Stage, *_stage helpers
│   ├── pipeline.py        # Pipeline executor (DAG, parallel waves, retries)
│   ├── checkpoint.py      # Atomic checkpoint with file locking
│   ├── registry.py        # Central pipeline registry (~/.stagehand/registry.json)
│   ├── dashboard.py       # `stagehand dashboard` HTML server
│   ├── cli.py             # `stagehand` command-line entry point
│   └── providers/
│       ├── __init__.py
│       ├── claude.py      # claude_stage (Anthropic SDK or claude CLI)
│       ├── openai.py      # openai_stage
│       ├── gemini.py      # gemini_stage
│       └── http.py        # http_stage (generic webhook)
├── tests/                 # pytest test suite
├── examples/              # End-to-end usage examples
├── PLAN.md                # Full implementation plan
├── pyproject.toml
└── README.md
```

## Roadmap

- [x] Project setup + core scaffold
- [x] Static `Pipeline` with atomic checkpointing
- [x] DAG-based parallel execution with retries and timeouts
- [x] Multi-provider stage helpers (`claude`, `openai`, `gemini`, `http`)
- [x] Pipeline registry + run history
- [x] CLI (`list`, `status`, `reset`, `runs`, `dashboard`, `version`)
- [x] Web dashboard
- [ ] `PipelineSpec` + `StageSpec` contract types
- [ ] Validator with structured errors
- [ ] Capability registry
- [ ] Decomposer + self-correction loop
- [ ] Agent runner subprocess
- [ ] Wave executor + `AgentPipeline`
- [ ] Full test suite + PyPI release v0.1.0

## Contributing

1. Fork the repo
2. Create a feature branch (`git checkout -b feat/your-feature`)
3. Commit with conventional commits (`git commit -m 'feat: add your feature'`)
4. Open a PR — use the template

## License

MIT © [Ankit Mishra](https://github.com/ankitpyc)
