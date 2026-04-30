# GRC Agent System Design Bible

This document is the concise system-design reference for GRC Agent. It is meant
to be reviewed by humans or external agents without requiring direct codebase
access.

## Product Goal

GRC Agent is a local autonomous assistant for GNU Radio Companion `.grc`
flowgraphs. It should understand natural language, inspect the active graph,
choose verified tools, perform safe mutations, validate with `grcc`, save only
when explicitly requested, and ask for clarification when required information is
missing or contradictory.

Autonomy comes from typed runtime state, explicit tools, deterministic
validation, and measured evals. It must not come from raw YAML edits, hidden
repairs, prompt-regex transaction rewriting, tutorial-derived mutation recipes,
block allowlists/blacklists, fixture-specific logic, broad planners, or
unbounded retry loops.

## Current Architecture

```text
User request
  -> CLI / llama.cpp chat adapter
  -> GrcAgent active session
  -> pre-model hard guards
  -> typed TurnPlan
  -> tool-schema narrowing
  -> local llama.cpp model sees only allowed schemas
  -> runtime schema validation
  -> route-mismatch gate
  -> verified tool execution
  -> transaction preflight
  -> candidate session mutation
  -> grcc validation
  -> atomic commit or rollback
  -> optional explicit save
  -> live-eval graph snapshot / result reporting
```

There is one runtime agent: `GrcAgent`. The local LLM is not trusted as an
authority. It proposes tool calls. `GrcAgent` owns session state, tool execution,
route validation, transaction normalization, mutation, rollback, validation, and
clarification storage.

There is no multi-agent router, no broad autonomous planner, and no tutorial/RAG
mutation executor. `auto_insert_block` is a deterministic bounded workflow tool,
not a second LLM agent.

## Safety Contract

- The model must never edit raw `.grc` YAML directly.
- All mutations must go through verified tools.
- `apply_edit` and exact wrapper tools are the mutation boundary.
- `grcc` remains the final validity authority.
- Failed edits roll back atomically.
- `propose_edit` previews never mutate.
- `save_graph` runs only when explicitly requested.
- Route mismatches fail closed; the runtime does not silently repair them.
- One typed recovery retry is allowed only for bounded schema-level recovery
  classes; invalid GNU end states and unsupported requests remain nonrecoverable.
- Tutorials/manual pages are explanation-only and provenance-first.

## Runtime Agents And Tool Ownership

### Main Runtime Agent: `GrcAgent`

Owns:

- Active `FlowgraphSession`.
- Tool registry and execution.
- Typed `TurnPlan` classification.
- Dynamic tool-schema narrowing.
- Route-mismatch blocking.
- Transaction normalization and validation boundary.
- Clarification persistence and resolution.
- Active-session summaries for model-visible state.

Uses all 17 public tools internally through `execute_tool`, but the model may see
only a narrowed subset for a given turn.

### Local Model Through llama.cpp

Role:

- Select allowed tools.
- Fill allowed tool arguments.
- Chain explicitly requested actions in one turn.
- Write concise final user-facing text after tools complete.

The model must not:

- Generate raw YAML patches.
- Invent hidden graph repairs.
- Bypass tools.
- Treat tutorial/manual content as mutation authority.
- Continue trying arbitrary tools until one succeeds.

### Deterministic Workflow Tool: `auto_insert_block`

Role:

- Search, score, and try bounded candidate insertions.
- Commit one `grcc`-valid candidate.
- Return real clarification options if multiple safe candidates exist.
- Reject safely when no candidate validates.

It is not an LLM helper agent. It uses catalog/session data, deterministic
candidate scoring, verified transaction application, and rollback.

## Public Tool Surface

The fixed public registry has 17 tools, in order:

1. `new_grc`: create a minimal empty flowgraph session.
2. `load_grc`: load a `.grc` file into the active session.
3. `summarize_graph`: bounded whole-graph summary.
4. `search_grc`: graph-backed lexical search over catalog or active session.
5. `get_grc_context`: local wiring/context around a session node.
6. `describe_block`: normalized catalog description for one block id.
7. `search_manual`: explanation-only search over bundled GNU Radio docs.
8. `semantic_search_grc`: read-only vector search over catalog/manual/tutorial
   candidates.
