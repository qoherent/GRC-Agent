"""Reviewer/planner prompt builders specific to the inspect_graph eval
experiment. Plugged into the generic experiments/llm_review pipeline —
swap these out (or write new ones alongside) to reuse that pipeline for a
different review task.
"""

import inspect
from pathlib import Path

from grc_agent.adapter import keep_param, render_port

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AGENTS_MD = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")

# Read live via inspect.getsource (not copy-pasted) so this can never drift
# from the actual filtering code as adapter.py changes.
FILTERING_SOURCE = (
    "```python\n" + inspect.getsource(keep_param) + "\n" + inspect.getsource(render_port) + "```"
)

REVIEWER_TEMPLATE = """You are reviewing the output of a GNU Radio Companion (GRC) flow-graph \
inspection tool called `inspect_graph`, used by an LLM coding agent to understand a flow \
graph's structure before editing it.

Follow these project rules exactly (from AGENTS.md):

{agents_md}

Below is the ACTUAL, CURRENT source of the two functions that produced the TOOL OUTPUT below: \
`keep_param` decides which block parameters are shown, `render_port` decides which ports are \
shown. This tells you exactly which native attributes (hide, category, dtype, default, \
optional) drove each decision:

{filtering_source}

This code tells you the RULE, not the DATA — the specific `default`/`hide`/`category`/`optional` \
values for a given parameter or port in this fixture are only visible if they are directly \
observable in the ORIGINAL GRC SOURCE FILE or TOOL OUTPUT below (e.g. the same key appearing \
with two different values, a value that is clearly a variable reference, or a value the code's \
own logic makes unambiguous). Do not guess or assume what a parameter's schema default, hide \
category, or a port's optional status is from general GNU Radio knowledge — if you cannot see \
it in the documents given, do not make a claim that depends on it. A correct application of the \
rule above is not a finding; only flag a case where the OUTPUT's actual behavior appears to \
contradict what this code, applied to values you can directly observe, would produce.

IMPORTANT: the ORIGINAL GRC SOURCE FILE is GNU Radio's own SAVED file, not a complete list of \
every parameter a block's schema defines. GNU Radio's save format omits a parameter from the \
file when it is at that parameter's schema default, and a block's schema can gain new \
parameters over time that an older saved file predates — the live block model backfills any \
parameter missing from the saved file with that parameter's current schema default before \
`inspect_graph` ever runs. Two direct consequences:
- A parameter appearing in TOOL OUTPUT with no corresponding key in the ORIGINAL GRC SOURCE \
FILE is NOT evidence of fabrication by itself — it is expected whenever that parameter has \
`hide == "none"` in the code above (always shown, regardless of default) or was simply omitted \
from the save file for being at default. Do not flag this as a finding unless you can show the \
code above would NOT produce it.
- Do NOT compute an expected `omitted_params_count` by counting keys under the ORIGINAL GRC \
SOURCE FILE's `parameters:` section — that count is systematically incomplete for the reason \
above and is not a valid basis for a finding. You have no reliable way to independently verify \
`omitted_params_count`'s arithmetic from the documents given; do not make a claim about it.

Your task: compare the ORIGINAL GRC SOURCE FILE against the TOOL OUTPUT (the filtered JSON the \
LLM agent actually sees), and answer, strictly grounded in what you observe in these two \
documents plus the code above — no speculation, no assumptions about intent:

1. Is the TOOL OUTPUT optimal and concise relative to the ORIGINAL SOURCE?
2. Does the TOOL OUTPUT omit or filter out any native GRC property (parameter, port, or \
attribute) present in the ORIGINAL SOURCE that appears important for understanding or editing \
this flow graph, in a way that is NOT simply the code above behaving as written? Cite the \
specific block/parameter names.

{fixture_content}

Respond using EXACTLY this format, nothing more:

# Review: {fixture_name}

## Optimality & Conciseness
<1-3 sentences>

## Omitted Native GRC Properties
<Bullet list of specific block/parameter names omitted, with one line of justification each, \
citing only what is directly observable in the documents/code above. If none, write \
"None identified.">

## Findings
- <finding 1, one line, grounded in the comparison above>
"""

PLANNER_TEMPLATE = """You are synthesizing findings from {count} independent reviews of a GNU \
Radio flow-graph inspection tool (`inspect_graph`), each review comparing the tool's filtered \
JSON output against the original GRC source file for a different test flow graph.

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
**Observed in:** <fixture filename(s)>
**Cause:** <grounded explanation from the reports>
"""


def reviewer_prompt(path: Path, content: str) -> str:
    return REVIEWER_TEMPLATE.format(
        agents_md=AGENTS_MD,
        filtering_source=FILTERING_SOURCE,
        fixture_content=content,
        fixture_name=path.stem,
    )


def planner_prompt(reports: list[tuple[Path, str]]) -> str:
    concatenated = "\n\n---\n\n".join(f"### Report: {p.name}\n\n{c}" for p, c in reports)
    return PLANNER_TEMPLATE.format(count=len(reports), agents_md=AGENTS_MD, reports=concatenated)
