# Phase 16 Production Readiness: Second Deterministic Docs QA Batch

Phase 16 is a deterministic docs QA improvement only. It does not change graph
mutation runtime behavior, tool schemas, helper-LLM defaults, or docs/RAG
authority.

Runtime classification remains unchanged:

- Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`
- Beta-validated: `R1_SET_STATE`, `R2`, `R3`, `R4A`, `R4B`, `R4C`, `R5`
- Diagnostic-clean: `R7_EXACT_EXTERNAL`, `R7_NATURAL_EXTERNAL`, `Tier5_ADVERSARIAL`
- Runtime: not production-ready

Docs QA status is now a threshold-met deterministic baseline:

- relevance target: `>= 30/35`, current `32/35`
- groundedness target: `>= 28/35`, current `28/35`
- misleading answers: `0`
- mutation leakage: `0`
- helper used: `0`

This is not a production-ready runtime claim.

## Remaining Row Inspection

Remaining Phase 15 weak rows before this batch:

| Row | Failure dimensions | Classification | Reason |
| ---: | --- | --- | --- |
| Q4 stream vs message ports | relevance, groundedness, insufficient correctness | missing source / comparison assembly gap | The selected sources covered message ports but not the stream-port side strongly enough. |
| Q11 flowgraph | groundedness | selected source too generic / answer template too sparse | The selected answer was a fragment from `Flowgraph Python Code`, not a concept definition. |
| Q15 message strobe + PMT | relevance, groundedness, insufficient correctness | selected source too fragmentary | The block source and message-passing source do not yet provide enough parent/neighbor evidence. |
| Q20 tags vs metadata | relevance, groundedness, insufficient correctness | eval expectation or missing source | Existing stream-tag/message-passing evidence does not directly compare tags to metadata. |
| Q28 scheduler internals | groundedness | eval expectation too strict | Safe insufficient answer is correct, but unsupported-topic groundedness remains counted as a gap. |
| Q29 ABI guarantees | groundedness | eval expectation too strict | Safe insufficient answer is correct; no guarantee source should be inferred. |
| Q30 FPGA bitstream export | groundedness, weak source | insufficient evidence should trigger | Safe insufficient answer remains correct; current source evidence is weak. |
| Q31 deterministic auto-repair | groundedness, weak source | insufficient evidence should trigger | Safe insufficient answer remains correct; docs must not imply auto-repair authority. |

The three remaining relevance failures were Q4, Q15, and Q20. All three need
comparison or parent/neighbor evidence work and were deliberately not changed in
this small batch.

## Fix Applied

Targeted row:

| Row | Question | Official/primary source | Fix type |
| ---: | --- | --- | --- |
| Q11 | What is a flowgraph? | GNU Radio Wiki `What Is GNU Radio` and GNU Radio source-tree usage manual export `Handling Flowgraphs` | curated local snippet |

Added:

- `docs/wiki_gnuradio_org/Flowgraph.md`

The snippet defines a flowgraph as connected signal-processing blocks, explains
source/sink/processing block roles, notes GRC `.grc` to Python generation at a
high level, and explicitly states that docs are not mutation authority.

Sources:

- <https://wiki.gnuradio.org/index.php/What_Is_GNU_Radio>
- <https://github.com/gnuradio/gnuradio/blob/main/docs/usage-manual/(exported%20from%20wiki)%20Handling%20Flowgraphs.txt>

Context7 official GNU Radio docs also surfaced `Handling Flowgraphs` as the
primary source for the flowgraph definition.

## Before/After Metrics

Before: Phase 15 committed baseline (`ea080aa66c26`).

After: `uv run python -m tests.retrieval_eval.grc_docs_answer_eval`.

| Metric | Before | After |
| --- | ---: | ---: |
| Rows | 35 | 35 |
| Fallback rows | 35/35 | 35/35 |
| Helper used | 0 | 0 |
| Helper eligible | 0 | 0 |
| Misleading answers | 0 | 0 |
| Mutation leakage | 0 | 0 |
| Relevance pass | 32/35 | 32/35 |
| Groundedness pass | 27/35 | 28/35 |
| Insufficient-evidence correctness | 32/35 | 32/35 |
| Source quality | strong 30, medium 3, weak 2 | strong 31, medium 2, weak 2 |
| Retrieval modes | lexical-only 24, lexical+semantic 11 | lexical-only 24, lexical+semantic 11 |

Row-level change:

- Q11 `What is a flowgraph?`: groundedness `False -> True`, source quality
  `medium -> strong`.
- No previously passing relevance, groundedness, or insufficient-correct row
  regressed.

## Remaining Docs QA Gaps

Remaining work is quality-focused, not safety-blocking:

1. Q4: add deterministic two-sided comparison evidence for stream ports and
   message ports.
2. Q15: add parent/neighbor evidence tying Message Strobe to PMT messages.
3. Q20: decide whether tags-vs-metadata has enough official evidence or should
   be reclassified as insufficient.
4. Q28-Q31: split unsupported-topic safe-refusal quality from answerable-topic
   groundedness so correct refusals do not look like source-quality failures.

## Threshold Verdict

The proposed deterministic docs QA threshold is met:

- relevance `32/35 >= 30/35`
- groundedness `28/35 >= 28/35`
- misleading answers `0`
- mutation leakage `0`
- helper used `0`

This should be described as a threshold-met deterministic docs baseline, not as
production-ready runtime evidence.

No production-ready claim is made.
