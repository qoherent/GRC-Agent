# GRC Agent

Local GNU Radio Companion `.grc` assistant focused on safe, validated, local-first graph edits.

## Vision

GRC Agent is a reliable local assistant for GNU Radio Companion graphs. It inspects the active graph, uses verified tools, mutates only through validated transactions, verifies results, autosaves successful mutations, and asks for clarification when required details are missing.

The project optimizes for reliability over cleverness. Autonomy comes from typed state, explicit tools, deterministic validation, and measured evals, not from hidden YAML edits, prompt tricks, or tutorial-derived recipes.

## Status

- Scoped release evidence exists for `R0_READ_ONLY` and `R1_SET_PARAM_ONLY`.
  Broader graph mutation capabilities are beta-validated. The runtime is not
  production-ready.
- One active `.grc` session per agent.
- Default model-facing runtime surface is the MVP wrapper profile:
  `inspect_graph`, `search_blocks`, `ask_grc_docs`, `change_graph`.
- The read-only wrappers are intentionally compact: `inspect_graph` overview is
  topology-only, targeted details expose guarded target refs and selected
  parameters, `search_blocks` returns concise catalog candidates, and
  `ask_grc_docs` returns cited explanation evidence.
- Low-level tools remain internal implementation primitives and are not
  model-facing.
- The model cannot save or load directly. `/save` is a CLI command; graph loading happens when the CLI session starts.
- All meaningful mutations go through verified tools. Valid edits validate
  before commit and autosave when the active copied graph path is safe and
  writable. Invalid intermediate commits require explicit `force=true` and
  still must pass schema, graph-reference, catalog, preflight, apply, and save
  checks.
- Vague or under-specified mutation requests clarify before execution.
- Runtime reminders must not force a mutation after the model asks a
  graph-evidence-backed clarification; ambiguous edits must leave the graph
  unchanged.
- Raw `.grc` YAML editing, undo/redo, and Python export/code-generation requests are refused.
- `ask_grc_docs` is explanation-only: it retrieves local manual/tutorial snippets and
  returns concise grounded answers with sources when evidence is strong. The
  default path uses deterministic grounded extraction (including catalog-assisted
  block definitions when allowed) and reports `insufficient_evidence` when local
  evidence is weak. DocsAnswerAdvisor synthesis is optional research-only and is
  not part of the critical runtime path.
- `grcc` remains final graph-validity authority.
- Default local backend in this workspace is `Qwen3.5-9B-UD-Q4_K_XL.gguf`.
  through a local text-only GGUF path and llama.cpp.
- Current deterministic safety coverage is strong; live evals are routing/behavior evidence, not proof of production autonomy.

## Context And Budget Policy

- Target context window is `120000` tokens when the local llama.cpp model/server supports it.
- Actual context can be checked with:
  - `uv run grc-agent doctor --start-llama --json`
  - `uv run grc-agent health`
- Compression is not done by starving `max_tokens`; low `max_tokens` only caps generation and can truncate.
- Compactness comes from bounded wrapper outputs, retrieval selection, snippet limits, and concise answer schemas.

## Reliability Truth

The validated subset is intentionally scoped: `R0_READ_ONLY` and
`R1_SET_PARAM_ONLY` are release-validated, broader graph mutations are
beta-validated, and the runtime is not production-ready.

- Deterministic tests cover schema rejection, raw-YAML refusal, rollback, autosave/manual-save behavior, insert safety, clarification handling, route validation, and typed recovery classification.
- Live evals check whether the local model routes representative prompts through the right tools and reaches selected semantic/end states.
- Live evals report routing pass, argument pass, tool success pass, semantic/end-state pass, safety pass, and recovery pass separately.
- A task is not considered reliable just because the expected tool name appeared. Correct arguments, graph diff, validation, saved file, and user-facing behavior matter.
- Route mismatches fail closed. For example, if a disable request is interpreted as block removal, execution is blocked before mutation rather than silently repaired.
- Live evals can run the Tier 5 adversarial intent/safety suite. Do not solve routing failures with regexes, phrase dictionaries, prompt folklore, or fixture-specific shortcuts; fix the authoritative data path, wrapper contract, validation, or context budget.

## Repo Map

