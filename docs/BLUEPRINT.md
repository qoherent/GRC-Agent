# Project Blueprint

## Purpose

GRC Agent is a local-first assistant for GNU Radio Companion `.grc` flowgraphs.
It creates, inspects, explains, edits, validates, and saves graphs through a bounded
tool contract; the model never edits raw YAML directly. The agent decides what graph
to build. The tools decide whether the graph mutation is valid. `grcc` decides final
validity.

## Architecture

| Layer | Owner | Purpose |
|---|---|---|
| Raw `.grc` | YAML on disk | Persistence format |
| Session | `flowgraph_session.py`, `models.py` | Loaded graph, mutation, validation, save, active session snapshot |
| Catalog | `catalog/` | GNU block metadata, parameter defaults, port definitions, `describe_block(...)` |
| Retrieval | `retrieval/` | Catalog/session search |
| Validation | `validation/` | Pure staged preflight checks, parameter default filling |
| Transaction | `transaction/` | Atomic propose/apply on copied session |
| Runtime | `agent.py` | Tool registry, schemas, prompt rules, history, argument normalization |
| Adapter | `llama_server.py`, `llama_launcher.py` | Thin llama.cpp transport loop, startup, readiness; `--no-mmproj` enforced |
| CLI | `cli.py` | `doctor`, `health`, `fake`, `chat`, `tool` |

## Model-facing contract

Thirteen tools, in fixed order:

1. `new_grc(graph_id="new_flowgraph")`
2. `load_grc(file_path)`
3. `summarize_graph(max_blocks=None)`
4. `search_grc(query, scope="catalog|session", k=5)`
5. `get_grc_context(node_id, hops=1, max_nodes=20)`
6. `describe_block(block_id)`
7. `suggest_compatible_insertions(connection_id, k=5)`
8. `insert_block_on_connection(connection_id, block_type, instance_name, params=None)`
9. `auto_insert_block(goal, preferred_block_type=None, target_hint=None, max_candidates=10)`
10. `apply_edit(transaction)`
11. `propose_edit(transaction)`
12. `validate_graph()`
13. `save_graph(path=None)`

### Clarification Contract v1

`auto_insert_block` may return a data-driven MCQ when multiple validated candidates exist. Options A/B/C come from real executable candidates. D is always custom free text. No mutation occurs until a user selects an option.

Design rules:

- `new_grc` creates an empty session; all graph construction uses `apply_edit`
- `apply_edit` stays before `propose_edit`
- raw YAML is never model-editable
- `save_graph` is gated by successful validation of the current dirty state
- `add_block` supports arbitrary catalog blocks with auto-filled parameter defaults
- `suggest_compatible_insertions` is read-only catalog metadata helper for insertion tasks
- invalid candidates never commit; the live session is never corrupted
- the loop is dumb: no GNU Radio domain knowledge, no prompt regex, no transaction rewriting
- all recovery hints are fact-driven: graph dependencies, catalog metadata, preflight errors, `grcc` output
- **Live model capability limit**: the 2B gemma model does not autonomously discover `insert_block_on_connection`. It often cannot use it autonomously. The tool is reliable when exact args are provided. `auto_insert_block` handles natural-language insertion with clarification/rejection guards.
- **Verified workflow tools**: `insert_block_on_connection` is implemented as a discoverability wrapper around `apply_edit`. Additional workflow wrappers require separate design review.
- for insert/add requests, inspect the graph first, then call `suggest_compatible_insertions`, then use `insert_block_on_connection` when exact connection_id and block_type are known, or `apply_edit` for lower-level transactions

## GNU-facing boundary

Supported and verified today:

| Area | Supported contract |
|---|---|
| New graph creation | `new_grc` creates a minimal valid skeleton; `apply_edit` builds the graph |
| Parameter edits | `update_params` on loaded blocks, including symbolic GNU/Python expressions and optional `block_type` disambiguation |
| Block state toggles | `update_states` on loaded blocks, supporting `enabled`/`disabled` and optional `block_type` disambiguation |
| Removal | `remove_block` on detached blocks, with optional `block_type` disambiguation |
| Add block | arbitrary catalog blocks via `add_block` with catalog-default parameter filling |
| Compatible insertion | `suggest_compatible_insertions` returns catalog-backed candidates for a connection; model may use `insert_block_on_connection` when exact args are known, or `apply_edit` for lower-level transactions |
| Autonomous insertion | `auto_insert_block` performs bounded candidate search, commits one grcc-valid goal-matching candidate, or returns `clarification_required` / safe rejection |
| Rewire | ordered transactions may disconnect/reconnect within one staged edit |
| Validation authority | `grcc` is final truth |

