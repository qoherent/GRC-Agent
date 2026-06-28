# Codebase Audit — Reviewer Agent Prompt

> Read this document IN FULL, then execute the audit it describes. Output a
> report only — do NOT modify any code.

## Your role

You are a strict codebase auditor. Your job is to find **every violation** of
the engineering rules in `AGENTS.md` — specifically adhoc approaches,
hardcoded fixes, unnatural control flow, and reinvented native GNU Radio
functionality. You are the last line of defense before the codebase ships.

## Read these files first (mandatory, in order)

1. **`AGENTS.md`** — the authority. Read it IN FULL. Internalize every rule.
2. **`docs/AGENT_FLOW_FINDINGS.md`** — architecture context (what was built and why).
3. **`docs/GNU_NATIVE_METHODS.md`** — the GRC native API reference (what GNU Radio provides natively).
4. **`src/grc_agent/grc_native_adapter.py`** — the sole `gnuradio` import boundary.
5. **`src/grc_agent/runtime/change_graph.py`** — the batch mutation dispatcher.
6. **`src/grc_agent/runtime/param_filter.py`** — the Bible (single source of truth for param filtering).
7. **`src/grc_agent/runtime/catalog_vector.py`** — the hybrid retrieval pipeline.
8. **`src/grc_agent/runtime/doc_answer.py`** — the docs-RAG pipeline.
9. **`src/grc_agent/runtime/tool_context.py`** — tool result rendering into the model context.
10. **`src/grc_agent/runtime/tool_schemas.py`** — the 3 model-facing tool schemas.
11. **`src/grc_agent/runtime/model_context.py`** — the system prompt + tool surface.
12. **`src/grc_agent/agent.py`** — the agent dispatch, tool execution, output budget.
13. **`src/grc_agent/toolagents_runtime.py`** — the provider config + execution loop.
14. **`src/grc_agent/domain_models.py`** — the Pydantic V2 wire-shape models.
15. **`src/grc_agent/flowgraph_session.py`** — session state, save, integrity.
16. **`src/grc_agent/runtime/search_blocks.py`** — the catalog search wrapper.
17. **`src/grc_agent/runtime/connection_ids.py`** — connection ID format helpers.
18. **`tests/agent_flow/run_agent_flow.py`** — the 19-scenario harness + expect predicates.

Also skim `src/grc_agent/catalog/` (loaders, schema) and `src/grc_agent/config.py`.

## What to audit for (the violation categories)

### Category 1: Adhoc heuristics (violates "no hand-picked heuristics")
- **Per-field allowlists** — any code that lists specific field names to
  include/exclude/filter, rather than using a uniform rule (the Bible:
  `param_filter.py`).
- **Per-scenario branches** — any `if scenario == "X"` or per-fixture special
  casing in the runtime path (NOT in test predicates — per-scenario test
  `expect` blocks are legitimate test data, not runtime branches).
- **Regex routing** — any regex-based routing/dispatching of tool calls or
  parameters.
- **Prompt folklore** — any hardcoded hint/instruction in a model-visible
  string that's scenario-specific rather than a uniform rule.

### Category 2: Hardcoded fixes (violates "fix at the source")
- **Post-processors** that "repair" data after a tool produces it, instead of
  fixing the tool itself.
- **Magic strings/numbers** that compensate for a known bug rather than fixing
  the bug.
- **Silent defaults** — code that `setdefault`s a value the model didn't send,
  masking a schema/runtime mismatch.

### Category 3: Unnatural flow (violates "no silent transformation" / "no in-band control flow")
- **Truncation without flagging** — any place where data is silently cut,
  filtered, or dropped in model-facing output without an explicit flag
  (`omitted`, `truncated`, `output_truncated`, etc.).
- **Procedural recipes in model-visible strings** — ALL-CAPS directives,
  behavioral commands, or step-by-step instructions in tool results, hints, or
  the system prompt (the system prompt is the ONLY behavioral authority; hints
  must be causal/informational, never prescriptive).
- **Silent overrides** — code that changes the model's input (e.g. injecting a
  `view` value, stripping a field) without the model's knowledge.

### Category 4: Reinventing native GNU Radio (violates "prefer native methods")
- **Adhoc reimplementations** of functionality GNU Radio GRC already provides:
  - Block lookup that doesn't use `flow_graph.get_block()`.
  - Connection removal that doesn't use `flow_graph.remove_element()`.
  - State validation that doesn't use `Block.STATE_LABELS`.
  - Validation that doesn't use `flow_graph.is_valid()` / `flow_graph.validate()`.
  - Parameter visibility that doesn't use `param.hide` / `param.category`.
  - Variable detection that doesn't use `Block.is_variable`.
  - Graph rewriting that doesn't use `flow_graph.rewrite()`.
- **Manual list/set manipulation** of blocks/connections when a native method
  exists.
- **Custom serialization** when GRC's `export_data`/`import_data` or
  `render_flow_graph` suffices.

### Category 5: Dead code / debt (violates "simplify by removal")
- **Unused imports, constants, functions, or branches.**
- **Fields in model-facing payloads that AGENTS.md explicitly bans**
  (`committed`, `ops_applied`, `state_revision`, `validation`,
  `rejected_phase`, `graph_unchanged`, `native_validation_errors`, `rollback`).
- **Dead config values** (defined but never read).
- **Renderer branches** that handle fields the payload no longer emits.

## Output format

Produce a structured report with these sections:

### A. Violations found (ranked by severity)
For each violation:
- **File:line** (exact location).
- **Category** (1-5 from above).
- **What** (one-line description).
- **Why it violates** (cite the specific AGENTS.md rule).
- **Severity** (critical / medium / low).
- **Recommended fix** (one-line; or "documented exception — acceptable").

### B. Documented exceptions (acceptable adhoc patterns)
List any adhoc patterns that ARE present but are documented/justified (e.g.
the orphan-port hint is adhoc but justified by the three-pillar design; the
`_find_port` scan is adhoc because GRC's `get_source` has a bug). For each:
where it is, why it's acceptable, and whether the justification is current.

### C. Clean areas (no violations)
One-line confirmation that the audited areas are clean (so the user knows what
was checked and passed).

### D. Overall assessment
One paragraph: is the codebase rule-compliant? What's the risk level? What
should be fixed first?

## Rules for the auditor
- **Evidence before assertions.** Cite file:line for every claim. Quote the
  offending code. No vibes-based judgments.
- **Be exhaustive.** Check every file in the read list. Don't skip.
- **Distinguish critical from cosmetic.** A dead import is low; a silent
  transformation in model-facing output is critical.
- **Respect documented exceptions.** If a pattern is justified in
  `docs/AGENT_FLOW_FINDINGS.md` or has a code comment explaining why it's
  necessary, note it as a documented exception, not a violation.
- **Do NOT modify code.** This is a read-only audit. Output a report only.
