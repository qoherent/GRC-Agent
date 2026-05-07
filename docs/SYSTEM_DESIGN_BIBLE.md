# System Design Bible

Updated: 2026-05-05

This document is the compact system-design guide for GRC Agent. `docs/BLUEPRINT.md` remains the detailed engineering contract; this file explains the package shape, runtime flow, safety boundaries, and decisions that should not be rediscovered.

## Product Scope

GRC Agent is production-candidate under frozen local scope for bounded GNU Radio Companion `.grc` workflows:

- inspect, summarize, search, and explain loaded graphs
- preview supported edits without mutation
- apply verified parameter/state edits
- remove exact connections and perform exact or clarification-backed rewires
- validate with `grcc`
- save only when explicitly requested
- roll back failed edits atomically

Non-goals:

- unsupervised production autonomy
- broad "fix this topology" planning
- raw `.grc` YAML editing
- tutorial-derived mutation recipes
- vector-driven mutation
- free-form `block_uid` mutation handles
- same-name same-type duplicate mutation without a verified `target_ref`

## Core Invariants

- One `GrcAgent` owns runtime state and graph mutation.
- The model proposes tool calls; the runtime validates whether they are allowed.
- All mutations go through verified tools.
- `grcc` remains the final graph-validity authority.
- Preview never mutates.
- Save requires explicit user intent and a validated latest dirty state.
- Failed edits roll back before live commit.
- Checkpoints are local infrastructure only; CLI restore writes to an explicit
  new copy path and is not exposed as model-facing undo.
- Route mismatches fail closed; they are not silently remapped.
- Clarification options must come from real executable graph/tool candidates.
- `llama_server.py` stays transport and bounded-loop oriented. GNU Radio policy belongs behind `GrcAgent`.

## Runtime Flow

```text
User prompt
  -> TurnPlan classification
  -> tool-schema narrowing
  -> llama.cpp bounded tool loop
  -> schema validation
  -> route validation
  -> verified tool execution
  -> preflight on cloned candidate graph
  -> grcc validation when needed
  -> atomic commit or rollback
  -> explicit save only if requested
```

`TurnPlan` is deterministic runtime policy. It narrows model-visible tools and
blocks unsafe uncertainty before execution.

In MVP default chat, TurnPlan narrows to wrapper tools (`inspect_graph`,
`search_blocks`, `ask_grc_docs`, `change_graph`). Wrapper dispatch handles
verified low-level internals.

Advisor-first direction: the local Advisor owns semantic intent classification.
It uses the same llama.cpp server and returns exactly one structured mode:
`{"mode":"inspect|preview|change|clarify|unsupported"}`.
The runtime may validate the mode enum and map it to a tool class, but it must
not duplicate user-language understanding with regexes, phrase dictionaries,
or hardcoded natural-language branches. Runtime safety remains structural:
schema validation, route gates, preflight, `grcc`, rollback, explicit save
state, and UID target-ref validation.

Advisor is shadow-only and runtime routing must not depend on it yet.

## Context And Output Budgeting

- Desired llama context target is `120000` tokens when backend/model support it.
- Actual context is verified via `grc-agent doctor --start-llama --json` and `grc-agent health`.
- Runtime budgets are centralized in config (`[llama]`, `[agent.docs_answer]`,
  `[agent.retrieval]`, `[agent.history]`, `[agent.guardrails]`).
- `max_tokens` is treated as generation ceiling only; it is not used as the
  primary compression lever.
- Primary compactness controls are:
  - bounded wrapper payloads
  - retrieval-stage snippet/source limits
  - concise answer schemas
  - explicit truncation telemetry in eval/debug paths

## Package Layers

| Layer | Main files | Responsibility |
|---|---|---|
| CLI | `src/grc_agent/cli.py` | `doctor`, `health`, `fake`, `chat`, direct tools, manual/vector/dogfood/history commands |
| Runtime | `src/grc_agent/agent.py`, `src/grc_agent/runtime/` | tool registry, prompt state, TurnPlan, schema/route validation, clarification, checkpoint journaling |
| Adapter | `src/grc_agent/llama_server.py`, `src/grc_agent/llama_launcher.py` | llama.cpp HTTP client, local server reuse/startup, bounded loop |
| Session | `src/grc_agent/flowgraph_session.py`, `src/grc_agent/session/` | loaded graph state, parsing, snapshots, atomic save |
| Transactions | `src/grc_agent/transaction/` | cloned candidate mutation, preflight, commit/rollback |
| Validation | `src/grc_agent/validation/` | pure preflight rules and GNU consistency checks |
| Catalog | `src/grc_agent/catalog/` | installed GNU Radio block metadata, ports, params, defaults |
| Retrieval | `src/grc_agent/retrieval/` | lexical graph/catalog search and read-only vector retrieval |
| Manual | `src/grc_agent/manual/` | cleaned GNU Radio docs search with citations |
| History | `src/grc_agent/history.py` | local checkpoint JSONL, exact graph deltas, retention, CLI-only restore to a copy path |
| Advisor | `src/grc_agent/runtime/turnplan_advisor.py` | shadow-only advisor-first mode classification and structural mode-to-tool mapping |