Derived rules from real `grcc` probes:

- removing a shared variable requires patching dependent parameters first
- detached stream blocks are not generally valid
- second-trace time-sink rewires require coordinated `nconnections` updates
- `state: disabled` is accepted by `grcc`; duplicate-name shadowing is real GNU behavior
- invalid intermediate states are acceptable only when the final staged result validates
- tutorial-driven DSP relationships such as packet formatter compatibility or constellation/unpack lockstep are model recipes and `grcc` concerns, not Python preflight rules

## Runtime properties

- Session search returns canonical `block_id` for `describe_block(...)`
- Empty session search results include a catalog-retry hint
- Session retrieval indexes are reused until session revision changes
- Session history keeps the latest active-session snapshot explicit for the model
- History compaction trims older payloads (100k char default, configurable) without dropping the current session state
- Launcher supports cold start and warm reuse with concurrency-safe file locking
- Tool-call schema validation rejects unknown tools, wrong types, enum mismatches, and extra fields before execution
- `describe_block` enriches ports with canonical GUI colors (blue, orange, etc.)
- Touched-block preflight revalidates incident connections after staged parameter/state edits
- Structural compatibility includes `vlen` and metadata-backed block `asserts`
- Duplicate enabled parsed identifiers are rejected during staged validation
- `get_grc_context` accepts exact instance names and resolves unambiguous symbol-style ids
- malformed wrapped or list-encoded transactions are normalized before execution
- `add_block` auto-fills missing parameters from catalog defaults
- adapter has no GNU Radio domain knowledge: transaction detection and batch-stop policy are behind agent API
- eval harness records `INFRA_FAIL` separately from model failures, retries infra failures once; old six-phase suite archived to `scripts/eval/archive/llama_eval_legacy/`
- turn-completion guard lives behind `GrcAgent` boundary: `init_turn_requirements()`, `record_tool_completion()`, `check_turn_continuation()`
- guard uses negation detection (`_keyword_is_negated`) to prevent false positives ("do not save" must not require `save_graph`)
- `llama_server.py` delegates guard calls entirely to agent; no direct guard imports in the adapter layer

## Architecture status — local alpha (updated 2026-04-27)

Current status: **local alpha ready for daily manual use.**

- No unsafe mutations observed across all eval runs.
- Runtime architecture is frozen.
- Remaining failures are mostly model reasoning/selection limits, not tool safety failures.
- No new planner layer.
- No raw YAML edit path.
- No hidden repair logic.
- No fuzzy Python graph designer.
- No full-sweep optimization unless a real regression appears.
- Raw YAML direct-edit requests are blocked by a deterministic keyword guard in `GrcAgent.check_unsupported_request()`.
- `save_graph` on new graphs (no session path) returns `SAVE_PATH_REQUIRED` instead of a generic error.
- Message-port connections (string ports) are fully supported alongside stream (integer) ports.
- `apply_edit` validates internally before committing; failed edits are atomic rollbacks.
- `suggest_compatible_insertions` added as read-only insertion helper; read-only, deterministic, generic filtering.

### Known limitations

1. **Small 2B model may choose incompatible blocks for arbitrary insertion tasks.** The model often selects hardware-specific blocks (e.g., `uhd_rfnoc_siggen`) that fail software-only validation. `suggest_compatible_insertions` added to address this; model must choose to use it.
2. **Small model may ask for clarification instead of acting.** The 2B model is conservative when prompts don't specify exact block types.
3. **Duplicate instance-name disambiguation is safely rejected, not fully editable.** When two blocks share the same `instance_name`, tools refuse ambiguous operations. Disambiguation by `block_type` is not yet supported. Only 1 of 175 installed examples is affected.
4. **`SAVE_PATH_REQUIRED` after `new_grc` may require user follow-up.** The 2B model sees the error but produces final text instead of retrying with a path. A larger model would likely self-correct.
5. **Expert GNU/DSP knowledge depends on backend model quality.** Complex PMT dict construction, scrambler disambiguation, and symbolic-expression variance are model limitations.

### When to patch

Only patch when one of these occurs:

1. Unsafe mutation
2. Invalid graph committed to disk
3. Preview mutates live graph
4. Raw YAML edit bypasses guard
5. Wrong file overwritten
6. Valid installed GNU example fails to load
7. Same failure repeats across 3+ unrelated real-use graphs

Do not patch isolated 2B model weirdness.

### Implemented milestones

