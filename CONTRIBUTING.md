# Contributing

## Setup

Follow [`README.md`](README.md)'s Installation section (GNU Radio + GTK
system prerequisites, `uv venv --system-site-packages`, `uv sync --extra dev`).

## Before opening a PR

Read [`AGENTS.md`](AGENTS.md) first — it documents the architecture, the
engineering rules this codebase holds itself to (no hand-picked heuristics,
no brittle reinventions, fix at the source), and a long list of specific,
live-verified bugs and why their fixes look the way they do. Most non-obvious
design decisions in this codebase are explained there, not in code comments.

Run the full test gate before submitting:

```bash
uv run playwright install chromium      # one-time, for test_frontend.py
uv run pytest tests/test_unit.py        # fast, no LLM
uv run pytest tests/test_web_app.py     # web endpoints, no LLM
uv run pytest tests/test_frontend.py    # dashboard UI, real Chromium, no LLM
uv run pytest tests/test_isolation.py   # settings/.env isolation, no LLM
uv run pytest tests/test_integration.py # live model, ~15-20 min — run when touching agent.py/tools
uv run ruff check
uv run ruff format --check
```

`test_unit.py`/`test_web_app.py` still touch live network (embeddings, a
lite.duckduckgo.com search) — not fully hermetic, but need no GUI/display
server. `test_integration.py` needs a running local Ollama (or an
`OPENROUTER_API_KEY`) and expects occasional flakiness on weaker/quantized
models unrelated to code correctness — retry the specific failing scenario
before treating it as a regression.

## Conventions worth knowing before you write code

- **No hand-picked heuristics.** A fix should be one uniform rule applied to
  every case, not a per-scenario branch or a hardcoded name/allowlist.
- **Fix at the source.** Correctness lives in the tool/handler that produces
  data, not in a post-processor.
- **Evidence before assertions.** Claims about behavior should cite a live,
  verified observation, not intent — a passing test is necessary, not
  sufficient. For a frontend fix, drive it with a real (even headless)
  browser via Playwright, not just an HTTP-level check — see AGENTS.md's
  `test_frontend.py` entry for why.
- **No silent transformation.** Any truncation, filtering, or omission in
  model-facing output must be explicit and documented.

## Reporting bugs / requesting features

Open a GitHub issue. Include your GNU Radio version (`gr.version()` or
`grc --version`), OS, and — for a chat/agent bug — which provider
(`ollama`/`ollama_cloud`/`openrouter`) and model.

## Security

See [`SECURITY.md`](SECURITY.md) for how to report a vulnerability instead of
opening a public issue.