9. `suggest_compatible_insertions`: read-only insertion candidates for a known
   connection.
10. `insert_block_on_connection`: exact-argument wrapper around `apply_edit` for
   one insertion.
11. `auto_insert_block`: bounded autonomous insertion workflow.
12. `remove_connection`: exact connection-removal wrapper.
13. `rewire_connection`: exact/resolved old-edge to exact/resolved new-edge atomic wrapper.
14. `apply_edit`: verified live mutation.
15. `propose_edit`: verified preview/dry-run, no mutation.
16. `validate_graph`: run `grcc` validation.
17. `save_graph`: explicitly save current graph.

Tool order matters because small local models prefer earlier tools. The order is
part of the model-facing contract and should not change without eval evidence.

## TurnPlan Policy

Every normal model turn starts with a finite typed `TurnPlan`.

Tracked fields:

- `intent`: finite label such as `param_edit`, `state_edit`, `add_variable`,
  `remove_block`, `disconnect`, `insertion`, `preview`, `ambiguous`,
  `uncertain_mutation`, or `unknown`.
- `allowed_tools`: tool names the model may call this turn.
- `expected_op_types`: allowed transaction op types for mutation tools.
- `required_actions`: explicit user-requested actions still needing tools.
- `requires_clarification`: whether the runtime should ask before model routing.
- `evidence_span`: phrase that triggered the plan.
- `target_ref` and `parameter_name`: exact graph evidence for schema narrowing.

Examples:

- “Disable `blocks_message_debug_0`, then validate”:
  `state_edit`, allowed tools `apply_edit`, `validate_graph`, expected op
  `update_states`.
- “Set `analog_sig_source_x_0` `amp` to `0.5`”:
  `param_edit`, full tool surface retained, but `apply_edit`/`propose_edit`
  transaction schema is narrowed to exact target `analog_sig_source_x_0` and
  exact param key `amp`.
- “Add a variable called `noise_level` set to `0.1`”:
  `add_variable`, allowed tool `apply_edit`, expected op `add_block`.
- “Use `auto_insert_block` to insert a compatible block; if multiple safe
  choices validate, ask me to choose”:
  `insertion`, allowed tool `auto_insert_block`.
- “Insert a compatible block into the main signal path”:
  `insertion`, allowed tool `auto_insert_block`.
- “Add a compatible filter”:
  `uncertain_mutation` plus direct clarification because placement context is
  missing.
- “Swap the signal chain around and save it”:
  `uncertain_mutation`, no model call, direct clarification for exact action,
  target, and placement details.
- “Disconnect source output 0 from throttle input 0”:
  `disconnect`, exposes `remove_connection` without an `apply_edit` fallback,
  and expects exact connection arguments.
- “Rewire connection_id old->edge to new->edge”:
  `rewire`, exposes `rewire_connection`, resolves the old edge to one
  connection ID, resolves exact or bounded new-endpoint hints, clarifies on
  ambiguity with executable options and `state_revision`, and commits only
  through the verified remove-plus-add transaction.
- “Disable block X and remove it”:
  `ambiguous`, no model call; runtime asks for clarification.

Policy boundaries:

- Clear state edits expose only the relevant mutation path and follow-up tools.
- Unclassified mutation requests never expose model tools. They clarify
  immediately unless the runtime can classify a finite safe intent.
- Natural insertion routes to `auto_insert_block` only when the wording includes
  placement context such as a connection, source/destination, or signal path.
- Route validation checks both selected tool and transaction op type.
- Mismatched routes return `route_mismatch` with no graph mutation.

## Model Execution Flow

1. CLI loads or creates a `FlowgraphSession`.
2. Runtime rejects unsupported classes early, including raw YAML edits, Python
   export/code-generation, undo/redo.
3. Runtime resolves pending clarification replies before normal model routing.
4. Runtime builds `TurnPlan`.
5. If the plan requires clarification, runtime returns a clarification directly.
6. Runtime sends compact history, active-session context, and narrowed tool
   schemas to llama.cpp.
