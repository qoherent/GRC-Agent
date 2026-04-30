# Project Blueprint

Updated: 2026-04-28

## Purpose

GRC Agent is a local-first assistant for GNU Radio Companion `.grc` flowgraphs. It creates, inspects, edits, validates, and saves graphs through a bounded tool contract. The model decides what graph work to attempt; verified tools decide whether mutations are allowed; `grcc` remains final validation authority.

## Safety Contract

- The model must never edit raw `.grc` YAML directly.
- All graph mutations go through `apply_edit`, `remove_connection`, `rewire_connection`, `insert_block_on_connection`, or `auto_insert_block`.
- Preview operations must never mutate the live graph.
- Failed edits must roll back atomically.
- `save_graph` is allowed only after the latest dirty state has validated successfully.
- Saving writes through a same-directory temp file and `os.replace`.
- No hidden repairs, prompt-regex transaction rewriting, fixture-specific logic, block recipes, block blacklists, unbounded retries, or unbounded candidate search.
- Clarification options must come from real validated graph/tool candidates and always include a custom/free-text option.

## Architecture

| Layer | Files | Responsibility |
|---|---|---|
| CLI | `cli.py` | `doctor`, `health`, `fake`, `chat`, direct `tool` execution, manual search, vector index build/search |
| Runtime | `agent.py`, `runtime/` | Tool registry, schemas, prompt, history, argument normalization, clarification handling |
| Adapter | `llama_server.py`, `llama_launcher.py` | llama.cpp HTTP transport, startup/reuse, bounded turn loop |
| Session | `flowgraph_session.py`, `models.py` | Loaded graph state, parsing, validation, atomic save, compact session snapshots |
| Catalog | `catalog/` | GNU block metadata, parameter defaults, port definitions, block descriptions |
| Retrieval | `retrieval/` | Bounded lexical catalog/session search and read-only vector retrieval |
| Manual | `manual/` | Read-only GNU Radio tutorial/reference cleaning and cited lexical search |
| Validation | `validation/` | Pure staged preflight checks and default filling |
| Transaction | `transaction/` | Atomic propose/apply on copied sessions before live commit |

Adapter boundary: `llama_server.py` should stay transport and bounded-loop oriented. Existing assistant-text fallback parsing is legacy weak-model compatibility and must not expand; moving it behind `GrcAgent` requires separate approval plus Tier 2 live eval.

## Model Tools

Seventeen tools are exposed in fixed order:

1. `new_grc(graph_id="new_flowgraph")`
2. `load_grc(file_path)`
3. `summarize_graph(max_blocks=None)`
4. `search_grc(query, scope="catalog|session", k=5)`
5. `get_grc_context(node_id, hops=1, max_nodes=20)`
6. `describe_block(block_id)`
7. `search_manual(query, k=3)`
8. `semantic_search_grc(query, scope="all|catalog|manual|tutorial", k=5)`
9. `suggest_compatible_insertions(connection_id, k=5)`
10. `insert_block_on_connection(connection_id, block_type, instance_name, params=None)`
11. `auto_insert_block(goal, preferred_block_type=None, target_hint=None, max_candidates=10)`
12. `remove_connection(connection_id)`
13. `rewire_connection(old_connection_id|old endpoints, new endpoints|new endpoint hints)`
14. `apply_edit(transaction)`
15. `propose_edit(transaction)`
16. `validate_graph()`
17. `save_graph(path=None)`

Tool order matters. Keep `search_manual` after catalog block description and before mutation helpers, keep the exact-argument `remove_connection` wrapper before the nested `apply_edit` fallback, keep `apply_edit` before `propose_edit`, and keep insertion helpers before lower-level edit tools unless a separate eval-backed change proves otherwise.

The fixed registry remains the public tool contract, but normal model turns now use a typed `TurnPlan` to narrow the schemas sent to llama.cpp when the intent is clear. The runtime owns this narrowing; the model never receives hidden mutation recipes. Unknown or unclassified mutation requests clarify before any model call; they never expose `apply_edit`, `propose_edit`, `save_graph`, or destructive wrappers until the runtime can classify a finite safe intent.

## Supported Graph Work