- `src/grc_agent/`: package code for runtime, ToolAgents integration, session, catalog, retrieval, validation, transaction, llama probing/launching, and CLI.
- `tests/`: deterministic `unittest` regression coverage.
- `tests/data/random_bit_generator.grc`: canonical fixture graph.
- `tests/llama_eval/`: live llama.cpp routing and behavior evals.
- `docs/BLUEPRINT.md`: concise source of truth for architecture, wrappers, harness loop, context handling, safety contract, eval harness, status, and roadmap.
- `docs/QUICKSTART.md`: setup and common usage.
- `docs/ISSUE_INTAKE.md`: issue report template and debug-bundle guidance.
- `docs/DEMO_VIDEO.md`: reproducible demo workflow.
- `docs/HANDOFF.md`: current handoff for the next implementation/audit agent.
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
- CUDA-enabled llama.cpp `llama-server` on `PATH` for model-backed chat on NVIDIA machines

The CLI can auto-start a configured local llama.cpp server for normal `chat` use when `llama-server` is installed. The configured default is explicit CUDA device `CUDA0` with `gpu_layers=999`; `llama-server --list-devices` must show `CUDA0`. The Qwen 3.5 9B GGUF is loaded directly via `model_path`. `doctor` is passive by default; use `uv run grc-agent doctor --start-llama` when you explicitly want it to start or reuse llama.cpp during environment checks.

`uv sync --locked` installs Python dependencies including ToolAgents and Qdrant/FastEmbed vector search. It does not install GNU Radio, llama.cpp, chat models, or embedding model files. `uv run grc-agent vector build` explicitly builds the local vector index and may download the FastEmbed embedding model into the user's local cache on first run.

Packaging policy:

- `pyproject.toml` is the primary install contract for Python dependencies.
- Docker is not required for the default local install path. A container/devcontainer can be added later for reproducible development, but it should not become the normal user path unless GNU Radio packaging forces it.
- Model files are runtime assets, not repo/package assets. Do not commit or bundle GGUF files, FastEmbed/Hugging Face caches, `.grc_agent/vector_index/`, or generated Qdrant state.
- The current vector default is `thenlper/gte-base` through FastEmbed. The local cached quantized ONNX package is about 400 MB on disk.

GNU Radio is normally installed outside this Python package. A plain uv virtual
environment may not automatically see distro-installed GNU Radio Python
bindings, even when `grcc` is on `PATH`.

Recommended local Linux profile when GNU Radio is installed by the OS package
manager:

```bash
rm -rf .venv
uv venv --system-site-packages --python /usr/bin/python3
uv sync --locked --python .venv/bin/python
uv run grc-agent doctor
```

This keeps GRC Agent dependencies locked by uv while allowing the venv to import
the system GNU Radio Python bindings. Verify the full environment with:

```bash
uv run python -m tests.production.install_smoke \
  --mode system-site-venv \
  --output /tmp/grc_agent_install_smoke_system_site.json
```

Package smoke does not require a prebuilt vector index. Use
`--require-vector-index` for runtime-readiness smoke, or `--build-vector-index`
when you explicitly want the smoke to build local Qdrant/FastEmbed state first.
Use `--require-llama` when the smoke must fail unless `grc-agent health`
reports a reachable llama.cpp server with verified actual context.

`PYTHONPATH` bridges are supported only as a last-resort local workaround when
the ABI and Python version match. Container/devcontainer and conda/mamba
profiles remain future packaging work.

## Usage

Open an existing graph:

```bash
uv run grc-agent chat tests/data/random_bit_generator.grc
```

For exploratory local turns where the model may need multiple tool steps before
answering:

```bash
uv run grc-agent chat --agentic tests/data/random_bit_generator.grc
```

`--agentic` only raises the bounded tool-round budget and request timeout. It
does not expose internal tools, lifecycle tools, or bypass validation. Use
`--max-tool-rounds N` for an explicit per-session limit.

Copied-graph rule: strongly prefer testing on copied graphs only.

```bash
cp /path/to/original.grc /tmp/grc-agent-test.grc
uv run grc-agent chat /tmp/grc-agent-test.grc
```

### GUI Sidekick Panel

To launch the PySide6 Desktop GUI sidekick panel to inspect and mutate GRC flowgraphs:

```bash
uv run grc-agent-gui
```

The GUI runs as a sidekick panel alongside the native GNU Radio Companion editor. It displays the chat interface, a real-time variables table, a blocks tree, and a connections list. It also includes "Compile & Run" and "Stop" controls with a real-time log console, and uses a Deferred Close sequence to prevent SDR hardware locks on application exit.

Recommended local validation sequence:
1. `uv run grc-agent doctor`
2. `uv run grc-agent health`
3. open chat on a copied graph
4. inspect naturally, then ask for targeted block details when needed
5. search with `search_blocks` / `ask_grc_docs` for read-only discovery or explanation
6. preview or apply edits through `change_graph`
7. let load, mutation, autosave, and manual-save paths run validation automatically
8. use `/save [path] [--overwrite]` for explicit manual save
9. use `/history` or `grc-agent history ...` only for debug/checkpoint review

Safe first prompts for manual testing:
- `Summarize this graph.`
- `What are the variables and connections in this graph?`
- `Show the parameters for analog_sig_source_x_0.`
- `Find a low-pass filter block.`
- `Ask docs: what are stream tags?`
- `Preview changing samp_rate to 48000. Do not apply.`
- `Change samp_rate to 48000 and validate.`
- `Change the signal source frequency from 440 to 10k.`

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

`vector build` indexes installed GNU Radio block metadata plus the local docs
corpus. The default embedding model is downloaded by FastEmbed when missing.
Use `--embedding-model <fastembed-model-name>` only when intentionally
rebuilding and querying with a different local embedding model. `vector search`
defaults to the embedding model recorded in the active vector index manifest.

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
mutations and manual saves, previews do not commit checkpoints, and restore
refuses to overwrite existing files.

## Beta Smoke (Local-Only)

Single local smoke command without llama.cpp:

```bash
uv run grc-agent doctor \
  && uv run grc-agent fake tests/data/random_bit_generator.grc \
  && uv run python -m unittest tests.test_mvp_tool_profile tests.test_toolagents_runtime tests.test_history_journal
```

Model-backed readiness smoke:

```bash
uv run grc-agent doctor --start-llama \
  && uv run grc-agent health
```

Retrieval regression is a separate slower gate:

```bash
uv run python -m tests.retrieval_eval.vector_regression
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
Use `docs/ISSUE_INTAKE.md` for the current report template and attachment
guidance.

Generate a redacted support bundle:

```bash
uv run grc-agent debug-bundle --output /tmp/grc_agent_debug_bundle.json
```

The bundle records doctor, health, release-manifest, tool-surface, hash, vector,
and artifact hygiene summaries. It does not include `.env` contents, API keys,
raw prompt history, or raw graph contents.

## Verification

Targeted gate (normal development):

```bash
uv run ruff check src/ tests/
uv run python -m unittest tests.test_toolagents_runtime tests.test_mvp_tool_profile
uv run python -m tests.retrieval_eval.vector_regression
```

Reserve full `uv run python -m unittest` for release-candidate gates or broad runtime sweeps.

Retrieval/vector eval note: run retrieval gates sequentially. Do not run
`vector_regression` and `grc_docs_answer_eval` in parallel while using the same
local index path.

Live quick gate (only when runtime/model-facing behavior changes):

```bash
uv run python -m tests.llama_eval.run_r0_release --n-runs 1 --results-path /tmp/r0.json
uv run python -m tests.llama_eval.run_r1_release --n-runs 1 --results-path /tmp/r1.json
uv run python -m tests.llama_eval.run_r2_release --n-runs 1 --results-path /tmp/r2.json
uv run python -m tests.llama_eval.run_dsp_gauntlet --seed 42 --count 30 --results-path /tmp/gauntlet.json
```

Release/default-routing evidence sweep:

```bash
uv run python -m tests.llama_eval.run_r0_release --n-runs 3 --results-path /tmp/r0_n3.json
uv run python -m tests.llama_eval.run_r1_release --n-runs 3 --results-path /tmp/r1_n3.json
uv run python -m tests.llama_eval.run_r2_release --n-runs 3 --results-path /tmp/r2_n3.json
uv run python -m tests.llama_eval.run_dsp_gauntlet --seed 42 --count 50 --n-runs 3 --results-path /tmp/gauntlet_n3.json
```

All live evals enforce 11 REPORT_DIMENSIONS including `budget_pass` (tool-round/call thresholds) and `lint_pass` (graph-hygiene checks for orphan blocks, unused variables, disabled-with-connections, duplicate names). Budget and lint metrics aggregate across scenarios in the gauntlet summary.

Operational reports are generated locally during eval/dogfood runs and are not required to be tracked in git for normal development.

## Safety Rules

- Never edit raw `.grc` YAML directly.
- Default model-facing mutation entrypoint is `change_graph` (wrapper). Internal verified handlers remain safety boundaries.
- Preview-only turns must not expose, require, nudge, or execute `apply_edit`,
  including prompts that say "do not apply" or "without applying".
- The model cannot save or load. Successful committed mutations autosave after validation when the copied graph path is safe and writable; `/save` remains the explicit manual save command.
- Failed edits must not mutate the live graph.
- Clarification choices must come from real executable candidates.
- Manual/tutorial retrieval is read-only explanation support with provenance; it is not mutation authority or runtime recipe material.
- `ask_grc_docs` remains explanation-only and never authorizes graph mutation.
- `search_blocks` and `ask_grc_docs` are the default model-facing retrieval wrappers. `search_blocks` uses exact/catalog metadata lookup, cached in-memory SQLite FTS5 sparse ranking, and vector retrieval when the local index is available; docs/manual retrieval remains read-only explanation support. Their model-visible outputs should stay concise and evidence-only.
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
- Retrieval eval currently covers 290 deterministic cases; vector search has 276 top-k hits with 0 exact-ID misses, 0 false-positive failures, and 0 source-type misses. A six-model FastEmbed bakeoff kept `thenlper/gte-base` as the runtime default.
- Block catalog search uses exact/catalog lexical metadata lookup, cached in-memory SQLite FTS5 sparse ranking, and vector retrieval when the local index is available, then deterministic merge/rerank. Exact block IDs, parameter IDs, ports, and dtypes must not depend on dense embeddings alone.
- Vector retrieval baseline evidence is frozen in `tests/data/retrieval/vector_eval_governed_metadata.json`; the no-LLM regression gate is `uv run python -m tests.retrieval_eval.vector_regression`.
- Any future catalog semantic metadata change must be evidence-backed and must rerun retrieval regression before acceptance.
- The `numpy<2` dependency marker is GNU Radio ABI compatibility debt for the current supported local GNU Radio 3.10.x environment; remove it only after the supported GNU Radio target and deterministic gates pass with NumPy 2.x.

## Roadmap

- Expand Tier 2 semantic checks beyond the canonical fixture and into more installed GNU examples where available.
- Keep persisting Tier 2/3/4/5 `--n-runs 3` results and gate release candidates through the dashboard.
- Keep the four-wrapper model surface small; keep `change_graph` as a compact `op + args` envelope instead of adding broad planners or new chat tools.
- Formalize budget and lint pass criteria in release gates. Establish P95 token-budget baselines from gauntlet runs and tighten threshold defaults.
- Expand the DSP fuzzing gauntlet with additional generator categories (AGC, filter cascade, channel model) and increase seed coverage.
- Run Tier 2, Tier 3, Tier 4, and Tier 5 with `--n-runs 3` before release candidates so stochastic 2B behavior is measured explicitly.
- Keep the default local retrieval stack lightweight: pyproject-managed dependencies, user-local model/cache downloads, generated local Qdrant state, and no bundled model artifacts.
- Improve explanation quality through the read-only vector docs path, but keep catalog metadata and `grcc` authoritative for graph edits.
- Continue real-user/user-graph pilot intake with `grc-agent dogfood record/report`; patch only STOP_THE_LINE issues or repeated generic failures across unrelated graphs, not one-off small-model weirdness.
- Re-enable `max_prompt_tokens` budget tracking by having `validate_tool_call` explicitly `kwargs.pop("debug", None)` before running its strict schema checks (requires `_maybe_enable_wrapper_eval_telemetry` fix in the runtime).

See `docs/BLUEPRINT.md` for the current design contract and patch criteria.
