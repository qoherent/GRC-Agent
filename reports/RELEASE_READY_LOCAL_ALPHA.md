# Release Ready Local Alpha

Date: 2026-04-30

## Status

GRC Agent is ready for a tagged local-alpha / internal beta-style release for
bounded GNU Radio Companion inspect, search, preview, edit, validate, and
explicit save-copy workflows.

This is not production autonomy and not arbitrary graph design. The supported
mode is supervised local use on copies of `.grc` graphs.

## Supported Scope

- Load one active `.grc` session.
- Inspect and summarize graph state.
- Search the active graph, GNU Radio catalog, manuals, and the read-only vector index.
- Preview supported edits without mutating.
- Apply verified parameter/state edits, exact disconnects, exact/bounded rewires, insertions with placement context, and explicit save-copy operations.
- Validate with `grcc`.
- Roll back failed edits atomically.
- Ask for clarification when topology, placement, endpoint, or duplicate identity is ambiguous.

## Non-Goals

- No raw `.grc` YAML editing.
- No arbitrary graph synthesis or broad topology planning.
- No vague "fix wiring" or "repair topology" automation.
- No vector-driven mutation.
- No tutorial-derived mutation recipes.
- No hidden repair/remapping.
- No `block_uid` mutation handle.
- No same-name same-type duplicate mutation.

## Verified Safety Boundaries

- One `GrcAgent` owns graph state and mutation.
- Deterministic `TurnPlan` narrows model-visible tools.
- Route mismatches fail closed before tool execution.
- All mutations go through verified tools.
- `grcc` remains final validation authority.
- Preview never mutates and never requires `apply_edit`.
- Save occurs only on explicit user request.
- Failed edits leave the live graph unchanged.
- Clarification options are generated from real executable candidates and carry `state_revision`.
- Vector retrieval is read-only candidate discovery and cannot authorize mutation.

## Evidence Summary

- Public tools: 17.
- Tier 4 promoted installed-example gate: 37 cases.
- Latest release dashboard:
  `reports/live_eval/rc_preview_boundary_release_dashboard.json`.
- Dashboard result: 282/282 model attempts passed, 0 infra failures, 0 unstable
  cases, `release_ready=true`.
- Dogfood pass 3:
  `reports/dogfood/DOGFOOD_2026-04-29_PASS3.md`.
- Dogfood result: 28 targeted boundary observations, 0 STOP_THE_LINE, 0 preview
  mutations, 0 apply during preview-only prompts, 0 save without explicit
  request, 0 save/reload mismatch, 0 repeated generic failure clusters.
- Vector baseline: 276/290 vector hits, 168/290 lexical hits, 0 exact-ID misses,
  0 false-positive failures, 0 source-type misses.

The latest n=3 dashboard is reused for this release-readiness package because
the current milestone changes only docs/reports, not runtime behavior, model
schemas, prompts, tool order, TurnPlan policy, or vector retrieval.

## Required Environment

- Python >= 3.12.
- GNU Radio 3.10.9.2 target environment with `grcc` on `PATH`.
- Local llama.cpp server binary/model configured for `unsloth/gemma-4-E2B-it-GGUF`.
- `uv` for dependency and command execution.
- `numpy<2` is intentional compatibility debt for GNU Radio 3.10.x Python
  bindings built against the NumPy 1.x ABI. Remove it only after the supported
  GNU Radio target and deterministic gates pass with NumPy 2.x.

## Install

```bash
uv sync --locked
uv run grc-agent doctor
```

Build vector retrieval only when semantic search is needed:

```bash
uv run grc-agent vector build
uv run grc-agent vector stats --json
```

If no vector index exists, vector commands return a structured `missing_index`
error with the instruction to run `grc-agent vector build`; chat/search must not
auto-build an index.

## Smoke Commands

```bash
uv sync --locked
uv run grc-agent doctor
uv run grc-agent health
uv run grc-agent fake tests/data/random_bit_generator.grc
uv run python -m tests.retrieval_eval.vector_regression
uv run python -m unittest
```

Current smoke status: passed in this workspace. A fully fresh clone was not
created because the workspace contains active release-candidate changes, but
`uv sync --locked`, environment checks, fake graph load, vector stats, vector
regression, and deterministic tests all passed.

## Standard Gates

```bash
uv run ruff check src/ tests/
uv run ruff check
uv run python -m unittest
uv run python -m tests.retrieval_eval.vector_regression
uv run python -m tests.llama_eval.tier1_live --quick
uv run python -m tests.llama_eval.tier2_release
uv run python -m tests.llama_eval.tier3_multiturn --quick
uv run python -m tests.llama_eval.tier4_external_examples --quick
uv run python -m tests.llama_eval.tier5_adversarial --quick
```

Release dashboard command:

```bash
uv run python -m tests.llama_eval.release_dashboard \
  --results-path reports/live_eval/rc_preview_boundary_tier2_n3.json \
  --results-path reports/live_eval/rc_preview_boundary_tier3_n3.json \
  --results-path reports/live_eval/rc_preview_boundary_tier4_n3.json \
  --results-path reports/live_eval/rc_preview_boundary_tier5_n3.json \
  --min-runs-per-case 3 > reports/live_eval/rc_preview_boundary_release_dashboard.json
```

## Patch Policy

Patch immediately only for:

- unsafe mutation
- invalid graph committed or saved
- preview mutation
- apply during preview-only prompt
- save without explicit request
- raw YAML bypass
- wrong file overwritten
- save/reload mismatch
- hidden repair/remapping

For normal failures, patch only when the same generic failure repeats across
3+ unrelated graphs or cross-source dogfood evidence shows a repeated issue.
Do not patch one-off `grcc` failures or safe clarification.

## Known Limits

- Local alpha, not production autonomy.
- Tier 4 is installed-example evidence, not proof over arbitrary GNU Radio graphs.
- Vague topology rewiring clarifies only.
- `block_uid` is read-only identity evidence.
- Same-name same-type duplicate mutation is blocked.
- Vector retrieval is frozen/read-only.
- Lexical `search_grc` remains the baseline retrieval path.
- Tutorials/manual chunks are explanation support, not mutation recipes.
