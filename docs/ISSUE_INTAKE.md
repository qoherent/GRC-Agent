# Issue Intake Template

Use this template for GRC Agent bug reports and dogfood findings. Do not attach
private original graphs unless you have reviewed and approved the contents.
Prefer a copied, minimized graph that still reproduces the issue.

## Required

- GRC Agent commit:
- Operating system and Python version:
- GNU Radio version:
- `grcc` path:
- Runtime model/backend:
- Llama health status:
- Vector index status:
- Command or prompt that reproduced the issue:
- Expected behavior:
- Actual behavior:
- Was the graph a copied working file, not an original installed/example file?
- Did validation run? Include result:
- Did save/load run? Include explicit path and result:

## Attachments

Attach these when available:

- Redacted debug bundle:
  `uv run grc-agent debug-bundle --output /tmp/grc_agent_debug_bundle.json`.
- Redacted `uv run grc-agent doctor --json` output.
- Redacted `uv run grc-agent health` output.
- Redacted `uv run grc-agent release-manifest` output.
- Install smoke output when the issue is setup-related:
  `uv run python -m tests.production.install_smoke --mode system-site-venv --output /tmp/grc_agent_install_smoke.json`.
- A copied `.grc` graph or a minimized reproduction graph.
- Relevant trace/gameplay artifact, with secrets and private paths reviewed.
- Recent terminal error output.

Do not attach:

- `.env` files.
- API keys or authorization headers.
- Full private filesystem dumps.
- Sensitive original graphs without review.

## Reproduction Notes

1. Start from a clean copied graph path.
2. Record the exact command or prompt sequence.
3. Record whether the issue happens with deterministic tools only or requires
   llama.cpp-backed chat.
4. Record whether rerunning changes the outcome.
5. If a mutation occurred, include the graph delta and validation result.

## Safety Flags

Call out any of these immediately:

- Preview mutated the graph.
- Failed validation committed a change.
- Raw YAML mutation reached the graph.
- Save happened without explicit user intent.
- Original or installed example graph was modified.
- Docs/RAG output was treated as mutation authority.
- Raw legacy/internal tool calls appeared in model-facing history.
