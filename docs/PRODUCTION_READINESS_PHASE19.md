# Production Readiness Phase 19: Reproducible Runtime Environment Strategy

Date: 2026-05-20

Phase 19 adds a reproducible local environment strategy and smoke evidence for
GNU Radio, `grcc`, GRC Agent, retrieval/vector readiness, and llama runtime
status. It does not change graph mutation runtime behavior, tool schemas, eval
scoring, or model behavior.

Runtime classification remains **not production-ready**.

## Current Baseline

- Phase 18 commit: `e09de1847f24`.
- Default clean uv env: package setup works, but GNU Radio Python import can fail
  because distro GNU Radio bindings are outside the uv-managed venv.
- Current host env: `uv run grc-agent doctor` passes because the active env can
  import GNU Radio and find `/usr/bin/grcc`.
- Current host health: `not_ready` when llama.cpp is unreachable. This is the
  correct runtime status, not a package install failure.

## External Grounding

- GNU Radio installs `gnuradio-companion` and `grcc` Python executables from
  `grc/scripts/CMakeLists.txt`:
  <https://github.com/gnuradio/gnuradio/blob/main/grc/scripts/CMakeLists.txt>
- GNU Radio Companion is a Python-enabled component depending on
  `gnuradio-runtime`, from `grc/CMakeLists.txt`:
  <https://github.com/gnuradio/gnuradio/blob/main/grc/CMakeLists.txt>
- GNU Radio build/release notes state that Python package installation
  environments must match the Python interpreter used by the GNU Radio build:
  <https://github.com/gnuradio/gnuradio/blob/main/release/GNURadioBuildAndReleaseProcedure.md>
- uv documents locked project sync and `uv run` for reproducible project
  commands:
  <https://github.com/astral-sh/uv/blob/main/docs/concepts/projects/sync.md>
  and <https://github.com/astral-sh/uv/blob/main/docs/concepts/projects/run.md>
- The installed uv CLI exposes `uv venv --system-site-packages`, which is the
  mechanism used by the recommended local profile.
- Docker documents Dockerfiles as the build input for container images:
  <https://docs.docker.com/build/concepts/dockerfile/>
- VS Code documents dev containers as full development environments defined by
  `.devcontainer/devcontainer.json`:
  <https://code.visualstudio.com/docs/devcontainers/create-dev-container>
- Conda documents creating environments from `environment.yml` with
  `conda env create -f`:
  <https://docs.conda.io/projects/conda/en/latest/commands/env/create.html>
- Mamba documents itself as a conda-compatible environment manager:
  <https://mamba.readthedocs.io/en/stable/user_guide/mamba.html>

## Environment Options

| Option | Install shape | `import gnuradio` | `grcc` | `uv run grc-agent` | Risks | Reproducibility | Difficulty | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| System Python with distro GNU Radio | Install GNU Radio and run package in system Python | Works if Python matches distro GNU Radio | Works when package installs `grcc` on `PATH` | Requires installing GRC Agent into system/user Python | Pollutes system/user Python; harder rollback | Medium | Medium | Supported for expert/local use |
| uv venv with system site packages | `uv venv --system-site-packages --python /usr/bin/python3`, then locked `uv sync` | Works when `/usr/bin/python3` matches GNU Radio build | Works from system `PATH` | Works from uv-managed project env | Host-specific; system packages can affect imports | Medium-high for one host class | Low-medium | **Recommended Phase 19 local path** |
| `PYTHONPATH` bridge | Plain uv env plus distro GNU Radio path injected | Works only if ABI/Python match | Works if `PATH` set | Works but fragile | Easy to mismatch ABI, hard to debug | Low | Medium-high | Last-resort workaround only |
| Conda/mamba env | Install GNU Radio and GRC Agent in same conda/mamba env | Can work if GNU Radio package is available for platform | Can work inside env | Can work after installing project | Solver/channel variance; pip/conda mixing risk | Medium | Medium-high | Future option; not implemented |
| Container/devcontainer | Image installs OS GNU Radio, uv, GRC Agent, vector tooling | Should work if image pins distro/Python pairing | Should work in image | Should work inside image | Image maintenance, hardware GUI/audio/USB mapping | High once pinned | Medium initially | Future preferred CI/user profile |
| Future packaged image | Published tested image with pinned digest | Expected | Expected | Expected | Release maintenance and supply-chain review | Highest | Low for users | Future work |

## Implemented Recommended Path

Phase 19 implements and tests a **system-site uv venv profile**:

```bash
rm -rf .venv
uv venv --system-site-packages --python /usr/bin/python3
uv sync --locked --python .venv/bin/python
uv run grc-agent doctor
uv run grc-agent health
```

This profile keeps Python dependencies locked through `uv.lock` while allowing
the venv to import the distro GNU Radio Python bindings. It is host-class
reproducible rather than fully hermetic: it depends on the host GNU Radio
package and Python ABI staying aligned.

Local probe result before wiring the smoke mode:

- `/usr/bin/python3`: Python 3.12.3.
- GNU Radio import path:
  `/usr/lib/python3/dist-packages/gnuradio/__init__.py`.
- Temp checkout with system-site venv passed:
  `uv sync --locked --python .venv/bin/python` and
  `uv run grc-agent doctor --json`.

## Install Smoke Mode

New command:

```bash
uv run python -m tests.production.install_smoke \
  --mode system-site-venv \
  --output /tmp/grc_agent_phase19_install_smoke_system_site.json
```

Supported modes:

- `default-uv`: Phase 18 default clean uv env. Package setup can pass while GNU
  Radio import fails.
- `system-site-venv`: creates a temp checkout, creates a system-site venv,
  syncs with the lockfile, then runs CLI and production checks.

Report fields now include:

- `mode`
- `selected_python`
- `readiness.package_ready`
- `readiness.gnu_radio_ready`
- `readiness.grcc_ready`
- `readiness.retrieval_ready`
- `readiness.vector_index_ready`
- `readiness.llama_ready`
- `readiness.context_verified`
- `readiness.overall_environment_classification`

Optional vector build check:

```bash
uv run python -m tests.production.install_smoke \
  --mode system-site-venv \
  --build-vector-index \
  --output /tmp/grc_agent_phase19_install_smoke_with_vector_build.json
```

The vector build is not default because it can download/build FastEmbed/Qdrant
state and should be explicit.

## Llama Runtime Strategy

Supported runtime mode remains external/local llama.cpp server mode:

- Expected URL: `http://127.0.0.1:8080` unless configured otherwise.
- Desired context: `120000` tokens.
- `health` verifies reachability through llama server properties and reports:
  - `llama_model_ready`
  - `llama_context_verified`
  - `llama_actual_context_tokens`
  - `llama_desired_context_tokens`
  - status reasons such as `llama_unreachable`

Package install readiness does not require llama. End-to-end runtime readiness
does require llama reachability and verified context. Auto-start/reuse exists
for configured chat paths, but production-ready ops still needs a pinned and
documented model-server profile.

## Debug Bundle Integration

Phase 19 extends the debug bundle with:

- environment mode (`system-python`, `virtualenv`, or `system-site-venv`)
- whether system site packages are visible
- Python executable/prefix/base prefix
- GNU Radio import status
- GNU Radio import path
- GNU Radio version when available
- `grcc` path
- vector index state
- llama reachability and context status

The bundle still excludes `.env` contents, API keys, authorization headers, raw
prompt history, and raw graph contents.

## Proof Commands

Required Phase 19 gates:

```bash
uv run ruff check src/ tests/
uv run python -m unittest tests.production
uv run python -m unittest
uv run grc-agent doctor
uv run grc-agent health
uv run grc-agent debug-bundle --output /tmp/grc_agent_debug_bundle_phase19.json
uv run grc-agent release-manifest
uv run python -m tests.production.install_smoke \
  --mode system-site-venv \
  --output /tmp/grc_agent_phase19_install_smoke_system_site.json
```

Expected local result while llama is down:

- Doctor: pass.
- Health: `not_ready`, `llama_unreachable`.
- Debug bundle: generated successfully with health `not_ready`.
- Install smoke `system-site-venv`: package/GNU Radio/grcc/retrieval ready;
  runtime not ready until llama is reachable and context is verified.

## Remaining Production Blockers

- No pinned container/devcontainer image yet.
- No CI job currently proves GNU Radio + uv + vector + model readiness in a
  clean hosted environment.
- Llama model server startup/context behavior is still host-local, not packaged.
- Vector build is explicit but not yet part of a complete fresh environment CI
  profile.
- Runtime remains not production-ready.

## Final Classification

- Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`
- Beta-validated: `R1_SET_STATE`, `R2`, `R3`, `R4A`, `R4B`, `R4C`, `R5`
- Diagnostic-clean: `R7_EXACT_EXTERNAL`, `R7_NATURAL_EXTERNAL`,
  `Tier5_ADVERSARIAL`
- Docs QA: threshold-met deterministic baseline
- Runtime: **not production-ready**
