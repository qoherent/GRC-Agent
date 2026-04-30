# GRC Agent

Local GNU Radio Companion `.grc` assistant focused on safe, validated, local-first graph edits.

## Vision

GRC Agent should become a reliable autonomous assistant for GNU Radio Companion graphs. It should inspect the active graph, choose verified tools, mutate only through validated transactions, verify the result, save only when asked, and ask the user naturally when required details are missing or contradictory.

The project optimizes for reliability over cleverness. Autonomy comes from typed state, explicit tools, deterministic validation, and measured evals, not from hidden YAML edits, prompt tricks, or tutorial-derived recipes.

## Status

- One active `.grc` session per agent.
- Model-facing runtime exposes 17 bounded tools for load, inspect, lexical search, vector search, describe, explanation-only manual retrieval, edit, validate, exact connection removal/rewire, and save.
- All meaningful mutations go through verified tools and validate before commit.
- Classified turns use typed tool narrowing before llama.cpp sees schemas, so clear requests such as enable/disable expose only the relevant mutation path plus requested follow-up tools.
- Vague mutation requests are treated as `uncertain_mutation`: they clarify before any model call and do not expose preview, live mutation, destructive, or save tools.
- Raw `.grc` YAML editing, undo/redo, and Python export/code-generation requests are refused.
- `grcc` remains final graph-validity authority.
- Default local backend is `unsloth/gemma-4-E2B-it-GGUF` through llama.cpp.
- Current deterministic safety coverage is strong; current live Tier 1/Tier 2/Tier 3/Tier 4 evals are routing/behavior evidence, not proof of full autonomous reliability.

## Reliability Truth

The product is local alpha quality for daily manual use.

- Deterministic tests cover schema rejection, raw-YAML refusal, rollback, save gating, atomic save, insert safety, clarification handling, turn-guard behavior, and typed recovery classification.
- Live evals check whether the local model routes representative prompts through the right tools and reaches selected semantic/end states.
- Live evals report routing pass, argument pass, tool success pass, semantic/end-state pass, safety pass, and recovery pass separately.
- A task is not considered reliable just because the expected tool name appeared. Correct arguments, graph diff, validation, saved file, and user-facing behavior matter.
- Route mismatches fail closed. For example, if a disable request is interpreted as block removal, execution is blocked before mutation rather than silently repaired.
- Deterministic adversarial TurnPlan coverage now exercises 100+ prompts, and live evals can run the Tier 5 adversarial intent/safety suite.

## Repo Map

- `src/grc_agent/`: package code for runtime, session, catalog, retrieval, validation, transaction, llama adapter, and CLI.
- `tests/`: deterministic `unittest` regression coverage.
- `tests/data/random_bit_generator.grc`: canonical fixture graph.
- `tests/llama_eval/`: live llama.cpp routing and behavior evals.
- `docs/BLUEPRINT.md`: current architecture, safety contract, status, and roadmap.
- `docs/QUICKSTART.md`: setup and common usage.
- `docs/LOCAL_ALPHA_PILOT.md`: real-user pilot checklist and failure recording workflow.
- `docs/wiki_gnuradio_org/`: local GNU Radio tutorial/reference corpus for explanation-only retrieval and evals.
- `reports/RELEASE_READY_LOCAL_ALPHA.md`: release-readiness checklist, evidence summary, and patch policy.

## Install

```bash
uv sync
uv run grc-agent doctor
```

Prerequisites:

- Python >= 3.12
- GNU Radio 3.10+ with `grcc` on `PATH`
- llama.cpp server binary/model available for model-backed chat

The CLI can auto-start a configured local llama.cpp server for normal `chat` use.
`doctor` is passive by default; use `uv run grc-agent doctor --start-llama` when you explicitly want it to start or reuse llama.cpp during environment checks.

## Usage

Open an existing graph:

```bash
uv run grc-agent chat tests/data/random_bit_generator.grc
```

