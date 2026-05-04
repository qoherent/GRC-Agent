# MVP Wrapper Efficiency Report

Date: 2026-05-03

## Scope

Evaluate wrapper-level efficiency after MVP surface consolidation:

- Latency (avg/p95)
- Output size bounds
- Internal handler call count per wrapper action
- Wasteful duplicate call checks

Evidence sources:

- Dispatch/efficiency unit tests (`tests/test_mvp_wrapper_dispatch.py`)
- Wrapper-only dogfood telemetry (`reports/dogfood/mvp_wrapper_dogfood_2026-05-02.jsonl`)
- Direct wrapper micro-benchmark (post-optimization re-run)
  on `tests/data/random_bit_generator.grc`

## Wrapper Latency And Output Size (Post-optimization)

| Wrapper action | Avg ms | p95 ms | Avg output bytes | Max output bytes | Internal handler calls |
|---|---:|---:|---:|---:|---|
| `search_blocks:exact` (`blocks_throttle2`) | 27.56 | 1.90 | 1164.0 | 1164 | lexical x30 |
| `search_blocks:concept (cold)` (`limit sample rate of stream`) | 742.77 | 771.24 | 1987.0 | 1987 | lexical + semantic |
| `search_blocks:concept (warm cached)` (same query, same agent) | 0.51 | 0.57 | 1987.0 | 1987 | cache hit (no lexical/semantic) |

Notes:

- Exact-path runs now skip semantic search and return `retrieval_mode="exact"`.
- Conceptual cold-path runs keep lexical + semantic with `retrieval_mode="hybrid"`.
- Conceptual warm-path uses in-memory cache keyed by normalized query + `k` + corpus/index version token.
- Exact-path output is below the ~2 KB target in this benchmark.
- Conceptual output is now below ~2 KB in this query profile.

## Search Optimization Delta

- Exact query latency moved from ~841/889 ms (avg/p95) to ~30/2 ms in this run.
- Exact query internal handler calls moved from lexical+semantic to lexical-only.
- Concept cold query keeps semantic recall behavior and remains in the same latency
  band (~743/771 ms here).
- Concept warm cached query drops to sub-millisecond latency in repeated-call cases.
- Output size improvement:
  - exact: ~4549 bytes -> ~1164 bytes
  - concept: ~5103 bytes -> ~1987 bytes

## Cache Hit/Miss Behavior

- Cold call example (`limit sample rate of stream`): ~1076.88 ms.
- Immediate repeated warm call (same query/`k`/version): ~0.58 ms.
- Cache misses occur when query text differs, `k` differs, or corpus/index
  version token changes.

## Dogfood Efficiency Signals

From `reports/dogfood/mvp_wrapper_dogfood_2026-05-02.jsonl` and summary report:

- Observations: `120`
- Wrapper telemetry rows captured: `87`
- Wrong internal handler count: `0`
- Legacy tool exposure: `0`
- Preview mutation: `0`
- Unsupported mutation: `0`
- Invalid commit: `0`

## Budget/Compactness Checks

Verified by deterministic tests:

- `search_blocks` default `k` bounded (`<=5` results by default output tests)
- `search_help` default `k` bounded (`<=3` results by default output tests)
- `search_blocks` output omits ports/params/mutation payloads by default
- `search_help` output stays explanation-only
- `inspect_graph` output paths are bounded/structured (no raw YAML dump)
- No duplicate internal lexical/semantic search calls per `search_blocks` query
  beyond one lexical call on exact-mode queries, or one lexical + one semantic
  call on conceptual queries.
- Repeated conceptual query (same query, same `k`, same corpus/index token)
  serves from cache without re-running lexical/semantic retrieval.
- Preview path uses propose-only handler and does not commit/checkpoint.
- Exact operation routes call the expected single mutation handler in tested
  disconnect/rewire/insert cases.

## Wasteful-call Findings

- No duplicate lexical or semantic call loops detected in `search_blocks`.
- No multi-handler mutation fan-out detected for exact
  disconnect/rewire/insert operations in dispatch tests.
- No legacy low-level tool fan-out exposed in default model-backed wrapper path.

## Conclusion

Wrapper dispatch is efficient for the MVP profile and remains safety-correct.
The highest latency components remain semantic retrieval and `grcc` validation.
Exact block lookups now avoid semantic overhead while preserving conceptual
query recall behavior.
