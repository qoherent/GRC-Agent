# Fix docs RAG false refusals (Experiment A → D fallback)

**Date:** 2026-06-25
**Status:** Design approved, awaiting implementation plan

## Problem

`query_knowledge` → docs domain (`ask_grc_docs` → `_generate_grounded_answer`)
returns the refusal string *"The provided documentation does not cover this."*
for **2 of 10** queries in the canonical battery, even though the retrieval
succeeded and the top-ranked source explicitly answers the question:

| Query | Top source | Distance | Answer |
|---|---|---|---|
| "How do I choose a sample rate for a flowgraph?" | `Sample_Rate.md` | 0.997 | REFUSAL |
| "What does the band-pass filter taps block do?" | `Band-pass_Filter_Taps.md` | 0.879 | REFUSAL |

For Q10, the top source's first sentence literally is:
*"Generates taps for a band-pass filter and stores it in variable called
whatever the ID is set to. It's essentially a convenience wrapper for calling
firdes.band_pass() or firdes.complex_band_pass()."* — the answer is in
the docs; the LLM still refuses.

Confirmed **context-independent** under the 120K context fix: refusals are
identical before and after the num_ctx migration.

## Root cause

Prompt-conservatism. The current `_generate_grounded_answer` prompt
(`doc_answer.py:267-275`) contains an explicit refusal instruction
(*"If the documentation does not contain the answer, say exactly: '... does
not cover this.'"*). The 7.5B model over-triggers this refusal: it makes
its own relevance judgment inline with answer generation and, being uncertain,
defaults to the safe refusal — even when the docs are clearly sufficient.

## Goal & non-goals

**Goal:** rescue the 2 refusals so all 10 queries produce grounded,
source-cited answers.

**Non-goals:** improve the 8 currently-grounded answers' *quality* (only
preserve them from regression). No retrieval/index/wrapper changes. No
new dependencies. No model upgrades.

## Success criteria (strict)

1. Q03 and Q10 are **not** refusals AND their answers are non-empty
   (≥ 60 chars) AND mention the expected topic (`sample rate` /
   `band-pass` or `bandpass`/`filter`).
2. The 8 currently-grounded queries do **not** become refusals
   (refusal-string check).
3. The 8 grounded answers do **not** begin fabricating content beyond the
   sources (the eval surfaces the full answer text for review; the eval
   itself enforces the refusal-string guard, and a snapshot of the current
   8 grounded answers is kept as a regression reference for diffing).

If criterion 1 or 2 fails, the experiment is a regression → fall back.

## Architecture

Three components, minimal blast radius:

1. **Eval harness** — `playground/query_knowledge_experiment/eval_docs_rag.py`
   (new). Re-runs the 10-query battery against `ask_grc_docs`, classifies
   each answer, prints per-query verdicts, exits non-zero on failure.
2. **Fix surface** — `src/grc_agent/runtime/doc_answer.py:_generate_grounded_answer`
   only. Retrieval, index, and the `ask_grc_docs` wrapper are untouched.
3. **Experiment loop** — driven empirically: try A; if it regresses, try D.

### Component 1 — Eval harness

Inputs: the 10 QUERIES (copy from `run_10_queries.py`), Ollama server.
For each query:
1. Call `ask_grc_docs(agent, question=q)` (same code path the model uses).
2. Classify the answer string:
   - `refusal`: contains the exact substring
     `"The provided documentation does not cover this"`.
   - `grounded`: not a refusal AND `len(answer) ≥ 60`.
   - `expected-grounded` (Q03, Q10 only): grounded AND contains the
     expected topic tokens (`sample rate` / `band-pass` / `bandpass` /
     `filter`).
3. Verdict per query:
   - Q03, Q10 → must reach `expected-grounded`.
   - The other 8 → must reach `grounded` (not `refusal`).
4. Print a 10-row table and a summary line.
5. Exit code: 0 if all 10 verdicts green, 1 otherwise.

The eval also writes the raw answer for each query to
`playground/query_knowledge_experiment/eval_outputs/<timestamp>/NN_<slug>.md`
so a human (and a future diff) can review.

The 8 currently-grounded queries' raw answers are snapshotted to
`playground/query_knowledge_experiment/eval_outputs/_baseline_8grounded/`
as the regression reference (one-off copy during implementation).

### Component 2 — Fix surface

Only the prompt string inside `_generate_grounded_answer`. No new functions,
no new imports, no model/config changes. Both Experiment A and Experiment D
edit the same function (A: prompt body; D: wrap with a gate call).