1. `suggest_compatible_insertions()` — **IMPLEMENTED v1** in `src/grc_agent/session/insertion_suggestions.py`
2. `insert_block_on_connection()` — **IMPLEMENTED v1** in `src/grc_agent/agent.py` as thin wrapper around `apply_edit`
3. `auto_insert_block()` — **IMPLEMENTED v1** in `src/grc_agent/session/auto_insert.py`; supports Clarification Contract v1; dtype inference from endpoint params; preferred_type recall with suggest_k=500

### Backlog (not implemented)

1. Stable `block_uid` for duplicate instance-name edits
2. Stronger backend/model comparison
3. Richer catalog descriptions
4. Optional `new_grc(path=...)` UX improvement

## Eval baseline (2025-04-25, cleanup pass)

586/594 model attempts = 98.7%. 0 infra failures.

8 remaining failures, all 2B model limitations (knowledge gaps, wording
inconsistency, compound-repair sequencing). No actionable prompt, tool schema,
or eval expectation fixes remain.

## External corpus evaluation (2025-04-25, v2)

Tool-chain tested on 10 installed GNU Radio example graphs. All 10 load, validate,
and support inspection/edit operations. Message-port graphs fully operational.

Results: 71 PASS, 2 SKIP (no safe scalar params), 0 FAIL across 73 tests.

## Human evaluation (2025-04-25, v1)

10 interactive tasks through real model loop. 57/60 (95.0%). 0 unsafe mutations.
8/10 tasks pass deterministically. 2 remaining: flaky model routing (1) and
model-size limitation on save-path retry (1).

## Extended real-world testing (2025-04-25, v1)

15 installed GNU Radio examples across 15 categories. 84 tasks in initial run;
add_block re-run covered all 15 graphs with corrected classifier.

Corrected results:

```text
0/15 successful apply_edit cases missed validation
8/15 attempted add-block edits failed safely (rejected by internal validation)
7/15 no edit attempted (model asked for clarification)
0 unsafe mutations
```

## Compatible Insertion Helper v1 (2025-04-26)

**Problem**: E_insertion was 0/4 PASS (2B) and 0/8 PASS (4B Q2). Model repeatedly
picks hardware-specific blocks without inspecting graph context.

**Solution**: Add read-only `suggest_compatible_insertions(connection_id, k=5)` tool.

- Catalog-metadata filtering (domain, dtype, vlen match)
- Generic exclusion of hardware/external blocks via category path / flags
- Deterministic ranking (params with defaults, 1-in/1-out preference, core category preference)
- Stream-domain insertion supported; message-domain rejected gracefully
- No graph mutation; `apply_edit` remains the only mutation path

### Live eval status

**Completed 2025-04-26.** See the Interactive Planning Evaluation v1 results below for the latest full picture.

43 realistic prompts across 8 real graphs. Results:

```
A_explain:   8/8 PASS (100%)
B_inspect:   5/8 PASS  (62.5%)
C_edit:      4/8 PASS  (50.0%)
D_insert:    0/8 PASS  (0.0%)
E_preview:   6/6 PASS (100%)
F_save:      3/3 PASS (100%)
Overall:    26/41 PASS (63.4%)
STOP_THE_LINE: 0
```

Insertion helper called zero times across all 8 insertion tasks. Model always jumps directly to `apply_edit`. This is a model-size routing limitation, not a tool gap.

## Refactor Audit v1 (2025-04-26)

Maintainability review of the entire codebase. Primary finding: `agent.py` at 2,025 lines is large due to mixed responsibilities (prompt + schemas + execution + normalization). Recommended extraction of tool schemas, prompt builder, and transaction normalization into separate modules — behavior-preserving only.

No refactors applied yet.

## Vision / mmproj policy

GRC Agent is text-only. Do not load multimodal projector files (`mmproj-BF16.gguf`).
- No image input is used
- `mmproj` consumes VRAM / RAM unnecessarily
- Does not improve `.grc` editing or tool calling

Launcher detects `--no-mmproj` support at runtime by checking `llama-server -h` output.
If supported, the flag is appended automatically. If absent, server starts without it
and a warning is printed.

## Standard gates

- `uv run ruff check`
- `uv run python -m unittest`
- `uv run grc-agent fake tests/data/random_bit_generator.grc`

## Verification commands

```bash
uv run ruff check
uv run python -m unittest
uv run grc-agent doctor
uv run grc-agent chat tests/data/random_bit_generator.grc --message "Summarize the graph."
uv run grc-agent chat --message "Create a new flowgraph called test_graph and summarize it."
```

## Harness v2 (2025-04-25)

Eval infrastructure with invariant auditing and state-aware classification.