7. llama.cpp responds with zero or more tool calls.
8. Runtime validates tool names and JSON arguments against schemas.
9. Runtime validates route against `TurnPlan`.
10. Runtime executes tools sequentially; mutation tools are not parallelized.
11. Runtime records tool completions and may send one continuation nudge when
    explicit requested actions remain.
12. Runtime returns final assistant text only after required tool actions finish
    or a safe failure occurs.

The llama.cpp adapter supports future `tool_choice` and `response_format`
arguments, but current production routing uses deterministic `TurnPlan` plus
schema narrowing, not a separate constrained mini-router.

## Mutation Flow

```text
tool call
  -> schema validation
  -> TurnPlan route gate
  -> transaction normalization
  -> preflight validation
  -> clone active session
  -> apply operations to clone
  -> grcc validate clone
  -> commit clone to active session if valid
  -> return structured result
```

`apply_edit` validates internally before commit. A successful apply marks the
graph dirty and commit-eligible. If the user explicitly asked for validation,
`validate_graph` must still be called as a separate tool. If the user asked to
save, `save_graph` runs only after the latest dirty state is valid.

`propose_edit` runs normalization and preflight but never mutates the active
session. Live evals verify preview graph snapshots remain unchanged.

## Search And Retrieval Design

### `search_grc`: Catalog/Session Graph Search

`search_grc` is the main GNU Radio block/session search tool. It is graph-backed
structured lexical retrieval, not vector embedding RAG.

Implementation properties:

- Uses `graphify` through the `graphifyy` package to build directed retrieval
  graphs from JSON-like node/edge extractions.
- Builds catalog indexes from installed GNU Radio block YAML, tree metadata, and
  domain metadata.
- Builds session indexes from loaded flowgraph blocks and connections.
- Session indexes are cached by graph id, state revision, path, and catalog root.
- Nodes include provenance, labels, identifiers, summaries, related labels,
  block ids, parameter names, port signatures, categories, and adjacency text.
- Edges represent category containment, domain usage, and session connections.
- Search is deterministic lexical scoring over prepared node fields:
  label, identifier, summary, and related adjacency/context.
- A small deterministic alias layer covers known lexical misses, including
  `audio smoother -> low pass filter`, `automatic gain control -> AGC`,
  `spectrum -> frequency/waterfall sink`, `rate limiter -> throttle`, and
  `scope/trace -> time sink`.
- Results are bounded and structured; they provide `block_id`, summaries,
  provenance, scores, and match reasons.

Use:

- `scope="catalog"` for block discovery.
- `scope="session"` for finding loaded blocks/classes in the active graph.
- Use before `describe_block` when the user says find/search/look up.

Limits:

- It is not semantic vector search.
- Alias expansion is intentionally finite and eval-driven; false positives must
  be tracked alongside successful matches.
- It does not infer graph designs.
- It is routing/discovery context, not mutation authority.

### `semantic_search_grc`: Read-Only Vector Retrieval

`semantic_search_grc` uses local Qdrant persistent mode plus FastEmbed
`BAAI/bge-small-en-v1.5` to search catalog blocks and cleaned manual/tutorial
chunks. It is additive to lexical retrieval and must not add mutation
authority.

Implementation properties:

- Persistent local Qdrant path: `.grc_agent/vector_index/qdrant`.
- Public alias: `grc_agent_retrieval_v1`; builds write a staging collection
  and atomically swap the alias after validation.
- Garbage collection is explicit: `uv run grc-agent vector gc` is a dry run,
  and `uv run grc-agent vector gc --apply` preserves the active alias target
  plus one previous retired collection, then deletes older staging/retired
  collections from this index family.
- First build may download the FastEmbed model; later search is local.
- Point IDs are UUID5 values derived from stable record IDs.
- Catalog records come from installed GNU Radio block YAML via existing catalog
  loaders.
- Manual/tutorial records come from cleaned `docs/wiki_gnuradio_org` chunks;
  tutorial classification uses an explicit checked-in manifest, not filename
  heuristics.
- Returned scores are `vector_score_raw`; they are comparable only within the
  same query, model, and index version.
- `match_reason` is deterministic metadata, not LLM-generated prose.

Allowed outputs:

- Candidate catalog blocks, manual chunks, tutorial chunks, and later installed
  example summaries.
- Stable IDs, titles, normalized text snippets, provenance, scores, and match
  reasons.
- Candidate block IDs that the existing verified tools may inspect further.

Forbidden outputs:

- Transactions, `params` payloads, `insert_tool_args`, `apply_edit` payloads,
  `save_graph` instructions, hidden recipes, tutorial-derived defaults,
  block allowlists, block blacklists, or repair plans.
- Any signal that authorizes mutation.

Mutation remains gated by exact `TurnPlan` intent, exact target or connection
evidence, verified tool schemas, route validation, `grcc`, rollback, and exact
graph-delta proof. A vector hit can suggest what to inspect; it cannot decide
what to mutate.

Use:

- Build: `uv run grc-agent vector build`.
- Inspect: `uv run grc-agent vector stats --json`.
- Query: `uv run grc-agent vector search "audio smoother" --scope catalog`.
- Record real-user miss evidence:
  `uv run grc-agent vector miss "leveler block" --expected-block analog_agc_xx --actual-top-id blocks_xor_xx`.
- Review deduplicated miss clusters: `uv run grc-agent vector misses --json`.
- Generate human-review metadata candidates:
  `uv run grc-agent vector proposals --json`.
- Cleanup: `uv run grc-agent vector gc --json`; use `--apply` only after
  persisted eval/dashboard evidence.
- Runtime tool: read-only discovery/explanation only; never exposed for
  `uncertain_mutation`.

Miss intake is evidence only. It appends sanitized JSONL under
`reports/retrieval/real_user_misses.jsonl` by default, strips forbidden
mutation-shaped keys, and does not update metadata, rebuild indexes, change
rankings, call model tools, or affect graph state. Intake is structured:
timestamp, sanitized query, normalized query key, expected block IDs if known,
actual top IDs, scope, category, source, and bounded notes. It does not accept
arbitrary free-form blobs. It redacts filesystem paths and `.grc` filenames
from stored query, notes, expected IDs, and actual top IDs while preserving
normal catalog IDs. Clustering is intentionally conservative: shared expected
IDs alone do not merge unrelated wording, so cases like “signal level” and
“gain control” remain separate unless repeated evidence justifies review. New
semantic metadata still requires repeated clustered miss evidence, a stable
block-capability reason, at least one mutation-shaped negative trap, and a
retrieval eval rerun with protected metrics clean.

Metadata proposal reports are non-authoritative. A cluster can be proposed only
when it has at least 3 misses or support from 2 distinct sources. One-off,
ambiguous, eval-issue, and protected-metric-regression clusters are blocked.
The report includes proposed block, candidate capability phrase, supporting
clusters, required negative trap, expected eval cases, and false-positive risk,
but it never edits `CATALOG_SEMANTIC_METADATA`.

### Vector Retrieval Eval Spec

Retrieval evals compare current lexical search and vector-only search. Hybrid
search and reranking remain future work until misses prove the need.

Required eval classes:

- Paraphrase query to expected block candidates, e.g. “audio smoother” to low
  pass filter candidates.
- False-positive traps where plausible terms must not outrank exact catalog
  matches.
- Manual/tutorial citation accuracy with source path, URL, and line/chunk
  provenance.
- Latency budget under local CLI constraints.
- Deterministic index rebuild checks using stable corpus IDs and source hashes.
- Safety tests proving retrieval results cannot expose or authorize
  `apply_edit`, `save_graph`, destructive wrappers, hidden repairs, or raw YAML.

Latest local eval: 290 deterministic cases, vector top-k hits 276, lexical top-k
hits 168, safety 290/290, provenance 290/290, 0 exact-ID misses, 0
false-positive failures, 0 source-type misses, deterministic rebuild pass true.
The report includes miss analysis for vector misses, lexical wins over vector,
exact-ID misses, false-positive failures, and source-type misses. Inspect that
before adding hybrid retrieval. Current triage is recorded in
`reports/retrieval/vector_miss_triage.md`.

Vector v1 is frozen in `reports/retrieval/VECTOR_BASELINE_V1.md`. The no-LLM
regression command is:

```bash
uv run python -m tests.retrieval_eval.vector_regression
```

It requires vector hits >=276, exact-ID misses 0, false-positive failures 0,
source-type misses 0, safety 290/290, provenance 290/290, and deterministic
rebuild pass true. Lexical hits are reported but are not a hard threshold.
Operational details are in `docs/VECTOR_RETRIEVAL.md`; metadata changes require
`docs/VECTOR_METADATA_CHANGE_CHECKLIST.md`.

Offline embedding bakeoff compared six FastEmbed-supported models with
temporary local Qdrant indexes before the latest governed-metadata additions.
None beat the protected-metric switch rule. Keep
`BAAI/bge-small-en-v1.5` as the runtime default. Report:
`reports/retrieval/embedding_bakeoff_summary.md`.

### Vector Index Schema

Record shapes:

- `catalog_block`: stable ID, source type, canonical block ID, title/label,
  normalized text, block metadata, catalog provenance path/line, vector score,
  match reason.
- `manual_chunk`: stable ID, source type, title, normalized text, section
  metadata, path/line/URL provenance, vector score, match reason.
- `tutorial_chunk`: stable ID, source type, title, normalized text, tutorial
  metadata, path/line/URL provenance, vector score, match reason.
- `installed_example_summary` (optional later): stable ID, source type, title,
  normalized summary, block families, port families, fixture/corpus provenance,
  lexical score, vector score, hybrid score, match reason.

Index builds are deterministic from source files and installed catalog metadata.
The vector DB can be deleted and rebuilt safely from the source corpus.

### `search_manual`: Read-Only Manual/Tutorial Search

`search_manual` searches `docs/wiki_gnuradio_org/` for GNU Radio conceptual
questions.

Implementation properties:

- Lexical chunk ranking over cleaned Markdown pages.
- Returns bounded excerpts with citation path, line range, URL, oldid,
  last-edited metadata, and license.
- Explicitly strips mutation-shaped keys such as `transaction`, `params`,
  `block_id`, and `insert_tool_args`.

Use:

- GNU Radio how/why/conceptual explanations.
- PMTs, stream tags, sample-rate math, tutorials, diagnostics, DSP background.

Limits:

- Explanation-only.
- Not catalog truth.
- Not mutation authority.
- Not a source for hidden recipes, defaults, allowlists, or block blacklists.

## Active Session Context

The model receives compact active-session context:

- path and graph id
- file format and GRC version
- state revision
- dirty state
- validation status
- block count
- connection count
- variable count
- bounded variable preview
- bounded block preview
- bounded connection preview with exact connection IDs

This is a routing aid only. It is not a substitute for calling
`summarize_graph`, `get_grc_context`, `search_grc`, or `describe_block` when the
user asks for those operations.

## Clarification Flow

Clarification is allowed only when grounded in executable candidates or clear
contradiction.

Sources:

- Ambiguous typed turn, such as disable plus remove in one request.
- `auto_insert_block` finds multiple safe candidates.
- Pending clarification reply from user.

Requirements:

- Options must come from real executable candidates.
- Always include a free-text/custom option when asking the user to choose.
- Invalid clarification replies must not mutate.
- Clarification expires if the graph changes before resolution.

## Recovery Policy

Recovery is typed and bounded.

Allowed:

- One corrected retry for schema-level malformed mutation calls when the error
  class and allowed tools are explicit.
- Dirty-save refusal recovery when a clear validate step is needed.
- Clarification payload resolution.

Not allowed:

- Retrying GNU-invalid end states.
- Trying arbitrary tools until success.
- Silent remapping from destructive operations to safer operations.
- Tutorial/manual-derived repairs.
- Broad planning.

## Eval Strategy

Deterministic tests are the safety gate. Live evals are evidence about the local
2B model, not proof of full autonomy.

Live reports separate:

- `routing_pass`
- `argument_pass`
- `tool_success_pass`
- `semantic_pass`
- `safety_pass`
- `end_state_pass`
- `recovery_pass`

Current post-TurnPlan evidence:

- Deterministic adversarial TurnPlan matrix: 100+ prompts across
  disable/remove/disconnect/add/preview/save/uncertain wording.
