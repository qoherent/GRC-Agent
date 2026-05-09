# GRC Agent

Local GNU Radio Companion `.grc` assistant focused on safe, validated, local-first graph edits.

## Vision

GRC Agent is a reliable local assistant for GNU Radio Companion graphs. It inspects the active graph, uses verified tools, mutates only through validated transactions, verifies results, saves only when asked, and asks for clarification when required details are missing.

The project optimizes for reliability over cleverness. Autonomy comes from typed state, explicit tools, deterministic validation, and measured evals, not from hidden YAML edits, prompt tricks, or tutorial-derived recipes.

## Status

- Production-candidate under frozen local scope for bounded workflows on copied graphs.
- One active `.grc` session per agent.
- Default model-facing runtime surface is the MVP wrapper profile:
  `inspect_graph`, `search_blocks`, `ask_grc_docs`, `change_graph`,
  `save_graph_explicit`, `load_graph_explicit`.
- Legacy low-level tools remain internal/compatibility-only and are not part of
  the default model-facing chat path.
- Save/load are model-facing only through explicit lifecycle wrappers:
  `save_graph_explicit` and `load_graph_explicit`.
- Lifecycle wrappers require explicit user intent and are currently
  beta-validated by R5 save/load evals; not release-validated.
- All meaningful mutations go through verified tools and validate before commit.
- Classified turns use typed tool narrowing before llama.cpp sees schemas, so clear requests expose only relevant wrapper actions.
- Vague or under-specified mutation requests clarify before execution.
- TurnPlan Advisor remains shadow-only. It does not affect default runtime
  routing or mutation authority.
- Raw `.grc` YAML editing, undo/redo, and Python export/code-generation requests are refused.
- `ask_grc_docs` is explanation-only: it retrieves local manual/tutorial snippets and
  returns concise grounded answers with sources when evidence is strong. The
  production-candidate default uses deterministic grounded extraction (including catalog-assisted
  block definitions when allowed) and reports `insufficient_evidence` when local
  evidence is weak. DocsAnswerAdvisor synthesis is optional research-only and is
  not part of the critical runtime path.
- `grcc` remains final graph-validity authority.
- Default local backend is `unsloth/gemma-4-E2B-it-GGUF` through llama.cpp.
- Current deterministic safety coverage is strong; live evals are routing/behavior evidence, not proof of production autonomy.

## Context And Budget Policy

- Target context window is `120000` tokens when the local llama.cpp model/server supports it.
- Actual context can be checked with:
  - `uv run grc-agent doctor --start-llama --json`
  - `uv run grc-agent health`
- Compression is not done by starving `max_tokens`; low `max_tokens` only caps generation and can truncate.
- Compactness comes from bounded wrapper outputs, retrieval selection, snippet limits, and concise answer schemas.

## Reliability Truth

The product is production-candidate quality for the frozen local scope.

- Deterministic tests cover schema rejection, raw-YAML refusal, rollback, save gating, atomic save, insert safety, clarification handling, turn-guard behavior, and typed recovery classification.
- Live evals check whether the local model routes representative prompts through the right tools and reaches selected semantic/end states.
- Live evals report routing pass, argument pass, tool success pass, semantic/end-state pass, safety pass, and recovery pass separately.
- A task is not considered reliable just because the expected tool name appeared. Correct arguments, graph diff, validation, saved file, and user-facing behavior matter.
- Route mismatches fail closed. For example, if a disable request is interpreted as block removal, execution is blocked before mutation rather than silently repaired.
- Deterministic adversarial TurnPlan coverage now exercises 100+ prompts, and live evals can run the Tier 5 adversarial intent/safety suite.

## Advisor-First Intent Boundary

Do not solve intent routing with regexes, phrase dictionaries, or hardcoded
natural-language branches. Intent routing belongs to the local Advisor. Runtime
code may validate enum/schema shape, map advisor mode to a tool class, enforce
allowed tools, validate operation schemas, run preflight/`grcc`, roll back
failed edits, and enforce save state. Runtime code must not duplicate semantic
intent logic with phrase lists such as preview wording, raw-YAML wording,
vague-topology wording, or block-UID wording.

