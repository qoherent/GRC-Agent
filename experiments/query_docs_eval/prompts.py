"""Reviewer/planner prompt builders for the query_docs eval experiment.
Plugged into the generic experiments/llm_review pipeline.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AGENTS_MD = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")

DOCS_DIR = REPO_ROOT / "docs" / "wiki_gnuradio_org"
CORPUS_TITLES = "\n".join(f"- {p.stem}" for p in sorted(DOCS_DIR.glob("*.md")))

REVIEWER_TEMPLATE = """You are reviewing the output of `query_docs`, a vector-RAG search tool \
over a fixed corpus of GNU Radio wiki documentation. An LLM coding agent uses it to answer \
conceptual/how-to questions about GNU Radio.

Follow these project rules exactly (from AGENTS.md):

{agents_md}

How `query_docs` works: every file in the corpus below is chunked by markdown heading, each \
chunk is embedded, and the top-k nearest chunks to the embedded QUERY are concatenated \
(separated by "---") into the ANSWER returned below — there is no separate LLM synthesis step, \
the ANSWER is exactly the retrieved raw text.

The corpus contains ONLY these {n_titles} files — nothing else exists to retrieve from. If the \
QUERY's topic genuinely isn't covered by any of these titles, a poor or irrelevant ANSWER is \
the CORRECT, expected outcome, not a tool failure. Do not claim a topic should have been found \
unless one of these titles plausibly covers it:

{corpus_titles}

QUERY: {query!r}

RESULT JSON:

```json
{result_json}
```

Your task, strictly grounded in the QUERY, the ANSWER text, and the corpus title list above — \
no speculation about documentation that isn't in the list:

1. Does the ANSWER genuinely and accurately address the QUERY?
2. Is the ANSWER complete, or does it look like a partial/tangential retrieval given what the \
corpus title list suggests should be available?
3. If the QUERY's topic has no plausible match anywhere in the corpus title list, say so \
plainly — that is a correct, expected outcome, not a tool failure.

Respond using EXACTLY this format, nothing more:

# Review: {slug}

## Relevance & Completeness
<1-3 sentences: does the answer address the query, and is it complete?>

## Retrieval Issues
<Specific ways the retrieval missed a better in-corpus match (name the title from the list \
above) or returned irrelevant/tangential chunks. If none, write "None identified.">

## Findings
- <finding 1, one line, grounded in the query/answer/corpus list above>
"""

PLANNER_TEMPLATE = """You are synthesizing findings from {count} independent reviews of \
`query_docs`, a vector-RAG search tool over a fixed GNU Radio documentation corpus, each review \
judging one question's retrieved answer for relevance and completeness.

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
    # rindex, not split() — the ANSWER text retrieved from the docs corpus can
    # itself contain literal ``` code fences, which would otherwise truncate
    # a naive split at the first fence found after the opening ```json.
    start = content.index("```json\n") + len("```json\n")
    end = content.rindex("\n```")
    return json.loads(content[start:end])


def reviewer_prompt(path: Path, content: str) -> str:
    parsed = _extract_result_json(content)
    query = parsed.get("query", path.stem.replace("_", " "))
    n_titles = len(CORPUS_TITLES.splitlines())
    return REVIEWER_TEMPLATE.format(
        agents_md=AGENTS_MD,
        n_titles=n_titles,
        corpus_titles=CORPUS_TITLES,
        query=query,
        result_json=json.dumps(parsed, indent=2),
        slug=path.stem,
    )


def planner_prompt(reports: list[tuple[Path, str]]) -> str:
    concatenated = "\n\n---\n\n".join(f"### Report: {p.name}\n\n{c}" for p, c in reports)
    return PLANNER_TEMPLATE.format(count=len(reports), agents_md=AGENTS_MD, reports=concatenated)
