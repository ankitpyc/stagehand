# QA Report: Issue #14 — Add `examples/basic_pipeline.py` and `examples/ai_pipeline.py` with working demos

## Scope of Validation

The issue body is terse ("Add two example files with working demos"). ACs were derived from:

1. Issue title — two specific example files must exist
2. README claims about what the framework does (stages, DAG, checkpoints, retry, AI support)
3. The `claude_stage` provider API
4. "Working demos" implies runnable end-to-end with visible output

No existing source file was modified. All changes are additive: two new example files and one new test file.

## Acceptance Criteria Validation

| # | Acceptance Criterion | Code | Test | Verdict |
|---|---|---|---|---|
| 1 | `examples/basic_pipeline.py` exists and runs end-to-end without API keys | `examples/basic_pipeline.py` | `test_examples.py::test_pipeline_runs_end_to_end`, `test_examples_qa.py::test_main_runs_to_completion` | PASS |
| 2 | `examples/ai_pipeline.py` exists and runs end-to-end with `claude_stage` | `examples/ai_pipeline.py` | `test_examples.py::test_pipeline_runs_end_to_end_with_mocked_llm`, `test_examples_qa.py::test_main_uses_default_topic_when_no_argv` | PASS |
| 3 | Basic demo covers DAG, parallel stages, output chaining | `basic_pipeline.py:80-100` (4-stage DAG, two parallel aggregators) | `test_build_pipeline_wires_expected_dag`, `test_enrich_adds_bucket_field` | PASS |
| 4 | Basic demo covers retry with backoff | `basic_pipeline.py:94-99` (`retry=3, retry_backoff=0.5`) | `test_examples_qa.py::test_main_survives_flaky_stage_within_retries` (NEW) | PASS |
| 5 | Basic demo covers checkpoint resume | docstring claims; mechanism in `pipeline.py` | `test_examples_qa.py::test_resume_after_deliver_failure_skips_completed_stages` (NEW) | PASS |
| 6 | AI demo covers critique→revise self-correction pattern | `ai_pipeline.py:102-122` | `test_build_pipeline_wires_expected_dag` (verifies critique feeds revise) | PASS |
| 7 | AI demo templates prompts from upstream ctx keys | `claude_stage(...{research}...)` calls | `test_pipeline_runs_end_to_end_with_mocked_llm` (prompt-substitution assertion), `test_examples_qa.py::test_missing_upstream_output_raises_keyerror` (NEW) | PASS |
| 8 | AI demo accepts topic from CLI (incl. multi-word) | `ai_pipeline.py:132` | `test_examples_qa.py::test_main_accepts_multi_word_topic` (NEW) | PASS |
| 9 | AI demo falls back to a default topic when no argv | `ai_pipeline.py:132` | `test_examples_qa.py::test_main_uses_default_topic_when_no_argv` (NEW) | PASS |
| 10 | Tests must not hit real APIs in CI | subprocess.run stubbed, `_sdk_available` monkeypatched | All ai_pipeline tests | PASS |
| 11 | Implementation does not modify existing source/tests | `git status` shows only additive files | n/a | PASS |
| 12 | Full suite (existing + new) passes | `pytest tests/` | 67 passed, 0 failed, 0 skipped | PASS |

## Additional Tests Written (NEW — `tests/test_examples_qa.py`, 8 tests)

All pass. These fill gaps not covered by the developer's tests:

- `TestBasicPipelineMain::test_main_runs_to_completion` — exercises `main()` entrypoint (argv + `random.seed` wiring) not just `build_pipeline`.
- `TestBasicPipelineMain::test_main_survives_flaky_stage_within_retries` — proves the retry path actually fires, not just that the stage succeeds on the first try.
- `TestBasicPipelineResume::test_resume_after_deliver_failure_skips_completed_stages` — simulates a mid-pipeline failure, re-runs, and asserts `fetch` does *not* re-execute. Validates the README's checkpoint-resume claim end-to-end, which the existing tests in `test_examples.py` did not.
- `TestAiPipelineMain::test_main_uses_default_topic_when_no_argv` — covers the `" ".join(sys.argv[1:]) or <default>` fallback.
- `TestAiPipelineMain::test_main_accepts_multi_word_topic` — asserts multi-word CLI args are joined (not just `argv[1]`), and that the topic substitutes into the first rendered prompt.
- `TestAiPipelinePromptFailure::test_missing_upstream_output_raises_keyerror` — asserts prompt templating fails *loudly* if an upstream stage's output is absent (no silent pass-through that would send an unformatted prompt to Claude).
- `TestFormatFinalStructure::test_banner_wraps_topic_and_separates_body` — asserts line-by-line banner structure, not just substring presence.
- `TestFormatFinalStructure::test_banner_includes_full_topic_even_if_long` — destructive: 200-char topic must not be truncated.

## Full Suite Result

```
$ pytest tests/
=========================== 67 passed in 10.37s ============================
```

Breakdown: 59 existing tests (all pass) + 8 new QA tests (all pass). No regressions.

## End-to-End Smoke Verification

- `python3 examples/basic_pipeline.py` — runs to completion, prints the DAG output block, all 5 stages complete. Verified manually.
- `python3 examples/ai_pipeline.py "my test topic"` — runs to completion with stubbed `subprocess.run`, all 6 stages complete, final banner renders correctly. Verified manually.

## Issues Found

### CONCERN-1 (minor, non-blocking): Misleading docstring in `basic_pipeline.py`

The module docstring (lines 16–18) says:

> Re-run after the first success — every stage will be skipped because the
> pipeline_id is stable.

This is **factually incorrect**. The core `Pipeline.run()` logic (see `src/stagehand/pipeline.py:152-153`) clears the active checkpoint on a successful run:

```python
if not failed_stages:
    ckpt.clear(self.pipeline_id)
```

Empirically confirmed: re-running `examples/basic_pipeline.py` after a successful run re-executes every stage from scratch. The `active/` directory only retains the `.lock` file; the checkpoint JSON is gone. Resume *only* applies when the previous run failed.

**Impact:** users following the docstring will be confused when the "skip" behavior doesn't happen. Not a correctness bug in the code — a documentation inaccuracy in the example file itself.

**Recommended fix (not applied — per QA contract):** rewrite the tip to say "re-run after a *failure* — completed stages will be skipped until the pipeline runs to success."

### CONCERN-2 (minor, non-blocking): Demo may fail on ~6% of fresh runs

`flaky_deliver` fails with probability 0.4; with `retry=3`, P(all attempts fail) = 0.4³ ≈ 6.4%. On a failed run, the user must re-run to hit the resume path. That is arguably intentional (it's an authentic demo of retry + resume), but a first-time user who sees a crash may not understand. The docstring doesn't flag this possibility.

**Impact:** mild first-impression risk for new users running the demo.

**Recommended fix (not applied):** either lower the fail rate to ~0.15 (P(all-fail) ≈ 0.3%) or add a line in the docstring noting "if deliver fails all 3 attempts, re-run — it resumes from the failed stage." This doubles as a naturally occurring demo of resume.

### No HIGH-severity issues found.

## Summary

Both example files are present, runnable, demonstrate the claimed features, and are backed by runnable tests (existing + new). All 67 tests pass. The AI example properly stubs the LLM in tests so CI doesn't need credentials. The two concerns above are documentation-quality nits, not correctness bugs — they do not block this issue.

## Verdict: PASS

---

[QA-GATE]: APPROVE
Reason: All 12 derived ACs pass with 67/67 tests green (59 existing + 8 QA-added). No implementation defects; only two documentation-level concerns that do not affect runtime correctness or test coverage.