- `new_grc` creates a minimal empty skeleton; construction uses `apply_edit`.
- `load_grc` binds one `.grc` file as the active session.
- `summarize_graph`, `search_grc`, `semantic_search_grc`, `get_grc_context`, `describe_block`, and `search_manual` are read-only inspection/explanation paths.
- `update_params` supports unique loaded blocks and variables, including symbolic GNU/Python expressions. Duplicate names may clarify only when `instance_name + block_type` is an executable discriminator.
- `update_states` supports `enabled` and `disabled` on unique loaded blocks. Duplicate names may clarify only when `instance_name + block_type` is an executable discriminator.
- `add_block` supports arbitrary catalog blocks with catalog-default parameter filling.
- `add_connection` and `remove_connection` support stream and message-port endpoints.
- `remove_block` requires the target to be detached and uniquely identified by the verified operation.
- `insert_block_on_connection` is a thin exact-arg wrapper around `apply_edit`.
- `remove_connection` is a thin wrapper around the verified edit path. It accepts an exact `connection_id`, or endpoint hints that the runtime resolves to one existing `connection_id` before mutation. Endpoint fields are not a second mutation path; zero matches fail unchanged and multiple matches clarify.
- `rewire_connection` is a thin wrapper around one ordered verified transaction:
  remove one resolved old connection, then add one resolved new connection. Old
  endpoint hints resolve to one `connection_id` or clarify with
  `state_revision`; partial new-endpoint hints are allowed only when they
  resolve to one executable candidate or a bounded clarification choice. The
  resolver rejects invalid message-port numeric hints before they can become
  executable clarification options.
- `suggest_compatible_insertions` is read-only and returns catalog-backed candidate args.
- `auto_insert_block` performs bounded candidate search, commits one `grcc`-valid insertion, asks for clarification, or rejects safely.
- Loaded blocks now carry a deterministic read-only `block_uid` for inspection
  and eval identity. It is not a mutation handle yet; duplicate instance-name
  edits still require explicit disambiguation through verified tool arguments.
- `FlowgraphSession.resolve_block_reference(...)` returns read-only identity
  candidates with the current `state_revision`; callers must reject stale
  selections after any graph change. Same-name same-type duplicates remain
  non-executable until a separate UID mutation design is proven.
- `FlowgraphSession.find_connection_candidates(...)` returns read-only endpoint
  candidates with the current `state_revision`; multiple matches must clarify,
  not pick the first connection.

## Runtime Properties

- Active-session context is explicit in CLI output, runtime history, and model-visible messages.
- Active-session context includes bounded counts, variable/block previews, and connection IDs so small models can route exact connection work without a full graph dump.
- History compaction keeps the latest active-session snapshot while trimming older large tool payloads.
- Tool-call schemas reject unknown tools, missing fields, wrong types, enum mismatches, and extra fields before execution.
- Each normal model turn is classified into a finite typed `TurnPlan` before the first completion. The plan records intent, required actions, allowed tools, expected transaction op types, clarification state, and evidence text.
- For classified turns, llama.cpp receives only the schemas allowed by the plan. For example, explicit disable/enable requests expose `apply_edit`/`propose_edit` plus any requested follow-up tools, and expect `update_states`.
- `uncertain_mutation` covers vague mutation wording such as swap/repair/rewire/fix/change topology when no finite safe intent is known. It clarifies before any model call and exposes no tools.
- Natural insertion wording routes to `auto_insert_block` only when the user supplies placement context such as a connection, source/destination, or signal path. Missing placement context clarifies before any model call.
- Exact disconnect wording routes to `remove_connection` without an `apply_edit`
  fallback. Endpoint hints are normalized to `connection_id` first; vague
  disconnect wording clarifies or stays in `uncertain_mutation`.
- Exact or bounded rewire wording routes to `rewire_connection` when the old
  connection is exact/resolvable and the new endpoint is exact or resolvable
  from bounded endpoint hints. Ambiguous old edges or new endpoints clarify with
  executable candidate options and `state_revision`; vague topology rewires
  stay in `uncertain_mutation`.
- Deterministic exact tool calls, such as exact add-variable and exact
  connection-id rewires, are built behind `GrcAgent` after `TurnPlan`
  classification. The llama.cpp adapter only transports the resulting
  structured tool call and still runs route validation, schema validation,
  wrapper execution, preflight, `grcc`, and rollback.
