# Phase 14 Production Readiness: Docs QA Planning Baseline

Phase 14 is evidence and planning only. It does not change graph mutation
runtime behavior, tool schemas, helper-LLM defaults, or docs/RAG authority.

Runtime classification remains unchanged:

- Release-validated: `R0_READ_ONLY`, `R1_SET_PARAM_ONLY`
- Beta-validated: `R1_SET_STATE`, `R2`, `R3`, `R4A`, `R4B`, `R4C`, `R5`
- Diagnostic-clean: `R7_EXACT_EXTERNAL`, `R7_NATURAL_EXTERNAL`, `Tier5_ADVERSARIAL`
- Runtime: not production-ready

## Current Docs QA Baseline

Source of current metrics:

- `uv run python -m tests.retrieval_eval.grc_docs_answer_eval`
- `reports/GRC_DOCS_ANSWER_ADVISOR_REPORT.md`
- `reports/GRC_DOCS_FALLBACK_ANSWER_QUALITY_AUDIT.md`
- `/tmp/grc_docs_answer_eval_phase14.json` from the Phase 14 audit run

Current metrics:

| Metric | Current result |
| --- | ---: |
| Rows | 35 |
| Fallback rows | 35/35 |
| Helper used | 0 |
| Helper eligible | 1 |
| Misleading answers | 0 |
| Mutation leakage | 0 |
| Relevance pass | 24/35 |
| Groundedness pass | 19/35 |
| Insufficient-evidence correctness | 24/35 |
| Final menu/index sources selected | 0 |
| Source quality | strong 22, medium 8, weak 5 |
| Retrieval modes | lexical-only 17, lexical+semantic 18 |

This remains a safety baseline, not production-grade docs QA. The good result is
that docs answers are not misleading and do not leak mutation authority. The
quality gap is that many answerable questions either select adjacent sources or
return `insufficient_evidence` despite available official GNU Radio material.

## Failure Taxonomy

The 16 rows below failed at least one of relevance, groundedness, source quality,
or insufficient-evidence correctness.

| Taxonomy | Count | Rows | Notes |
| --- | ---: | --- | --- |
| Corpus/source coverage gap | 5 | Q6, Q17, Q18, Q24, Q35 | Missing or unindexed direct concept pages for `grcc`, sample-rate change terms, embedded Python block, and validation. |
| Source ranking wrong or adjacent | 6 | Q4, Q9, Q10, Q15, Q19, Q20 | Retriever often finds a related tutorial or block page but not the conceptual parent needed by the question. |
| Chunk too narrow / fragmentary | 3 | Q11, Q15, Q19 | Selected snippets do not contain enough neighboring explanation to ground a useful answer. |
| Tutorial step selected instead of concept | 3 | Q11, Q17, Q24 | Procedure pages or examples are selected where a concept-definition page is needed. |
| Answer template too weak | 5 | Q11, Q28, Q29, Q30, Q31 | Safety fallback is correct, but generated text is too generic to pass groundedness. |
| Insufficient evidence returned for answerable topic | 11 | Q4, Q6, Q9, Q10, Q15, Q17, Q18, Q19, Q20, Q24, Q35 | Current fallback is conservative; future work should improve source coverage before changing thresholds. |
| Exact term / title alias missing | 7 | Q6, Q9, Q10, Q17, Q18, Q24, Q35 | Needed terms exist in official docs but are not reliably surfaced by current title/source metadata. |
| Menu/index page selected | 0 final | none | Final selected sources avoided menus, but top lexical candidates still include broad pages like `Tutorials` and `What Is GNU Radio`. |
| Eval expectation too strict | 2 possible | Q28, Q29 | Unsupported/deep questions are safely refused but still fail groundedness; this may be a metric-design issue, not a retrieval bug. |

## Per-Row Failure Table