Advisor remains shadow-only and is not used for default runtime routing.

## Repo Map

- `src/grc_agent/`: package code for runtime, session, catalog, retrieval, validation, transaction, llama adapter, and CLI.
- `tests/`: deterministic `unittest` regression coverage.
- `tests/data/random_bit_generator.grc`: canonical fixture graph.
- `tests/llama_eval/`: live llama.cpp routing and behavior evals.
- `docs/BLUEPRINT.md`: single source of truth for architecture, package flow, harness loop, tool surface, safety contract, retrieval, eval gates, status, and roadmap.
- `docs/BLIND_HARNESS_AUDIT.md`: prompt for an independent online architecture reviewer.
- `docs/ONLINE_REVIEW_EVIDENCE_PACKET.md`: ready-to-attach local evidence summary for reviewers without full repo access.
- `docs/QUICKSTART.md`: setup and common usage.
- `docs/wiki_gnuradio_org/`: local GNU Radio tutorial/reference corpus for explanation-only retrieval and evals.
- `tests/data/retrieval/vector_eval_governed_metadata.json`: frozen vector regression baseline artifact.

## Install

```bash
uv sync --locked
uv run grc-agent doctor
```

Prerequisites:

- Python >= 3.12
- GNU Radio 3.10.x with `grcc` on `PATH`
- llama.cpp `llama-server` on `PATH` for model-backed chat

The CLI can auto-start a configured local llama.cpp server for normal `chat` use when `llama-server` is installed. `doctor` is passive by default; use `uv run grc-agent doctor --start-llama` when you explicitly want it to start or reuse llama.cpp during environment checks.

`uv sync --locked` installs Python dependencies including Graphify-backed lexical search and Qdrant/FastEmbed vector search. It does not install GNU Radio or llama.cpp. `uv run grc-agent vector build` explicitly builds the local vector index and may download the FastEmbed model on first run.

## Usage

Open an existing graph:

```bash
uv run grc-agent chat tests/data/random_bit_generator.grc
```

Copied-graph rule: strongly prefer testing on copied graphs only.

```bash
cp /path/to/original.grc /tmp/grc-agent-test.grc
uv run grc-agent chat /tmp/grc-agent-test.grc
```

Recommended production-candidate sequence:
1. `uv run grc-agent doctor`
2. `uv run grc-agent health`
3. inspect with `inspect_graph`
4. search with `search_blocks` / `ask_grc_docs`
5. preview via `change_graph` dry-run behavior
6. apply via `change_graph` committed behavior
7. validate
8. review history/checkpoints
9. use `history` restore to explicit copy path if needed

Safe first prompts for manual testing:
- `Summarize this graph.`
- `Validate this graph.`
- `Find a low-pass filter block.`
- `Ask docs: what are stream tags?`
- `Preview changing samp_rate to 48000. Do not apply.`
- `Change samp_rate to 48000 and validate.`
- `Show history/checkpoints.`

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

List and restore local graph checkpoints without exposing undo/restore to the
model:

```bash
uv run grc-agent history list
uv run grc-agent history show <id>
uv run grc-agent history diff <id1> <id2>
uv run grc-agent history restore <id> --to /tmp/restored_copy.grc
```

History is local-only under `.grc_agent/history/`. A baseline checkpoint is
created when a graph loads, accepted versions are recorded after verified
mutations and explicit saves, previews do not commit checkpoints, and restore
refuses to overwrite existing files.

## Beta Smoke (Local-Only)

Single local smoke command (no live model required):

```bash
uv run grc-agent doctor \
  && uv run grc-agent health \
  && uv run grc-agent fake tests/data/random_bit_generator.grc \
  && uv run python -m tests.retrieval_eval.vector_regression \
  && uv run python -m unittest tests.test_mvp_tool_profile tests.test_mvp_wrapper_dispatch tests.test_history_journal
```

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