- Route mismatches fail closed before execution. If a disable/enable turn attempts `remove_block`, no graph mutation runs; the result is `route_mismatch`, not a hidden rewrite to `update_states`.
- Schema-rejected tool calls are recorded as failed turn actions, so the bounded turn guard does not nudge the model to continue after invalid arguments.
- `apply_edit` validates internally before committing; successful apply satisfies the dirty-state validation gate for save, but explicit user validation still requires `validate_graph`.
- `grcc` is used for final validation and remains the authority over GNU behavior.
- llama.cpp local startup uses file locking, model-alias verification, deterministic `temperature=0.0`, bounded generation defaults, and `--no-mmproj` when supported.
- `doctor` is passive by default and does not start llama.cpp unless `--start-llama` is supplied.
- Live eval reports collect best-effort llama.cpp `/props` metadata so backend tool-template/parser capability is visible without failing older servers.
- Live eval reports include repeat-run stability metadata and persisted release metadata: git commit, prompt hash/version, tool-schema hash, TurnPlan policy hash/version, model alias, backend metadata, chat-template hash, results schema version, and fixture identifiers. `--n-runs` controls repeated attempts and `--stability-threshold` controls the reported per-case release-stability threshold without changing majority pass/fail gating.
- `tests.llama_eval.release_dashboard` aggregates persisted `--results-path` stores across live tiers and fails CI-style when required phases, minimum run counts, infra health, or per-case stability are not met.
- Failed-tool recovery is classified by a typed policy shared with live evals. The policy can mark missing mutation arguments, dirty-save refusal, and clarification payloads as bounded recoverable cases; GNU-invalid end states and unsupported requests stay nonrecoverable. It snapshots graph state before/after recovery attempts, limits recovery mutation retries, and does not synthesize graph recipes or bypass tools.
- Raw YAML direct-edit, undo/redo, and Python export/code-generation requests are refused as unsupported.
- `search_grc` remains deterministic lexical graph search. It now has a finite alias layer for known misses such as audio smoother, automatic gain control, spectrum, rate limiter, scope, and trace.
- `semantic_search_grc` is read-only vector search over a local Qdrant index built with FastEmbed `BAAI/bge-small-en-v1.5`. It is candidate discovery only and is not exposed for `uncertain_mutation`.
- Vector retrieval v1 is frozen as local Qdrant + FastEmbed + `BAAI/bge-small-en-v1.5`, vector-only, read-only, no hybrid, no reranker, and no runtime multi-model selector. The operating contract is `docs/VECTOR_RETRIEVAL.md`; the frozen baseline is `reports/retrieval/VECTOR_BASELINE_V1.md`.
- Catalog semantic metadata additions must describe stable block capability, not patch one eval query. The alias must still be true if the eval query did not exist, must be supported by at least 3 clustered misses or repeated misses across 2 distinct sources, must include mutation-shaped negative traps, and must rerun retrieval eval before acceptance. One-off and ambiguous clusters do not qualify. Use `docs/VECTOR_METADATA_CHANGE_CHECKLIST.md` before any metadata change. Current governed entries and false-positive checks are recorded in `reports/retrieval/catalog_semantic_metadata.md`.
- `grc-agent vector miss` records sanitized real-user/eval/manual-review retrieval misses to JSONL evidence only. It redacts filesystem paths and `.grc` filenames from stored query, notes, expected IDs, and actual top IDs while preserving normal catalog IDs. `grc-agent vector misses` clusters and deduplicates those records conservatively; shared expected IDs alone do not merge unrelated wording. `grc-agent vector proposals` generates a human-review candidate report only. These commands must not update metadata, rebuild indexes, change rankings, call model tools, or authorize mutation.
- `grc-agent vector gc` is explicit cleanup for old local Qdrant collections from this index family. It is dry-run by default, requires `--apply` to delete, preserves the active alias target plus one previous retired collection, and deletes older staging/retired collections only with `--apply`.
- `grc-agent dogfood record` and `grc-agent dogfood report` provide structured real-use evidence intake. They are evidence-only: no model call, no tool execution, no graph mutation, no retrieval rebuild, and no automatic fix promotion. Intake redacts filesystem paths and `.grc` filenames from stored user text, records task/failure/severity categories, and clusters repeated observations conservatively so fixes are based on repeated generic gaps rather than one-off prompts.
- Assistant-text fallback parsing is frozen legacy weak-model compatibility. Do not expand it into a hidden router, hidden repair path, or mutation policy layer without a separate design review and Tier 2/3/5 evidence.
- Vector/hybrid retrieval must remain read-only. It may return candidate blocks/docs/chunks with provenance and scores, but it must not return transactions, params payloads, insert arguments, save instructions, hidden recipes, tutorial-derived defaults, or any mutation authorization.

