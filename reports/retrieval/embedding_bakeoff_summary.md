# Embedding Bakeoff Summary

Date: 2026-04-28

Command:

```bash
uv run python -m tests.retrieval_eval.embedding_bakeoff > reports/retrieval/embedding_bakeoff.json
```

Scope:

- Offline eval only.
- FastEmbed-supported models only.
- Temporary local Qdrant indexes.
- No runtime embedding model change.
- No hybrid retrieval, reranker, fallback model, or multi-model runtime index.

Decision rule:

- More than 265/290 vector top-k hits.
- 0 exact-ID misses.
- 0 false-positive failures.
- 0 source-type misses.
- Mean search latency within budget.
- No new backend/dependency complexity.

## Results

| Model | Dim | Vector hits | Lexical hits | Vector misses | Lexical wins | Exact-ID misses | False-positive failures | Source-type misses | Mean search ms | Build ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `BAAI/bge-small-en-v1.5` | 384 | 265 | 168 | 25 | 7 | 0 | 0 | 0 | 371.231 | 145733.078 |
| `snowflake/snowflake-arctic-embed-xs` | 384 | 228 | 168 | 62 | 28 | 5 | 4 | 0 | 360.924 | 73712.805 |
| `snowflake/snowflake-arctic-embed-s` | 384 | 225 | 168 | 65 | 31 | 2 | 10 | 0 | 447.098 | 154894.232 |
| `jinaai/jina-embeddings-v2-small-en` | 512 | 248 | 168 | 42 | 17 | 3 | 4 | 0 | 444.089 | 121700.660 |
| `sentence-transformers/all-MiniLM-L6-v2` | 384 | 265 | 168 | 25 | 13 | 1 | 2 | 0 | 367.119 | 28049.171 |
| `nomic-ai/nomic-embed-text-v1.5-Q` | 768 | 249 | 168 | 41 | 16 | 3 | 4 | 0 | 595.680 | 475099.083 |

Notes:

- FastEmbed emitted a Hugging Face update warning for
  `nomic-ai/nomic-embed-text-v1.5-Q`; no runtime dependency or backend change
  was made.
- `sentence-transformers/all-MiniLM-L6-v2` tied total hits but failed protected
  exact-ID and false-positive metrics.
- All other candidates failed both the hit threshold and protected metrics.

Decision:

- Keep `BAAI/bge-small-en-v1.5` as the runtime default.
- No FastEmbed-supported candidate qualifies for a runtime switch.
- Hybrid remains unjustified; exact-ID and false-positive behavior is already
  clean under the current model, and lexical wins remain diagnostic evidence
  rather than enough reason to add fusion complexity.