Run one prompt:

```bash
uv run grc-agent chat tests/data/random_bit_generator.grc \
  --message "Change samp_rate to 48000 and validate the graph."
```

Create a new graph:

```bash
uv run grc-agent chat --new
```

Run one tool without a model:

```bash
uv run grc-agent tool summarize_graph --file tests/data/random_bit_generator.grc
uv run grc-agent tool validate_graph --file tests/data/random_bit_generator.grc
```

Search cited GNU Radio manual/tutorial excerpts without a model or graph mutation:

```bash
uv run grc-agent manual search "stream tags" --k 3 --json
```

Build and query the local read-only vector index:

```bash
uv run grc-agent vector build
uv run grc-agent vector stats --json
uv run grc-agent vector search "audio smoother" --scope catalog --k 5 --json
uv run grc-agent vector miss "leveler block" --expected-block analog_agc_xx --actual-top-id blocks_xor_xx --category ambiguous_wording --source real_user --json
uv run grc-agent vector misses --json
uv run grc-agent vector proposals --json
uv run grc-agent vector gc --json
uv run grc-agent vector gc --apply --json
```

`vector miss` records sanitized real-user retrieval misses in JSONL evidence
without changing metadata, rankings, tools, or graph state. Use it to collect
repeated misses before adding new semantic metadata. `vector misses` clusters
and deduplicates intake so similar misses are reviewed as evidence groups, not
one-off query patches. Intake redacts filesystem paths and `.grc` filenames
from stored query, notes, expected IDs, and actual top IDs while preserving
normal catalog IDs. Clustering is conservative: shared expected IDs alone do
not merge unrelated wording. `vector proposals` is a human-review report only:
it does not edit metadata, rebuild indexes, change rankings, or promote
retrieval behavior.

`vector gc` is opt-in cleanup for old local Qdrant collections from this index
family. It is a dry run unless `--apply` is provided. The retention policy is:
preserve the active alias target plus one previous retired collection; delete
older staging/retired collections only with `--apply`.

Record real-use dogfooding observations without changing runtime behavior:

```bash
uv run grc-agent dogfood record \
  "Change cutoff to 3000, validate, and save a copy" \
  --graph /path/to/heldout.grc \
  --source real_user \
  --task-type param_edit \
  --failure-category no_failure \
  --json

uv run grc-agent dogfood record \
  "Rewire the old edge to the new sink" \
  --source manual_review \
  --task-type rewire \
  --failure-category confusing_clarification \
  --actual-tool rewire_connection \
  --reproducible \
  --notes "Clarification options were hard to distinguish." \
  --json

uv run grc-agent dogfood report --json
```

`dogfood record` is evidence intake only. It does not call the model, execute
tools, mutate graphs, rebuild retrieval indexes, or promote fixes. It redacts
filesystem paths and `.grc` filenames from stored user text. Use `dogfood
report` to cluster repeated observations, then patch only repeated generic
failures or STOP_THE_LINE safety issues.

## Verification

Deterministic gates:

```bash
uv run ruff check src/ tests/
uv run python -m unittest
uv run grc-agent fake tests/data/random_bit_generator.grc
uv run python -m tests.retrieval_eval.vector_retrieval
uv run python -m tests.retrieval_eval.vector_regression
```

Full repo lint after cleanup/refactors:

```bash
uv run ruff check
```

Live model gates:

```bash
uv run python -m tests.llama_eval.tier1_live --quick
uv run python -m tests.llama_eval.tier2_release
uv run python -m tests.llama_eval.tier3_multiturn --quick
uv run python -m tests.llama_eval.tier4_external_examples --quick
uv run python -m tests.llama_eval.tier5_adversarial --quick
```

Release stability dashboard over persisted repeated live runs:

```bash
uv run python -m tests.llama_eval.release_dashboard \
  --results-path /tmp/grc-agent-live-runs.json \
  --min-runs-per-case 3
```