## Current Status

Local alpha is ready for daily manual use.

- Ruff gate passed: `uv run ruff check src/ tests/`.
- Deterministic unittest gate passed: `uv run python -m unittest`.
- Tier 1 live eval reporting distinguishes routing pass, argument pass, tool success pass, semantic pass, safety pass, end-state pass, and recovery pass. The first semantic checks cover simple parameter edit, preview no-mutation, explicit save reload/validate, raw YAML refusal no-mutation, and edit-validate-save.
- Tier 1 live quick eval with semantic reporting passed: 15/15. Tool success passed 12/15 because three insertion cases safely returned clarification rather than a committed mutation.
- Tier 2 release eval now uses the shared declarative live scenario harness and reports routing, argument, tool success, semantic, safety, end-state, and recovery dimensions. Latest quick live run passed 37/37 with every dimension green; latest `--n-runs 3` run passed 111/111 model attempts with 0 infra failures.
- Tier 3 multi-turn live eval covers clarification replies, preview-then-apply,
  edit-then-validate, edit-then-save, bounded recovery classification, vague
  connection refusal, old-edge rewire clarification, new-endpoint rewire
  clarification, stale rewire selections, and invalid rewire rollback. Latest
  quick live run passed 13/13 with every dimension green; latest `--n-runs 3`
  run passed 39/39 model attempts with 0 infra failures.
- Tier 4 installed-example live eval covers read-only summary, validation,
  save-copy behavior, edit/validate, edit/validate/save-copy, message-port
  context, stream mux validation, PDU validation, tag propagation, Qt
  GUI/message-port variable edits, duplicate-family read-only inspection,
  connected-block rollback, exact message-port disconnect, exact message-port
  disconnect save/reload, exact message-port rewire, exact stream-port rewire,
  exact stream-port rewire save/reload, PDU block-parameter edits,
  PDU message-port disconnect preflight rollback, stream-demux parameter edits,
  mixed stream/message add-request clarification, variable and non-variable
  parameter edits, same-name duplicate target rejection across packet and UHD
  WBFM examples, and
  state edits on installed GNU Radio examples. The current promoted gate has
  37 cases, including GNU-validation rollback for invalid disconnect,
  GNU-validation rollback for exact audio stream disconnect,
  occupied-input rewire rollback, and connected selector block removal
  rollback, plus clarification-backed stream-port rewire followed by explicit
  validate/save and saved-graph reload proof, filter parameter edit
  save/reload with saved-value proof, and saved-copy rollback persistence
  after connected-block removal rejection. Latest targeted Tier 4
  `--n-runs 3` run passed 111/111 model
  attempts with 0 infra failures and 0 unstable cases.
  Two installed audio add-connection rollback checks remain `--include-probes`
  only because the current 2B model routes them to safe clarification instead
  of `apply_edit`; they are not release evidence until repeated live runs pass.
- Tier 5 adversarial live eval latest quick run passed 7/7 with every dimension green; latest `--n-runs 3` run passed 21/21 model attempts with 0 infra failures.
- Tier 5 adversarial live eval now covers disable/remove/disconnect minimal pairs, vague mutation no-mutation behavior, missing-anchor insertion clarification, raw YAML refusal, and validation without save.
- Deterministic adversarial TurnPlan coverage now includes 100+ prompts across disable/remove/disconnect/add/preview/save/vague wording.
- The latest persisted release-candidate dashboard is
  `reports/live_eval/rc_preview_boundary_release_dashboard.json` and passed
  with `release_ready=true`: required phases 20/30/40/50 present, 282/282 model
  passes, 0 infra failures, 0 unstable cases, and minimum 3 runs per case after
  the preview/apply boundary fix.
- STOP_THE_LINE safety events: 0 in the accepted eval baseline.
- Structured dogfooding intake is available through `grc-agent dogfood record`
  and `grc-agent dogfood report`. This is the preferred path for manual
  held-out installed-example and user-graph evidence after the current Tier 4
  expansion; it does not alter runtime behavior or promote fixes automatically.
