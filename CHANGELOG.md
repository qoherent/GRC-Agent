# Changelog

## 2026-06-11 — Harness Refactor (Empirical)

### Phase 5: Free-Style Harness
- **Finding:** Removing ALL-CAPS behavioral commands from the system prompt and sanitizing tool descriptions eliminated the `inspect_graph` infinite loop (zero deduplicated calls vs. 4-5 in old harness).
- **Finding:** The "Always call inspect_graph before change_graph" string in the tool description was the single most expensive token in the codebase — deleting it cured the looping.
- **Finding:** Complete removal of structural rules (variables, wire insertion, bypass, force) degraded payload completeness. The model needs domain scaffolding.

### Phase 6: Goldilocks Hybrid
- **Finding:** ALL-CAPS section headers (ROLE:, EXECUTION REQUIREMENT:, STRUCTURAL RULES:) act as implicit stop tokens for the 9B model — causing 67% passive runs.
- **Finding:** When the Goldilocks harness works, it works cleanly: correct block IDs, zero inspect spam, proper force=true usage.

### Phase 7: Seamless Harness (Terminal)
- **Finding:** A single flowing paragraph with echo bridged to action ("and then immediately execute") matched the old 11-rule harness ceiling (both blocks + connection, state_revision 4, valid) while having zero behavioral commands.
- **Finding:** The 9B model's 33% passivity rate is a stochastic attention-head limitation — no prompt variant eliminated it.
- **Decision:** Freeze Seamless prompt + sanitized tool schemas as the permanent architecture.

### Slop Eradication (4-Subagent Audit)
- **Finding:** 50+ instances of in-band behavioral commands across the backend — dispatcher (STOP/CONTINUE), runtime reminders (Call now/Retry/Do not), validation errors (Run search_blocks/Inspect), tool schemas (Use this when/Do NOT/Never), hint strings (you must/please specify/retry with).
- **Fix:** 188 lines of behavioral commands removed across 8 files. 18 tool descriptions sanitized to capability-only. Runtime reminders reduced to factual statements. ALL-CAPS directives eliminated everywhere.
- **Finding:** Dedup cache was not invalidated after successful mutations — model received stale topology data.
- **Fix:** Cache cleared on change_graph commit. `_last_failed_ops_hash` cleared on success. `parallel_tool_calls` enabled. GUI now respects TOML `max_tool_rounds`.
- **Finding:** Contradictory insert-on-wire instructions between system prompt and tool schema.
- **Fix:** Aligned both to single source of truth: "batch remove_connections + add_blocks + add_connections."

### Architectural Principle (Terminal)
The System Prompt is the only place authorized to dictate behavior. Tool schemas describe capability. Tool results return state. Error strings return facts. Nothing else.

---

## 2026-06-11 — Data Starvation & Authority Fixes

- **Finding:** Hardcoded `max_tool_result_chars=800` truncated `query_knowledge` results, deleting block IDs the 4B model needed.
- **Fix:** Exposed `max_tool_result_chars` to config (default 4000).
- **Finding:** Chat-tuned small models would stop mid-task to ask "What should I do?" after gathering evidence.
- **Fix:** Added explicit AUTHORITY preamble and tightened rules 7/10.
- **Fix:** Ported REPL printers to typed ChatMessage objects (zero-shim policy).
