# Plan: Stagehand AgentPipeline — Dynamic Decomposition + Agent-per-Stage
Date: 2026-03-14
Status: Planning

## Goal

Extend stagehand with an `AgentPipeline` that:
1. Accepts a plain-English task
2. Has Claude dynamically decompose it into a validated `PipelineSpec`
3. Executes each stage as an **isolated Claude subprocess** (not a thread)
4. Passes outputs between stages via the checkpoint
5. Self-corrects invalid specs before execution (up to 3 attempts)

## Success Criteria

- [ ] `AgentPipeline("research LangGraph and write me a Substack post").run()` works end-to-end
- [ ] Each stage runs as a separate `claude -p` process (verified via `ps aux`)
- [ ] Failed stage retried with failure context injected — not regenerated from scratch
- [ ] Invalid decomposition self-corrected: wrong deps, bad model, missing prompt → Claude revises
- [ ] Capability registry extensible: add a new function, Claude can use it without code changes
- [ ] Existing `Pipeline` API unchanged — `AgentPipeline` is purely additive
- [ ] 5-stage pipeline completes under 3 minutes

## What is NOT being built (scope control)

- No web UI or dashboard
- No parallel worktrees (stages share the filesystem — Phase 2)
- No streaming output from stage agents
- No multi-machine execution
- No human-in-the-loop approval gates (Phase 2)
- No dynamic capability discovery (registry is manually maintained)

## Implementation Steps

### Step 1: PipelineSpec + StageSpec (`spec.py`)
Contract types shared between decomposer and executor. Roundtrip serialization.

### Step 2: Validator (`validator.py`)
9 validation rules. Returns `ValidationResult` with structured `ValidationError` list including `fix` suggestions.

### Step 3: Capability Registry (`registry.py` + `registry/default.json`)
`CapabilityRegistry` with `load_default()`, `register_function()`, `to_prompt_context()`.

### Step 4: Decomposer (`decomposer.py`)
Builds decomposition prompt, calls Claude, self-correction loop (max 3 attempts).

### Step 5: Agent Runner (`agent_runner.py`)
Subprocess entrypoint. Reads ctx from checkpoint, renders prompt, calls `claude -p`, writes output.

### Step 6: Wave Executor + AgentPipeline (`agent_pipeline.py`)
Popen-based wave executor. Polls checkpoint for completion.

### Step 7: Export + integrate (`__init__.py`)
Export `AgentPipeline`, `CapabilityRegistry` alongside existing exports.

### Step 8: Tests
`test_spec.py`, `test_validator.py`, `test_registry.py`, `test_decomposer.py` (Claude mocked),
`test_agent_pipeline.py` (Popen mocked).

### Step 9: Update `/orchestrate` skill
Teach Claude when to use `AgentPipeline` vs `Pipeline`.

### Step 10: End-to-end verify
Real run with `examples/agent_research.py`.

## File Creation Order

```
spec.py           → no deps
registry.py       → no deps
validator.py      → spec.py, registry.py
decomposer.py     → spec.py, validator.py, registry.py
agent_runner.py   → spec.py, checkpoint.py
agent_pipeline.py → all of the above
__init__.py       → last
tests/            → alongside each component
```

## Risks

| Risk | Mitigation |
|------|-----------|
| Claude generates ambiguous stages | Few-shot examples calibrate granularity; validator rejects before execution |
| Agent subprocess hangs | Per-stage timeout; `proc.kill()` after timeout |
| Checkpoint race in same wave | fcntl lock; each agent writes only its own stage key |
| Large outputs bloat checkpoint | Outputs >100KB truncated with warning (Phase 2: artifact files) |
| CLAUDECODE env var blocks nesting | `env.pop("CLAUDECODE", None)` in agent_runner |