- First operational dogfood pass:
  `reports/dogfood/DOGFOOD_2026-04-29.md` recorded 15 observations across 12
  held-out installed examples and 3 workspace fixture stand-ins. It found 13
  clean outcomes, 2 one-off GNU-validation failures before commit, 0
  STOP_THE_LINE events, and 0 repeated generic failure clusters. No runtime
  patch was justified.
- Second operational dogfood pass:
  `reports/dogfood/DOGFOOD_2026-04-29_PASS2.md` stopped on a preview-contract
  bug in the first attempt: preview prompts containing "Do not apply it" could
  still require `apply_edit` through the turn guard. The generic patch now
  treats negated apply wording as preview-only, narrows preview-only parameter
  edits to `propose_edit`, and route-rejects `apply_edit` for preview-only
  turns. The patched rerun recorded 27 observations across 23 held-out
  installed examples and 4 workspace fixture stand-ins: 21 clean/safe outcomes,
  5 safe preflight rejections, 1 safe GNU-validation failure before commit, 0
  unresolved STOP_THE_LINE events, 0 preview mutations after the patch, 0
  save/reload mismatches, and 0 repeated generic failure clusters.
- Third targeted dogfood pass:
  `reports/dogfood/DOGFOOD_2026-04-29_PASS3.md` recorded 28 boundary-focused
  observations across 24 held-out installed examples and 4 workspace/eval
  stand-ins. It found 0 STOP_THE_LINE events, 0 preview mutations, 0 apply
  during preview-only prompts, 0 save without explicit request, 0 save/reload
  mismatch, and 0 repeated generic failure clusters. No additional runtime
  patch was justified.
- Local-alpha release package:
  `reports/RELEASE_READY_LOCAL_ALPHA.md` records the supported scope,
  environment assumptions, smoke commands, standard gates, patch policy, and
  known limits for tagged local-alpha/internal beta-style use.
  `docs/LOCAL_ALPHA_PILOT.md` is the real-user pilot checklist. The current
  n=3 dashboard is reused for this packaging milestone because no runtime,
  prompt, schema, tool-order, TurnPlan, or vector behavior changed after the
  latest release-candidate evidence.
  `reports/LOCAL_ALPHA_RELEASE_TAG.md` is the local-alpha release marker for
  `local-alpha-v0.1.0-20260430`; a Git tag should be created only after the
  current dirty release snapshot is committed.
- First copied user-graph pilot:
  `reports/dogfood/USER_PILOT_2026-04-30.md` recorded 28 observations across 8
  copied user/workspace graphs. It found 28 clean/safe outcomes, 0
  STOP_THE_LINE events, 0 preview mutations, 0 apply during preview-only
  prompts, 0 save without explicit request, 0 invalid graph committed/saved, 0
  wrong file writes, 0 save/reload mismatches, and 0 repeated generic failure
  clusters. No runtime patch was justified.
- Default backend remains `unsloth/gemma-4-E2B-it-GGUF` through llama.cpp.
- The `numpy<2` dependency marker is intentional compatibility debt for local
  GNU Radio 3.10.x Python bindings built against the NumPy 1.x ABI. Remove it
  only when the supported GNU Radio target and deterministic gates pass with
  NumPy 2.x.

## Known Limits

- The default 2B model is reliable for summarize, inspect, search, describe, validate, save, preview, raw-YAML refusal, and simple parameter edits.
- Natural-language insertion is bounded and safe, but may clarify or reject instead of mutating.
- Complex multi-step graph creation is still model-limited.
- Exact natural-language disconnection requests route through
  `remove_connection` when a connection ID can be provided or resolved from
  endpoint hints; GNU-invalid end states roll back and are classified as
  nonrecoverable.
- Exact rewires are atomic remove-plus-add transactions. Ambiguous old-edge or
  new-endpoint hints clarify and stale choices are rejected after graph
  changes. A failed preflight or GNU-invalid rewire leaves the live graph
  unchanged; partial disconnects are not committed.
- Endpoint candidate resolution is clarification-backed, not automatic
  guessing. Ambiguous endpoint matches ask the user, and stale selections are
  rejected after graph changes.
