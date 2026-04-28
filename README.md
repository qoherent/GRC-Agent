# GRC Agent

Local GNU Radio Companion `.grc` assistant focused on safe, validated, local-first graph edits.

## Vision

GRC Agent should become a reliable autonomous assistant for GNU Radio Companion graphs. It should inspect the active graph, choose verified tools, mutate only through validated transactions, verify the result, save only when asked, and ask the user naturally when required details are missing or contradictory.

The project optimizes for reliability over cleverness. Autonomy comes from typed state, explicit tools, deterministic validation, and measured evals, not from hidden YAML edits, prompt tricks, or tutorial-derived recipes.

## Status

- One active `.grc` session per agent.
- Model-facing runtime exposes 15 bounded tools for load, inspect, search, describe, explanation-only manual retrieval, edit, validate, exact connection removal, and save.
- All meaningful mutations go through verified tools and validate before commit.
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

## Repo Map

- `src/grc_agent/`: package code for runtime, session, catalog, retrieval, validation, transaction, llama adapter, and CLI.
- `tests/`: deterministic `unittest` regression coverage.
- `tests/data/random_bit_generator.grc`: canonical fixture graph.
- `tests/llama_eval/`: live llama.cpp routing and behavior evals.
- `docs/BLUEPRINT.md`: current architecture, safety contract, status, and roadmap.
- `docs/QUICKSTART.md`: setup and common usage.
- `docs/wiki_gnuradio_org/`: local GNU Radio tutorial/reference corpus for explanation-only retrieval and evals.

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

## Verification

Deterministic gates:

```bash
uv run ruff check src/ tests/
uv run python -m unittest
uv run grc-agent fake tests/data/random_bit_generator.grc
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
```

Release stability dashboard over persisted repeated live runs:

```bash
uv run python -m tests.llama_eval.release_dashboard \
  --results-path /tmp/grc-agent-live-runs.json \
  --min-runs-per-case 3
```

Latest persisted release-candidate dashboard for Tier 2, Tier 3, and Tier 4 with `--n-runs 3` passed 147/147 model attempts with `release_ready=true`, including installed-example edit/validate, edit/validate/save, and non-variable block-parameter edit cases.

## Safety Rules

- Never edit raw `.grc` YAML directly.
- Use `apply_edit`, `remove_connection`, `insert_block_on_connection`, or `auto_insert_block` for mutations.
- Use `propose_edit` only for explicit preview/dry-run requests.
- Save only when the user asks and only after validation of the latest dirty state.
- Failed edits must not mutate the live graph.
- Clarification choices must come from real executable candidates.
- Manual/tutorial retrieval is read-only explanation support with provenance; it is not mutation authority or runtime recipe material.

## Roadmap

- Expand Tier 2 semantic checks beyond the canonical fixture and into installed GNU examples where available.
- Move toward a typed turn-state/executor policy behind `GrcAgent`; do not add a broad graph planner.
- Run Tier 2, Tier 3, and Tier 4 with `--n-runs 3` before release candidates so stochastic 2B behavior is measured explicitly.
- Improve explanation quality using the read-only manual search path, but keep catalog metadata and `grcc` authoritative for graph edits.

See `docs/BLUEPRINT.md` for the current design contract and patch criteria.
