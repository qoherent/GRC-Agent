# GRC Agent

Local GNU Radio Companion `.grc` assistant focused on safe, validated, local-first graph edits.

## Status

- One active `.grc` session per agent.
- Model-facing runtime exposes 13 bounded tools for load, inspect, search, describe, edit, validate, and save.
- All graph mutations go through verified tools and validate before commit.
- Raw `.grc` YAML editing is refused.
- `grcc` remains final graph-validity authority.
- Default local backend is `unsloth/gemma-4-E2B-it-GGUF` through llama.cpp.
- Current accepted baseline: Ruff clean, unittest green, Tier 1 live 15/15, Tier 2 live 35/36 with 0 STOP_THE_LINE.

## Repo Map

- `src/grc_agent/`: package code for runtime, session, catalog, retrieval, validation, transaction, llama adapter, and CLI.
- `tests/`: deterministic `unittest` regression coverage.
- `tests/data/random_bit_generator.grc`: canonical fixture graph.
- `tests/llama_eval/`: live llama.cpp eval suite.
- `docs/BLUEPRINT.md`: current architecture, safety contract, status, and backlog.
- `docs/QUICKSTART.md`: setup and common usage.
- `docs/wiki_gnuradio_org/`: local GNU Radio tutorial/reference corpus for future docs/retrieval/evals.

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

## Verification

```bash
uv run ruff check src/ tests/
uv run python -m unittest
uv run grc-agent fake tests/data/random_bit_generator.grc
```

Live model checks:

```bash
uv run python -m tests.llama_eval.tier1_live --quick
uv run python -m tests.llama_eval.tier2_release
```

## Safety Rules

- Never edit raw `.grc` YAML directly.
- Use `apply_edit`, `insert_block_on_connection`, or `auto_insert_block` for mutations.
- Use `propose_edit` only for explicit preview/dry-run requests.
- Save only after validation of the latest dirty state.
- Failed edits must not mutate the live graph.
- Tutorial material is not runtime recipe material.

See `docs/BLUEPRINT.md` for the current design contract and patch criteria.