- Copying structured fields from one tool output into another is not consistently reliable with the 2B model.
- Runtime correction now handles schema-level malformed mutation calls with one typed retry through the model, restricted to recovery-policy allowed tools. The promoted selector block-parameter live case exercises this path and still validates through `apply_edit` plus `grcc`.
- Valid installed examples with mixed stream/message port identifiers now load through `GrcAgent`; connection ordering normalizes port sort keys instead of comparing unlike Python types.
- Natural-language state edits are verified by the tool layer and guarded by typed routing. The default 2B model may still confuse "disable block" with removal, but that mismatch is now blocked before mutation instead of reaching graph preflight.
- Vague user goals may now clarify or preview instead of mutating. This is intentional: safe false-blocks are preferred over guessing topology changes with a 2B model.
- Expert GNU/DSP answers can use cited `search_manual` excerpts, but answer quality still depends on backend synthesis and corpus coverage.
- The current live evals measure bounded tool routing, selected semantic/end-state outcomes, multi-turn follow-up behavior, and safety; they do not prove Claude Code/Cursor-style long-horizon autonomy.
- Tier 2 semantic checks are broader than Tier 1 but still canonical-fixture scoped. Tier 4 adds a small installed-example smoke/edit gate, but it is not evidence for arbitrary installed GNU examples or long-horizon design tasks.

## Tutorial Corpus Policy

`docs/wiki_gnuradio_org/` is kept as a local GNU Radio tutorial/reference corpus. It is available through `search_manual` for cited, explanation-only retrieval and may inform future explanation evals.

- Do not turn tutorials into runtime block recipes.
- Do not add tutorial-derived hidden repairs.
- Do not use tutorial pages as block blacklists or allowlists.
- Keep catalog metadata and `grcc` as the truth for tool arguments and validity.
- Manual results must keep provenance and must not expose mutation-shaped outputs such as transactions or insert-tool arguments.

## Patch Criteria

Patch runtime behavior only when one of these occurs:

1. Unsafe mutation.
2. Invalid graph committed or saved.
3. Preview mutates the live graph.
4. Raw YAML edit bypasses the guard.
5. Wrong file overwritten.
6. Valid installed GNU example fails to load.
7. The same failure repeats across three or more unrelated real-use graphs.

Do not patch isolated small-model weirdness.

Use dogfooding evidence before broadening runtime behavior:

```bash
uv run grc-agent dogfood record "..." --source real_user --task-type param_edit --failure-category routing_failure --reproducible
uv run grc-agent dogfood report --json
```

Dogfood records may come from `real_user`, `eval`, `manual_review`,
`installed_example`, or `user_graph` observations. A cluster is only a generic
patch candidate after repeated evidence, cross-source evidence, or a
STOP_THE_LINE safety event. The intake path must never become an automatic
metadata, prompt, tool-order, or runtime-change pipeline.

## Standard Gates

```bash
uv run ruff check src/ tests/
uv run python -m unittest
uv run python -m tests.llama_eval.tier1_live --quick
uv run python -m tests.llama_eval.tier2_release
uv run python -m tests.llama_eval.tier3_multiturn --quick
uv run python -m tests.llama_eval.tier4_external_examples --quick
uv run python -m tests.llama_eval.tier5_adversarial --quick
uv run python -m tests.retrieval_eval.vector_regression
```

Use Tier 1 after runtime, prompt, schema, or live-eval changes. Use Tier 2 before release or after adapter behavior changes. Use Tier 3 before claiming multi-turn clarification/recovery reliability. Use Tier 4 when installed GNU Radio examples are available. Use Tier 5 after TurnPlan, tool narrowing, safety, or route-gate changes. For release candidates, run Tier 2, Tier 3, Tier 4, and Tier 5 with `--n-runs 3` and inspect the stability report. Tier 4 `--include-probes` is for future known-gap investigation only and is not part of release readiness unless a probe is explicitly promoted after repeated stable runs.

Persisted release dashboard:

```bash
uv run python -m tests.llama_eval.release_dashboard \
  --results-path /tmp/grc-agent-live-runs.json \
  --min-runs-per-case 3
```

Latest release-candidate evidence commands:

```bash
uv run python -m tests.llama_eval.tier2_release --n-runs 3 --results-path reports/live_eval/rc_preview_boundary_tier2_n3.json
uv run python -m tests.llama_eval.tier3_multiturn --n-runs 3 --results-path reports/live_eval/rc_preview_boundary_tier3_n3.json
uv run python -m tests.llama_eval.tier4_external_examples --n-runs 3 --results-path reports/live_eval/rc_preview_boundary_tier4_n3.json
uv run python -m tests.llama_eval.tier5_adversarial --n-runs 3 --results-path reports/live_eval/rc_preview_boundary_tier5_n3.json
uv run python -m tests.llama_eval.release_dashboard \
  --results-path reports/live_eval/rc_preview_boundary_tier2_n3.json \
  --results-path reports/live_eval/rc_preview_boundary_tier3_n3.json \
  --results-path reports/live_eval/rc_preview_boundary_tier4_n3.json \
  --results-path reports/live_eval/rc_preview_boundary_tier5_n3.json \
  --min-runs-per-case 3 > reports/live_eval/rc_preview_boundary_release_dashboard.json
```

Latest release-candidate dashboard result:
`reports/live_eval/rc_preview_boundary_release_dashboard.json` has
`release_ready=true`, 282/282 model passes, 0 infra failures, 0 unstable cases,
and no short-run cases across Tier 2/3/4/5 at `--n-runs 3`.

Vector retrieval evals compare lexical and vector retrieval on paraphrase
queries, exact block IDs, manual/tutorial conceptual queries, false-positive
traps, citation accuracy, latency, deterministic record rebuilds, and safety
checks proving retrieval cannot expose mutation tools or authorize graph
changes. Latest local eval: 290 deterministic cases, vector top-k hits 276,
lexical top-k hits 168, safety 290/290, provenance 290/290, 0 exact-ID misses,
0 false-positive failures, and 0 source-type misses. The report includes
miss analysis for vector misses, lexical wins over vector, exact-ID misses,
false-positive failures, and source-type misses; inspect those before planning
hybrid retrieval. Current triage is recorded in
`reports/retrieval/vector_miss_triage.md`.
- `tests.retrieval_eval.vector_regression` is the frozen vector v1 no-LLM
  regression gate. It requires vector hits >=276, exact-ID misses 0,
  false-positive failures 0, source-type misses 0, safety 290/290, provenance
  290/290, and deterministic rebuild pass true. It reports lexical hits but
  does not hard-fail on the lexical count.
- Offline embedding bakeoff compared six FastEmbed-supported models using
  temporary local indexes before the latest governed-metadata additions. None
  beat the protected-metric switch rule, and runtime default remains
  `BAAI/bge-small-en-v1.5`; report:
  `reports/retrieval/embedding_bakeoff_summary.md`.

## Backlog

- Continue the local-alpha pilot on copied private/user graphs using
  `docs/LOCAL_ALPHA_PILOT.md`; patch only STOP_THE_LINE issues or repeated
  generic failures across unrelated graphs.
- Keep expanding Tier 4 only with installed-example cases that pass repeated live evidence; any future pre-turn setup must use public verified tools, persist setup calls in the report, and validate the graph before the measured turn.
- Dogfood held-out installed examples and user graphs with structured intake before adding more synthetic eval cases. Classify failures as routing, argument-copying, preflight false reject, unsafe mutation risk, `grcc` failure, save/reload mismatch, confusing clarification, retrieval miss, tool error, or other.
- Expand the typed `TurnPlan` coverage only from repeated generic evidence; do not recover wrong destructive operations by inventing hidden repairs.
- Experiment with llama.cpp `response_format=json_schema` only for an optional finite-label router if deterministic `TurnPlan` coverage proves insufficient. That router must output intent labels only, never transactions.
- Move assistant-text fallback parsing behind `GrcAgent` without behavior drift.
- Keep vector retrieval vector-only until eval evidence justifies hybrid sparse search or reranking.
- Expand manual retrieval quality and coverage for explanation-only answers without making it mutation-adjacent.
- Persist accepted release-dashboard artifacts in a stable location when cutting tagged releases.
- Promote `block_uid` from read-only identity evidence to mutation
  disambiguation only after exact tool-schema, route-gate, and save/reload
  tests prove it cannot target the wrong duplicate block.
- Add a clarification-backed endpoint disconnect flow that resolves exact
  source/destination endpoint candidates, rejects stale revisions, and clarifies
  on multiple matches before any mutation.
