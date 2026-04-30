# Local Alpha Release Marker

Date: 2026-04-30

## Release Candidate

- Release marker: `local-alpha-v0.1.0-20260430`.
- Package version: `0.1.0`.
- Git commit at verification time: `0236cea0a5e73f5634d9800c70df54d809a5dac5`.
- Git tag status: not created from this dirty worktree. A Git tag should be
  created only after committing the current release snapshot, otherwise the tag
  would point at `HEAD` without the local-alpha release files and runtime
  changes.

This marker freezes the current local-alpha evidence package for supervised
internal/beta-style use. It is not a production release claim.

## Supported Scope

GRC Agent is local alpha for bounded GNU Radio Companion
inspect/edit/validate/save workflows on copied `.grc` graphs.

Supported workflows:

- load one active graph session
- summarize, inspect, search, and describe graph/catalog/manual/vector context
- preview supported edits without mutation
- apply verified parameter/state edits
- remove exact connections
- perform exact or clarification-backed rewires
- validate with `grcc`
- save only to an explicitly requested path
- roll back failed edits atomically
- clarify ambiguous topology, endpoint, placement, or duplicate-identity cases

## Non-Goals

- general release-grade autonomy
- unconstrained graph design
- broad topology repair
- vague "fix wiring" automation
- raw `.grc` YAML editing
- Python export/code generation
- vector-driven mutation
- tutorial-derived mutation recipes
- `block_uid` mutation handles
- same-name same-type duplicate mutation
- hidden repair/remapping

## Environment Requirements

- Python >= 3.12.
- GNU Radio 3.10.9.2 target environment with `grcc` on `PATH`.
- `uv` for installs and command execution.
- Local llama.cpp server/model configured for `unsloth/gemma-4-E2B-it-GGUF`.
- `numpy<2` remains pinned for GNU Radio 3.10.x Python binding ABI
  compatibility.

## Install And Smoke Commands

```bash
uv sync --locked
uv run grc-agent doctor
uv run grc-agent health
uv run grc-agent fake tests/data/random_bit_generator.grc
```

Vector retrieval is local and read-only. Build it only when semantic search is
needed:

```bash
uv run grc-agent vector build
uv run grc-agent vector stats --json
```

Missing vector indexes return a structured `missing_index` error; chat/search
must not auto-build an index.

## Final Verification Commands

```bash
uv sync --locked
uv run ruff check src/ tests/
uv run ruff check
uv run python -m unittest
uv run python -m tests.retrieval_eval.vector_regression
uv run grc-agent doctor
uv run grc-agent health
uv run grc-agent fake tests/data/random_bit_generator.grc
uv run python -m tests.llama_eval.tier1_live --quick
uv run python -m tests.llama_eval.tier2_release
uv run python -m tests.llama_eval.tier3_multiturn --quick
uv run python -m tests.llama_eval.tier4_external_examples --quick
uv run python -m tests.llama_eval.tier5_adversarial --quick
```

## Current Evidence

- Deterministic tests: 911 tests, 9 skipped.
- Vector regression: 276/290 vector hits, 168/290 lexical hits, 0 exact-ID
  misses, 0 false-positive failures, 0 source-type misses.
- Final pre-tag quick gates on 2026-04-30:
  - Tier 1 quick: 15/15.
  - Tier 2 quick: 37/37.
  - Tier 3 quick: 13/13.
  - Tier 4 quick: 37/37.
  - Tier 5 quick: 7/7.
- Latest RC dashboard:
  `reports/live_eval/rc_preview_boundary_release_dashboard.json`.
- RC dashboard result: 282/282 model passes, `release_ready=true`, 0 infra
  failures, 0 unstable cases.
- Tier 4 promoted installed-example gate: 37 cases.
- Release-readiness report: `reports/RELEASE_READY_LOCAL_ALPHA.md`.
- User pilot report: `reports/dogfood/USER_PILOT_2026-04-30.md`.
- User pilot result: 8 copied user/workspace graphs, 28 observations, 28/28
  clean or safe outcomes, 0 STOP_THE_LINE events, 0 preview mutations, 0 apply
  during preview-only prompts, 0 save without explicit request, 0 invalid graph
  committed/saved, 0 wrong file writes, 0 save/reload mismatches, and 0
  repeated generic failure clusters.
- Vector baseline report: `reports/retrieval/VECTOR_BASELINE_V1.md`.

## Safety Guarantees

- One `GrcAgent` owns graph state and mutation.
- Deterministic `TurnPlan` narrows model-visible tools.
- Route mismatch fails closed.
- All mutations go through verified tools.
- `grcc` remains final validation authority.
- Preview never mutates.
- Save happens only on explicit request.
- Failed edits roll back atomically.
- Clarification options come from real executable candidates and carry
  `state_revision`.
- Vector retrieval is read-only candidate discovery and cannot authorize
  mutation.

## Known Limits

- Local alpha only, not unconstrained autonomous graph handling.
- Tier 4 evidence is installed-example coverage, not proof for arbitrary GNU
  Radio graphs.
- Vague topology rewiring clarifies only.
- `block_uid` is read-only identity evidence.
- Same-name same-type duplicate mutation is blocked.
- Vector retrieval is frozen/read-only.
- Lexical search remains available and is not replaced by vector retrieval.
- Tutorials/manual chunks are explanation material, not mutation recipes.

## Patch Criteria

Patch immediately only for:

- unsafe mutation
- preview mutation
- apply during preview-only request
- invalid graph committed or saved
- raw YAML bypass
- wrong file overwritten
- save without explicit request
- save/reload mismatch
- hidden repair/remapping

Patch normal failures only when the same generic issue repeats across at least
three unrelated graphs or across distinct evidence sources. Do not patch
one-off model weirdness, safe clarification, safe preflight rejection, or a
single `grcc` failure before commit.

## Controlled Beta Dogfood

Continue controlled beta dogfooding on copied graphs only:

- target 10-20 additional copied user/private graphs
- record 50-100 real tasks over time
- use `grc-agent dogfood record` for every issue
- use `grc-agent dogfood report --json` for clustering
- patch only STOP_THE_LINE issues or repeated generic failures

Do not start these without repeated evidence:

- `block_uid` mutation handles
- hybrid retrieval/reranking
- planner or multi-agent router
- vague topology repair
- arbitrary graph construction
- assistant-text fallback expansion