- Tier 1 includes exact graph-delta checks for raw YAML refusal, parameter edit,
  preview no-mutation, explicit save reload/validate, and edit-validate-save.
- Tier 2/3 use shared exact graph-delta semantic checks for representative
  mutations.
- Tier 4 has 37 promoted installed-example cases covering read-only summary,
  validation, save-copy, PDU validation, message-port context, stream-mux
  validation, duplicate-family read-only inspection, connected-block rollback,
  GNU-validation rollback for invalid disconnect, occupied-input rewire
  rollback, connected selector block removal rollback, GNU-validation rollback
  for exact audio stream disconnect,
  exact message-port disconnect, exact message-port disconnect save/reload,
  exact message-port rewire, exact stream-port rewire, exact stream-port rewire
  save/reload, clarification-backed stream-port rewire with explicit
  validate/save and saved-graph reload proof, filter parameter edit
  save/reload with saved-value proof, saved-copy rollback persistence after
  connected-block removal rejection, tag propagation, Qt
  GUI/message-port variable edits, PDU
  block-parameter edits, PDU message-port disconnect preflight rollback,
  mixed stream/message add-request clarification, stream-demux parameter
  edits, variable/non-variable parameter edits, same-name duplicate target
  rejection across packet and UHD WBFM examples, state edit, and explicit save-copy. Promoted mutation cases
  use exact graph-delta semantic checks. Latest targeted Tier 4 `--n-runs 3`
  run passed 111/111 model attempts with 0 infra failures and 0 unstable cases.
  Installed audio add-connection rollback checks are kept as opt-in probes
  until the model routes them through `apply_edit` reliably; safe clarification
  is accepted behavior, but not promoted release evidence.
- Tier 5 adversarial live eval exists for minimal pairs, uncertain mutation,
  missing-anchor insertion, raw YAML refusal, and no-save validation.
- STOP_THE_LINE safety events in accepted baseline: 0.

Persisted live-eval rows include git commit/dirty state, prompt hash/version,
tool-schema hash, TurnPlan policy hash/version, model alias, backend metadata,
chat-template hash, result schema version, and fixture identifiers.

Latest release-candidate evidence is persisted under `reports/live_eval/`.
`reports/live_eval/rc_preview_boundary_release_dashboard.json` passed the
release dashboard with required phases 20/30/40/50, minimum 3 runs per case,
282/282 model passes, 0 infra failures, 0 unstable cases, and
`release_ready=true` after the preview/apply boundary fix.

Real-use evidence after this baseline should use structured dogfooding intake,
not ad-hoc notes or immediate runtime patches:

```bash
uv run grc-agent dogfood record "..." --source real_user --task-type rewire --failure-category confusing_clarification --reproducible
uv run grc-agent dogfood report --json
```

The dogfood path is evidence-only. It does not call the model, run tools, mutate
graphs, rebuild retrieval indexes, or promote fixes. It records sanitized
prompt/expected/actual/notes fields, redacts filesystem paths and `.grc`
filenames, and clusters observations conservatively by task, failure category,
and prompt topic. A cluster can justify design work only after repeated generic
evidence, cross-source evidence, or a STOP_THE_LINE safety event.

First dogfood result:
`reports/dogfood/DOGFOOD_2026-04-29.md` recorded 15 observations across 12
held-out installed examples and 3 workspace fixture stand-ins. It found 13
clean outcomes, 2 one-off GNU-validation failures before commit, 0
STOP_THE_LINE events, and 0 repeated generic failure clusters. No architecture
or runtime patch was justified.

Second dogfood result:
`reports/dogfood/DOGFOOD_2026-04-29_PASS2.md` found one generic
STOP_THE_LINE preview-contract bug during the first attempt. Preview prompts
containing "Do not apply it" could still cause the turn guard to require
`apply_edit` after `propose_edit`. The fix keeps negated apply wording
preview-only, narrows preview-only parameter edits to `propose_edit`, and
route-rejects `apply_edit` for preview-only turns. The patched rerun recorded
27 observations across 23 held-out installed examples and 4 workspace fixture
stand-ins: 21 clean/safe outcomes, 5 safe preflight rejections, 1 safe
GNU-validation failure before commit, 0 unresolved STOP_THE_LINE events, and 0
repeated generic failure clusters.

