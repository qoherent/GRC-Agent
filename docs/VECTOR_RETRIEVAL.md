# Vector Retrieval V1

Vector retrieval v1 is a stable read-only candidate discovery subsystem. It is
not a mutation authority and does not replace `search_grc` or `search_manual`.

## Frozen Stack

- Local Qdrant path mode, not server or Docker.
- FastEmbed through `qdrant-client[fastembed]`.
- Embedding model: `BAAI/bge-small-en-v1.5`.
- Collection alias: `grc_agent_retrieval_v1`.
- Vector-only ranking.
- No hybrid sparse search.
- No reranker.
- No runtime multi-model selection.

## Safety Boundary

`semantic_search_grc` may return candidate blocks, manual chunks, tutorial
chunks, provenance, excerpts, scores, and deterministic match reasons.

It must never return:

- Transactions.
- Parameter payloads.
- Insert arguments.
- `apply_edit` payloads.
- `save_graph` instructions.
- Hidden recipes.
- Tutorial-derived defaults.
- Repair plans.
- Mutation authorization.

Mutation remains gated by deterministic `TurnPlan`, narrowed tool schemas,
route validation, verified tools, `grcc`, rollback, and exact graph-delta proof.
`semantic_search_grc` is not exposed for `uncertain_mutation`.

## CLI Workflow

Build and inspect the local index:

```bash
uv run grc-agent vector build --json
uv run grc-agent vector stats --json
```

Search manually:

```bash
uv run grc-agent vector search "audio smoother" --scope catalog --k 5 --json
uv run grc-agent vector search "leveler block" --scope catalog --k 5 --json
```

Record and review sanitized miss evidence:

```bash
uv run grc-agent vector miss "waveform viewer" --expected-block qtgui_time_sink_x --actual-top-id blocks_probe_signal_x --source manual_review
uv run grc-agent vector misses --json
uv run grc-agent vector proposals --json
```

Garbage-collect old local collections:

```bash
uv run grc-agent vector gc --json
uv run grc-agent vector gc --apply --json
```

`gc` is dry-run by default. `--apply` preserves the active alias target plus one
previous retired collection and deletes only older staging/retired collections
from this index family.

## Evidence Intake

`vector miss` is evidence-only. It appends sanitized structured JSONL records and
does not update metadata, rebuild indexes, change rankings, call model tools, or
affect graph state.

Stored fields are bounded and structured:

- timestamp
- sanitized query
- query key
- expected block IDs, if known
- actual top IDs
- scope
- category
- source: `real_user`, `eval`, or `manual_review`
- bounded notes

Paths and `.grc` filenames are redacted from every stored/displayed user field
while canonical catalog IDs remain visible. Clustering is conservative: shared
expected IDs alone must not merge unrelated wording.

## Metadata Governance

Metadata changes require `docs/VECTOR_METADATA_CHANGE_CHECKLIST.md`. Minimum
promotion criteria:

- At least 3 clustered misses, or repeated misses across 2 distinct sources.
- Stable block-capability reason.
- No ambiguity or eval-expectation issue.
- Mutation-shaped negative trap.
- Retrieval eval rerun.
- Exact-ID misses remain 0.
- False-positive failures remain 0.
- Source-type misses remain 0.

`vector proposals` is a human-review report only. It does not edit
`CATALOG_SEMANTIC_METADATA`, docs, indexes, or runtime behavior.

## Regression Gates

Run the no-LLM retrieval eval:

```bash
uv run python -m tests.retrieval_eval.vector_retrieval
```

Run the frozen vector v1 regression gate:

```bash
uv run python -m tests.retrieval_eval.vector_regression
```

Protected v1 thresholds:

- Vector top-k hits >= 276/290.
- Exact-ID misses = 0.
- False-positive failures = 0.
- Source-type misses = 0.
- Safety pass = 290/290.
- Provenance pass = 290/290.
- Deterministic rebuild pass = true.

Lexical top-k count is reported as the baseline comparison, but it is not a hard
regression threshold unless lexical behavior is intentionally changed.

## Stop Criteria

Do not keep tuning vector retrieval unless one of these happens:

- Real-user clustered misses show a repeated stable capability gap.
- An exact-ID miss appears.
- A false-positive failure appears.
- A source-type miss appears.
- Lexical wins materially increase.
- Manual/tutorial provenance fails.
- Search latency becomes unacceptable.
- Qdrant/FastEmbed dependency or install behavior breaks.

Explicitly deferred: hybrid sparse search, reranking, runtime model selector,
second embedding backend, LangChain, LlamaIndex, RAG-Anything, automatic
metadata promotion, tutorial-derived mutation recipes, vector result to
`apply_edit` bridges, and semantic search inside `uncertain_mutation`.
