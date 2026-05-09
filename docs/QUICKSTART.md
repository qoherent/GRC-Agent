# Quickstart

This guide is for using GRC Agent, not understanding its internals. Work on copied `.grc` files until you trust a workflow.

Default model-facing tools in chat are MVP wrappers only:
- `inspect_graph`
- `search_blocks`
- `ask_grc_docs`
- `change_graph`
- `save_graph_explicit`
- `load_graph_explicit`

Advisor is shadow-only and does not control runtime routing by default.
Lifecycle save/load are explicit wrappers and require explicit user intent.
`change_graph` remains mutation-only.
Lifecycle wrappers are beta-validated via R5 save/load live evals and are not
release-validated yet.

## 1. Install And Check

```bash
uv sync --locked
uv run grc-agent doctor
uv run grc-agent health
```

To verify desired vs actual llama context, run:

```bash
uv run grc-agent doctor --start-llama --json
```

Target policy is `desired_context_tokens=120000` when the local model/server supports it.
Output compactness is controlled by retrieval and schema budgets, not by forcing tiny `max_tokens`.

Local production-candidate smoke (no live-model requirement):

```bash
uv run grc-agent doctor
uv run grc-agent health
uv run grc-agent fake tests/data/random_bit_generator.grc
uv run python -m tests.retrieval_eval.vector_regression
uv run python -m unittest tests.test_mvp_tool_profile tests.test_mvp_wrapper_dispatch tests.test_history_journal
```

Required outside this package:

- Python >= 3.12.
- GNU Radio 3.10.x with `grcc` on `PATH`.
- `llama-server` from llama.cpp on `PATH` for model-backed chat.

Installed by `uv sync --locked`:

- GRC Agent Python package.
- Graphify-backed lexical retrieval dependency (`graphifyy`).
- Local vector retrieval dependency (`qdrant-client[fastembed]`).

Not installed automatically:

- GNU Radio.
- llama.cpp / `llama-server`.
- A prebuilt vector index.

## 2. Open A Graph

Use a copied graph:

```bash
cp /path/to/original.grc /tmp/grc-agent-test.grc
uv run grc-agent chat /tmp/grc-agent-test.grc
```

Do not edit originals in place (for example under `/usr/share/gnuradio/examples`).

Run one prompt and exit:

```bash
uv run grc-agent chat /tmp/work.grc \
  --message "Summarize this graph and validate it."
```

Create a new empty graph:

```bash
uv run grc-agent chat --new
```

New graphs require an explicit save path before they can be saved.

## 3. Safe Prompt Patterns

Inspect:

```text
Summarize this flowgraph.
```

Validate:

```text
Validate the graph.
```

Preview without mutation:

```text
Preview changing samp_rate to 48000. Do not apply it.
```

Apply and validate:

```text
Change samp_rate to 48000 and validate.
```

Save/copy is explicit via `save_graph_explicit`. Load/open is explicit via
`load_graph_explicit`. Both wrappers require clear user intent.
These lifecycle wrappers are model-facing beta-validated capabilities; do not
promote them to release-validated without a separate lifecycle safety audit.

Disconnect an exact connection:

```text
Remove connection connection_3 and validate.
```

Disconnect safety contract:
- Prefer exact `connection_id`.
- Endpoint hints are allowed only when they resolve to one exact connection.
- Ambiguous endpoint hints clarify and do not mutate.
- Stale `connection_id` / stale `state_revision` requests fail closed.
- Preview disconnect never mutates.
- Failed `grcc` disconnect validation never commits.

Rewire an exact edge:

```text
Rewire connection_3 to connect source:0 to sink:0, then validate.
```

Ambiguous requests should clarify:

```text
Rewire this topology so it is cleaner.
```

Unsupported requests should refuse or clarify:

```text
Edit the raw YAML directly.
```

Broad topology repair stays clarification-first:

```text
Fix this topology automatically.
```

## 3.1 Production-Candidate Flow (Copied Graphs Only)

1. Install dependencies.
2. Verify environment with `doctor` and `health`.
3. Copy a `.grc` graph to a writable working path.
4. Start `chat` on the copied graph.
5. Inspect using `inspect_graph`.
6. Search blocks with `search_blocks`.
7. Ask docs/help with `ask_grc_docs`.
`ask_grc_docs` is explanation-only and returns grounded sources when evidence is
strong; production-candidate default uses a deterministic grounded-answer builder and honest
`insufficient_evidence` when local docs are weak. DocsAnswerAdvisor synthesis
is optional/research-only and not required for the frozen runtime path.
8. Preview a change (`change_graph` dry-run path).
9. Apply a change (`change_graph` committed path).
10. Validate the resulting graph.
11. Rely on checkpoint/history for local recovery.
12. Restore to an explicit copy path if needed.

Intent routing note: the experimental Advisor is shadow-only and does not route
default runtime execution. Runtime safety comes from verified tools,
schema/route checks, `grcc`, rollback, preview no-mutation, and explicit save.
Do not rely on hidden regex routing or raw command handles.

## 4. What The Agent Will And Will Not Do

The agent will:

- inspect the active graph
- search installed GNU Radio catalog/session context
- preview supported edits
- apply verified parameter/state edits
- remove exact connections
- perform exact or clarification-backed rewires
- validate with `grcc`
- save only when explicitly requested (model-facing via
  `save_graph_explicit`)

For same-name duplicate blocks, choose the clarification option the agent shows.
Do not type raw `block_uid` mutation commands; UID targeting is guarded
internally and only supports parameter/state edits after current graph identity
checks.

The agent will not:

- edit raw `.grc` YAML directly
- save without explicit request
- mutate during preview
- pick the first ambiguous candidate
- mutate same-name same-type duplicate blocks without clarification
- use free-form `block_uid` text as a mutation handle
- use vector/manual search results as mutation authority
- perform broad "fix this graph" topology planning
- force-save invalid graphs

## 5. Direct Tool Commands

Use direct tools when you do not need a model:

```bash
uv run grc-agent tool summarize_graph --file /tmp/work.grc
uv run grc-agent tool validate_graph --file /tmp/work.grc
```

Run the deterministic fake-model smoke test:

```bash
uv run grc-agent fake tests/data/random_bit_generator.grc
```

Inspect local checkpoints:

```bash
uv run grc-agent history list
uv run grc-agent history show <id>
uv run grc-agent history diff <id1> <id2>
uv run grc-agent history restore <id> --to /tmp/restored_copy.grc
```

Checkpoint restore is CLI-only for now. It always writes to an explicit new
copy path and refuses to overwrite existing files.

Search the bundled GNU Radio manual/reference corpus:

```bash
uv run grc-agent manual search "stream tags" --k 3 --json
```

## 5.1 Report Issues During Beta

Use evidence intake when behavior does not match expectation:

```bash
uv run grc-agent dogfood record "your prompt here" \
  --source real_user \
  --task-type other \
  --failure-category other \
  --json
```

Include prompt, expected behavior, actual behavior, sanitized copied-graph
reference, validation result, checkpoint result, and short notes.

## 6. llama.cpp Setup

The package does not install llama.cpp. Install or build llama.cpp separately so `llama-server` is on `PATH`.

Default config in `grc_agent.toml`:

```toml
[llama]
server_url = "http://127.0.0.1:8080"
model = "unsloth/gemma-4-E2B-it-GGUF"
hf_model = "unsloth/gemma-4-E2B-it-GGUF:UD-Q4_K_XL"
```

Normal `chat` can start or reuse a local llama.cpp server if `llama-server` is available:

```bash
uv run grc-agent chat /tmp/work.grc --message "Summarize this graph."
```

To include llama startup in environment checks:

```bash
uv run grc-agent doctor --start-llama
```

Manual equivalent:

```bash
llama-server \
  -hf unsloth/gemma-4-E2B-it-GGUF:UD-Q4_K_XL \
  --alias unsloth/gemma-4-E2B-it-GGUF \
  --host 127.0.0.1 \
  --port 8080 \
  --jinja \
  --no-mmproj
```

If your llama.cpp build supports `-hf`, first launch may download the configured model. GRC Agent itself does not manage llama.cpp installation or model files.

## 7. Vector Search Setup

Vector search is optional, local, and read-only. It does not authorize mutations.

Build the index:

```bash
uv run grc-agent vector build
```

First build may download the FastEmbed model `BAAI/bge-small-en-v1.5`. The index is stored under:

```text
.grc_agent/vector_index/qdrant
```

Inspect and search:

```bash
uv run grc-agent vector stats --json
uv run grc-agent vector search "audio smoother" --scope catalog --k 5 --json
```

If the index is missing, semantic search returns a structured `missing_index` error. Chat does not auto-build the vector index.

Record retrieval misses as evidence only:

```bash
uv run grc-agent vector miss "leveler block" \
  --expected-block analog_agc_xx \
  --actual-top-id blocks_xor_xx \
  --category ambiguous_wording \
  --source real_user \
  --json

uv run grc-agent vector misses --json
uv run grc-agent vector proposals --json
```

`vector proposals` does not modify metadata or rebuild indexes.

## 8. Dogfood Issue Intake

Record real-use observations without changing runtime behavior:

```bash
uv run grc-agent dogfood record \
  "Preview changing samp_rate to 48000. Do not apply." \
  --graph /tmp/work.grc \
  --source real_user \
  --task-type preview \
  --failure-category no_failure \
  --json

uv run grc-agent dogfood report --json
```

Patch policy: fix STOP_THE_LINE safety issues immediately; patch normal failures only after repeated generic evidence across unrelated graphs.

## 9. Troubleshooting

`grcc` not found:

```bash
which grcc
```

Install GNU Radio and ensure the same shell can run `grcc`.

`llama-server binary not found on PATH`:

Install/build llama.cpp and put `llama-server` on `PATH`, or start your own server and pass `--llama-server-url`.

Vector index missing:

```bash
uv run grc-agent vector build
```

GNU Radio / NumPy mismatch:

The package pins `numpy<2` for the supported GNU Radio 3.10.x Python ABI. Do not remove this pin unless deterministic gates pass with your GNU Radio target.

## 10. Developer Verification

```bash
uv run ruff check src/ tests/
uv run ruff check
uv run python -m unittest
uv run python -m tests.retrieval_eval.vector_regression
```

Run retrieval/vector gates sequentially. Do not run
`tests.retrieval_eval.vector_regression` and
`tests.retrieval_eval.grc_docs_answer_eval` in parallel against the same local
index path.

Live model quick gates:

```bash
uv run python -m tests.llama_eval.tier1_live --quick
uv run python -m tests.llama_eval.tier2_release
uv run python -m tests.llama_eval.tier3_multiturn --quick
uv run python -m tests.llama_eval.tier4_external_examples --quick
uv run python -m tests.llama_eval.tier5_adversarial --quick
```

## 11. Local Beta Smoke (No Live Model Required)

```bash
uv run grc-agent doctor \
  && uv run grc-agent health \
  && uv run grc-agent fake tests/data/random_bit_generator.grc \
  && uv run python -m tests.retrieval_eval.vector_regression \
  && uv run python -m unittest tests.test_mvp_tool_profile tests.test_history_journal
```
