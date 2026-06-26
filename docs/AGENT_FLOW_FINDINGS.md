# Agent Flow Findings

> **Status:** Retrieval layer fixed (hybrid search). Residual failure is a
> topology-reasoning ceiling. Agent-flow raw counts swing ~±1 from Ollama
> temp-0 nondeterminism, so they are not a reliable gate — the controlled
> retrieval eval and live catalog tests are the trustworthy signal.
> Model: `gemma4:e4b-it-qat-120k` (7.5B Q4_0, native ctx 131K). Custom Modelfile
> model with `PARAMETER num_ctx 120000` — Ollama's `/v1` endpoint ignores
> per-request `num_ctx`, so the 120K window is baked into the model.

---

## Three-pillar adapter design

| Pillar | Principle | What it does |
|--------|-----------|-------------|
| **Syntax symmetry** | Read/write use the same format | Connections are flat strings `"src:0->dst:0"` everywhere — inspect returns them, add/remove accepts them. Max nesting depth: 2. |
| **Deterministic offloading** | Compiler work stays in the adapter | When a newly-added block omits `type`, the adapter infers it from the connected neighbor's port dtype. The LLM handles topology; the adapter handles types. |
| **Error locality** | Every error names the exact element | GRC's `iter_error_messages()` yields `(element, message)`. The adapter formats `"block_name: Port - dir(key): message"` so the model can identify exactly what failed. |

---

## Scenario results

Scenarios 01–05, 07, 08 pass. Scenario 06 fails at the **topology** layer
(it used to fail earlier, at **retrieval** — see below).

| # | Scenario | Status | Key enabler |
|---|----------|:------:|-------------|
| 01 | add_throttle inline | ✓ | auto-resolve type from neighbor |
| 02 | update sample rate | ✓ | simple param edit |
| 03 | disable + re-enable | ✓ | error identity → model identifies port |
| 04 | add + use variable | ✓ | expression params (system prompt) |
| 05 | full rewire | ✓ | auto-resolve + flat connections |
| 06 | multiply via query_knowledge | ✗ | topology limit: orphaned noise source |
| 07 | force-disable connected block | ✓ | force description + error identity |
| 08 | fm_rx inline throttle | ✓ | auto-resolve + flat connections |

> **Harness variance:** a single agent-flow run is noisy. At temperature 0,
> Ollama still produces minor logit variance from thread scheduling, and the
> 7.5B model occasionally emits a degenerate **empty turn** (`completion_tokens`
> emitted, empty content) that aborts a scenario. Scenario 08 has been observed
> to fail this way with **zero** `query_knowledge` calls — i.e. independent of
> retrieval. Treat per-run pass/fail counts as ±1; the retrieval eval and live
> catalog tests are the stable signal.

---

## Retrieval layer: root cause and fix (Scenario 06, stage 1)

The model's *first* blocker in Scenario 06 was **not** topology — it was that
`query_knowledge` never surfaced `blocks_multiply_xx`.

**Root cause (verified by direct index probing):** the catalog retriever was
pure vector KNN (L2). For the bare word `multiply` it ranked the block **#1**,
but for the verbose natural-language queries the model actually issues
(`"signal multiplier block id"`, `"multiply signal block id float"`) semantic
drift pushed it to **#8–#11** — outside the `k=3` result window. L2-vs-cosine
was exonerated (identical global ranking); the index was sound for terse
queries; the block was present (rowid 107 of 564). The two real causes were
(a) no lexical anchor to counter "multiplier"→"modulator" drift, and (b) a
3-wide result window truncating the #8–#11 rank.

**Fix — hybrid retrieval (consultant-approved, data-selected):**

| Component | Choice | Why |
|-----------|--------|-----|
| Lexical backend | **SQLite FTS5 + Porter stemmer** | Zero new deps (native to our sqlite stack). Porter stems `multiplier`→`multiply`. Beat unstemmed BM25 (which missed `multiplier`/`adder`/`subtractor`). |
| Vector backend | existing sqlite-vec KNN | unchanged |
| Fusion | **Weighted RRF, `w_vec=2`** | Vector 2x lexical → lexical is a *boost-only* signal. Plain unweighted RRF dilutes strong vector matches (`sine wave source` regression); `w_vec=2` dominates it on a 30-query eval. |
| Result window | `k` 3 → **10** | Controlled eval: rec@10 87%→97%. |

