# GRC Agent Dataset Collection Harness

Collects fine-tuning data for the GRC agent: an LLM-simulated user drives the
REAL desktop app (under xvfb) through ~15 seeded GNU Radio tasks; every layer
of each session is recorded (session DB + step store + this harness's
manifest), completeness is audited, and audited sessions export as
Unsloth-ready OpenAI `messages` JSONL. See
`docs/plans/2026-09-08-1627-feat-finetuning-dataset-collection-harness-plan.md`.

This is experiments-only tooling: not packaged, not run in CI.

## Prerequisites

- `xvfb-run` available (`sudo apt install xvfb`).
- Teacher model configured in the app (Settings, or the real `.env`) — the
  campaign records whatever provider/model the settings resolve at boot.
- **API keys via the process environment only. Never copy the real `.env`
  into the campaign dir.** The campaign env file is created empty on purpose:
  `export OLLAMA_API_KEY=...` (or `OLLAMA_CLOUD_API_KEY`) before running.
- `GRC_SHELL_DENIED_COMMANDS` must not be set empty (refuses to start).
- **Operator safety check before unattended runs:** the shell denylist matches
  executable names only and is not a security boundary; flowgraph execution on
  a host with attached SDR hardware could key a transmitter. Confirm the
  machine is safe for unattended runs (container/VM or no radios attached).

## Hermetic checks (no LLM spend)

```bash
uv run python -m experiments.dataset_collection.task_spec --validate      # U1
uv run python -m experiments.dataset_collection.task_spec --prepare-fixtures
uv run python -m experiments.dataset_collection.simulator                # U2 selftest
uv run python -m experiments.dataset_collection.audit --selftest         # U4 leg 1
uv run python -m experiments.dataset_collection.audit --validate-real-db # U4 leg 2
xvfb-run -a uv run python -m experiments.dataset_collection.dryrun       # U4 leg 3
```

## Mini-campaign smoke (stub simulator, TestModel agent)

```bash
xvfb-run -a uv run python -m experiments.dataset_collection.collect \
  --simulator scripted --agent testmodel --campaign-dir /tmp/campaign_smoke
uv run python -m experiments.dataset_collection.audit --campaign /tmp/campaign_smoke
uv run python -m experiments.dataset_collection.export --campaign /tmp/campaign_smoke
```

## Full campaign (gated on the API key + configured teacher)

```bash
export OLLAMA_API_KEY=...
xvfb-run -a uv run python -m experiments.dataset_collection.collect \
  --campaign-dir /tmp/campaign_real            # fresh dir; refuses non-empty DB
uv run python -m experiments.dataset_collection.audit --campaign /tmp/campaign_real
uv run python -m experiments.dataset_collection.export --campaign /tmp/campaign_real
```

- Resume after a crash: rerun the same command with a task list minus the
  completed ones (`--tasks t05_qam_debug,...`); the audit cross-checks what
  exists.
- **Mid-campaign checkpoint (after 3 tasks):** read
  `<campaign>/export/../tasks.jsonl` usage totals, recompute per-type token
  yield, and adjust remaining turn budgets/task count to hold the 1–2M target
  while keeping expected compaction low.
- Review the audit report and `export/dataset_card.md` before any upload.

## Layout

| File | Role (plan U-ID) |
| --- | --- |
| `task_spec.py` | U1 — schema, personas, fixture prep, validation |
| `tasks/*.json` | U1 — 15 task specs + persona library |
| `simulator_prompt.py` / `simulator.py` | U2 — prompt assembly, clients, secret filter |
| `collect.py` / `runner_contract.py` / `manifest.py` | U3 — campaign driver, runner contract, manifest |
| `invariants.py` / `audit.py` | U4 — completeness auditor (three legs) |
| `export.py` | U5 — Unsloth JSONL, dataset card, redaction, round-trip |
| `dryrun.py` | U6 — fault-injected dry run |