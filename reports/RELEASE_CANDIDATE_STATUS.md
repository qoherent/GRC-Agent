# Release Candidate Status

Date: 2026-04-29

## Scope

Current status is bounded local alpha for GNU Radio Companion graph inspection,
search, preview, verified edits, validation, explicit save-copy workflows, exact
disconnects, and clarification-backed rewires.

This is not a claim of arbitrary graph-design autonomy. The agent does not
perform vague topology repair, hidden graph planning, vector-driven mutation, or
raw `.grc` YAML editing.

## Runtime Contract

- Public tools: 17.
- Runtime owner: one `GrcAgent`.
- Routing: deterministic `TurnPlan` plus narrowed tool schemas.
- Mutation boundary: verified tools only.
- Validation authority: `grcc`.
- Preview: must not mutate or require `apply_edit`.
- Save: only on explicit user request.
- Rollback: failed edits must leave live graph unchanged.
- `block_uid`: read-only identity evidence, not a mutation handle.
- Vector retrieval: frozen read-only candidate discovery, not mutation authority.

## Frozen Vector Baseline

- Stack: Qdrant local mode + FastEmbed + `BAAI/bge-small-en-v1.5`.
- Mode: vector-only; no hybrid, reranker, runtime model selector, or vector-to-action bridge.
- Baseline: 276/290 vector top-k hits.
- Protected metrics: 0 exact-ID misses, 0 false-positive failures, 0 source-type misses.
- Regression command: `uv run python -m tests.retrieval_eval.vector_regression`.
- Baseline report: `reports/retrieval/VECTOR_BASELINE_V1.md`.

## Dogfood Evidence

Pass 1:
- Report: `reports/dogfood/DOGFOOD_2026-04-29.md`.
- 15 observations across 12 held-out installed examples and 3 workspace fixture stand-ins.
- 13 clean outcomes, 2 one-off GNU-validation failures before commit, 0 STOP_THE_LINE events, 0 repeated generic failure clusters.

Pass 2:
- Report: `reports/dogfood/DOGFOOD_2026-04-29_PASS2.md`.
- First attempt found one STOP_THE_LINE preview-contract bug.
- Patch: negated apply wording is preview-only; preview-only parameter edits expose only `propose_edit`; route validation rejects `apply_edit` for preview-only turns.
- Patched rerun: 27 observations, 0 unresolved STOP_THE_LINE, 0 preview mutations, 0 save/reload mismatches, 0 repeated generic failure clusters.

Pass 3:
- Report: `reports/dogfood/DOGFOOD_2026-04-29_PASS3.md`.
- 28 targeted boundary observations across 24 held-out installed examples and 4 workspace/eval stand-ins.
- 0 STOP_THE_LINE, 0 preview mutations, 0 apply during preview-only prompts, 0 save without explicit request, 0 save/reload mismatch, 0 repeated generic failure clusters.
- Patch decision: no additional runtime patch justified.

## Release Dashboard

Latest release-candidate dashboard:

```text
reports/live_eval/rc_preview_boundary_release_dashboard.json
release_ready=true
model_attempts=282
model_passes=282
infra_failures=0
unstable_cases=0
short_run_cases=0
min_runs_per_case=3
```

Reproduce:

```bash
uv run python -m tests.llama_eval.tier2_release --n-runs 3 --results-path reports/live_eval/rc_preview_boundary_tier2_n3.json
uv run python -m tests.llama_eval.tier3_multiturn --n-runs 3 --results-path reports/live_eval/rc_preview_boundary_tier3_n3.json
uv run python -m tests.llama_eval.tier4_external_examples --n-runs 3 --results-path reports/live_eval/rc_preview_boundary_tier4_n3.json
uv run python -m tests.llama_eval.tier5_adversarial --n-runs 3 --results-path reports/live_eval/rc_preview_boundary_tier5_n3.json
uv run python -m tests.llama_eval.release_dashboard \
  --results-path reports/live_eval/rc_preview_boundary_tier2_n3.json \
  --results-path reports/live_eval/rc_preview_boundary_tier3_n3.json \
  --results-path reports/live_eval/rc_preview_boundary_tier4_n3.json \
  --results-path reports/live_eval/rc_preview_boundary_tier5_n3.json \
  --min-runs-per-case 3 > reports/live_eval/rc_preview_boundary_release_dashboard.json
```

## Clean Smoke

Passed in the current workspace:

```bash
uv sync --locked
uv run grc-agent doctor --json
uv run grc-agent health
uv run grc-agent fake tests/data/random_bit_generator.grc
uv run grc-agent vector stats --json
```

Environment assumptions:
- Python >= 3.12.
- GNU Radio 3.10.9.2.
- `grcc` on `PATH`.
- `numpy<2` remains intentional compatibility debt for GNU Radio 3.10.x Python bindings built against the NumPy 1.x ABI.

## Known Limits

- Tier 4 is installed-example evidence, not arbitrary GNU Radio graph proof.
- Vague topology rewiring and "fix wiring" requests clarify only.
- Same-name same-type duplicate mutation is blocked.
- `block_uid` is not a public mutation schema.
- Vector retrieval is read-only and frozen.
- Tutorials/manual chunks are explanation material, not mutation recipes.

## Patch Criteria

Patch immediately only for:
- unsafe mutation
- apply during preview-only request
- preview mutation
- invalid graph committed
- raw YAML bypass
- wrong file overwritten
- save without explicit request
- save/reload mismatch
- hidden repair/remapping

For all other issues, patch only after the same generic failure repeats across
3+ unrelated graphs. Do not patch one-off `grcc` failures, safe clarification,
or unsupported installed-example input.