Third dogfood result:
`reports/dogfood/DOGFOOD_2026-04-29_PASS3.md` recorded 28 targeted boundary
observations across 24 held-out installed examples and 4 workspace/eval
stand-ins. It found 0 STOP_THE_LINE events, 0 preview mutations, 0 apply during
preview-only prompts, 0 save without explicit request, 0 save/reload mismatch,
and 0 repeated generic failure clusters. No additional runtime patch was
justified.

Local-alpha release package:
`reports/RELEASE_READY_LOCAL_ALPHA.md` is the release-readiness checklist for
tagged local-alpha/internal beta-style use. `docs/LOCAL_ALPHA_PILOT.md` is the
pilot guide for 5-10 private/user graphs and 20-30 real tasks. The current n=3
dashboard is reused for this packaging milestone because the changes are
documentation/reporting only, not runtime, prompt, schema, tool-order,
TurnPlan, or vector behavior changes.
`reports/LOCAL_ALPHA_RELEASE_TAG.md` is the release marker for
`local-alpha-v0.1.0-20260430`; create an actual Git tag only after committing
the current dirty release snapshot.

First copied user-graph pilot:
`reports/dogfood/USER_PILOT_2026-04-30.md` recorded 28 observations across 8
copied user/workspace graphs. It found 28 clean/safe outcomes, 0 STOP_THE_LINE
events, 0 preview mutations, 0 apply during preview-only prompts, 0 save
without explicit request, 0 invalid graph committed/saved, 0 wrong file writes,
0 save/reload mismatches, and 0 repeated generic failure clusters. No runtime,
prompt, schema, tool-order, TurnPlan, vector, or architecture patch was
justified.

## Accepted Design Decisions

- Prefer deterministic typed routing over a mini-router LLM for the current
  2B local model.
- Use dynamic tool narrowing to reduce cognitive load and prevent unsafe
  exposed choices.
- Keep runtime-owned route validation even when schemas are narrowed.
- Keep `search_manual` read-only and explanation-scoped.
- Keep `search_grc` graph-backed lexical retrieval, not mutation-adjacent RAG.
- Avoid Claude Code/Cursor-style multi-agent decomposition until repeated
  evidence shows the simpler typed design is insufficient.
- Keep broad graph design/planning out of runtime.

## Open Risks

- TurnPlan coverage can be too rigid or too broad; expand only from repeated
  generic failures, not single weird prompts.
- Dynamic schema narrowing can hide valid tools if classification is wrong.
- Small models may choose the least-wrong valid JSON even under schema
  constraints.
- Tier 2 is still mostly canonical-fixture scoped.
- Tier 4 is broader than before but still a small installed-example set.
- `block_uid` is currently read-only identity evidence in graph context, not a
  mutation handle. Promoting it to mutation disambiguation requires separate
  schema, route-gate, exact graph-delta, and save/reload proof.
- Block and connection candidate resolvers return read-only candidates plus the
  current `state_revision`. Clarification replies reject stale selections after
  graph changes and clarify on duplicate block or endpoint matches instead of
  choosing the first candidate.
- Duplicate block mutations are executable only when `instance_name +
  block_type` identifies one target. Same-name same-type duplicates stay
  non-executable until a separate UID mutation contract is proven.
- Endpoint disconnects normalize endpoint hints to one exact `connection_id`
  before mutation. Endpoint fields are not a separate mutation path.
- Exact rewires are not a planner path: `rewire_connection` resolves old-edge
  hints to one existing `connection_id`, clarifies on ambiguity with
  `state_revision`, resolves exact or bounded new-endpoint hints, then runs one
  verified `remove_connection` plus `add_connection` transaction. Ambiguous new
  endpoints clarify with executable options; stale selections are rejected, and
  failed rewires roll back without committing a partial disconnect. Invalid
  numeric hints for message-only ports are rejected before they can become
  executable clarification options.