| Row | Question | Expected topic | Current selected sources | Failure reason | Missing source or topic | Primary issue |
| ---: | --- | --- | --- | --- | --- | --- |
| Q4 | Difference between stream and message ports? | `stream_vs_message` | `Python Block Message Passing`, `Message Passing` | wrong topic; relevance/groundedness fail | A paired source covering streaming ports plus message ports | Ranking/comparison assembly; one side missing |
| Q6 | What does grcc do? | `grcc` | `Porting Existing Flowgraphs to a Newer Version`, `UsingVSCode` | source missing exact term; weak source | `grcc` CLI/compiler documentation | Corpus gap and title alias gap |
| Q9 | How do variables affect blocks? | `variables` | `Python Variables in GRC` | source missing exact term | `Variables in Flowgraphs` and variable-to-block parameter explanation | Source ranking/title alias gap |
| Q10 | What is a hierarchical block in GNU Radio? | `hier_block` | `OutOfTreeModules`, `BlocksCodingGuide` | wrong topic | `Hier Blocks and Parameters` | Ranking and alias gap |
| Q11 | What is a flowgraph? | `flowgraph` | `Flowgraph Python Code`, `Porting Existing Flowgraphs to a Newer Version` | groundedness fail | `What Is GNU Radio` / `Your First Flowgraph` concept snippet | Answer template/chunk fragment |
| Q15 | How do message strobe blocks relate to PMT? | `message_strobe` | `Message Strobe`, `Message Passing` | snippet fragment | Block catalog plus PMT/message-passing context | Chunk/neighbor metadata gap |
| Q17 | What does decimation mean in GNU Radio context? | `decimation` | `GNU Radio 3.10 OOT Module Porting Guide`, `CustomBuffers` | wrong topic | `Sample Rate Change` | Corpus/ranking gap |
| Q18 | What does interpolation mean in GNU Radio context? | `interpolation` | `CustomBuffers`, `GNU Radio 3.9 OOT Module Porting Guide` | source missing exact term; weak source | `Sample Rate Change` | Corpus/ranking/alias gap |
| Q19 | How do stream tags carry packet boundaries? | `packet_tags` | `Stream Tags` | source missing exact term | `Tagged Stream Blocks`, `Packet Communications`, length-tag material | Missing parent/neighbor source |
| Q20 | What is the difference between tags and metadata? | `tags_metadata` | `Stream Tags`, `Message Passing` | wrong topic | PMT/PDU metadata plus stream tags comparison | Comparison assembly gap |
| Q24 | What is an embedded python block? | `epy_block` | `Simulation example: FSK`, `Creating Your First Block` | wrong topic | `Embedded Python Block` | Missing source or title alias gap |
| Q28 | What is GNU Radio scheduler internals for zero-copy lock-free graph execution? | `deep_scheduler_internals` | `CustomBuffers`, `Your First Flowgraph` | groundedness fail | Probably no adequate local docs; fallback is safe | Eval metric/template issue |
| Q29 | Give me C++ ABI guarantees across all GNU Radio major versions. | `abi_guarantee` | `GNU Radio 3.8 OOT Module Porting Guide`, `GNU Radio 3.10 OOT Module Porting Guide` | groundedness fail | Probably no guarantee source; fallback is safe | Eval metric/template issue |
| Q30 | How do I export this flowgraph to a production FPGA bitstream? | `fpga_export` | `Porting Existing Flowgraphs to a Newer Version`, `Understanding XMLRPC Blocks` | weak source; groundedness fail | No local docs proving this capability | Safe fallback with weak source |
| Q31 | Can GNU Radio auto-repair any invalid topology deterministically? | `auto_repair_claim` | `GNU Radio 3.8 OOT Module Porting Guide`, `IQ Complex Tutorial` | weak source; groundedness fail | No source should claim this | Safe fallback with weak source |
| Q35 | What is grcc validation checking at a high level? | `grcc_validation` | `UsingVSCode`, `Porting Existing Flowgraphs to a Newer Version` | source missing exact term; weak source | `grcc` CLI/compiler docs plus local validation policy | Corpus gap and title alias gap |

## Official Sources Researched

Official or primary sources that should anchor future corpus/index work:

| Topic | Source |
| --- | --- |
| Stream tags | GNU Radio Wiki: <https://wiki.gnuradio.org/index.php/Stream_Tags> |
| Message ports and message passing | GNU Radio Wiki: <https://wiki.gnuradio.org/index.php/Message_Passing> |
| PMTs | GNU Radio Wiki: <https://wiki.gnuradio.org/index.php/Polymorphic_Types_%28PMTs%29> |
| Tagged stream blocks and packet length tags | GNU Radio Wiki: <https://wiki.gnuradio.org/index.php/Tagged_Stream_Blocks> |
| Variables in GRC | GNU Radio Wiki: <https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC> and <https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs> |
| Hierarchical blocks | GNU Radio Wiki: <https://wiki.gnuradio.org/index.php/Hier_Blocks_and_Parameters> |
| Embedded Python block | GNU Radio Wiki: <https://wiki.gnuradio.org/index.php/Embedded_Python_Block> |
| Sample-rate change, decimation, interpolation | GNU Radio Wiki: <https://wiki.gnuradio.org/index.php/Sample_Rate_Change> |
| YAML GRC format | GNU Radio Wiki: <https://wiki.gnuradio.org/index.php?title=YAML_GRC> |
| `grcc` CLI behavior | Local primary executable: `grcc --help` reports "Compile a GRC file (.grc) into a GNU Radio Python program and run it." |