**Evidence:** `playground/search_blocks_experiment/eval_retrieval.py` is a
controlled 30-query battery (terse/verbose/synonym/morphology) measuring
recall@k + MRR across candidate retrievers. Selected result:

| Approach | rec@3 | rec@5 | rec@10 |
|----------|------:|------:|-------:|
| vec k=3 (old prod) | 87% | 87% | 87% |
| vec k=10 | 87% | 87% | 97% |
| plain RRF (FTS+vec) | 87% | 87% | 93% |
| **weighted RRF `w_vec=2`** | **87%** | **90%** | **97%** |

After the fix, `"signal multiplier block id"` returns `blocks_multiply_xx` at
**#2** (was #11/absent), and Scenario 06 now discovers the block and reaches
the `change_graph` stage.

---

## The remaining failure (Scenario 06, stage 2): topology ceiling

With retrieval solved, the model *finds* `blocks_multiply_xx`, adds it with the
correct `type=float`, and wires the two sinusoid sources into it. It then
removes `blocks_add_xx` — which **orphans** `analog_noise_source_x_0`'s output
(the noise source used to feed the adder). GRC rejects the graph:

```
analog_noise_source_x_0: Source - out(0): Port is not connected.
```

The model reads this error but does not infer the causal link ("removing the
adder left the noise source dangling") and does not remove the noise source,
reconnect it, or use `force`. This is a **multi-step topology-cleanup reasoning
limit** of the 7.5B model — the second wall, now that the retrieval wall is
breached. No schema/retrieval change fixes it.

**Mitigation implemented — orphaned-port causal hint:** the adapter now traces
removed-block → orphaned-port causality (a uniform rule in `change_graph.py`:
for each pre-batch edge that touched a removed block, name the removed block
in the surviving endpoint's validation hint). The model now sees, alongside
the raw error:

```json
{
  "code": "gnu_validation",
  "message": "analog_noise_source_x_0: Source - out(0): Port is not connected.",
  "hint": "output was connected to removed block 'blocks_add_xx'"
}
```

The hint is **surgical** — it fires only for ports genuinely left dangling (the
reconnected sig sources and audio sink get no hint). It is informational
(causal link), not procedural. Whether it tips the 7.5B model over the line on
Scenario 06 is **unconfirmed**: the agent-flow harness is too noisy (the model
often takes a degenerate empty-turn path before reaching `change_graph`), so
the hint's live effect can't be isolated from inference nondeterminism. It is
verified correct at the unit + `grc_native` integration level.

**Fix path:** upgrade the model (8B+), or build on the hint with further
deterministic offloading.

---

## Docs RAG false refusals (resolved)

`query_knowledge` docs domain (`ask_grc_docs`) over-refused: 2 of 10 canonical
queries returned *"The provided documentation does not cover this"* despite the
top-ranked source explicitly answering (e.g. Q10's `Band-pass_Filter_Taps.md`
at distance 0.879 literally describes the block in its first sentence).

**Root cause:** prompt-conservatism — the explicit refusal instruction
(*"If the documentation does not contain the answer, say exactly: '... does
not cover this.'"*) was over-triggered by the 7.5B model, which made its own
relevance judgment inline with generation and defaulted to the safe refusal.
Confirmed **context-independent**: identical refusals before and after the
120K `num_ctx` fix.

**Fix — groundedness-first prompt** (`doc_answer.py:_generate_grounded_answer`):
reframe retrieval as already-vetted, require grounding+citation, **decouple
partial answer from full refusal** (answer what IS covered, flag only the
gap), and narrow the refusal trigger to "NONE of the sources are related."
Eval (10-query battery): **10/10 grounded**, Q10 rescued with a cited answer,
the 8 previously-grounded answers unchanged (no fabrication). A two-call
relevance-gate fallback was designed but not needed.

> **Infrastructure caveat:** mid-investigation gemma4's llama-server began
> crashing on load (`GGML_ASSERT(n_inputs < GGML_SCHED_MAX_SPLIT_INPUTS)` — a
> llama.cpp graph-scheduler assert with gemma4's sliding-window attention,
> triggered under memory pressure). Ollama itself stayed up (embeddinggemma
> kept working). Resolved by `sudo systemctl restart ollama`. Not a code
> issue, but it can recur; if gemma4 starts 500-ing on every call, restart
> Ollama.

---

## Fixes applied (chronological)

| Fix | File(s) | Impact |
|-----|---------|--------|
| Native API consolidation (`get_block`, `remove_element`, `STATE_LABELS`) | `grc_native_adapter.py` | Eliminated adhoc reimplementations |
| Dead code deletion (~4,200 lines) | `validation/`, `transaction.py`, `flowgraph_session.py` | Lean codebase |
| Connection ordering (remove before add) | `change_graph.py` | Enables atomic inline-insert |
| Schema flattening (depth 3→2) | `tool_schemas.py` | Flat strings for add/remove connections |
| Payload simplification | `change_graph.py` | Agent sees `ok`/`errors` only (no `committed`) |
| num_ctx 4096→120000 | `toolagents_runtime.py` | Eliminated output truncation |
| Catalog enum values | `catalog/schema.py` | `"enum=[complex,float,int,short]=complex"` |
| Auto-resolve type from neighbor | `change_graph.py` | Adapter fills missing `type` deterministically |
| Error block+port identity | `grc_native_adapter.py` | `"blocks_add_xx: Sink - in2(2): Port is not connected."` |
| System prompt direction | `model_context.py` | `*_xx` defaults + expression params |
| Hybrid catalog retrieval (FTS5-porter + vector, weighted RRF) | `catalog_vector.py`, `search_blocks.py`, `config.py` | `blocks_multiply_xx` surfaces for verbose/morphology queries (#11→#2); k 3→10 |
| Orphaned-port causal hint | `change_graph.py` | Validation error names the removed block that orphaned a port (topology offloading) |
| inspect↔change key symmetry (`block_id`/`params`) + catalog `default_params` | `domain_models.py`, `catalog/schema.py` | Read/write shapes mirror; catalog blueprints copy-pasteable into `add_blocks.params` |
| Model-level `num_ctx=120000` (Modelfile) + dead `/v1` payload purge | `grc_agent.toml`, `config.py`, `toolagents_runtime.py` | Harness/prod run at real 120K (was silently 4096 — Ollama `/v1` ignores per-request num_ctx); GUI preference migration remaps stale model |
| Docs-RAG groundedness-first prompt | `doc_answer.py` | Rescues the stable Q10 band-pass-taps false refusal; 10/10 docs queries grounded, no regression |

---

## Runtime behavior reference

These are GRC/GNU-specific runtime behaviors (not coding-agent rules —
see `AGENTS.md` for those). Documented here for reference.

### Disconnect precision

Native `flow_graph.disconnect(src, dst)` removes ALL edges from a port.
The adapter's `disconnect()` finds the exact `Connection` object and calls
native `flow_graph.remove_element(connection)` for single-edge deletion.
Idempotent: if the edge is already gone (e.g. cascaded by `remove_block`),
the KeyError is caught and the operation is a silent no-op.

### Type auto-resolve

When a newly-added block omits the `type` param and the batch connects it
to a typed neighbor, the adapter sets `type` from the neighbor's port
dtype. The decision is reported in the `auto_resolved` field of the
`change_graph` response: `{"auto_resolved": {"mid_throttle": "float"}}`.
Only fills MISSING values — never overrides model-specified params.

### Error locality

GRC's `iter_error_messages()` yields `(element, message)` tuples where the
element is the Block/Port/Connection with the error. The adapter formats
every error as `"block_name: Port - dir(key): message"` (e.g.,
`"blocks_add_xx: Sink - in2(2): Port is not connected."`). The element
identity is never silently dropped.

### Connection ordering

`remove_connections` runs BEFORE `add_connections` in the batch dispatcher.
This prevents transient double-upstream errors when doing inline-insert
(remove 1 edge → add 2 edges).