## Public Tool Surface

Default model-facing chat surface is the MVP wrapper profile:

1. `inspect_graph`
2. `search_blocks`
3. `ask_grc_docs`
4. `change_graph`

`ask_grc_docs` retrieves local docs and returns a grounded answer with sources. 
Docs answers are explanation-only and not mutation authority. 
Beta default is deterministic grounded extraction with source evidence and
honest `insufficient_evidence` on weak local support. DocsAnswerAdvisor helper
synthesis is optional research-only best effort; safe fallback remains
first-class.

Low-level handlers remain internal/compatibility-only and are not deleted.
They are still safety-tested and are called through wrapper dispatch.

Do not expand the default model-facing tool surface without repeated generic
evidence and full live-gate validation.

## Mutation Boundaries

Model-facing mutation entrypoint in MVP default chat:

- `change_graph`

Verified internal handlers behind `change_graph`:

- `apply_edit(transaction)` for validated graph transactions
- `propose_edit(transaction)` for preview only
- `remove_connection(...)` for exact or uniquely resolved connection removal
- `rewire_connection(...)` for atomic old-edge removal plus new-edge addition
- `insert_block_on_connection(...)` for exact compatible insertion
- `auto_insert_block(...)` for bounded insertion when enough placement context exists
- `save_graph(path)` is compatibility/direct-tool only in MVP default chat

These internal handlers are not directly selected by the model in MVP default
chat.

Mutation rules:

- Endpoint hints must resolve to exactly one executable candidate before mutation.
- Ambiguous endpoint or duplicate-block candidates clarify; no first-candidate auto-pick.
- Stale clarification replies are rejected using `state_revision`.
- `remove_block` requires a detached unique target.
- Same-name same-type duplicate param/state mutation requires a verified
  `target_ref` generated from current graph identity evidence and current
  `state_revision`; it is not accepted from free-form text.
- Same-name different-type duplicates may execute only when `instance_name + block_type` identifies exactly one target.
- `block_uid` is not a public mutation argument. It is accepted only inside a
  checked `target_ref` for `update_params` / `update_states`; connection,
  rewire, add-block, and broad topology operations cannot use UID targeting.

## Retrieval

Model-facing retrieval wrappers:

- `search_blocks` for block discovery (compact results: `block_id`, `name`,
  `summary`)
- `ask_grc_docs` for concise grounded docs answers with sources

Internal retrieval handlers:

- `search_grc` (lexical graph/catalog)
- `semantic_search_grc` (vector candidate discovery)
- `search_manual` (manual/tutorial excerpts with citations)

### Lexical Graph/Catalog Search

`search_grc` is deterministic lexical retrieval over the active graph/session and installed GNU Radio catalog. It uses the `graphifyy` package and a finite alias layer for known terminology misses such as:

- audio smoother -> low-pass filter
- automatic gain control -> AGC blocks
- spectrum -> frequency/waterfall sinks
- rate limiter -> throttle
- scope/trace -> time sink

Aliases must describe stable block capability, not one-off query patches.

### Manual Search

`search_manual` searches cleaned local GNU Radio docs under `docs/wiki_gnuradio_org/` and returns cited excerpts. It is explanation-only. Tutorials and manual chunks are not mutation recipes.

### Vector Search

`semantic_search_grc` is read-only candidate discovery:

- local Qdrant persistent mode
- FastEmbed through `qdrant-client[fastembed]`
- model: `BAAI/bge-small-en-v1.5`
- vector-only v1, no hybrid, no reranker, no runtime multi-model
- index path: `.grc_agent/vector_index/qdrant`
- explicit build: `uv run grc-agent vector build`

Vector results may contain candidate blocks/docs/chunks with provenance and scores. They must never return transactions, params payloads, insert args, save instructions, hidden recipes, repair plans, or mutation authorization.

Current frozen baseline:

- vector: 276/290 top-k hits
- lexical: 168/290 top-k hits
- exact-ID misses: 0
- false-positive failures: 0
- source-type misses: 0

See `docs/BLUEPRINT.md` and `tests/data/retrieval/vector_eval_governed_metadata.json`.

## Setup Automation Reality

What is automatic through `uv sync --locked`:

- Python package dependencies
- `graphifyy`
- `qdrant-client[fastembed]`
- tests/dev dependencies when using the dev environment

What is checked but not installed by the package:

- GNU Radio and `grcc`
- the `llama-server` binary from llama.cpp

What the CLI can start or download:

