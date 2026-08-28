# DESIGN BRIEF — Hybrid RRF fusion, docs-domain `output_truncated` symmetry, and the 5 stress-designed tests

Investigation: read-only design (no implementation). Every anchor below was verified in this
session against the current working tree (HEAD `c4c2008` + pre-existing dirty worktree; all
line numbers refer to the working tree as read today, 2026-08-28). Stress-test artifacts were
re-read and independently recomputed from `/tmp/grc_stress/results_{lexical,llamacpp}.json`
(n=116 docs queries + 7 catalog queries per backend, ground truth from
`docs/wiki_gnuradio_org`); the headline numbers below are my own recomputation, not a
transcription of the brief.

Deliverable contract: chosen designs with file:line anchors + minimal diff sketches (text
only), rejected alternatives with reasons, updated hermetic test designs, AGENTS.md/docs edit
list, risk table, and an implementation order that keeps tests green at every step.

---

## 1. Executive summary

| # | Finding | Severity |
|---|---------|----------|
| 1 | The docs/truncation asymmetry is confirmed and is a **one-line omission plus one missing argument**: `query_catalog` passes `extra_limit=1` and copies `output_truncated` into its response (`rag.py:648`, `rag.py:668`); `query_docs` passes no `extra_limit` (default 0, `rag.py:538`) and omits the key from its response (`rag.py:797-802`). With `extra_limit=0`, `fetch_limit = limit` (`rag.py:578`) caps the candidate pool at `limit`, so `len(rows) > limit` (`rag.py:588,604`) is **unsatisfiable** — the flag can structurally never fire on docs. | High (model has no "more results exist" signal on docs; fires 7/7 on catalog in the stress run) |
| 2 | The two engines are measured to be complementary: lexical BM25 exact-phrase hit@1 0.978 but paraphrase hit@5 0.655; vector paraphrase hit@5 0.862 but misses 7/45 verbatim-unique phrases (all 7 rank top-1 lexically); UNION hit@5 = 112/116 = 0.966 vs 0.871 best single. A single uniform RRF fusion rule (k=60, Cormack/Clarke/Buettcher SIGIR 2009) over the two existing indexes is the minimal engine change that captures the union, costs ~1 ms (lexical leg on the already-open connection), and needs zero new infrastructure. | High (retrieval quality ceiling) |
| 3 | `search_mode` has more consumers than the brief lists: besides the tool docstring and AGENTS.md row, `chat_sidebar._tool_label` string-matches the **serialized tool result** for `"vector"`/`"lexical"` (`chat_sidebar.py:171-179`) — a new `hybrid` value without a matching branch silently strips the mode suffix from the GUI expander label. `tests/test_button_integration.py:294` (live, skipif-gated) also asserts the value domain and must be updated in the same landing. | Medium (silent UI regression / red live suite if missed) |
| 4 | The lexical-only paths are provably untouched by the fusion design: the hybrid guard requires a successful query embedding **and** both indexes present (`rag.py:579` + FTS existence), so lexical-backend DBs (`model is None`), embed-failure fallbacks, and outage-built lexical-only DBs behave exactly as today (each path pinned by an existing hermetic test). | Guard (by design) |
| 5 | Truncation stays truthful under fusion with **per-index** over-fetch: fetch `limit + extra_limit` from each index, fuse the two rankings, report `output_truncated = len(fused_pool) > limit`. Proof that the boolean is exact in both directions is in §7.2(c). | Medium (an honest flag is the whole point of fix 1) |
| 6 | The harness is the wrong home for fusion: installed pydantic_ai_harness 0.23.0 exports no retrieval capability (its `__all__` verified; `ConversationSearch` is BM25 over persisted *step history*, not domain corpora), and Context7's current upstream docs surface only ExaSearch (web). The engine change belongs in app-local `rag.py`; the sanctioned pydantic-ai boundary (the plain `Tool(fn, name=..., description=...)` registration) is untouched. | Argument, resolved |

---

## 2. VERIFIED FACTS

Stress-metric facts were recomputed by this session from the raw JSONs, not copied from the
brief.

1. **Lexical BM25 profile** — exact tier: hit@1 0.978 (44/45), hit@5 1.000, MRR 0.989; paraphrase
   tier: hit@5 0.655 (19/29); overall docs hit@5 0.845, MRR 0.761; per-query median latency
   0.6–1.2 ms. Evidence: my recomputation of `/tmp/grc_stress/results_lexical.json`
   `docs_queries` (116 entries, `search_mode` == `"lexical"` on all 116).
