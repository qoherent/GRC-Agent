# Tool-Output Native Refactor — Remaining Phases (2–6)

> Applies the methodology now in `AGENTS.md`: general rules (no ad-hoc), native
> methods where one exists, fix at the source, no silent transformation.
> **Verification protocol for every phase** (mandatory):
> 1. TDD — write the failing test first, watch it fail for the right reason.
> 2. Implement the minimal change.
> 3. Unit green + full-suite regression run.
> 4. **Behavioral inspection** — render the real model-visible output and have a
>    fresh subagent verdict it (a pass is not "working"). Cite the observation.
> 5. Update any size-guard test that encoded the old ad-hoc limit.

Phase 1 (inspect param selection → native GRC `hide` filter) is **done and verified**.

---

## Phase 2 — Flag connection truncation in inspect (finishes R3)

**Problem:** `inspect_graph` details caps per-block connections at
`max_connections_per_block` (`inspect_graph.py:604-605`) with **no truncation
flag**, while params are flagged (`more_params_available`). Overview already
flags connection omission; the gap is the details per-block cap. (The
compactor's separate 8-conn cap is handled in Phase 4.)

**Approach (general, no hand-picking):** mirror the existing params flag
pattern. When `incoming`/`outgoing` are sliced, emit one uniform flag on the
row, e.g. `connections_truncated: {incoming: N, outgoing: M}`. No per-block
special casing.

**Handling:** in `_block_details_row`, capture the pre-slice counts, and add
the flag when `len > cap`. Surface it through `_compact_inspect_targets`
(`tool_context.py`) so it reaches the rendered output.

**Testing:**
- Unit: a block with >`max_connections_per_block` connections (construct one
  in a temp graph, or lower the cap via a fixture override) → assert the flag
  fires with correct counts.
- Behavioral: render details; subagent confirms the flag is present, honest,
  and that non-truncated blocks do **not** show it (no false positives).

---

## Phase 3 — Resolve the params-in-overview conflict (R1)

**Problem:** overview silently deletes the `params` filter
(`inspect_graph.py:119` `del targets, params`), but the schema advertises
`params` as a filter ("For X on Y: targets=['Y'], params=['X']"). The model
passes it, gets a full dump, and is confused (this bit R0 scenario-4).

**Approach (native behavior, no silent drop):** overview is the whole-graph
view, so a param filter doesn't apply there. Two clean options — pick one with
the maintainer:
- **(A) Honor it** — overview applies `params` as a cross-block param filter
  (only matching param keys shown per block). Most useful; uniform.
- **(B) Reject honestly** — if `params` is given without `targets`, return a
  factual `params_filter.unmatched` note (not a silent ignore) telling the
  model the filter needs a target (→ use details).

Either is general; the rule is "never silently ignore a declared arg."

**Handling:** edit `_overview` / the view-selection in `_normalize_inspect_graph_args`
(`agent.py:533`). Remove the silent `del`.

**Testing:**
- Unit: `inspect_graph({params:["samp_rate"]})` (no targets) → assert it
  either filters (A) or returns the factual unmatched note (B); never a silent
  full dump with no acknowledgement.
- Behavioral: subagent confirms the model-visible output explicitly
  acknowledges the `params` arg.

---

## Phase 4 — Eliminate the `_compact_*` allowlists (the big legacy removal; R1/R2)

**Problem:** `tool_context._compact_change_graph` / `_compact_inspect_graph`
(and `_compact_search_blocks` / `_compact_ask_grc_docs`) are **hand-picked
field allowlists** that silently drop raw fields the tools return — exactly the
"post-processor carves it down" pattern AGENTS.md now forbids. They also carry
per-field `_short_text` clips and a hard 8-connection cap, all unflagged.

**Approach (general):** make the **tool the source of truth** for the
model-visible shape. Replace every allowlist with a single uniform transform:
  1. **Drop-empty only** — recursively drop `None`/`""`/`[]`/`{}` (`is_meaningful`).
  2. **One uniform char budget** (Phase 5) — proportional slice + sentinel.
No field allowlists; nothing hand-picked.

**Handling (per-tool, subagent-driven):**
- Move any truly-needed shaping into the tool itself (`inspect_graph.py`,
  `change_graph.py`, `query_knowledge` path) so the raw payload *is* the
  model-ready shape.
- Reduce `_compact_wrapper_result` to: drop-empty + budget. Remove the per-tool
  allowlist functions.
- Remove the compactor's hard 8-connection cap (Phase 2's flag covers the
  tool-level cap).

**Risk:** the allowlists exist to keep output small for small local models.
The uniform budget (Phase 5) replaces them — token cost stays bounded, just
without silent curation. **Update size-guard tests** (`<3650 overview`,
`<1200 details`, `<6000 change_graph`) — they encode the old ad-hoc limits;
re-express them as honest budget checks.

**Testing:**
- Unit per tool: every non-empty field the tool returns survives to the
  rendered output; only empties dropped; budget truncation flagged with sentinel.
- Behavioral (subagent, per tool): render real inspect/change_graph/
  query_knowledge outputs; verdict that nothing meaningful was silently lost
  and size is still bounded.

---

## Phase 5 — Uniform char budget (R4; folds into Phase 4)

**Problem:** `_short_text` clips per field (`message`≤180, `hint`≤260,
`err`≤100/220, etc.) — ad-hoc per-field limits.

**Approach (general):** one per-result character budget; proportional slice +
`... [TRUNCATED … was N, kept M]` sentinel. Reuse the proven
`compact_chat_history` algorithm. Replaces all per-field `_short_text` limits.

**Handling:** a single budget function applied to the rendered result after
drop-empty. Remove `_short_text` call sites (keep the helper only if used by
`compact_chat_history`).

**Testing:**
- Unit: a fixture producing a >budget message/result → truncated once,
  sentinel present, N computed exactly once.
- Behavioral: subagent confirms long validation errors are still readable up to
  the cut and the cut is announced.

---

## Phase 6 — Episode-pruning honesty (R6; currently zero test coverage)

**Problem:** `_prune_completed_episodes` (`model_context.py:129`) silently
erases **all** tool evidence from completed prior turns (keeps only assistant
text). This creates multi-turn blindness and has no tests.

**Approach (general/honest — never silently erase):** let the uniform
compaction budget (Phase 5 / `compact_chat_history`) manage size honestly
rather than hard-erasing by episode. If prior tool evidence must shrink, it
shrinks under the budget with a sentinel — it is not categorically deleted.

**Handling:** revise `_prune_completed_episodes` to retain prior turns' tool
content (the budget handles size), or — only if a hard cap is unavoidable —
replace erased tool evidence with a factual marker, never silent deletion.
**Add the missing tests first** (characterize current behavior, then assert
the new honest behavior).

**Testing:**
- Unit: a 2+ turn history where turn 1 used `inspect_graph` → after rendering,
  turn-1 tool evidence is either retained or honestly marked (assert which).
- Behavioral: subagent confirms the model can still see (or is told about)
  prior-turn tool results, i.e. no silent multi-turn blindness.

---

## Cross-cutting

- After each phase: re-run `tests.test_change_graph_flat_batch
  tests.test_mvp_tool_profile tests.test_runtime_tool_validation` plus the
  phase's new test, then the full suite.
- The pre-existing `test_search_blocks_exact_catalog_match…` flakiness is
  order-dependent catalog-cache pollution (unrelated) — run modules in the
  order above to avoid it; track separately.
- Any phase that changes model-visible shape → regenerate
  `docs/MODEL_CONTEXT_BIBLE.md` only if the **schema** changed (these phases
  change **output**, not schema, so the bible is unaffected).