## Experiment A — Groundedness-first reframing (first attempt)

Replace the prompt body in `_generate_grounded_answer` (current lines
267-275) with:

> You are answering a GNU Radio question. Use ONLY the documentation
> below. Ground every claim in the docs and cite the source file name.
> The sources below were retrieved as relevant to this question.
>
> Answer concisely and directly. If a specific sub-question is not
> addressed by the sources, say which part is not covered, but still
> answer what IS covered.
>
> Do not make up information. If NONE of the sources are related to
> the question, say exactly: "The provided documentation does not cover
> this."

Key changes vs current:
1. *"retrieved as relevant"* — re-frames retrieval as already-vetted,
   combating the over-refusal trigger (the model stops second-guessing
   relevance).
2. *"Ground every claim and cite"* — active grounding anchor;
   counterbalances fabrication risk.
3. *"answer what IS covered"* — decouples partial answer from full
   refusal (directly targets Q03/Q10).
4. Narrowed refusal trigger: *"If NONE of the sources are related"*
   (was *"does not contain the answer"*).

**Run procedure:** change prompt → run eval → if all 10 verdicts green,
ship A. Otherwise revert and try D.

## Experiment D — Two-call relevance gate (fallback)

Add a relevance-gate call before answer generation. Decouples refusal
from generation — the gate makes the relevance judgment in isolation;
the answer call only runs when the gate confirms relevance.

Gate call (new, cheap, constrained output):

> Do the sources below contain the answer to the question?
> Reply with ONLY "YES" or "NO" on the first line, then one short
> sentence of reasoning.
>
> Question: {question}
>
> Sources:
> {sources_paths_and_one_line_each}

Parse: take the first line, strip, uppercase, check `YES`/`NO`. If
unparseable → default `NO` (refuse) for safety on the hallucination bar.

If `YES` → call the grounded answer (using A's prompt for consistency
and best grounding). If `NO` → return the refusal string directly
(no second LLM call).

Cost: 2× LLM calls per docs query. Acceptable for local Ollama.

**Run procedure:** only invoked if A regresses. Implement D, run eval,
ship if all 10 verdicts green. The gate's answer call uses A's prompt
(so D ships with the best grounding even if A alone wasn't enough to
rescue the refusals).

## Error handling

| Failure mode | Handling |
|---|---|
| Ollama unreachable / timeout | Existing `_generate_grounded_answer` raises; the eval surfaces the exception per query (verdict = `error`, does NOT count as grounded). Harness run is invalidated; retry. |
| Gate output unparseable (Experiment D) | Default to `NO` → refuse. Conservative: a parse failure must not let the answer call run un-vetted. |
| Context cap (`_MAX_CONTEXT_WORDS`) | Unchanged. Existing cap already protects the prompt from exceeding `num_ctx: 32768`. |
| Empty `sources` list | Existing `ask_grc_docs` returns an error payload; the eval marks the query as `error`, not `grounded`. Out of scope for this fix. |

## What does NOT change

- Retrieval / vector index / `DOCS_DIR` / chunking / embedding model.
- `ask_grc_docs` wrapper signature.
- `query_knowledge` tool surface.
- The 8 grounded queries' source content.
- Any test in the existing 272 default / 29 grc_native / 6 gui gates.

## Scope freeze

Only `src/grc_agent/runtime/doc_answer.py` (prompt) and the new
`playground/query_knowledge_experiment/eval_docs_rag.py` may be added or
modified. No other file changes.

## Out of scope (follow-ups, not closed by this work)

- Graph serialization audit (no suspicion; available on request).
- Agent-flow harness noise (±1 scenario from Ollama temp-0 nondeterminism;
  inherent).
- Scenario 06 topology ceiling (accepted local-inference limit; cloud
  backend handles it).
- Improving the 8 grounded answers' quality (this work only preserves
  them).

## Self-review (placeholder / consistency / scope / ambiguity)

- No `TBD` / `TODO` placeholders. ✓
- Internal consistency: experiment flow (try A → fall back to D) matches
  the success criteria and error-handling sections. ✓
- Scope: single focused iteration on docs refusal; the 10-query battery
  is the eval ground truth; the success bar is explicit. ✓
- Ambiguity: "grounded" and "expected-grounded" verdicts are defined
  concretely (refusal-string + length + topic tokens). The 60-char floor
  is explicit. Gate parse rule is explicit. ✓