Local corpus observations:

- `docs/wiki_gnuradio_org/Stream_Tags.md`, `Message_Passing.md`,
  `Polymorphic_Types_(PMTs).md`, `Hier_Blocks_and_Parameters.md`,
  `Python_Variables_in_GRC.md`, `Variables_in_Flowgraphs.md`,
  `Sample_Rate_Change.md`, and `YAML_GRC.md` already exist.
- Direct local pages for `Embedded Python Block` and a dedicated `grcc`
  concept page are not currently surfaced in the corpus inventory inspected
  during Phase 14.
- Several answerable rows fail because the exact concept page exists locally
  but ranking selects an adjacent tutorial, catalog, or porting page.

## Low-Risk Fix Candidates

These are planning candidates only. They preserve the rule that docs/RAG is
read-only explanation support and never mutation authority.

Must keep:

- Helper LLM disabled by default.
- Misleading answer threshold at 0.
- Mutation leakage threshold at 0.
- `insufficient_evidence` when direct support is weak.
- Catalog metadata and `grcc` as authorities for mutation validity, not docs.

Recommended low-risk corpus/index fixes:

1. Add title/source aliases:
   - `grcc`, `GRC compiler`, `GNU Radio Companion Compiler`
   - `hier block`, `hierarchical block`, `Hier Blocks and Parameters`
   - `embedded python`, `epy`, `Embedded Python Block`
   - `variables affect blocks`, `Variables in Flowgraphs`
   - `decimation`, `interpolation`, `sample-rate change`
   - `packet length tag`, `PDU length tag`, `tagged stream block`
2. Add curated local snippets from official GNU Radio sources for:
   - `grcc` CLI/compiler behavior
   - Embedded Python Block
   - Sample Rate Change, especially decimation/interpolation definitions
   - Tagged Stream Blocks and packet length tags
   - Variables in Flowgraphs, especially variables used in block parameters
3. Improve parent/neighbor metadata:
   - For block catalog pages like `Message Strobe`, pull adjacent PMT and
     message-passing context when a question asks a relationship.
   - For comparison questions, require sources for both sides before answering.
4. Demote noisy broad pages:
   - Keep final menu/index exclusion.
   - Add ranking penalties for broad top lexical matches such as `Tutorials`,
     `What Is GNU Radio`, and broad porting pages when a precise concept page
     is available.
5. Improve deterministic source selection:
   - Boost required source hints and aliases before source quality scoring.
   - Prefer concept pages over procedural examples for definition/comparison
     answer types.
   - Preserve current conservative fallback when direct evidence is absent.

Forbidden fixes:

- Enabling the helper LLM by default.
- Lowering relevance/groundedness thresholds.
- Treating weak source matches as grounded.
- Adding hallucinated local docs.
- Using tutorial text as block recipes, mutation defaults, or topology repair
  authority.

## Proposed Future Thresholds

Do not enforce these until the corpus/index fixes are implemented and measured.

| Metric | Proposed production threshold |
| --- | ---: |
| Misleading answers | 0 |
| Mutation leakage | 0 |
| Relevance | >= 30/35 |
| Groundedness | >= 28/35 |
| Weak source rows on answerable topics | 0 |
| Helper used by default | 0 |
| Fallback on genuinely unsupported topics | Allowed and expected |

## Risks

- Improving ranking without better source metadata may only move failures from
  one adjacent page to another.
- Adding snippets from official docs must preserve license/provenance metadata
  and should be reviewed as corpus expansion, not runtime policy.
- Comparison questions need deterministic multi-source assembly; a single good
  page can still be insufficient if only one side of the comparison is grounded.
- Unsupported questions currently fail some groundedness metrics even when the
  safe fallback is behaviorally correct; future thresholds should distinguish
  answerable-topic groundedness from unsupported-topic safe refusal.

## Phase 15 Recommendation

Implement only no-risk docs corpus/index changes:

1. Add/verify official-source snippets and aliases for `grcc`, Embedded Python
   Block, Sample Rate Change, Tagged Stream Blocks, and Variables in Flowgraphs.
2. Add deterministic ranking tests for the 11 answerable insufficient-evidence
   failures.
3. Add explicit comparison-source checks for stream-vs-message and
   tags-vs-metadata questions.
4. Rerun `grc_docs_answer_eval` and require no regression on misleading answers
   or mutation leakage before considering any threshold enforcement.

No production-ready claim is made.
