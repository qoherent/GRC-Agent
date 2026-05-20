# Phase 15 Production Readiness: Deterministic Docs QA Improvements

Phase 15 improves deterministic docs QA only. It does not change graph mutation
runtime behavior, tool schemas, helper-LLM defaults, or docs/RAG authority.

Runtime classification remains unchanged:

- Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`
- Beta-validated: `R1_SET_STATE`, `R2`, `R3`, `R4A`, `R4B`, `R4C`, `R5`
- Diagnostic-clean: `R7_EXACT_EXTERNAL`, `R7_NATURAL_EXTERNAL`, `Tier5_ADVERSARIAL`
- Runtime: not production-ready

## Selected Low-Risk Batch

The first batch targeted eight answerable Phase 14 failures where official or
primary source evidence was clear and the fix could remain deterministic.

| Row | Failure reason before | Bad source before | Desired source | Official/primary source | Fix type |
| ---: | --- | --- | --- | --- | --- |
| Q6 `grcc` | source missing exact term; weak | `Porting Existing Flowgraphs`, `UsingVSCode` | `grcc` compiler evidence | local `grcc` man page plus <https://github.com/gnuradio/gnuradio/blob/main/grc/scripts/grcc> | curated snippet, alias, ranking |
| Q9 variables | source missing exact term | `Python Variables in GRC` | `Variables in Flowgraphs` | <https://wiki.gnuradio.org/index.php/Variables_in_Flowgraphs> | curated snippet, alias, query expansion |
| Q10 hierarchical block | wrong topic | `OutOfTreeModules`, `BlocksCodingGuide` | `Hier Blocks and Parameters` | <https://wiki.gnuradio.org/index.php/Hier_Blocks_and_Parameters> | curated snippet, alias, ranking |
| Q17 decimation | wrong topic | OOT guide fragments | `Sample Rate Change` | <https://wiki.gnuradio.org/index.php/Sample_Rate_Change> | query expansion, alias, sentence selection |
| Q18 interpolation | source missing exact term; weak | `CustomBuffers`, OOT guide | `Sample Rate Change` | <https://wiki.gnuradio.org/index.php/Sample_Rate_Change> | curated snippet, alias, query expansion |
| Q19 packet tags | source missing exact term | `Stream Tags` only | `Tagged Stream Blocks`, `Packet Communications` | <https://wiki.gnuradio.org/index.php/Tagged_Stream_Blocks> | curated snippet, alias, query expansion |
| Q24 embedded Python | wrong topic | FSK simulation, `Creating Your First Block` | `Embedded Python Block` | <https://wiki.gnuradio.org/index.php/Embedded_Python_Block> | curated snippet, alias, ranking |
| Q35 `grcc` validation | source missing exact term; weak | `UsingVSCode`, porting guide | `grcc` compiler evidence | local `grcc` man page plus <https://github.com/gnuradio/gnuradio/blob/main/grc/scripts/grcc> | curated snippet, alias, ranking |

## Fixes Applied

Corpus additions:

- `docs/wiki_gnuradio_org/grcc.md`
- `docs/wiki_gnuradio_org/Variables_Block_Parameters.md`
- `docs/wiki_gnuradio_org/Hier_Blocks.md`
- `docs/wiki_gnuradio_org/Sample_Rate.md`
- `docs/wiki_gnuradio_org/Tagged_Stream_Blocks.md`
- `docs/wiki_gnuradio_org/Embedded_Python_Block.md`

Each curated snippet includes source title, source URL, retrieval topic,
aliases, why it is relevant, official/primary status, and an explicit
explanation-only safety boundary where mutation-adjacent wording could be
misread.

Deterministic index/retrieval changes:

- Added lexical alias expansions for `grcc`, variables, hierarchical blocks,
  embedded Python blocks, sample-rate change, decimation/interpolation, packet
  boundaries, and tagged streams.
- Added title aliases for the same source families so eval source-hint matching
  and deterministic ranking can select the right source without helper LLM use.
- Added deterministic preferred-source markers for the Phase 15 topics.
- Penalized the curated variables snippet when the query is not about variables,
  preserving the existing `options_block` row.
- Improved deterministic sentence selection so leading markdown headings do not
  hide useful grounded sentences and exact term hits beat loose synonym hits.
- Shortened deterministic comparison side sentences so required comparison
  shape is not truncated before the `Difference:` clause.

No helper LLM was enabled. No mutation authority was added to docs/RAG.

## Before/After Metrics

Baseline from Phase 14:

| Metric | Before | After |
| --- | ---: | ---: |
| Rows | 35 | 35 |
| Fallback rows | 35/35 | 35/35 |
| Helper used | 0 | 0 |
| Helper eligible | 1 | 0 |
| Misleading answers | 0 | 0 |
| Mutation leakage | 0 | 0 |
| Relevance pass | 24/35 | 32/35 |
| Groundedness pass | 19/35 | 27/35 |
| Insufficient-evidence correctness | 24/35 | 32/35 |
| Source quality | strong 22, medium 8, weak 5 | strong 30, medium 3, weak 2 |
| Retrieval modes | lexical-only 17, lexical+semantic 18 | lexical-only 24, lexical+semantic 11 |

Improved rows:

- Q6 `grcc`: relevance/groundedness/insufficient/source quality now pass.
- Q9 variables: relevance/groundedness/insufficient/source quality now pass.
- Q10 hierarchical block: relevance/groundedness/insufficient/source quality now pass.
- Q17 decimation: relevance/groundedness/insufficient/source quality now pass.
- Q18 interpolation: relevance/groundedness/insufficient/source quality now pass.
- Q19 packet tags: relevance/groundedness/insufficient now pass.
- Q24 embedded Python block: relevance/groundedness/insufficient/source quality now pass.
- Q35 `grcc` validation: relevance/groundedness/insufficient/source quality now pass.

Regression guard:

- The first Phase 15 eval pass produced regressions in `options_block` and
  `packet_length_tags`.
- `packet_length_tags` was fixed by keeping deterministic comparison answers
  short enough to preserve the required `Difference:` clause.
- `options_block` was fixed by preferring the existing catalog-backed Options
  source when the query is about options and by penalizing the new variables
  snippet when the query is not about variables.
- Final row comparison shows no previously passing relevance or groundedness
  row regressed.

## Remaining Weak Rows

Rows still failing at least one quality dimension:

| Row | Current issue | Recommended next batch |
| ---: | --- | --- |
| Q4 stream vs message ports | one-sided comparison, still misses stream-port side | Add deterministic comparison assembly from `Streams and Vectors` plus `Message Passing`. |
| Q11 flowgraph | groundedness weak, answer template/chunk fragment | Add concise official concept snippet for flowgraph/top block. |
| Q15 message strobe + PMT | snippet fragment | Add parent/neighbor evidence from Message Strobe catalog plus PMT/message-passing docs. |
| Q20 tags vs metadata | comparison unsupported | Decide whether the eval expects a broader metadata source or should remain insufficient. |
| Q28 scheduler internals | unsupported topic groundedness metric only | Separate unsupported safe-refusal groundedness from answerable-topic groundedness. |
| Q29 ABI guarantees | unsupported topic groundedness metric only | Same unsupported-topic metric split. |
| Q30 FPGA bitstream export | weak source safe fallback | Keep insufficient unless official source is added. |
| Q31 deterministic auto-repair | weak source safe fallback | Keep insufficient; docs must not imply auto-repair authority. |

## Phase 16 Recommendation

Use the same approach for the next docs QA batch:

1. Fix Q4 with paired comparison evidence from `Streams and Vectors` and
   `Message Passing`.
2. Add a small flowgraph/top-block concept snippet for Q11.
3. Add message-strobe parent/neighbor evidence for Q15.
4. Decide whether Q20 has sufficient official metadata evidence or should be
   reclassified as correctly insufficient.
5. Split unsupported-question safety metrics from answerable-topic groundedness
   so safe refusals do not look like groundedness regressions.

Do not enable the helper LLM by default, do not lower thresholds, and do not
use docs/RAG as mutation authority.

No production-ready claim is made.
