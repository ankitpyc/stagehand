# Stagehand

> Lightweight Python pipeline runner with agent-per-stage execution, dynamic task decomposition, and first-class AI support.

![Status](https://img.shields.io/badge/status-active-brightgreen)
![Python](https://img.shields.io/badge/python-3.10+-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![GitHub last commit](https://img.shields.io/github/last-commit/ankitpyc/stagehand)

## Overview

Stagehand is a minimal Python pipeline framework for multi-step workflows. It gives you checkpointed, resumable pipelines with zero external dependencies — and optionally lets Claude dynamically decompose any plain-English task into an executable pipeline, running each stage as an isolated AI agent subprocess.

It's the missing layer between "run a script" and "deploy Airflow."

## Features

- **Checkpointing** — every stage saves its output; crashes resume from the last completed stage
- **Agent-per-stage** — each LLM stage runs as an isolated subprocess with its own full context window
- **Dynamic decomposition** — Claude breaks down a plain-English task into a validated pipeline spec automatically
- **Self-correction** — invalid specs are corrected before execution (up to 3 attempts)
- **Capability registry** — extensible catalog of what Claude can use in stages; grows with your stack
- **Multi-provider** — `claude_stage`, `openai_stage`, `gemini_stage`, `http_stage` built in
- **Zero runtime deps** — stdlib only for the core engine
- **CLI** — `stagehand list`, `stagehand status`, `stagehand reset`, `stagehand runs`

## Quick Start

### Static pipeline (known workflow)

```python
from stagehand import Pipeline, claude_stage

p = Pipeline("weekly-content")
p.stage("fetch",    fetch_from_notion)
p.stage("generate", claude_stage("Write a LinkedIn post about: {fetch}"), deps=["fetch"])
p.stage("deliver",  send_to_telegram, deps=["generate"])
p.run()
```

### Dynamic pipeline (plain-English task)

```python
from stagehand import AgentPipeline, CapabilityRegistry

registry = CapabilityRegistry.load_default()
registry.register_function("web_search", "Search the web and return top results")

outputs = AgentPipeline(
    "Research LangGraph architecture and write a Substack post about it",
    registry=registry
).run()
```

Claude decomposes the task, validates the spec, runs each stage as an isolated agent process, and returns all outputs — with full checkpointing throughout.

## How it works

```
User task (string)
      │
      ▼
AgentPipeline.run(task)
  ┌─────────────┐   ┌───────────┐   ┌──────────────┐
  │  Decomposer │──▶│ Validator │──▶│ WaveExecutor │
  │  (Claude)   │◀──│           │   │  (Popen)     │
  └─────────────┘   └───────────┘   └──────────────┘
  self-corrects       validates         fires agents
  up to 3x            spec              in parallel waves

          Wave 1: [search_papers] [search_practitioner]  ← parallel
          Wave 2: [synthesize]                           ← waits for wave 1
          Wave 3: [draft]                                ← waits for wave 2
          Wave 4: [deliver]                              ← waits for wave 3

Each box is an isolated claude -p subprocess with its own 200k context window.
Results flow between stages via the checkpoint file.
```

## Installation

```bash
pip install stagehand
```

With AI providers:
```bash
pip install stagehand[claude]    # Anthropic SDK
pip install stagehand[openai]    # OpenAI SDK
pip install stagehand[all]       # All providers
```

## How it differs from Prefect / Airflow

| | Stagehand | Prefect | Airflow |
|--|--|--|--|
| Setup | `pip install` | Server + DB | K8s / Docker |
| Runtime deps | Zero (stdlib) | Many | Many |
| AI-native | Yes (agent-per-stage) | No | No |
| Dynamic decomposition | Yes (Claude) | No | No |
| Target | Developers | Teams | Enterprise |

## Project Structure

```
stagehand/
├── src/stagehand/
│   ├── spec.py           # PipelineSpec + StageSpec contract types
│   ├── validator.py      # Pre-execution spec validation
│   ├── registry.py       # Capability registry
│   ├── decomposer.py     # Claude-driven task decomposition + self-correction
│   ├── agent_runner.py   # Per-stage agent subprocess entrypoint
│   ├── agent_pipeline.py # Wave executor + AgentPipeline
│   ├── pipeline.py       # Static Pipeline API (unchanged)
│   ├── checkpoint.py     # Atomic checkpoint with file locking
│   ├── cli.py            # stagehand CLI
│   └── providers/        # claude_stage, openai_stage, gemini_stage, http_stage
├── registry/
│   └── default.json      # Bundled capability registry
├── examples/             # End-to-end usage examples
├── tests/                # pytest test suite
└── PLAN.md               # Full implementation plan
```

## Roadmap

- [x] Project setup + core scaffold
- [x] Static `Pipeline` with atomic checkpointing
- [x] Multi-provider stage helpers
- [x] CLI
- [ ] `PipelineSpec` + `StageSpec` contract types
- [ ] Validator with structured errors
- [ ] Capability registry
- [ ] Decomposer + self-correction loop
- [ ] Agent runner subprocess
- [ ] Wave executor + `AgentPipeline`
- [ ] Full test suite + PyPI release v0.1.0
- [ ] Web dashboard (v0.2.0)

## Contributing

1. Fork the repo
2. Create a feature branch (`git checkout -b feat/your-feature`)
3. Commit with conventional commits (`git commit -m 'feat: add your feature'`)
4. Open a PR — use the template

## License

MIT © [Ankit Mishra](https://github.com/ankitpyc)
