# Phase 18 Production Readiness: Debug Bundle And Install Smoke

Phase 18 adds support-evidence automation. It does not change graph mutation
runtime behavior, tool schemas, model behavior, or capability classification.

Runtime classification remains unchanged:

- Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`
- Beta-validated: `R1_SET_STATE`, `R2`, `R3`, `R4A`, `R4B`, `R4C`, `R5`
- Diagnostic-clean: `R7_EXACT_EXTERNAL`, `R7_NATURAL_EXTERNAL`, `Tier5_ADVERSARIAL`
- Docs QA: threshold-met deterministic baseline
- Runtime: not production-ready

## External Grounding

- uv project docs support `uv sync --locked` for lockfile-based project
  installation and `uv run` for project-environment command execution:
  <https://github.com/astral-sh/uv/blob/main/docs/concepts/projects/sync.md>,
  <https://github.com/astral-sh/uv/blob/main/docs/concepts/projects/run.md>.
- GNU Radio source installs `gnuradio-companion` and `grcc` Python executables
  as part of the GRC component, and installs GNU Radio Python sources under the
  GNU Radio Python destination:
  <https://github.com/gnuradio/gnuradio/blob/main/grc/scripts/CMakeLists.txt>,
  <https://github.com/gnuradio/gnuradio/blob/main/grc/CMakeLists.txt>.

These sources ground install/ops expectations only. They do not authorize graph
mutation.

## Added Debug Bundle Command

Command:

```bash
uv run grc-agent debug-bundle --output /tmp/grc_agent_debug_bundle.json
```

Optional:

```bash
uv run grc-agent debug-bundle \
  --output /tmp/grc_agent_debug_bundle.json \
  --vector-index-dir /tmp/missing_or_custom_vector_index
```

Bundle schema version:

- `2026-05-20.phase18-debug-bundle-v1`

Top-level sections:

- `package`: package name and installed package version when available.
- `git`: commit, branch, dirty state, dirty files.
- `environment`: Python version, executable, platform, system, machine.
- `config`: effective app config with sensitive fields redacted.
- `doctor`: passive doctor JSON; it does not start llama.
- `health`: health JSON; unreachable llama remains `not_ready`.
- `release_manifest`: release manifest JSON.
- `tool_surface`: model/internal tool-surface summary from the manifest.
- `hashes`: prompt/schema/policy hashes.
- `vector_index`: summarized vector index stats or `missing_index`.
- `gnu_radio`: GNU Radio and `grcc` doctor checks.
- `llama`: server URL, reachability/context status, and status reasons.
- `recent_trace_summaries`: counts and latest mtimes only.
- `artifact_hygiene`: ignore/tracking status for local artifact paths.
- `recent_errors`: placeholder until structured app error logs exist.
- `exclusions`: confirms env contents, raw prompt history, and raw graph content
  are not included.

Explicit exclusions:

- no `.env` contents
- no raw API keys
- no authorization headers
- no bearer tokens
- no raw prompt history
- no raw graph content

The command exits `0` when the bundle is written, even if health is `not_ready`.
The degraded runtime status is evidence inside the bundle, not a bundle failure.

## Install Smoke Automation

Added:

- `tests/production/install_smoke.py`

Command:

```bash
uv run python -m tests.production.install_smoke \
  --output /tmp/grc_agent_phase18_install_smoke.json
```

The smoke creates a clean temporary workspace copy, then runs:

- `uv sync --locked`
- `uv run grc-agent --help`
- `uv run grc-agent doctor --json`
- `uv run grc-agent health`
- `uv run python -m unittest tests.production`

It reports expected failures separately:

- `missing_gnuradio_python_bindings`
- `missing_grcc`
- `missing_retrieval_catalog`
- `llama_unreachable`
- `llama_context_unknown`
- `missing_index` when vector stats are checked separately

Phase 18 local result matched Phase 17:

- clean uv sync and CLI help work
- `tests.production` passes
- fresh uv env can still fail GNU Radio Python import even with host `grcc`
- health reports `not_ready` when llama.cpp is unreachable

## GNU Radio Environment Strategy

GNU Radio is not a normal Python dependency of this project. It is usually
installed through the operating system, source build, conda/mamba, or a future
container image. The package depends on GNU Radio being visible to the Python
interpreter that runs GRC Agent.

Supported local approaches:

1. Use a Python environment where `import gnuradio` already works.
2. Use a venv configured with system site packages when the distro GNU Radio
   package and Python version match.
3. Set `PYTHONPATH` to the distro GNU Radio Python path only when ABI and Python
   version compatibility are understood.
4. Use a future container/devcontainer once the project defines one.

Verification remains:

```bash
uv run grc-agent doctor
uv run grc-agent health
```

`doctor` verifies package/GNU/catalog readiness. `health` verifies runtime
readiness and must remain `not_ready` when llama is unavailable or actual
context is unknown.

## Tests Added

- Debug bundle CLI creates a bundle in a temp output directory.
- Bundle schema and required sections are present.
- Missing llama status is recorded, not treated as a bundle crash.
- Missing vector index records `missing_index`.
- Secret redaction removes sensitive keys and values.
- Bundle text does not contain API-key, authorization, bearer-token, or test
  secret markers.
- Install-smoke classification identifies missing GNU Radio Python bindings and
  llama-unreachable health reasons.

## Local Gate Results

Required Phase 18 gates:

- `uv run ruff check src/ tests/`
- `uv run python -m unittest tests.production`
- `uv run python -m unittest`
- `uv run grc-agent doctor`
- `uv run grc-agent health`
- `uv run grc-agent release-manifest`
- `uv run grc-agent debug-bundle --output /tmp/grc_agent_debug_bundle.json`

Expected local runtime state:

- `doctor`: passes package/GNU/catalog checks.
- `health`: exits nonzero with `status=not_ready`,
  `status_reasons=["llama_unreachable"]`.
- debug bundle: still generated.

## Remaining Ops Gaps

- No production container/devcontainer yet.
- No wheel/sdist install smoke yet.
- GNU Radio Python binding strategy is documented but not automated.
- Debug bundle is JSON-only, not a zip with optional attachments.
- Debug bundle does not include structured app error logs because no stable log
  path exists yet.
- Vector-index status is available in the bundle and vector commands, but
  `doctor`/`health` still do not summarize vector-index presence directly.

## Final Status

Phase 18 improves supportability and reproducibility. It does not make the
runtime production-ready.

No production-ready claim is made.