- 11 automatic invariant checks (safety, edit, save, refusal, domain)
- 16 failure categories including STOP_THE_LINE
- 7 scenario families, 34 cases
- 22/34 PASS, 0 STOP_THE_LINE, 0 invariant violations
- Confirmed: no unsafe mutations, no graph corruption, no wrong-file overwrites
- Repeated patterns: save path omission (5), insertion knowledge gap (4)

## Targeted Fix Pass 1 (2025-04-25)

Evidence-backed fixes only — no broad changes, no overfitting.

- Fixed scenario runner tool-extraction bug (E-family misclassified as all MODEL_ROUTING)
- Strengthened `save_graph` tool description to require explicit `path` for copy/new-graph saves
- Save-family results improved: 1/4 → 3/4 PASS; D-message save cases: 0/3 → 3/3 PASS
- Zero STOP_THE_LINE before and after fixes
- Zero production runtime regressions

## Targeted Improvement Pass 2 (2025-04-25)

Investigated compatible insertion failures without adding runtime tools.

- Expanded E_insertion from 4 to 8 case definitions across graph types
- Built test-only oracle analyzer (`tests/harness/oracle.py`) using catalog metadata
- Oracle result: 285 stream middle-block candidates, 202 with full defaults
- Model behavior: model picks hardware blocks (`uhd_rfnoc_*`) and ignores context tools
- Root cause: model-size limitation (2B cannot use context effectively), not missing tool
- Decision: `suggest_compatible_insertions()` — **IMPLEMENTED** in pass-2 investigation
- No production runtime changes at the time (helper was added later)
- Invariant violations: 0

## Backend Comparison v1 (2025-04-25)

- **Qwen 3.5 4B**: tested, rejected — tool-format instability (`<tool>:` output instead of JSON), 48% INFRA
- **Gemma 4 E4B Q2**: tested on 32 cases, 22/32 PASS (69%), 0 STOP_THE_LINE — no meaningful improvement in insertion
- **Current default**: `unsloth/gemma-4-E2B-it-GGUF` (2B Q4) retained as smallest, fastest, most stable
- mmproj disabled when supported via runtime `--no-mmproj` detection

## External Corpus v3 (2025-04-26)

Broader graph coverage tool-chain check — 20 additional GNU Radio example graphs, no live model.

- Categories: audio, analog, blocks, channels, digital, DTV, FEC, filter, metadata, network, PDU, QT GUI, ZeroMQ, Python snippets
- Graph sizes: 94–1294 lines, 5–47 blocks
- grcc/load/validate: 20/20 PASS
- summarize/context/save/roundtrip: 20/20 PASS
- Compatible insertion helper: 17/17 PASS (3 message-only SKIP)
- Message port detection: 7/7 graphs correctly detected
- No new tool gaps, no STOP_THE_LINE, no data loss

## Cheating removal record

The following were intentionally removed as eval-overfitted compensations:

- hardcoded `samp_rate` canonical repair transaction substitution
- fixture-specific block names (`blocks_throttle2_0`, `qtgui_time_sink_x_0`) in repair logic
- prompt-text pattern matching that rewrote transactions based on eval phrases
- hidden same-turn transaction recovery via `_normalize_same_turn_samp_rate_recovery_transaction`
- auto-expansion of `remove_block(samp_rate)` into multi-step repair via `_expand_samp_rate_remove_keep_working_operation`
- prompt-regex follow-up routing via `_text_requests_*` and `required_next_tools`
- validation status rewriting in history renderer (`"valid"` -> `"internal_compile_check_passed"`)
- prompt-based variable inference from `_parse_variable_add_request_from_prompt`
- prompt-based connection repair in `_repair_partial_remove_connection_operation`
- `samp_rate` preference in `_default_update_params_instance_name`

## Default 2B backend capability profile

The current default backend is `unsloth/gemma-4-E2B-it-GGUF` via llama.cpp.

Reliable:
- Summarize, inspect, search, describe, validate, save, preview, raw-YAML refusal
- Simple single-parameter edits

Partial:
- Natural-language insertion through `auto_insert_block` — works for tested throttle/head cases; may return `clarification_required` (MCQ) or safe rejection
- Insert with exact args (verified tool works; model cannot synthesize args autonomously)

Unreliable:
- Multi-step graph creation
- Copying structured tool output fields into another call

Tool-only (reliable, exact-args required):
- `insert_block_on_connection` (needs exact connection_id, block_type, params)
- `suggest_compatible_insertions` (needs exact connection_id; returns copyable `insert_tool_args`)