- Live-eval pre-turn setup is not fixture magic: setup must be performed
  through public verified tools, persisted in the report, and followed by
  `validate_graph` before the measured user turn begins.
- `search_grc` is lexical; `semantic_search_grc` improves paraphrase recall but
  still misses some vague concepts.
- Vector retrieval is vector-only; no hybrid sparse search or reranker is
  implemented.
- Vector retrieval v1 should stop changing unless real clustered misses,
  exact-ID misses, false-positive failures, source-type misses, provenance
  failures, latency problems, or dependency breakage justify reopening it.
- Dogfood intake is not a hidden patch pipeline. It is a structured way to
  collect held-out installed-example and user-graph evidence; runtime changes
  still require the normal patch criteria, tests, and live gates.
- `numpy<2` is intentional compatibility debt for local GNU Radio 3.10.x
  Python bindings built against the NumPy 1.x ABI. Remove it only when the
  supported GNU Radio target and deterministic gates pass with NumPy 2.x.
- `search_manual` answer quality depends on corpus coverage and model synthesis.
- Full long-horizon autonomous graph construction is not proven.

## Future Options

Only consider these after measured repeated failures:

- Continue the local-alpha pilot from `docs/LOCAL_ALPHA_PILOT.md` before adding
  new architecture.
- Add llama.cpp `response_format=json_schema` finite-label routing as an
  advisory classifier. It may output only intent labels, never transactions.
- Add more TurnPlan intents when repeated generic failures prove the need.
- Expand Tier 4 installed-example semantic coverage.
- Use `grc-agent dogfood record/report` on copied held-out installed examples
  and user graphs, then patch only STOP_THE_LINE issues or repeated generic
  failures across unrelated graphs.
- Add release-dashboard artifacts as versioned evidence.
- Improve retrieval quality with hybrid sparse search or reranking only if eval
  misses justify the complexity.

## External System Designer Review Prompt

Use this prompt with an online reviewer that cannot see the codebase:

```text
You are an unbiased senior system designer reviewing a local autonomous GNU
Radio Companion assistant architecture. You cannot see the codebase. Review only
the supplied document: docs/SYSTEM_DESIGN_BIBLE.md.

Goal:
Give a hard, evidence-based verdict on whether the architecture is likely to be
safe, reliable, and maintainable for a low-intelligence local 2B model using
llama.cpp tool calling.

Be bold and skeptical. Do not praise by default. Call out wrong decisions,
missing gates, unsafe flows, unnecessary complexity, hidden coupling,
overfitting, latency/cost traps, and places where the design likely fails in
real use. For every criticism, give the concrete argument, likely failure mode,
and what proof or eval would resolve it.

Focus areas:
1. Exact end-to-end flow from user request to final answer.
2. Which agent/component owns which tools and decisions.
3. Whether a single GrcAgent plus typed TurnPlan is better than a mini-router,
   helper agent, RAG router, or multi-agent design for a local 2B model.
4. Whether dynamic tool narrowing and route-mismatch fail-closed behavior
   improve safety without blocking too many valid tasks.
5. Whether mutation safety is complete: no raw YAML edits, verified tools only,
   candidate-session mutation, grcc final validation, rollback, preview
   no-mutation, save only on explicit request.
6. Whether search_grc as graphify-backed structured lexical graph search is the
   right discovery tool, and whether its lack of vector semantic search is a
   problem.
7. Whether search_manual should remain explanation-only and prohibited from
   mutation recipes.
8. Whether auto_insert_block being deterministic rather than an LLM subagent is
   correct.
9. Whether the eval strategy proves the claims or is still too routing-focused.
10. What the next milestone should be, ranked by risk reduction.

Required output:
- Verdict: approve / approve with reservations / reject.
- Top 5 architectural risks, ordered by severity.
- Top 5 design decisions that are correct and should be protected.
- Specific changes you recommend, with rationale.
- Specific changes you reject, with rationale.
- Minimum eval evidence required before claiming release stability beyond local alpha.
- Any questions whose answers would materially change your verdict.

Do not assume hidden implementation exists. If the document does not specify a
safety gate, treat it as missing. Prefer simple deterministic mechanisms over
agentic complexity unless you can prove the complexity buys reliability.
```
