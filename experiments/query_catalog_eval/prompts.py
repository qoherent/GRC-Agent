"""Reviewer/planner prompt builders for the query_catalog eval experiment.
Plugged into the generic experiments/llm_review pipeline.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AGENTS_MD = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")

REVIEWER_TEMPLATE = """You are reviewing the output of `query_catalog`, a semantic/vector search \
tool over GNU Radio's block catalog. An LLM coding agent uses it to find the right GNU Radio \
block(s) for a task before adding them to a flowgraph.

Follow these project rules exactly (from AGENTS.md):

{agents_md}

How `query_catalog` works: each GNU Radio block is embedded as free text composed of its label, \
block_id, category, every visible parameter (rendered as "key=[dtype]=default"), and every port \
("port_id (dtype)"). A query is embedded the same way and the top results are returned ranked by \
vector distance (lower distance = more semantically similar). Each result already shows its \
block_id, label, category, live-rendered params (each already in "[dtype]=default" form — the \
schema default is directly visible, do not guess at one that isn't shown), and ports.

QUERY: {query!r}

RESULT JSON:

```json
{result_json}
```

Your task, strictly grounded in the QUERY and RESULT JSON above plus real, general GNU Radio \
domain knowledge — do not invent a specific block_id you are not confident actually exists in \
GNU Radio; if you believe a different real block would serve the query better, name it only if \
you are confident it is real, otherwise describe the kind of block instead of a specific ID:

1. Does the top result (or top few) genuinely match what the query is asking for?
2. Is there a clearly more relevant, real GNU Radio block that seems to be missing or ranked too \
low?
3. Is the shown param/port information sufficient to actually use the top result, or is \
something important missing?
4. If the query has no good match anywhere in GNU Radio's real block catalog, say so plainly — \
that is a correct, expected outcome, not a tool failure.

Respond using EXACTLY this format, nothing more:

# Review: {slug}

## Relevance
<1-3 sentences: does the top result match the query's intent?>

## Missing or Better Matches
<Named real GNU Radio block(s) that would serve the query better and are missing/ranked too \
low, with justification. If none, write "None identified.">

## Findings
- <finding 1, one line, grounded in the query/result above>
"""

PLANNER_TEMPLATE = """You are synthesizing findings from {count} independent reviews of \
`query_catalog`, a semantic search tool over GNU Radio's block catalog, each review judging one \
search query's results for relevance and completeness.

Follow these project rules exactly (from AGENTS.md):

{agents_md}

Below are the {count} individual reviewer reports:

{reports}

Task: produce a single final report containing ONLY "Findings and Causes" — for each distinct \
finding that appears (deduplicated) across the individual reports, state the finding and its \
underlying cause as evidenced by the reports.

Strict constraints:
- Do NOT include any suggestions, fixes, or recommendations.
- Do NOT include any information not grounded in the individual reports provided.
- Do NOT introduce bias or speculation.

Respond using EXACTLY this format:

# Final Findings and Causes

## Finding: <short title>
**Observed in:** <query/queries>
**Cause:** <grounded explanation from the reports>
"""


def _extract_result_json(content: str) -> dict:
    # rindex, not split() — a block's rendered param/label text could in
    # principle contain a literal ``` sequence, which would otherwise
    # truncate a naive split at the first fence found after the opening
    # ```json.
    start = content.index("```json\n") + len("```json\n")
    end = content.rindex("\n```")
    return json.loads(content[start:end])


def reviewer_prompt(path: Path, content: str) -> str:
    parsed = _extract_result_json(content)
    query = parsed.get("query", path.stem.replace("_", " "))
    return REVIEWER_TEMPLATE.format(
        agents_md=AGENTS_MD,
        query=query,
        result_json=json.dumps(parsed, indent=2),
        slug=path.stem,
    )


def planner_prompt(reports: list[tuple[Path, str]]) -> str:
    concatenated = "\n\n---\n\n".join(f"### Report: {p.name}\n\n{c}" for p, c in reports)
    return PLANNER_TEMPLATE.format(count=len(reports), agents_md=AGENTS_MD, reports=concatenated)