Latest release-candidate evidence is persisted under `reports/live_eval/`.
`reports/live_eval/rc_preview_boundary_release_dashboard.json` passed with
`release_ready=true`: Tier 2/3/4/5 at `--n-runs 3` produced 282/282 model
passes, 0 infra failures, and 0 unstable cases after the preview/apply boundary
fix. Tier 4 currently has 37
promoted installed-example cases, including connected-block rollback,
GNU-validation rollback for invalid disconnect, occupied-input rewire rollback,
duplicate-family read-only inspection, exact stream disconnect rollback on an
installed audio example, exact message-port disconnect,
message-port disconnect save/reload, exact message-port rewire, exact
stream-port rewire, stream-port rewire save/reload, clarification-backed
stream-port rewire followed by validate/save and saved-graph reload proof,
filter parameter edit save/reload with saved-value proof, saved-copy rollback
persistence after connected-block removal rejection, PDU parameter edit,
PDU message-port disconnect preflight rollback, stream-demux parameter edit,
mixed stream/message add-request clarification, same-name duplicate target
rejection across packet and UHD WBFM examples, and state-edit save/reload. Two installed audio
add-connection rollback checks remain opt-in probes because the current model
routes them to safe clarification rather than `apply_edit`.

Latest operational dogfood pass:
`reports/dogfood/DOGFOOD_2026-04-29.md` covers 12 held-out installed examples
and 3 workspace fixture stand-ins. It recorded 15 observations, 13 clean
outcomes, 2 one-off GNU-validation failures before commit, 0 STOP_THE_LINE
events, 0 unsafe mutations, and 0 repeated generic failure clusters.

Second operational dogfood pass:
`reports/dogfood/DOGFOOD_2026-04-29_PASS2.md` records a stopped first attempt
that exposed a preview-contract bug, then a patched rerun with 27 observations
across 23 held-out installed examples and 4 workspace fixture stand-ins. Final
rerun result: 21 clean/safe observations, 5 safe preflight rejections, 1 safe
GNU-validation failure before commit, 0 unresolved STOP_THE_LINE events, 0
preview mutations after the patch, 0 save/reload mismatches, and 0 repeated
generic failure clusters.

Third targeted dogfood pass:
`reports/dogfood/DOGFOOD_2026-04-29_PASS3.md` records 28 boundary-focused
observations across 24 held-out installed examples and 4 workspace/eval
stand-ins. Result: 0 STOP_THE_LINE events, 0 preview mutations, 0 apply during
preview-only prompts, 0 save without explicit request, 0 save/reload mismatch,
and 0 repeated generic failure clusters. No additional runtime patch was
justified.

Local-alpha release package:
`reports/RELEASE_READY_LOCAL_ALPHA.md` is the current release-readiness
checklist and `docs/LOCAL_ALPHA_PILOT.md` is the real-user pilot guide. The
current n=3 dashboard is reused for this packaging milestone because only docs
and reports changed after the latest runtime evidence.
`reports/LOCAL_ALPHA_RELEASE_TAG.md` is the local-alpha release marker for
`local-alpha-v0.1.0-20260430`; a Git tag should be created only after the
current dirty release snapshot is committed.

First copied user-graph pilot:
`reports/dogfood/USER_PILOT_2026-04-30.md` records 28 observations across 8
copied user/workspace graphs. Result: 28/28 clean or safe outcomes, 0
STOP_THE_LINE events, 0 preview mutations, 0 apply during preview-only prompts,
0 save without explicit request, 0 invalid graph committed/saved, 0 wrong file
writes, 0 save/reload mismatches, and 0 repeated generic failure clusters. No
runtime patch was justified.

## Safety Rules

- Never edit raw `.grc` YAML directly.
- Use `apply_edit`, `remove_connection`, `rewire_connection`, `insert_block_on_connection`, or `auto_insert_block` for mutations.
- Use `propose_edit` only for explicit preview/dry-run requests.
- Preview-only turns must not expose, require, nudge, or execute `apply_edit`,
  including prompts that say "do not apply" or "without applying".