- `grc-agent chat` can start/reuse a local `llama-server` if the binary is on `PATH`.
- `grc-agent doctor --start-llama` can start/reuse llama.cpp during checks.
- The default llama.cpp command uses `-hf unsloth/gemma-4-E2B-it-GGUF:UD-Q4_K_XL`; if the installed llama.cpp supports Hugging Face model download, first startup may download the model.
- `grc-agent vector build` downloads the FastEmbed model on first build if it is not cached.

What is intentionally not automatic:

- Vector index build during chat. Missing index returns a structured `missing_index` result.
- Runtime metadata promotion from vector misses.
- `llama.cpp` installation. This should be a future `grc-agent setup` or documented platform script, not hidden runtime behavior.

## llama.cpp Backend

Default config in `grc_agent.toml`:

```toml
[llama]
server_url = "http://127.0.0.1:8080"
model = "unsloth/gemma-4-E2B-it-GGUF"
hf_model = "unsloth/gemma-4-E2B-it-GGUF:UD-Q4_K_XL"
temperature = 0.0
enable_thinking = false
```

The launcher verifies local host URLs, reuses healthy servers, checks model alias, uses file locking, and passes `--jinja`. It adds `--no-mmproj` when supported because graph editing is text-only.

## Evidence And Gates

Retrieval/vector eval gates are currently a sequential contract while the local
index path is shared. Do not run retrieval eval gates in parallel unless index
isolation is explicitly enabled.

Current deterministic evidence in this checkout:

- `tests/data/retrieval/vector_eval_governed_metadata.json`

Operational eval and dogfood reports are generated locally and may be untracked.

The evidence supports production-candidate confidence for bounded inspect/search/help/preview/change
workflows on copied `.grc` graphs. It does not prove arbitrary GNU Radio graph
autonomy.

Fast default gate:

```bash
uv run ruff check src/ tests/
uv run ruff check
uv run python -m unittest
uv run python -m tests.retrieval_eval.vector_regression
```

Live quick gate (runtime/model-facing changes only):

```bash
uv run python -m tests.llama_eval.tier1_live --quick
uv run python -m tests.llama_eval.tier2_release
uv run python -m tests.llama_eval.tier3_multiturn --quick
uv run python -m tests.llama_eval.tier4_external_examples --quick
uv run python -m tests.llama_eval.tier5_adversarial --quick
```

Release gate (release claims/default-routing changes):

- Run Tier 2/3/4/5 with `--n-runs 3`
- Run `tests.llama_eval.release_dashboard` on persisted results

Advisor/model bakeoff scripts are research-only and must be run explicitly.

## Experiments And Decisions To Preserve

- Multi-agent routing was rejected. For a 2B local model it adds latency and failure modes without improving mutation safety.
- RAG/manual-derived mutation recipes were rejected. Tutorials remain explanation material only.
- Broad planner behavior was rejected. Use typed TurnPlan plus verified tools instead.
- Hidden repair/remapping was rejected. Route mismatch fails closed.
- Vector hybrid/reranking was deferred. Current vector-only Qdrant/FastEmbed stack improved paraphrase recall while keeping protected metrics clean.
- FastEmbed bakeoff kept `BAAI/bge-small-en-v1.5`; competing models did not beat protected metrics.
- Free-form `block_uid` mutation was rejected. A later contract added guarded
  `target_ref` support for block-local param/state edits only, with expected
  name/type and stale-state checks.
- A self-dogfood run found repeated `block_uid` mutation wording reaching `apply_edit`; the accepted fix guarded UID mutation behind verified `target_ref`. Future advisor work should classify free-form UID language as `clarify`, not add runtime phrase dictionaries.
- Assistant-text fallback parsing is frozen legacy compatibility. Do not expand it into routing or repair.
- Superseded advisor prompt-version experiments are archived as report evidence.
  Active advisor research remains explicit/offline only and is not part of the
  default deterministic gate.

## Patch Policy

Patch immediately only for STOP_THE_LINE issues:

- unsafe mutation
- preview mutation
- apply during preview-only prompt
- invalid graph committed or saved
- raw YAML bypass
- wrong file overwritten
- save without explicit request
- save/reload mismatch
- hidden repair/remapping

Patch normal failures only when the same generic issue repeats across at least three unrelated graphs or across distinct evidence sources. Do not patch one-off model weirdness, safe clarification, safe preflight rejection, or a single `grcc` failure before commit.

## Known Limits

- Production-candidate for the frozen local scope only.
- Tier 4 is installed-example evidence, not arbitrary-graph proof.
- Vague topology repair clarifies only.
- Complex graph creation is still model-limited.
- Same-name same-type duplicate parameter/state edits require a guarded
  `target_ref`; same-name duplicate remove remains conservative.
- Free-form `block_uid` mutation is blocked.
- Vector retrieval is frozen/read-only.
- `numpy<2` is intentional GNU Radio 3.10.x ABI compatibility debt.