When reporting issues, include prompt, expected behavior, actual behavior,
sanitized copied-graph reference, validation result, and checkpoint result.

## Verification

Fast default gate (normal development):

```bash
uv run ruff check src/ tests/
uv run ruff check
uv run python -m unittest
uv run python -m tests.retrieval_eval.vector_regression
```

Retrieval/vector eval note: run retrieval gates sequentially. Do not run
`vector_regression` and `grc_docs_answer_eval` in parallel while using the same
local index path.

Live quick gate (only when runtime/model-facing behavior changes):

```bash
uv run python -m tests.llama_eval.tier1_live --quick
uv run python -m tests.llama_eval.tier2_release
uv run python -m tests.llama_eval.tier3_multiturn --quick
uv run python -m tests.llama_eval.tier4_external_examples --quick
uv run python -m tests.llama_eval.tier5_adversarial --quick
```

Release gate (release claims/default-routing changes only):

```bash
uv run python -m tests.llama_eval.tier2_release --n-runs 3 --results-path /tmp/tier2_n3.json
uv run python -m tests.llama_eval.tier3_multiturn --n-runs 3 --results-path /tmp/tier3_n3.json
uv run python -m tests.llama_eval.tier4_external_examples --n-runs 3 --results-path /tmp/tier4_n3.json
uv run python -m tests.llama_eval.tier5_adversarial --n-runs 3 --results-path /tmp/tier5_n3.json
uv run python -m tests.llama_eval.release_dashboard \
  --results-path /tmp/tier2_n3.json \
  --results-path /tmp/tier3_n3.json \
  --results-path /tmp/tier4_n3.json \
  --results-path /tmp/tier5_n3.json \
  --min-runs-per-case 3
```

Advisor/model bakeoff scripts are research-only and are not part of default verification.

Operational reports are generated locally during eval/dogfood runs and are not required to be tracked in git for normal development.

## Safety Rules

- Never edit raw `.grc` YAML directly.
- Default model-facing mutation entrypoint is `change_graph` (wrapper). Internal verified handlers remain safety boundaries.
- Preview-only turns must not expose, require, nudge, or execute `apply_edit`,
  including prompts that say "do not apply" or "without applying".
- Save only when the user asks and only after validation of the latest dirty state.
- Failed edits must not mutate the live graph.
- Clarification choices must come from real executable candidates.
- Manual/tutorial retrieval is read-only explanation support with provenance; it is not mutation authority or runtime recipe material.
- `ask_grc_docs` remains explanation-only and never authorizes graph mutation.
- `search_blocks` and `ask_grc_docs` are the default model-facing retrieval wrappers. Internal lexical/vector/manual handlers remain read-only.
- Loaded blocks include a deterministic `block_uid` in graph context for
  duplicate identity evidence. Free-form UID mutation remains rejected; the
  only supported UID path is a checked `target_ref` object for block-local
  parameter/state edits after current graph identity and `state_revision`
  validation.
- Read-only block/connection candidate resolvers include `state_revision`, so
  clarification selections reject stale graph state instead of mutating the
  wrong duplicate block or endpoint.
- Duplicate block edits may clarify when `instance_name + block_type` safely
  identifies one target, or for same-name same-type param/state edits when
  clarification produces a verified `target_ref`. The agent still never picks
  by order or first match.
- Users should choose the clarification option shown by the agent; they should
  not type raw `block_uid` mutation commands.
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
- Vector retrieval baseline evidence is frozen in `tests/data/retrieval/vector_eval_governed_metadata.json`; the no-LLM regression gate is `uv run python -m tests.retrieval_eval.vector_regression`.
- Any future catalog semantic metadata change must be evidence-backed and must rerun retrieval regression before acceptance.
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
See `docs/BLUEPRINT.md` for the active retrieval operating contract.
