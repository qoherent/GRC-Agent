# Phase 17 Production Readiness: Package, Install, And Ops Audit

Phase 17 audits install, configuration, diagnostics, artifact hygiene, and issue
intake. It does not change graph mutation runtime behavior, tool schemas, helper
LLM defaults, or capability classification.

Runtime classification remains unchanged:

- Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`
- Beta-validated: `R1_SET_STATE`, `R2`, `R3`, `R4A`, `R4B`, `R4C`, `R5`
- Diagnostic-clean: `R7_EXACT_EXTERNAL`, `R7_NATURAL_EXTERNAL`, `Tier5_ADVERSARIAL`
- Docs QA: threshold-met deterministic baseline
- Runtime: not production-ready

## External Grounding

- uv docs: `uv sync` synchronizes a project environment from the lockfile, and
  `uv run` executes commands inside that project environment. Sources:
  <https://github.com/astral-sh/uv/blob/main/docs/concepts/projects/sync.md>,
  <https://github.com/astral-sh/uv/blob/main/docs/concepts/projects/run.md>.
- Ollama API docs: local API base is `http://localhost:11434/api`, Cloud API base
  is `https://ollama.com/api`, Cloud auth uses `OLLAMA_API_KEY`, `/api/tags`
  lists models, `/api/version` reports version, and OpenAI compatibility is
  documented as a partial compatibility surface. Sources:
  <https://docs.ollama.com/api/introduction>,
  <https://docs.ollama.com/api/authentication>,
  <https://docs.ollama.com/api/tags>,
  <https://docs.ollama.com/api/openai-compatibility>.
- FastEmbed/Qdrant docs: `qdrant-client[fastembed]` is the documented Python
  integration path for automatic local embedding generation with Qdrant.
  Sources: <https://github.com/qdrant/fastembed/blob/main/README.md>,
  <https://github.com/qdrant/fastembed/blob/main/docs/index.md>.

These sources were used only for package/ops expectations. They are not mutation
authority.

## Installation Path Audit

| Area | Current status | Evidence | Risk |
| --- | --- | --- | --- |
| Python package install | Automated by `uv sync --locked` | `pyproject.toml` declares package metadata, dependencies, and `grc-agent = grc_agent.cli:main` | Works for Python deps only; not enough for GNU Radio bindings |
| CLI entry point | Present | `uv run grc-agent --help` exposes `doctor`, `health`, `release-manifest`, `fake`, `chat`, `tool`, `manual`, `vector`, `dogfood`, `history` | CLI is available after sync |
| Python dependencies | Declared | `graphifyy`, `networkx`, `pyyaml`, `qdrant-client[fastembed]`, `numpy<2` for current GNU Radio ABI | Python 3.13 and NumPy 2 migration remain future compatibility work |
| GNU Radio / `grcc` | Manual prerequisite | README and Quickstart require GNU Radio 3.10.x and `grcc` on `PATH`; local host has `/usr/bin/grcc` and GNU Radio `3.10.9.2` | Fresh uv venv may not see system GNU Radio Python bindings |
| Qdrant/FastEmbed | Python dependency installed; index build is manual | README documents `uv run grc-agent vector build`; local vector stats report 1605 records, BAAI/bge-small-en-v1.5, 384 dimensions | `doctor`/`health` do not prove vector index presence; `vector stats` does |
| llama.cpp backend | Manual prerequisite for chat | README requires `llama-server`; local binary exists at `/home/linuxbrew/.linuxbrew/bin/llama-server` | Server is currently unreachable, so `health` correctly reports `not_ready` |
| Ollama | Harness-only, not default GRC Agent runtime | Production gameplay docs use explicit Ollama readiness checks; core README focuses on llama.cpp | Not a core install blocker unless running dummy-user gameplay |

What is automated:

- Project dependency installation through `uv sync --locked`.
- Console script availability through `pyproject.toml`.
- Local lexical/manual/vector Python dependencies.
- Deterministic tests and CI fast checks.

What is manual:

- Installing GNU Radio and compatible Python bindings.
- Ensuring `grcc` is on `PATH`.
- Installing/configuring llama.cpp and model files.
- Starting or allowing explicit startup of llama.cpp.
- Building the vector index with `uv run grc-agent vector build`.

Expected failure modes:

- Without GNU Radio Python bindings: `doctor` fails `GNU Radio import/version`.
- Without `grcc`: `doctor` fails `grcc on PATH`; validation-backed mutation is not operational.
- Without llama.cpp server: `health` returns nonzero with `status=not_ready` and `llama_unreachable`.
- Without vector index: `grc-agent vector stats --index-dir <missing> --json` returns `ok=false`, `error_type=missing_index`.

## Fresh Environment Simulation

Command set run from a clean `git archive` extraction under `/tmp`:

- `uv sync --locked`
- `uv run grc-agent --help`
- `uv run grc-agent doctor --json`
- `uv run grc-agent health`
- `uv run python -m unittest tests.production`

Results:

| Check | Result |
| --- | --- |
| Fresh workspace | `/tmp/grc_agent_phase17_install_V6IVdJ` |
| `uv sync --locked` | Passed |
| `grc-agent --help` | Passed; CLI entry point available |
| `doctor --json` | Failed, correctly, because `gnuradio` Python module was not importable in the fresh uv environment |
| `health` | Failed with exit `1`, `status=not_ready`, `status_reasons=["llama_unreachable"]` |
| `tests.production` | Passed, 34 tests |

Important finding: the fresh uv environment selected Python `3.12.13`, while the
host GNU Radio Python bindings are available to the current project environment
as GNU Radio `3.10.9.2`. `uv sync` alone does not install or bridge GNU Radio
Python bindings. This is the main install-readiness gap for real users.

## Doctor And Health Audit

| Dependency/state | How checked now | Current behavior | Expected missing-state behavior | Gap |
| --- | --- | --- | --- | --- |
| Package import / CLI | `uv run grc-agent --help` | Passes | Command unavailable if install failed | None |
| Python version | `doctor` | Passes current env: `3.12.3`; fresh env: `3.12.13` | Fail below 3.12 | None |
| `grcc` | `doctor` uses `shutil.which("grcc")` | Passes current and fresh host: `/usr/bin/grcc` | Fail if absent | None |
| GNU Radio Python module/version | `doctor` imports `gnuradio.gr` and checks `3.10.9.2` | Passes current env; fails fresh uv env | Fail if missing or wrong version | Real install docs need a supported GNU Radio/Python binding path |
| App config | `doctor` loads `grc_agent.toml` or user/default config | Passes | Fail invalid explicit config | None |
| Catalog retrieval readiness | `doctor` calls `initialize_retrieval()` | Passes with `/usr/share/gnuradio/grc/blocks` | Fail if catalog unavailable | None |
| Vector index presence | `grc-agent vector stats --json` | Passes current index; missing index fails `missing_index` | Fail closed for vector commands | `doctor`/`health` do not summarize vector-index presence |
| llama server reachability | `health` calls `/props`; `doctor` only checks llama when `--start-llama` is used | `health` reports `not_ready` and `llama_unreachable` | Must not report OK | None for health; doctor default is package/passive by design |
| Actual context known | `health` extracts context from llama props | Current `null`, `llama_context_verified=false` | Degraded/not-ready if unknown | Correct when server unavailable |
| Model-facing tools | `health` and `release-manifest` list tools | Six MVP wrappers only | Legacy/internal tools must not be model-facing | None |
| Session loaded | `health` reports `session_loaded=false` without graph | Correct | Should be false outside graph session | None |

Current local probe results:

- `uv run grc-agent doctor --json`: `ok=true` for package/GNU/catalog checks
  when llama is not requested.
- `uv run grc-agent health`: exit `1`, `status=not_ready`,
  `status_reasons=["llama_unreachable"]`.
- `uv run grc-agent release-manifest`: `dirty=false`, health status
  `not_ready`, actual context `null`, six model-facing tools.
- `uv run grc-agent vector stats --json`: `ok=true`, 1605 records, 564 catalog
  blocks, 882 manual chunks, 159 tutorial chunks.

## Debug Bundle Design

No one-command debug bundle exists yet. The CLI currently exposes
`doctor`, `health`, `release-manifest`, `vector stats`, dogfood intake, history,
and trace/eval artifacts, but there is no packaged redacted bundle command.

Recommended future command:

```bash
uv run grc-agent debug-bundle --output /tmp/grc-agent-debug-bundle.zip
```

Expected contents:

- Git commit and dirty state.
- Redacted config source and effective config summary.
- `doctor --json`.
- `health`.
- `release-manifest`.
- Tool surface and prompt/schema/policy hashes.
- Vector index stats, when available.
- Recent trace summaries and error summaries, not full raw secrets.
- Environment versions: Python, GNU Radio, `grcc`, platform, package version.
- Optional copied graph summary and validation result when explicitly provided.

Hard requirements:

- No `.env` contents.
- No raw API keys or authorization headers.
- Redact home/private paths unless the user opts into full paths.
- Do not include original graphs by default.
- Include a manifest of files in the bundle.

This should be implemented only after redaction tests exist.

## Artifact Hygiene

| Artifact class | Current behavior | Status |
| --- | --- | --- |
| `.env` | Ignored by `.gitignore` | Pass |
| Local history/vector state | `.grc_agent/` ignored | Pass |
| Llama eval store | `.llama_eval/` ignored | Pass when present |
| Reports | `reports/` ignored | Pass |
| Local temp path | `tmp/` ignored; most gameplay/eval commands use `/tmp` | Pass |
| Saved/restored/temp graphs | `*.saved.grc`, `*.restored.grc`, `*.tmp.grc` ignored | Pass |
| grcc generated Python at repo root | `/*.py` ignored except `setup.py` and `conftest.py` | Pass |
| Frozen retrieval baseline | Explicitly unignored `tests/data/retrieval/vector_eval_governed_metadata.json` | Intentional |

Generated dashboards are typically written to `/tmp` by documented commands.
If a user writes dashboard JSON into the repo under a non-ignored path, git will
show it; there is no broad `*.json` ignore because tracked manifests and
scenarios are intentional.

## Issue Intake

Added user-facing issue intake guidance:

- `docs/ISSUE_INTAKE.md`

Required issue evidence:

- Commit, OS/Python/GNU Radio versions, `grcc` path.
- Redacted `doctor`, `health`, and `release-manifest`.
- Copied/minimized graph, not sensitive original graph.
- Exact reproduction prompt or command.
- Expected vs actual behavior.
- Validation result, save/load result, graph delta when relevant.

Immediate safety flags include preview mutation, failed-validation commit, raw
YAML mutation, implicit save, original/example graph mutation, docs/RAG mutation
authority, and raw legacy/internal model-facing tool calls.

## Release Readiness Table

| Area | Status | Production blocker |
| --- | --- | --- |
| Install docs | Partial | Need documented, tested GNU Radio Python binding path for fresh uv envs |
| Python packaging | Basic pass | No wheel/sdist smoke yet |
| CLI availability | Pass | None for basic command availability |
| GNU Radio dependency | Current env pass; fresh uv env fail | Yes |
| llama health semantics | Pass | Server/model still manual and currently unreachable locally |
| Vector index ops | Partial pass | Doctor/health do not report vector-index presence; users need separate `vector stats` |
| Debug bundle | Missing | Yes for production supportability |
| Artifact hygiene | Mostly pass | Need future bundle redaction tests |
| Issue intake | Added docs template | Needs integration into repository issue workflow if public |
| CI | Partial | CI runs lint, unit, fake smoke; no vector/docs eval or install artifact smoke |

## Blockers To Production-Ready

- Fresh `uv sync --locked` does not by itself provide GNU Radio Python bindings.
- No supported fresh-machine install recipe proves GNU Radio, `grcc`, Python
  bindings, vector index, and llama.cpp together.
- No wheel/sdist installation smoke.
- No one-command redacted debug bundle.
- `doctor`/`health` do not summarize vector-index presence; vector commands fail
  closed, but ops status is split.
- llama.cpp server is currently unreachable locally, and `health` correctly
  reports `not_ready`.

## Phase 18 Recommendation

Do not change graph mutation behavior. Next package/ops work should be:

1. Define a supported install matrix for GNU Radio Python bindings with uv:
   system Python, venv with system site packages, container/devcontainer, or
   conda/mamba.
2. Add a wheel/sdist smoke in a clean temp environment.
3. Add a `debug-bundle` design test plan before implementation.
4. Consider adding vector-index status to `doctor`/`health` as ops telemetry,
   without changing retrieval behavior.
5. Add CI jobs for vector/docs evals only if runtime cost and artifact caching
   are acceptable.

No production-ready claim is made.
