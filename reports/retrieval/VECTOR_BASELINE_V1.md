# Vector Retrieval Baseline V1

Status: frozen vector v1 baseline.

## Frozen Runtime Stack

- Qdrant local persistent mode.
- FastEmbed via `qdrant-client[fastembed]`.
- Embedding model: `BAAI/bge-small-en-v1.5`.
- Collection alias: `grc_agent_retrieval_v1`.
- Vector-only search.
- Read-only candidate discovery.
- No hybrid sparse search.
- No reranker.
- No runtime multi-model selector.

## Index Identity

- Git commit: `0236cea0a5e73f5634d9800c70df54d809a5dac5`.
- Qdrant path: `.grc_agent/vector_index/qdrant`.
- Active collection: `grc_agent_retrieval_v1_staging_20260428231348_01bb3a838ac2`.
- Previous collection: `grc_agent_retrieval_v1_staging_20260428204448_01bb3a838ac2`.
- Build timestamp: `2026-04-28T23:16:09.800591+00:00`.
- Corpus hash: `01bb3a838ac26126e213676e5c4c6eeb36041d1a18f0561088956738ef0a9edd`.
- Index schema version: `2026-04-28-vector-v1`.
- Record count: 1605.
- Records by source type: `catalog_block=564`, `manual_chunk=882`, `tutorial_chunk=159`.
- GNU Radio version: `3.10.9.2`.
- Catalog root: `/usr/share/gnuradio/grc/blocks`.

## Frozen Retrieval Metrics

Source report: `reports/retrieval/vector_eval_governed_metadata.json`.

- Total deterministic cases: 290.
- Vector top-k hits: 276.
- Lexical top-k hits: 168.
- Safety passes: 290/290.
- Provenance passes: 290/290.
- Exact-ID misses: 0.
- False-positive failures: 0.
- Source-type misses: 0.
- Deterministic rebuild pass: true.

## Bakeoff Decision

Source reports:

- `reports/retrieval/embedding_bakeoff.json`
- `reports/retrieval/embedding_bakeoff_summary.md`

Decision: keep `BAAI/bge-small-en-v1.5`. The FastEmbed bakeoff found no model
that improved retrieval enough while preserving protected metrics and avoiding
new dependency/runtime complexity.

## Governance Artifacts

- Metadata governance: `reports/retrieval/catalog_semantic_metadata.md`.
- Miss triage: `reports/retrieval/vector_miss_triage.md`.
- Metadata checklist: `docs/VECTOR_METADATA_CHANGE_CHECKLIST.md`.
- Vector design doc: `docs/VECTOR_RETRIEVAL.md`.
- Release dashboard: `reports/live_eval/vector_release_dashboard.json`.

The latest persisted release dashboard passed with `release_ready=true`,
required phases 20/30/40/50 present, 183/183 model passes, 0 infra failures, 0
unstable cases, and minimum 3 runs per case.

## Reproduce

Build or refresh the index:

```bash
uv run grc-agent vector build --json
```

Inspect stats:

```bash
uv run grc-agent vector stats --json
```

Run retrieval eval and frozen regression:

```bash
uv run python -m tests.retrieval_eval.vector_retrieval
uv run python -m tests.retrieval_eval.vector_regression
```

Run deterministic gates:

```bash
uv run ruff check src/ tests/
uv run ruff check
uv run python -m unittest
```

Run live quick gates before any release claim:

```bash
uv run python -m tests.llama_eval.tier1_live --quick
uv run python -m tests.llama_eval.tier2_release
uv run python -m tests.llama_eval.tier3_multiturn --quick
uv run python -m tests.llama_eval.tier4_external_examples --quick
uv run python -m tests.llama_eval.tier5_adversarial --quick
```

Persist repeated release evidence when cutting a release candidate:

```bash
uv run python -m tests.llama_eval.tier2_release --n-runs 3 --results-path reports/live_eval/vector_v1_final_tier2_n3.json
uv run python -m tests.llama_eval.tier3_multiturn --n-runs 3 --results-path reports/live_eval/vector_v1_final_tier3_n3.json
uv run python -m tests.llama_eval.tier4_external_examples --n-runs 3 --results-path reports/live_eval/vector_v1_final_tier4_n3.json
uv run python -m tests.llama_eval.tier5_adversarial --n-runs 3 --results-path reports/live_eval/vector_v1_final_tier5_n3.json
uv run python -m tests.llama_eval.release_dashboard \
  --results-path reports/live_eval/vector_v1_final_tier2_n3.json \
  --results-path reports/live_eval/vector_v1_final_tier3_n3.json \
  --results-path reports/live_eval/vector_v1_final_tier4_n3.json \
  --results-path reports/live_eval/vector_v1_final_tier5_n3.json \
  --min-runs-per-case 3 > reports/live_eval/vector_v1_final_release_dashboard.json
```

## Stop Criteria

Do not continue vector tuning unless real evidence shows one of:

- Repeated clustered stable capability misses.
- Exact-ID miss.
- False-positive failure.
- Source-type miss.
- Material increase in lexical wins.
- Manual/tutorial provenance failure.
- Unacceptable search latency.
- Qdrant/FastEmbed dependency or install breakage.
