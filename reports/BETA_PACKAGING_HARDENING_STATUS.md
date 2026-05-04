# Beta Packaging Hardening Status

Date: 2026-05-03

Scope: packaging/UX/operational hardening only. No runtime architecture, tool-surface, Advisor promotion, vector policy, planner, or mutation-semantics changes.

## Audit Matrix

| Item | Status | Notes |
|---|---|---|
| Install path (`uv sync --locked`) | ready | Works with local config + packaged deps; external GNU Radio/llama.cpp remain explicit prerequisites. |
| Config defaults | ready | Safe defaults confirmed: MVP wrapper profile on by default via `legacy_model_tool_surface=false`; advisor influence disabled; temperature `0.0`; `enable_thinking=false`; localhost server URL. |
| CLI command surface | low-risk polish | Added actionable hints for common failure cases and a warning when users load installed example paths directly. |
| `doctor` / `health` diagnostics | ready | Structured output and human-readable checks are stable; includes GNU and retrieval readiness. |
| llama.cpp startup/reuse | ready | Startup/reuse flow works; launcher errors remain explicit and actionable. |
| GNU Radio / `grcc` detection | ready | `doctor` reports both `grcc` path and GNU Radio import/version clearly. |
| Vector missing-index behavior | ready | No auto-build in chat path; CLI now hints to run `vector build` when missing index is reported. |
| Checkpoint/history behavior | ready | Baseline + accepted checkpoints recorded; restore remains CLI-only to explicit copy path and refuses overwrite. |
| Artifact ignore hygiene | low-risk polish | Added ignore entries for local cache/tmp/benchmark and copied smoke graph artifacts; `reports/**/*.json*` and `.grc_agent/` already ignored. |
| Copied-graph safety posture | docs-only fix | Updated docs to repeatedly require copied `.grc` files and warn against editing installed originals. |
| Docs consistency | docs-only fix | Updated README/QUICKSTART/BLUEPRINT/SYSTEM_DESIGN_BIBLE to align on MVP wrappers default + advisor shadow-only. |
| Error message actionability | low-risk polish | Added targeted hints for file-load, invalid-grc, retrieval-not-ready, restore-target-exists, and missing vector index. |

## CLI Polish Applied

- `grc-agent chat` / `grc-agent fake` now print a non-blocking warning when the provided graph path appears to be an installed GNU Radio example path (`/usr/share/gnuradio/examples` or `/usr/local/share/gnuradio/examples`).
- CLI error rendering now appends actionable hints for:
  - missing/unreadable `.grc` path
  - invalid `.grc` payload
  - retrieval-not-ready startup failures
- `grc-agent vector search` missing-index failures now include a direct `vector build` hint.
- `grc-agent history restore` target-exists failures now include an explicit `--to` new-path hint.

## Config Hardening Check

Verified defaults (from config + code):

- `agent.legacy_model_tool_surface = false` (MVP wrappers default for chat)
- `agent.advisor_enabled = false`
- `agent.advisor_limited_advisory = false`
- `agent.advisor_shadow_telemetry = true`
- `llama.server_url = http://127.0.0.1:8080`
- `llama.temperature = 0.0`
- `llama.enable_thinking = false`
- vector index is explicit-build only (no chat auto-build)
- history restore refuses overwrite by design

## Dogfood Intake UX Check

`grc-agent dogfood record` supports required beta fields:

- source
- graph reference (sanitized)
- prompt
- expected / actual behavior
- actual tools
- graph delta summary
- validation state
- save/checkpoint state
- failure category
- severity
- reproducible flag
- notes

Wrapper/internal telemetry can be captured via `actual` and `notes` in intake rows (and is already used by wrapper-controlled dogfood reports).

## Packaging Readiness Decision

- Overall status: ready for controlled beta on copied graphs.
- Blocking issues found: none.
- Runtime behavior patch required: none.
- Docs/config/CLI polish applied: yes (low-risk only).
- Final tester handoff: `reports/BETA_FINAL_TESTING_HANDOFF.md`.