- Save only when the user asks and only after validation of the latest dirty state.
- Failed edits must not mutate the live graph.
- Clarification choices must come from real executable candidates.
- Manual/tutorial retrieval is read-only explanation support with provenance; it is not mutation authority or runtime recipe material.
- `search_grc` remains deterministic lexical graph search with a finite alias layer for known misses.
- `semantic_search_grc` is read-only vector candidate discovery using local Qdrant + FastEmbed. It never returns transactions, parameter payloads, insert args, save instructions, hidden recipes, or mutation authorization.
- Loaded blocks include a deterministic read-only `block_uid` in graph context
  for duplicate identity evidence. It is not a mutation handle; verified tool
  arguments still decide mutation targets.
- Read-only block/connection candidate resolvers include `state_revision`, so
  clarification selections reject stale graph state instead of mutating the
  wrong duplicate block or endpoint.
- Duplicate block edits may clarify only when `instance_name + block_type`
  safely identifies one target. Same-name same-type duplicates are not mutated
  by UID, order, or first match.
- `remove_connection` may accept endpoint hints, but it resolves them to one
  exact `connection_id` before mutation. Ambiguous endpoint matches clarify.
- Exact rewires use `rewire_connection` or one ordered `apply_edit`
  transaction that removes the old connection and adds the new connection
  atomically. Ambiguous old-edge or new-endpoint hints clarify with
  executable options and `state_revision`; stale selections are rejected, and
  invalid rewires roll back without committing a partial disconnect.
- Live-eval pre-turn setup, where needed, must use public verified tools,
  record the setup calls, and validate the graph before the measured turn.
- Retrieval eval currently covers 290 deterministic cases; vector search has 276 top-k hits versus 168 lexical hits with 0 exact-ID misses, 0 false-positive failures, and 0 source-type misses. A six-model FastEmbed bakeoff kept `BAAI/bge-small-en-v1.5` as the runtime default.
- Vector v1 is frozen in `reports/retrieval/VECTOR_BASELINE_V1.md`; the no-LLM regression gate is `uv run python -m tests.retrieval_eval.vector_regression`.
- New catalog semantic metadata requires `docs/VECTOR_METADATA_CHANGE_CHECKLIST.md`: at least 3 clustered misses or misses across 2 distinct sources, a stable capability reason that remains true without the failing query, mutation-shaped negative traps, and a rerun retrieval eval. One-off and ambiguous clusters do not qualify.
- The `numpy<2` dependency marker is GNU Radio ABI compatibility debt for the current supported local GNU Radio 3.10.x environment; remove it only after the supported GNU Radio target and deterministic gates pass with NumPy 2.x.

## Roadmap

- Expand Tier 2 semantic checks beyond the canonical fixture and into more installed GNU examples where available.
- Keep persisting Tier 2/3/4/5 `--n-runs 3` results and gate release candidates through the dashboard.
- Expand the typed TurnPlan/executor policy behind `GrcAgent`; do not add a broad graph planner.
- Run Tier 2, Tier 3, Tier 4, and Tier 5 with `--n-runs 3` before release candidates so stochastic 2B behavior is measured explicitly.
- Keep vector retrieval vector-only until eval evidence justifies hybrid sparse search or reranking.
- Improve explanation quality using the read-only manual search path, but keep catalog metadata and `grcc` authoritative for graph edits.
- Continue real-user/user-graph pilot intake with `grc-agent dogfood record/report`; patch only STOP_THE_LINE issues or repeated generic failures across unrelated graphs, not one-off small-model weirdness.

See `docs/BLUEPRINT.md` for the current design contract and patch criteria.
See `docs/VECTOR_RETRIEVAL.md` for the frozen vector v1 operating contract.