2. **Vector (EmbeddingGemma-300M, 768-d) profile** — overall hit@5 0.871, MRR 0.719; paraphrase
   hit@5 0.862 (25/29); discrimination hit@5 0.944 (17/18); exact hit@5 0.844 — it misses
   **7/45 exact-tier queries**, and every one of those 7 is lexical rank 1
   ("which gives 0.898757", "binary symmetric NRZ line codes", "use a _Window Type_ of
   _Rectangular_", …). Median latency ~32 ms. Evidence: recomputation of
   `/tmp/grc_stress/results_llamacpp.json` (`search_mode` == `"vector"` on all 116).
3. **Union of both engines: hit@5 = 112/116 = 0.966** vs 0.871 best single. 11 of the vector
   engine's 15 misses are lexical hits (10 at rank 1, 1 at rank 4). 4 queries miss in **both**
   engines ("dividing the incoming rate by an integer factor", "exposing values of a packaged
   subgraph…", "length header versus payload in burst transmission", "frame integrity check
   before transmission") — fusion cannot recover those; they are a corpus-coverage ceiling, not
   a ranking failure. Evidence: my UNION recomputation + per-miss cross-ranking table.
4. **`_query_index` output_truncated semantics** — computed as `len(<candidate rows>) > limit`
   in both branches (`src/grc_agent/adapter/rag.py:588` vector, `:604` lexical), where candidate
   rows are capped by `fetch_limit = limit + extra_limit` (`rag.py:578`) and `extra_limit`
   defaults to 0 (`rag.py:538`). So the flag means exactly "the candidate pool held more than
   `limit` matching entries; at least one was dropped from the response" — and it is *reachable*
   only when the caller over-fetches (`extra_limit > 0`).
5. **Catalog passes `extra_limit=1`** (`rag.py:648`) and copies the flag into its response
   (`rag.py:668`, dict at `:662-669`); its render loop surfaces at most `limit` results
   (`rag.py:653-662`) and consumes the +1 row as a render-failure spare (`render_catalog_block`
   may return `None`, `rag.py:658-660`). **Docs passes no `extra_limit`** (`rag.py:781-789`) and
   its response dict omits the key entirely (`rag.py:797-802`). With `extra_limit=0`,
   `len(rows) > limit` cannot be true → the docs flag is structurally dead, exactly as the brief
   claims.
6. **Catalog `output_truncated` fired 7/7 on both backends** in the stress run (all 7 catalog
   queries; ranks 1,1,1,1,1,2,1). This matches the prior grounding report's warning that the
   flag "fires on *every* result" on large corpora
   (`docs/investigation/grounding-fix-options-sessions-150-151.md:68`) — a real information-
   density tension that fix 1 must phrase around (§7.1).
7. **D2 docstring enrichment validated end-to-end**: all 7 catalog queries rank ≤ 2 with a
   non-empty `doc` field carrying the required units/semantics substrings — `doc_check` 7/7 on
   **both** backends (e.g. `analog_pll_carriertracking_cc` rank 1, doc contains
   "radians per sample" and "NOT HERTZ"; `blocks_throttle` rank 2 behind `blocks_throttle2`).
   Evidence: `catalog_queries` in both results JSONs.
8. **Both indexes already exist in llamacpp DBs** — `catalog_fts`/`docs_fts` are created
   unconditionally from the full chunk set (`src/grc_agent/ingest.py:160`, `:314`; module
   docstring `ingest.py:20-21`: "catalog_fts/docs_fts are always built from the full chunk set
   regardless of embedding outcome"), while the vec0 index is all-or-nothing
   (`ingest.py:12-16`, pinned by `tests/test_isolation.py::test_partial_embedding_failure_yields_no_vector_index`).
9. **Query-side XOR** — `_query_index` runs vector XOR lexical: `vec_available = query_vec is
   not None and _table_exists(conn, idx_table)` (`rag.py:579`); the FTS leg runs only in the
   `else` branch (`rag.py:590-610`). Lexical is reachable today only as (a) lexical backend
   (`model is None`, `rag.py:556-563`), (b) embed-call failure (`embed_error`, `rag.py:559-563`),
   or (c) a DB with no vector index (outage-built; `rag.py:579` guard fails even when the embed
   call now succeeds — pinned by `tests/test_isolation.py::test_query_catalog_lexical_message_present_even_when_embed_succeeds`).
10. **`search_mode` consumers, complete list** (repo-wide grep): the two message gates in
    `query_catalog`/`query_docs` (`rag.py:672`, `:803` — both `if result["search_mode"] ==
    "lexical":`), the GUI label string-match over the serialized result
    (`src/grc_agent/chat_sidebar.py:171-179`, three JSON/repr spellings each), the tool
    docstring's implicit contract (`agent.py:544-552` — currently silent about values), AGENTS.md
    Tool Surface row (`AGENTS.md:90`), `docs/technical_overview.md:70` (`"search_mode":
    "vector" | "lexical"`), and the test assertions listed in §7.2(a). `output_truncated` has
    **zero** Python consumers outside `rag.py` (grep returned none) — it is model-facing only.
11. **`_FRESHNESS_CACHE` keys are `(domain, db_path, model)`** (`rag.py:272-274`, populated at
    `:419`/`:460`, short-circuit at `:314`) — fusion introduces no new key dimension, table, or
    meta key; `_build_db` reads only `sqlite_master` + `_db_meta` (`rag.py:318-355`). Verified:
    zero structural interaction.
12. **The GUI poller reads only `_rag_building`** (`chat_sidebar.py:1268-1310`, imported from
    `rag.py:197`; entries written at `rag.py:426,432,456,462`) — build-state machinery fusion
    never touches.
13. **Harness capability surface (installed 0.23.0)** — `pydantic_ai_harness/__init__.py`
    `__all__` (`.venv/lib/python3.12/site-packages/pydantic_ai_harness/__init__.py:59-117`)
    contains no retrieval/RAG/vector capability; `ConversationSearch` is "BM25 recall over
    persisted step history" (`pydantic_ai_harness/conversation_search/__init__.py:1`).
    `ToolOutputLimits` exists but is **not wired anywhere in GRC** (grep of
    `agent_factory.py`/`agent.py` for `Overflow|tool_output|ToolOutput`: no matches).
    Context7 (/pydantic/pydantic-ai-harness, current upstream docs) likewise surfaces only
    ExaSearch (web research) — no local-corpus retrieval/fusion capability.
14. **Fixture idiom confirmed at the cited lines** — `tests/test_adapter_rag.py:19-20`:
    `monkeypatch.setenv("GRC_AGENT_VECTORS_DIR", str(tmp_vectors))` and
    `monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))`; `vectors_dir()` honors
    `GRC_AGENT_VECTORS_DIR` (`src/grc_agent/_paths.py:8-11`), `docs_dir()` honors
    `GRC_AGENT_DOCS_DIR` (`_paths.py:14-16`); `resolve_embed_backend` defaults to `"lexical"`
    (`src/grc_agent/settings.py:159-168`, `:60-61`).
15. **RRF citation verified from the primary source** — Cormack, Clarke & Bütcher,
    "Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods",
    SIGIR '09, Boston, ACM 978-1-60558-483-6, pp. 758-759: `RRFscore(d) = Σ_{r∈R} 1/(k + r(d))`,
    with k = 60 "was fixed during a pilot investigation and not altered during subsequent
    validation". RRF "combines ranks without regard to the arbitrary scores returned by
    particular ranking methods" — which is precisely our situation (cosine distance vs BM25).
16. **Hermetic baseline is green** (run read-only this session):
    `pytest tests/test_adapter_rag.py` → **9 passed in 0.89s**;
    `pytest "tests/test_chat_sidebar.py::test_query_knowledge_label_shows_search_mode"` →
    **1 passed**; `pytest tests/test_isolation.py -k "lexical or partial_embedding or
    fts_table_missing"` → **9 passed, 52 deselected in 5.68s**.
17. **The tool wrapper is a thin pass-through** — `query_knowledge_func` clamps k to 1-20,
    dispatches to `query_catalog`/`query_docs` via `asyncio.to_thread`, raises `ModelRetry` on
    `ok: False`, else `json.dumps(res)` verbatim (`src/grc_agent/agent.py:538-560`); the Tool is
    registered with `docstring_format="google", require_parameter_descriptions=True`
    (`agent.py:833-839`). Any response-shape change flows to the model verbatim.
18. **Ingest's test hook exists** — `ingest.py:46` captures `_orig_embed_document = embed_document`
    and `ingest.py:119,271` branch per-item when `embed_document is not _orig_embed_document`,
    so monkeypatching `grc_agent.ingest.embed_document` with a deterministic fake builds real
    vec0+FTS DBs with no runtime (the mechanism the hybrid integration test in §7.3 uses).

---

## 3. REFUTED / DRIFTED CLAIMS

1. **Brief anchor drift (docs call site).** The brief cites `rag.py:786-790` for the docs
   `_query_index` call and `rag.py:822-827` for its response. Current working tree: the call is
   `rag.py:781-789` and the response dict is `rag.py:797-802`. The *claims* are correct (no
   `extra_limit`; key omitted — verified at those lines), only the numbers drifted. The catalog
   anchors (648, 668) are current.
2. **Grounding-report anchor drift.** `grounding-fix-options-sessions-150-151.md:68` cites
   `rag.py:580,596` for the `output_truncated` computations; the current lines are `:588` and
   `:604`. Same file, earlier state — consistent with its own fact 21 (dirty worktree).
3. **`docs/technical_overview.md:70` is future-drifted by design.** It states the tool result
   tags `"search_mode": "vector" | "lexical"`. Under fix 2 this value domain gains `"hybrid"`;
   the doc must be edited in the same landing or it becomes a contradicted claim (listed in the
   edit list, §7.4).
4. **AGENTS.md:90 query_knowledge row is future-drifted by design.** "Vector search (sqlite-vec)
   is primary; falls back to a local SQLite FTS5/BM25 keyword search when the embed call fails
   or no vector index exists" describes the current XOR engine. After fix 2 the primary path on
   a healthy llamacpp DB is *hybrid*, and the row must say so (§7.4). No current AGENTS.md claim
   is contradicted by today's code.
5. **Refuted by evidence, not drift: "the docs flag could fire if only it were included."**
   False — including the key without `extra_limit` would report a constant `False` (pool is
   capped at `limit` by the SQL `LIMIT`, `rag.py:578` + `:588`/`:604`). The honest fix needs the
   over-fetch *and* the key (§7.1).

---

## 4. REDUNDANCY & LEAN AUDIT

1. **No duplicated retrieval logic exists to remove** — both domains already share one
   `_query_index` (`rag.py:529-631`); the asymmetry is confined to two call-site differences
   (one argument, one response key). The fix is therefore *adding* uniformity, not deduplicating.
2. **`_lexical_fallback_message` (rag.py:510-526) stays exactly as-is** — its two cases
   (embed-call failure; outage-built lexical-only DB) are both lexical-mode-only facts. Fusion
   must not grow a third message variant; "hybrid" is not a fallback and says nothing.
3. **No harness/pydantic-ai redundancy**: nothing in pydantic-ai 2.31.0 or harness 0.23.0
   provides local-corpus ranking fusion (fact 13) — nothing to delete in favor of a library.
   sqlite-vec + FTS5 *are* the standard libraries here (AGENTS.md "No Brittle Reinventions");
   RRF is 6 lines of pure Python because no installed dependency ships it.
4. **Lean-watch items the fix must NOT add**: no per-domain fusion policy, no user-tunable k,
   no second DB connection, no re-embedding, no cache keyed on anything beyond the existing
   `(domain, db_path, model)`, no response-shape change for the catalog (its key already
   exists), no new capability object where a module constant + pure function suffice.
5. **Pre-existing wart surfaced (not created by this work):** lexical-mode catalog results
   render `"distance": 0.0` for every hit (`rag.py:603` empty `distance_by_rowid` + `:658`
   `.get(rowid, 0.0)`), which reads as a perfect vector match. Under hybrid the same convention
   would apply to lexical-sourced rows. The design adds a truthful `score` field for hybrid
   results rather than widening the wart (§7.2); a full `distance`-semantics cleanup is
   explicitly out of scope (rejected alternative R7).

---

## 5. SMALL LOST DETAILS

1. **The catalog flag is already near-vacuous on the production corpus** — fires 7/7 in the
   stress run (§2 fact 6) because any 568-block catalog has ≥ 6 candidates for any real query.
   Fix 1 replicates that on docs (718 chunks). The grounding report flagged exactly this
   ("trains the model to ignore it"). Design answer: keep the honest boolean, but make the
   docstring sentence *actionable* ("raise k or refine the query") so the flag retains a use
   even when usually true; and pin the truthful-False case in tests so the semantics can't rot
   (§7.3 T2).
2. **`extra_limit=1` on the catalog is a render-failure spare, not a truncation probe** — the
   +1 row exists so one `render_catalog_block → None` (`rag.py:658-660`) doesn't shrink the
   result list below `limit`; the truncation flag falls out of the same over-fetch. Docs has no
   render step (payloads always resolve), so its over-fetch exists *only* to make the flag able
   to be true — and the answer must be sliced back to `limit` (`rag.py:794`) or docs would
   silently return k+1 chunks. This is the one place fix 1 is not purely additive.
3. **The GUI label matches three spellings** (`'"search_mode": "lexical"'`,
   `"'search_mode': 'lexical'"`, `'"search_mode":"lexical"'` — `chat_sidebar.py:171-179`)
   because the result arrives both as the JSON the model saw and as Python `repr` in history
   rendering. The hybrid branch must replicate all three spellings, and
   `test_query_knowledge_label_shows_search_mode` (`tests/test_chat_sidebar.py:3047-3064`) must
   gain the hybrid case.
4. **`query_capped` can occur inside hybrid** — the FTS token cap (`_FTS_MAX_TOKENS = 32`,
   `rag.py:482`; cap logic `rag.py:506-510`) applies to the lexical leg of a fused query too.
   Today the cap message is gated behind `search_mode == "lexical"` (`rag.py:672,803`), which
   would silently swallow the disclosure in hybrid mode — a fresh "no silent transformation"
   hole the message-gate restructure (§7.2 step 3) closes. Equivalence with today's behavior in
   the existing modes is proven in §7.2(a) note 1.
5. **Hybrid with an empty lexical leg** — a query with no word tokens makes `_fts_query_string`
   return `None` (`rag.py:496-498`); RRF over `[vec_ranking, []]` degenerates to the vector
   order. The uniform rule keeps `search_mode: "hybrid"` (mode names the engines that *ran*,
   not the ones that contributed rows) — results are byte-identical to vector, so nothing is
   hidden, but the edge is documented here so a future "optimization" doesn't add a per-query
   mode branch.
6. **Tie-breaking must be specified** — two rowids can tie on RRF score (e.g. each rank-1 in
   exactly one list scores 1/61… actually 2/(k+1) vs 1/(k+1) differ; true ties arise from
   symmetric rank profiles like [1,∞] vs [∞,1]). The design fixes the total order at
   `(-score, rowid)` — deterministic for a given DB build (rowids are assigned in corpus order,
   `ingest.py:151-154,304-308`), no relevance meaning, stated in the helper docstring so tests
   can pin it.
7. **`tests/test_button_integration.py` is gated on `OLLAMA_CLOUD_API_KEY`**
   (`tests/test_button_integration.py:47-51`) — its `search_mode in ("vector", "lexical")`
   assertion (`:294`) cannot be executed in this environment at all; it must be edited in the
   same landing as fix 2 (add `"hybrid"`) even though it can only be validated on a provisioned
   run.
8. **A tmp-docs-corpus test can poison `_CORPUS_VERSION_CACHE`** — the cache is keyed by domain
   only (`rag.py:189,233-234,262`) and never keyed on the docs directory, so a test that points
   `GRC_AGENT_DOCS_DIR` at a tmp corpus must pop `_CORPUS_VERSION_CACHE["docs"]` in setup and
   `finally`, exactly like the `_FRESHNESS_CACHE` discipline the existing tests already practice
   (`tests/test_isolation.py` finally blocks). Otherwise later real-corpus tests in the same
   process compute freshness against the poisoned hash (order-dependent green, semantically
   wrong).
9. **The stress harness is itself the post-fix acceptance harness** —
   `/tmp/grc_stress/run_backend.py` already sets the documented isolation env
   (`GRC_AGENT_ENV`/`GRC_AGENT_VECTORS_DIR`, `run_backend.py:16-19`) and the full ground-truth
   matrix (`ground_truth.QUERY_MATRIX`); re-running it after fix 2 measures the fused engine
   against the same n=116+7 truth with zero new tooling.
10. **`render_catalog_block` can silently drop a candidate** (returns `None` on
    `fg.new_block(block_id)` KeyError, `rag.py:730-734`) — AGENTS.md's `omitted_files`
    convention ("counted, never silently") is not applied to dropped catalog renders today.
    Out of scope for the three fixes, recorded here per the beyond-brief duty.

---

## 6. UNVERIFIED

1. **RRF's realized hit@5 on this corpus.** The union bound (0.966) is an *upper reference*,
   not a prediction — RRF at k=60 can land anywhere between best-single (0.871) and the union.
   Confirm post-landing by re-running `/tmp/grc_stress/run_backend.py llamacpp` (artifacts +
   truth already in `/tmp/grc_stress/`). Guard rail: the measured number is *reported*, never
   used to tune k (tuning k to the corpus is precisely the hand-picked heuristic AGENTS.md
   forbids; k stays the literature constant, falsifiable by the paper's own ablation table).
2. **True-vector fusion behavior on a provisioned runtime** (T3's skipif variant, and the
   stress re-run) — requires a machine with the llama.cpp runtime downloaded; not executable in
   this session.
3. **sqlite-vec vec0 KNN with `k` larger than the row count** (returns all rows — implied by
   production use and the stress cold builds, but not separately verified by a hermetic test).
   Cheap confirm: assert in T4/T7 that a 7-chunk vector DB queried with `k=6` returns ≤ 6 rows
   and no error.
4. **"Fake-embed → full vec0 build → KNN query" has never been exercised by any existing
   hermetic test** (today's fakes either fail embedding or hand-build empty vec0 tables). T7 is
   the first; residual risk is sqlite-vec quirks on a 3-dim toy table (production syntax at
   `rag.py:583` is proven in production, so risk is low but non-zero).
5. **ConversationSearch end-to-end behavior with "hybrid" tokens** in persisted tool results —
   argued negligible (its index is content-agnostic BM25 over step history,
   `conversation_search/__init__.py:1`), not executed.
6. **Whether any live-LLM session depends on the docs response *not* carrying
   `output_truncated`** — no consumer exists (§2 fact 10), so this is near-certainly safe; the
   only residual is a prompt-folklore dependence we cannot see from code.

---

## 7. RECOMMENDATIONS (designs, ordered for landing)

### 7.1 Fix 1 — Docs-domain `output_truncated` symmetry (one uniform rule)

**Verified semantics being unified.** `output_truncated` = "the candidate pool held more than
`limit` matching entries; ≥ 1 was dropped from the response" (`rag.py:588,604` over
`fetch_limit = limit + extra_limit`, `rag.py:578`). Per AGENTS.md's caps convention
(generate_python row: dropped files "counted in `omitted_files`, never silently"; Engineering
Rules: "Any truncation, filtering, or omission in model-facing output must be explicit"), a
domain response must disclose this. Today only catalog can, and only because it over-fetches.

**The rule (one sentence):** *every domain call passes `extra_limit=1` to `_query_index`,
surfaces at most `limit` entries, and copies the engine-computed flag into its response.*

Minimal diff (3 edits, all in `query_docs`, `src/grc_agent/adapter/rag.py`):

```python
# rag.py:781-789 — add the over-fetch argument (catalog already does this at :648)
     result = _query_index(
         "docs",
         q,
         limit,
         idx_table="docs_idx",
         fts_table="docs_fts",
         chunks_table="docs_chunks",
         id_column="payload",
+        extra_limit=1,
     )

# rag.py:794 — cap the answer at `limit`; the +1 rowid is a spare, exactly like the
# catalog's render-failure spare (payloads always resolve, so today this slice is a
# no-op whenever the pool is small — the answer string is byte-identical on every input)
-    chunks = [id_by_rowid[r] for r in result["ranked_rowids"] if r in id_by_rowid]
+    chunks = [id_by_rowid[r] for r in result["ranked_rowids"] if r in id_by_rowid][:limit]

# rag.py:797-802 — copy the flag, mirroring the catalog response's key order (:662-669)
     response: dict[str, Any] = {
         "ok": True,
         "query": q,
         "answer": answer,
+        "output_truncated": result["output_truncated"],
         "search_mode": result["search_mode"],
     }
```

Decisions the brief asked for:

- **Does the docs response ADD the key?** Yes, with *identical value semantics to catalog*
  (same engine-computed boolean, same meaning). No per-domain branch is introduced — both
  callers now satisfy the one rule, and `_query_index` remains the single place that defines
  the flag.
- **Does the answer string change?** No, provably: today `fetch_limit = limit` ⇒
  `len(ranked_rowids) ≤ limit` ⇒ the slice is a no-op on every input; post-fix the pool may be
  `limit+1` but the slice restores today's exact output. The only observable delta is the new
  key.
- **Does the tool docstring need one honest sentence?** Yes — `agent.py:544-552` currently says
  nothing about the response envelope. Add one sentence to the docstring body (after the
  domain/k lines, `agent.py:544-552`):
  `"Responses carry search_mode ('vector' | 'lexical' | 'hybrid') and output_truncated (true when more matching entries existed beyond the k returned — raise k or refine the query)."`
  (The `'hybrid'` token lands with fix 2; landing the sentence once, in fix 1's commit, with
  fix 2 amending nothing is acceptable — or defer the whole sentence to fix 2's landing; the
  chosen order below lands it in fix 1 with `vector|lexical` and amends the token list in fix 2,
  so every commit's docstring is truthful.)
- **The always-True tension** (§2 fact 6, grounding report #5): on the production corpora the
  flag will usually read `True`. That is *honest* (entries genuinely were dropped) and the
  docstring makes it actionable; the rejected alternative (a count field) is R3 below. The
  truthful-False case is pinned by T2 so the semantics stay observable and testable.
- **Interaction with fix 2:** none at landing time — the flag's *definition* ("pool > surfaced")
  is unchanged by fusion; only the pool's *composition* grows (union of two indexes), and
  §7.2(c) proves the boolean stays exact.

**Rejected alternatives**

- **R1 — Compute docs truncation with a separate COUNT query instead of over-fetching.** Adds a
  second SQL round-trip per query to express what `fetch_limit = limit+1` already encodes, and
  creates a *second* truncation mechanism alongside the catalog's — two mechanisms for one
  concept violates the one-uniform-rule reading of AGENTS.md's "Simplify First".
- **R2 — Append a truncation sentence to the docs `answer` string** instead of a JSON key.
  Mutates the model-facing content bytes (context-flooding adjacent), entangles disclosure with
  content, and diverges from the catalog shape — not uniform.
- **R3 — Replace the boolean with a dropped-count (`more_available: N`)** (closer to the
  `omitted_files` convention). Rejected *for now*: it forces a catalog response-shape change
  too (wider blast radius), and under fusion the count is only ever a lower bound
  (pool capped at `2(limit+1)` by the per-index fetch), so an integer would *overstate*
  precision. The boolean plus the actionable docstring sentence carries the same decision
  signal. Revisit only if the flag's near-always-True density (§5.1) measurably trains the
  model to ignore it.
- **R4 — Fire the flag only when dropped entries were not consumed as render spares** (catalog
  refinement). Per-domain semantics; would report `False` exactly when a render failure
  consumed the spare — hiding that the pool was larger. Rejected.

### 7.2 Fix 2 — Hybrid retrieval via Reciprocal Rank Fusion (one uniform rule)

**The rule (one sentence):** *when the resolved backend's DB contains both a vec0 index and an
FTS5 index, and the query embedded successfully, run both rankings and fuse them with
Reciprocal Rank Fusion; otherwise exactly the current two branches.*

Llama.cpp DBs always satisfy the index condition when built successfully (`ingest.py:160,314`
— FTS unconditional; vec0 only on all-or-nothing success, §2 fact 8), so the fused path *is*
the new primary on a healthy llamacpp install; every lexical-only situation is structurally
excluded from it.

**Core code (module constant + pure function + three-way branch), `src/grc_agent/adapter/rag.py`:**

```python
# Above _query_index, next to _FTS_MAX_TOKENS (rag.py:482):
+# Reciprocal Rank Fusion (RRF): score(d) = sum over input rankings of
+# 1 / (k + rank(d)), ranks 1-based. k = 60 is the constant from the source
+# publication — Cormack, Clarke & Bütcher, "Reciprocal Rank Fusion
+# outperforms Condorcet and individual Rank Learning Methods", SIGIR 2009,
+# pp. 758-759 — where it "was fixed during a pilot investigation and not
+# altered during subsequent validation". A literature constant, not a
+# tuned knob; do not tune it against local metrics.
+_RRF_K = 60
+
+
+def _rrf_fuse(rankings: list[list[int]]) -> dict[int, float]:
+    """Fuse ranked rowid lists via Reciprocal Rank Fusion (k = _RRF_K).
+    Pure and deterministic: no DB, no I/O; ties are broken by ascending
+    rowid at the sort site (stable, DB-deterministic, no relevance meaning)."""
+    scores: dict[int, float] = {}
+    for ranking in rankings:
+        for pos, rowid in enumerate(ranking, start=1):
+            scores[rowid] = scores.get(rowid, 0.0) + 1.0 / (_RRF_K + pos)
+    return scores
```

```python
# rag.py:578-610 — replace the two-way branch with three; the elif/else bodies
# are TODAY'S bodies, verbatim:
         fetch_limit = limit + extra_limit
         vec_available = query_vec is not None and _table_exists(conn, idx_table)
+        fts_available = _table_exists(conn, fts_table)
 
-        if vec_available:
+        fts_result = _fts_query_string(q)
+        fts_query = fts_result[0] if fts_result else None
+
+        if vec_available and fts_available:
+            # Hybrid: both indexes present and the query embedded — fuse.
+            vec_rows = conn.execute(
+                f"SELECT rowid, distance FROM {idx_table} WHERE embedding MATCH ? "
+                f"AND k = ? ORDER BY distance",
+                (sqlite_vec.serialize_float32(query_vec), fetch_limit),
+            ).fetchall()
+            fts_rows = (
+                conn.execute(
+                    f"SELECT rowid FROM {fts_table} WHERE {fts_table} MATCH ? "
+                    f"ORDER BY bm25({fts_table}) LIMIT ?",
+                    (fts_query, fetch_limit),
+                ).fetchall()
+                if fts_query
+                else []
+            )
+            fused = _rrf_fuse(
+                [[row["rowid"] for row in vec_rows], [row["rowid"] for row in fts_rows]]
+            )
+            ranked_rowids = [
+                rowid
+                for rowid, _score in sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))
+            ][:fetch_limit]
+            distance_by_rowid = {row["rowid"]: row["distance"] for row in vec_rows}
+            output_truncated = len(fused) > limit
+            search_mode = "hybrid"
+            query_capped = bool(fts_result and fts_result[1])
+        elif vec_available:
             # ... today's vector branch, rag.py:582-589, byte-identical ...
         else:
             # ... today's lexical branch, rag.py:591-610, byte-identical ...
```

(The `fts_result`/`fts_query` hoist is behavior-neutral for the lexical branch — same values it
already computed at `rag.py:591-592`.)

```python
# rag.py:653-662 (query_catalog render loop) — attach the fused score in hybrid:
     for rowid in result["ranked_rowids"]:
         ...
         if rendered:
+            if result["search_mode"] == "hybrid":
+                rendered["score"] = round(result["score_by_rowid"].get(rowid, 0.0), 4)
             results.append(rendered)
```
with `"score_by_rowid": fused` added to the bundle (`rag.py:620-630`, hybrid branch only;
`{}` in the other two branches so the bundle keeps one shape). `distance` keeps its existing
vector-only meaning (`0.0` for lexical-sourced rows — today's pre-existing convention, §4.5).

**(a) search_mode honesty — the new value and every consumer:**

New value: `"hybrid"` — one token, standard IR terminology, and a single token is what the
GUI string-match and test assertions need. A fused result must not claim `"vector"` (it may
contain rows the vector engine never surfaced — that is the entire point) nor `"lexical"`
(it is not a fallback). Consumers, exhaustively:

1. **Message gates** (`rag.py:672-680` catalog, `rag.py:803-810` docs) — restructure both to:
   ```python
   if result["query_capped"]:
       response["message"] = (
           f"Lexical search truncated the query to the first {_FTS_MAX_TOKENS} word tokens."
       )
   elif result["search_mode"] == "lexical" and (
       result["embed_error"] or resolve_embed_backend(load_settings()) == "llamacpp"
   ):
       response["message"] = _lexical_fallback_message(result["embed_error"])
   ```
   *Equivalence proof for existing modes:* `query_capped` is initialized `False`
   (`rag.py:557`) and set `True` only in the lexical branch (`rag.py:610`), so in vector mode
   the first condition is False and the `elif` fails on `search_mode != "lexical"` — no
   message, as today; in lexical mode the evaluation order is unchanged. *New honesty:* in
   hybrid, a capped FTS MATCH is now disclosed (today's gate would have swallowed it — §5.4),
   and the fallback message is unreachable in hybrid by construction (`embed_error` is `None`
   whenever the hybrid guard passes).
2. **GUI label** (`chat_sidebar.py:171-179`) — add an `elif` branch recognizing `"hybrid"` in
   all three spellings, producing `⚙ query_knowledge (hybrid) ✓`. Without it the suffix
   silently disappears for the new primary mode.
3. **Tool docstring** (`agent.py:544-552`) — the sentence from fix 1 already names all three
   values.
4. **AGENTS.md:90** (Tool Surface row) and **AGENTS.md:43** (`adapter/` file-table row) —
   §7.4.
5. **docs/technical_overview.md:70** — value domain gains `"hybrid"` (§7.4).
6. **Tests**: `tests/test_button_integration.py:283-294` — extend the assertion to
   `in ("vector", "lexical", "hybrid")` (live suite, cannot run here — see §5.7);
   `tests/test_chat_sidebar.py:3047` — add the hybrid label case; the lexical hermetic tests
   (`tests/test_isolation.py:560-686`) assert `== "lexical"` on paths the fusion guard cannot
   reach — unchanged and still passing (verified baseline §2 fact 16).
7. **`_lexical_fallback_message`** — unchanged; never fires in hybrid (gate above).
8. **`ingest.py:17`** docstring mention of `search_mode: "vector"` describes the
   partial-index failure mode and remains accurate — no edit.

**(b) Lexical-only paths that must behave EXACTLY as today (enumerated, each with its pin):**

| # | Path | Guard that excludes it from fusion | Pinned by |
|---|------|------------------------------------|-----------|
| 1 | Native lexical backend (`GRC_EMBED_BACKEND=lexical` ⇒ `model is None`) | `query_vec is None` ⇒ `vec_available` False (`rag.py:556-563,579`) | `tests/test_isolation.py::test_query_catalog_native_lexical_mode_has_no_fallback_message` |
| 2 | Embed call fails at query time (backend down) | `query_vec is None`, `embed_error` set ⇒ lexical + fallback message | `tests/test_isolation.py::test_query_catalog_falls_back_to_lexical_when_embedding_unreachable` (+ docs twin) |
| 3 | DB built lexical-only during a past outage (no `{domain}_idx`) even when embed now succeeds | `_table_exists(conn, idx_table)` False ⇒ lexical + "no vector index" message | `tests/test_isolation.py::test_query_catalog_lexical_message_present_even_when_embed_succeeds` |
| 4 | Vector index present, FTS missing (pre-lexical DBs — but `_build_db` treats those as stale and rebuilds, `rag.py:332-341`; transient/corrupt state only) | `fts_available` False ⇒ today's pure vector branch | new T7 case 3 |
| 5 | DB build/missing failure | returns `{"ok": False, "message": ...}` before any ranking (`rag.py:566-570`) | existing failure tests (`tests/test_adapter_rag.py::test_query_knowledge_func_raises_model_retry_on_failure` upstream of it) |
| 6 | No-word-token query in hybrid | lexical leg contributes an empty ranking; RRF degenerates to vector order; mode stays `"hybrid"` (§5.5) | T6 edge case |

**(c) Truncation semantics under fusion.** Fetch `limit + extra_limit` **per index** (the same
`fetch_limit` expression applied to each), fuse, then `output_truncated = len(fused_pool) >
limit`, and surface the top `fetch_limit` of the fused pool through the *unchanged* caller
loops (catalog renders until `limit`; docs slices to `limit`). Truthfulness proof:

- Let V, L be the fetched sets (each ≤ `fetch_limit = limit+1`), F = V ∪ L the fused pool.
- If each index's full match set is ≤ `fetch_limit`, the fetch returns it *entirely*, so F is
  the true candidate pool ⇒ `|F| > limit` ⟺ entries were dropped. Exact in both directions.
- If any index's match set exceeds `fetch_limit`, then `|F| ≥ fetch_limit = limit+1 > limit` ⇒
  flag True, and the surfaced top-`limit` is a strict prefix of a larger pool — entries
  genuinely dropped. Exact.
- Surfacing: `ranked_rowids` = top `fetch_limit` of F by `(-score, rowid)`; the boolean is
  computed from `|F|` *before* the cut. The IN-resolution query (`rag.py:612-619`) grows from
  ≤ 6 to ≤ 2(limit+1) = 12 placeholders — same single query, no new round-trip.

**(d) Latency budget.** The hybrid leg adds exactly one FTS5 SELECT with LIMIT on the
**already-open** connection (`rag.py:572`; one `sqlite3.connect` per query, unchanged), using
the **already-computed** `query_vec` (`rag.py:556-563`; no re-embedding) and the
**already-verified-fresh** DB (`_FRESHNESS_CACHE` short-circuit, `rag.py:314`). Measured cost
of a full lexical pass: docs median 0.6–1.2 ms, catalog median 6.1 ms (results JSONs), against
a ~32 ms median vector query — hybrid ≈ vector + 2–19%, no new asymptotic term, no second DB
open, no second embedding call.

**(e) AGENTS.md compliance.**
- *Not a hand-picked heuristic:* the rule is one uniform condition applied to every domain and
  every query through the single shared `_query_index` — there is no per-domain branch, no
  per-scenario case, no regex routing, no per-field allowlist (the exact patterns the rule
  prohibits). The one constant, k=60, is adopted from the literature with citation, and the
  paper itself states it was fixed in a pilot and never altered during validation (§2 fact 15)
  — adopting a published constant is the *opposite* of hand-picking; the prohibition targets
  unmeasured local folklore. The `(-score, rowid)` tie-break is a determinism requirement, not
  a relevance opinion. The guard conditions are capability checks (does the DB have both
  indexes; did the embed succeed), the same class of check as the existing
  `vec_available` line.
- **No knob becomes user-tunable.** `settings.py` gains nothing; `GRC_EMBED_BACKEND=lexical`
  remains the single documented opt-out (it yields `model is None` ⇒ lexical-only, path b.1).
  A tunable k or a per-domain fusion toggle multiplies the test matrix with no measurement to
  justify it — rejected (R5).
- **`_FRESHNESS_CACHE`/stale-DB machinery: zero interaction.** The cache keys are
  `(domain, db_path, model)` (`rag.py:272-274,314`); fusion adds no key dimension, no table,
  no `_db_meta` entry; `_build_db`'s validation reads only `sqlite_master` + `_db_meta`
  (`rag.py:318-358`), none of which fusion alters (§2 fact 11).
- **Harness vs app code — the engine change belongs in the app.** Installed harness 0.23.0
  ships no retrieval capability (`__all__` verified, §2 fact 13); `ConversationSearch` is BM25
  over persisted *chat step history*, not domain corpora; upstream docs (Context7) surface only
  ExaSearch (web). AGENTS.md's "prefer pydantic_ai's sanctioned extension points" governs
  agentic-loop/retry/context logic; the sanctioned boundary here is the plain
  `Tool(fn, name=..., description=...)` registration (`agent.py:833-839`), which fix 2 does not
  touch — the engine behind it is deliberately app-local (one file, `rag.py`, over sqlite-vec +
  FTS5). Moving fusion into the harness would require upstreaming a GNU Radio-specific engine
  into a generic capability library for a single consumer — the opposite of "No Brittle
  Reinventions" at the harness layer.

**(f) Determinism for tests.** RRF over rank *positions* (not scores) is deterministic given
the two input orders; sqlite-vec KNN `ORDER BY distance` and FTS5 `ORDER BY bm25(...)` are
deterministic for a given DB; the tie-break is total (`(-score, rowid)`). Test design: a pure
unit test feeds synthetic rankings (T6); the integration test builds a both-indexes DB with a
deterministic fake embedder and queries it with the same fake (T7) — no server, no network;
true-vector behavior is covered by the skipif-provisioned variant (T3) only.

**Rejected alternatives**

- **R5 — Weighted-sum / linear score fusion** (e.g. `α·(1−dist) + (1−α)·bm25_norm`): requires
  normalizing two incommensurable scales (cosine distance ∈ [0,~2], FTS5 bm25 negative and
  unbounded) and a hand-tuned α — a textbook hand-picked heuristic with no corpus-agnostic
  justification; CombMNZ-style schemes need per-system score functions the paper itself shows
  RRF beats without (§2 fact 15). RRF consumes ranks only — no normalization, no weights.
- **R6 — A user knob for fusion on/off or k** (env var / setting): unmeasured tuning surface;
  the backend choice already exists as the escape hatch; AGENTS.md "Simplify First" and the
  no-heuristics rule both cut against it. If a corpus ever measurably needs different k, that
  is new evidence demanding a new brief — not a runtime knob.
- **R7 — Clean up `distance` semantics in the same landing** (omit the key or make it nullable
  for lexical-sourced rows): touches `render_catalog_block`'s output contract consumed by
  every catalog test; the `score` field already gives the model a truthful ranking signal.
  Defer; recorded as pre-existing wart (§4.5).
- **R8 — Fuse always, even when only one index exists** (treat a missing index as an empty
  ranking): observably identical to the single-index branch but renames every lexical-mode
  result to `"hybrid"`, breaking the honesty contract that lexical *fallback* must say
  "lexical" (`_lexical_fallback_message` semantics, tests b.1-b.3). The both-indexes guard is
  what keeps fallback honest.
- **R9 — Move fusion into the harness / pydantic-ai**: refuted by §2 fact 13 + §7.2(e) — no
  such capability exists in 0.23.0 or upstream docs, and the domain engine is app-local by
  design.
- **R10 — Fuse by round-robin interleave** (take 1 from each list alternately): destroys the
  "both engines agree" signal that the stress data shows is the win (11/15 vector misses
  rescued by lexical rank ≤ 4, and vice versa); RRF's squared penalty for disagreement is the
  property that approximates the union at 0.966.

### 7.3 Fix 3 — The 5 stress-designed tests, updated for the post-fix world (+ 2 fusion tests)

Shared fixture idiom (verbatim from `tests/test_adapter_rag.py:19-20`, already the repo
convention, also used by `/tmp/grc_stress/run_backend.py:16-19`):

```python
monkeypatch.setenv("GRC_AGENT_VECTORS_DIR", str(tmp_path / "vectors"))
monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
```
plus `save_settings(...)` for the backend under test, `_FRESHNESS_CACHE.pop(domain, None)` and
(where a fake embedder is used) `_EMBEDDING_DIM_CACHE` isolation in `finally` — the exact
discipline already practiced in `tests/test_isolation.py` and
`tests/test_adapter_rag.py:25-28,60-66`.

**T1 — `test_docs_exact_phrase_ranks_top1_lexical`** (in `tests/test_isolation.py`, beside the
other engine tests)
- *Fixture:* the shared idiom + `save_settings("ollama_local", "<any-model>",
  embed_backend="lexical")` ⇒ `model=None`, zero embed calls; real shipped corpus via the
  default `docs_dir()`; cold-build via the first `query_docs` call.
- Curated phrases: the 7 verbatim-unique exact-tier phrases the stress proved lexical ranks
  top-1 (from `results_llamacpp.json` misses with `lex_rank=1`): "which gives 0.898757",
  "must be accounted for when reasoning about stream rates", "binary symmetric NRZ line codes",
  "The ZMQ stream blocks have the option to pass tags", "sent over the _ok_ or _fail_ output
  ports", "This addition of the extra zeros is called _zeropadding_", "use a _Window Type_ of
  _Rectangular_".
- Assertions per phrase: (1) *corpus-drift guard* — the phrase (case-insensitive) still occurs
  in some `docs_dir()/*.md`, else skip that phrase, with `assert present >= 5` so the test can
  never rot into vacuousness; (2) `query_docs(phrase, limit=5)` → `ok`, `search_mode ==
  "lexical"`, and the rank-1 chunk's `path:` header names a file that contains the phrase
  (parse the answer's first chunk header; read the file).
- Why hermetic: lexical backend means no embed server, no runtime, pure SQLite over the shipped
  corpus; cost is one FTS ingest of 718 chunks (sub-second — the stress harness cold-built in
  ~0 s per its own `docs_cold_query_seconds: 0.0`).
- Why it exists: pins the exact-phrase *floor* (0.978 hit@1) that fusion must not regress, and
  documents the 7 verbatim-unique phrases that motivated fix 2.

**T2 — `test_docs_and_catalog_both_report_output_truncated`** (in `tests/test_isolation.py`)
— *redesigned replacement for the old "output_truncated documentation" test, pinning the FIXED
behavior.*
- Fixture: shared idiom + `save_settings(..., embed_backend="lexical")` +
  `GRC_AGENT_DOCS_DIR` → tmp dir with two small `.md` files yielding exactly 7 chunks
  (`#`/`##` headings drive `_chunk_markdown`, `ingest.py:199-215`); pop
  `_CORPUS_VERSION_CACHE["docs"]` in setup **and** `finally` (cross-test pollution hazard,
  §5.8); cold-build via `query_docs`.
- Assertions (docs, the fixed behavior): the response **contains** `output_truncated`
  (today the key is absent — `rag.py:797-802`); `limit=5` ⇒ `output_truncated is True` (7-chunk
  pool > 5 — proves the key can fire on docs); `limit=20` ⇒ `False` (truthful negative on a
  small corpus — the brief's required case, made non-vacuous by using a 7-chunk corpus, not a
  1-chunk one); the answer contains exactly `min(5, 7) = 5` `---`-separated chunks at
  `limit=5` and 7 at `limit=20` (pins the slice contract of §7.1).
- Assertions (catalog, the unchanged side): same-shaped small catalog DB via
  `ingest_catalog` ⇒ response carries `output_truncated` (`rag.py:668`) with truthful
  True/False for the same limits.
- Why hermetic: lexical backend + tmp corpora; no gnuradio platform needed for the docs half,
  platform only for the catalog half (already a suite dependency).

**T3 — `test_catalog_docstring_units_semantics_retrieval`** (skipif-provisioned) **+ hermetic
lexical twin `test_catalog_docstring_units_semantics_retrieval_lexical`**
  (both in `tests/test_isolation.py`)
- Provisioned variant: `@pytest.mark.skipif(not embed_runtime.is_alive(), reason="llama.cpp
  embedding runtime not provisioned")` (`is_alive` at `src/grc_agent/embed_runtime.py:619`
  checks socket health without starting anything — cold tests must never download or spawn a
  server); `save_settings(..., embed_backend="llamacpp")`; run the 7-query catalog matrix
  copied from `/tmp/grc_stress/run_backend.py:25-33`. Assert per query: expected `block_id`
  present, rank ≤ 2, `doc` non-empty and containing the required substrings
  ("radians per sample"+"NOT HERTZ" / "initial item offset" / "radians/sample" / "Costas loop"
  / "deemphasis" / "samples_per_sec" / "quadrature demodulator" — the exact `doc_check`
  predicates from the stress JSONs, 7/7 verified on both backends, §2 fact 7), and
  `search_mode in ("vector", "hybrid")` post-fix. This is the only non-hermetic test of the
  set (cold catalog build ≈ minutes of embedding on first provisioned run; acceptable for a
  skipif-gated suite).
- Lexical twin: identical matrix with `embed_backend="lexical"` — the stress proved 7/7 rank ≤ 2
  *and* 7/7 `doc_check` on the lexical backend too (ranks 1,1,1,1,1,2,1), so the twin is fully
  hermetic *and* non-vacuous; it pins the D2 payload enrichment
  (`_compose_catalog_text`, `ingest.py:180-192`) against composition regressions.
- Why the pair: the provisioned variant validates the *vector* ranking path end-to-end; the
  twin validates the same contract hermetically so a provisioned-less machine still guards the
  docstring feature.

**T4 — `test_db_build_meta_integrity`** (in `tests/test_isolation.py`)
- Fixture: shared idiom; two flavors — (a) `embed_backend="llamacpp"` with the deterministic
  fake embedder of T7 (`ingest_mod.embed_document` monkeypatched; `_EMBEDDING_DIM_CACHE`
  cleared); (b) `embed_backend="lexical"`. Call `ingest_catalog`/`ingest_docs` directly.
- Assertions per domain/flavor: `_db_meta["corpus_version"] == _corpus_version(domain)` (the
  stress's `corpus_version_expected == db_meta` invariant); `embedding_model` present iff the
  vec0 index exists (the exact asymmetry `_write_meta` implements, `ingest.py:56-66`);
  `sqlite_master` contains `{domain}_chunks`, `{domain}_fts`, and `{domain}_idx` iff vector;
  the vec0 dim parsed from `sqlite_master.sql` matches the fake vector length (same regex
  `_build_db` uses, `rag.py:343-346`); FTS `content='{domain}_chunks'` external-content config;
  chunk count equals the ingest return value.
- Why hermetic: pure SQLite assertions over a tmp DB; the fake embedder is the only stand-in.

**T5 — `test_stale_db_rebuilds_on_corpus_version_change`** (in `tests/test_isolation.py`)
- Fixture: shared idiom + lexical backend + tmp docs corpus (7 chunks) + `GRC_AGENT_DOCS_DIR`;
  cold-build; then corrupt: `UPDATE _db_meta SET corpus_version='0000000000000000'`;
  `_FRESHNESS_CACHE.pop("docs", None)` (the cache is per-process — `rag.py:272-274` — so
  popping simulates the fresh process that would notice).
- Assertions: next `query_docs` rebuilds — `_db_meta["corpus_version"]` restored to the live
  `_corpus_version("docs")`, chunk count restored, query succeeds with `search_mode ==
  "lexical"`. Negative case (the anti-thrash rule, `rag.py:393-418`): with a *fresh* DB,
  monkeypatch `ingest.ingest_docs` with a counting fail-loud stub and assert zero calls on a
  subsequent query — a healthy lexical-only DB must never rebuild merely because the vector
  index is absent.
- Why hermetic: no embedder anywhere; the staleness check is pure metadata.

**T6 — `test_rrf_fuse_pure`** (new, in `tests/test_adapter_rag.py` — the pure-unit home; no DB)
- Import only `grc_agent.adapter.rag._rrf_fuse` (and `_RRF_K`).
- Cases: single ranking `[10, 11]` → scores `{10: 1/61, 11: 1/62}` in order; two rankings
  `[[1, 2], [2, 3]]` → rowid 2 scores `2/(k+2) > 1/(k+1)`, 1 → `1/61`, 3 → `1/62`, order
  `[2, 1, 3]` after the `(-score, rowid)` sort (the "both engines agree" property, asserted
  numerically against k=60); disjoint equal-rank lists interleave deterministically; tie case
  `[[1], [1]]`-style symmetric profiles → equal scores → ascending rowid; empty input lists and
  an empty rankings list → `{}`; a 100-element list stays O(n) with exact float sums (compare
  against `sum(1/(k+r))`).
- Why hermetic: no I/O, no embed, no platform — trivially.

**T7 — `test_hybrid_fusion_when_both_indexes_present`** (new, in `tests/test_isolation.py`)
- Fixture (fully hermetic — this is the "lexical-forced/DB-shape" tier of the brief's two-tier
  integration plan): shared idiom + `save_settings(..., embed_backend="llamacpp")` +
  deterministic text-derived fake embedder shared by all three patch points:
  ```python
  def fake_embed(text, model):  # noqa: ARG001
      h = hashlib.sha256(text.encode()).digest()
      return [b / 255.0 for b in h[:3]]          # 3-dim, deterministic, text-derived
  monkeypatch.setattr(ingest_mod, "embed_document", fake_embed)   # ingest probe+items (hook ingest.py:119)
  monkeypatch.setattr("grc_agent.adapter.rag.embed_document", fake_embed)  # dim check (rag.py:_get_embedding_dim)
  monkeypatch.setattr("grc_agent.adapter.rag.embed_query", fake_embed)     # query time
  ```
  (All three are required: the ingest hook, the `_build_db` dimension check, and
  `embed_query` are separate module attributes; missing the second would send `_build_db` into
  `embed_runtime.ensure_server()` — a real download/start, breaking hermeticity. `_EMBEDDING_DIM_CACHE`
  cleared in setup and `finally`, `_FRESHNESS_CACHE` popped, `_CORPUS_VERSION_CACHE` untouched
  since the real platform is used.) `ingest_catalog(db_path, model)` ⇒ DB has BOTH
  `catalog_idx` (float[3]) and `catalog_fts`.
- Case 1 (the rule): `query_catalog("low pass filter", 5)` → `search_mode == "hybrid"`;
  results non-empty and ≤ 5; `output_truncated` present and truthful (718-block platform ⇒
  True); **no** `message` key (hybrid is not a fallback — pins the restructured gate);
  every result carries `score`; vector-sourced results carry a real `distance`.
- Case 2 (fallback against a both-indexes DB): same DB, but `rag.embed_query` patched to
  raise ⇒ `search_mode == "lexical"` + fallback `message` present (pins §7.2(b) path 2 in the
  *hardest* configuration — today's code already does this; the fusion branch must keep it).
- Case 3 (single-index guard): drop `catalog_fts` by hand (`DROP TABLE`) ⇒ `fts_available`
  False ⇒ pure vector branch, `search_mode == "vector"` (pins §7.2(b) path 4).
- Note recorded in the test docstring: this is the first hermetic test to exercise a full
  fake-embed vec0 build + KNN query (§6 item 4); the KNN syntax is production-proven
  (`rag.py:583`).
- Complementary GUI pin: extend `test_query_knowledge_label_shows_search_mode`
  (`tests/test_chat_sidebar.py:3047`) with
  `_tool_label("query_knowledge", result='{"search_mode": "hybrid"}') ==
  "⚙ query_knowledge (hybrid) ✓"`.

### 7.4 AGENTS.md / docs edit list (rows and lines that change)

1. **`AGENTS.md:90`** (Tool Surface, `query_knowledge` row) — replace the engine sentence with:
   vector search is primary; **when the DB contains both a vec0 index and an FTS5 index and the
   query embeds, the two rankings fuse via Reciprocal Rank Fusion (k=60, Cormack/Clarke/
   Bütcher SIGIR 2009) and `search_mode` is `"hybrid"`**; the lexical-only fallback paths
   (embed failure, no vector index, lexical backend) are unchanged and still tag
   `search_mode: "lexical"`; every response carries `search_mode` and `output_truncated`
   (true when matching entries existed beyond the k returned); keep the `k` (1-20) sentence.
2. **`AGENTS.md:43`** (`adapter/` file-table row) — "`rag.py` (catalog/docs vector RAG with
   cached embed client)" → "`rag.py` (catalog/docs RAG: hybrid vector+FTS5 retrieval with RRF
   fusion, cached embed client)".
3. **`AGENTS.md` Key Conventions** — add one bullet adjacent to the all-or-nothing bullet
   (`AGENTS.md:134-136`): **"Hybrid retrieval is one uniform rule"** — when the resolved DB has
   both indexes and the query embedded, vector + lexical fuse via RRF (`rag._RRF_K = 60`,
   literature constant — never tuned locally); lexical-only paths behave exactly as before;
   `search_mode ∈ {vector, lexical, hybrid}` and `output_truncated` are the honesty contract;
   per-index over-fetch (`extra_limit=1`) keeps the flag truthful under fusion.
4. **`docs/technical_overview.md:70`** — `"search_mode": "vector" | "lexical"` →
   `"vector" | "lexical" | "hybrid"` and one clause noting the RRF fusion default on healthy
   llamacpp DBs.
5. **`CHANGELOG.md` `[Unreleased]` → Added/Changed** — one entry each for the docs truncation
   symmetry and the hybrid default (no version bump — AGENTS.md forbids bumps without explicit
   user approval).

Not edited (checked): `AGENTS.md:134` (all-or-nothing) and `AGENTS.md:136` (built-not-shipped)
remain true; `ingest.py:17`'s `search_mode: "vector"` mention describes the failure mode and
remains true.

### 7.5 Risk table

| # | What could regress | Mechanism | Likelihood | Impact | Mitigation / evidence |
|---|--------------------|-----------|------------|--------|----------------------|
| 1 | Existing lexical-backend DBs (`*_lexical.db`, no vec0) change behavior | Hybrid guard requires `idx_table` to exist AND `query_vec` non-None (`rag.py:579`); lexical DBs have neither | Very low | High | Both branches byte-identical; pinned by three existing hermetic tests (§7.2(b) paths 1-3, baseline green §2 fact 16) |
| 2 | Embed-failure fallback regresses (the most safety-critical path) | `query_vec is None` on exception (`rag.py:559-563`) excludes hybrid structurally | Very low | High | T7 case 2 pins the fallback against a both-indexes DB; existing fallback tests stay green |
| 3 | GUI expander label loses its mode suffix on the new primary mode | `chat_sidebar.py:171-179` string-matches only `vector`/`lexical` | Certain if unpatched | Low (cosmetic but silent) | Hybrid label branch + test case in the same landing (§7.2(a) item 2) |
| 4 | Live integration suite goes red on provisioned runs | `tests/test_button_integration.py:294` asserts `search_mode in ("vector", "lexical")` | Certain if unpatched | Medium (CI/provisioned runs) | Extend the assertion to include `"hybrid"` in the same landing; cannot be validated locally (skipif on `OLLAMA_CLOUD_API_KEY`, `:47-51`) — flagged in §6 |
| 5 | ConversationSearch behavior drift | It BM25-indexes persisted *step history* (`conversation_search/__init__.py:1`); tool-result strings gain "hybrid" tokens | Negligible | Negligible | No structural coupling; content-agnostic index |
| 6 | GUI poller `_poll_indexing` | Reads `rag._rag_building` only (`chat_sidebar.py:1268-1310`; writers `rag.py:426-462`) — fusion touches neither | None | None | Verified: build-state machinery unchanged |
| 7 | tool_overflow interplay | (a) `StopGracefully` ceiling `max_requests=40` (`agent.py:429`, wired default `agent_factory.py:799,826`) — better per-call recall can only *reduce* query counts; (b) harness `ToolOutputLimits` exists but is not wired (grep: no wiring) so no truncation layer conflicts with the flag; (c) `_MAX_TOOL_DISPLAY_CHARS = 8000` (`chat_sidebar.py:137`) is UI-display-only | Low | Low | One extra JSON key + occasional `score` fields are orders of magnitude under every bound; the historical 18-requery loop was a compaction bug (AGENTS.md:157), orthogonal |
| 8 | Hybrid changes ranking on provisioned backends unexpectedly | New primary path | Expected (that is the fix) | Medium if it regresses | Re-run the stress harness post-landing (§6 item 1); accept only ≥ best-single 0.871 with the union 0.966 as reference; never tune k to the number |
| 9 | `output_truncated` information density (near-always True on 568/718-chunk corpora) | Pool ≥ limit+1 for most real queries (7/7 observed, §2 fact 6) | Certain in production | Low (flag fatigue) | Honest boolean + actionable docstring sentence; count-field escape hatch documented as rejected-for-now (R3) |
| 10 | Determinism of fused order across rebuilds | Tie-break by `rowid` is insertion-order-dependent; corpus changes rebuild the DB and reassign rowids | Low | Low | Ties are rare (symmetric rank profiles only); order remains deterministic *per DB build*; T6 pins the rule |
| 11 | `distance: 0.0` on lexical-sourced hybrid rows reads as a perfect vector match | Pre-existing convention (`rag.py:603,658`) | Certain | Low (mitigated) | `score` field carries the truthful fused rank signal; full `distance` cleanup rejected for now (R7, §4.5) |
| 12 | `_CORPUS_VERSION_CACHE` cross-test poisoning from tmp-corpus tests | Cache keyed by domain only (`rag.py:189,233-234,262`) | Certain if undisciplined | Medium (order-dependent greens) | Setup/finally pop in T2/T5 fixture contract (§5.8) |

### 7.6 Implementation order (every step stays green)

1. **Land `_RRF_K` + `_rrf_fuse` + T6** (`rag.py` constants/function + pure test). Green: new
   test passes, nothing else changes (the helper is briefly unused — it is wired in step 3;
   do not land steps 1 and 3 far apart).
2. **Land fix 1 + T2** (`rag.py:781-789` extra_limit, `:794` slice, `:797-802` response key;
   `agent.py` docstring sentence with `vector|lexical` values; T2). Green: no existing test
   asserts the docs response key set; answer bytes unchanged (proof in §7.1).
3. **Land fix 2 + all its consumers + T7 + label-test case + doc edits** (`rag.py` three-way
   branch + `_rrf_fuse` wiring + message-gate restructure + `score`; `chat_sidebar.py:171-179`
   hybrid branch; `AGENTS.md:43,90` + Key Conventions bullet; `docs/technical_overview.md:70`;
   `CHANGELOG.md`). Green because hermetic suites only ever exercise lexical-only or fake-DB
   paths except T7 itself.
4. **Land T1, T3 (+twin), T4, T5** — the quality/meta regression net (all green on the fixed
   code by the stress evidence, §2 facts 1-9).
5. **Same-landing edit of the live suites** — `tests/test_button_integration.py:283-294`
   accept `"hybrid"` (executed only on provisioned/Ollama-Cloud runs; recorded as required for
   landing, §5.7).
6. **Post-landing validation** — re-run `/tmp/grc_stress/run_backend.py llamacpp` against the
   fused engine and report the realized docs hit@5/MRR per tier against the 0.871 single / 0.966
   union references; run the full hermetic suite (`pytest tests/test_adapter_rag.py
   tests/test_isolation.py tests/test_chat_sidebar.py -q`).

---

*Report generated by the read-only investigation auditor; no source file was modified. All
anchors reference the working tree as read in this session (HEAD `c4c2008` + pre-existing dirty
worktree; `git diff --cached` empty at audit time).*