# Experiments

One-off evaluation scripts and findings from past audit passes. These are NOT
part of the shipped application — `src/grc_agent/` does not import from here,
and `pyproject.toml` does not package this directory. The experiments are kept
for historical reference and to inform future improvements.

## Directory layout

| Directory | What it evaluates | Key output |
|-----------|-------------------|------------|
| `inspect_eval/` | `inspect_graph` tool output — does the filtered JSON correctly retain non-default params per `keep_param`? | `final_findings_and_causes.md` |
| `query_catalog_eval/` | `query_knowledge(domain="catalog")` — does vector search return the right block for a functional query? | `final_findings_and_causes.md` |
| `query_docs_eval/` | `query_knowledge(domain="docs")` — does vector search return relevant doc chunks, or is it diluted by boilerplate? | `final_findings_and_causes.md` |
| `llm_review/` | Shared library for running an LLM-as-judge review pipeline over agent outputs (used by the `*_eval` experiments). | `pipeline.py` |

## Running

Each `*_eval` directory follows the same pattern:
1. `generate_outputs.py` — runs the agent/tool against a set of prompts, saves raw outputs to `raw_outputs/`.
2. `review_outputs.py` — feeds the raw outputs through an LLM judge, saves to `reports/`.
3. `final_review.py` — aggregates the per-prompt reviews into `final_findings_and_causes.md`.

These scripts require a live LLM backend (Ollama or OpenRouter) and a built
vector DB. They are not part of the test suite and are not run in CI.
